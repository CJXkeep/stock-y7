// ==================== 扫描买入：弹窗/轮询容错/历史归档（improvements #3/#13） ====================
import { escHtml, showToastMsg } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze } from './main.js';
import { openSbSection } from './watchlist.js';
// ==================== 扫描功能（原独立 script 块） ====================
// ===== 扫描功能 =====
export let _scanTimer = null;

// ---- 扫描结果归档（frontend迭代：本地留存最近30次，供回看/导出） ----
export const STORAGE_SCAN_ARCHIVE = 'qs_scan_archive';
export const MAX_SCAN_ARCHIVE = 30;

export function getScanArchive() {
  try { return JSON.parse(localStorage.getItem(STORAGE_SCAN_ARCHIVE)) || []; } catch (e) { return []; }
}
export function saveScanArchive(list) {
  try { localStorage.setItem(STORAGE_SCAN_ARCHIVE, JSON.stringify(list)); return true; }
  catch (e) { showToastMsg('归档失败：浏览器存储空间不足'); return false; }
}
// 幂等签名：同一次运行结果重复渲染（关弹窗后重开）不会重复归档
export function _scanRunSig(results, elapsed) {
  const f = results[0] || {}, l = results[results.length - 1] || {};
  return `${results.length}|${elapsed}|${f.symbol || ''}|${l.symbol || ''}`;
}
export function archiveScanRun(data) {
  const results = data.results || [];
  const elapsed = data.elapsed || 0;
  const sig = _scanRunSig(results, elapsed);
  const list = getScanArchive();
  const now = Date.now();
  const newest = list[0];
  // 10分钟内同签名的视为同一轮结果，跳过
  if (newest && newest.sig === sig && (now - newest.finishedAt) < 10 * 60 * 1000) return newest;
  const run = {
    id: 's' + now,
    finishedAt: now,
    elapsed: elapsed,
    scannedTotal: data.scanned || null,
    marketTotal: data.total || null,
    sig: sig,
    count: results.length,
    items: results.map(r => ({
      symbol: r.symbol, name: r.name, price: r.price, daily_pct: r.daily_pct,
      daily_action: r.daily_action, daily_score: r.daily_score,
      weekly_action: r.weekly_action, weekly_score: r.weekly_score,
      combined_score: r.combined_score, position_advice: r.position_advice,
      risk_reward: r.risk_reward,
    })),
  };
  list.unshift(run);
  while (list.length > MAX_SCAN_ARCHIVE) list.pop();
  saveScanArchive(list);
  // 用户正停留在侧栏"扫描档"分区时，就地刷新列表
  if (_sbSection === 'scan' && document.getElementById('sb-wide-scan')) renderScanArchiveList();
  return run;
}

export function openScan() {
  document.getElementById('scan-overlay').classList.add('show');
  // 先拉一次状态，再决定是显示进度还是启动新扫描
  fetchWithTimeout('/api/scan').then(r => r.json()).then(data => {
    if (data.status === 'running') {
      renderScanProgress(data);
      startScanPolling();
    } else if (data.status === 'done' && data.results && data.results.length > 0) {
      renderScanResults(data);
    } else {
      renderScanIdle();
    }
  }).catch(() => { renderScanIdle(); });
}

export function closeScan(e) {
  if (e && e.target !== document.getElementById('scan-overlay')) return;
  document.getElementById('scan-overlay').classList.remove('show');
  stopScanPolling();
}

export function renderScanIdle() {
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-empty">
      <div style="margin-bottom:16px;font-size:15px;color:#aaa">扫描全A股，找出日K和周K同时符合买入信号的股票</div>
      <div style="margin-bottom:8px;color:#888;font-size:13px">筛选条件：日K买入 + 周K买入（双周期共振）</div>
      <div style="margin-bottom:12px;color:#ccc;font-size:13px">
        <label for="scan-topn" style="color:#888;margin-right:6px">扫描范围</label>
        <select id="scan-topn" style="background:#111;border:1px solid #333;color:#ddd;font-size:13px;padding:4px 8px;border-radius:4px">
          <option value="500">成交额前 500</option>
          <option value="1000" selected>成交额前 1000</option>
          <option value="2000">成交额前 2000</option>
          <option value="0">全A股（较慢）</option>
        </select>
      </div>
      <div style="margin-bottom:20px;color:#888;font-size:13px">预计耗时：2-4分钟（全量更久）</div>
      <button class="scan-btn" style="font-size:15px;padding:8px 28px" onclick="startScan()">开始扫描</button>
    </div>`;
}

export function startScan() {
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-progress-wrap">
      <div class="scan-stage">正在启动扫描...</div>
      <div class="scan-bar-bg"><div class="scan-bar-fill" style="width:0%"></div></div>
    </div>`;
  const topn = (document.getElementById('scan-topn') ? document.getElementById('scan-topn').value : '1000');
  fetchWithTimeout('/api/scan?action=start&max_stocks=' + encodeURIComponent(topn)).then(r => r.json()).then(data => {
    if (data.status === 'started' || data.status === 'running') {
      startScanPolling();
    }
  });
}

let _scanFailCount = 0;            // 扫描轮询连续失败计数（improvements #3）
const _SCAN_FAIL_THRESHOLD = 3;

export function startScanPolling() {
  stopScanPolling();
  _scanFailCount = 0;
  hideScanConnIssue();
  _scanTimer = setInterval(scanPollTick, 2000);
}

export function scanPollTick() {
  fetchWithTimeout('/api/scan', {}, 10000).then(r => r.json()).then(data => {
    _scanFailCount = 0;
    hideScanConnIssue();
    if (data.status === 'running') {
      renderScanProgress(data);
    } else if (data.status === 'done') {
      stopScanPolling();
      renderScanResults(data);
    } else if (data.status === 'error') {
      stopScanPolling();
      renderScanError(data);
    }
  }).catch(() => {
    // 不再静默吞错：连续失败达到阈值时给出可见提示与手动重试入口
    _scanFailCount += 1;
    if (_scanFailCount >= _SCAN_FAIL_THRESHOLD) showScanConnIssue();
  });
}

// 轮询失败恢复：清零计数、隐藏横幅并立即补一次轮询
export function scanPollRetry() {
  _scanFailCount = 0;
  hideScanConnIssue();
  scanPollTick();
}

export function showScanConnIssue() {
  const host = document.getElementById('scan-content');
  if (!host || document.getElementById('scan-conn-issue')) return;
  host.insertAdjacentHTML('afterbegin',
    `<div id="scan-conn-issue" style="margin:0 12px 10px;padding:8px 10px;background:rgba(255,107,107,0.08);border:1px solid rgba(255,107,107,0.2);border-radius:6px;font-size:12px;color:#ff6b6b;display:flex;align-items:center;gap:10px">
      <span>与服务器的连接中断</span>
      <span style="cursor:pointer;color:#4fc3f7;text-decoration:underline" data-act="scanRetry">[重试]</span>
    </div>`);
}

export function hideScanConnIssue() {
  const el = document.getElementById('scan-conn-issue');
  if (el) el.remove();
}

export function stopScanPolling() {
  if (_scanTimer) { clearInterval(_scanTimer); _scanTimer = null; }
}

export function renderScanProgress(data) {
  const pct = data.progress || 0;
  const stage = data.stage || '扫描中...';
  const scanned = data.scanned || 0;
  const total = data.total || 0;
  const found = data.found || 0;
  const elapsed = data.elapsed || 0;
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-progress-wrap">
      <div class="scan-stage">${stage}</div>
      <div class="scan-bar-bg"><div class="scan-bar-fill" style="width:${pct}%"></div></div>
      <div class="scan-stats">
        <span>进度: <b>${scanned}/${total}</b></span>
        <span>发现买入: <b style="color:#ff9800">${found}</b></span>
        <span>耗时: <b>${elapsed}s</b></span>
        <span>进度: <b>${pct}%</b></span>
      </div>
    </div>
    <div class="scan-empty">正在扫描中，请耐心等待...</div>`;
}

export function renderScanResults(data) {
  const results = data.results || [];
  const elapsed = data.elapsed || 0;
  const archivedRun = archiveScanRun(data);   // 自动归档（幂等）
  if (!results.length) {
    document.getElementById('scan-content').innerHTML = `
      <div class="scan-empty">
        <div style="margin-bottom:12px;color:#aaa">扫描完成，未发现双周期买入信号</div>
        <div style="color:#888;font-size:13px">当前市场可能处于调整期，可稍后再试</div>
        <div style="margin-top:16px">
          <button class="scan-btn" onclick="renderScanIdle()">重新扫描</button>
          ${archivedRun ? `<button class="scan-btn scan-btn-ghost" onclick="closeScan();openSbSection('scan')">在左侧查看归档 (${getScanArchive().length})</button>` : ''}
        </div>
      </div>`;
    return;
  }
  let html = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>扫描完成，耗时 <b style="color:#ddd">${elapsed}s</b></span>
      <span>双周期买入: <b style="color:#ff9800">${results.length}</b> 只</span>
      <span class="scan-archived-tag" title="结果已自动归档到本地，可在历史归档中回看">已归档✓</span>
      <button class="scan-btn scan-btn-ghost" style="padding:3px 12px;font-size:12px" onclick="closeScan();openSbSection('scan')">在左侧查看归档 (${getScanArchive().length})</button>
      <button class="scan-btn" style="margin-left:auto;padding:3px 12px;font-size:12px" onclick="renderScanIdle()">重新扫描</button>
    </div>
    ${_scanTableHtml(results)}
    <div style="margin-top:10px;color:#888;font-size:11px">本次结果已自动归档，关闭弹窗后仍可在「历史归档」中回看与导出。</div>`;
  document.getElementById('scan-content').innerHTML = html;
}

// 结果表格（实时结果与归档详情共用）
export function _scanTableHtml(results) {
  let html = `
    <div class="scan-table-wrap">
    <table class="scan-table">
      <thead><tr>
        <th>#</th><th>代码</th><th>名称</th><th>现价</th>
        <th>日K信号</th><th>日K分</th>
        <th>周K信号</th><th>周K分</th>
        <th>综合分</th><th>仓位</th><th>盈亏比</th>
        <th>操作</th>
      </tr></thead>
      <tbody>`;
  results.forEach((r, i) => {
    const dAct = formatScanAction(r.daily_action);
    const wAct = formatScanAction(r.weekly_action);
    const pct = (r.daily_pct || 0).toFixed(2);
    const pctColor = r.daily_pct > 0 ? '#ff2d2d' : r.daily_pct < 0 ? '#00b35c' : '#888';
    html += `<tr>
      <td class="scan-rank">${i + 1}</td>
      <td>${escHtml(r.symbol)}</td>
      <td>${escHtml(r.name)}</td>
      <td style="color:${pctColor}">${r.price ? r.price.toFixed(2) : '-'}<span style="font-size:11px;color:#888"> ${pct}%</span></td>
      <td class="${dAct.cls}">${dAct.text}</td>
      <td>${r.daily_score}</td>
      <td class="${wAct.cls}">${wAct.text}</td>
      <td>${r.weekly_score}</td>
      <td class="scan-combined" style="color:#ff9800">${r.combined_score}</td>
      <td style="font-size:12px;color:#aaa">${r.position_advice ? r.position_advice.split('—')[0].trim() : '-'}</td>
      <td style="color:${(r.risk_reward||0) >= 2 ? '#00b35c' : (r.risk_reward||0) >= 1 ? '#ffc107' : '#ff2d2d'}">${r.risk_reward || '-'}</td>
      <td><button class="scan-analyze-btn" data-act="analyzeFromScan" data-code="${escHtml(r.symbol)}">分析</button></td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  return html;
}

// ---- 历史归档视图 ----
export function _fmtScanTime(ts) {
  const d = new Date(ts);
  const p = n => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function renderScanArchiveList() {
  const list = getScanArchive();
  let rows;
  if (!list.length) {
    rows = `<div class="scan-empty">暂无归档。每次扫描完成后会自动留档（保留最近 ${MAX_SCAN_ARCHIVE} 次）。</div>`;
  } else {
    rows = list.map(run => `
      <div class="scan-hist-row">
        <span class="scan-hist-time">${_fmtScanTime(run.finishedAt)}</span>
        <span class="scan-hist-meta">命中 <b style="color:${run.count ? '#ff9800' : '#666'}">${run.count}</b> 只 · 耗时 ${run.elapsed}s${run.scannedTotal ? ` · 扫描 ${run.scannedTotal} 只` : ''}</span>
        <span class="scan-hist-ops">
          <button class="scan-analyze-btn" data-act="renderArchivedRun" data-run-id="${escHtml(run.id)}">查看</button>
          <button class="scan-analyze-btn" data-act="exportScanCsv" data-run-id="${escHtml(run.id)}">CSV</button>
          <button class="scan-analyze-btn scan-del-btn" data-act="deleteScanRun" data-run-id="${escHtml(run.id)}">删除</button>
        </span>
      </div>`).join('');
    rows = `<div class="scan-hist-list">${rows}</div>`;
  }
  const host = document.getElementById('sb-wide-scan');
  if (!host) return;   // 宿主在左侧工作台扫描分区
  host.innerHTML = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>扫描历史归档 <b style="color:#ddd">${list.length}</b> / ${MAX_SCAN_ARCHIVE} 次</span>
      ${list.length ? `<button class="scan-btn scan-btn-ghost" style="padding:3px 12px;font-size:12px" onclick="clearScanArchive()">清空全部</button>` : ''}
    </div>
    ${rows}`;
}

export function renderArchivedRun(id) {
  const run = getScanArchive().find(r => r.id === id);
  if (!run) { renderScanArchiveList(); return; }
  const host = document.getElementById('sb-wide-scan');
  if (!host) return;
  host.innerHTML = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>归档 ${_fmtScanTime(run.finishedAt)}</span>
      <span>命中 <b style="color:#ff9800">${run.count}</b> 只 · 耗时 ${run.elapsed}s</span>
      <button class="scan-btn scan-btn-ghost" style="padding:3px 12px;font-size:12px" data-act="exportScanCsv" data-run-id="${escHtml(run.id)}">导出 CSV</button>
      <button class="scan-btn" style="margin-left:auto;padding:3px 12px;font-size:12px" onclick="renderScanArchiveList()">返回列表</button>
    </div>
    ${run.count ? _scanTableHtml(run.items) : '<div class="scan-empty">该次扫描未发现双周期买入信号</div>'}
    <div style="margin-top:10px;color:#888;font-size:11px">⚠ 归档为扫描当时快照：价格/涨跌幅为当时数据，「分析」按最新行情重新计算。</div>`;
}

export function exportScanCsv(id) {
  const run = getScanArchive().find(r => r.id === id);
  if (!run) return;
  const head = '代码,名称,现价,涨跌%,日K信号,日K分,周K信号,周K分,综合分,仓位建议,盈亏比';
  const lines = run.items.map(r => [
    r.symbol, `"${String(r.name || '').replace(/"/g, '""')}"`,
    r.price != null ? r.price : '', r.daily_pct != null ? r.daily_pct : '',
    r.daily_action || '', r.daily_score != null ? r.daily_score : '',
    r.weekly_action || '', r.weekly_score != null ? r.weekly_score : '',
    r.combined_score != null ? r.combined_score : '',
    `"${String(r.position_advice || '').replace(/"/g, '""')}"`,
    r.risk_reward != null ? r.risk_reward : '',
  ].join(','));
  const csv = '\uFEFF' + head + '\n' + lines.join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const d = new Date(run.finishedAt);
  const p = n => String(n).padStart(2, '0');
  a.download = `scan-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
  showToastMsg('扫描归档已导出 CSV');
}

export function deleteScanRun(id) {
  if (!confirm('删除这条扫描归档？')) return;
  saveScanArchive(getScanArchive().filter(r => r.id !== id));
  renderScanArchiveList();
}

export function clearScanArchive() {
  if (!confirm(`清空全部 ${getScanArchive().length} 条扫描归档？此操作不可恢复。`)) return;
  saveScanArchive([]);
  renderScanArchiveList();
}

export function formatScanAction(act) {
  if (!act) return { text: '-', cls: 'scan-action-watch' };
  if (act.includes('强烈')) return { text: '强买', cls: 'scan-action-strong' };
  if (act.includes('买入') && !act.includes('谨慎')) return { text: '买入', cls: 'scan-action-buy' };
  if (act.includes('谨慎')) return { text: '谨慎', cls: 'scan-action-caution' };
  if (act.includes('卖出')) return { text: '卖出', cls: 'scan-action-watch' };
  return { text: '观望', cls: 'scan-action-watch' };
}

export function analyzeFromScan(symbol) {
  closeScan();
  analyze(symbol);
}

export function renderScanError(data) {
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-empty">
      <div style="margin-bottom:12px;color:#ff4d4d">扫描失败</div>
      <div style="color:#888;font-size:13px">${escHtml(data.error || '未知错误')}</div>
      <div style="margin-top:16px"><button class="scan-btn" onclick="renderScanIdle()">重试</button></div>
    </div>`;
}



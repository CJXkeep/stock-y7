// ==================== 扫描买入：弹窗/轮询容错/历史归档（improvements #3/#13） ====================
import { escHtml, showToastMsg } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze } from './main.js';
import { openSbSection, getSbSection } from './watchlist.js';
// ==================== 扫描功能（原独立 script 块） ====================
// ===== 扫描功能 =====
export let _scanTimer = null;

// ---- 扫描结果归档（I13 重构：服务器唯一事实源 data/scan/history.jsonl） ----
// localStorage 旧归档（qs_scan_archive，I13 之前仅存 results）首次加载时一次性迁移入服务器。
export const STORAGE_SCAN_ARCHIVE = 'qs_scan_archive';
export const STORAGE_SCAN_MIGRATED = 'qs_scan_archive_migrated';

export function getScanArchive() {
  try { return JSON.parse(localStorage.getItem(STORAGE_SCAN_ARCHIVE)) || []; } catch (e) { return []; }
}

// 旧归档 → 服务器行映射（旧档无拦截组：blocked_all 恒空，如实标注）
function _legacyRunToHistory(run) {
  const finishedAt = run.finishedAt ? new Date(run.finishedAt) : new Date();
  const p = n => String(n).padStart(2, '0');
  return {
    run_id: run.id || ('m' + run.finishedAt),
    started_at: '',
    finished_at: `${finishedAt.getFullYear()}-${p(finishedAt.getMonth() + 1)}-${p(finishedAt.getDate())} ${p(finishedAt.getHours())}:${p(finishedAt.getMinutes())}:${p(finishedAt.getSeconds())}`,
    elapsed: run.elapsed || 0,
    max_stocks: null,
    market_total: run.marketTotal || null,
    scanned_total: run.scannedTotal || null,
    dual_buy_total: run.count || (run.items || []).length,
    blocked_total: 0,
    failed_total: 0,
    results_all: run.items || [],
    blocked_all: [],
    auto_candidates: {},
    note: 'I13 前旧归档迁移：拦截组当时未存档',
  };
}

// 一次性迁移：成功（或确认无需迁移）后置标记并清空本地；失败静默保留待下次重试
export async function migrateLocalArchive() {
  try {
    if (localStorage.getItem(STORAGE_SCAN_MIGRATED) === '1') return;
    const legacy = getScanArchive();
    if (!legacy.length) {
      localStorage.setItem(STORAGE_SCAN_MIGRATED, '1');
      return;
    }
    const resp = await fetchWithTimeout('/api/scan/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'migrate', runs: legacy.map(_legacyRunToHistory) }),
    }, 15000);
    const data = await resp.json();
    if (data && data.ok) {
      localStorage.setItem(STORAGE_SCAN_MIGRATED, '1');
      localStorage.removeItem(STORAGE_SCAN_ARCHIVE);
      showToastMsg(`旧扫描归档已迁移到服务器（${data.imported} 条）`);
    }
  } catch (e) { /* 迁移失败保留本地数据，下次重试 */ }
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
  _scanFailedHint({});
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
  // 先读扫描范围再替换 innerHTML：#scan-topn 在 scan-content 内，替换后即销毁
  const topn = (document.getElementById('scan-topn') ? document.getElementById('scan-topn').value : '1000');
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-progress-wrap">
      <div class="scan-stage">正在启动扫描...</div>
      <div class="scan-bar-bg"><div class="scan-bar-fill" style="width:0%"></div></div>
    </div>`;
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

// 扫描失败统计提示（optimization-round2）：failed_total>0 时显示，否则隐藏
export function _scanFailedHint(data) {
  const el = document.getElementById('scan-failed-hint');
  if (!el) return;
  const n = (data && data.failed_total) || 0;
  if (n > 0) {
    el.style.display = 'block';
    el.textContent = `本次扫描有 ${n} 只个股分析失败（已跳过）。明细见 data/scan/latest.json 或服务日志。`;
  } else {
    el.style.display = 'none';
  }
}

export function stopScanPolling() {
  if (_scanTimer) { clearInterval(_scanTimer); _scanTimer = null; }
}

export function renderScanProgress(data) {
  _scanFailedHint(data);
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
  _scanFailedHint(data);
  const results = data.results || [];
  const blocked = data.blocked || [];
  const elapsed = data.elapsed || 0;
  const runId = data.run_id || '';   // I13：本轮服务器归档 id（全量与拦截组在扫描档）
  const archBtn = `<button class="scan-btn scan-btn-ghost" style="padding:3px 12px;font-size:12px" data-act="scanArchOpen" ${runId ? `data-run-id="${escHtml(runId)}"` : ''}>在扫描档查看全量与拦截组</button>`;
  if (!results.length) {
    document.getElementById('scan-content').innerHTML = `
      <div class="scan-empty">
        <div style="margin-bottom:12px;color:#aaa">扫描完成，未发现双周期买入信号</div>
        <div style="color:#888;font-size:13px">${blocked.length
          ? `有 <b style="color:#ffb74d">${blocked.length}</b> 只信号达到买入档位，但被「第一性原则策略门」按市场环境压住（M分&lt;30 或下降趋势），策略设计为极弱市场不新增仓位。`
          : '当前市场可能处于调整期，可稍后再试'}</div>
        ${blocked.length ? _scanBlockedHtml(blocked) : ''}
        <div style="margin-top:16px">
          <button class="scan-btn" onclick="renderScanIdle()">重新扫描</button>
          ${archBtn}
        </div>
      </div>`;
    return;
  }
  let html = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>扫描完成，耗时 <b style="color:#ddd">${elapsed}s</b></span>
      <span>双周期买入: <b style="color:#ff9800">${results.length}</b> 只（显示前 20）</span>
      ${blocked.length ? `<span>被策略门拦截: <b style="color:#ffb74d">${blocked.length}</b> 只</span>` : ''}
      <span class="scan-archived-tag" title="结果已按轮归档到服务器 data/scan/history.jsonl">已归档✓</span>
      ${archBtn}
      <button class="scan-btn" style="margin-left:auto;padding:3px 12px;font-size:12px" onclick="renderScanIdle()">重新扫描</button>
    </div>
    ${_scanTableHtml(results)}
    ${blocked.length ? _scanBlockedHtml(blocked) : ''}
    <div style="margin-top:10px;color:#888;font-size:11px">本次结果已按轮归档到服务器；拦截组为日K买入档口径（周K阶段不参与拦截采集）。</div>`;
  document.getElementById('scan-content').innerHTML = html;
}

// 被「第一性原则策略门」拦截的候选（达买入档但环境门压住，仅供观察、不可直接买入）
export function _scanBlockedHtml(blocked, statusMap, total) {
  return `
    <div style="margin-top:16px;border-top:1px solid #222;padding-top:10px">
      <div style="color:#ffb74d;font-size:12px;font-weight:bold;margin-bottom:6px">
        被策略门拦截（${total || blocked.length} 只，显示当前页）
        <span style="color:#888;font-weight:normal;font-size:11px">— 日K买入档口径：信号达买入档但被市场环境/趋势门压住，不新增仓位；仅作观察</span>
      </div>
      <div class="scan-table-wrap">
        <table class="scan-table compact">
          <thead><tr><th>代码/名称</th><th>原始信号</th><th>综合分</th><th>M分</th><th>拦截原因</th><th>操作</th></tr></thead>
          <tbody>
          ${blocked.map(b => {
            const cs = (statusMap || {})[b.symbol];
            const badge = cs
              ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:rgba(79,195,247,0.12);color:#4fc3f7">${escHtml(cs.status || '')}</span>`
              : '';
            return `
            <tr>
              <td><b>${escHtml(b.name || b.symbol)}</b><span class="code">${escHtml(b.symbol)}</span>${badge}</td>
              <td style="color:#ff9800">${escHtml(b.original_action || '')}</td>
              <td>${b.score != null ? b.score : '-'}</td>
              <td>${b.m_score != null ? b.m_score : '-'}</td>
              <td style="font-size:11px;color:#ffb74d">${escHtml(b.veto_reason || '')}</td>
              <td>
                <button class="scan-analyze-btn" data-act="analyzeFromScan" data-code="${escHtml(b.symbol)}">分析</button>
                <button class="scan-analyze-btn" data-act="scanWatchAdd" data-code="${escHtml(b.symbol)}" data-name="${escHtml(b.name || '')}">自选</button>
              </td>
            </tr>`;
          }).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

// 结果表格（实时结果与归档详情共用）
// opts.compact=true：窄面板模式——现价并入名称格、日K/周K「动作+分」各合一列、去掉仓位列
export function _scanTableHtml(results, opts) {
  const compact = !!(opts && opts.compact);
  const head = compact
    ? `<thead><tr>
        <th>#</th><th>代码 / 名称</th>
        <th>日K</th><th>周K</th>
        <th>综合</th><th>盈亏比</th><th>操作</th>
      </tr></thead>`
    : `<thead><tr>
        <th>#</th><th>代码</th><th>名称</th><th>现价</th>
        <th>日K信号</th><th>日K分</th>
        <th>周K信号</th><th>周K分</th>
        <th>综合分</th><th>仓位</th><th>盈亏比</th>
        <th>操作</th>
      </tr></thead>`;
  let html = `
    <div class="scan-table-wrap">
    <table class="scan-table${compact ? ' compact' : ''}">
      ${head}
      <tbody>`;
  results.forEach((r, i) => {
    const dAct = formatScanAction(r.daily_action);
    const wAct = formatScanAction(r.weekly_action);
    const pct = (r.daily_pct || 0).toFixed(2);
    const pctColor = r.daily_pct > 0 ? '#ff2d2d' : r.daily_pct < 0 ? '#00b35c' : '#888';
    const rr = r.risk_reward || 0;
    const rrColor = rr >= 2 ? '#00b35c' : rr >= 1 ? '#ffc107' : '#ff2d2d';
    if (compact) {
      html += `<tr>
      <td class="scan-rank">${i + 1}</td>
      <td><b>${escHtml(r.name || r.symbol)}</b><span class="code">${escHtml(r.symbol)}</span>
          <span style="display:block;font-size:10px;color:${pctColor}">${r.price ? r.price.toFixed(2) : '-'} ${pct}%</span></td>
      <td class="${dAct.cls}" style="white-space:nowrap">${dAct.text}<span style="font-size:10px;color:#888"> ${r.daily_score != null ? r.daily_score : '-'}</span></td>
      <td class="${wAct.cls}" style="white-space:nowrap">${wAct.text}<span style="font-size:10px;color:#888"> ${r.weekly_score != null ? r.weekly_score : '-'}</span></td>
      <td class="scan-combined" style="color:#ff9800">${r.combined_score}</td>
      <td style="color:${rrColor}">${r.risk_reward || '-'}</td>
      <td style="white-space:nowrap"><button class="scan-analyze-btn" data-act="analyzeFromScan" data-code="${escHtml(r.symbol)}">析</button>
          <button class="scan-analyze-btn" data-act="candAdd" data-code="${escHtml(r.symbol)}" data-name="${escHtml(r.name)}" data-source="scan" title="加入候选池">候选</button></td>
    </tr>`;
      return;
    }
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
      <td style="color:${rrColor}">${r.risk_reward || '-'}</td>
      <td><button class="scan-analyze-btn" data-act="analyzeFromScan" data-code="${escHtml(r.symbol)}">分析</button>
          <button class="scan-analyze-btn" data-act="candAdd" data-code="${escHtml(r.symbol)}" data-name="${escHtml(r.name)}" data-source="scan" title="加入候选池">候选</button></td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  return html;
}

// ---- 历史归档视图（I13：读服务器 /api/scan/history，与弹窗同源） ----
export function _fmtScanTime(ts) {
  if (typeof ts === 'string' && ts.includes('-')) return ts.slice(5, 16);   // "MM-DD HH:MM"
  const d = new Date(ts);
  if (isNaN(d.getTime())) return String(ts || '');
  const p = n => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export async function renderScanArchiveList() {
  const host = document.getElementById('sb-wide-scan');
  if (!host) return;   // 宿主在左侧工作台扫描分区
  host.innerHTML = `<div class="scan-empty">扫描历史加载中…</div>`;
  await migrateLocalArchive();   // 首次：旧 localStorage 归档迁入服务器
  let runs = [], maxRows = 60, err = '';
  try {
    const resp = await fetchWithTimeout('/api/scan/history', {}, 10000);
    const data = await resp.json();
    if (data && data.ok) { runs = data.runs || []; maxRows = data.max_rows || 60; }
    else err = (data && data.error) || '';
  } catch (e) { err = '无法连接服务器'; }
  let rows;
  if (err) {
    rows = `<div class="scan-empty" style="color:#ff6b6b">扫描历史读取失败：${escHtml(err)}</div>`;
  } else if (!runs.length) {
    rows = `<div class="scan-empty">暂无归档。每次扫描完成后会自动按轮留档（保留最近 ${maxRows} 轮，含被拦截组全量）。</div>`;
  } else {
    rows = runs.map(run => `
      <div class="scan-hist-row">
        <span class="scan-hist-time">${escHtml(_fmtScanTime(run.finished_at))}</span>
        <span class="scan-hist-meta">命中 <b style="color:${run.dual_buy_total ? '#ff9800' : '#666'}">${run.dual_buy_total}</b> 只 · 拦截 <b style="color:#ffb74d">${run.blocked_total}</b> 只 · 入池 ${(run.auto_candidates && run.auto_candidates.added) || 0} · 耗时 ${run.elapsed}s</span>
        <span class="scan-hist-ops">
          <button class="scan-analyze-btn" data-act="renderArchivedRun" data-run-id="${escHtml(run.run_id)}">查看</button>
          <button class="scan-analyze-btn" data-act="exportScanCsv" data-run-id="${escHtml(run.run_id)}">CSV</button>
          <button class="scan-analyze-btn scan-del-btn" data-act="deleteScanRun" data-run-id="${escHtml(run.run_id)}">删除</button>
        </span>
      </div>`).join('');
    rows = `<div class="scan-hist-list">${rows}</div>`;
  }
  host.innerHTML = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>扫描历史归档 <b style="color:#ddd">${runs.length}</b> / ${maxRows} 轮</span>
    </div>
    ${rows}`;
}

let _archCurrent = null;    // 当前查看的归档 run（翻页不再重新请求）
let _archPage = { results: 1, blocked: 1 };   // 两表独立分页状态
const ARCH_PAGE_SIZE = 20;

export async function renderArchivedRun(id) {
  const host = document.getElementById('sb-wide-scan');
  if (!host) return;
  host.innerHTML = `<div class="scan-empty">归档详情加载中…</div>`;
  let run = null;
  try {
    const resp = await fetchWithTimeout('/api/scan/history?run_id=' + encodeURIComponent(id), {}, 10000);
    const data = await resp.json();
    if (data && data.ok) run = data.run;
    else host.innerHTML = `<div class="scan-empty" style="color:#ff6b6b">${escHtml((data && data.error) || '读取失败')}</div>`;
  } catch (e) { host.innerHTML = `<div class="scan-empty" style="color:#ff6b6b">无法连接服务器</div>`; }
  if (!run) return;
  _archCurrent = run;
  _archPage = { results: 1, blocked: 1 };
  _renderArchDetail();
}

function _pagerHtml(kind, total) {
  const totalPages = Math.max(1, Math.ceil(total / ARCH_PAGE_SIZE));
  const page = Math.min(_archPage[kind] || 1, totalPages);
  _archPage[kind] = page;
  if (totalPages <= 1) return '';
  return `<div class="scan-pager">
    <button class="scan-analyze-btn" data-act="scanArchPage" data-table="${kind}" data-dir="-1" ${page <= 1 ? 'disabled' : ''}>‹ 上一页</button>
    <span>第 ${page} / ${totalPages} 页 · 共 ${total} 只</span>
    <button class="scan-analyze-btn" data-act="scanArchPage" data-table="${kind}" data-dir="1" ${page >= totalPages ? 'disabled' : ''}>下一页 ›</button>
  </div>`;
}

function _renderArchDetail() {
  const run = _archCurrent;
  if (!run) return;
  const host = document.getElementById('sb-wide-scan');
  if (!host) return;
  const statusMap = run.candidate_status || {};
  const resultsAll = run.results_all || [];
  const blockedAll = run.blocked_all || [];
  const rPage = Math.min(_archPage.results || 1, Math.max(1, Math.ceil(resultsAll.length / ARCH_PAGE_SIZE)));
  const bPage = Math.min(_archPage.blocked || 1, Math.max(1, Math.ceil(blockedAll.length / ARCH_PAGE_SIZE)));
  const shownResults = resultsAll.slice((rPage - 1) * ARCH_PAGE_SIZE, rPage * ARCH_PAGE_SIZE);
  const shownBlocked = blockedAll.slice((bPage - 1) * ARCH_PAGE_SIZE, bPage * ARCH_PAGE_SIZE);
  host.innerHTML = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>归档 ${escHtml(_fmtScanTime(run.finished_at))}</span>
      <span>命中 <b style="color:#ff9800">${run.dual_buy_total}</b> 只 · 拦截 <b style="color:#ffb74d">${run.blocked_total}</b> 只 · 入池 ${(run.auto_candidates && run.auto_candidates.added) || 0}</span>
      <button class="scan-btn scan-btn-ghost" style="padding:3px 12px;font-size:12px" data-act="exportScanCsv" data-run-id="${escHtml(run.run_id)}">导出 CSV</button>
      <button class="scan-btn" style="margin-left:auto;padding:3px 12px;font-size:12px" data-act="scanArchBack">返回列表</button>
    </div>
    ${resultsAll.length ? _scanTableHtml(shownResults, { compact: true }) + _pagerHtml('results', resultsAll.length) : '<div class="scan-empty">该次扫描未发现双周期买入信号</div>'}
    ${blockedAll.length ? _scanBlockedHtml(shownBlocked, statusMap, blockedAll.length) + _pagerHtml('blocked', blockedAll.length) : ''}
    ${run.failed_total ? `<div style="margin-top:8px;color:#888;font-size:11px">本次扫描 ${run.failed_total} 只分析失败（已跳过）${(run.failed_symbols || []).length ? '：' + escHtml((run.failed_symbols || []).join('、')) : ''}</div>` : ''}
    <div style="margin-top:10px;color:#888;font-size:11px">⚠ 归档为扫描当时快照：价格/涨跌幅为当时数据，「分析」按最新行情重新计算。${run.source === 'migrated' ? '（迁移自浏览器旧归档：拦截组当时未存档）' : ''}</div>`;
}

export function scanArchPage(el) {
  const kind = el && el.dataset ? el.dataset.table : '';
  const dir = el && el.dataset ? parseInt(el.dataset.dir || '1', 10) : 1;
  if (!kind) return;
  _archPage[kind] = Math.max(1, (_archPage[kind] || 1) + dir);
  _renderArchDetail();
}

export async function exportScanCsv(id) {
  let run = null;
  try {
    const resp = await fetchWithTimeout('/api/scan/history?run_id=' + encodeURIComponent(id), {}, 10000);
    const data = await resp.json();
    if (data && data.ok) run = data.run;
  } catch (e) { /* fallthrough */ }
  if (!run) { showToastMsg('归档读取失败'); return; }
  const head = '代码,名称,现价,涨跌%,日K信号,日K分,周K信号,周K分,综合分,仓位建议,盈亏比';
  const q = v => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
  const lines = (run.results_all || []).map(r => [
    r.symbol, q(r.name), r.price != null ? r.price : '', r.daily_pct != null ? r.daily_pct : '',
    r.daily_action || '', r.daily_score != null ? r.daily_score : '',
    r.weekly_action || '', r.weekly_score != null ? r.weekly_score : '',
    r.combined_score != null ? r.combined_score : '',
    q(r.position_advice), r.risk_reward != null ? r.risk_reward : '',
  ].join(','));
  const blockedLines = (run.blocked_all || []).map(b => [
    b.symbol, q(b.name), '', '', q('被拦截:' + (b.original_action || '')), b.score != null ? b.score : '',
    '', '', b.score != null ? b.score : '', '', '',
  ].join(','));
  const csv = '\uFEFF' + head + '\n' + lines.join('\n')
    + ((run.blocked_all || []).length
      ? '\n\n被策略门拦截（日K口径）\n' + head + '\n' + blockedLines.join('\n')
      : '');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const d = new Date(run.finished_at || Date.now());
  const p = n => String(n).padStart(2, '0');
  a.download = `scan-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
  showToastMsg('扫描归档已导出 CSV');
}

export async function deleteScanRun(id) {
  if (!confirm('删除这条扫描归档？')) return;
  try {
    const resp = await fetchWithTimeout('/api/scan/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete', run_id: id }),
    }, 10000);
    const data = await resp.json();
    if (data && data.ok) { showToastMsg('归档已删除'); renderScanArchiveList(); return; }
    showToastMsg((data && data.error) || '删除失败');
  } catch (e) { showToastMsg('删除失败：无法连接服务器'); }
}

// 详情页辅助动作（data-act 注册见 ui.js）
export function scanArchBack() {
  renderScanArchiveList();
}

export function scanArchOpen(el) {
  const runId = el && el.dataset ? el.dataset.runId : '';
  closeScan();
  openSbSection('scan');
  if (runId) renderArchivedRun(runId); else renderScanArchiveList();
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
  _scanFailedHint(data);
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-empty">
      <div style="margin-bottom:12px;color:#ff4d4d">扫描失败</div>
      <div style="color:#888;font-size:13px">${escHtml(data.error || '未知错误')}</div>
      <div style="margin-top:16px"><button class="scan-btn" onclick="renderScanIdle()">重试</button></div>
    </div>`;
}



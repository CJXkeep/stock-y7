// ==================== 数据面板层：一览/信号档案/核心池/速递/信号统计（improvements #13） ====================
import { C, S } from './shared.js';
import { escHtml, showToast, showToastMsg, removeToast } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze } from './main.js';
import { getGroups, getStockMap, saveGroups, saveStockMap, getWatchlist, saveHistory, addHistory, getHistory, sigTag, fmtTime, sbBadge } from './watchlist.js';

// 模块内私有状态
const STORAGE_SIGNALS = 'qs_signal_records';
const MAX_SIGNAL_RECORDS = 200;
// ===== 多股一览 =====
export async function loadOverview() {
  const el = document.getElementById('wp-content-overview');
  const list = getWatchlist();
  if (!list.length) {
    el.innerHTML = '<div class="wp-empty"><span class="wp-empty-icon">📊</span>还没有自选股<br>添加自选后可在多股行情中一屏对比</div>';
    return;
  }
  el.innerHTML = '<div class="wp-ov-loading">正在获取行情数据...</div>';

  // 并行获取所有自选股的简要行情
  const results = await Promise.all(list.map(async s => {
    try {
      const r = await fetchWithTimeout(`${API}/api/quote?symbol=${s.code}`);
      const q = await r.json();
      if (q.error) return { code: s.code, name: s.name, error: true };
      return {
        code: s.code, name: q.name || s.name,
        price: q.price, pct: q.pct, volume: q.volume,
        action: s.action || '', score: s.score || 0,
      };
    } catch(e) {
      return { code: s.code, name: s.name, error: true };
    }
  }));

  // 按涨跌幅排序
  results.sort((a, b) => (b.pct || -999) - (a.pct || -999));

  el.innerHTML = results.map(s => {
    const pctCls = s.pct > 0 ? 'up' : s.pct < 0 ? 'down' : 'flat';
    const pctStr = s.pct != null ? (s.pct > 0 ? '+' : '') + s.pct.toFixed(2) + '%' : '--';
    const sigTag = s.action ? `<span class="wp-ov-sig-tag ${s.action === '买入' ? 'buy' : s.action === '卖出' ? 'sell' : 'watch'}">${s.action}</span>` : '<span class="wp-ov-sig-tag none">--</span>';
    const scoreStr = s.score ? s.score : '--';
    const scoreColor = s.score >= 60 ? C.up : s.score >= 40 ? '#ffc107' : s.score > 0 ? C.down : '#666';
    return `<div class="wp-ov-item" data-act="analyze" data-code="${escHtml(s.code)}">
      <span class="wp-ov-code">${escHtml(s.code)}</span>
      <span class="wp-ov-name">${escHtml(s.name)}</span>
      <span class="wp-ov-pct ${pctCls}">${pctStr}</span>
      <span class="wp-ov-score" style="color:${scoreColor}">${scoreStr}</span>
      <span class="wp-ov-sig">${sigTag}</span>
    </div>`;
  }).join('');
}

// ===== 信号档案（真实信号日志，只读） =====
export const _journalTypeNames = {
  buy: '买入', strong_buy: '强烈买入', cautious_buy: '谨慎买入',
  breakout_exit: '突破卖出', short_cover: '空头平仓',
  chanlun_buy1: '缠论一买', chanlun_buy2: '缠论二买',
  chanlun_sell1: '缠论一卖', chanlun_sell2: '缠论二卖',
};
let _journalShowDupes = false;
// ---- 证券名称解析（frontend迭代：信号档案等仅存代码的场景补显示名称） ----
const STORAGE_SYM_NAMES = 'qs_symbol_names';
export function _symNames() {
  try { return JSON.parse(localStorage.getItem(STORAGE_SYM_NAMES)) || {}; } catch (e) { return {}; }
}
export function _saveSymNames(m) {
  try { localStorage.setItem(STORAGE_SYM_NAMES, JSON.stringify(m)); } catch (e) {}
}
// 已知名称：自选股详情表 > 历史记录 > 本地名称缓存
export function _knownName(sym) {
  const m = getStockMap();
  if (m[sym] && m[sym].name) return m[sym].name;
  const h = getHistory().find(x => x.code === sym);
  if (h && h.name) return h.name;
  return _symNames()[sym] || null;
}
// 批量补齐缺失名称（/api/quotes 每批≤50只），有新学到时返回 true
export async function _resolveSymbolNames(symbols) {
  const missing = [...new Set((symbols || []).filter(s => s && !_knownName(s)))];
  if (!missing.length) return false;
  let learned = false;
  for (let i = 0; i < missing.length; i += 50) {
    const chunk = missing.slice(i, i + 50);
    try {
      const r = await fetchWithTimeout(`${API}/api/quotes?codes=${encodeURIComponent(chunk.join(','))}`);
      const j = await r.json();
      const m = _symNames();
      for (const c of chunk) {
        const q = j.quotes && j.quotes[c];
        if (q && q.name && !m[c]) { m[c] = q.name; learned = true; }
      }
      if (learned) _saveSymNames(m);
    } catch (e) {}
  }
  return learned;
}

let _journalTypeFilter = '';
let _journalRenderSeq = 0;   // 防止名称异步补齐后的重渲染覆盖更新的渲染
let _journalSymbolFilter = '';
// 导出用：最近一次 /api/journal 结果与其过滤条件（frontend-iteration）
window._journalLastRecords = [];
window._journalLastQuery = null;

export function _followupMap(rec) {
  const m = {};
  (rec.followups || []).forEach(f => { m[f.horizon] = f; });
  return m;
}

export async function loadJournal() {
  const el = document.getElementById('wp-content-journal');
  el.innerHTML = '<div class="wp-ov-loading">正在读取信号日志...</div>';
  const qs = new URLSearchParams({
    include_dupes: _journalShowDupes ? '1' : '0',
    limit: '500',
  });
  if (_journalTypeFilter) qs.set('type', _journalTypeFilter);
  if (_journalSymbolFilter) qs.set('symbol', _journalSymbolFilter);
  let data;
  try {
    const r = await fetchWithTimeout(`${API}/api/journal?${qs}`);
    data = await r.json();
  } catch (e) {
    el.innerHTML = '<div class="wp-error" style="padding:16px;color:#e57373;font-size:12px">信号日志读取失败：' + escHtml(String(e)) + '</div>';
    return;
  }
  if (data.error) {
    el.innerHTML = `<div class="wp-error" style="padding:16px;color:#e57373;font-size:12px">${escHtml(data.error)}</div>`;
    return;
  }
  const records = data.records || [];
  window._journalLastRecords = records;
  window._journalLastQuery = {
    type: _journalTypeFilter,
    symbol: _journalSymbolFilter,
    include_dupes: _journalShowDupes,
  };
  // 名称补齐：先按本地已知渲染，缺失的批量反查，学到新名称后重渲染一次（seq 防过期覆盖）
  const _seq = ++_journalRenderSeq;
  _resolveSymbolNames(records.map(r => r.symbol)).then(learned => {
    if (learned && _seq === _journalRenderSeq) loadJournal();
  });
  const s = data.summary || {};
  const typeOpts = ['<option value="">全部类型</option>'].concat(
    Object.keys(_journalTypeNames).map(k =>
      `<option value="${k}" ${_journalTypeFilter === k ? 'selected' : ''}>${_journalTypeNames[k]}</option>`)
  ).join('');

  const winStr = s.buy_20d_win_rate_pct == null ? '--' : s.buy_20d_win_rate_pct.toFixed(1) + '%';
  const avgStr = s.buy_20d_avg_return_pct == null ? '--' : (s.buy_20d_avg_return_pct > 0 ? '+' : '') + s.buy_20d_avg_return_pct.toFixed(2) + '%';

  const rows = records.map(rec => {
    const f = _followupMap(rec);
    const cell = h => h == null ? '<span style="color:#555">--</span>'
      : `<span style="color:${h > 0 ? C.up : h < 0 ? C.down : '#999'}">${h > 0 ? '+' : ''}${h.toFixed(2)}%</span>`;
    const dupTag = rec.deduped ? '<span title="近期已记录（去重窗口内重复信号）">🔁</span>' : '';
    const nm = _knownName(rec.symbol);
    return `<tr>
      <td>${rec.trigger_date || ''}</td>
      <td><a href="#" data-act="analyze" data-code="${escHtml(rec.symbol)}" style="color:#ff9800;text-decoration:none">${escHtml(rec.symbol)}</a>${nm ? `<div style="color:#888;font-size:10px;margin-top:1px">${escHtml(nm)}</div>` : ''}</td>
      <td>${_journalTypeNames[rec.signal_type] || rec.signal_type}${dupTag}</td>
      <td>${rec.snapshot_close != null ? rec.snapshot_close : '--'}</td>
      <td>${cell(f[5] && f[5].return_pct)}</td>
      <td>${cell(f[10] && f[10].return_pct)}</td>
      <td>${cell(f[20] && f[20].return_pct)}</td>
      <td>${cell(f[60] && f[60].return_pct)}</td>
    </tr>`;
  }).join('');

  el.innerHTML = `
    <div style="display:flex;gap:14px;padding:8px 12px;border-bottom:1px solid #222;font-size:11px;color:#aaa;flex-wrap:wrap;align-items:center">
      <span>总信号 <b style="color:#eee">${s.total || 0}</b></span>
      <span>买侧20日样本 <b style="color:#eee">${s.buy_20d_count || 0}</b></span>
      <span>20日上涨比例 <b style="color:${(s.buy_20d_win_rate_pct||0) >= 50 ? C.up : C.down}">${winStr}</b></span>
      <span>20日平均收益 <b style="color:${(s.buy_20d_avg_return_pct||0) >= 0 ? C.up : C.down}">${avgStr}</b></span>
      <label>类型
        <select onchange="_journalTypeFilter=this.value;loadJournal()" style="background:#111;color:#ccc;border:1px solid #333;font-size:11px">${typeOpts}</select>
      </label>
      <label>代码
        <input value="${escHtml(_journalSymbolFilter)}" placeholder="如 600519" size="7"
          onchange="_journalSymbolFilter=this.value.trim();loadJournal()"
          style="background:#111;color:#ccc;border:1px solid #333;font-size:11px;width:70px">
      </label>
      <label style="margin-left:auto;cursor:pointer"><input type="checkbox" ${_journalShowDupes ? 'checked' : ''} onchange="_journalShowDupes=this.checked;loadJournal()"> 显示重复(近期已记录)</label>
      <button onclick="exportJournalCsv()" style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:2px 8px;font-size:11px">导出CSV</button>
      <button onclick="exportJournalJson()" style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:2px 8px;font-size:11px">导出JSON</button>
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:11px;color:#ccc">
      <thead><tr style="color:#777;text-align:left;border-bottom:1px solid #222">
        <th style="padding:4px 8px">信号日</th><th style="padding:4px 6px">代码</th>
        <th style="padding:4px 6px">类型</th><th style="padding:4px 6px">信号价</th>
        <th style="padding:4px 6px">5日</th><th style="padding:4px 6px">10日</th>
        <th style="padding:4px 6px">20日</th><th style="padding:4px 6px">60日</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="8" style="padding:16px;color:#888">暂无记录——产生买卖信号后自动落档</td></tr>'}</tbody>
    </table>`;
}

// ===== 信号档案导出（frontend-iteration：纯前端生成，不新增后端接口） =====
export function _downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 200);
}

export function _csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

export function _journalExportStem() {
  const q = window._journalLastQuery || {};
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  const dateStr = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
  const parts = [];
  if (q.type) parts.push('type-' + q.type);
  if (q.symbol) parts.push('symbol-' + q.symbol);
  if (q.include_dupes) parts.push('dupes');
  return '信号档案_' + dateStr + (parts.length ? '_' + parts.join('_') : '');
}

export function exportJournalCsv() {
  const records = window._journalLastRecords || [];
  if (!records.length) { alert('当前过滤条件下暂无记录可导出'); return; }
  const header = ['信号日', '代码', '名称', '类型', '动作', '信号价', '去重标记', '5日%', '10日%', '20日%', '60日%'];
  const lines = [header.join(',')];
  records.forEach(rec => {
    const f = _followupMap(rec);
    const pct = h => (f[h] && f[h].return_pct != null) ? f[h].return_pct : '';
    lines.push([
      rec.trigger_date || '', rec.symbol || '', _knownName(rec.symbol) || '',
      _journalTypeNames[rec.signal_type] || rec.signal_type || '',
      rec.action || '', rec.snapshot_close != null ? rec.snapshot_close : '',
      rec.deduped ? '是' : '否',
      pct(5), pct(10), pct(20), pct(60),
    ].map(_csvCell).join(','));
  });
  // UTF-8 BOM：保证 Excel 直接打开中文不乱码
  _downloadText(_journalExportStem() + '.csv', '\uFEFF' + lines.join('\r\n'), 'text/csv;charset=utf-8');
}

export function exportJournalJson() {
  const records = window._journalLastRecords || [];
  if (!records.length) { alert('当前过滤条件下暂无记录可导出'); return; }
  _downloadText(_journalExportStem() + '.json', JSON.stringify(records, null, 2), 'application/json;charset=utf-8');
}


// ===== 核心池管理（可视化维护，变更自动递增池版本） =====
export async function poolPost(body) {
  const r = await fetchWithTimeout(`${API}/api/pool`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

// 核心池面板状态（frontend-iteration）：行业筛选为纯前端过滤，不重新请求
let _poolLastData = null;
let _poolSnapBanner = '';
let _poolIndustryFilter = '';
let _poolImportOpen = false;

export async function loadPool() {
  const el = document.getElementById('wp-content-pool');
  el.innerHTML = '<div class="wp-ov-loading">正在读取核心池（手动）...</div>';
  let data;
  try {
    const r = await fetchWithTimeout(`${API}/api/pool`);
    data = await r.json();
  } catch (e) {
    el.innerHTML = '<div class="wp-error" style="padding:16px;color:#e57373;font-size:12px">核心池（手动）读取失败：' + escHtml(String(e)) + '</div>';
    return;
  }
  _poolLastData = data;
  // 快照同步状态（I7.5 失效提示闭环）
  _poolSnapBanner = '<div style="padding:6px 12px;font-size:11px;color:#888;border-bottom:1px solid #222">未找到历史统计快照——可运行 python -m backtest snapshot 生成</div>';
  try {
    const sr = await fetchWithTimeout(`${API}/api/snapshot-info`);
    const snap = await sr.json();
    if (snap.snapshot_id) {
      if (snap.pool_version === data.version) {
        _poolSnapBanner = `<div style="padding:6px 12px;font-size:11px;color:#81c784;border-bottom:1px solid #222">✓ 快照与核心池（手动）同步（${escHtml(snap.snapshot_id)}，基于 v${snap.pool_version}）</div>`;
      } else {
        _poolSnapBanner = `<div style="padding:6px 12px;font-size:11px;color:#ffd54f;background:#3a3320;border-bottom:1px solid #222">⚠ 核心池（手动）已更新（当前 v${data.version}），最新快照基于 v${snap.pool_version}——建议重建快照：python -m backtest snapshot</div>`;
      }
    }
  } catch (e) { /* 快照信息不可用时保持引导文案 */ }
  renderPoolPanel();
}

export function renderPoolPanel() {
  const el = document.getElementById('wp-content-pool');
  const data = _poolLastData || { version: 0, items: [] };
  const items = data.items || [];
  const cur = (typeof S.currentSymbol !== 'undefined' && S.currentSymbol) ? S.currentSymbol : '';
  // 行业筛选（纯前端过滤）
  const industries = [];
  items.forEach(it => {
    const ind = (it.industry || '').trim();
    if (ind && !industries.includes(ind)) industries.push(ind);
  });
  industries.sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
  const visible = _poolIndustryFilter
    ? items.filter(it => (it.industry || '').trim() === _poolIndustryFilter)
    : items;
  const indOpts = ['<option value="">全部行业</option>'].concat(
    industries.map(ind => `<option value="${escHtml(ind)}" ${_poolIndustryFilter === ind ? 'selected' : ''}>${escHtml(ind)}</option>`)
  ).join('');
  const countText = _poolIndustryFilter ? `${visible.length}/${items.length} 只` : `${items.length} 只`;
  const rows = visible.map((it, i) => `
    <div style="display:flex;align-items:center;gap:8px;padding:6px 12px;border-bottom:1px solid #1c1c1c;font-size:12px">
      <span style="color:#888;width:18px">${i + 1}</span>
      <a href="#" data-act="analyze" data-code="${escHtml(it.symbol)}" style="color:#ff9800;text-decoration:none;min-width:52px">${escHtml(it.symbol)}</a>
      <span style="min-width:80px;color:#ddd" title="${escHtml(it.industry || '')}">${escHtml(it.name || '--')}</span>
      ${it.industry ? `<span style="color:#888;font-size:10px;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(it.industry)}</span>` : ''}
      <input value="${escHtml(it.note || '')}" placeholder="备注" style="flex:1;background:#111;border:1px solid #2a2a2a;color:#bbb;font-size:11px;padding:2px 6px"
        data-chgact="poolNote" data-code="${escHtml(it.symbol)}">
      <button data-act="poolMove" data-code="${escHtml(it.symbol)}" data-dir="-1" ${i === 0 ? 'disabled' : ''} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:0 6px">↑</button>
      <button data-act="poolMove" data-code="${escHtml(it.symbol)}" data-dir="1" ${i === visible.length - 1 ? 'disabled' : ''} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:0 6px">↓</button>
      <button data-act="poolRemove" data-code="${escHtml(it.symbol)}" style="background:none;border:1px solid #5a2a2a;color:#e57373;cursor:pointer;padding:0 6px">删</button>
    </div>`).join('');

  el.innerHTML = `
    <div style="display:flex;gap:10px;padding:8px 12px;border-bottom:1px solid #222;font-size:11px;color:#aaa;align-items:center;flex-wrap:wrap">
      <span>池版本 <b style="color:#ff9800">v${data.version}</b></span>
      <span>${countText}</span>
      <input id="pool-add-symbol" placeholder="代码 如 600519" size="9" style="background:#111;border:1px solid #333;color:#ccc;font-size:11px;padding:2px 6px">
      <input id="pool-add-name" placeholder="名称(可选)" size="8" style="background:#111;border:1px solid #333;color:#ccc;font-size:11px;padding:2px 6px">
      <button onclick="poolAdd()" style="background:#ff9800;border:none;color:#000;padding:2px 10px;cursor:pointer;font-size:11px">添加</button>
      <button data-act="poolAddCurrent" data-code="${escHtml(cur)}" ${cur ? '' : 'disabled'} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:2px 8px;font-size:11px">+ 当前(${cur ? escHtml(cur) : '无'})</button>
      <select id="pool-industry-filter" onchange="_poolIndustryFilter=this.value;renderPoolPanel()" title="按行业筛选（纯前端过滤）"
        style="background:#111;border:1px solid #333;color:#ccc;font-size:11px;padding:2px 4px;max-width:110px">${indOpts}</select>
      <button onclick="poolFillIndustry()" ${items.length ? '' : 'disabled'} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:2px 8px;font-size:11px">补全行业</button>
      <button onclick="togglePoolImport()" style="${_poolImportOpen ? 'background:#ff9800;border:none;color:#000;' : 'background:none;border:1px solid #333;color:#aaa;'}cursor:pointer;padding:2px 8px;font-size:11px;margin-left:auto">批量导入</button>
    </div>
    ${_poolSnapBanner}
    ${_poolImportOpen ? `
    <div style="padding:8px 12px;border-bottom:1px solid #222">
      <textarea id="pool-import-text" rows="5" placeholder="每行一条：代码 或 代码,名称（逗号/制表符分隔，兼容 Excel 直接粘贴）&#10;例：&#10;600519,贵州茅台&#10;000001"
        style="width:100%;background:#111;border:1px solid #333;color:#ccc;font-size:11px;padding:4px 6px;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;margin-top:6px;align-items:center">
        <button onclick="poolImportSubmit()" style="background:#ff9800;border:none;color:#000;padding:2px 12px;cursor:pointer;font-size:11px">导入</button>
        <button onclick="togglePoolImport()" style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:2px 8px;font-size:11px">取消</button>
        <span id="pool-import-result" style="font-size:11px;color:#888"></span>
      </div>
    </div>` : ''}
    ${rows || '<div style="padding:20px;color:#888;font-size:12px">核心池（手动）为空——手动输入代码添加、分析个股后点「+ 当前」加入，或用「批量导入」粘贴多行代码。<br>核心池（手动）将用于信号档案筛选与历史统计。</div>'}`;
}

export async function poolAdd() {
  const symbolEl = document.getElementById('pool-add-symbol');
  const nameEl = document.getElementById('pool-add-name');
  const symbol = (symbolEl.value || '').trim();
  if (!symbol) return;
  const data = await poolPost({ action: 'add', symbol, name: (nameEl.value || '').trim() });
  if (!data.ok) alert(data.error || '添加失败');
  loadPool();
}

export async function poolAddCurrent(symbol) {
  if (!symbol) return;
  const data = await poolPost({ action: 'add', symbol });
  if (!data.ok) alert(data.error || '添加失败');
  loadPool();
}

export async function poolRemove(symbol) {
  if (!confirm(`从核心池移除 ${symbol}？`)) return;
  const data = await poolPost({ action: 'remove', symbol });
  if (!data.ok) alert(data.error || '删除失败');
  loadPool();
}

export async function poolNote(symbol, note) {
  const data = await poolPost({ action: 'note', symbol, note });
  if (!data.ok) alert(data.error || '备注保存失败');
  else loadPool();
}

export async function poolMove(symbol, offset) {
  const data = await poolPost({ action: 'move', symbol, offset });
  if (!data.ok) alert(data.error || '移动失败');
  else loadPool();
}

// ===== 批量导入 / 行业补全（frontend-iteration） =====
export function _infoToast(text) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.onclick = () => removeToast(toast);
  toast.innerHTML = `<div style="font-size:12px">${escHtml(text)}</div>`;
  container.appendChild(toast);
  setTimeout(() => removeToast(toast), 5000);
}

export function togglePoolImport() {
  _poolImportOpen = !_poolImportOpen;
  renderPoolPanel();
}

export async function poolImportSubmit() {
  const ta = document.getElementById('pool-import-text');
  const text = ((ta && ta.value) ? ta.value : '').trim();
  if (!text) return;
  // 每行一条：代码 或 代码,名称（逗号/制表符/顿号分隔，兼容 Excel 粘贴）
  const items = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const parts = line.split(/[,\t，、]/).map(s => s.trim()).filter(Boolean);
    return { symbol: parts[0] || '', name: parts.slice(1).join(' ') };
  }).filter(it => it.symbol);
  const resEl = document.getElementById('pool-import-result');
  const showImportError = msg => {
    if (resEl) {
      resEl.className = 'wp-error';
      resEl.style.cssText = 'color:#e57373;font-size:11px';
      resEl.textContent = msg;
    } else {
      alert(msg);
    }
  };
  try {
    const data = await poolPost({ action: 'import', items });
    if (!data.ok) {
      showImportError('导入失败：' + (data.error || '未知错误'));
      return;
    }
    _poolImportOpen = false;
    _infoToast(`批量导入完成：新增 ${data.added} 只，跳过 ${data.skipped} 只`);
    loadPool();
  } catch (e) {
    showImportError('导入失败：' + String(e));
  }
}

export async function poolFillIndustry() {
  try {
    const data = await poolPost({ action: 'fill-industry' });
    if (!data.ok) alert(data.error || '行业补全失败');
    else _infoToast(data.filled ? `行业补全完成：更新 ${data.filled} 只` : '池内行业信息完整，无需补全');
    loadPool();
  } catch (e) { alert('行业补全失败：' + String(e)); }
}


// ===== 相邻查看方向一致率统计 =====
export function getSignalRecords() {
  try { return JSON.parse(localStorage.getItem(STORAGE_SIGNALS) || '[]'); } catch(e) { return []; }
}
export function saveSignalRecords(list) {
  try { localStorage.setItem(STORAGE_SIGNALS, JSON.stringify(list.slice(0, MAX_SIGNAL_RECORDS))); } catch(e) {}
}
export function recordSignal(code, name, action, score, price) {
  const records = getSignalRecords();
  records.unshift({ code, name, action, score, price, time: Date.now() });
  saveSignalRecords(records);
}
export function calcSignalAccuracy(code) {
  const records = getSignalRecords().filter(r => r.code === code);
  if (records.length < 2) return null;
  let correct = 0, total = 0;
  const details = [];
  for (let i = 0; i < records.length - 1; i++) {
    const cur = records[i];
    const prev = records[i + 1];
    if (!prev.price || !cur.price) continue;
    const priceChange = (cur.price - prev.price) / prev.price * 100;
    let isCorrect = false;
    if (prev.action === '买入' && priceChange > 0) isCorrect = true;
    else if (prev.action === '卖出' && priceChange < 0) isCorrect = true;
    else if (prev.action === '观望') isCorrect = Math.abs(priceChange) < 3;
    total++;
    if (isCorrect) correct++;
    if (details.length < 5) {
      details.push({
        action: prev.action, price: prev.price,
        nextPrice: cur.price, change: priceChange,
        correct: isCorrect, time: prev.time,
      });
    }
  }
  if (total === 0) return null;
  return {
    accuracy: Math.round(correct / total * 100),
    total, correct,
    buyCount: records.filter(r => r.action === '买入').length,
    sellCount: records.filter(r => r.action === '卖出').length,
    watchCount: records.filter(r => r.action === '观望').length,
    details: details.reverse(),
  };
}
export function renderSignalAccuracy(code) {
  const card = document.getElementById('accuracy-card');
  const stats = calcSignalAccuracy(code);
  if (!stats) {
    card.style.display = 'none';
    return;
  }
  card.style.display = 'block';
  const accColor = stats.accuracy >= 60 ? C.up : stats.accuracy >= 40 ? '#ffc107' : C.down;
  document.getElementById('sa-stats').innerHTML = `
    <div style="padding:6px 8px;font-size:11px;color:#888;line-height:1.5">口径：仅统计相邻两次查看同一股票的方向是否一致，非策略胜率/回测准确率。</div>
    <div class="sa-item">
      <span class="sa-val" style="color:${accColor}">${stats.accuracy}%</span>
      <span class="sa-label">方向一致率</span>
    </div>
    <div class="sa-item">
      <span class="sa-val" style="color:#ddd">${stats.total}</span>
      <span class="sa-label">信号次数</span>
    </div>
    <div class="sa-item">
      <span class="sa-val" style="color:${C.up}">${stats.buyCount}</span>
      <span class="sa-label">买入</span>
    </div>
    <div class="sa-item">
      <span class="sa-val" style="color:${C.down}">${stats.sellCount}</span>
      <span class="sa-label">卖出</span>
    </div>
    <div class="sa-item">
      <span class="sa-val" style="color:#ffc107">${stats.watchCount}</span>
      <span class="sa-label">观望</span>
    </div>
  `;
  // 详情列表
  const detailHtml = stats.details.map(d => {
    const cls = d.correct ? 'up' : 'down';
    const sign = d.change > 0 ? '+' : '';
    return `<div style="padding:2px 0">
      <span style="color:${d.action === '买入' ? C.up : d.action === '卖出' ? C.down : '#ffc107'}">${d.action}</span>
      @ ${d.price.toFixed(2)} → 后续 <span class="${cls}">${sign}${d.change.toFixed(1)}%</span>
      <span style="color:${d.correct ? C.up : C.down}">${d.correct ? '✓' : '✗'}</span>
    </div>`;
  }).join('');
  document.getElementById('sa-detail').innerHTML = detailHtml;
}

// ===== 信号变更提醒 =====
export function checkSignalChange(code, name, action, score, price) {
  const prev = S._lastSignal[code];
  if (prev && prev.action && prev.action !== action) {
    // 信号变更了
    showToast(name, code, prev.action, action, price);
    // 如果是自选股，增加变更角标
    const watch = getWatchlist();
    if (watch.some(s => s.code === code)) {
      const badge = document.getElementById('watch-change');
      const count = parseInt(badge.textContent || '0') + 1;
      badge.textContent = count;
      badge.style.display = 'inline-block';
    }
  }
  S._lastSignal[code] = { action, score, price, time: Date.now() };
}


export function clearWatchChangeBadge() {
  const badge = document.getElementById('watch-change');
  badge.textContent = '0';
  badge.style.display = 'none';
}


// ==================== 每日速递（daily-digest：手动生成 + 后台轮询） ====================
let _digestTimer = null;
let _digestDays = 1;         // 块1 回看窗口（1/3/5 个信号日，前端裁剪）
let _digestRenderSeq = 0;

export function _digestName(t) { return _journalTypeNames[t] || t; }

export function _dgCell(v) {
  if (v == null) return '<span style="color:#555">--</span>';
  return `<span style="color:${v > 0 ? C.up : v < 0 ? C.down : '#999'}">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
}

export function _dgCard(title, note, body) {
  return `<div style="border:1px solid #222;border-radius:6px;margin:8px 10px;overflow:hidden">
    <div style="padding:6px 10px;background:#181818;border-bottom:1px solid #222;display:flex;align-items:baseline;gap:8px">
      <span style="color:#ff9800;font-size:12px;font-weight:bold">${title}</span>
      ${note ? `<span style="color:#888;font-size:10px">${escHtml(note)}</span>` : ''}
    </div>
    <div style="font-size:11px;color:#ccc">${body}</div>
  </div>`;
}

export async function loadDigest() {
  const el = document.getElementById('wp-content-digest');
  el.innerHTML = '<div class="wp-ov-loading">正在读取每日速递...</div>';
  let data;
  try {
    const r = await fetchWithTimeout(`${API}/api/digest`);
    data = await r.json();
  } catch (e) {
    el.innerHTML = '<div class="wp-error" style="padding:16px;color:#e57373;font-size:12px">每日速递读取失败：' + escHtml(String(e)) + '</div>';
    return;
  }
  renderDigest(data);
  if (data.status === 'running') startDigestPolling();
}

export function refreshDigest() {
  fetchWithTimeout(`${API}/api/digest?action=refresh`).then(r => r.json()).then(data => {
    if (data.status === 'started' || data.status === 'running') {
      startDigestPolling();
      renderDigest(data);
    } else {
      renderDigest(data);
    }
  }).catch(() => {});
}

export function startDigestPolling() {
  stopDigestPolling();
  _digestTimer = setInterval(() => {
    fetchWithTimeout(`${API}/api/digest`).then(r => r.json()).then(data => {
      if (data.status === 'running') renderDigest(data);
      else { stopDigestPolling(); renderDigest(data); }
    }).catch(() => {});
  }, 2000);
}

export function stopDigestPolling() {
  if (_digestTimer) { clearInterval(_digestTimer); _digestTimer = null; }
}

export function _digestSymbols(dg) {
  if (!dg) return [];
  const out = [];
  const push = s => { if (s && !out.includes(s)) out.push(s); };
  ((dg.recent_signals || {}).groups || []).forEach(g => g.records.forEach(r => push(r.symbol)));
  ((dg.performance || {}).matured || []).forEach(r => push(r.symbol));
  const ps = dg.pool_scan || {};
  (ps.buy || []).forEach(r => push(r.symbol));
  (ps.others || []).forEach(r => push(r.symbol));
  return out;
}

export function renderDigest(data) {
  const el = document.getElementById('wp-content-digest');
  if (!el) return;
  window._digestLastData = data;
  const running = data.status === 'running';
  const dg = data.digest || null;
  const meta = dg ? dg.meta : null;
  const statusText = { idle: '尚未生成', running: '生成中…', done: '已生成', error: '生成失败' }[data.status] || data.status;
  const btn = running
    ? '<button disabled style="background:#333;border:none;color:#888;padding:4px 12px;cursor:default;font-size:11px">生成中…</button>'
    : '<button onclick="refreshDigest()" style="background:#ff9800;border:none;color:#000;padding:4px 12px;cursor:pointer;font-size:11px">生成今日速递</button>';
  const prog = running ? `
    <div style="flex:1;min-width:160px;max-width:260px">
      <div style="font-size:10px;color:#ff9800;margin-bottom:2px">${escHtml(data.stage || '')} ${data.progress || 0}%</div>
      <div class="scan-bar-bg" style="height:6px"><div class="scan-bar-fill" style="width:${Math.min(100, data.progress || 0)}%"></div></div>
    </div>` : '';
  const head = `
    <div style="display:flex;gap:10px;padding:8px 12px;border-bottom:1px solid #222;font-size:11px;color:#aaa;align-items:center;flex-wrap:wrap">
      ${btn}
      <span>状态 <b style="color:${running ? '#ff9800' : '#eee'}">${statusText}</b></span>
      ${meta && meta.generated_at ? `<span>生成于 <b style="color:#ddd">${escHtml(meta.generated_at)}</b></span>` : ''}
      ${meta && meta.elapsed_sec != null ? `<span>耗时 <b style="color:#ddd">${meta.elapsed_sec}s</b></span>` : ''}
      ${running ? prog : ''}
      ${data.error ? `<span style="color:#e57373">${escHtml(data.error)}</span>` : ''}
    </div>`;

  let body = '';
  if (!dg) {
    body = data.status === 'error'
      ? `<div style="padding:20px;color:#e57373;font-size:12px">${escHtml(data.error || '生成失败')}</div>`
      : `<div style="padding:20px;color:#888;font-size:12px">还没有速递——点击「生成今日速递」聚合：大盘环境、最近新增信号、历史战绩回顾、核心池（自动）全量扫描与历史统计摘要。</div>`;
  } else {
    body = _digestSections(dg);
  }
  const foot = `
    <div style="padding:6px 12px 8px;font-size:10px;color:#888;line-height:1.6">
      口径：信号档案记录的是最终 action（含后处理）；历史统计为原始 run_analysis 输出，两者不可混用。自用参考，非投资建议。
    </div>`;
  el.innerHTML = head + body + foot;

  // 名称补齐：学到新名称后重渲染一次（seq 防过期覆盖）
  const symbols = _digestSymbols(dg);
  if (symbols.length) {
    const seq = ++_digestRenderSeq;
    _resolveSymbolNames(symbols).then(learned => {
      if (learned && seq === _digestRenderSeq && window._digestLastData) renderDigest(window._digestLastData);
    });
  } else {
    _digestRenderSeq++;
  }
}

export function _digestSections(dg) {
  const parts = [];

  // —— 大盘环境 ——
  const m = dg.market || {};
  let marketHtml;
  if (m.error) marketHtml = `<span style="color:#e57373">${escHtml(m.error)}</span>`;
  else if (m.close != null) {
    const pctCls = (m.pct || 0) >= 0 ? C.up : C.down;
    const r20 = m.r20 == null ? '--' : ((m.r20 > 0 ? '+' : '') + m.r20.toFixed(1) + '%');
    const b = m.breadth;
    marketHtml = `上证 <b style="color:#eee">${m.close}</b> <span style="color:${pctCls}">${m.pct > 0 ? '+' : ''}${(m.pct || 0).toFixed(2)}%</span> · 20日 <span style="color:${(m.r20 || 0) >= 0 ? C.up : C.down}">${r20}</span>` +
      (b ? ` · ${b.up}涨${b.down}跌${b.breadth_ratio != null ? `（${(b.breadth_ratio * 100).toFixed(0)}%上涨）` : ''}` : '');
  } else marketHtml = '<span style="color:#888">大盘环境暂不可用</span>';
  parts.push(`<div style="padding:8px 12px;border-bottom:1px solid #222;font-size:12px;color:#ccc">📌 大盘环境　${marketHtml}</div>`);

  // —— ① 最近新增信号 ——
  const rs = dg.recent_signals || {};
  let recentHtml = '';
  if (rs.error) recentHtml = `<div style="padding:10px;color:#e57373">${escHtml(rs.error)}</div>`;
  const groups = (rs.groups || []).slice(0, Math.max(1, _digestDays));
  if (!recentHtml && (rs.groups || []).length === 0) recentHtml = `<div style="padding:10px;color:#888">最近没有新增信号——产生买卖信号后会自动落档。</div>`;
  if (!recentHtml) {
    const maxD = Math.max(1, Math.min(5, (rs.groups || []).length));
    const dayOpts = [1, 3, 5].filter(d => d <= maxD).map(d =>
      `<option value="${d}" ${_digestDays === d ? 'selected' : ''}>最近 ${d} 个信号日</option>`).join('');
    const rows = groups.map(g => g.records.map(rec => {
      const nm = _knownName(rec.symbol);
      return `<tr>
        <td style="padding:4px 8px">${g.trigger_date}</td>
        <td style="padding:4px 6px"><a href="#" data-act="analyze" data-code="${escHtml(rec.symbol)}" style="color:#ff9800;text-decoration:none">${escHtml(rec.symbol)}</a>${nm ? `<div style="color:#888;font-size:10px">${escHtml(nm)}</div>` : ''}</td>
        <td style="padding:4px 6px">${_digestName(rec.signal_type)}</td>
        <td style="padding:4px 6px">${escHtml(rec.action || '')}</td>
        <td style="padding:4px 6px">${rec.snapshot_close != null ? rec.snapshot_close : '--'}</td>
      </tr>`;
    }).join('')).join('');
    recentHtml = `
      <div style="display:flex;gap:8px;padding:6px 12px;align-items:center;font-size:11px;color:#888">
        <select onchange="_digestDays=parseInt(this.value,10);renderDigest(window._digestLastData)" style="background:#111;color:#ccc;border:1px solid #333;font-size:11px">${dayOpts}</select>
        <span>共 ${groups.reduce((n, g) => n + g.records.length, 0)} 条 · 默认排除窗口内重复</span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;color:#ccc">
        <thead><tr style="color:#777;text-align:left;border-bottom:1px solid #222">
          <th style="padding:4px 8px">信号日</th><th style="padding:4px 6px">代码</th>
          <th style="padding:4px 6px">类型</th><th style="padding:4px 6px">动作</th><th style="padding:4px 6px">信号价</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }
  parts.push(_dgCard('① 最近新增信号', '来源：信号档案（记录最终 action，含后处理）', recentHtml));

  // —— ② 历史战绩回顾 ——
  const pf = dg.performance || {};
  let perfHtml = '';
  if (pf.error) perfHtml = `<div style="padding:10px;color:#e57373">${escHtml(pf.error)}</div>`;
  const ov = pf.overview || {};
  const winStr = ov.buy_20d_win_rate_pct == null ? '--' : ov.buy_20d_win_rate_pct.toFixed(1) + '%';
  const avgStr = ov.buy_20d_avg_return_pct == null ? '--' : (ov.buy_20d_avg_return_pct > 0 ? '+' : '') + ov.buy_20d_avg_return_pct.toFixed(2) + '%';
  const ovHtml = pf.overview ? `
    <div style="display:flex;gap:14px;padding:6px 12px;border-bottom:1px solid #222;font-size:11px;color:#aaa;flex-wrap:wrap">
      <span>总信号 <b style="color:#eee">${ov.total || 0}</b></span>
      <span>买侧20日样本 <b style="color:#eee">${ov.buy_20d_count || 0}</b></span>
      <span>20日上涨比例 <b style="color:${(ov.buy_20d_win_rate_pct || 0) >= 50 ? C.up : C.down}">${winStr}</b></span>
      <span>20日平均收益 <b style="color:${(ov.buy_20d_avg_return_pct || 0) >= 0 ? C.up : C.down}">${avgStr}</b></span>
      ${pf.window_days ? `<span>到期窗口：最近 ${pf.window_days} 个自然日</span>` : ''}
    </div>` : '';
  const matured = pf.matured || [];
  let maturedHtml;
  if (matured.length === 0) maturedHtml = `<div style="padding:10px;color:#888">窗口内没有到期的信号战绩（生成前已自动补记）</div>`;
  else {
    const rows = matured.map(r => {
      const nm = _knownName(r.symbol);
      return `<tr>
        <td style="padding:4px 6px"><a href="#" data-act="analyze" data-code="${escHtml(r.symbol)}" style="color:#ff9800;text-decoration:none">${escHtml(r.symbol)}</a>${nm ? `<div style="color:#888;font-size:10px">${escHtml(nm)}</div>` : ''}</td>
        <td style="padding:4px 6px">${_digestName(r.signal_type)}</td>
        <td style="padding:4px 6px">${escHtml(r.action || '')}</td>
        <td style="padding:4px 6px">${r.horizon}日</td>
        <td style="padding:4px 6px">${r.asof}</td>
        <td style="padding:4px 6px">${_dgCell(r.return_pct)}</td>
      </tr>`;
    }).join('');
    maturedHtml = `<table style="width:100%;border-collapse:collapse;font-size:11px;color:#ccc">
      <thead><tr style="color:#777;text-align:left;border-bottom:1px solid #222">
        <th style="padding:4px 6px">代码</th><th style="padding:4px 6px">类型</th><th style="padding:4px 6px">动作</th>
        <th style="padding:4px 6px">视界</th><th style="padding:4px 6px">到期日</th><th style="padding:4px 6px">收益</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }
  parts.push(_dgCard('② 历史战绩回顾', '最近 7 个自然日到期的 5/10/20/60 日收益（先补记后统计）', ovHtml + maturedHtml));

  // —— ③ 核心池全量扫描 ——
  const ps = dg.pool_scan || {};
  let scanHtml = '';
  if (ps.error) scanHtml = `<div style="padding:10px;color:#e57373">${escHtml(ps.error)}</div>`;
  if (!scanHtml && ps.total === 0) scanHtml = `<div style="padding:10px;color:#888">核心池（手动）为空——先把股票加入核心池（手动）再生成速递。</div>`;
  if (!scanHtml) {
    const mkRow = r => {
      const nm = _knownName(r.symbol);
      return `<tr>
        <td style="padding:4px 6px"><a href="#" data-act="analyze" data-code="${escHtml(r.symbol)}" style="color:#ff9800;text-decoration:none">${escHtml(r.symbol)}</a>${nm ? `<div style="color:#888;font-size:10px">${escHtml(nm)}</div>` : ''}</td>
        <td style="padding:4px 6px">${r.price != null ? r.price : '--'}</td>
        <td style="padding:4px 6px">${sbBadge(r.action)}</td>
        <td style="padding:4px 6px">${r.score != null ? r.score : '--'}</td>
        <td style="padding:4px 6px">${r.confidence != null ? r.confidence : '--'}</td>
        <td style="padding:4px 6px">${r.m_score != null ? r.m_score : '--'}</td>
        <td style="padding:4px 6px">${escHtml(r.position_advice || '')}</td>
        <td style="padding:4px 6px">${r.risk_reward != null ? r.risk_reward : '--'}</td>
      </tr>`;
    };
    const buyRows = (ps.buy || []).map(mkRow).join('');
    const otherRows = (ps.others || []).map(mkRow).join('');
    const head = `<tr style="color:#777;text-align:left;border-bottom:1px solid #222">
      <th style="padding:4px 6px">代码</th><th style="padding:4px 6px">现价</th><th style="padding:4px 6px">动作</th>
      <th style="padding:4px 6px">评分</th><th style="padding:4px 6px">置信</th><th style="padding:4px 6px">M分</th>
      <th style="padding:4px 6px">仓位建议</th><th style="padding:4px 6px">盈亏比</th>
    </tr>`;
    scanHtml = `
      <div style="display:flex;gap:14px;padding:6px 12px;border-bottom:1px solid #222;font-size:11px;color:#aaa;flex-wrap:wrap">
        <span>核心池（自动） <b style="color:#eee">${ps.total} 只</b></span>
        <span>买侧 <b style="color:${C.up}">${(ps.buy || []).length}</b></span>
        <span>观望/卖出 <b style="color:#aaa">${(ps.others || []).length}</b></span>
        ${ps.failed_count ? `<span style="color:#e57373">获取失败 ${ps.failed_count} 只${ps.failed_symbols && ps.failed_symbols.length ? '：' + escHtml(ps.failed_symbols.join('、')) : ''}</span>` : ''}
      </div>
      ${(ps.buy || []).length ? `<table style="width:100%;border-collapse:collapse;font-size:11px;color:#ccc"><thead>${head}</thead><tbody>${buyRows}</tbody></table>` : '<div style="padding:10px;color:#888">今日核心池（自动）无买入信号</div>'}
      ${(ps.others || []).length ? `<details style="border-top:1px solid #222"><summary style="padding:6px 12px;font-size:11px;color:#888;cursor:pointer">观望/卖出 ${(ps.others || []).length} 只（点击展开）</summary><table style="width:100%;border-collapse:collapse;font-size:11px;color:#ccc"><thead>${head}</thead><tbody>${otherRows}</tbody></table></details>` : ''}`;
  }
  parts.push(_dgCard('③ 核心池（自动）全量扫描', '仅日线 · 并行 ≤15 · 只读不落档（不回写信号档案）', scanHtml));

  // —— ④ 历史统计摘要 ——
  const ss = dg.stats_summary || {};
  let statsHtml = '';
  if (ss.error) statsHtml = `<div style="padding:10px;color:#e57373">${escHtml(ss.error)}</div>`;
  else {
    const c6 = (block) => block == null
      ? '<td style="padding:4px 6px">--</td>'
      : `<td style="padding:4px 6px"><b>${block.n}</b><span style="color:#888">（${block.win_rate == null ? '--' : block.win_rate.toFixed(1) + '%'} / ${block.avg_return == null ? '--' : (block.avg_return > 0 ? '+' : '') + block.avg_return.toFixed(2) + '%'}）</span>${block.insufficient_sample ? ' <span style="color:#ffd54f">⚠样本不足</span>' : ''}</td>`;
    const row = (label, block) => `<tr><td style="padding:4px 6px;font-weight:bold">${label}</td>${[5, 10, 20, 60].map(h => c6(block && block['r' + h])).join('')}</tr>`;
    const byActionHtml = Object.keys(ss.by_action || {}).map(k => row(k, ss.by_action[k])).join('');
    statsHtml = `
      <div style="padding:6px 12px;font-size:10px;color:#888;border-bottom:1px solid #222">快照 ${escHtml(ss.snapshot_id || '')} · 报告 ${escHtml(ss.report_path || '')} · 口径：原始 run_analysis（不含 app 后处理）</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;color:#ccc">
        <thead><tr style="color:#777;text-align:left;border-bottom:1px solid #222">
          <th style="padding:4px 6px">分组</th><th style="padding:4px 6px">r5（n·胜率/均值%）</th><th style="padding:4px 6px">r10</th><th style="padding:4px 6px">r20</th><th style="padding:4px 6px">r60</th>
        </tr></thead>
        <tbody>${row('总体', ss.overall)}${byActionHtml}</tbody>
      </table>`;
  }
  parts.push(_dgCard('④ 历史统计摘要', '去重后·参与统计口径，n<10 标注样本不足', statsHtml));

  return parts.join('');
}




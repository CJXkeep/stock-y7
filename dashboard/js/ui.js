// ==================== 通用 UI 层（improvements #13） ====================
// 转义/事件委托/术语即点即译/风险大白话/toast/搜索推荐/首访引导/更多菜单。
import { C, S } from './shared.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze, setMode, toggleSettings, doLogout, fxEnabled } from './main.js';
import { toggleStar, openSbSection, toggleWatchOverview, sbToggleCollapse, renameGroupInline, renameGroupInlineById, deleteGroup, moveStock, pinStock, removeFromWatchlist, hideCtxMenu } from './watchlist.js';
import { openScan, renderArchivedRun, exportScanCsv, deleteScanRun, analyzeFromScan, scanPollRetry } from './scan.js';
import { poolNote, poolMove, poolRemove, poolAddCurrent,
  journalSetType, journalSetSymbol, journalToggleDupes, poolSetIndustry, digestSetDays } from './journal.js';
import { openDoc, pickSnapshot, evalRefresh, evalSensitivity,
         correctToggle, correctPayload, correctValidate, correctExecute } from './evaluation.js';
import { candAdd, candRemove, candStatus, candNote,
         candImportScan, candValidateStart, candOpenDoc } from './candidates.js';
export const RISK_EXPLAIN = [
  { codes: ['price_below_ma20'], kws: ['跌破MA20'],
    text: '股价跌破20日均线（MA20）：短期趋势生命线失守，趋势转弱。',
    advice: '不宜急于买入，等待收盘价重新站回MA20再关注。' },
  { codes: ['price_down_volume_up'], kws: ['价跌量增'],
    text: '价跌量增：下跌同时成交量放大，像恐慌抛售，抛压比较重。',
    advice: '不要急着抄底，等量能萎缩、价格企稳后再看。' },
  { codes: ['obv_down'], kws: ['OBV下降', 'OBV走低', 'OBV下行'],
    text: 'OBV能量潮下降：资金在悄悄撤退，上涨缺乏"弹药"支撑。',
    advice: '对买入信号多一分警惕，优先等资金回流迹象。' },
  { codes: ['ma20_down'], kws: ['MA20向下', 'MA20下行'],
    text: '20日均线方向向下：短期趋势整体朝下。',
    advice: '顺势而为，把反弹当减仓机会而不是加仓机会。' },
  { codes: ['price_below_ma60'], kws: ['受压60日', '60日决策线'],
    text: '股价运行在60日决策线下方：上方存在中期套牢压力，反弹空间受限。',
    advice: '降低盈利预期，有效突破季线之前不重仓。' },
  { kwGroup: true, kws: ['市场环境偏空', '大盘偏空', '大盘环境偏空'],
    text: '大盘环境偏空：多数股票在跌，逆势操作胜率低。',
    advice: '控制总仓位，轻仓或空仓等大盘转暖。' },
  { kwGroup: true, kws: ['倒挂'],
    text: '盈亏比倒挂：可能赚的空间还不如可能亏的多，这笔买卖不划算。',
    advice: '放弃本次入场，等待更低的买点或更高的目标价。' },
  { kwGroup: true, kws: ['偏低'],
    text: '盈亏比偏低：收益空间相对止损距离优势不足。',
    advice: '可等价格更接近止损位时再考虑，提高盈亏比。' },
  // improvements #7：全量覆盖后端风险文案（守护测试从 app.py/signal_engine.py 抽取模板逐一校验）
  { kwGroup: true, kws: ['量价配合不佳'],
    text: '量价配合不佳：价格走势得不到成交量支持，上涨"没底气"，信号可靠性下降。',
    advice: '降低仓位或先观望，等量能回升、量价重新配合后再考虑。' },
  { kwGroup: true, kws: ['处于下降趋势'],
    text: '处于下降趋势：股价整体重心下移，逆势抄底胜率低。',
    advice: '不要急于买入，等趋势企稳（如收盘重新站回关键均线）再关注。' },
  { kwGroup: true, kws: ['勉强达标'],
    text: '盈亏比勉强达标：可能赚的空间只比可能亏的略大一点，安全垫很薄。',
    advice: '轻仓参与或不参与，优先寻找盈亏比≥2 的更好位置。' },
  { kwGroup: true, kws: ['风险收益比良好', '盈亏比良好'],
    text: '盈亏比良好：可能赚的空间明显大于可能亏的距离，是值得考虑的位置。',
    advice: '仍要按计划设好止损、分批建仓，避免单笔重仓。' },
];

export function explainRisks(signal) {
  const out = [];
  const seen = {};
  const sources = []
    .concat(signal.risk_warnings || [])
    .concat(signal.risk_notes || [])
    .concat(signal.veto_reason ? [signal.veto_reason] : []);
  const codes = signal.risk_codes || [];
  for (const item of sources) {
    if (!item) continue;
    let hit = null;
    for (const r of RISK_EXPLAIN) {
      if (!hit && r.codes && r.codes.some(c => codes.includes(c))) hit = r;
      if (!hit && item && r.kws && r.kws.some(k => String(item).includes(k))) hit = r;
    }
    const key = hit ? hit.text : String(item);
    if (seen[key]) continue;
    seen[key] = 1;
    out.push(hit
      ? { text: glossarize(hit.text), advice: hit.advice }
      : { text: glossarize(String(item)), advice: '结合仓位管理谨慎对待该信号。' });
  }
  // M分偏空即使没进 risk_warnings 也补一条（小白最该知道的大盘环境）
  const m = signal.momentum || {};
  if (m.m_score != null && m.m_score < 30 && !seen['市场环境偏空']) {
    seen['市场环境偏空'] = 1;
    const r = RISK_EXPLAIN.find(x => x.kws && x.kws.includes('市场环境偏空'));
    out.push({ text: glossarize(r.text), advice: r.advice });
  }
  return out;
}

export function riskBannerHtml(risks) {
  if (!risks || !risks.length) return '';
  const rows = risks.map(r =>
    `<div class="rb-item"><div class="rb-text">• ${r.text}</div><div class="rb-advice">建议：${escHtml(r.advice)}</div></div>`
  ).join('');
  return `
    <div class="risk-banner">
      <div class="rb-head">⚠ 有 ${risks.length} 条风险需要注意</div>
      ${rows}
      <div class="rb-foot">以上为规则解读，仅供参考，非投资建议。</div>
    </div>`;
}

export function _applyTermChips(safeHtml) {
  if (S._mode !== 'simple' || !window.GLOSSARY_TERMS) return safeHtml;
  return safeHtml.split(/(<[^>]+>)/g).map(part => {
    if (!part || part.startsWith('<')) return part;   // 标签原样保留
    let s = part;
    for (const t of window.GLOSSARY_TERMS) {
      const re = new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'g');
      s = s.replace(re, '<span class="gl-chip" data-term="' + t + '">$1</span>');
    }
    return s;
  }).join('');
}
export function glossarize(text) {
  const safe = escHtml(text == null ? '' : String(text));
  return _applyTermChips(safe);
}

// 术语气泡：同一时刻最多一个，点空白关闭
document.addEventListener('click', function (e) {
  const chip = e.target.closest && e.target.closest('.gl-chip');
  const pop = document.getElementById('glossary-pop');
  if (!pop) return;
  if (chip) {
    e.stopPropagation();
    const g = window.GLOSSARY && window.GLOSSARY[chip.dataset.term];
    if (!g) return;
    pop.innerHTML =
      `<div class="gp-term">${escHtml(chip.dataset.term)}<span class="gp-full">${escHtml(g.full)}</span></div>` +
      `<div class="gp-plain">${escHtml(g.plain)}</div>` +
      `<div class="gp-ex">例：${escHtml(g.example)}</div>` +
      (g.limit ? `<div class="gp-limit">⚠ 局限：${escHtml(g.limit)}</div>` : '');
    pop.style.display = 'block';
    const r = chip.getBoundingClientRect();
    let left = Math.min(r.left, window.innerWidth - 310);
    let top = r.bottom + 8;
    pop.style.left = '0px'; pop.style.top = '0px';
    if (top + pop.offsetHeight > window.innerHeight) top = Math.max(8, r.top - pop.offsetHeight - 8);
    pop.style.left = Math.max(8, left) + 'px';
    pop.style.top = top + 'px';
    return;
  }
  if (!pop.contains(e.target)) pop.style.display = 'none';
});

// 信号"为什么"解释
export function whyTextFor(sigText) {
  const s = String(sigText || '');
  if (/趋势强势|趋势上升/.test(s)) return '均线呈多头排列、价格在上升通道内，说明买方掌握主动权。';
  if (/持仓\(N=|系统/.test(s)) return '唐奇安通道突破系统：价格创出N日新高后持有，跌破对应止损位才离场。';
  if (/空头平仓/.test(s)) return '前期做空/避险的资金开始回补买入，通常会推动价格上行。';
  if (/净流入/.test(s)) return '主力资金当天是净买入的，有真金白银在推升股价。';
  if (/流出/.test(s)) return '主力资金当天净卖出，短期要提防回落风险。';
  if (/头肩底/.test(s)) return '经典底部反转形态，突破颈线后的理论涨幅约等于头部到颈线的高度。';
  if (/量价/.test(s)) return '价格上涨伴随成交量放大，量价配合健康，涨得"有底气"。';
  if (/M\(|大盘|市场/.test(s)) return '来自市场环境评估模块：反映整个A股大盘的方向和强弱。';
  return '由五模块规则引擎在满足特定指标条件时触发，仅供参考。';
}
export function toggleWhy(el) {
  const body = el.parentElement.querySelector('.sig-why-body');
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  el.textContent = open ? '为什么？' : '收起';
}

// 分数数字滚动（标准/炫酷档）
export function countUpScore(target) {
  const el = document.getElementById('sum-score');
  if (!el) return;
  const prefix = (el.textContent || '').replace(/\d+.*$/, '');   // 保留「综合 」等前缀
  if (!fxEnabled() || document.hidden) { el.textContent = prefix + target + '分'; return; }
  const t0 = performance.now(), dur = 600;
  function step(t) {
    const p = Math.min(1, (t - t0) / dur);
    el.textContent = prefix + Math.round(target * p) + '分';
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// 右侧卡片依次淡入上滑（标准/炫酷档）
export function fxCardStagger() {
  if (S._fxLevel === 'off') return;
  const cards = document.querySelectorAll('.right-panel > div');
  let i = 0;
  cards.forEach(c => {
    if (c.offsetParent === null) return; // display:none 跳过
    c.classList.remove('fx-enter');
    void c.offsetWidth; // 强制重置动画
    c.style.animationDelay = (i * 60) + 'ms';
    c.classList.add('fx-enter');
    const delay = i;
    setTimeout(() => { c.classList.remove('fx-enter'); c.style.animationDelay = ''; }, 1000 + delay * 60);
    i++;
  });
}

const searchInput = document.getElementById('search-input');
const searchResults = document.getElementById('search-results');
let searchTimer = null;

searchInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  const kw = searchInput.value.trim();
  if (!kw) { showHotStocksPanel(); return; }
  searchTimer = setTimeout(() => doSuggest(kw), 250);
});

// improvements #12：热门股从顶栏移入搜索框聚焦推荐面板
const HOT_STOCKS = [
  { code: '600519', name: '茅台' },
  { code: '601899', name: '紫金矿业' },
  { code: '600276', name: '恒瑞医药' },
  { code: '588170', name: '科创半导体' },
  { code: '000001', name: '平安银行' },
];
export function showHotStocksPanel() {
  if (document.activeElement !== searchInput) return;   // 失焦后迟到的渲染不再打开联想
  clearTimeout(searchTimer);
  searchResults.innerHTML =
    '<div class="sr-hot-title">热门股票</div>' +
    HOT_STOCKS.map(s =>
      `<div class="sr-item" data-act="selectStock" data-code="${escHtml(s.code)}" data-name="${escHtml(s.name)}">
        <span class="code">${escHtml(s.code)}</span><span class="name">${escHtml(s.name)}</span>
      </div>`
    ).join('');
  searchResults.style.display = 'block';
}
searchInput.addEventListener('focus', () => {
  if (!searchInput.value.trim()) showHotStocksPanel();
});

searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    searchResults.style.display = 'none';
    const kw = searchInput.value.trim();
    if (/^\d{6}$/.test(kw)) analyze(kw);
    else doSearch();
  }
});

// blur 延迟收起联想（fe-smoke：联想下拉右缘会盖住周期工具栏，误点联想项
// 会分析错误的股票；150ms 延迟保证联想项自身的 click 先于隐藏执行）。
searchInput.addEventListener('blur', () => {
  setTimeout(() => { searchResults.style.display = 'none'; }, 150);
});

document.addEventListener('click', e => {
  if (!e.target.closest('.search-wrap')) searchResults.style.display = 'none';
});

export async function doSuggest(kw) {
  if (document.activeElement !== searchInput) return;   // 失焦后迟到的联想响应不再打开下拉
  try {
    const r = await fetchWithTimeout(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);
    const data = await r.json();
    if (document.activeElement !== searchInput) return; // 响应等待期间已失焦同理
    if (data.results && data.results.length) {
      searchResults.innerHTML = data.results.map(s =>
        `<div class="sr-item" data-act="selectStock" data-code="${escHtml(s.code)}" data-name="${escHtml(s.name)}">
          <span class="code">${escHtml(s.code)}</span><span class="name">${escHtml(s.name)}</span>
        </div>`
      ).join('');
      searchResults.style.display = 'block';
    } else searchResults.style.display = 'none';
  } catch(e) {}
}

export async function doSearch() {
  const kw = searchInput.value.trim();
  if (!kw) return;
  searchResults.style.display = 'none';
  if (/^\d{6}$/.test(kw)) { analyze(kw); return; }
  try {
    const r = await fetchWithTimeout(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);
    const data = await r.json();
    if (data.results && data.results.length) selectStock(data.results[0].code, data.results[0].name);
  } catch(e) {}
}

export function selectStock(code, name) {
  searchResults.style.display = 'none';
  searchInput.value = code;
  analyze(code);
}

export function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ==================== XSS 加固：统一事件委托（improvements #1） ====================
// 规范：innerHTML 模板中的动态文本必须经 escHtml() 转义；
// 禁止 onclick="fn('${var}')" 式内嵌字符串拼接事件属性，一律改用
// data-act（click）/ data-dblact（dblclick）/ data-chgact（change）+ data-* 参数，
// 由下方 document 级委托分发。静态模板（无插值）的 inline handler 过渡期保留。
export const DELEGATED_ACTIONS = {
  analyze: el => analyze(el.dataset.code),
  selectStock: el => selectStock(el.dataset.code, el.dataset.name || ''),
  analyzeFromScan: el => analyzeFromScan(el.dataset.code),
  renderArchivedRun: el => renderArchivedRun(el.dataset.runId),
  exportScanCsv: el => exportScanCsv(el.dataset.runId),
  deleteScanRun: el => deleteScanRun(el.dataset.runId),
  scanRetry: () => scanPollRetry(),
  sbToggleCollapse: el => sbToggleCollapse(el.dataset.gid),
  renameGroupInline: el => renameGroupInline(el, el.dataset.gid),
  moveStock: el => moveStock(el.dataset.code, el.dataset.gid),
  pinStock: el => pinStock(el.dataset.code, el.dataset.gid),
  removeStockCtx: el => { hideCtxMenu(); removeFromWatchlist(el.dataset.code); },
  renameGroupMenu: el => { hideCtxMenu(); renameGroupInlineById(el.dataset.gid); },
  toggleCollapseMenu: el => { hideCtxMenu(); sbToggleCollapse(el.dataset.gid); },
  deleteGroupMenu: el => { hideCtxMenu(); deleteGroup(el.dataset.gid); },
  poolNote: el => poolNote(el.dataset.code, el.value),
  // 信号档案 / 核心池 / 速递的筛选控件（ESM 化遗留的内联 handler 已改委托）
  journalSetType: el => journalSetType(el.value),
  journalSetSymbol: el => journalSetSymbol(el.value),
  journalToggleDupes: el => journalToggleDupes(el.checked),
  poolSetIndustry: el => poolSetIndustry(el.value),
  digestSetDays: el => digestSetDays(el.value),
  poolMove: el => poolMove(el.dataset.code, parseInt(el.dataset.dir, 10)),
  poolRemove: el => poolRemove(el.dataset.code),
  poolAddCurrent: el => poolAddCurrent(el.dataset.code),
  evalPickSnapshot: el => pickSnapshot(el),
  evalOpenDoc: el => openDoc(el),
  evalRefresh: () => evalRefresh(),
  evalSensitivity: () => evalSensitivity(),
  evalCorrectToggle: () => correctToggle(),
  evalCorrectAction: () => correctPayload(),
  evalCorrectValidate: () => correctValidate(),
  evalCorrectExecute: el => correctExecute(el),
  candAdd: () => candAdd(),
  candRemove: el => candRemove(el),
  candStatus: el => candStatus(el),
  candNote: el => candNote(el),
  candImportScan: () => candImportScan(),
  candValidateStart: () => candValidateStart(),
  candOpenDoc: el => candOpenDoc(el),
  openScan: () => openScan(),
};

// improvements #8：小屏「更多」菜单
export function closeMoreMenu() {
  const m = document.getElementById('more-menu');
  if (m) m.style.display = 'none';
}
DELEGATED_ACTIONS.toggleMoreMenu = () => {
  const m = document.getElementById('more-menu');
  if (m) m.style.display = (m.style.display === 'none' || !m.style.display) ? 'block' : 'none';
};
DELEGATED_ACTIONS.moreStar = () => { closeMoreMenu(); toggleStar(); };
DELEGATED_ACTIONS.moreSettings = () => { closeMoreMenu(); toggleSettings(); };
DELEGATED_ACTIONS.openArchiveSeg = (el) => {
  const seg = el && el.dataset && el.dataset.seg;
  if (seg) openSbSection(seg);
};
DELEGATED_ACTIONS.openTaskSeg = (el) => {
  const seg = el && el.dataset && el.dataset.seg;
  if (seg) openSbSection(seg);
};
DELEGATED_ACTIONS.openWatchOverview = () => { toggleWatchOverview(); };
DELEGATED_ACTIONS.moreLogout = () => { closeMoreMenu(); doLogout(); };
document.addEventListener('click', e => {
  if (!e.target.closest('.more-wrap')) closeMoreMenu();
});

function _delegateDispatch(e, attr) {
  const el = e.target.closest('[' + attr + ']');
  if (!el) return;
  const fn = DELEGATED_ACTIONS[el.getAttribute(attr)];
  if (!fn) return;
  if (attr === 'data-act') {
    if (el.tagName === 'A') e.preventDefault();
    if (el.hasAttribute('data-stop')) e.stopPropagation();
  }
  fn(el, e);
}
document.addEventListener('click', e => _delegateDispatch(e, 'data-act'));
document.addEventListener('dblclick', e => _delegateDispatch(e, 'data-dblact'));
document.addEventListener('change', e => _delegateDispatch(e, 'data-chgact'));


export function showToastMsg(msg) {
  const c = document.getElementById('toast-container'); if (!c) return;
  const d = document.createElement('div');
  d.className = 'toast msg-toast'; d.textContent = msg;
  c.appendChild(d);
  setTimeout(() => { d.classList.add('removing'); setTimeout(() => d.remove(), 350); }, 2200);
}


export function showToast(name, code, oldAction, newAction, price) {
  const container = document.getElementById('toast-container');
  const cls = newAction === '买入' ? 'buy' : newAction === '卖出' ? 'sell' : '';
  const oldColor = oldAction === '买入' ? C.up : oldAction === '卖出' ? C.down : '#ffc107';
  const newColor = newAction === '买入' ? C.up : newAction === '卖出' ? C.down : '#ffc107';
  const toast = document.createElement('div');
  toast.className = `toast ${cls}`;
  toast.onclick = () => removeToast(toast);
  toast.innerHTML = `
    <div style="font-weight:bold;font-size:14px;margin-bottom:4px">${escHtml(name)} (${escHtml(code)})</div>
    <div>信号变更：<span style="color:${oldColor}">${oldAction}</span> → <span style="color:${newColor};font-weight:bold">${newAction}</span></div>
    <div style="font-size:11px;color:#888;margin-top:4px">当前价 ${price ? price.toFixed(2) : '--'}</div>
  `;
  container.appendChild(toast);
  setTimeout(() => removeToast(toast), 8000);
}

export function removeToast(el) {
  if (!el || !el.parentNode) return;
  el.classList.add('removing');
  setTimeout(() => { if (el.parentNode) el.remove(); }, 300);
}


// ==================== 首访三步引导（improvements #9） ====================
const ONBOARD_KEY = 'qs_onboarded_v1';
let _onboardStep = 1;

function onboardShow() {
  const ov = document.getElementById('onboard-overlay');
  if (!ov) return;
  _onboardStep = 1;
  _renderOnboard();
  ov.style.display = 'flex';
}
function _renderOnboard() {
  document.querySelectorAll('.onboard-step').forEach(el =>
    el.classList.toggle('active', +el.dataset.step === _onboardStep));
  document.querySelectorAll('.od-dot').forEach((d, i) =>
    d.classList.toggle('active', i === _onboardStep - 1));
  const nxt = document.getElementById('onboard-next');
  if (nxt) nxt.textContent = _onboardStep >= 3 ? '开始使用' : '下一步';
}
function onboardFinish() {
  try { localStorage.setItem(ONBOARD_KEY, '1'); } catch (e) {}
  const ov = document.getElementById('onboard-overlay');
  if (ov) ov.style.display = 'none';
}
DELEGATED_ACTIONS.onboardNext = () => {
  if (_onboardStep >= 3) { onboardFinish(); return; }
  _onboardStep += 1;
  _renderOnboard();
};
DELEGATED_ACTIONS.onboardSkip = () => onboardFinish();

(function onboardInit() {
  let done = false;
  try { done = localStorage.getItem(ONBOARD_KEY) === '1'; } catch (e) {}
  if (!done) setTimeout(onboardShow, 600);
})();



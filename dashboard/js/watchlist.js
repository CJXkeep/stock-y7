// ==================== 自选/分组/侧边栏工作台（improvements #11/#13） ====================
import { C, S } from './shared.js';
import { escHtml, DELEGATED_ACTIONS, showToast, showToastMsg } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze, fxEnabled, _syncSbTabsAria } from './main.js';
import { loadOverview, loadJournal, loadPool, loadDigest, clearWatchChangeBadge } from './journal.js';
import { renderScanArchiveList } from './scan.js';
import { resizeAllChartsSafe } from './chart.js';

export function getSbSection() { return _sbSection; }

// 图表实例句柄由 chart.js 持有；侧边栏开合需要触发图表 resize，
// 通过注入回调避免反向依赖（由 main.js 启动时调用 registerResizeHook）。
let _resizeHook = null;
export function registerResizeHook(fn) { _resizeHook = fn; }
// ===== 自选股 & 历史记录 =====
const STORAGE_WATCH = 'qs_watchlist';
const STORAGE_HISTORY = 'qs_history';
const MAX_HISTORY = 30;
let _currentTab = 'watch';

// --- localStorage 读写（frontend-ux-v42 R2：分组模型；旧键只读不删） ---
const GKEY_GROUPS = 'qs_watch_groups';
const GKEY_STOCKS = 'qs_watch_stocks';

export function _lsGet(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; }
  catch (e) { return fallback; }
}
export function _lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); return true; } catch (e) { return false; }
}

export function getGroups() { return _lsGet(GKEY_GROUPS, [{ id: 'default', name: '我的自选', order: 0, collapsed: false, codes: [] }]); }
export function saveGroups(g) { _lsSet(GKEY_GROUPS, g); renderSidebar(); updateBadges(); _wlScheduleSync(); }
export function getStockMap() { return _lsGet(GKEY_STOCKS, {}); }
export function saveStockMap(m) { _lsSet(GKEY_STOCKS, m); _wlScheduleSync(); }

// ==================== 自选服务端持久化（improvements #11） ====================
// 服务端 data/watchlist.json 为唯一事实源；localStorage 仅作缓存（离线只读回退）。
// 写操作：先落本地缓存保证 UI 即时反馈，再防抖整体写穿服务端。
let _wlSyncTimer = null;

export function _wlSnapshot() {
  return { groups: getGroups(), stocks: getStockMap() };
}
export function _wlApply(data) {
  // 服务端数据回填本地缓存；返回是否发生实际变化
  if (!data || !Array.isArray(data.groups)) return false;
  const before = JSON.stringify([_lsGet(GKEY_GROUPS, null), _lsGet(GKEY_STOCKS, null)]);
  try {
    localStorage.setItem(GKEY_GROUPS, JSON.stringify(data.groups));
    localStorage.setItem(GKEY_STOCKS, JSON.stringify(data.stocks || {}));
  } catch (e) { return false; }
  const after = JSON.stringify([_lsGet(GKEY_GROUPS, null), _lsGet(GKEY_STOCKS, null)]);
  return before !== after;
}
export function _wlScheduleSync() {
  if (_wlSyncTimer) clearTimeout(_wlSyncTimer);
  _wlSyncTimer = setTimeout(_wlSyncPush, 1200);
}
export async function _wlSyncPush() {
  _wlSyncTimer = null;
  try {
    await fetchWithTimeout(`${API}/api/watchlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_wlSnapshot()),
    }, 8000);
  } catch (e) { console.warn('自选服务端同步失败（保留本地缓存，稍后重试）:', e); }
}
export async function _wlSyncInit() {
  // 启动对齐：服务端有数据则以服务端为真回填；服务端为空则把本地既有数据迁移上移（幂等）
  try {
    const r = await fetchWithTimeout(`${API}/api/watchlist`, {}, 8000);
    const remote = await r.json();
    const remoteEmpty = !remote || !Array.isArray(remote.groups) ||
      (remote.groups.every(g => !g.codes || !g.codes.length) && Object.keys(remote.stocks || {}).length === 0);
    if (!remoteEmpty) {
      if (_wlApply(remote)) { renderSidebar(); updateBadges(); renderWatchlist(); }
    } else {
      await _wlSyncPush();
    }
  } catch (e) { console.warn('自选初始化同步失败（进入离线缓存模式）:', e); }
}

// 旧版平铺 qs_watchlist 自动迁移（一次性；迁移失败保留原键不动）
export function migrateWatchlist() {
  let raw = null;
  try { raw = localStorage.getItem(STORAGE_WATCH); } catch (e) {}
  if (!raw || localStorage.getItem(GKEY_GROUPS)) return;
  let old = null;
  try { old = JSON.parse(raw); } catch (e) { console.warn('[watch-migrate] 旧自选解析失败，保留原键不迁移'); return; }
  if (!Array.isArray(old)) return;
  const stocks = {}; const codes = [];
  for (const s of old) {
    if (!s || !s.code) continue;
    codes.push(String(s.code));
    stocks[s.code] = { name: s.name || s.code, action: s.action || '', score: s.score || 0, addedAt: s.addedAt || Date.now(), pinned: false };
  }
  if (!_lsSet(GKEY_STOCKS, stocks)) return;
  if (!_lsSet(GKEY_GROUPS, [{ id: 'default', name: '我的自选', order: 0, collapsed: false, codes: codes }])) {
    try { localStorage.removeItem(GKEY_STOCKS); } catch (e) {}   // 写组失败则回滚
    return;
  }
  console.log('[watch-migrate] 已迁移', codes.length, '只自选到「我的自选」分组');
}

// 派生平铺列表：让老代码（wp面板/多股一览/updateBadges）无感继续工作
export function getWatchlist() {
  const stocks = getStockMap();
  const out = []; const seen = {};
  for (const g of getGroups()) for (const c of (g.codes || [])) {
    if (seen[c]) continue; seen[c] = 1;
    const st = stocks[c];
    if (st) out.push({ code: c, name: st.name, action: st.action || '', score: st.score || 0, addedAt: st.addedAt, price: st.price, pct: st.pct });
  }
  return out;
}
// 老代码写入路径：字段同步进股票详情表（不再写旧键）
export function saveWatchlist(list) {
  const m = getStockMap(); let dirty = false;
  for (const s of (list || [])) {
    if (s && s.code && m[s.code]) {
      for (const k of ['name', 'action', 'score', 'price', 'pct']) if (s[k] !== undefined && s[k] !== null) m[s.code][k] = s[k];
      dirty = true;
    }
  }
  if (dirty) saveStockMap(m);
}

export function addToGroup(code, name, gid) {
  const groups = getGroups();
  const g = groups.find(x => x.id === gid) || groups[0];
  if (!g) return;
  const m = getStockMap();
  if (!m[code]) m[code] = { name: name || code, action: '', score: 0, addedAt: Date.now(), pinned: false };
  else if (name) m[code].name = name;
  saveStockMap(m);
  if (!g.codes.includes(code)) { g.codes.unshift(code); saveGroups(groups); }
  updateStarButton(S.currentSymbol); renderWatchlist(); renderSidebar(); updateBadges();
}
export function removeStockEverywhere(code) {
  const groups = getGroups(); let changed = false;
  for (const g of groups) { const i = g.codes.indexOf(code); if (i >= 0) { g.codes.splice(i, 1); changed = true; } }
  if (changed) saveGroups(groups);
  const m = getStockMap(); if (m[code]) { delete m[code]; saveStockMap(m); }
}
export function getHistory() {
  try { return JSON.parse(localStorage.getItem(STORAGE_HISTORY) || '[]'); } catch(e) { return []; }
}
export function saveHistory(list) {
  localStorage.setItem(STORAGE_HISTORY, JSON.stringify(list));
}

// --- 自选股操作 ---
export function toggleStar() {
  if (!S.currentSymbol) return;
  const inWatch = getWatchlist().some(s => s.code === S.currentSymbol);
  const btn = document.getElementById('star-btn');
  if (inWatch) {
    removeFromWatchlist(S.currentSymbol);
  } else {
    addToGroup(S.currentSymbol, S._currentStockName || S.currentSymbol, _sbActiveGroup || 'default');
    if (btn && fxEnabled()) { btn.classList.remove('fx-pop'); void btn.offsetWidth; btn.classList.add('fx-pop'); }
  }
}

export function removeFromWatchlist(code) {
  const list = getWatchlist().filter(s => s.code !== code);
  saveWatchlist(list);
  updateStarButton(S.currentSymbol);
  renderWatchlist();
  updateBadges();
}

export function updateStarButton(symbol) {
  const btn = document.getElementById('star-btn');
  if (!btn || !symbol) return;
  const inWatch = getWatchlist().some(s => s.code === symbol);
  btn.textContent = inWatch ? '★' : '☆';
  btn.classList.toggle('starred', inWatch);
  btn.title = inWatch ? '从自选中移除' : '加入自选';
}

// --- 历史记录操作 ---
export function addHistory(code, name, action, score) {
  let list = getHistory();
  // 去重：移除已存在的相同code
  list = list.filter(s => s.code !== code);
  // 加到头部
  list.unshift({ code, name, action, score, time: Date.now() });
  // 限制数量
  if (list.length > MAX_HISTORY) list = list.slice(0, MAX_HISTORY);
  saveHistory(list);
  if (_sbSection === 'history') renderHistory();   // 历史分区打开时实时刷新
  updateBadges();
}

export function clearHistory() {
  saveHistory([]);
  renderHistory();
  updateBadges();
}

// --- 渲染 ---
export function renderWatchlist() {
  // 自选列表已迁至左侧工作台「自选」分区，统一由分组侧栏渲染
  renderSidebar();
}

export function renderHistory() {
  const el = document.getElementById('wp-content-history');
  const list = getHistory();
  if (!list.length) {
    el.innerHTML = '<div class="wp-empty"><span class="wp-empty-icon">🕐</span>暂无浏览记录<br>分析过的股票会自动留痕，方便回找</div>';
    return;
  }
  el.innerHTML = list.map(s => {
    const tag = sigTag(s.action, s.score);
    const t = fmtTime(s.time);
    return `<div class="wp-item" data-act="analyze" data-code="${escHtml(s.code)}">
      <span class="code">${escHtml(s.code)}</span>
      <span class="name">${escHtml(s.name)}</span>
      ${tag}
      <span class="time">${t}</span>
    </div>`;
  }).join('');
}

export function sigTag(action, score) {
  if (!action) return '<span class="sig-tag none">--</span>';
  const cls = action === '买入' ? 'buy' : action === '卖出' ? 'sell' : 'watch';
  return `<span class="sig-tag ${cls}">${action}${score ? ' ' + score : ''}</span>`;
}

export function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
  if (d.toDateString() === now.toDateString()) {
    return `今天 ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  }
  const yest = new Date(now); yest.setDate(yest.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return '昨天';
  return `${d.getMonth()+1}/${d.getDate()}`;
}


export function switchTab(tab) {
  // wp-panel 已迁入左侧工作台：自选股 tab 回路由到自选分区
  if (tab === 'watch' && document.getElementById('sb-pane-watch')) { openSbSection('watch'); return; }
  _currentTab = tab;
  // tab 样式（improvements #4：第二套 wp-tab 已移除，仅同步侧边栏高亮）
  document.querySelectorAll('.sb-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.sb === tab);
  });
  document.getElementById('wp-content-history').style.display = tab === 'history' ? 'block' : 'none';
  document.getElementById('wp-content-overview').style.display = tab === 'overview' ? 'block' : 'none';
  document.getElementById('wp-content-journal').style.display = tab === 'journal' ? 'block' : 'none';
  document.getElementById('wp-content-pool').style.display = tab === 'pool' ? 'block' : 'none';
  document.getElementById('wp-content-digest').style.display = tab === 'digest' ? 'block' : 'none';
  // 渲染内容
  if (tab === 'history') renderHistory();
  else if (tab === 'overview') loadOverview();
  else if (tab === 'journal') loadJournal();
  else if (tab === 'pool') loadPool();
  else if (tab === 'digest') loadDigest();
  // 底部操作栏
  const footer = document.getElementById('wp-footer');
  const list = tab === 'history' ? getHistory() : [];
  if (list.length > 0 && tab !== 'overview') {
    footer.style.display = 'flex';
    document.getElementById('wp-footer-info').textContent = `共 ${list.length} 条记录`;
    document.getElementById('wp-clear-btn').textContent = '清空历史';
  } else {
    footer.style.display = 'none';
  }
  // 多股一览打开时清除变更角标
  if (tab === 'overview') clearWatchChangeBadge();
}

export function clearCurrentTab() {
  if (_currentTab === 'watch') {
    saveWatchlist([]);
    renderWatchlist();
    updateStarButton(S.currentSymbol);
  } else {
    clearHistory();
  }
  updateBadges();
  switchTab(_currentTab);
}

export function updateBadges() {
  const wl = getWatchlist().length;
  const hl = getHistory().length;
  const wb = document.getElementById('watch-count');
  const hb = document.getElementById('history-count');
  if (wb) { wb.textContent = wl; wb.style.display = wl > 0 ? 'inline-block' : 'none'; }
  if (hb) { hb.textContent = hl; hb.style.display = hl > 0 ? 'inline-block' : 'none'; }
}


// 阻止面板内点击冒泡导致关闭
document.getElementById('wp-panel').addEventListener('click', e => e.stopPropagation());

// 跟踪当前股票名称（用于自选时获取名称）

// ==================== 左侧工作台（frontend迭代：分区 + 宽面板） ====================
let _sbOpen = true;
let _sbActiveGroup = 'default';
let _sbTimer = null;
let _sbSection = 'watch';   // watch | history | overview | journal | pool | digest | scan
const SB_SECTIONS = { watch: '自选股', history: '浏览记录', overview: '多股行情', journal: '信号档案', pool: '核心池', digest: '每日速递', scan: '扫描档' };
// improvements #4：各模块分区头部一句用途说明（12px）
const SB_SECTION_DESC = {
  history: '浏览记录：看过的股票自动留痕，方便回找与对比。',
  overview: '多股行情：自选分组的多股实时行情一览，涨跌与信号一屏尽收。',
  journal: '信号档案：分析产生的买卖信号自动留档，含后续涨跌验证。',
  pool: '核心池：精选股票池，是每日速递与批量扫描的数据底座。',
  digest: '每日速递：每天一份核心池信号汇总，收盘后自动生成。',
};

export function loadSbSection() {
  try { const v = localStorage.getItem('qs_sb_section'); if (v && SB_SECTIONS[v]) _sbSection = v; } catch (e) {}
}
// 切换分区（侧栏tab/面板内tab统一入口，不做收起）
export function openSbSection(sec) {
  if (!SB_SECTIONS[sec]) return;
  _sbSection = sec;
  try { localStorage.setItem('qs_sb_section', sec); } catch (e) {}
  if (!_sbOpen) _sbOpen = true;
  applySidebar();
  renderSbSection();
}
// 顶栏入口：同区且已展开 → 收起；否则切到该区并展开
export function toggleSbSection(sec) {
  if (SB_SECTIONS[sec] && _sbSection === sec && _sbOpen) { toggleSidebar(); return; }
  openSbSection(sec);
}
export function renderSbSection() {
  document.querySelectorAll('.sb-tab').forEach(t => t.classList.toggle('active', t.dataset.sb === _sbSection));
  _syncSbTabsAria();
  const pWatch = document.getElementById('sb-pane-watch');
  const pMods = document.getElementById('sb-pane-modules');
  const pScan = document.getElementById('sb-pane-scan');
  if (!pWatch || !pMods || !pScan) return;
  pWatch.classList.toggle('active', _sbSection === 'watch');
  pMods.classList.toggle('active', ['history', 'overview', 'journal', 'pool', 'digest'].includes(_sbSection));
  pScan.classList.toggle('active', _sbSection === 'scan');
  const title = document.getElementById('sb-title');
  if (title) title.textContent = SB_SECTIONS[_sbSection];
  const desc = document.getElementById('sb-pane-desc');
  if (desc) {
    const txt = SB_SECTION_DESC[_sbSection] || '';
    desc.textContent = txt;
    desc.style.display = txt ? 'block' : 'none';
  }
  if (_sbSection === 'watch') renderSidebar();
  else if (_sbSection === 'scan') renderScanArchiveList();
  else switchTab(_sbSection);   // 复用原面板渲染器，内部 tab 高亮同步
}

export function isMarketOpen() {
  const d = new Date(); const wd = d.getDay();
  if (wd === 0 || wd === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return (m >= 555 && m <= 695) || (m >= 775 && m <= 905); // 9:15-11:35 / 12:55-15:05
}
export function sidebarLoadState() {
  try { const v = localStorage.getItem('qs_sidebar_open'); _sbOpen = (v == null) ? true : v === '1'; } catch (e) {}
}
export function toggleSidebar() { _sbOpen = !_sbOpen; applySidebar(); try { localStorage.setItem('qs_sidebar_open', _sbOpen ? '1' : '0'); } catch (e) {} }

export function applySidebar() {
  const sb = document.getElementById('sidebar'); const t = document.getElementById('sb-toggle');
  if (sb) sb.classList.toggle('open', _sbOpen);
  document.body.classList.toggle('sb-open', _sbOpen);
  document.body.classList.toggle('sb-section-watch', _sbSection === 'watch');   // 驱动 --sb-w 宽度变量
  document.querySelectorAll('.sb-tab').forEach(x => x.classList.toggle('active', x.dataset.sb === _sbSection));
  _syncSbTabsAria();
  if (t) t.textContent = _sbOpen ? '◀' : '▶';
  setTimeout(resizeAllChartsSafe, (S._fxLevel === 'max') ? 220 : 0);
}

export function sbBadge(action) {
  if (!action) return '';
  const cls = action.includes('买') ? 'b' : action.includes('卖') ? 's' : 'w';
  const txt = action.replace('强烈', '').replace('谨慎', '');
  return `<span class="sb-badge ${cls}">${escHtml(txt)}</span>`;
}

export function renderSidebar() {
  const wrap = document.getElementById('sb-groups'); if (!wrap) return;
  const groups = getGroups().slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  const stocks = getStockMap();
  const frag = document.createDocumentFragment();   // 护栏：DocumentFragment 渲染
  let totalCodes = 0;

  groups.forEach(g => {
    const box = document.createElement('div');
    box.className = 'sb-group' + (g.id === _sbActiveGroup ? ' active' : '');
    box.dataset.gid = g.id;
    totalCodes += g.codes.length;

    // —— 组头 ——
    let up = 0, down = 0;
    g.codes.forEach(c => { const st = stocks[c]; if (st && st.pct != null) { if (st.pct > 0) up++; else if (st.pct < 0) down++; } });
    const h = document.createElement('div');
    h.className = 'sb-ghead'; h.draggable = true; h.dataset.gid = g.id;
    h.innerHTML =
      `<span class="sb-arrow" data-act="sbToggleCollapse" data-stop data-gid="${escHtml(g.id)}">${g.collapsed ? '▸' : '▾'}</span>` +
      `<span class="sb-gname" title="双击重命名" data-dblact="renameGroupInline" data-gid="${escHtml(g.id)}">${escHtml(g.name)}</span>` +
      `<span class="sb-gstat">${g.codes.length}只${(up + down) ? ` · ${up}涨${down}跌` : ''}</span>`;
    h.addEventListener('contextmenu', ev => { ev.preventDefault(); showCtxMenu(ev, 'group', g.id); });
    h.addEventListener('dragover', ev => ev.preventDefault());
    h.addEventListener('drop', ev => {   // 拖拽股票到组头 = 跨组移动
      ev.preventDefault();
      const code = ev.dataTransfer.getData('text/plain'); if (!code) return;
      const gs = getGroups(); const src = gs.find(x => x.codes.includes(code));
      if (!src || src.id === g.id) return;
      src.codes.splice(src.codes.indexOf(code), 1);
      if (!g.codes.includes(code)) g.codes.push(code);
      saveGroups(gs); hideCtxMenu();
    });
    box.appendChild(h);

    // —— 股票行 ——
    const list = document.createElement('div');
    list.className = 'sb-rows';
    list.style.display = g.collapsed ? 'none' : 'block';
    g.codes.forEach(code => {
      const st = stocks[code]; if (!st) return;
      const row = document.createElement('div');
      row.className = 'sb-row'; row.draggable = true; row.dataset.code = code; row.dataset.gid = g.id;
      const pct = (st.pct != null && !isNaN(st.pct)) ? st.pct : null;
      const cls = pct == null ? '' : pct > 0 ? 'up' : pct < 0 ? 'down' : '';
      row.innerHTML =
        `<span class="sb-rmain"><span class="sb-rname">${escHtml(st.name)}</span><span class="sb-rcode">${escHtml(code)}</span></span>` +
        `<span class="sb-rnum ${cls}">${st.price != null ? (+st.price).toFixed(2) : '--'}</span>` +
        `<span class="sb-rpct ${cls}">${pct != null ? ((pct > 0 ? '+' : '') + (+pct).toFixed(2) + '%') : '--'}</span>` +
        sbBadge(st.action);
      row.addEventListener('click', () => analyze(code));   // 单击切换标的，侧边栏保持打开
      row.addEventListener('contextmenu', ev => { ev.preventDefault(); ev.stopPropagation(); showCtxMenu(ev, 'stock', code, g.id); });
      row.addEventListener('dragstart', ev => { ev.dataTransfer.setData('text/plain', code); row.classList.add('dragging'); });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
      row.addEventListener('dragover', ev => ev.preventDefault());
      row.addEventListener('drop', ev => {   // 组内排序
        ev.preventDefault(); ev.stopPropagation();
        const dragged = ev.dataTransfer.getData('text/plain');
        if (!dragged || dragged === code) return;
        const gs = getGroups(); const tg = gs.find(x => x.id === g.id); if (!tg) return;
        const from = tg.codes.indexOf(dragged);
        if (from >= 0) tg.codes.splice(from, 1);
        tg.codes.splice(tg.codes.indexOf(code), 0, dragged);
        saveGroups(gs);
      });
      list.appendChild(row);
    });
    box.appendChild(list);
    frag.appendChild(box);
  });

  wrap.innerHTML = '';
  if (!totalCodes) {
    const empty = document.createElement('div');
    empty.className = 'sb-empty';
    empty.innerHTML = '☆ 还没有自选股<br>分析股票后点击 ☆ 添加';
    frag.appendChild(empty);
  }
  wrap.appendChild(frag);

  // 收起态图标栏：显示总数量
  const badge = document.querySelector('.sb-collapsed-badge');
  if (badge) badge.textContent = totalCodes || '';
}

// ---- 分组操作 ----
export function sbToggleCollapse(gid) {
  const gs = getGroups(); const g = gs.find(x => x.id === gid); if (!g) return;
  g.collapsed = !g.collapsed; saveGroups(gs);
}
export function sbSelectGroup(gid) { _sbActiveGroup = gid; renderSidebar(); }
export function createGroup(n) {
  const gs = getGroups();
  if (gs.some(g => g.name === n)) { showToastMsg('同名分组已存在'); return false; }
  const maxOrder = gs.reduce((m, g) => Math.max(m, g.order || 0), 0);
  gs.push({ id: 'g' + Date.now(), name: n, order: maxOrder + 1, collapsed: false, codes: [] });
  if (!_lsSet(GKEY_GROUPS, gs)) { showToastMsg('创建失败：浏览器存储不可用'); return false; }
  renderSidebar(); updateBadges();
  showToastMsg(`分组「${n}」已创建`);
  return true;
}
// 中文输入法守卫：合成中的回车/按键（确认候选词）不当作最终确认
function _imeComposing(ev) { return ev.isComposing === true || ev.keyCode === 229; }
let _sbNewGroupClosedAt = 0;   // 刚保存/取消后的短守卫：避免 blur 保存与按钮 click 竞争再弹空框
export function addGroupInline() {
  // 收起态先展开，否则输入框在屏幕外看不见
  if (!_sbOpen) toggleSidebar();
  const f = document.querySelector('.sb-footer'); if (!f) return;
  let inp = f.querySelector('.sb-new-input');
  if (inp) { inp.focus(); return; }   // 已在输入中：聚焦而不是忽略
  if (Date.now() - _sbNewGroupClosedAt < 300) return;   // 刚由 blur 完成保存，忽略这次点击
  inp = document.createElement('input');
  inp.className = 'sb-new-input'; inp.placeholder = '输入分组名，回车保存';
  f.appendChild(inp); inp.focus();
  inp.addEventListener('keydown', ev => {
    if (_imeComposing(ev)) return;   // 关键：输入法合成中的 Enter 不处理
    if (ev.key === 'Enter') { ev.preventDefault(); _finishNewGroup(inp); }
    else if (ev.key === 'Escape') { inp.value = ''; _finishNewGroup(inp); }
  });
  // 点别处：有内容就保存（防误丢），空则取消；延迟一拍避开与按钮点击的竞争
  inp.addEventListener('blur', () => setTimeout(() => { if (document.body.contains(inp)) _finishNewGroup(inp); }, 120));
}
export function _finishNewGroup(inp) {
  const n = (inp.value || '').trim();
  inp.remove();
  _sbNewGroupClosedAt = Date.now();
  if (!n) return;   // 空名静默取消
  createGroup(n);
}
export function renameGroupInline(el, gid) {
  const g = getGroups().find(x => x.id === gid); if (!g) return;
  if (gid === 'default') { showToastMsg('默认分组不可重命名'); return; }
  const inp = document.createElement('input');
  inp.className = 'sb-rename-input'; inp.value = g.name;
  el.replaceWith(inp); inp.focus(); inp.select();
  const done = () => {
    if (!document.body.contains(inp)) return;   // 防重复触发
    const n = inp.value.trim();
    inp.remove();
    if (n && n !== g.name) {
      const gs = getGroups(); const t = gs.find(x => x.id === gid);
      if (t && !gs.some(x => x.id !== gid && x.name === n)) { t.name = n; saveGroups(gs); }
      else { showToastMsg('重命名失败：名称为空或与现有分组重名'); renderSidebar(); }
    } else renderSidebar();
  };
  inp.addEventListener('keydown', ev => {
    if (_imeComposing(ev)) return;   // 中文输入法合成中的 Enter 不确认
    if (ev.key === 'Enter') { ev.preventDefault(); done(); }
    else if (ev.key === 'Escape') { inp.value = g.name; done(); }   // Esc 还原
  });
  inp.addEventListener('blur', () => setTimeout(done, 120));
}
export function renameGroupInlineById(gid) {
  const el = document.querySelector(`.sb-ghead[data-gid="${gid}"] .sb-gname`);
  if (el) renameGroupInline(el, gid); else renderSidebar();
}
export function deleteGroup(gid) {
  if (gid === 'default') { showToastMsg('默认分组不可删除'); return; }
  const gs = getGroups(); const g = gs.find(x => x.id === gid); if (!g) return;
  if (!confirm(`删除分组「${g.name}」？其 ${g.codes.length} 只成员将移入"我的自选"。`)) return;
  const def = gs.find(x => x.id === 'default');
  g.codes.forEach(c => { if (!def.codes.includes(c)) def.codes.push(c); });
  saveGroups(gs.filter(x => x.id !== gid));
  showToastMsg('分组已删除，成员已回落默认分组');
}
export function moveStock(code, gid) {
  const gs = getGroups();
  for (const g of gs) { const i = g.codes.indexOf(code); if (i >= 0) { g.codes.splice(i, 1); break; } }
  const t = gs.find(x => x.id === gid);
  if (t && !t.codes.includes(code)) t.codes.unshift(code);
  saveGroups(gs); hideCtxMenu();
}
export function pinStock(code, gid) {
  const gs = getGroups(); const g = gs.find(x => x.id === gid); if (!g) { hideCtxMenu(); return; }
  const i = g.codes.indexOf(code);
  if (i >= 0) g.codes.splice(i, 1);
  g.codes.unshift(code);
  const m = getStockMap(); if (m[code]) { m[code].pinned = true; saveStockMap(m); }
  saveGroups(gs); hideCtxMenu();
}


export function showCtxMenu(ev, type, id, gid) {
  const menu = document.getElementById('ctx-menu'); if (!menu) return;
  let html = '';
  if (type === 'stock') {
    html = '<div class="ctx-title">移动到分组</div>' +
      getGroups().map(g => `<div class="ctx-item${g.id === gid ? ' cur' : ''}" data-act="moveStock" data-code="${escHtml(id)}" data-gid="${escHtml(g.id)}">${g.id === gid ? '✓ ' : ''}${escHtml(g.name)}</div>`).join('') +
      `<div class="ctx-sep"></div>` +
      `<div class="ctx-item" data-act="pinStock" data-code="${escHtml(id)}" data-gid="${escHtml(gid)}">置顶</div>` +
      `<div class="ctx-item danger" data-act="removeStockCtx" data-code="${escHtml(id)}">删除</div>`;
  } else {
    html = `<div class="ctx-item" data-act="renameGroupMenu" data-gid="${escHtml(id)}">重命名</div>` +
           `<div class="ctx-item" data-act="toggleCollapseMenu" data-gid="${escHtml(id)}">折叠/展开</div>`;
    if (id !== 'default') html += `<div class="ctx-item danger" data-act="deleteGroupMenu" data-gid="${escHtml(id)}">删除分组</div>`;
  }
  menu.innerHTML = html;
  menu.style.display = 'block';
  menu.style.left = Math.min(ev.clientX, window.innerWidth - menu.offsetWidth - 8) + 'px';
  menu.style.top = Math.min(ev.clientY, window.innerHeight - menu.offsetHeight - 8) + 'px';
}
export function hideCtxMenu() { const m = document.getElementById('ctx-menu'); if (m) m.style.display = 'none'; }
document.addEventListener('click', e => { if (!(e.target.closest && e.target.closest('#ctx-menu'))) hideCtxMenu(); });

// ---- 行情轮询：盘中5s / 盘后60s / 页签隐藏暂停（A8/A85-A87） ----

export async function sbRefreshQuotes() {
  if (document.hidden) return;
  const codes = [...new Set(getWatchlist().map(s => s.code))];
  if (!codes.length) return;
  let quotes = null;
  try {   // P3：优先批量接口
    const r = await fetchWithTimeout(`${API}/api/quotes?codes=${encodeURIComponent(codes.join(','))}`);
    const j = await r.json();
    if (j && j.quotes) quotes = j.quotes;
  } catch (e) {}
  if (!quotes) {   // 兜底：并行逐只拉取
    quotes = {};
    await Promise.all(codes.map(async c => {
      try { const r = await fetchWithTimeout(`${API}/api/quote?symbol=${c}`); const q = await r.json(); if (!q.error) quotes[c] = q; } catch (e) {}
    }));
  }
  const m = getStockMap();
  const changedRows = [];
  for (const c of codes) {
    const q = quotes[c]; if (!q || !m[c]) continue;
    const prevAct = m[c].action, prevPct = m[c].pct;
    m[c].price = q.price; m[c].pct = q.pct;
    if (q.name) m[c].name = q.name;
    if (q.action != null && q.action !== '') m[c].action = q.action;   // 批量接口可带信号字段
    if (prevAct !== m[c].action || prevPct !== m[c].pct) changedRows.push(c);
  }
  saveStockMap(m);
  renderSidebar();
  // 炫酷档：信号变更的行角标扩散一次
  if (S._fxLevel === 'max') changedRows.forEach(code => {
    const el = document.querySelector(`.sb-row[data-code="${code}"] .sb-badge`);
    if (el) { el.classList.add('fx-ring'); setTimeout(() => el.classList.remove('fx-ring'), 1400); }
  });
}
export function sbSchedulePolling() {
  if (_sbTimer) clearInterval(_sbTimer);
  _sbTimer = setInterval(sbRefreshQuotes, isMarketOpen() ? 5000 : 60000);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { sbSchedulePolling(); sbRefreshQuotes(); }
});

// ---- 设置弹层 / 导出导入 ----

export function exportWatchlist() {
  const data = { version: 1, exportedAt: new Date().toISOString(), groups: getGroups(), stocks: getStockMap() };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'watchlist-backup-' + new Date().toISOString().slice(0, 10).replace(/-/g, '') + '.json';
  a.click(); URL.revokeObjectURL(a.href);
  showToastMsg('自选股已导出');
}
export function importWatchlist(input) {
  const f = input.files && input.files[0];
  input.value = '';
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    let j = null;
    try { j = JSON.parse(rd.result); } catch (e) { showToastMsg('导入失败：不是合法 JSON 文件'); return; }
    if (!j || j.version !== 1 || !Array.isArray(j.groups) || typeof j.stocks !== 'object' || j.stocks === null) {
      showToastMsg('导入失败：文件结构不符合备份格式'); return;
    }
    const gs = getGroups(); const m = getStockMap(); let cnt = 0;
    for (const g of j.groups) {
      if (!g || !g.name || !Array.isArray(g.codes)) continue;
      let t = gs.find(x => x.name === g.name);   // 同名分组保留现名
      if (!t) { t = { id: 'g' + Date.now() + Math.random().toString(36).slice(2, 6), name: String(g.name), order: gs.length, collapsed: false, codes: [] }; gs.push(t); }
      for (const c of g.codes) {
        if (typeof c !== 'string') continue;
        if (!t.codes.includes(c)) t.codes.push(c);
      }
    }
    for (const key of Object.keys(j.stocks)) {
      const s = j.stocks[key];
      if (!s || typeof s !== 'object') continue;
      const cur = m[key];
      if (!cur || !(cur.addedAt > (s.addedAt || 0))) {   // 按 code 去重，保留较新 addedAt
        m[key] = { name: s.name || key, action: s.action || '', score: s.score || 0, addedAt: s.addedAt || Date.now(), pinned: false, price: s.price, pct: s.pct };
        if (!cur) cnt++;
      }
    }
    saveStockMap(m); saveGroups(gs);
    renderSidebar(); renderWatchlist(); updateBadges(); updateStarButton(S.currentSymbol);
    showToastMsg(`导入完成，新增 ${cnt} 只自选`);
  };
  rd.readAsText(f, 'utf-8');
}

// 轻量消息 Toast（复用现有 toast 样式容器）

// improvements #11：启动后与服务端对齐自选数据（服务端为真，本地为缓存）
setTimeout(function () { try { _wlSyncInit(); } catch (e) {} }, 400);



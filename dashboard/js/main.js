// ==================== 入口模块：编排/分析流程/信号渲染/FX/设置（improvements #13） ====================
// 原 4781 行单文件按域拆分后的入口；各域见同目录 api/ui/shared/watchlist/chart/journal/scan.js。
import { C, S } from './shared.js';
import { API, fetchWithTimeout, isTimeoutError, explainError } from './api.js';
import { escHtml, glossarize, _applyTermChips, explainRisks, riskBannerHtml, whyTextFor, toggleWhy, showToastMsg, showToast, DELEGATED_ACTIONS, countUpScore, fxCardStagger } from './ui.js';
import { getGroups, getStockMap, getWatchlist, saveWatchlist, getHistory, saveHistory, addHistory, migrateWatchlist, toggleStar, updateStarButton, updateBadges, openSbSection, toggleSbSection, toggleSidebar, sidebarLoadState, loadSbSection, renderSbSection, applySidebar, renderSidebar, renderWatchlist, exportWatchlist, importWatchlist, sbRefreshQuotes, sbSchedulePolling, removeFromWatchlist, registerResizeHook, clearCurrentTab, addGroupInline } from './watchlist.js';
import { initCharts, switchView, calcMA, renderKline, findEntryIndex, applyRange, bindChartTooltip, updateZoomInfo, renderChanlun, renderChanlunDaily, applyChanlunDailyOverlay, renderMinute, loadMinute, refreshMinuteLight, renderFlow, switchFlowMode, loadRealtimeFlow, refreshKlineLastCandle, resizeAllChartsSafe, switchIndicator, _lastMA } from './chart.js';
import { loadOverview, loadJournal, exportJournalCsv, exportJournalJson, loadPool, poolAdd, poolAddCurrent, poolRemove, poolNote, poolMove, togglePoolImport, poolImportSubmit, poolFillIndustry, recordSignal, renderSignalAccuracy, checkSignalChange, clearWatchChangeBadge, loadDigest, refreshDigest, renderPoolPanel } from './journal.js';
import { openScan, closeScan, renderScanIdle, startScan, stopScanPolling, renderScanArchiveList, clearScanArchive, renderArchivedRun, exportScanCsv, deleteScanRun, analyzeFromScan } from './scan.js';
import { loadNotifySettings, saveNotifySettings, testNotify, runNotifyOnce, refreshNotifyStatus } from './notify.js';
/* 趋势分析看板主逻辑 —— 自 index.html 拆分（frontend-ux-v42 P0） */






let _refreshTimer = null;





// ---- 简单鉴权（web-auth）：AUTH_PASSWORD 启用后，任意受保护请求 401 → 跳登录页 ----
(function () {
  const _orig = window.fetch;
  window.fetch = function (input, init) {
    return _orig.apply(this, arguments).then(function (res) {
      if (res.status === 401 && !location.pathname.endsWith('/login.html')) {
        location.href = '/login.html';
      }
      return res;
    });
  };
})();

// 鉴权启用时显示「退出」；未登录则跳登录页
export async function _initAuth() {
  try {
    const s = await (await fetchWithTimeout('/api/auth/status')).json();
    const btn = document.getElementById('btn-logout');
    if (btn && s && s.enabled) btn.style.display = '';
    const mBtn = document.getElementById('more-logout');
    if (mBtn && s && s.enabled) mBtn.style.display = '';
    if (s && s.enabled && !s.authed) location.href = '/login.html';
  } catch (e) { /* 离线/异常时静默 */ }
}

export function doLogout() {
  fetchWithTimeout('/api/auth/logout', { method: 'POST' }).catch(function () {})
    .finally(function () { location.href = '/login.html'; });
}










// ==================== FX 动效系统（frontend-ux-v42 R3） ====================
// 档位：off=关 / std=标准 / max=炫酷 / auto=自动判定（默认）
// 护栏：仅 transform/opacity 动画；图表动画仅首帧；关档 JS 跳过调度；reduced-motion 强制关
let _fxSetting = 'auto';

const _animConsumed = {};   // 图表首帧动画消耗标记（按图表key）

function _fxReducedMotion() {
  try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch (e) { return false; }
}
function _fxResolveAuto() {
  if (_fxReducedMotion()) return 'off';
  try {
    if (navigator.deviceMemory && navigator.deviceMemory < 4) return 'off';
    if (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) return 'off';
  } catch (e) {}
  return 'std';
}
function applyFx() {
  let s = 'auto';
  try { s = localStorage.getItem('qs_fx') || 'auto'; } catch (e) {}
  _fxSetting = s;
  // prefers-reduced-motion 最高优先级：任何档位强制无动画
  S._fxLevel = _fxReducedMotion() ? 'off' : (s === 'off' || s === 'std' || s === 'max' ? s : _fxResolveAuto());
  document.body.classList.remove('fx-off', 'fx-std', 'fx-max');
  document.body.classList.add('fx-' + S._fxLevel);
  const hint = document.getElementById('fx-hint');
  if (hint) {
    const actual = { off: '关', std: '标准', max: '炫酷' }[S._fxLevel];
    hint.textContent = _fxSetting === 'auto'
      ? `自动档：当前实际「${actual}」（低配设备/系统减动效偏好会自动降为关）`
      : { off: '已关闭全部动效', std: '标准动效：内容入场与微交互', max: '炫酷动效：含图表首帧动画与氛围效果' }[_fxSetting] || '';
    document.querySelectorAll('.fx-opt').forEach(b => b.classList.toggle('active', b.dataset.fx === _fxSetting));
  }
}
function setFx(s) {
  try { localStorage.setItem('qs_fx', s); } catch (e) {}
  applyFx();
}
export function fxEnabled() { return S._fxLevel !== 'off'; }

// 图表首帧动画开关：max档且该图表未被消耗过才允许动画；不带key=用户主动重渲染（如切换指标），max档恒可动画
export function chartAnim(key) {
  if (S._fxLevel !== 'max') return false;
  if (key === undefined) return true;
  if (_animConsumed[key]) return false;
  _animConsumed[key] = true;
  return true;
}
export function resetChartAnim() { Object.keys(_animConsumed).forEach(k => delete _animConsumed[k]); }

// ===== ECharts 全局主题色 =====

// ===== 初始化 =====

// ==================== 小白模式增强（frontend-ux-v42 R1） ====================
// 风险解读字典：codes 匹配结构化风险码优先，kws 匹配文案关键词兜底

// 汇总当前信号的风险并翻译成大白话。返回 [{key,text,advice,raw}]

// 三段式小白结论：📌现状 / ⚠️风险与机会 / ✅现在该做什么
function buildBeginnerSegments(signal) {
  const tr = signal.trend || {};
  const vp = signal.volume_price || {};
  const mo = signal.momentum || {};
  const plan = signal.trade_plan || {};
  const action = signal.action || '';

  // —— 现状 ——
  const seg1 = `目前处于<b>${escHtml(tr.direction || '中性')}趋势</b>` +
    (tr.strength ? `（强度${tr.strength}分）` : '') +
    (vp.pattern ? `，量价形态「${glossarize(vp.pattern)}」` : '') +
    (mo.m_score != null ? `，大盘M分 ${mo.m_score}${mo.m_score < 30 ? '（偏空）' : ''}` : '') + '。';

  // —— 风险与机会 ——
  const buyN = (signal.buy_signals || []).length;
  const sellN = (signal.sell_signals || []).length;
  const seg2Parts = [];
  if (signal.risk_level) seg2Parts.push(`风险等级「${signal.risk_level}」`);
  if (buyN) seg2Parts.push(`${buyN} 条偏多信号`);
  if (sellN) seg2Parts.push(`${sellN} 条偏空信号`);
  const seg2 = seg2Parts.length ? `${seg2Parts.join('，')}。` : '暂无明显的机会或风险信号。';

  // —— 现在该做什么 ——
  let seg3;
  const isBuyAct = action.includes('买入');
  if (action === '观望' || action === '卖出') {
    let cond = '耐心等待综合评分回到 60 分以上再考虑。';
    const ma20 = _lastMA(20);
    if ((tr.direction === '下降' || (signal.risk_codes || []).includes('price_below_ma20')) && ma20) {
      cond = `等待收盘价重新站回 MA20（${ma20.toFixed(2)} 元）之上，再重新关注。`;
    }
    seg3 = `<b>${escHtml(action === '观望' ? '先不动，保持观察' : '注意离场/回避')}</b>。${cond}` +
      (signal.position_advice ? ` 当前参考：${escHtml(signal.position_advice)}。` : '');
  } else {
    const entry = plan.entry_price || 0, stop = plan.stop_loss || 0, target = plan.target_price || 0;
    seg3 = `<b>可以考虑${escHtml(action)}</b>。建议仓位：<b>${escHtml(signal.position_advice || plan.position_size || '轻仓试探')}</b>` +
      (entry ? `，参考买入价 ${(+entry).toFixed(2)}` : '') +
      (stop ? `，跌破 <b>${(+stop).toFixed(2)}</b> 无条件止损` : '') +
      (target ? `，目标看 ${(+target).toFixed(2)}` : '') +
      (plan.risk_reward_ratio ? `（盈亏比 ${glossarize(String(plan.risk_reward_ratio))}）` : '') +
      '。首次建仓建议只用计划仓位的一半试错。';
  }

  const disclaimer = '<div class="seg-disclaimer">以上由规则自动生成，仅供参考，非投资建议。</div>';
  return `
    <div class="seg-card seg-now"><div class="seg-title">📌 现状</div><div class="seg-body">${_applyTermChips(seg1)}</div></div>
    <div class="seg-card seg-risk"><div class="seg-title">⚠️ 风险与机会</div><div class="seg-body">${_applyTermChips(seg2)}</div></div>
    <div class="seg-card seg-do"><div class="seg-title">✅ 现在该做什么</div><div class="seg-body">${_applyTermChips(seg3)}</div>${disclaimer}</div>`;
}

// 术语即点即译：把词典命中的词包成 chip。
// glossarize(纯文本)：先 HTML 转义再加 chip；
// _applyTermChips(已构建的HTML片段)：跳过标签只处理文本节点——否则片段内的 <b> 会被转义成可见文字（bug修复）

// ===== 一句话总结（小白第一眼看的） =====
function renderSummary(signal) {
  const el = document.getElementById('sum-body');
  const action = signal.action;
  const isBuy = action.includes('买入') && action !== '谨慎买入';
  const isCautious = action === '谨慎买入';
  const isSell = action.includes('卖出');
  const badgeClass = isBuy ? 'sum-badge-buy' : isCautious ? 'sum-badge-cautious' : isSell ? 'sum-badge-sell' : 'sum-badge-watch';
  const badgeText = action;

  const strength = signal.signal_strength || '';
  const sClass = strength === '强' ? 'strong' : strength === '中' ? 'medium' : 'weak';
  const sText = strength ? `信号${strength}` : '';

  const risk = signal.risk_level || '';
  const riskClass = risk === '低' ? 'low' : risk === '中' ? 'mid' : 'high';

  const score = signal.score || 0;
  const scoreColor = isBuy ? C.up : isSell ? C.down : '#ffc107';

  let summary = signal.plain_summary || '';
  const isSimpleMode = S._mode === 'simple';
  const risks = explainRisks(signal);
  if (isSimpleMode) {
    summary = buildBeginnerSegments(signal);   // 三段式（内部已做术语chip）
  }

  // 优化信号信息
  const origAction = signal.original_action || '';
  const vetoReason = signal.veto_reason || '';
  const riskNotes = signal.risk_notes || [];
  const posAdvice = signal.position_advice || '';
  const riskReward = signal.risk_reward || 0;

  // 如果信号被优化降级，显示对比标签
  let optimizeHtml = '';
  if (origAction && origAction !== action) {
    optimizeHtml = `<div style="margin-top:4px;font-size:11px;color:#888">
      <span style="text-decoration:line-through;color:#888">${origAction}</span>
      <span style="margin:0 2px">→</span>
      <span style="color:${isCautious ? '#ffb74d' : '#ffc107'};font-weight:500">${action}</span>
      ${vetoReason ? `<span style="margin-left:6px;color:#ff6b6b">⚠ ${vetoReason}</span>` : ''}
    </div>`;
  } else if (vetoReason) {
    optimizeHtml = `<div style="margin-top:4px;font-size:11px;color:#ff6b6b">⚠ ${vetoReason}</div>`;
  }

  // 风险提示
  let riskNotesHtml = '';
  if (riskNotes.length > 0) {
    riskNotesHtml = `<div style="margin-top:6px;padding:6px 8px;background:rgba(255,152,0,0.08);border-radius:6px;border:1px solid rgba(255,152,0,0.15)">
      ${riskNotes.map(n => `<div style="font-size:11px;color:#ffb74d;line-height:1.5">⚠ ${n}</div>`).join('')}
    </div>`;
  }

  // 仓位建议
  let posHtml = '';
  if (posAdvice && posAdvice !== '空仓等待' && (isBuy || isCautious)) {
    posHtml = `<div style="margin-top:4px;font-size:11px;color:#aaa">
      <span style="color:#ff9800">建议仓位：</span><span style="color:#ddd">${posAdvice}</span>
      ${riskReward ? `<span style="margin-left:8px;color:#888">盈亏比 ${riskReward}</span>` : ''}
    </div>`;
  }

  el.innerHTML = `
    ${isSimpleMode ? riskBannerHtml(risks) : ''}
    <div class="sum-action-row">
      <span class="sum-badge ${badgeClass}">${badgeText}</span>
      ${sText ? `<span class="sum-strength ${sClass}">${sText}</span>` : ''}
      <span class="sum-score-big" id="sum-score" style="color:${scoreColor}">${score}分</span>
    </div>
    <div class="sum-text">${summary}</div>
    ${optimizeHtml}
    ${posHtml}
    ${riskNotesHtml}
    <div class="sum-risk-row">
      <span class="sum-risk-dot ${riskClass}"></span>
      <span style="color:#888">风险等级：<span style="color:${risk==='低'?C.down:risk==='高'?C.up:'#ffc107'};font-weight:bold">${risk}</span></span>
      <span style="color:#555;margin-left:auto">置信度 ${signal.confidence||0}%</span>
    </div>
  `;
  if (fxEnabled()) countUpScore(score);
}

// ===== 数据元数据 =====
function renderDataMeta(meta) {
  const el = document.getElementById('sum-meta');
  if (!meta || !el) return;
  const parts = [];
  if (meta.source) parts.push(`数据源:${meta.source}`);
  if (meta.adjust) parts.push(`复权:${meta.adjust}`);
  if (meta.latest_bar_date) parts.push(`最新bar:${meta.latest_bar_date}`);
  if (meta.latest_bar_status) {
    const statusLabels = { intraday: '盘中', closed: '已收盘', unknown: '未知' };
    parts.push(`状态:${statusLabels[meta.latest_bar_status] || meta.latest_bar_status}`);
  }
  if (meta.calculated_at) parts.push(`计算:${meta.calculated_at}`);
  el.innerHTML = parts.join(' | ') || '';
}

// ===== 操作计划 =====
function renderTradePlan(signal) {
  const card = document.getElementById('plan-card');
  const el = document.getElementById('plan-body');
  const plan = signal.trade_plan;
  if (!plan || !plan.action) { card.style.display = 'none'; return; }
  card.style.display = 'block';

  // 用优化后的action（如果有），否则用plan原始action
  const action = signal.action || plan.action;
  const isBuy = action.includes('买入') && action !== '谨慎买入';
  const isCautious = action === '谨慎买入';
  const isSell = action.includes('卖出');
  const isWatch = action === '观望';

  const entry = plan.entry_price || 0;
  const stop = plan.stop_loss || 0;
  const target = plan.target_price || 0;
  const rr = plan.risk_reward_ratio || 0;
  const lossPct = plan.max_loss_pct || 0;
  const pos = plan.position_size || '';
  const period = plan.holding_period || '';
  const notes = plan.notes || '';

  if (isWatch) {
    const vetoReason = signal.veto_reason || '';
    const vetoHtml = vetoReason ? `
      <div style="padding:6px 8px;margin-bottom:6px;background:rgba(255,107,107,0.08);border-radius:6px;border:1px solid rgba(255,107,107,0.15);font-size:11px;color:#ff6b6b;line-height:1.5">
        ⚠ ${vetoReason}
      </div>` : '';
    el.innerHTML = `
      ${vetoHtml}
      <div style="padding:8px 0;font-size:13px;color:#aaa;line-height:1.6">
        <div style="margin-bottom:6px"><span style="color:#ffc107">当前建议：</span>${pos || signal.position_advice || '空仓等待'}</div>
        <div style="margin-bottom:6px"><span style="color:#888">适合周期：</span>${period}</div>
        <div class="plan-notes">${notes}</div>
      </div>`;
    return;
  }

  if (isSell) {
    el.innerHTML = `
      <div style="padding:8px 0;font-size:13px;color:#aaa;line-height:1.6">
        <div style="margin-bottom:6px"><span style="color:${C.down}">操作建议：</span>${pos}</div>
        <div class="plan-notes">${notes}</div>
      </div>`;
    return;
  }

  // 买入计划：显示价格三件套
  const cautionBanner = isCautious ? `
    <div style="padding:6px 8px;margin-bottom:8px;background:rgba(255,152,0,0.1);border-radius:6px;border:1px solid rgba(255,152,0,0.2);font-size:11px;color:#ffb74d;line-height:1.5">
      ⚠ 谨慎买入：信号存在风险因素，建议轻仓试探，严格执行止损
    </div>` : '';
  el.innerHTML = `
    ${cautionBanner}
    <div class="plan-prices">
      <div class="plan-price-box">
        <div class="plan-price-label">买入价 <span class="plan-tip">现价入手</span></div>
        <div class="plan-price-val" style="color:${C.up}">${entry.toFixed(2)}</div>
      </div>
      <div class="plan-price-box">
        <div class="plan-price-label">止损价 <span class="plan-tip">跌到这里就卖</span></div>
        <div class="plan-price-val" style="color:${C.down}">${stop.toFixed(2)}</div>
      </div>
      <div class="plan-price-box">
        <div class="plan-price-label">目标价 <span class="plan-tip">涨到这里就卖</span></div>
        <div class="plan-price-val" style="color:#ff9800">${target.toFixed(2)}</div>
      </div>
    </div>
    <div class="plan-rr">
      <span class="plan-rr-item">盈亏比 <b>${rr || signal.risk_reward || 0}</b> <span class="plan-tip">冒1元风险可赚${rr || signal.risk_reward || 0}元</span></span>
      <span class="plan-rr-item">最大亏损 <b style="color:${C.down}">${lossPct}%</b></span>
    </div>
    <div class="plan-row">
      <span class="plan-label">建议仓位 <span class="plan-tip">投多少钱</span></span>
      <span class="plan-val" style="color:#ff9800">${pos || signal.position_advice || ''}</span>
    </div>
    <div class="plan-row">
      <span class="plan-label">持有周期 <span class="plan-tip">大概持多久</span></span>
      <span class="plan-val">${period}</span>
    </div>
    <div class="plan-notes">${notes}</div>
  `;
}

// ===== 信号面板 =====
// 信号文本 → 对应点位预览/点击跳转图表
function _signalAnchorFor(text, signal) {
  if (!text || !signal || !signal.breakouts) return null;
  const sys = (text.match(/系统[一二]/) || [''])[0];
  if (!sys) return null;
  const b = signal.breakouts.find(x => x.system && x.system.indexOf(sys) === 0);
  if (!b) return null;
  let price = null, kind = '';
  if (/持仓|入场|突破/.test(text)) { price = b.entry_price || b.breakout_price || b.stop_loss; kind = '入场'; }
  else if (/空头平仓/.test(text)) { price = b.breakout_price || b.entry_price || b.stop_loss; kind = '平仓'; }
  else if (/止损|卖出|跌到|涨到/.test(text)) { price = b.stop_loss || b.exit_price || b.entry_price; kind = '止损'; }
  else { price = b.entry_price || b.stop_loss; kind = '价位'; }
  if (!price || price <= 0) return null;
  const idx = (S._klineData && S._klineData.length) ? findEntryIndex(S._klineData, price) : -1;
  const date = (idx >= 0 && S._klineData[idx]) ? S._klineData[idx].date : '';
  return { price, kind, date, idx, system: b.system };
}

function jumpToPoint(el) {
  if (!el) return;
  const price = parseFloat(el.getAttribute('data-point'));
  const date = el.getAttribute('data-date') || '';
  if (!price || !S._klineData.length) return;
  let idx = S._klineData.findIndex(k => k && k.date === date);
  if (idx < 0) idx = findEntryIndex(S._klineData, price);
  if (idx < 0) idx = S._klineData.length - 1;
  const total = S._klineData.length;
  const half = Math.min(30, Math.floor(total / 2));
  let s = Math.max(0, ((idx - half) / total) * 100);
  let e = Math.min(100, ((idx + half) / total) * 100);
  if (e - s < 2) { s = 0; e = 100; }
  try { klineChart.dispatchAction({ type: 'dataZoom', start: s, end: e }); } catch (err) {}
  updateZoomInfo(s, e);
  const k = S._klineData[idx];
  const label = k && k.date ? k.date : '';
  showToastMsg(`已定位 ${label} 点位 ${price.toFixed(2)}`);
  const chartEl = document.getElementById('kline-chart');
  if (chartEl && window.innerWidth <= 768) {
    try { chartEl.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (err) {}
  }
}
function renderSignal(signal) {
  // 一句话总结
  renderSummary(signal);

  // 操作计划
  renderTradePlan(signal);

  // 模块评分
  const ml = document.getElementById('module-list');
  const ms = signal.module_scores || {};
  const labels = { '趋势': '趋势方向', '形态': '图表形态', '量价': '量价关系', '突破': '突破信号', '动量资金': '动量/资金/市场' };
  const sorted = Object.entries(ms).sort((a, b) => b[1] - a[1]);
  ml.innerHTML = sorted.map(([k, v]) => {
    const pct = Math.min(100, Math.max(0, v));
    const c = pct >= 65 ? C.up : pct >= 45 ? '#ffc107' : C.down;
    const tag = pct >= 65 ? '偏多' : pct >= 45 ? '中性' : '偏空';
    return `<div class="ml-row">
      <span class="ml-name">${labels[k] || k}</span>
      <div class="ml-bar-bg"><div class="ml-bar" style="width:${pct}%;background:${c}"></div></div>
      <span class="ml-val" style="color:${c}">${v}</span>
      <span style="font-size:10px;color:${c};width:28px">${tag}</span>
    </div>`;
  }).join('');

  // 买卖信号列表（按重要度排序，标记核心信号）
  const sl = document.getElementById('sig-list');
  const buys = (signal.buy_signals || []).map(s => ({ type: 'buy', text: s }));
  const sells = (signal.sell_signals || []).map(s => ({ type: 'sell', text: s }));
  const allSigs = [...buys, ...sells].sort((a, b) => {
    // 含"强势"/"确认"/"突破"的排前面
    const weight = (t) => /强势|确认|突破|流入|优秀/.test(t) ? 0 : 1;
    return weight(a.text) - weight(b.text);
  });
  // 标记前2条为核心信号
  const simpleSig = S._mode === 'simple';
  sl.innerHTML = allSigs.map((s, idx) => {
    const isCore = idx < 2 && allSigs.length > 2;
    const coreTag = isCore ? '<span class="sig-core-tag">核心</span>' : '';
    const body = simpleSig ? glossarize(s.text) : escHtml(s.text);
    const why = simpleSig ? `<span class="sig-why" onclick="toggleWhy(this)">为什么？</span><span class="sig-why-body" style="display:none">${escHtml(whyTextFor(s.text))}</span>` : '';
    const anchor = _signalAnchorFor(s.text, signal);
    const ptHtml = anchor ? `<span class="sig-pt${anchor.kind === '止损' || anchor.kind === '卖出' ? ' sig-pt-stop' : ''}" title="${anchor.system} · ${anchor.kind}位 ${anchor.price.toFixed(2)}">点 ${anchor.price.toFixed(2)}</span>` : '';
    const jumpAttr = anchor ? ` data-point="${anchor.price}" data-date="${anchor.date || ''}" onclick="jumpToPoint(this)"` : '';
    if (s.type === 'buy') return `<div class="sig-item sig-buy"${jumpAttr}>▲ ${body}${ptHtml}${coreTag}${why}</div>`;
    else return `<div class="sig-item sig-sell"${jumpAttr}>▼ ${body}${ptHtml}${coreTag}${why}</div>`;
  }).join('') || '<div style="color:#555;font-size:12px;padding:8px">暂无信号</div>';

  // 风险
  const rc = document.getElementById('risk-card');
  const rl = document.getElementById('risk-list');
  const risks = signal.risk_warnings || [];
  if (risks.length) {
    rc.style.display = 'block';
    rl.innerHTML = risks.map(w => `<div class="sig-item sig-risk">⚠ ${w}</div>`).join('');
  } else { rc.style.display = 'none'; }
}

// ===== 动量/资金/市场环境综合分 =====
function renderMomentum(cs) {
  if (!cs) return;
  const items = [
    { l: 'C', s: cs.c_score, n: '近期动力', tip: '近期价格涨势' },
    { l: 'A', s: cs.a_score, n: '中期趋势', tip: '中期价格趋势' },
    { l: 'N', s: cs.n_score, n: '新高形态', tip: '是否创新高/新底部' },
    { l: 'S', s: cs.s_score, n: '供需关系', tip: '换手率/量价配合' },
    { l: 'L', s: cs.l_score, n: '领涨强度', tip: '相对大盘强度' },
    { l: 'I', s: cs.i_score, n: '机构资金', tip: '主力资金流向' },
    { l: 'M', s: cs.m_score, n: '大盘环境', tip: '市场整体方向' },
  ];
  document.getElementById('cs-grid').innerHTML = items.map(i => {
    const c = i.s >= 65 ? C.up : i.s >= 45 ? '#ffc107' : C.down;
    const tag = i.s >= 65 ? '好' : i.s >= 45 ? '中' : '差';
    return `<div class="cs-item" title="${i.tip}">
      <span class="cs-letter">${i.l}</span>
      <span class="cs-score" style="color:${c}">${i.s}</span>
      <span class="cs-label">${i.n}</span>
      <span style="font-size:9px;color:${c}">${tag}</span>
    </div>`;
  }).join('');

  // 小白模式简化显示
  const totalScore = Math.round((cs.c_score + cs.a_score + cs.n_score + cs.s_score + cs.l_score + cs.i_score + cs.m_score) / 7);
  const tColor = totalScore >= 65 ? C.up : totalScore >= 45 ? '#ffc107' : C.down;
  const tLabel = totalScore >= 65 ? '良好' : totalScore >= 45 ? '一般' : '较差';
  // 找最强和最弱维度
  const sorted = [...items].sort((a, b) => b.s - a.s);
  const best = sorted[0], worst = sorted[sorted.length - 1];
  document.getElementById('cs-simple').innerHTML = `
    <div style="display:flex;align-items:center;gap:8px">
      <span class="cs-simple-score" style="color:${tColor}">${totalScore}</span>
      <span class="cs-simple-label">动量/资金/市场环境综合评分（${tLabel}）</span>
    </div>
    <div style="font-size:11px;color:#888;margin-top:6px">
      最强：<span style="color:${best.s >= 65 ? C.up : '#ffc107'}">${best.n} ${best.s}分</span>　
      最弱：<span style="color:${worst.s >= 45 ? '#ffc107' : C.down}">${worst.n} ${worst.s}分</span>
    </div>
  `;
}

// ===== 关键价位 =====
const KL_TOOLTIPS = {
  '趋势线': '连接近期重要高点或低点的直线，价格触及此处可能反弹或突破。',
  '颈线': '头肩/双顶/双底等形态的颈线位，突破后视为形态确认。',
  '头部': '头肩形态中的极值点，是形态测量的基准价位。',
  '止损': '海龟交易法则的2N止损位。跌破（做多）或涨破（做空）此处应离场止损。',
  '系统一': '海龟法则20日唐奇安通道触发，短期突破系统。',
  '系统二': '海龟法则55日唐奇安通道触发，中长期突破系统。',
  '支撑': '价格下跌时可能获得买盘支撑的位置。',
  '压力': '价格上涨时可能遭遇卖盘压力的位置。',
  '压力位': '价格上涨时可能遭遇卖盘压力的位置。',
};
function explainKeyLevel(label) {
  for (const [key, text] of Object.entries(KL_TOOLTIPS)) {
    if (label.includes(key)) return text;
  }
  return '鼠标悬浮查看该价位含义；关键价位来自趋势线、形态颈线或海龟交易系统。';
}
function renderKeyLevels(levels) {
  const el = document.getElementById('kl-grid');
  if (!levels || !Object.keys(levels).length) {
    el.innerHTML = '<span style="color:#555;font-size:12px;padding:8px">无关键价位</span>';
    return;
  }
  el.innerHTML = Object.entries(levels).map(([k, v]) => {
    const tip = explainKeyLevel(k);
    return `<div class="kl-item" title="${tip}" style="cursor:help">
      <span class="kl-label" style="border-bottom:1px dashed #444">${k}</span>
      <span class="kl-val">${v.toFixed(2)}</span>
    </div>`;
  }).join('');
}

// ===== 大盘环境 =====
function renderMarket(signal, breadth) {
  const card = document.getElementById('market-card');
  const el = document.getElementById('market-body');
  if (!signal.momentum) { card.style.display = 'none'; return; }

  const m = signal.momentum.m_score;
  // 从M维度描述中提取大盘信息
  const isBearish = m < 40;

  card.style.display = 'block';
  const mColor = m >= 65 ? C.up : m >= 45 ? '#ffc107' : C.down;
  const mLabel = m >= 65 ? '偏多' : m >= 45 ? '中性' : '偏空';

  // 市场宽度数据（真实涨跌家数）
  let breadthHtml = '';
  if (breadth && breadth.total >= 50) {
    const upN = breadth.up || 0;
    const downN = breadth.down || 0;
    const br = breadth.breadth_ratio || 0.5;
    const brPct = (br * 100).toFixed(0);
    const brColor = br >= 0.6 ? C.up : br >= 0.4 ? '#ffc107' : C.down;
    const brLabel = br >= 0.7 ? '普涨' : br >= 0.6 ? '多数上涨' : br >= 0.4 ? '多数下跌' : '普跌';
    breadthHtml = `<div style="margin-top:6px;padding:6px 8px;background:rgba(255,255,255,0.05);border-radius:6px">
      <div style="display:flex;align-items:center;gap:8px;font-size:12px">
        <span style="color:${C.up};font-weight:bold">${upN}</span>
        <span style="color:#888">涨</span>
        <span style="color:#444">/</span>
        <span style="color:${C.down};font-weight:bold">${downN}</span>
        <span style="color:#888">跌</span>
        <span style="color:${brColor};font-weight:bold;margin-left:auto">${brPct}% ${brLabel}</span>
      </div>
    </div>`;
  }

  // 大盘环境卡片：显示M评分+影响+建议（使用真实涨跌家数）
  let advice = '';
  if (isBearish && breadth && breadth.breadth_ratio >= 0.55) {
    // 趋势偏空但今日实际多数上涨 → 不再硬说"3/4下跌"
    advice = '<div style="color:#ffc107;margin-top:4px;font-size:11px">大盘趋势偏空，但今日多数个股上涨，短线可关注反弹</div>';
  } else if (isBearish) {
    advice = `<div style="color:#ff2d2d;margin-top:4px;font-size:11px">大盘偏空${breadth ? `，今日${breadth.up}涨/${breadth.down}跌` : ''}，建议降低仓位或等待大盘转暖</div>`;
  } else if (m >= 65) {
    advice = '<div style="color:#00b35c;margin-top:4px;font-size:11px">大盘环境偏多，适合积极操作</div>';
  } else {
    advice = '<div style="color:#ffc107;margin-top:4px;font-size:11px">大盘环境中性，可适度操作但需谨慎</div>';
  }

  // 提取M维度信号（含大盘/今日/市场环境关键词）
  const mSignals = (signal.momentum.signals || []).filter(s =>
    s.includes('市场环境') || s.includes('大盘') || s.includes('M(') || s.includes('今日')
  );
  const mSignalText = mSignals.join('；');

  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <span style="font-size:14px;font-weight:bold;color:${mColor}">${mLabel}</span>
      <span style="font-size:28px;font-weight:bold;color:${mColor};line-height:1">${m}分</span>
      <span style="font-size:11px;color:#888;margin-left:auto">上证指数环境</span>
    </div>
    ${mSignalText ? `<div style="color:#888;font-size:11px">${mSignalText}</div>` : ''}
    ${breadthHtml}
    ${advice}
  `;
}

// ===== 行情显示 =====
function updateQuote(q) {
  const el = document.getElementById('quote-bar');
  if (!q) { el.innerHTML = '<span class="qb-name flat">无行情</span>'; return; }
  const cls = q.pct > 0 ? 'up' : q.pct < 0 ? 'down' : 'flat';
  const sign = q.pct > 0 ? '+' : '';
  el.innerHTML = `
    <span class="qb-name">${q.name}</span>
    <span class="qb-price ${cls}">${q.price.toFixed(3)}</span>
    <span class="qb-pct ${cls}">${sign}${q.pct.toFixed(2)}%</span>
    <span class="qb-meta">量${fmtVol(q.volume)} 换手${q.turnover.toFixed(1)}%</span>
  `;
}

function fmtVol(v) {
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
  return (v || 0).toFixed(0);
}

// ===== 搜索 =====

// ===== 分析 =====
let _lastOkTime = '';          // 上次分析成功时间（data_meta.calculated_at）
let _failRetryCount = 0;       // 连续失败后的自动重试次数
const _MAX_FAIL_RETRY = 2;     // 自动重试上限（手动点击"立即重试"不受限）
let _failRetryTimer = null;

function _scheduleFailRetry(symbol) {
  if (_failRetryCount >= _MAX_FAIL_RETRY) return;
  _failRetryCount += 1;
  if (_failRetryTimer) clearTimeout(_failRetryTimer);
  _failRetryTimer = setTimeout(() => {
    _failRetryTimer = null;
    if (S.currentSymbol === symbol) analyze(symbol);
  }, 8000);
}

function _markAnalyzeFail(symbol, err) {
  document.getElementById('loading').style.display = 'none';
  const el = document.getElementById('sum-body');
  const retryLink = `<span style="color:#4fc3f7;cursor:pointer;text-decoration:underline" data-act="analyze" data-code="${escHtml(symbol)}">立即重试</span>`;
  const autoTxt = _failRetryCount < _MAX_FAIL_RETRY ? '，8秒后自动重试' : '';
  const reason = '行情数据源暂时连不上，稍后再试';
  const hint = isTimeoutError(err) ? '（请求超时，15 秒无响应）' : '（本地服务可能未启动）';
  if (_lastOkTime && el.innerHTML.trim()) {
    // 已有上次成功结果：保留旧数据，只在结论区顶部插一条失败横幅
    const oldBanner = document.getElementById('analyze-fail-banner');
    if (oldBanner) oldBanner.remove();
    el.insertAdjacentHTML('afterbegin',
      `<div id="analyze-fail-banner" style="margin-bottom:8px;padding:6px 8px;background:rgba(255,107,107,0.08);border-radius:6px;border:1px solid rgba(255,107,107,0.15);font-size:11px;color:#ff6b6b;line-height:1.5">⚠ 本次刷新失败，以下为上次结果（计算于 ${_lastOkTime}）· ${retryLink}${autoTxt}</div>`);
  } else {
    // 无历史数据：整块错误提示 + 重试入口
    el.innerHTML = `<div class="sum-text" style="color:${C.down}">${reason}<span style="color:#888;font-size:11px"> ${hint}</span><br><span style="display:inline-block;margin-top:8px;font-size:11px">${retryLink}${autoTxt}</span></div>`;
  }
  _scheduleFailRetry(symbol);
}

// ==================== 错误码 → 人话文案（improvements #5） ====================
export async function analyze(symbol) {
  S.currentSymbol = symbol;
  try { localStorage.setItem('qs_last_symbol', symbol); } catch(e) {}
  document.getElementById('loading').style.display = 'flex';
  if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
  if (_failRetryTimer) { clearTimeout(_failRetryTimer); _failRetryTimer = null; }

  // 周K视图传period=week，否则默认day
  const periodParam = S.currentView === 'week' ? '&period=week' : '';

  // 缠论接口单独容错：它失败不应拖垮主分析渲染
  const clFetch = fetchWithTimeout(`${API}/api/chanlun_daily?symbol=${symbol}${periodParam}`).catch(() => null);

  try {
    const r = await fetchWithTimeout(`${API}/api/analyze?symbol=${symbol}${periodParam}`);
    const data = await r.json();
    const clRes = await clFetch;
    S._dailyChanlun = null;
    try { if (clRes) S._dailyChanlun = await clRes.json(); } catch(e) {}
    document.getElementById('loading').style.display = 'none';

    if (data.error) {
      console.warn('analyze error:', symbol, data.error);
      const retryLinkHtml = `<span style="color:#4fc3f7;cursor:pointer;text-decoration:underline" data-act="analyze" data-code="${escHtml(symbol)}">立即重试</span>`;
      document.getElementById('sum-body').innerHTML =
        `<div class="sum-text" style="color:${C.down}">${escHtml(explainError(data))}<br><span style="display:inline-block;margin-top:8px;font-size:11px">${retryLinkHtml}</span></div>`;
      return;
    }

    updateQuote(data.quote);
    renderKline(data.klines, data.signal);
    renderFlow(data.flows);
    renderSignal(data.signal);
    renderDataMeta(data.data_meta);
    renderMomentum(data.signal.momentum);
    renderKeyLevels(data.signal.key_levels);
    renderMarket(data.signal, data.breadth);

    // 记录历史 & 更新自选星标
    const _sName = data.quote ? data.quote.name : symbol;
    const _sAction = data.signal ? data.signal.action : '';
    const _sScore = data.signal ? data.signal.score : 0;
    const _sPrice = data.quote ? data.quote.price : 0;
    addHistory(symbol, _sName, _sAction, _sScore);
    updateStarButton(symbol);

    // 更新自选股中的action/score（用于多股一览显示）
    const _wl = getWatchlist();
    const _wi = _wl.findIndex(s => s.code === symbol);
    if (_wi >= 0) {
      _wl[_wi].action = _sAction;
      _wl[_wi].score = _sScore;
      _wl[_wi].name = _sName;
      _wl[_wi].price = _sPrice || _wl[_wi].price;
      saveWatchlist(_wl);
      renderSidebar();
    }

    // 信号变更检测 & Toast提醒
    checkSignalChange(symbol, _sName, _sAction, _sScore, _sPrice);

    // 记录信号到localStorage（用于相邻查看方向一致率统计）
    if (_sAction && _sPrice > 0) {
      recordSignal(symbol, _sName, _sAction, _sScore, _sPrice);
    }

    // 渲染相邻查看方向一致率统计
    renderSignalAccuracy(symbol);

    // 成功标记：记录本次成功时间并重置连续失败计数
    _lastOkTime = (data.data_meta && data.data_meta.calculated_at) || new Date().toLocaleTimeString();
    _failRetryCount = 0;

    // 缠论日线/周线分析
    document.getElementById('chanlun-daily-label').textContent = S.currentView === 'week' ? '缠论周线分析' : '缠论日线分析';
    if (S._dailyChanlun && !S._dailyChanlun.error) {
      renderChanlunDaily(S._dailyChanlun);
      applyChanlunDailyOverlay(S._dailyChanlun);
      document.getElementById('chanlun-daily-card').style.display = 'block';
    } else {
      document.getElementById('chanlun-daily-card').style.display = 'none';
    }

    // 缠论分时面板在分时视图才显示
    if (S.currentView !== 'minute') {
      document.getElementById('chanlun-card').style.display = 'none';
    }

    if (S.currentView === 'minute') loadMinute(symbol);
    fxCardStagger();   // 右侧卡片依次淡入（FX标准/炫酷档）
    _refreshTimer = setInterval(() => refreshQuote(symbol), 2000);
  } catch(e) {
    console.warn('analyze failed:', symbol, e);
    _markAnalyzeFail(symbol, e);
  }
}

async function refreshQuote(symbol) {
  try {
    const r = await fetchWithTimeout(`${API}/api/quote?symbol=${symbol}`);
    const q = await r.json();
    if (!q.error) {
      updateQuote(q);
      // K线最后一根蜡烛跟随实时行情更新
      refreshKlineLastCandle(q);
      if (S.currentView === 'minute') {
        // 分时视图：用轻量刷新，只更新价格数据，不全量重载图表
        refreshMinuteLight(symbol);
      }
      // 实时资金流刷新（5秒间隔，后端有5秒缓存）
      if (S._flowMode === 'realtime') {
        loadRealtimeFlow(symbol);
      }
    }
  } catch(e) {}
}

// 用最新行情刷新K线最后一根蜡烛（日线实时感）

// ==================== 网络请求统一超时封装（improvements #3） ====================

const _origUpdateQuote = updateQuote;
updateQuote = function(q) {
  _origUpdateQuote(q);
  if (q && q.name) S._currentStockName = q.name;
};

// ---- 右键菜单 ----
export function toggleSettings() {
  const o = document.getElementById('settings-overlay');
  if (!o) return;
  const opening = o.style.display !== 'flex';
  o.style.display = opening ? 'flex' : 'none';
  applyFx();
  if (opening) refreshNotifyStatus();   // 打开设置时刷新推送状态行
}
function closeSettings(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  const o = document.getElementById('settings-overlay'); if (o) o.style.display = 'none';
}
function closeSettingsForce() { const o = document.getElementById('settings-overlay'); if (o) o.style.display = 'none'; }

// ===== 启动 =====
applyFx();                 // FX 档位（body class + 设置面板状态）
sidebarLoadState();        // 侧边栏开合记忆
loadSbSection();           // 上次停留的工作台分区
migrateWatchlist();        // 旧自选一次性迁移（在首次渲染前执行）
initCharts();
updateBadges();
loadMode();
loadNotifySettings();      // 钉钉推送：启动即拉取配置与状态（设置面板回显用）
// 工作台分区 tab 点击
const _sbTabs = document.getElementById('sb-tabs');
if (_sbTabs) _sbTabs.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('.sb-tab');
  if (b) openSbSection(b.dataset.sb);
});
// improvements #10：页签 ARIA 语义 + 左右键导航
function _syncSbTabsAria() {
  document.querySelectorAll('.sb-tab').forEach(t =>
    t.setAttribute('aria-selected', t.classList.contains('active') ? 'true' : 'false'));
}
if (_sbTabs) {
  _sbTabs.setAttribute('role', 'tablist');
  document.querySelectorAll('.sb-tab').forEach(t => {
    t.setAttribute('role', 'tab');
    t.setAttribute('tabindex', '0');
    t.setAttribute('aria-selected', t.classList.contains('active') ? 'true' : 'false');
  });
  _sbTabs.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const tabs = Array.from(_sbTabs.querySelectorAll('.sb-tab'));
    const idx = tabs.indexOf(document.activeElement);
    if (idx < 0) return;
    e.preventDefault();
    const next = tabs[(idx + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    next.focus();
    next.click();   // 走既有 click 委托切换分区
  });
}
applySidebar();
renderSidebar();
renderSbSection();

sbSchedulePolling();
sbRefreshQuotes();         // 启动即刷一轮自选行情
// 启动：恢复上次选中的股票（若从未分析过则不自动加载，避免默认跳到茅台）
let _lastSymbol = '';
try { _lastSymbol = localStorage.getItem('qs_last_symbol') || ''; } catch(e) {}
if (_lastSymbol) analyze(_lastSymbol);

// ===== 小白/专业模式切换 =====
function loadMode() {
  try { S._mode = localStorage.getItem('qs_mode') || 'pro'; } catch(e) { S._mode = 'pro'; }
  applyMode();
}
export function setMode(mode) {
  S._mode = mode;
  try { localStorage.setItem('qs_mode', mode); } catch(e) {}
  applyMode();
  // 小白模式：折叠部分卡片（风险卡不折叠——小白必须看到风险，frontend-ux-v42 A18）
  if (mode === 'simple') {
    collapseCard('momentum', true);
    collapseCard('levels', true);
    collapseCard('chanlun-daily', true);
    collapseCard('chanlun-minute', true);
    collapseCard('accuracy', true);
  } else {
    // 专业模式：全部展开
    document.querySelectorAll('.signal-card.collapsed, .chanlun-card.collapsed').forEach(c => c.classList.remove('collapsed'));
  }
  // 模式切换即时生效：用缓存的上次信号数据重渲染结论与信号列表（无需重新请求）
  if (S._lastSignalData) { renderSummary(S._lastSignalData); }
}
function applyMode() {
  document.body.classList.remove('mode-pro', 'mode-simple');
  document.body.classList.add('mode-' + S._mode);
  document.getElementById('mt-pro').classList.toggle('active', S._mode === 'pro');
  document.getElementById('mt-simple').classList.toggle('active', S._mode === 'simple');
}

// ===== 卡片折叠 =====
function toggleCard(headerEl) {
  const card = headerEl.closest('.signal-card, .chanlun-card');
  if (card) card.classList.toggle('collapsed');
  // improvements #10：折叠状态同步 aria-expanded
  if (headerEl && headerEl.hasAttribute('aria-expanded')) {
    const expanded = headerEl.getAttribute('aria-expanded') === 'true';
    headerEl.setAttribute('aria-expanded', expanded ? 'false' : 'true');
  }
}
// 键盘可达：折叠头 Enter/Space 触发（improvements #10）
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const h = e.target && e.target.closest && e.target.closest('.sc-header');
  if (!h) return;
  e.preventDefault();
  toggleCard(h);
});
function collapseCard(cardName, collapse) {
  const card = document.querySelector(`[data-card="${cardName}"]`);
  if (card) card.classList.toggle('collapsed', collapse);
}

// ==================== 静态 inline handler 的显式全局暴露清单（spec §14/A88 过渡期约定） ====================
Object.assign(window, {
  analyze, setMode, toggleSettings, closeSettings, setFx, doLogout,
  toggleStar, toggleSbSection, toggleSidebar, addGroupInline, clearCurrentTab,
  switchIndicator, switchFlowMode, toggleCard, exportWatchlist, importWatchlist,
  closeScan, openScan, startScan, renderScanIdle, clearScanArchive,
  renderScanArchiveList, renderArchivedRun, exportScanCsv, deleteScanRun, analyzeFromScan,
  toggleWhy, exportJournalCsv, exportJournalJson, poolAdd, poolImportSubmit, poolFillIndustry,
  applyRange, updateQuote, renderPoolPanel, refreshDigest, stopScanPolling,
  saveNotifySettings, testNotify, runNotifyOnce,
});

// 侧边栏开合需要图表 resize（chart 实例为 chart.js 私有）
registerResizeHook(resizeAllChartsSafe);


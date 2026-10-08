const riskLabels = { normal: '正常', guarded: '回撤警戒', halted: '清仓暂停', failed: '风险目标未达成' };
const reasons = {
  stop: '触及硬止损', risk: '组合风险减仓', risk_reduce: '组合风险减仓',
  signal: '趋势退出', trend_exit: '趋势退出', limit_down_deferred: '跌停，等待成交',
  t1_restriction: '当日买入，等待 T+1', limit_up_deferred: '涨停，取消买入',
  expired: '买入计划已过期', unavailable: '数据不可用', stale_quote: '报价已过期',
  gap_too_large: '高开超过允许范围', insufficient_cash: '可用资金不足',
  corporate_action_unverified: '价格基准变化，等待核验',
  risk_halted: '回撤达到清仓阈值', risk_failed: '回撤突破风险目标，持续清仓',
  single_cap: '单股仓位超限', industry_cap: '行业仓位超限', stock_cap: '总仓位超限',
  planned_risk_cap: '组合计划止损风险超限', unreliable_valuation: '无法可靠估值',
  insufficient_risk_or_capacity: '剩余风险或仓位额度不足', position_limit: '持仓数量已达上限',
  already_holding: '已经持有，不再加仓', limit_up: '涨停，无法买入',
};

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function number(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function table(headers, rows, empty = '暂无记录') {
  if (!rows.length) return `<p class="sim-empty">${escapeHtml(empty)}</p>`;
  return `<table class="sim-table"><thead><tr>${headers.map(h => `<th scope="col">${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(v => `<td>${escapeHtml(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

export function controls(data, busy = false) {
  const exists = Boolean(data.exists);
  const terminal = ['halted', 'failed'].includes(data.risk?.status);
  const positions = Object.keys(data.state?.positions || {}).length;
  const running = data.status === 'running';
  return {
    create: busy || running || Boolean(data.enabled) || positions > 0,
    enable: busy || running || !exists || Boolean(data.enabled) || (terminal && positions === 0),
    pause: busy || !exists || !data.enabled,
    run: busy || running || !exists || !data.enabled,
  };
}

function reason(value) { return value ? String(value).split('+').map(v => reasons[v] || v).join('；') : '—'; }
let latest = {};
let busy = false;
let fetching = false;
let chart;
const el = id => document.getElementById(`trend-${id}`);

function render(data) {
  latest = data;
  const state = data.state || {};
  const risk = data.risk || {};
  const summary = data.summary || {};
  const usable = risk.reliable !== false;
  const label = data.exists ? `${riskLabels[risk.status] || '等待评估'} · ${data.enabled ? '自动模拟已启用' : '自动模拟已暂停'}` : '尚未创建模拟运行';
  el('status').textContent = data.status === 'running' ? `${label} · 正在执行` : label;
  for (const [key, disabled] of Object.entries(controls(data, busy))) el(key).disabled = disabled;
  el('enable').textContent = ['halted', 'failed'].includes(risk.status) ? '继续清仓' : '启用自动模拟';
  const equity = risk.equity ?? summary.equity;
  const values = [
    ['组合净值', data.exists && usable ? number(equity) : '—'],
    ['可用现金', data.exists ? number(state.cash) : '—'],
    ['当前回撤', data.exists && usable ? `${number((risk.drawdown || 0) * 100)}%` : '—'],
    ['股票仓位', data.exists && usable && equity > 0 ? `${number((risk.market_value || 0) / equity * 100)}%` : '—'],
    ['持仓数量', Object.keys(state.positions || {}).length],
    ['风险状态', usable ? (riskLabels[risk.status] || '尚未评估') : '无法可靠估值'],
  ];
  el('overview').innerHTML = `<div class="sim-ov-grid">${values.map(([k, v]) => `<div class="sim-ov-item"><div class="sim-ov-label">${escapeHtml(k)}</div><div class="sim-ov-val">${escapeHtml(v)}</div></div>`).join('')}</div>`;
  el('risk-note').textContent = !data.exists ? '新建后默认暂停，启用后按固定规则模拟。' : !usable ? '行情不完整或已过期，暂停新增风险，等待有效数据。' : `运行：${state.run_id || '—'}；最近收盘计划：${state.last_screen_date || '尚未生成'}。`;
  el('positions').innerHTML = table(['股票', '股数', '买入价', '硬止损', '行业', '待执行原因'], Object.values(state.positions || {}).map(p => {
    const pending = (state.sell_queue || []).find(order => order.symbol === p.symbol);
    return [`${p.name || ''} ${p.symbol}`, number(p.shares, 0), number(p.buy_price), number(p.stop), p.industry || '未知行业',
      pending ? [reason(pending.reason), pending.last_error ? reason(pending.last_error) : '待成交'].join('；') : '—'];
  }), '暂无持仓；现金也是组合的一部分。');
  el('plans').innerHTML = table(['方向', '股票', '信号日期', '依据', '未成交原因'], [
    ...(state.buy_queue || []).map(p => ['买入', `${p.name || ''} ${p.symbol}`, p.date || p.signal_date || p.trigger_date || '—', reason(p.reason), '待次日开盘检查']),
    ...(state.sell_queue || []).map(p => ['卖出', p.symbol, p.date || p.signal_date || p.trigger_date || '—', reason(p.reason), p.last_error ? reason(p.last_error) : '待成交']),
  ], '暂无待执行计划。');
  el('events').innerHTML = table(['时间', '股票', '记录'], (data.events || []).slice(-30).reverse().map(e => [e.ts || e.date, e.symbol || '账户', e.message || reason(e.reason || e.type)]));
  el('trades').innerHTML = table(['时间', '方向', '股票', '股数', '成交价', '费用', '盈亏', '原因'], (data.trades || []).slice(-50).reverse().map(t => [t.ts, t.side === 'buy' ? '买入' : '卖出', `${t.name || ''} ${t.symbol}`, number(t.shares, 0), number(t.price), number(t.fees), number(t.pnl), reason(t.reason)]));
  el('runs').innerHTML = table(['历史运行', '创建时间', '状态'], (data.runs || []).map(r => [r.run_id, r.created_at, riskLabels[r.status] || r.status || '已保留']));
  if (window.echarts) {
    chart ||= window.echarts.init(el('chart'));
    const rows = data.equity || [];
    chart.setOption({ backgroundColor: 'transparent', grid: { left: 70, right: 20, top: 25, bottom: 35 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: rows.map(r => r.ts || r.date), axisLabel: { color: '#999' } }, yAxis: { type: 'value', scale: true, axisLabel: { color: '#999' }, splitLine: { lineStyle: { color: '#282828' } } }, series: [{ name: '组合净值', type: 'line', showSymbol: false, connectNulls: false, data: rows.map(r => r.reliable === false ? null : r.equity), lineStyle: { color: '#4fc3f7', width: 2 } }] });
  }
}

function showError(message = '') { el('error').textContent = message; el('error').hidden = !message; }

async function request(body) {
  const response = await fetch('/api/trend', body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  if (response.status === 401) { window.location.assign('/login.html'); throw new Error('请先登录'); }
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || '请求失败');
  return data;
}

async function refresh() {
  if (fetching) return;
  fetching = true;
  try { render(await request()); showError(latest.error || ''); }
  catch (error) { showError(error.message); for (const key of ['create', 'enable', 'pause', 'run']) el(key).disabled = true; }
  finally { fetching = false; }
}

async function act(action) {
  if (busy) return;
  busy = true;
  render(latest);
  let failure = '';
  try { await request({ action }); showError(); }
  catch (error) { failure = error.message; }
  finally { busy = false; render(latest); }
  await refresh();
  if (failure) showError(failure);
}

if (typeof document !== 'undefined') {
  for (const action of ['create', 'enable', 'pause', 'run']) el(action).addEventListener('click', () => act(action));
  el('refresh').addEventListener('click', refresh);
  window.addEventListener('resize', () => chart?.resize());
  refresh();
  window.setInterval(() => { if (!document.hidden && !busy) refresh(); }, 10000);
}

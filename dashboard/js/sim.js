// ==================== 模拟账户（v6 sim-account：策略自动买卖 + 记账 + 绩效）====================
import { API, fetchWithTimeout } from './api.js';
import { showToastMsg, escHtml } from './ui.js';

let _simData = null;
let _equityChart = null;

const _fmtMoney = (v) => (v == null ? '--' : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const _fmtPct = (v) => (v == null ? '--' : `${Number(v).toFixed(2)}%`);
const _fmtSigned = (v, suffix = '') => {
  if (v == null) return '--';
  const n = Number(v);
  const s = n > 0 ? '+' : '';
  return `${s}${n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${suffix}`;
};

function _pnlClass(v) {
  if (v == null) return '';
  const n = Number(v);
  return n > 0 ? 'up' : n < 0 ? 'down' : '';
}

// ---------------------------------------------------------------- 概览

function _renderOverview(data) {
  const el = document.getElementById('sim-overview');
  if (!el) return;
  const a = data.account || {};
  const pnlCls = _pnlClass(a.total_pnl);
  el.innerHTML = `
    <div class="sim-ov-grid">
      <div class="sim-ov-item"><span class="sim-ov-label">总资产</span><span class="sim-ov-val">${_fmtMoney(a.equity)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">可用资金</span><span class="sim-ov-val">${_fmtMoney(a.cash)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">持仓市值</span><span class="sim-ov-val">${_fmtMoney(a.market_value)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">总盈亏</span><span class="sim-ov-val ${pnlCls}">${_fmtSigned(a.total_pnl)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">收益率</span><span class="sim-ov-val ${pnlCls}">${_fmtPct(a.total_pnl_pct)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">已实现</span><span class="sim-ov-val">${_fmtSigned(a.realized_pnl)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">浮动盈亏</span><span class="sim-ov-val ${_pnlClass(a.unrealized_pnl)}">${_fmtSigned(a.unrealized_pnl)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">胜率</span><span class="sim-ov-val">${a.win_rate == null ? '--' : a.win_rate + '%'}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">平仓笔数</span><span class="sim-ov-val">${a.trade_count || 0}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">持仓只数</span><span class="sim-ov-val">${a.position_count || 0}</span></div>
    </div>
  `;
}

function _renderMetrics(data) {
  const el = document.getElementById('sim-metrics');
  if (!el) return;
  const m = data.metrics || {};
  const insufficient = m.sample_sufficient === false && (m.days || 0) > 0
    ? '<span class="sim-miss" title="净值样本不足 20 个交易日，指标仅供参考">样本不足</span>' : '';
  const note = (m.days || 0) < 2 ? '<span class="sim-miss">净值点数不足，暂无法计算</span>' : '';
  el.innerHTML = `
    <div class="sim-ov-grid">
      <div class="sim-ov-item"><span class="sim-ov-label">年化收益率</span><span class="sim-ov-val ${_pnlClass(m.annualized)}">${_fmtPct(m.annualized)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">最大回撤</span><span class="sim-ov-val down">${m.max_drawdown == null ? '--' : '-' + Number(m.max_drawdown).toFixed(2) + '%'}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">夏普</span><span class="sim-ov-val">${m.sharpe == null ? '--' : Number(m.sharpe).toFixed(2)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">卡玛</span><span class="sim-ov-val">${m.calmar == null ? '--' : Number(m.calmar).toFixed(2)}</span></div>
    </div>
    <div class="sim-metrics-note">${insufficient}${note}${insufficient || note ? '　' : ''}口径：无风险利率 0%；样本不足时仅参考。</div>
  `;
}

// ---------------------------------------------------------------- 持仓

function _renderPositions(data) {
  const el = document.getElementById('sim-positions');
  if (!el) return;
  const positions = data.positions || [];
  if (!positions.length) {
    el.innerHTML = '<div class="sim-empty">暂无持仓。开启自动交易后，策略会在交易时段自动选股买入。</div>';
    return;
  }
  el.innerHTML = `<table class="sim-table">
    <thead><tr>
      <th>代码 / 名称</th><th>数量</th><th>成本</th><th>现价</th><th>浮动盈亏</th>
      <th>止损</th><th>目标</th><th>持有</th><th>策略</th><th></th>
    </tr></thead><tbody>
    ${positions.map((p) => `
      <tr>
        <td><b>${escHtml(p.name || p.symbol)}</b><span class="code">${escHtml(p.symbol)}</span></td>
        <td>${p.shares}</td>
        <td>${Number(p.avg_cost).toFixed(2)}</td>
        <td>${p.current_price == null ? '—' : Number(p.current_price).toFixed(2)}</td>
        <td class="${_pnlClass(p.pnl)}">${_fmtSigned(p.pnl)}<span class="sim-sub">${_fmtPct(p.pnl_pct)}</span></td>
        <td>${p.stop ? Number(p.stop).toFixed(2) : '—'}</td>
        <td>${p.target ? Number(p.target).toFixed(2) : '—'}</td>
        <td>${p.hold_days == null ? '—' : p.hold_days + ' 天'}</td>
        <td class="sim-sub">${escHtml(p.strategy || '—')}</td>
        <td><button class="settings-btn" data-symbol="${escHtml(p.symbol)}" onclick="simSell(this.dataset.symbol)">卖出</button></td>
      </tr>`).join('')}
    </tbody></table>`;
}

// ---------------------------------------------------------------- 成交流水

function _renderTrades(data) {
  const el = document.getElementById('sim-trades');
  if (!el) return;
  const trades = data.trades || [];
  if (!trades.length) {
    el.innerHTML = '<div class="sim-empty">暂无成交记录。</div>';
    return;
  }
  el.innerHTML = `<table class="sim-table">
    <thead><tr><th>时间</th><th>方向</th><th>标的</th><th>价格</th><th>数量</th><th>费用</th><th>盈亏</th><th>原因</th><th>策略</th></tr></thead>
    <tbody>
    ${trades.map((t) => {
      const isBuy = t.side === 'buy';
      return `<tr>
        <td class="sim-sub">${escHtml(t.date || '')} ${escHtml((t.ts || '').slice(11, 16))}</td>
        <td><span class="sim-side ${isBuy ? 'sim-buy' : 'sim-sell'}">${isBuy ? '买入' : '卖出'}</span></td>
        <td>${escHtml(t.name || t.symbol)}<span class="code">${escHtml(t.symbol)}</span></td>
        <td>${t.price == null ? '—' : Number(t.price).toFixed(2)}</td>
        <td>${t.shares}</td>
        <td>${t.fees == null ? '—' : Number(t.fees).toFixed(2)}</td>
        <td class="${_pnlClass(t.pnl)}">${isBuy ? '—' : _fmtSigned(t.pnl)}</td>
        <td class="sim-sub">${escHtml(t.reason || '')}${t.note === 'forced' ? '<span class="sim-tag">强平</span>' : ''}</td>
        <td class="sim-sub">${escHtml(t.strategy || '—')}</td>
      </tr>`;
    }).join('')}
    </tbody></table>`;
}

// ---------------------------------------------------------------- 净值曲线

function _renderEquity(data) {
  const el = document.getElementById('sim-equity-chart');
  if (!el) return;
  const rows = data.equity || [];
  if (!rows.length || typeof echarts === 'undefined') {
    if (el) el.innerHTML = '<div class="sim-empty">暂无净值数据。每轮巡检后这里会画出净值曲线。</div>';
    return;
  }
  const dates = rows.map((r) => (r.date || (r.ts || '').slice(0, 10)));
  const vals = rows.map((r) => Number(r.equity || 0));
  const initial = (data.account || {}).initial_capital || null;
  if (_equityChart == null) _equityChart = echarts.init(el);
  _equityChart.setOption({
    grid: { left: 60, right: 16, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#888', fontSize: 10 } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#888', fontSize: 10 } },
    series: [{
      name: '总资产', type: 'line', data: vals, showSymbol: false,
      lineStyle: { color: '#4fc3f7', width: 1.5 },
      areaStyle: { color: 'rgba(79,195,247,0.08)' },
    }, ...(initial ? [{
      name: '初始资金', type: 'line', data: dates.map(() => initial), showSymbol: false,
      lineStyle: { color: '#888', type: 'dashed', width: 1 }, silent: true,
    }] : [])],
  });
}

// ---------------------------------------------------------------- 配置面板

function _renderConfig(data) {
  const cfg = data.config || {};
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el && val != null) el.value = String(val);
  };
  const enabledEl = document.getElementById('sim-enabled');
  if (enabledEl) enabledEl.checked = !!cfg.enabled;
  set('sim-initial-capital', cfg.initial_capital);
  set('sim-universe', cfg.universe);
  set('sim-scan-limit', cfg.scan_limit);
  set('sim-interval', cfg.interval_min);
  set('sim-screening', cfg.screening_interval_min);
  set('sim-max-positions', cfg.max_positions);
  set('sim-per-trade', cfg.per_trade_pct);
  set('sim-max-hold', cfg.max_hold_days);
  set('sim-min-score', cfg.min_score);
  const levels = new Set(cfg.buy_levels || []);
  ['strong', 'normal', 'cautious'].forEach((lv) => {
    const el = document.getElementById('sim-level-' + lv);
    if (el) el.checked = levels.has(lv);
  });
  ['auto_sell', 'stop_loss_enabled', 'take_profit_enabled'].forEach((k) => {
    const el = document.getElementById('sim-' + k);
    if (el) el.checked = !!cfg[k];
  });
}

// ---------------------------------------------------------------- 状态行

function _renderStateLine(data) {
  const el = document.getElementById('sim-state-line');
  if (!el) return;
  const s = data.state || {};
  const cfg = data.config || {};
  const bits = [];
  bits.push(cfg.enabled ? '已启用' : '未启用');
  const statusMap = { running: '巡检中', done: '已完成', error: '异常', waiting_market: '等待开盘', busy: '巡检中', idle: '' };
  if (statusMap[s.status]) bits.push(statusMap[s.status]);
  if (s.last_run_at) bits.push('最近巡检 ' + (s.last_run_at || '').slice(5, 16));
  if (s.last_bought) bits.push('本轮买 ' + s.last_bought);
  if (s.last_sold) bits.push('本轮卖 ' + s.last_sold);
  if (s.last_unfilled) bits.push('放弃 ' + s.last_unfilled);
  if (s.last_equity) bits.push('净值 ' + _fmtMoney(s.last_equity));
  if (s.rounds) bits.push('累计 ' + s.rounds + ' 轮');
  let text = bits.join(' · ');
  if (s.last_error) text += '　⚠ ' + escHtml(s.last_error);
  el.innerHTML = text;
}

// ---------------------------------------------------------------- 交互

export async function loadSimPanel() {
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data || data.ok === false) return;
    _simData = data;
    _renderOverview(data);
    _renderMetrics(data);
    _renderPositions(data);
    _renderTrades(data);
    _renderEquity(data);
    _renderConfig(data);
    _renderStateLine(data);
  } catch (e) { /* 静默：账户面板不因接口失败而不可用 */ }
}
export function refreshSimStatus() { return loadSimPanel(); }
export function renderSimPanel() { loadSimPanel(); }

function _readConfigForm() {
  const val = (id) => (document.getElementById(id) || {}).value;
  const num = (id, def) => {
    const n = parseFloat(val(id));
    return Number.isFinite(n) ? n : def;
  };
  const int = (id, def) => {
    const n = parseInt(val(id), 10);
    return Number.isNaN(n) ? def : n;
  };
  const levels = ['strong', 'normal', 'cautious'].filter((lv) => {
    const el = document.getElementById('sim-level-' + lv);
    return el && el.checked;
  });
  return {
    enabled: !!((document.getElementById('sim-enabled') || {}).checked),
    initial_capital: num('sim-initial-capital', 100000),
    universe: val('sim-universe') || 'scan',
    scan_limit: int('sim-scan-limit', 300),
    interval_min: int('sim-interval', 15),
    screening_interval_min: int('sim-screening', 60),
    max_positions: int('sim-max-positions', 5),
    per_trade_pct: num('sim-per-trade', 20),
    max_hold_days: int('sim-max-hold', 0),
    min_score: int('sim-min-score', 0),
    buy_levels: levels,
    auto_sell: !!((document.getElementById('sim-auto_sell') || {}).checked),
    stop_loss_enabled: !!((document.getElementById('sim-stop_loss_enabled') || {}).checked),
    take_profit_enabled: !!((document.getElementById('sim-take_profit_enabled') || {}).checked),
  };
}

export async function saveSimConfig() {
  const form = _readConfigForm();
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'save', ...form }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? '模拟账户配置已保存' : (data.error || '保存失败'));
  } catch (e) {
    showToastMsg('保存请求失败，请稍后再试');
  }
  loadSimPanel();
}

export async function runSimOnce() {
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'run_once', force: true }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? '已触发一轮巡检（后台执行）' : (data.error || '触发失败'));
    if (data.ok) { setTimeout(loadSimPanel, 4000); setTimeout(loadSimPanel, 12000); }
  } catch (e) {
    showToastMsg('请求失败，请稍后再试');
  }
}

export async function resetSimAccount() {
  if (!window.confirm('确定重置模拟账户？将清空全部持仓与流水并恢复初始资金，此操作不可逆。')) return;
  const capitalEl = document.getElementById('sim-initial-capital');
  const capital = capitalEl ? parseFloat(capitalEl.value) : undefined;
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reset', capital: Number.isFinite(capital) ? capital : undefined }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? '模拟账户已重置' : (data.error || '重置失败'));
  } catch (e) {
    showToastMsg('请求失败，请稍后再试');
  }
  loadSimPanel();
}

export async function simBuy(symbol) {
  if (!symbol) return;
  const amountRaw = window.prompt(`模拟买入 ${symbol}，输入预算金额（留空 = 按当前仓位规则）`, '');
  if (amountRaw === null) return;
  let amount = undefined;
  if (amountRaw.trim() !== '') {
    const n = parseFloat(amountRaw);
    if (!Number.isFinite(n) || n <= 0) { showToastMsg('金额无效'); return; }
    amount = n;
  }
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'buy', symbol, amount }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? `已模拟买入 ${symbol}` : (data.error || '买入失败'));
  } catch (e) {
    showToastMsg('请求失败，请稍后再试');
  }
  loadSimPanel();
}

export async function simSell(symbol) {
  if (!symbol) return;
  if (!window.confirm(`确定模拟卖出 ${symbol}（全部持仓）？`)) return;
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'sell', symbol }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? `已模拟卖出 ${symbol}` : (data.error || '卖出失败'));
  } catch (e) {
    showToastMsg('请求失败，请稍后再试');
  }
  loadSimPanel();
}

export async function simBuyPrompt() {
  const raw = window.prompt('输入要模拟买入的股票代码', '');
  if (!raw) return;
  const symbol = String(raw).trim().padStart(6, '0');
  if (!/^\d{6}$/.test(symbol)) { showToastMsg('请输入 6 位股票代码'); return; }
  simBuy(symbol);
}

export function onSimResize() {
  if (_equityChart) { try { _equityChart.resize(); } catch (e) {} }
}

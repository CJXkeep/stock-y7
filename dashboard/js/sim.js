// ==================== 模拟账户（v6 sim-account：策略自动买卖 + 记账 + 绩效）====================
// 注意：本模块被 sim.html 大页独立加载（sim_page.js），只依赖 api.js 与 shared.js
// 两个零 DOM 依赖模块——不得 import ui.js（看板模块图在 sim.html 上会抛错）。
import { API, fetchWithTimeout } from './api.js';
import { showToastMsg, escHtml } from './shared.js';

let _simData = null;
let _equityChart = null;
let _strategySchema = {};   // 当前策略参数 schema（/api/sim strategy_schema）
let _strategyOptions = [];  // 注册表枚举 [{id, label, params_schema?}]（策略切换即时重渲染用）
let _cfgDirty = false;      // 配置表单有未保存修改：自动刷新跳过表单重置（防冲掉编辑）

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
  const binfo = data.benchmark_info || {};
  const insufficient = m.sample_sufficient === false && (m.days || 0) > 0
    ? '<span class="sim-miss" title="净值样本不足 20 个交易日，指标仅供参考">样本不足</span>' : '';
  const note = (m.days || 0) < 2 ? '<span class="sim-miss">净值点数不足，暂无法计算</span>' : '';
  const benchName = escHtml(binfo.name || binfo.code || '基准');
  // 超额区（v8 基准对比）：对齐样本不足照常计算但标注；信息比率额外要求样本 ≥20 才给数
  let excessHtml = '';
  if (m.excess_coverage_days != null && (binfo.coverage_days || 0) > 0) {
    const exInsufficient = m.excess_sample_sufficient === false
      ? '<span class="sim-miss" title="对齐基准样本不足 20 个交易日，超额指标仅供参考">样本不足</span>' : '';
    const irCell = m.excess_information_ratio == null
      ? (m.excess_sample_sufficient ? '--' : '<span class="sim-miss">样本不足</span>')
      : Number(m.excess_information_ratio).toFixed(2);
    excessHtml = `
      <div class="sim-excess-title">超额（vs ${benchName}）${exInsufficient}</div>
      <div class="sim-ov-grid">
        <div class="sim-ov-item"><span class="sim-ov-label">超额年化</span><span class="sim-ov-val ${_pnlClass(m.excess_annualized)}">${_fmtPct(m.excess_annualized)}</span></div>
        <div class="sim-ov-item"><span class="sim-ov-label">超额最大回撤</span><span class="sim-ov-val down">${m.excess_max_drawdown == null ? '--' : '-' + Number(m.excess_max_drawdown).toFixed(2) + '%'}</span></div>
        <div class="sim-ov-item"><span class="sim-ov-label">信息比率</span><span class="sim-ov-val">${irCell}</span></div>
        <div class="sim-ov-item"><span class="sim-ov-label">空仓天数占比</span><span class="sim-ov-val">${binfo.idle_ratio == null ? '--' : Number(binfo.idle_ratio).toFixed(1) + '%'}</span></div>
      </div>
      <div class="sim-metrics-note">覆盖 ${binfo.coverage_days || 0} 个交易日（其中空仓 ${binfo.idle_days || 0} 天）；口径：组合日收益 − 基准日收益；空仓占比高时超额可能来自「空仓躲跌」；切换基准后超额从新基准重新起算。</div>`;
  } else {
    excessHtml = '<div class="sim-metrics-note">暂无基准数据：等待下一轮净值快照写入「' + benchName + '」后开始计算超额。</div>';
  }
  el.innerHTML = `
    <div class="sim-ov-grid">
      <div class="sim-ov-item"><span class="sim-ov-label">年化收益率</span><span class="sim-ov-val ${_pnlClass(m.annualized)}">${_fmtPct(m.annualized)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">最大回撤</span><span class="sim-ov-val down">${m.max_drawdown == null ? '--' : '-' + Number(m.max_drawdown).toFixed(2) + '%'}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">夏普</span><span class="sim-ov-val">${m.sharpe == null ? '--' : Number(m.sharpe).toFixed(2)}</span></div>
      <div class="sim-ov-item"><span class="sim-ov-label">卡玛</span><span class="sim-ov-val">${m.calmar == null ? '--' : Number(m.calmar).toFixed(2)}</span></div>
    </div>
    <div class="sim-metrics-note">${insufficient}${note}${insufficient || note ? '　' : ''}口径：无风险利率 0%；样本不足时仅参考。</div>
    ${excessHtml}`;
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
        <td><a class="sim-jump" href="/?symbol=${escHtml(p.symbol)}" title="回看板分析该股"><b>${escHtml(p.name || p.symbol)}</b><span class="code">${escHtml(p.symbol)}</span></a></td>
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
  // 账户重置（reason=reset）是 append-only 审计记录，不是真实成交——单独分区展示，避免误导
  const real = trades.filter((t) => t.reason !== 'reset');
  const resetAudit = trades.filter((t) => t.reason === 'reset');
  let html = '';
  if (!real.length) {
    html += '<div class="sim-empty">暂无成交记录。账户重置产生的强制平仓记录见下方「重置审计」，不计入收益。</div>';
  } else {
    html += `<table class="sim-table">
    <thead><tr><th>时间</th><th>方向</th><th>标的</th><th>价格</th><th>数量</th><th>费用</th><th>盈亏</th><th>原因</th><th>策略</th></tr></thead>
    <tbody>
    ${real.map((t) => {
      const isBuy = t.side === 'buy';
      return `<tr>
        <td class="sim-sub">${escHtml(t.date || '')} ${escHtml((t.ts || '').slice(11, 16))}</td>
        <td><span class="sim-side ${isBuy ? 'sim-buy' : 'sim-sell'}">${isBuy ? '买入' : '卖出'}</span></td>
        <td><a class="sim-jump" href="/?symbol=${escHtml(t.symbol)}" title="回看板分析该股">${escHtml(t.name || t.symbol)}<span class="code">${escHtml(t.symbol)}</span></a></td>
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
  if (resetAudit.length) {
    html += '<div class="eval-notice-line">以下 ${resetAudit.length} 条为「账户重置」审计记录'
      + '（重置时强制平仓清算写账，仅留痕，不计入账户收益与交易次数）。</div>'
      + '<table class="sim-table"><thead><tr><th>时间</th><th>标的</th><th>价格</th><th>数量</th><th>备注</th></tr></thead><tbody>'
      + resetAudit.map((t) => `<tr>
        <td class="sim-sub">${escHtml(t.date || '')} ${escHtml((t.ts || '').slice(11, 16))}</td>
        <td><a class="sim-jump" href="/?symbol=${escHtml(t.symbol)}" title="回看板分析该股">${escHtml(t.name || t.symbol)}<span class="code">${escHtml(t.symbol)}</span></a></td>
        <td>${t.price == null ? '—' : Number(t.price).toFixed(2)}</td>
        <td>${t.shares}</td>
        <td class="sim-sub"><span class="sim-tag">重置</span>${t.note === 'forced' ? '<span class="sim-tag">强平</span>' : ''}</td>
      </tr>`).join('')
      + '</tbody></table>';
  }
  el.innerHTML = html;
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
  // 基准线（v8）：快照行内基准值归一化到「净值首日=100」画右轴，单一数据源（equity 行）
  const benchName = (data.benchmark_info || {}).name || '基准';
  const benchRaw = rows.map((r) => {
    if (r.benchmark == null || !r.benchmark_code) return null;
    const n = Number(r.benchmark);
    return Number.isFinite(n) && n > 0 ? n : null;
  });
  const firstBench = benchRaw.find((v) => v != null);
  const benchVals = firstBench
    ? benchRaw.map((v) => (v == null ? null : +(v / firstBench * 100).toFixed(2)))
    : null;
  const hasBench = !!benchVals && benchVals.filter((v) => v != null).length >= 2;

  const series = [{
    name: '总资产', type: 'line', data: vals, showSymbol: false,
    lineStyle: { color: '#4fc3f7', width: 1.5 },
    areaStyle: { color: 'rgba(79,195,247,0.08)' },
  }];
  if (initial) {
    series.push({
      name: '初始资金', type: 'line', data: dates.map(() => initial), showSymbol: false,
      lineStyle: { color: '#888', type: 'dashed', width: 1 }, silent: true,
    });
  }
  if (hasBench) {
    series.push({
      name: benchName, type: 'line', data: benchVals, yAxisIndex: 1,
      showSymbol: false, connectNulls: true,
      lineStyle: { color: '#ffb74d', width: 1.2 },
    });
  }
  const yAxis = [{ type: 'value', scale: true, axisLabel: { color: '#888', fontSize: 10 } }];
  if (hasBench) {
    yAxis.push({
      type: 'value', scale: true, position: 'right',
      axisLabel: { color: '#b58a4c', fontSize: 10 }, splitLine: { show: false },
    });
  }
  if (_equityChart == null) _equityChart = echarts.init(el);
  _equityChart.setOption({
    grid: { left: 60, right: hasBench ? 52 : 16, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#888', fontSize: 10 } },
    yAxis,
    series,
  }, true);
}

// ---------------------------------------------------------------- 配置面板

// ---------------------------------------------------------------- 策略参数动态渲染（v7 解耦）

function _renderStrategyParamsFor(schema, params) {
  const el = document.getElementById('sim-strategy-params');
  if (!el) return;
  _strategySchema = schema || {};
  const keys = Object.keys(_strategySchema);
  if (!keys.length) {
    el.innerHTML = '<div class="sim-empty">当前策略无可配置参数。</div>';
    return;
  }
  el.innerHTML = keys.map((key) => {
    const rule = _strategySchema[key] || {};
    const val = params[key] !== undefined ? params[key] : rule.default;
    const label = escHtml(rule.label || key);
    if (rule.type === 'bool') {
      return `<label class="notify-switch"><input type="checkbox" data-sp="${escHtml(key)}" ${val ? 'checked' : ''}> ${label}</label>`;
    }
    if (rule.type === 'enum' && Array.isArray(rule.options)) {
      const selected = Array.isArray(val) ? val : (val != null ? [val] : []);
      return `<div class="sim-cfg-row"><span class="sim-cfg-label">${label}</span>${rule.options.map((opt) =>
        `<label class="notify-switch"><input type="checkbox" data-sp="${escHtml(key)}" data-sp-opt="${escHtml(opt)}" ${selected.includes(opt) ? 'checked' : ''}> ${escHtml(opt)}</label>`).join('')}</div>`;
    }
    if (rule.type === 'int' || rule.type === 'float') {
      const step = rule.type === 'float' ? '0.01' : '1';
      const bounds = `${rule.min != null ? ` min="${rule.min}"` : ''}${rule.max != null ? ` max="${rule.max}"` : ''}`;
      return `<label>${label}<input type="number" data-sp="${escHtml(key)}" step="${step}"${bounds} value="${val == null ? '' : val}"></label>`;
    }
    // 未知类型容错：退化为文本输入并保留原值
    return `<label>${label}<input type="text" data-sp="${escHtml(key)}" value="${escHtml(val == null ? '' : String(val))}"></label>`;
  }).join('');
}

function _renderStrategyParams(data) {
  _renderStrategyParamsFor((data || {}).strategy_schema || {},
                           ((data || {}).config || {}).strategy_params || {});
}

function _readStrategyParams() {
  const out = {};
  document.querySelectorAll('[data-sp]').forEach((el) => {
    const key = el.dataset.sp;
    const rule = _strategySchema[key] || {};
    if (rule.type === 'enum') {
      const group = document.querySelectorAll(`[data-sp="${key}"][data-sp-opt]`);
      const picked = Array.from(group).filter((c) => c.checked).map((c) => c.dataset.spOpt);
      out[key] = picked.length ? picked : (rule.default || []);
    } else if (rule.type === 'bool') {
      out[key] = el.checked;
    } else if (rule.type === 'int') {
      const n = parseInt(el.value, 10);
      out[key] = Number.isNaN(n) ? (rule.default != null ? rule.default : 0) : n;
    } else if (rule.type === 'float') {
      const n = parseFloat(el.value);
      out[key] = Number.isNaN(n) ? (rule.default != null ? rule.default : 0) : n;
    } else {
      out[key] = el.value;
    }
  });
  return out;
}

function _renderStrategyOptions(data) {
  const el = document.getElementById('sim-strategy');
  if (!el) return;
  const cfg = data.config || {};
  const options = data.strategy_options || [];
  if (!options.length) return;   // 后端异常时保留下拉现状，避免清空选项
  _strategyOptions = options;
  el.innerHTML = options.map((o) =>
    `<option value="${escHtml(o.id)}">${escHtml(o.label || o.id)}</option>`).join('');
  el.value = cfg.strategy || (options[0] && options[0].id) || '';
}

function _renderConfig(data) {
  // 脏状态保护：有未保存修改时自动刷新不重置表单（含策略下拉与参数区），保留用户编辑
  if (_cfgDirty) return;
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
  set('sim-benchmark', cfg.benchmark || '000300');
  set('sim-signal-mode', cfg.signal_mode || 'auto');
  // 信号执行模式：默认跟随策略声明（data.signal_mode 为生效值），下拉首项动态标注
  const smSel = document.getElementById('sim-signal-mode');
  if (smSel) {
    const eff = data.signal_mode || 'close_nextday';
    const first = smSel.querySelector('option[value="auto"]');
    if (first) first.textContent = '跟随策略（当前：' + (eff === 'intraday' ? '盘中实时' : '收盘定档·次日执行') + '）';
  }
  _renderStrategyOptions(data);
  ['auto_sell', 'stop_loss_enabled', 'take_profit_enabled'].forEach((k) => {
    const el = document.getElementById('sim-' + k);
    if (el) el.checked = !!cfg[k];
  });
  // 钉钉推送（sim-notify）：enabled/app_key/app_secret/robot_code/open_conversation_id/ops
  const nt = cfg.notify || {};
  const ntEn = document.getElementById('sim-notify-enabled');
  if (ntEn) ntEn.checked = !!nt.enabled;
  const ntAppKey = document.getElementById('sim-notify-app-key');
  if (ntAppKey && !ntAppKey.value) ntAppKey.value = nt.app_key || '';
  const ntSecret = document.getElementById('sim-notify-app-secret');
  if (ntSecret && !ntSecret.value && nt.app_secret) ntSecret.placeholder = '已保存（留空保持不变）';
  const ntRobot = document.getElementById('sim-notify-robot-code');
  if (ntRobot && !ntRobot.value) ntRobot.value = nt.robot_code || '';
  const ntConv = document.getElementById('sim-notify-conversation-id');
  if (ntConv && !ntConv.value) ntConv.value = nt.open_conversation_id || '';
  const ntOps = Array.isArray(nt.ops) ? nt.ops : ['buy', 'sell'];
  ['buy', 'sell'].forEach((op) => {
    const el = document.getElementById('sim-notify-' + op);
    if (el) el.checked = (ntOps.indexOf(op) >= 0);
  });
  _renderStrategyParams(data);
}


// ---------------------------------------------------------------- 待执行计划（close_nextday）

function _renderPlans(data) {
  const el = document.getElementById('sim-plans');
  if (!el) return;
  const q = data.queues || {};
  const buys = q.buys || [];
  const sells = q.sells || [];
  if (!buys.length && !sells.length) {
    el.innerHTML = '<div class="sim-empty">暂无待执行计划。开启自动交易后，每个交易日收盘后定档生成「次日」买入/信号卖出清单。</div>';
    return;
  }
  const LEVEL = { strong: '强烈买入', normal: '买入', cautious: '谨慎买入' };
  let html = '';
  if (buys.length) {
    html += '<div class="eval-notice-line">明日买入 ' + buys.length + ' 只 · 收盘定档 ' + escHtml(q.screen_date || '--') + '</div>'
      + '<table class="sim-table"><thead><tr><th>代码 / 名称</th><th>档位</th><th>综合分</th><th>止损</th><th>目标</th></tr></thead><tbody>'
      + buys.map((b) => '<tr>'
          + '<td><a class="sim-jump" href="/?symbol=' + escHtml(b.symbol) + '" title="回看板分析该股"><b>' + escHtml(b.name || b.symbol) + '</b><span class="code">' + escHtml(b.symbol) + '</span></a></td>'
          + '<td>' + escHtml(LEVEL[b.level] || b.level || '--') + '</td>'
          + '<td>' + (b.score == null ? '—' : Number(b.score).toFixed(0)) + '</td>'
          + '<td>' + (b.stop ? Number(b.stop).toFixed(2) : '—') + '</td>'
          + '<td>' + (b.target ? Number(b.target).toFixed(2) : '—') + '</td>'
          + '</tr>').join('')
      + '</tbody></table>';
  }
  if (sells.length) {
    html += (html ? '<div style="height:8px"></div>' : '')
      + '<div class="eval-notice-line">明日信号卖出 ' + sells.length + ' 只（止损/止盈/超期仍盘中实时）</div>'
      + '<table class="sim-table"><thead><tr><th>代码 / 名称</th><th>信号日</th><th>策略</th></tr></thead><tbody>'
      + sells.map((t) => '<tr>'
          + '<td><a class="sim-jump" href="/?symbol=' + escHtml(t.symbol) + '" title="回看板分析该股">' + escHtml(t.name || t.symbol) + '<span class="code">' + escHtml(t.symbol) + '</span></a></td>'
          + '<td class="sim-sub">' + escHtml(String(t.signal_date || '').slice(0, 10) || '—') + '</td>'
          + '<td class="sim-sub">' + escHtml(t.strategy || '—') + '</td>'
          + '</tr>').join('')
      + '</tbody></table>';
  }
  el.innerHTML = html;
}
// ---------------------------------------------------------------- 状态行与自动交易胶囊

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
  if (s.next_run_at) {
    bits.push('下次巡检 ' + String(s.next_run_at).slice(5, 16));
  } else if (cfg.enabled && s.next_run_reason && s.status !== 'waiting_market') {
    bits.push(escHtml(s.next_run_reason));
  }
  if (s.last_bought) bits.push('本轮买 ' + s.last_bought);
  if (s.last_sold) bits.push('本轮卖 ' + s.last_sold);
  if (s.last_unfilled) bits.push('放弃 ' + s.last_unfilled);
  if (s.last_equity) bits.push('净值 ' + _fmtMoney(s.last_equity));
  if (s.rounds) bits.push('累计 ' + s.rounds + ' 轮');
  const plans = data.queues || {};
  if ((data.signal_mode || 'close_nextday') !== 'intraday') {
    if ((plans.buys || []).length) bits.push('明日买单 ' + plans.buys.length);
    if ((plans.sells || []).length) bits.push('明日卖单 ' + plans.sells.length);
  }
  if (s.screen_deferred) bits.push('⏸ ' + escHtml(s.screen_deferred));
  if (s.source_throttled) bits.push('⚠ 行情源限流，上轮选股提前终止');
  let text = bits.join(' · ');
  if (s.last_error) text += '　⚠ ' + escHtml(s.last_error);
  el.innerHTML = text;
}

function _renderAutoPill(data) {
  const pill = document.getElementById('sim-auto-pill');
  const textEl = document.getElementById('sim-auto-pill-text');
  const btn = document.getElementById('sim-auto-toggle-btn');
  if (!pill || !textEl || !btn) return;
  const enabled = !!((data.config || {}).enabled);
  pill.classList.toggle('sim-pill-on', enabled);
  pill.classList.toggle('sim-pill-paused', !enabled);
  textEl.textContent = enabled ? '自动交易已开启' : '自动交易已暂停';
  btn.textContent = enabled ? '暂停' : '恢复';
}

// ---------------------------------------------------------------- 交互

export async function loadSimPanel() {
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`);
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data || data.ok === false) return;
    _simData = data;
    _renderAutoPill(data);
    _renderOverview(data);
    _renderMetrics(data);
    _renderPositions(data);
    _renderTrades(data);
    _renderPlans(data);
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
    // 策略与基准（v8）：策略下拉由注册表枚举驱动；基准属账户/引擎参数
    strategy: val('sim-strategy') || 'qushi_v5',
    benchmark: val('sim-benchmark') || '000300',
    signal_mode: val('sim-signal-mode') || 'auto',
    auto_sell: !!((document.getElementById('sim-auto_sell') || {}).checked),
    stop_loss_enabled: !!((document.getElementById('sim-stop_loss_enabled') || {}).checked),
    take_profit_enabled: !!((document.getElementById('sim-take_profit_enabled') || {}).checked),
    // 钉钉推送（sim-notify）：enabled/app_key/app_secret/robot_code/open_conversation_id/ops
    notify: {
      enabled: !!((document.getElementById('sim-notify-enabled') || {}).checked),
      app_key: (document.getElementById('sim-notify-app-key') || {}).value || '',
      app_secret: (document.getElementById('sim-notify-app-secret') || {}).value || '',
      robot_code: (document.getElementById('sim-notify-robot-code') || {}).value || '',
      open_conversation_id: (document.getElementById('sim-notify-conversation-id') || {}).value || '',
      ops: ['buy', 'sell'].filter((op) =>
        !!((document.getElementById('sim-notify-' + op) || {}).checked)),
    },
    // 策略参数由 schema 驱动动态读取（v7 解耦）
    strategy_params: _readStrategyParams(),
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
    if (data.ok) _clearDirty();   // 保存成功后恢复自动刷新表单同步
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

export async function simToggleAuto() {
  const cfg = (_simData || {}).config || {};
  const next = !cfg.enabled;
  try {
    const resp = await fetchWithTimeout(`${API}/api/sim`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'save', enabled: next }),
    });
    const data = await resp.json();
    showToastMsg(data.ok
      ? (next ? '自动交易已开启：交易时段内按策略自动选股与买卖（无人工确认环节）' : '自动交易已暂停：不再自动巡检与下单')
      : (data.error || '操作失败'));
  } catch (e) {
    showToastMsg('请求失败，请稍后再试');
  }
  loadSimPanel();
}

// ---------------------------------------------------------------- 配置变更确认与脏状态（v8 迭代）

function _markDirty() {
  if (_cfgDirty) return;
  _cfgDirty = true;
  const card = document.getElementById('sim-config-card');
  const badge = document.getElementById('sim-dirty-badge');
  if (card) card.classList.add('cfg-dirty');
  if (badge) badge.hidden = false;
  _setConfigCollapsed(false);   // 脏状态下自动展开，保证提示与表单可见
}

function _clearDirty() {
  _cfgDirty = false;
  const card = document.getElementById('sim-config-card');
  const badge = document.getElementById('sim-dirty-badge');
  if (card) card.classList.remove('cfg-dirty');
  if (badge) badge.hidden = true;
}

export function simToggleConfig() {
  const body = document.getElementById('sim-config-body');
  if (!body) return;
  _setConfigCollapsed(!body.hidden);
}

function _setConfigCollapsed(collapsed) {
  const body = document.getElementById('sim-config-body');
  const caret = document.getElementById('sim-config-caret');
  if (body) body.hidden = !!collapsed;
  if (caret) caret.textContent = collapsed ? '▸' : '▾';
}

function _bindConfigGuards() {
  const strategyEl = document.getElementById('sim-strategy');
  if (strategyEl && !strategyEl.dataset.simGuard) {
    strategyEl.dataset.simGuard = '1';
    strategyEl.addEventListener('change', () => {
      const cfg = (_simData || {}).config || {};
      if (!cfg.strategy || strategyEl.value === cfg.strategy) return;
      const positions = (((_simData || {}).account || {}).position_count) || 0;
      // 带持仓切换（Q1/Q4）：现有持仓由新策略接管卖出信号，止损/止盈/超期仍按建仓价位生效
      if (positions > 0 && !window.confirm(
        `切换策略后，现有 ${positions} 个持仓将由新策略接管卖出信号，止损/止盈/超期卖出仍按建仓时价位生效。确定切换？`)) {
        strategyEl.value = cfg.strategy;
        return;
      }
      // 切换即重渲染：按新策略 schema 以默认值渲染参数区（保存时服务端仍会归一化，双保险）
      const opt = _strategyOptions.find((o) => o.id === strategyEl.value);
      if (opt && opt.params_schema) _renderStrategyParamsFor(opt.params_schema, {});
    });
  }
  const benchEl = document.getElementById('sim-benchmark');
  if (benchEl && !benchEl.dataset.simGuard) {
    benchEl.dataset.simGuard = '1';
    benchEl.addEventListener('change', () => {
      const cfg = (_simData || {}).config || {};
      if ((cfg.benchmark || '000300') === benchEl.value) return;
      if (!window.confirm('切换基准后，超额收益将从新基准重新起算，历史基准不参与；组合自身指标不受影响。确定切换？')) {
        benchEl.value = cfg.benchmark || '000300';
      }
    });
  }
  // 脏状态跟踪：配置区任何用户编辑（input/change）都标记未保存
  const cfgBody = document.getElementById('sim-config-body');
  if (cfgBody && !cfgBody.dataset.simDirty) {
    cfgBody.dataset.simDirty = '1';
    cfgBody.addEventListener('input', _markDirty, true);
    cfgBody.addEventListener('change', _markDirty, true);
  }
}
_bindConfigGuards();

// 有未保存修改时离开页面前提醒（_cfgDirty 仅会在 sim.html 的配置区编辑后为真）
window.addEventListener('beforeunload', (e) => {
  if (_cfgDirty) { e.preventDefault(); e.returnValue = ''; }
});

export function onSimResize() {
  if (_equityChart) { try { _equityChart.resize(); } catch (e) {} }
}

/* 趋势分析看板主逻辑 —— 自 index.html 拆分（frontend-ux-v42 P0） */
const API = '';
let currentSymbol = '';
let currentView = 'dayk';
let _mode = 'pro';  // 'pro' or 'simple'
let klineChart, volumeChart, flowChart, minuteChart, minuteVolChart, indicatorChart;
let _klineData = [];
let _zoomBound = false;
let _refreshTimer = null;
let _minuteData = null;      // 缓存分时数据
let _minuteChanlun = null;   // 缓存缠论分时分析结果
let _minuteYRange = null;    // 固定Y轴范围
let _dailyChanlun = null;    // 缓存缠论日线分析结果
let _flowMode = 'realtime';  // 资金流模式：'realtime'=今日实时, 'daily'=近30日
let _dailyFlows = null;      // 缓存日级资金流数据（analyze返回）
let _signalLines = [];       // K线水平线信息（止损/入场/目标），供鼠标悬浮解释
let _signalPoints = [];     // K线标记点信息（买卖信号），供鼠标悬浮解释
let _currentIndicator = 'none';  // 当前技术指标
let _lastSignalData = null;      // 保存上次的signal数据（用于BOLL清除时重渲染）
let _lastSignal = {};        // 记录各股票的上次信号 { code: { action, score, time } }
const STORAGE_SIGNALS = 'qs_signal_records';  // 信号记录存储key
const MAX_SIGNAL_RECORDS = 200;  // 最多记录200条信号

// ==================== FX 动效系统（frontend-ux-v42 R3） ====================
// 档位：off=关 / std=标准 / max=炫酷 / auto=自动判定（默认）
// 护栏：仅 transform/opacity 动画；图表动画仅首帧；关档 JS 跳过调度；reduced-motion 强制关
let _fxSetting = 'auto';
let _fxLevel = 'std';
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
  _fxLevel = _fxReducedMotion() ? 'off' : (s === 'off' || s === 'std' || s === 'max' ? s : _fxResolveAuto());
  document.body.classList.remove('fx-off', 'fx-std', 'fx-max');
  document.body.classList.add('fx-' + _fxLevel);
  const hint = document.getElementById('fx-hint');
  if (hint) {
    const actual = { off: '关', std: '标准', max: '炫酷' }[_fxLevel];
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
function fxEnabled() { return _fxLevel !== 'off'; }

// 图表首帧动画开关：max档且该图表未被消耗过才允许动画；不带key=用户主动重渲染（如切换指标），max档恒可动画
function chartAnim(key) {
  if (_fxLevel !== 'max') return false;
  if (key === undefined) return true;
  if (_animConsumed[key]) return false;
  _animConsumed[key] = true;
  return true;
}
function resetChartAnim() { Object.keys(_animConsumed).forEach(k => delete _animConsumed[k]); }

// ===== ECharts 全局主题色 =====
const C = {
  bg: 'transparent',
  up: '#ff2d2d',       // 涨-红
  down: '#00b35c',     // 跌-绿
  ma5: '#ffeb3b',      // MA5-黄
  ma10: '#e040fb',     // MA10-紫
  ma20: '#4fc3f7',     // MA20-蓝
  ma60: '#ff9800',     // MA60-橙
  text: '#aaa',
  textDim: '#666',
  grid: '#1a1a1a',
  axis: '#333',
  preClose: '#555',
  avgLine: '#ff9800',
};

// ===== 初始化 =====
function initCharts() {
  const opts = {
    backgroundColor: C.bg,
    textStyle: { color: C.text, fontFamily: 'Microsoft YaHei' },
    categoryAxis: {
      axisLine: { lineStyle: { color: C.axis } },
      axisLabel: { color: C.textDim, fontSize: 10 },
      splitLine: { show: false },
    },
    valueAxis: {
      axisLine: { lineStyle: { color: C.axis } },
      axisLabel: { color: C.textDim, fontSize: 10 },
      splitLine: { lineStyle: { color: C.grid } },
    },
    tooltip: { backgroundColor: 'rgba(20,20,20,0.95)', borderColor: '#333', textStyle: { color: '#ddd', fontSize: 12 } },
  };

  klineChart = echarts.init(document.getElementById('kline-chart'));
  volumeChart = echarts.init(document.getElementById('volume-chart'));
  flowChart = echarts.init(document.getElementById('flow-chart'));
  minuteChart = echarts.init(document.getElementById('minute-chart'));
  minuteVolChart = echarts.init(document.getElementById('minute-vol'));
  indicatorChart = echarts.init(document.getElementById('indicator-chart'));

  // K线/成交量/副图指标 三图联动：任一处缩放（滑块/滚轮/框选）自动同步到其余两图
  echarts.connect([klineChart, volumeChart, indicatorChart]);

  klineChart.setOption(opts);
  volumeChart.setOption(opts);
  flowChart.setOption(opts);
  minuteChart.setOption(opts);
  minuteVolChart.setOption(opts);
  indicatorChart.setOption(opts);

  bindBoxZoom();   // K线区拖拽框选X轴范围

  window.addEventListener('resize', () => {
    klineChart.resize(); volumeChart.resize(); flowChart.resize();
    minuteChart.resize(); minuteVolChart.resize();
    indicatorChart.resize();
  });

  // 视图切换
  document.getElementById('view-dayk').onclick = () => switchView('dayk');
  document.getElementById('view-week').onclick = () => switchView('week');
  document.getElementById('view-minute').onclick = () => switchView('minute');

  // 时间范围
  document.querySelectorAll('.tb-btn[data-range]').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('.tb-btn[data-range]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyRange(parseInt(btn.dataset.range));
    };
  });

  bindChartTooltip();
}

function switchView(view) {
  const prevView = currentView;
  currentView = view;
  const dk = document.getElementById('view-dayk');
  const wk = document.getElementById('view-week');
  const mn = document.getElementById('view-minute');
  const kc = document.getElementById('kline-chart');
  const vc = document.getElementById('volume-chart');
  const mc = document.getElementById('minute-chart');
  const mv = document.getElementById('minute-vol');
  const sep = document.getElementById('sep-range');

  dk.classList.remove('active');
  wk.classList.remove('active');
  mn.classList.remove('active');

  if (view === 'minute') {
    mn.classList.add('active');
    kc.style.display = 'none'; vc.style.display = 'none';
    mc.style.display = 'block'; mv.style.display = 'block';
    sep.style.display = 'none';
    document.querySelectorAll('.tb-btn[data-range]').forEach(b => b.style.display = 'none');
    document.getElementById('zoom-info').style.display = 'none';
    document.getElementById('chanlun-daily-card').style.display = 'none';
    document.getElementById('indicator-toolbar').style.display = 'none';
    document.getElementById('indicator-chart').style.display = 'none';
    // 关键：容器从hidden切到visible后必须resize，否则ECharts画不出来
    setTimeout(() => {
      minuteChart.resize();
      minuteVolChart.resize();
      if (currentSymbol) loadMinute(currentSymbol);
    }, 50);
  } else {
    // dayk 或 week
    if (view === 'week') wk.classList.add('active'); else dk.classList.add('active');
    mc.style.display = 'none'; mv.style.display = 'none';
    kc.style.display = 'block'; vc.style.display = 'block';
    sep.style.display = '';
    document.querySelectorAll('.tb-btn[data-range]').forEach(b => b.style.display = '');
    document.getElementById('zoom-info').style.display = '';
    // 指标副图工具栏显示（如果有指标）
    if (_currentIndicator !== 'none') {
      document.getElementById('indicator-toolbar').style.display = 'flex';
      document.getElementById('indicator-chart').style.display = 'block';
      setTimeout(() => indicatorChart.resize(), 50);
    }
    // 切到日K/周K视图时隐藏分时缠论面板
    document.getElementById('chanlun-card').style.display = 'none';
    // 日K/周K视图显示缠论面板（如果有数据）
    if (_dailyChanlun && !_dailyChanlun.error) {
      document.getElementById('chanlun-daily-card').style.display = 'block';
    }
    setTimeout(() => {
      klineChart.resize();
      volumeChart.resize();
    }, 50);
    // 日K↔周K切换时重新分析
    if (prevView !== view && currentSymbol) {
      analyze(currentSymbol);
    }
  }
  // FX标准/炫酷档：视图切换 crossfade（仅 opacity）
  if (fxEnabled()) {
    const vis = view === 'minute' ? [mc, mv] : [kc, vc];
    vis.forEach(el => { el.style.transition = 'opacity .15s'; el.style.opacity = '0'; });
    requestAnimationFrame(() => requestAnimationFrame(() => vis.forEach(el => { el.style.opacity = '1'; })));
  }
}

// ===== MA计算 =====
function calcMA(data, period) {
  const result = new Array(data.length).fill(null);
  for (let i = period - 1; i < data.length; i++) {
    let sum = 0;
    for (let j = 0; j < period; j++) sum += data[i - j].close;
    result[i] = +(sum / period).toFixed(3);
  }
  return result;
}

// ===== K线图 =====
function renderKline(klines, signal) {
  _klineData = klines;
  _lastSignalData = signal;
  const dates = klines.map(k => k.date);
  const ohlc = klines.map(k => [k.open, k.close, k.low, k.high]);
  const ma5 = calcMA(klines, 5);
  const ma10 = calcMA(klines, 10);
  const ma20 = calcMA(klines, 20);
  const ma60 = calcMA(klines, 60);

  // 买卖点标记——买入▲在K线下方，卖出▼在K线上方
  const markPoints = [];
  _signalPoints = [];
  if (signal.breakouts) {
    for (const b of signal.breakouts) {
      if (b.signal === '买入') {
        const idx = findEntryIndex(klines, b.entry_price);
        const k = klines[idx];
        // 向下偏移，让标记明显在K线下方
        const candleLow = (k && k.low) ? k.low : (b.entry_price || b.breakout_price);
        const range = k ? (k.high - k.low) : 0;
        const markerY = candleLow - Math.max(range * 0.5, candleLow * 0.005);
        const dateStr = dates[idx] || dates[dates.length-1];
        markPoints.push({
          coord: [dateStr, markerY],
          symbol: 'triangle', symbolSize: 20, symbolRotate: 0,
          itemStyle: { color: C.up, borderWidth: 2, borderColor: '#fff' },
          label: { show: true, formatter: '买入', fontSize: 11, fontWeight: 'bold', color: '#fff',
                   backgroundColor: C.up, padding: [2,4], borderRadius: 3, position: 'bottom' },
        });
        _signalPoints.push({
          date: dateStr, price: markerY,
          title: '买入信号（海龟法则·做多）',
          formula: `突破${b.system || '20日'}最高点 ${b.breakout_price || b.channel_high}\n→ 入场 ${b.entry_price}，止损 ${b.stop_loss}（入场-2×N）`,
          desc: `唐奇安通道：股价突破过去N天最高点时触发买入信号。\nN值=${b.current_n || '?'}（ATR，反映日均波动幅度）。\n入场后止损价=入场价-2×N，跌破止损或触及反向通道退出。`,
        });
      } else if (b.signal === '卖出') {
        const idx = dates.length - 1;
        const k = klines[idx];
        // 向上偏移，让标记明显在K线上方
        const candleHigh = (k && k.high) ? k.high : b.breakout_price;
        const range = k ? (k.high - k.low) : 0;
        const markerY = candleHigh + Math.max(range * 0.5, candleHigh * 0.005);
        markPoints.push({
          coord: [dates[idx], markerY],
          symbol: 'triangle', symbolSize: 20, symbolRotate: 180,
          itemStyle: { color: C.down, borderWidth: 2, borderColor: '#fff' },
          label: { show: true, formatter: '卖出', fontSize: 11, fontWeight: 'bold', color: '#fff',
                   backgroundColor: C.down, padding: [2,4], borderRadius: 3, position: 'top' },
        });
        _signalPoints.push({
          date: dates[idx], price: markerY,
          title: '卖出信号（海龟法则·做空）',
          formula: `跌破${b.system || '20日'}最低点 ${b.breakout_price || b.channel_low}\n→ 入场 ${b.entry_price}，止损 ${b.stop_loss}（入场+2×N）`,
          desc: `唐奇安通道：股价跌破过去N天最低点时触发做空信号。\nN值=${b.current_n || '?'}（ATR，反映日均波动幅度）。\n做空止损价=入场价+2×N，涨到止损或触及反向通道平仓。`,
        });
      }
    }
  }

  // 关键水平线（止损、目标价、支撑/压力）——加粗+背景色突出显示
  // 先判断整体方向：空头持仓时止损在入场价上方
  let isBearish = false;
  if (signal.breakouts) {
    for (const b of signal.breakouts) {
      if (b.entry_price && b.stop_loss && b.stop_loss > b.entry_price) {
        isBearish = true; break;
      }
    }
  }
  if (!isBearish && signal.action) {
    const a = String(signal.action);
    if (a.includes('卖') || a.includes('空') || a.includes('跌')) isBearish = true;
    else if (a.includes('买') || a.includes('涨')) isBearish = false;
  }

  const markLines = [];
  _signalLines = [];
  if (signal.breakouts) {
    for (const b of signal.breakouts) {
      if (b.stop_loss && b.stop_loss > 0) {
        const slLabel = isBearish
          ? `止损 ${b.stop_loss.toFixed(2)}\n涨到这里就止损`
          : `止损 ${b.stop_loss.toFixed(2)}\n跌到这里就卖`;
        markLines.push({ yAxis: b.stop_loss, lineStyle: { color: C.down, type: 'dashed', width: 2 },
          label: { formatter: slLabel, color: '#fff', fontSize: 11, fontWeight: 'bold',
            backgroundColor: C.down, padding: [3,6], borderRadius: 3, position: 'insideStartTop' } });
        const nVal = b.current_n || '?';
        _signalLines.push({
          value: b.stop_loss,
          title: isBearish ? '止损价（做空）' : '止损价（做多）',
          formula: isBearish
            ? `${b.entry_price} + 2 × ${nVal} = ${b.stop_loss}`
            : `${b.entry_price} - 2 × ${nVal} = ${b.stop_loss}`,
          desc: isBearish
            ? `海龟法则2N止损。N=${nVal}（ATR平均真实波幅，反映股票日均波动幅度）。\n做空止损在入场价上方：如果股价反弹到这里，说明判断错了，认亏平仓。`
            : `海龟法则2N止损。N=${nVal}（ATR平均真实波幅，反映股票日均波动幅度）。\n做多止损在入场价下方：如果股价跌到这里，说明判断错了，认亏卖出。`,
        });
      }
      // 持仓也要显示入场价，方便知道成本位置
      if (b.entry_price && b.entry_price > 0 && b.signal !== '观望') {
        const entryColor = isBearish ? C.down : C.up;
        const entryText = isBearish ? `做空 ${b.entry_price.toFixed(2)}` : `入场 ${b.entry_price.toFixed(2)}`;
        markLines.push({ yAxis: b.entry_price, lineStyle: { color: entryColor, type: 'solid', width: 2 },
          label: { formatter: entryText, color: '#fff', fontSize: 11, fontWeight: 'bold',
            backgroundColor: entryColor, padding: [3,6], borderRadius: 3, position: 'insideStartTop' } });
        _signalLines.push({
          value: b.entry_price,
          title: isBearish ? '做空入场价（海龟法则）' : '做多入场价（海龟法则）',
          formula: isBearish
            ? `股价跌破${b.system || '20日'}最低点 → 做空入场 ${b.entry_price}\n当时通道下轨=${b.channel_low}，上轨=${b.channel_high}`
            : `股价突破${b.system || '20日'}最高点 → 做多入场 ${b.entry_price}\n当时通道上轨=${b.channel_high}，下轨=${b.channel_low}`,
          desc: isBearish
            ? `唐奇安通道做空：当股价跌破过去N天的最低点时，触发做空信号。\n入场价=突破时的通道下轨。N值=${b.current_n || '?'}。\n已持有对应天数，止损价在上方。`
            : `唐奇安通道做多：当股价突破过去N天的最高点时，触发做多信号。\n入场价=突破时的通道上轨。N值=${b.current_n || '?'}。\n已持有对应天数，止损价在下方。`,
        });
      }
    }
  }
  // 形态目标价
  if (signal.patterns) {
    for (const p of signal.patterns) {
      if (p.target_price && p.target_price > 0) {
        const targetLabel = isBearish
          ? `目标 ${p.target_price.toFixed(2)}\n跌到这里就止盈`
          : `目标 ${p.target_price.toFixed(2)}\n涨到这里就卖`;
        markLines.push({ yAxis: p.target_price, lineStyle: { color: '#ff9800', type: 'dashed', width: 2 },
          label: { formatter: targetLabel, color: '#fff', fontSize: 11, fontWeight: 'bold',
            backgroundColor: '#ff9800', padding: [3,6], borderRadius: 3, position: 'insideStartTop' } });
        _signalLines.push({
          value: p.target_price,
          title: `${p.name} 目标价`,
          formula: p.description || '',
          desc: isBearish
            ? `${p.name}是经典看跌形态。跌破颈线后，预计再跌一个头部高度的幅度。\n跌到目标价就止盈平仓。置信度${p.confidence || '?'}%。`
            : `${p.name}是经典看涨形态。突破颈线后，预计再涨一个头部高度的幅度。\n涨到目标价就止盈卖出。置信度${p.confidence || '?'}%。`,
        });
      }
    }
  }

  const total = dates.length;
  const defaultDays = Math.min(60, total);
  const ds = total > defaultDays ? (1 - defaultDays / total) * 100 : 0;

  klineChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim('kline'),
    xAxis: {
      type: 'category', data: dates,
      axisLine: { lineStyle: { color: C.axis } },
      axisLabel: { color: C.textDim, fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      scale: true,
      axisLine: { show: false },
      axisLabel: { color: C.textDim, fontSize: 11 },
      splitLine: { lineStyle: { color: C.grid } },
    },
    grid: { left: 60, right: 50, top: 30, bottom: 50 },
    legend: {
      data: ['MA5','MA10','MA20','MA60'],
      top: 4, left: 60,
      textStyle: { color: C.textDim, fontSize: 10 },
      itemWidth: 14, itemHeight: 2,
    },
    series: [
      {
        name: 'K线', type: 'candlestick', data: ohlc,
        itemStyle: { color: C.up, color0: C.down, borderColor: C.up, borderColor0: C.down },
        markPoint: markPoints.length ? { data: markPoints, animation: false } : undefined,
        markLine: markLines.length ? { silent: true, animation: false, data: markLines, symbol: 'none' } : undefined,
      },
      { name: 'MA5', type: 'line', data: ma5, symbol: 'none', lineStyle: { color: C.ma5, width: 1 } },
      { name: 'MA10', type: 'line', data: ma10, symbol: 'none', lineStyle: { color: C.ma10, width: 1 } },
      { name: 'MA20', type: 'line', data: ma20, symbol: 'none', lineStyle: { color: C.ma20, width: 1 } },
      { name: 'MA60', type: 'line', data: ma60, symbol: 'none', lineStyle: { color: C.ma60, width: 1 } },
    ],
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: '#666' } },
      formatter: (params) => {
        if (!params || !params.length) return '';
        const idx = params[0].dataIndex;
        const k = klines[idx];
        if (!k) return '';
        const isUp = k.close >= k.open;
        const c = isUp ? 'up' : 'down';
        let html = `<div style="font-size:12px;line-height:1.6">
          <div style="color:${C.textDim}">${k.date}</div>
          <div>开 <span class="${c}" style="font-weight:bold">${k.open.toFixed(2)}</span>
               高 <span class="up" style="font-weight:bold">${k.high.toFixed(2)}</span></div>
          <div>收 <span class="${c}" style="font-weight:bold">${k.close.toFixed(2)}</span>
               低 <span class="down" style="font-weight:bold">${k.low.toFixed(2)}</span></div>`;
        if (k.pct) html += `<div style="color:${k.pct>=0?C.up:C.down}">${k.pct>=0?'+':''}${k.pct.toFixed(2)}%</div>`;
        html += `<div style="color:${C.textDim}">量 ${fmtVol(k.volume)}`;
        if (k.amount > 0) html += ` 额 ${fmtVol(k.amount)}`;
        if (k.turnover > 0) html += ` 换手 ${k.turnover.toFixed(1)}%`;
        html += '</div>';
        // MA值
        for (const p of params) {
          if (p.seriesName && p.seriesName.startsWith('MA') && p.value != null) {
            html += `<div style="color:${p.color}">${p.seriesName} ${p.value.toFixed(2)}</div>`;
          }
        }
        html += '</div>';
        return html;
      }
    },
    dataZoom: [
      { type: 'inside', start: ds, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: false, moveOnMouseWheel: false },
      { type: 'slider', start: ds, end: 100, height: 28, bottom: 8,
        borderColor: '#222', backgroundColor: '#0a0a0a',
        fillerColor: 'rgba(255,152,0,0.1)',
        selectedDataBackground: { lineStyle: { color: C.ma60 }, areaStyle: { color: 'rgba(255,152,0,0.15)' } },
        dataBackground: { lineStyle: { color: '#222' }, areaStyle: { color: '#111' } },
        handleStyle: { color: '#ff9800', borderColor: '#ff9800' },
        moveHandleStyle: { color: '#ff9800' },
        textStyle: { color: C.textDim, fontSize: 10 },
        brushSelect: false,
      },
    ],
  }, true);

  // 成交量
  volumeChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim('volume'),
    xAxis: { type: 'category', data: dates, show: false },
    yAxis: { type: 'value', axisLabel: { color: C.textDim, fontSize: 9 }, splitLine: { lineStyle: { color: C.grid } } },
    grid: { left: 60, right: 50, top: 5, bottom: 5 },
    series: [{
      type: 'bar', data: klines.map(k => ({
        value: k.volume,
        itemStyle: { color: k.close >= k.open ? C.up + '88' : C.down + '88' }
      })),
    }],
    tooltip: { trigger: 'axis', formatter: p => p[0] ? `<div style="font-size:11px">${p[0].axisValue}<br/>量 ${fmtVol(p[0].value)}</div>` : '' },
    dataZoom: [
      { type: 'inside', start: ds, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: false },
      { type: 'slider', start: ds, end: 100, show: false },
    ],
  }, true);

  bindZoomSync();
  updateZoomInfo(ds, 100);
  renderIndicator(_currentIndicator);
}

// ===== 框选缩放：K线区直接拖拽划定X轴范围（松手生效，双击复位） =====
let _boxBound = false;
let _boxSel = null;   // {x0, el, moved}
function bindBoxZoom() {
  if (_boxBound) return;
  _boxBound = true;
  const dom = document.getElementById('kline-chart');
  dom.title = '拖拽框选放大 · 滚轮缩放 · 双击复位';
  const zr = klineChart.getZr();

  zr.on('mousedown', e => {
    if (!e.event || e.event.button !== 0) return;   // 仅左键
    const r = dom.getBoundingClientRect();
    const sliderTop = r.height - 36 - 8;   // 底部滑块(高28+bottom8)区域不参与框选
    if (e.offsetY > sliderTop || e.offsetY < 4) return;
    const rect = document.createElement('div');
    rect.className = 'zoom-box';
    rect.style.left = e.offsetX + 'px';
    rect.style.top = '4px';
    rect.style.height = (sliderTop - 8) + 'px';
    rect.style.width = '0px';
    dom.appendChild(rect);
    _boxSel = { x0: e.offsetX, el: rect, moved: false };
  });

  zr.on('mousemove', e => {
    if (!_boxSel) return;
    const w = Math.abs(e.offsetX - _boxSel.x0);
    if (w > 6) _boxSel.moved = true;
    _boxSel.el.style.left = Math.min(e.offsetX, _boxSel.x0) + 'px';
    _boxSel.el.style.width = w + 'px';
  });

  zr.on('mouseup', e => {
    if (!_boxSel) return;
    const bs = _boxSel; _boxSel = null;
    if (bs.el && bs.el.parentNode) bs.el.remove();
    if (!bs.moved) return;   // 单击不算框选（保留十字光标/tooltip）
    const total = _klineData.length;
    if (!total) return;
    let i1, i2;
    try {
      i1 = Math.round(klineChart.convertFromPixel({ xAxisIndex: 0 }, Math.min(bs.x0, e.offsetX)));
      i2 = Math.round(klineChart.convertFromPixel({ xAxisIndex: 0 }, Math.max(bs.x0, e.offsetX)));
    } catch (err) { return; }
    if (i1 == null || i2 == null || isNaN(i1) || isNaN(i2)) return;
    i1 = Math.max(0, Math.min(total - 1, i1));
    i2 = Math.max(0, Math.min(total - 1, i2));
    if (i2 - i1 < 1) { i1 = Math.max(0, i1 - 1); i2 = Math.min(total - 1, i2 + 1); }   // 选太窄时前后各扩一根
    klineChart.dispatchAction({ type: 'dataZoom', startValue: i1, endValue: i2 });   // connect 自动带到量/副图
  });

  zr.on('globalout', () => { if (_boxSel) { _boxSel.el.remove(); _boxSel = null; } });
  dom.addEventListener('dblclick', () => applyRange(0));   // 双击复位全部
}

function findEntryIndex(klines, entryPrice) {
  if (!entryPrice) return klines.length - 1;
  let best = 0, bestDiff = Infinity;
  for (let i = 0; i < klines.length; i++) {
    const d = Math.abs(klines[i].close - entryPrice);
    if (d < bestDiff) { bestDiff = d; best = i; }
  }
  return best;
}

// ===== 缩放联动 =====
// 三图窗口联动已由 echarts.connect 托管；这里只负责 zoom-info 文本与预设按钮高亮。
// 注意：不能读 option.dataZoom[0]——拖滑块时滚轮组件状态是过期的，必须优先取事件负载。
function bindZoomSync() {
  if (_zoomBound) return;
  _zoomBound = true;
  klineChart.on('datazoom', evt => {
    let s = null, e = null;
    const b = evt && evt.batch;
    if (b && b.length) { s = b[0].start; e = b[0].end; }
    else if (evt && typeof evt.start === 'number') { s = evt.start; e = evt.end; }
    if (s == null || e == null) {
      const dz = klineChart.getOption().dataZoom || [];
      const c = dz[dz.length - 1] || {};
      s = c.start; e = c.end;
    }
    if (s != null && e != null) {
      updateZoomInfo(s, e);
      syncRangeBtns(s, e);
    }
  });
}

function applyRange(days) {
  const total = _klineData.length;
  if (!total) return;
  let s, e;
  if (days === 0 || days >= total) { s = 0; e = 100; }
  else { s = Math.max(0, (1 - days / total) * 100); e = 100; }
  klineChart.dispatchAction({ type: 'dataZoom', start: s, end: e });   // connect 自动带到量/副图
  updateZoomInfo(s, e);
}

// ===== 鼠标悬浮信号线/标记点 → 显示计算说明 =====
function bindChartTooltip() {
  let _tooltipBound = false;
  if (_tooltipBound) return;
  _tooltipBound = true;

  const tooltipEl = document.getElementById('signal-tooltip');
  const chartDom = document.getElementById('kline-chart');

  // 用ZRender的mousemove获取像素坐标，再转换为数据值，检测是否靠近信号线/标记点
  klineChart.getZr().on('mousemove', function(e) {
    if (!_signalLines.length && !_signalPoints.length) {
      tooltipEl.style.display = 'none';
      return;
    }

    // 转换像素Y坐标 → Y轴数据值
    let yVal;
    try {
      yVal = klineChart.convertFromPixel({ yAxisIndex: 0 }, e.offsetY);
    } catch(err) {
      tooltipEl.style.display = 'none';
      return;
    }
    if (yVal == null || isNaN(yVal)) {
      tooltipEl.style.display = 'none';
      return;
    }

    // 1. 检测是否靠近水平线（止损/入场/目标）
    let found = null;
    for (const line of _signalLines) {
      if (line.value > 0 && Math.abs(yVal - line.value) / line.value < 0.012) {
        found = line;
        break;
      }
    }

    // 2. 没找到水平线 → 检测是否靠近标记点（买卖信号）
    if (!found && _signalPoints.length) {
      let xIdx;
      try {
        xIdx = Math.round(klineChart.convertFromPixel({ xAxisIndex: 0 }, e.offsetX));
      } catch(err) {}

      if (xIdx != null && !isNaN(xIdx)) {
        for (const pt of _signalPoints) {
          const idx = _klineData.findIndex(k => k.date === pt.date);
          if (idx >= 0 && Math.abs(idx - xIdx) <= 1 && pt.price > 0 && Math.abs(yVal - pt.price) / pt.price < 0.02) {
            found = pt;
            break;
          }
        }
      }
    }

    if (found) {
      tooltipEl.innerHTML =
        '<div class="stt-hint">信号说明（鼠标移开自动隐藏）</div>' +
        '<div class="stt-title">' + found.title + '</div>' +
        (found.formula ? '<div class="stt-formula">' + found.formula + '</div>' : '') +
        (found.desc ? '<div class="stt-desc">' + found.desc + '</div>' : '');
      tooltipEl.style.display = 'block';
    } else {
      tooltipEl.style.display = 'none';
    }
  });

  klineChart.getZr().on('mouseout', function() {
    tooltipEl.style.display = 'none';
  });
}

function updateZoomInfo(start, end) {
  const total = _klineData.length;
  if (!total) return;
  const si = Math.floor(start / 100 * total);
  const ei = Math.min(total - 1, Math.floor(end / 100 * total));
  const sd = _klineData[si]?.date || '';
  const ed = _klineData[ei]?.date || '';
  document.getElementById('zoom-info').textContent = `${sd} ~ ${ed} (${ei - si + 1}根)`;
}

function syncRangeBtns(start, end) {
  const total = _klineData.length;
  if (!total) return;
  const days = Math.round((end - start) / 100 * total);
  document.querySelectorAll('.tb-btn[data-range]').forEach(b => {
    const r = parseInt(b.dataset.range);
    if (r === 0) b.classList.toggle('active', start === 0 && end === 100);
    else b.classList.toggle('active', Math.abs(days - r) < 5 && end > 99);
  });
}

// ===== 缠论分时分析面板 =====
function renderChanlun(data) {
  const card = document.getElementById('chanlun-card');
  const el = document.getElementById('chanlun-body');
  if (!data || data.error) {
    card.style.display = 'none';
    return;
  }
  card.style.display = 'block';

  const signals = data.signals || [];
  const stateText = data.current_state || '';
  const summary = data.summary || '';

  let html = '';

  // 白话总结（小白模式显示）
  let plainSummary = '';
  if (signals.length > 0) {
    const lastSig = signals[0];
    plainSummary = `缠论分时：${lastSig.type_name}信号，${lastSig.description}`;
  } else if (data.kline_count < 5) {
    plainSummary = '缠论分时：数据不足，5分钟K线少于5根，无法分析';
  } else {
    plainSummary = '缠论分时：暂无买卖信号，等待背驰形成';
  }
  html += `<div class="cl-plain-summary"><b>白话总结：</b>${plainSummary}</div>`;

  // 状态描述
  if (stateText) {
    html += `<div class="cl-state">${stateText}</div>`;
  }

  // 信号列表
  if (signals.length > 0) {
    html += '<div class="cl-signals">';
    for (const sig of signals) {
      const isBuy = sig.type.startsWith('buy');
      const cls = isBuy ? 'cl-signal-buy' : 'cl-signal-sell';
      const tagCls = sig.type;
      html += `<div class="cl-signal-item ${cls}">
        <span class="cl-signal-tag ${tagCls}">${sig.type_name}</span>
        <span class="cl-signal-desc">${sig.description}</span>
        <span class="cl-signal-time">${sig.time}</span>
      </div>`;
    }
    html += '</div>';
  }

  // 统计
  html += `<div class="cl-stats">
    <span>5分K线: <b style="color:#ddd">${data.kline_count}</b></span>
    <span>分型: <b style="color:#ddd">${data.fractal_count}</b></span>
    <span>笔: <b style="color:#ddd">${data.stroke_count}</b></span>
    <span>信号: <b style="color:#ddd">${signals.length}</b></span>
  </div>`;

  el.innerHTML = html;
}

// ===== 缠论日线分析面板 =====
function renderChanlunDaily(data) {
  const card = document.getElementById('chanlun-daily-card');
  const el = document.getElementById('chanlun-daily-body');
  if (!data || data.error) {
    card.style.display = 'none';
    return;
  }
  card.style.display = 'block';

  const signals = data.signals || [];
  const zhongshus = data.zhongshus || [];
  const stateText = data.current_state || '';
  const currentPrice = data.current_price || (_klineData.length ? _klineData[_klineData.length - 1].close : null);

  let html = '';

  // 白话总结（小白模式显示）
  let plainSummary = '';
  const zsLabel = currentView === 'week' ? '缠论周线' : '缠论日线';
  if (signals.length > 0) {
    const lastSig = signals[0];
    plainSummary = `${zsLabel}：${lastSig.type_name}信号，${lastSig.description}`;
  } else {
    plainSummary = `${zsLabel}：暂无买卖信号，等待背驰/中枢突破`;
  }
  if (currentPrice != null && zhongshus && zhongshus.length > 0) {
    const lastZs = zhongshus[zhongshus.length - 1];
    const zd = parseFloat(lastZs.zd), zg = parseFloat(lastZs.zg);
    if (!isNaN(zd) && !isNaN(zg)) {
      if (currentPrice >= zd && currentPrice <= zg) {
        plainSummary += `；当前在中枢[${zd}-${zg}]内震荡`;
      } else if (currentPrice > zg) {
        plainSummary += `；已突破中枢上沿${zg}`;
      } else {
        plainSummary += `；已跌破中枢下沿${zd}`;
      }
    }
  }
  html += `<div class="cl-plain-summary"><b>白话总结：</b>${plainSummary}</div>`;

  if (stateText) {
    html += `<div class="cl-state" style="border-left-color:#7F77DD">${stateText}</div>`;
  }

  // 信号列表
  if (signals.length > 0) {
    html += '<div class="cl-signals">';
    for (const sig of signals) {
      const isBuy = sig.type.startsWith('buy');
      const cls = isBuy ? 'cl-signal-buy' : 'cl-signal-sell';
      const tagCls = sig.type;
      html += `<div class="cl-signal-item ${cls}">
        <span class="cl-signal-tag ${tagCls}">${sig.type_name}</span>
        <span class="cl-signal-desc">${sig.description}</span>
        <span class="cl-signal-time">${sig.date}</span>
      </div>`;
    }
    html += '</div>';
  } else {
    html += '<div style="color:#555;font-size:12px;padding:6px 0">暂无买卖信号，等待背驰/中枢突破</div>';
  }

  // 当前价格与所处中枢判断
  let currentZs = null;
  if (currentPrice != null && zhongshus.length > 0) {
    for (const zs of zhongshus) {
      const zd = parseFloat(zs.zd);
      const zg = parseFloat(zs.zg);
      if (!isNaN(zd) && !isNaN(zg) && currentPrice >= zd && currentPrice <= zg) {
        currentZs = zs; break;
      }
    }
  }

  // 中枢信息
  if (zhongshus.length > 0) {
    html += '<div style="margin-top:6px;font-size:11px;color:#666;border-top:1px solid #0d0d0d;padding-top:4px">中枢列表</div>';
    if (currentZs) {
      html += `<div style="margin:4px 0;padding:5px 8px;background:#1a1200;border:1px solid #ff980044;border-radius:4px;font-size:11px;color:#ff9800">
        当前价格 <b>${currentPrice.toFixed(2)}</b> 处于中枢 <b>[${currentZs.zd} - ${currentZs.zg}]</b> 内，区间上沿压力 ${currentZs.zg}，下沿支撑 ${currentZs.zd}
      </div>`;
    } else if (currentPrice != null) {
      const lastZs = zhongshus[zhongshus.length - 1];
      const zd = parseFloat(lastZs.zd), zg = parseFloat(lastZs.zg);
      const pos = currentPrice > zg ? `上沿 ${zg} 之上` : `下沿 ${zd} 之下`;
      const hint = currentPrice > zg ? '已向上突破，关注回踩能否站稳' : '已跌破中枢，关注是否形成第三类卖点';
      html += `<div style="margin:4px 0;padding:5px 8px;background:#0d1a0d;border:1px solid #00b35c44;border-radius:4px;font-size:11px;color:#aaa">
        当前价格 <b>${currentPrice.toFixed(2)}</b> 位于最新中枢 [${lastZs.zd} - ${lastZs.zg}] ${pos}，${hint}
      </div>`;
    }
    for (const zs of zhongshus.slice(-4)) {
      const isCurrent = currentZs && currentZs === zs;
      const brokenCls = zs.is_broken ? 'cl-zhongshu-broken' : '';
      const currentCls = isCurrent ? 'cl-zhongshu-current' : '';
      const dirText = zs.is_broken ? (zs.break_direction === 'up' ? '向上突破' : '向下突破') : '震荡中';
      const currentTag = isCurrent ? '<span style="color:#ff9800;font-weight:bold;margin-right:4px">[当前]</span>' : '';
      html += `<div class="cl-zhongshu ${brokenCls} ${currentCls}">
        ${currentTag}[${zs.zd} - ${zs.zg}] ${zs.start_date}~${zs.end_date} ${dirText}
      </div>`;
    }
  }

  // 统计
  html += `<div class="cl-stats">
    <span>${currentView === 'week' ? '周K' : '日K'}: <b style="color:#ddd">${data.kline_count}</b></span>
    <span>合并: <b style="color:#ddd">${data.merged_count}</b></span>
    <span>分型: <b style="color:#ddd">${data.fractal_count}</b></span>
    <span>笔: <b style="color:#ddd">${data.stroke_count}</b></span>
    <span>中枢: <b style="color:#ddd">${data.zhongshu_count}</b></span>
    <span>信号: <b style="color:#ddd">${signals.length}</b></span>
  </div>`;

  el.innerHTML = html;
}

// ===== 日K线缠论图表叠加 =====
function applyChanlunDailyOverlay(data) {
  if (!data || !klineChart) return;

  const chartSignals = data.chart_signals || [];
  const chartFractals = data.chart_fractals || [];
  const chartZhongshus = data.chart_zhongshus || [];
  const chartStrokes = data.chart_strokes || [];

  // 获取当前K线series的markPoint和markLine（保留已有的买卖点标记）
  const opt = klineChart.getOption();
  const klineSeries = opt.series.find(s => s.type === 'candlestick');
  if (!klineSeries) return;

  // 合并markPoint：原有的买卖点 + 缠论买卖点 + 分型
  const existingMarkPoints = (klineSeries.markPoint && klineSeries.markPoint.data) || [];
  const clMarkPoints = [];

  // 缠论买卖点标记——买入▲放K线下方，卖出▼放K线上方
  const _chanlunExplain = {
    'buy1': '一类买点：价格创新低但MACD力度背驰（下上下三段，后段低点更低但力度更弱），认为是趋势底部。缠论中最强的买点。',
    'buy2': '二类买点：中枢形成后价格回踩不破中枢上沿。次强买点，出现在一类买点之后。',
    'buy3': '三类买点：中枢突破后价格回踩不破中枢上沿。确认性买点，趋势已确认向上。',
    'sell1': '一类卖点：价格创新高但MACD力度背驰（上下上三段，后段高点更高但力度更弱），认为是趋势顶部。缠论中最强的卖点。',
    'sell2': '二类卖点：中枢形成后价格反弹不破中枢下沿。次强卖点，出现在一类卖点之后。',
    'sell3': '三类卖点：中枢跌破后价格反弹不破中枢下沿。确认性卖点，趋势已确认向下。',
  };
  for (const sig of chartSignals) {
    const isBuy = sig.symbol === 'triangle';
    const sigDate = sig.coord[0];
    const sigPrice = sig.coord[1];
    // 在K线数据中查找对应日期的蜡烛
    const candle = _klineData.find(k => k.date === sigDate);
    let markerY = sigPrice;
    if (candle) {
      const range = candle.high - candle.low;
      const offset = Math.max(range * 0.6, sigPrice * 0.008);
      markerY = isBuy ? candle.low - offset : candle.high + offset;
    }
    clMarkPoints.push({
      coord: [sigDate, markerY],
      symbol: isBuy ? 'triangle' : 'triangle',
      symbolSize: 18,
      symbolRotate: isBuy ? 0 : 180,
      itemStyle: sig.itemStyle,
      label: {
        show: true,
        formatter: sig.label?.formatter || (isBuy ? '买' : '卖'),
        fontSize: 10,
        fontWeight: 'bold',
        color: sig.label?.color || (isBuy ? C.up : C.down),
        position: isBuy ? 'bottom' : 'top',
      },
    });
    const sigType = sig.type || (isBuy ? 'buy1' : 'sell1');
    _signalPoints.push({
      date: sigDate, price: markerY,
      title: sig.label?.formatter ? `缠论·${sig.label.formatter}` : (isBuy ? '缠论买点' : '缠论卖点'),
      formula: `信号价位 ${sigPrice}`,
      desc: _chanlunExplain[sigType] || (isBuy ? '缠论买点信号' : '缠论卖点信号'),
    });
  }

  // 分型标记
  for (const f of chartFractals) {
    clMarkPoints.push({
      coord: f.coord,
      symbol: f.symbol,
      symbolSize: f.symbolSize,
      itemStyle: f.itemStyle,
    });
  }

  // 中枢矩形用markArea
  const markAreas = chartZhongshus.map(zs => ([
    { xAxis: zs.xAxis[0], yAxis: zs.yAxis[0] },
    { xAxis: zs.xAxis[1], yAxis: zs.yAxis[1],
      itemStyle: zs.itemStyle },
  ]));

  // 笔的连线用markLine
  const strokeLines = chartStrokes.map(s => ([
    { coord: s.coords[0] },
    { coord: s.coords[1], lineStyle: s.lineStyle },
  ]));

  // 合并markLine：原有的止损/目标价 + 缠论笔
  const existingMarkLines = (klineSeries.markLine && klineSeries.markLine.data) || [];

  klineChart.setOption({
    series: [{
      name: 'K线',
      markPoint: {
        data: [...existingMarkPoints, ...clMarkPoints],
        animation: false,
      },
      markLine: {
        silent: true,
        animation: false,
        symbol: 'none',
        data: [...existingMarkLines, ...strokeLines],
      },
      markArea: markAreas.length ? {
        silent: true,
        animation: false,
        data: markAreas,
      } : undefined,
    }],
  });
}
function renderMinute(data, chanlunData) {
  const pre = data.pre_close;
  const prices = data.prices;
  const avgs = data.avg_prices;
  const times = data.times;
  const vols = data.volumes;

  // 固定Y轴范围：用昨收价±10%（或±20%），防止跳动
  // 创业板/科创板/北交所 = ±20%，其他 = ±10%
  if (!_minuteYRange || _minuteYRange.pre !== pre) {
    const is20pct = currentSymbol && (
      currentSymbol.startsWith('300') || currentSymbol.startsWith('688') ||
      currentSymbol.startsWith('920') || currentSymbol.startsWith('8')
    );
    const pct = is20pct ? 0.20 : 0.10;
    _minuteYRange = {
      pre: pre,
      min: pre * (1 - pct),
      max: pre * (1 + pct),
    };
  }
  const yMin = _minuteYRange.min;
  const yMax = _minuteYRange.max;

  const lastP = prices.filter(p => p > 0).slice(-1)[0] || pre;
  const pColor = lastP >= pre ? C.up : C.down;

  // 构建缠论买卖点标记
  const markPoints = [];
  if (chanlunData && chanlunData.signals) {
    for (const sig of chanlunData.signals) {
      const isBuy = sig.type.startsWith('buy');
      const timeIdx = times.indexOf(sig.time);
      const t = timeIdx >= 0 ? sig.time : times[times.length - 1];
      markPoints.push({
        coord: [t, sig.price],
        symbol: isBuy ? 'triangle' : 'pin',
        symbolSize: 16,
        symbolRotate: isBuy ? 0 : 180,
        itemStyle: {
          color: isBuy ? C.up : C.down,
          borderWidth: 2,
          borderColor: '#fff',
        },
        label: {
          show: true,
          formatter: sig.type_name,
          fontSize: 9,
          fontWeight: 'bold',
          color: '#fff',
          position: isBuy ? 'bottom' : 'top',
          backgroundColor: isBuy ? C.up : C.down,
          padding: [2, 4],
          borderRadius: 2,
        },
      });
    }
  }

  // 构建分型标记（小圆点）
  const fractalMarks = [];
  if (chanlunData && chanlunData.fractals) {
    for (const f of chanlunData.fractals) {
      const timeIdx = times.indexOf(f.time);
      if (timeIdx < 0) continue;
      fractalMarks.push({
        coord: [f.time, f.price],
        symbol: 'circle',
        symbolSize: 6,
        itemStyle: {
          color: f.type === 'top' ? '#00b35c' : '#ff2d2d',
          borderColor: '#fff',
          borderWidth: 1,
        },
      });
    }
  }

  minuteChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim('minute'),
    xAxis: {
      type: 'category', data: times,
      axisLine: { lineStyle: { color: C.axis } },
      axisLabel: { color: C.textDim, fontSize: 10, interval: Math.floor(times.length / 6) },
      splitLine: { show: true, lineStyle: { color: C.grid, type: 'dashed' } },
    },
    yAxis: {
      type: 'value', min: yMin, max: yMax,
      axisLine: { show: false },
      axisLabel: {
        color: C.textDim, fontSize: 11,
        formatter: v => {
          const pct = ((v - pre) / pre * 100).toFixed(2);
          return v.toFixed(2) + '\n' + (pct > 0 ? '+' : '') + pct + '%';
        }
      },
      splitLine: { lineStyle: { color: C.grid, type: 'dashed' } },
    },
    grid: { left: 70, right: 60, top: 10, bottom: 25 },
    series: [
      {
        name: '价格', type: 'line', data: prices, symbol: 'none',
        lineStyle: { color: pColor, width: 1.5 },
        areaStyle: { color: pColor === C.up ? 'rgba(255,45,45,0.08)' : 'rgba(0,179,92,0.08)' },
        markLine: { silent: true, symbol: 'none', animation: false,
          data: [{ yAxis: pre, lineStyle: { color: C.preClose, type: 'dashed', width: 1 },
            label: { formatter: '昨收 ' + pre.toFixed(3), color: C.textDim, fontSize: 10, position: 'insideEndTop' } }]
        },
        markPoint: (markPoints.length || fractalMarks.length) ? {
          data: [...markPoints, ...fractalMarks],
          animation: false,
        } : undefined,
      },
      { name: '均价', type: 'line', data: avgs, symbol: 'none',
        lineStyle: { color: C.avgLine, width: 1, type: 'dashed' } },
    ],
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: '#666' } },
      formatter: (params) => {
        if (!params || !params.length) return '';
        let html = `<div style="font-size:12px;line-height:1.6"><div style="color:${C.textDim}">${params[0].axisValue}</div>`;
        for (const p of params) {
          if (p.value > 0) {
            const pct = ((p.value - pre) / pre * 100).toFixed(2);
            const c = p.value >= pre ? C.up : C.down;
            html += `<div><span style="color:${p.color}">●</span> ${p.seriesName} <span style="color:${c};font-weight:bold">${p.value.toFixed(3)}</span> <span style="color:${c}">(${pct>0?'+':''}${pct}%)</span></div>`;
          }
        }
        return html + '</div>';
      }
    },
  }, true);

  // 分时量
  const vc = vols.map((v, i) => {
    if (v === 0) return { value: 0, itemStyle: { color: '#222' } };
    const p = prices[i] || 0;
    return { value: v, itemStyle: { color: p >= pre ? C.up + '66' : C.down + '66' } };
  });
  minuteVolChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim('minuteVol'),
    xAxis: { type: 'category', data: times, show: false },
    yAxis: { type: 'value', axisLabel: { color: C.textDim, fontSize: 9 }, splitLine: { lineStyle: { color: C.grid } } },
    grid: { left: 70, right: 60, top: 5, bottom: 5 },
    series: [{ type: 'bar', data: vc }],
    tooltip: { trigger: 'axis', formatter: p => p[0] ? `<div style="font-size:11px">${p[0].axisValue}<br/>量 ${fmtVol(p[0].value)}</div>` : '' },
  }, true);
}

// ===== 资金流 =====
function renderFlow(flows) {
  _dailyFlows = flows;  // 缓存日级数据，供模式切换时使用
  if (_flowMode === 'realtime') {
    // 实时模式：不渲染日级图表，由loadRealtimeFlow处理
    loadRealtimeFlow(currentSymbol);
    return;
  }
  _renderDailyFlow(flows);
}

function _renderDailyFlow(flows) {
  const fs = document.getElementById('flow-summary');
  if (!flows || !flows.length) {
    fs.textContent = '';
    flowChart.setOption({ title: { text: '无资金流数据', left: 'center', top: 'center', textStyle: { color: C.textDim, fontSize: 13 } } }, true);
    return;
  }
  // 近5日主力净流入摘要
  const recent = flows.slice(-5);
  const totalMain = recent.reduce((s, f) => s + f.main_net, 0);
  const totalMainYi = (totalMain / 1e8).toFixed(2);
  fs.innerHTML = `近5日主力 <span style="color:${totalMain>=0?C.up:C.down};font-weight:bold">${totalMain>=0?'+':''}${totalMainYi}亿</span>`;

  const dates = flows.map(f => f.date);
  const mainNet = flows.map(f => +(f.main_net / 1e8).toFixed(3));
  const superLarge = flows.map(f => +(f.super_large_net / 1e8).toFixed(3));

  flowChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim('flowDaily'),
    legend: { data: ['主力净流入', '超大单'], textStyle: { color: C.textDim, fontSize: 10 }, top: 2, itemWidth: 12, itemHeight: 8 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: C.textDim, fontSize: 9 }, axisLine: { lineStyle: { color: C.axis } } },
    yAxis: { type: 'value', axisLabel: { color: C.textDim, fontSize: 9 }, splitLine: { lineStyle: { color: C.grid } } },
    grid: { left: 50, right: 20, top: 22, bottom: 18 },
    series: [
      { name: '主力净流入', type: 'bar', data: mainNet.map(v => ({ value: v, itemStyle: { color: v >= 0 ? C.up + '88' : C.down + '88' } })) },
      { name: '超大单', type: 'line', data: superLarge, symbol: 'circle', symbolSize: 3, lineStyle: { color: C.ma20, width: 1 } },
    ],
    tooltip: { trigger: 'axis', formatter: p => {
      let html = `<div style="font-size:11px">${p[0].axisValue}</div>`;
      for (const x of p) html += `<div><span style="color:${x.color}">●</span> ${x.seriesName}: ${x.value>=0?'+':''}${x.value.toFixed(2)}亿</div>`;
      return html;
    }},
  }, true);
}

// ===== 盘中实时资金流 =====
function switchFlowMode(mode) {
  _flowMode = mode;
  // 更新按钮状态
  document.getElementById('ft-rt').classList.toggle('ft-active', mode === 'realtime');
  document.getElementById('ft-daily').classList.toggle('ft-active', mode === 'daily');
  if (mode === 'realtime') {
    loadRealtimeFlow(currentSymbol);
  } else {
    _renderDailyFlow(_dailyFlows);
  }
}

async function loadRealtimeFlow(symbol) {
  if (!symbol) return;
  try {
    const r = await fetch(`${API}/api/realtime_flow?symbol=${symbol}`);
    const data = await r.json();
    renderRealtimeFlow(data);
  } catch(e) {
    const fs = document.getElementById('flow-summary');
    fs.textContent = '实时资金流获取失败';
  }
}

function renderRealtimeFlow(data) {
  const fs = document.getElementById('flow-summary');
  if (data.error || !data.flows || !data.flows.length) {
    fs.textContent = data.error || '暂无实时资金流数据';
    flowChart.setOption({ title: { text: '盘前/非交易日', left: 'center', top: 'center', textStyle: { color: C.textDim, fontSize: 13 } } }, true);
    return;
  }

  const flows = data.flows;
  const summary = data.summary || {};
  const mainNetYi = (summary.main_net / 1e8).toFixed(2);
  const superLargeYi = (summary.super_large_net / 1e8).toFixed(2);
  fs.innerHTML = `今日主力 <span style="color:${summary.main_net>=0?C.up:C.down};font-weight:bold">${summary.main_net>=0?'+':''}${mainNetYi}亿</span> | 超大单 <span style="color:${summary.super_large_net>=0?C.up:C.down};font-weight:bold">${summary.super_large_net>=0?'+':''}${superLargeYi}亿</span>`;

  const times = flows.map(f => f.time);
  const mainNet = flows.map(f => +(f.main_net / 1e8).toFixed(4));
  const superLarge = flows.map(f => +(f.super_large_net / 1e8).toFixed(4));

  flowChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim('flowRt'),
    legend: { data: ['主力净流入(累计)', '超大单(累计)'], textStyle: { color: C.textDim, fontSize: 10 }, top: 2, itemWidth: 12, itemHeight: 8 },
    xAxis: { type: 'category', data: times, axisLabel: { color: C.textDim, fontSize: 9, interval: 29 }, axisLine: { lineStyle: { color: C.axis } } },
    yAxis: { type: 'value', axisLabel: { color: C.textDim, fontSize: 9 }, splitLine: { lineStyle: { color: C.grid } } },
    grid: { left: 50, right: 20, top: 22, bottom: 18 },
    series: [
      { name: '主力净流入(累计)', type: 'bar', data: mainNet.map(v => ({ value: v, itemStyle: { color: v >= 0 ? C.up + '88' : C.down + '88' } })) },
      { name: '超大单(累计)', type: 'line', data: superLarge, symbol: 'none', lineStyle: { color: C.ma20, width: 1 } },
    ],
    tooltip: { trigger: 'axis', formatter: p => {
      let html = `<div style="font-size:11px">${p[0].axisValue}</div>`;
      for (const x of p) html += `<div><span style="color:${x.color}">●</span> ${x.seriesName}: ${x.value>=0?'+':''}${x.value.toFixed(3)}亿</div>`;
      return html;
    }},
  }, true);
}

// ==================== 小白模式增强（frontend-ux-v42 R1） ====================
// 风险解读字典：codes 匹配结构化风险码优先，kws 匹配文案关键词兜底
const RISK_EXPLAIN = [
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
];

function _lastMA(period) {
  try {
    if (!_klineData || _klineData.length < period) return null;
    const arr = calcMA(_klineData, period);
    const v = arr && arr.length ? arr[arr.length - 1] : null;
    return (v == null || isNaN(v)) ? null : v;
  } catch (e) { return null; }
}

// 汇总当前信号的风险并翻译成大白话。返回 [{key,text,advice,raw}]
function explainRisks(signal) {
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

function riskBannerHtml(risks) {
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
function _applyTermChips(safeHtml) {
  if (_mode !== 'simple' || !window.GLOSSARY_TERMS) return safeHtml;
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
function glossarize(text) {
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
      `<div class="gp-ex">例：${escHtml(g.example)}</div>`;
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
function whyTextFor(sigText) {
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
function toggleWhy(el) {
  const body = el.parentElement.querySelector('.sig-why-body');
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  el.textContent = open ? '为什么？' : '收起';
}

// 分数数字滚动（标准/炫酷档）
function countUpScore(target) {
  const el = document.getElementById('sum-score');
  if (!el) return;
  if (!fxEnabled() || document.hidden) { el.textContent = target + '分'; return; }
  const t0 = performance.now(), dur = 600;
  function step(t) {
    const p = Math.min(1, (t - t0) / dur);
    el.textContent = Math.round(target * p) + '分';
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// 右侧卡片依次淡入上滑（标准/炫酷档）
function fxCardStagger() {
  if (_fxLevel === 'off') return;
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
  const isSimpleMode = _mode === 'simple';
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
      <span style="text-decoration:line-through;color:#666">${origAction}</span>
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
  const simpleSig = _mode === 'simple';
  sl.innerHTML = allSigs.map((s, idx) => {
    const isCore = idx < 2 && allSigs.length > 2;
    const coreTag = isCore ? '<span class="sig-core-tag">核心</span>' : '';
    const body = simpleSig ? glossarize(s.text) : escHtml(s.text);
    const why = simpleSig ? `<span class="sig-why" onclick="toggleWhy(this)">为什么？</span><span class="sig-why-body" style="display:none">${escHtml(whyTextFor(s.text))}</span>` : '';
    if (s.type === 'buy') return `<div class="sig-item sig-buy">▲ ${body}${coreTag}${why}</div>`;
    else return `<div class="sig-item sig-sell">▼ ${body}${coreTag}${why}</div>`;
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
        <span style="color:#666">涨</span>
        <span style="color:#444">/</span>
        <span style="color:${C.down};font-weight:bold">${downN}</span>
        <span style="color:#666">跌</span>
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
      <span style="font-size:11px;color:#666;margin-left:auto">上证指数环境</span>
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
const searchInput = document.getElementById('search-input');
const searchResults = document.getElementById('search-results');
let searchTimer = null;

searchInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  const kw = searchInput.value.trim();
  if (!kw) { searchResults.style.display = 'none'; return; }
  searchTimer = setTimeout(() => doSuggest(kw), 250);
});

searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    searchResults.style.display = 'none';
    const kw = searchInput.value.trim();
    if (/^\d{6}$/.test(kw)) analyze(kw);
    else doSearch();
  }
});

document.addEventListener('click', e => {
  if (!e.target.closest('.search-wrap')) searchResults.style.display = 'none';
});

async function doSuggest(kw) {
  try {
    const r = await fetch(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);
    const data = await r.json();
    if (data.results && data.results.length) {
      searchResults.innerHTML = data.results.map(s =>
        `<div class="sr-item" onclick="selectStock('${s.code}','${s.name}')">
          <span class="code">${s.code}</span><span class="name">${s.name}</span>
        </div>`
      ).join('');
      searchResults.style.display = 'block';
    } else searchResults.style.display = 'none';
  } catch(e) {}
}

async function doSearch() {
  const kw = searchInput.value.trim();
  if (!kw) return;
  searchResults.style.display = 'none';
  if (/^\d{6}$/.test(kw)) { analyze(kw); return; }
  try {
    const r = await fetch(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);
    const data = await r.json();
    if (data.results && data.results.length) selectStock(data.results[0].code, data.results[0].name);
  } catch(e) {}
}

function selectStock(code, name) {
  searchResults.style.display = 'none';
  searchInput.value = code;
  analyze(code);
}

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
    if (currentSymbol === symbol) analyze(symbol);
  }, 8000);
}

function _markAnalyzeFail(symbol) {
  document.getElementById('loading').style.display = 'none';
  const el = document.getElementById('sum-body');
  const retryLink = `<span style="color:#4fc3f7;cursor:pointer;text-decoration:underline" onclick="analyze('${symbol}')">立即重试</span>`;
  const autoTxt = _failRetryCount < _MAX_FAIL_RETRY ? '，8秒后自动重试' : '';
  if (_lastOkTime && el.innerHTML.trim()) {
    // 已有上次成功结果：保留旧数据，只在结论区顶部插一条失败横幅
    const oldBanner = document.getElementById('analyze-fail-banner');
    if (oldBanner) oldBanner.remove();
    el.insertAdjacentHTML('afterbegin',
      `<div id="analyze-fail-banner" style="margin-bottom:8px;padding:6px 8px;background:rgba(255,107,107,0.08);border-radius:6px;border:1px solid rgba(255,107,107,0.15);font-size:11px;color:#ff6b6b;line-height:1.5">⚠ 本次刷新失败，以下为上次结果（计算于 ${_lastOkTime}）· ${retryLink}${autoTxt}</div>`);
  } else {
    // 无历史数据：整块错误提示 + 重试入口
    el.innerHTML = `<div class="sum-text" style="color:${C.down}">请求失败，请检查服务是否运行<br><span style="display:inline-block;margin-top:8px;font-size:11px">${retryLink}${autoTxt}</span></div>`;
  }
  _scheduleFailRetry(symbol);
}

async function analyze(symbol) {
  currentSymbol = symbol;
  document.getElementById('loading').style.display = 'flex';
  if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
  if (_failRetryTimer) { clearTimeout(_failRetryTimer); _failRetryTimer = null; }

  // 周K视图传period=week，否则默认day
  const periodParam = currentView === 'week' ? '&period=week' : '';

  // 缠论接口单独容错：它失败不应拖垮主分析渲染
  const clFetch = fetch(`${API}/api/chanlun_daily?symbol=${symbol}${periodParam}`).catch(() => null);

  try {
    const r = await fetch(`${API}/api/analyze?symbol=${symbol}${periodParam}`);
    const data = await r.json();
    const clRes = await clFetch;
    _dailyChanlun = null;
    try { if (clRes) _dailyChanlun = await clRes.json(); } catch(e) {}
    document.getElementById('loading').style.display = 'none';

    if (data.error) {
      document.getElementById('sum-body').innerHTML = `<div class="sum-text" style="color:${C.down}">${data.error}</div>`;
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
    document.getElementById('chanlun-daily-label').textContent = currentView === 'week' ? '缠论周线分析' : '缠论日线分析';
    if (_dailyChanlun && !_dailyChanlun.error) {
      renderChanlunDaily(_dailyChanlun);
      applyChanlunDailyOverlay(_dailyChanlun);
      document.getElementById('chanlun-daily-card').style.display = 'block';
    } else {
      document.getElementById('chanlun-daily-card').style.display = 'none';
    }

    // 缠论分时面板在分时视图才显示
    if (currentView !== 'minute') {
      document.getElementById('chanlun-card').style.display = 'none';
    }

    if (currentView === 'minute') loadMinute(symbol);
    fxCardStagger();   // 右侧卡片依次淡入（FX标准/炫酷档）
    _refreshTimer = setInterval(() => refreshQuote(symbol), 2000);
  } catch(e) {
    _markAnalyzeFail(symbol);
  }
}

async function refreshQuote(symbol) {
  try {
    const r = await fetch(`${API}/api/quote?symbol=${symbol}`);
    const q = await r.json();
    if (!q.error) {
      updateQuote(q);
      // K线最后一根蜡烛跟随实时行情更新
      refreshKlineLastCandle(q);
      if (currentView === 'minute') {
        // 分时视图：用轻量刷新，只更新价格数据，不全量重载图表
        refreshMinuteLight(symbol);
      }
      // 实时资金流刷新（5秒间隔，后端有5秒缓存）
      if (_flowMode === 'realtime') {
        loadRealtimeFlow(symbol);
      }
    }
  } catch(e) {}
}

// 用最新行情刷新K线最后一根蜡烛（日线实时感）
function refreshKlineLastCandle(q) {
  if (!_klineData.length || !q || !q.price) return;
  // 周K视图不实时刷新最后一根蜡烛（周K是聚合数据，实时更新会误导）
  if (currentView === 'week') return;
  const last = _klineData[_klineData.length - 1];
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  if (last.date !== today) return; // 非当日不更新

  last.close = q.price;
  last.high = Math.max(last.high, q.high || q.price);
  last.low = Math.min(last.low, q.low || q.price);
  if (q.volume) last.volume = q.volume;
  if (q.amount) last.amount = q.amount;

  const ma5 = calcMA(_klineData, 5);
  const ma10 = calcMA(_klineData, 10);
  const ma20 = calcMA(_klineData, 20);
  const ma60 = calcMA(_klineData, 60);

  klineChart.setOption({
    animation: false,   // 轮询刷新永远无动画（护栏）
    series: [
      { name: 'K线', data: _klineData.map(k => [k.open, k.close, k.low, k.high]) },
      { name: 'MA5', data: ma5 },
      { name: 'MA10', data: ma10 },
      { name: 'MA20', data: ma20 },
      { name: 'MA60', data: ma60 },
    ]
  }, false);

  volumeChart.setOption({
    animation: false,   // 轮询刷新永远无动画（护栏）
    series: [{
      data: _klineData.map(k => ({
        value: k.volume,
        itemStyle: { color: k.close >= k.open ? C.up + '88' : C.down + '88' }
      }))
    }]
  }, false);
}

async function loadMinute(symbol) {
  try {
    // 确保容器有尺寸（从hidden切过来时ECharts可能还是0x0）
    minuteChart.resize();
    minuteVolChart.resize();
    // 重置Y轴范围（切换股票时）
    _minuteYRange = null;
    // 并行获取分时数据和缠论分析
    const [minuteRes, chanlunRes] = await Promise.all([
      fetch(`${API}/api/minute?symbol=${symbol}`),
      fetch(`${API}/api/chanlun_minute?symbol=${symbol}`),
    ]);
    const data = await minuteRes.json();
    if (data.error) {
      minuteChart.setOption({ title: { text: data.error, left: 'center', top: 'center', textStyle: { color: C.down, fontSize: 14 } } }, true);
      document.getElementById('chanlun-card').style.display = 'none';
      return;
    }
    _minuteData = data;
    let chanlunData = null;
    try {
      chanlunData = await chanlunRes.json();
      _minuteChanlun = chanlunData;
    } catch(e) { chanlunData = null; }
    renderMinute(data, chanlunData);
    renderChanlun(chanlunData);
  } catch(e) {
    minuteChart.setOption({ title: { text: '分时数据获取失败', left: 'center', top: 'center', textStyle: { color: C.down, fontSize: 14 } } }, true);
  }
}

// 分时数据轻量刷新：只更新最后一个点的价格，不全量重载
async function refreshMinuteLight(symbol) {
  try {
    const r = await fetch(`${API}/api/minute?symbol=${symbol}`);
    const data = await r.json();
    if (data.error || !_minuteData) return;
    // 检查数据是否实际变化（长度或最后一个价格）
    const oldLen = _minuteData.prices.length;
    const newLen = data.prices.length;
    const oldLast = _minuteData.prices[oldLen - 1];
    const newLast = data.prices[newLen - 1];
    if (oldLen === newLen && oldLast === newLast) return; // 无变化，跳过

    // 增量更新：只更新数据，保持Y轴和其他配置不变
    _minuteData = data;
    const pre = data.pre_close;
    const pColor = newLast >= pre ? C.up : C.down;

    // 用setOption只更新series数据，不重算Y轴
    minuteChart.setOption({
      animation: false,   // 轮询增量更新永远无动画（护栏）
      series: [
        { name: '价格', data: data.prices, lineStyle: { color: pColor, width: 1.5 } },
        { name: '均价', data: data.avg_prices },
      ],
    });
    // 更新分时量
    const vc = data.volumes.map((v, i) => {
      if (v === 0) return { value: 0, itemStyle: { color: '#222' } };
      const p = data.prices[i] || 0;
      return { value: v, itemStyle: { color: p >= pre ? C.up + '66' : C.down + '66' } };
    });
    minuteVolChart.setOption({
      animation: false,   // 轮询增量更新永远无动画（护栏）
      series: [{ data: vc }],
    });
  } catch(e) {}
}

// ===== 自选股 & 历史记录 =====
const STORAGE_WATCH = 'qs_watchlist';
const STORAGE_HISTORY = 'qs_history';
const MAX_HISTORY = 30;
let _currentTab = 'watch';
let _panelOpen = false;

// --- localStorage 读写（frontend-ux-v42 R2：分组模型；旧键只读不删） ---
const GKEY_GROUPS = 'qs_watch_groups';
const GKEY_STOCKS = 'qs_watch_stocks';

function _lsGet(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; }
  catch (e) { return fallback; }
}
function _lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); return true; } catch (e) { return false; }
}

function getGroups() { return _lsGet(GKEY_GROUPS, [{ id: 'default', name: '我的自选', order: 0, collapsed: false, codes: [] }]); }
function saveGroups(g) { _lsSet(GKEY_GROUPS, g); renderSidebar(); updateBadges(); }
function getStockMap() { return _lsGet(GKEY_STOCKS, {}); }
function saveStockMap(m) { _lsSet(GKEY_STOCKS, m); }

// 旧版平铺 qs_watchlist 自动迁移（一次性；迁移失败保留原键不动）
function migrateWatchlist() {
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
function getWatchlist() {
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
function saveWatchlist(list) {
  const m = getStockMap(); let dirty = false;
  for (const s of (list || [])) {
    if (s && s.code && m[s.code]) {
      for (const k of ['name', 'action', 'score', 'price', 'pct']) if (s[k] !== undefined && s[k] !== null) m[s.code][k] = s[k];
      dirty = true;
    }
  }
  if (dirty) saveStockMap(m);
}

function addToGroup(code, name, gid) {
  const groups = getGroups();
  const g = groups.find(x => x.id === gid) || groups[0];
  if (!g) return;
  const m = getStockMap();
  if (!m[code]) m[code] = { name: name || code, action: '', score: 0, addedAt: Date.now(), pinned: false };
  else if (name) m[code].name = name;
  saveStockMap(m);
  if (!g.codes.includes(code)) { g.codes.unshift(code); saveGroups(groups); }
  updateStarButton(currentSymbol); renderWatchlist(); renderSidebar(); updateBadges();
}
function removeStockEverywhere(code) {
  const groups = getGroups(); let changed = false;
  for (const g of groups) { const i = g.codes.indexOf(code); if (i >= 0) { g.codes.splice(i, 1); changed = true; } }
  if (changed) saveGroups(groups);
  const m = getStockMap(); if (m[code]) { delete m[code]; saveStockMap(m); }
}
function getHistory() {
  try { return JSON.parse(localStorage.getItem(STORAGE_HISTORY) || '[]'); } catch(e) { return []; }
}
function saveHistory(list) {
  localStorage.setItem(STORAGE_HISTORY, JSON.stringify(list));
}

// --- 自选股操作 ---
function toggleStar() {
  if (!currentSymbol) return;
  const inWatch = getWatchlist().some(s => s.code === currentSymbol);
  const btn = document.getElementById('star-btn');
  if (inWatch) {
    removeFromWatchlist(currentSymbol);
  } else {
    addToGroup(currentSymbol, _currentStockName || currentSymbol, _sbActiveGroup || 'default');
    if (btn && fxEnabled()) { btn.classList.remove('fx-pop'); void btn.offsetWidth; btn.classList.add('fx-pop'); }
  }
}

function removeFromWatchlist(code) {
  removeStockEverywhere(code);
  updateStarButton(currentSymbol);
  renderWatchlist();
  updateBadges();
}

function removeFromWatchlist(code) {
  const list = getWatchlist().filter(s => s.code !== code);
  saveWatchlist(list);
  updateStarButton(currentSymbol);
  renderWatchlist();
  updateBadges();
}

function updateStarButton(symbol) {
  const btn = document.getElementById('star-btn');
  if (!btn || !symbol) return;
  const inWatch = getWatchlist().some(s => s.code === symbol);
  btn.textContent = inWatch ? '★' : '☆';
  btn.classList.toggle('starred', inWatch);
  btn.title = inWatch ? '从自选中移除' : '加入自选';
}

// --- 历史记录操作 ---
function addHistory(code, name, action, score) {
  let list = getHistory();
  // 去重：移除已存在的相同code
  list = list.filter(s => s.code !== code);
  // 加到头部
  list.unshift({ code, name, action, score, time: Date.now() });
  // 限制数量
  if (list.length > MAX_HISTORY) list = list.slice(0, MAX_HISTORY);
  saveHistory(list);
  if (_panelOpen && _currentTab === 'history') renderHistory();
  updateBadges();
}

function clearHistory() {
  saveHistory([]);
  renderHistory();
  updateBadges();
}

// --- 渲染 ---
function renderWatchlist() {
  // 自选列表已迁至左侧工作台「自选」分区，统一由分组侧栏渲染
  renderSidebar();
}

function renderHistory() {
  const el = document.getElementById('wp-content-history');
  const list = getHistory();
  if (!list.length) {
    el.innerHTML = '<div class="wp-empty"><span class="wp-empty-icon">🕐</span>暂无历史记录<br>分析过的股票会显示在这里</div>';
    return;
  }
  el.innerHTML = list.map(s => {
    const tag = sigTag(s.action, s.score);
    const t = fmtTime(s.time);
    return `<div class="wp-item" onclick="analyze('${s.code}');closePanel()">
      <span class="code">${s.code}</span>
      <span class="name">${escHtml(s.name)}</span>
      ${tag}
      <span class="time">${t}</span>
    </div>`;
  }).join('');
}

function sigTag(action, score) {
  if (!action) return '<span class="sig-tag none">--</span>';
  const cls = action === '买入' ? 'buy' : action === '卖出' ? 'sell' : 'watch';
  return `<span class="sig-tag ${cls}">${action}${score ? ' ' + score : ''}</span>`;
}

function fmtTime(ts) {
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

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// --- 面板控制 ---
// wp-panel 已常驻左侧工作台，不再有"弹出层关闭"语义；保留函数为空操作，
// 兼容历史/一览/档案/核心池条目 onclick 里的历史调用。
function closePanel() {}

function togglePanel(tab) {
  _currentTab = tab;
  const panel = document.getElementById('wp-panel');
  if (_panelOpen && panel.classList.contains('show')) {
    // 已打开，切换tab
    if (_currentTab === tab) {
      // 同一按钮再次点击=关闭
      closePanel();
      return;
    }
  } else {
    panel.classList.add('show');
    _panelOpen = true;
  }
  switchTab(tab);
}

function switchTab(tab) {
  // wp-panel 已迁入左侧工作台：自选股 tab 回路由到自选分区
  if (tab === 'watch' && document.getElementById('sb-pane-watch')) { openSbSection('watch'); return; }
  _currentTab = tab;
  // tab样式
  document.querySelectorAll('.wp-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.querySelectorAll('.sb-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.sb === tab);
  });
  // 内容切换（wp-content-watch 已随自选功能迁至独立分区，安全跳过）
  const wEl = document.getElementById('wp-content-watch');
  if (wEl) wEl.style.display = tab === 'watch' ? 'block' : 'none';
  document.getElementById('wp-content-history').style.display = tab === 'history' ? 'block' : 'none';
  document.getElementById('wp-content-overview').style.display = tab === 'overview' ? 'block' : 'none';
  document.getElementById('wp-content-journal').style.display = tab === 'journal' ? 'block' : 'none';
  document.getElementById('wp-content-pool').style.display = tab === 'pool' ? 'block' : 'none';
  // 渲染内容
  if (tab === 'history') renderHistory();
  else if (tab === 'overview') loadOverview();
  else if (tab === 'journal') loadJournal();
  else if (tab === 'pool') loadPool();
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

function clearCurrentTab() {
  if (_currentTab === 'watch') {
    saveWatchlist([]);
    renderWatchlist();
    updateStarButton(currentSymbol);
  } else {
    clearHistory();
  }
  updateBadges();
  switchTab(_currentTab);
}

function updateBadges() {
  const wl = getWatchlist().length;
  const hl = getHistory().length;
  const wb = document.getElementById('watch-count');
  const hb = document.getElementById('history-count');
  const wt = document.getElementById('wt-count');
  const ht = document.getElementById('ht-count');
  const ov = document.getElementById('ov-count');
  if (wb) { wb.textContent = wl; wb.style.display = wl > 0 ? 'inline-block' : 'none'; }
  if (hb) { hb.textContent = hl; hb.style.display = hl > 0 ? 'inline-block' : 'none'; }
  if (wt) wt.textContent = wl > 0 ? `(${wl})` : '';
  if (ht) ht.textContent = hl > 0 ? `(${hl})` : '';
  if (ov) ov.textContent = wl > 0 ? `(${wl})` : '';
}

// 点击面板外部关闭
document.addEventListener('click', e => {
  if (_panelOpen && !e.target.closest('.watch-wrap')) {
    closePanel();
  }
});

// 阻止面板内点击冒泡导致关闭
document.getElementById('wp-panel').addEventListener('click', e => e.stopPropagation());

// 跟踪当前股票名称（用于自选时获取名称）
let _currentStockName = '';
const _origUpdateQuote = updateQuote;
updateQuote = function(q) {
  _origUpdateQuote(q);
  if (q && q.name) _currentStockName = q.name;
};

// ==================== 左侧工作台（frontend迭代：分区 + 宽面板） ====================
let _sbOpen = true;
let _sbActiveGroup = 'default';
let _sbTimer = null;
let _sbSection = 'watch';   // watch | history | overview | journal | pool | scan
const SB_SECTIONS = { watch: '自选股', history: '历史记录', overview: '多股一览', journal: '信号档案', pool: '核心池', scan: '扫描归档' };

function loadSbSection() {
  try { const v = localStorage.getItem('qs_sb_section'); if (v && SB_SECTIONS[v]) _sbSection = v; } catch (e) {}
}
// 切换分区（顶栏按钮/侧栏tab/面板内tab统一入口）
function openSbSection(sec) {
  if (!SB_SECTIONS[sec]) return;
  _sbSection = sec;
  try { localStorage.setItem('qs_sb_section', sec); } catch (e) {}
  if (!_sbOpen) _sbOpen = true;
  applySidebar();
  renderSbSection();
}
function renderSbSection() {
  document.querySelectorAll('.sb-tab').forEach(t => t.classList.toggle('active', t.dataset.sb === _sbSection));
  const pWatch = document.getElementById('sb-pane-watch');
  const pMods = document.getElementById('sb-pane-modules');
  const pScan = document.getElementById('sb-pane-scan');
  if (!pWatch || !pMods || !pScan) return;
  pWatch.classList.toggle('active', _sbSection === 'watch');
  pMods.classList.toggle('active', ['history', 'overview', 'journal', 'pool'].includes(_sbSection));
  pScan.classList.toggle('active', _sbSection === 'scan');
  const title = document.getElementById('sb-title');
  if (title) title.textContent = SB_SECTIONS[_sbSection];
  if (_sbSection === 'watch') renderSidebar();
  else if (_sbSection === 'scan') renderScanArchiveList();
  else switchTab(_sbSection);   // 复用原面板渲染器，内部 tab 高亮同步
}

function isMarketOpen() {
  const d = new Date(); const wd = d.getDay();
  if (wd === 0 || wd === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return (m >= 555 && m <= 695) || (m >= 775 && m <= 905); // 9:15-11:35 / 12:55-15:05
}
function sidebarLoadState() {
  try { const v = localStorage.getItem('qs_sidebar_open'); _sbOpen = (v == null) ? true : v === '1'; } catch (e) {}
}
function toggleSidebar() { _sbOpen = !_sbOpen; applySidebar(); try { localStorage.setItem('qs_sidebar_open', _sbOpen ? '1' : '0'); } catch (e) {} }
function resizeAllChartsSafe() {
  [klineChart, volumeChart, flowChart, minuteChart, minuteVolChart, indicatorChart].forEach(c => { try { c && c.resize(); } catch (e) {} });
}
function applySidebar() {
  const sb = document.getElementById('sidebar'); const t = document.getElementById('sb-toggle');
  if (sb) sb.classList.toggle('open', _sbOpen);
  document.body.classList.toggle('sb-open', _sbOpen);
  document.body.classList.toggle('sb-section-watch', _sbSection === 'watch');   // 驱动 --sb-w 宽度变量
  document.querySelectorAll('.sb-tab').forEach(x => x.classList.toggle('active', x.dataset.sb === _sbSection));
  if (t) t.textContent = _sbOpen ? '◀' : '▶';
  setTimeout(resizeAllChartsSafe, (_fxLevel === 'max') ? 220 : 0);
}

function sbBadge(action) {
  if (!action) return '';
  const cls = action.includes('买') ? 'b' : action.includes('卖') ? 's' : 'w';
  const txt = action.replace('强烈', '').replace('谨慎', '');
  return `<span class="sb-badge ${cls}">${escHtml(txt)}</span>`;
}

function renderSidebar() {
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
      `<span class="sb-arrow" onclick="event.stopPropagation();sbToggleCollapse('${g.id}')">${g.collapsed ? '▸' : '▾'}</span>` +
      `<span class="sb-gname" title="双击重命名" ondblclick="renameGroupInline(this,'${g.id}')">${escHtml(g.name)}</span>` +
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
function sbToggleCollapse(gid) {
  const gs = getGroups(); const g = gs.find(x => x.id === gid); if (!g) return;
  g.collapsed = !g.collapsed; saveGroups(gs);
}
function sbSelectGroup(gid) { _sbActiveGroup = gid; renderSidebar(); }
function createGroup(n) {
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
function addGroupInline() {
  // 收起态先展开，否则输入框在屏幕外看不见
  if (!_sbOpen) toggleSidebar();
  const f = document.querySelector('.sb-footer'); if (!f) return;
  let inp = f.querySelector('.sb-new-input');
  if (inp) { inp.focus(); return; }   // 已在输入中：聚焦而不是忽略
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
function _finishNewGroup(inp) {
  const n = (inp.value || '').trim();
  inp.remove();
  if (!n) return;   // 空名静默取消
  createGroup(n);
}
function renameGroupInline(el, gid) {
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
function renameGroupInlineById(gid) {
  const el = document.querySelector(`.sb-ghead[data-gid="${gid}"] .sb-gname`);
  if (el) renameGroupInline(el, gid); else renderSidebar();
}
function deleteGroup(gid) {
  if (gid === 'default') { showToastMsg('默认分组不可删除'); return; }
  const gs = getGroups(); const g = gs.find(x => x.id === gid); if (!g) return;
  if (!confirm(`删除分组「${g.name}」？其 ${g.codes.length} 只成员将移入"我的自选"。`)) return;
  const def = gs.find(x => x.id === 'default');
  g.codes.forEach(c => { if (!def.codes.includes(c)) def.codes.push(c); });
  saveGroups(gs.filter(x => x.id !== gid));
  showToastMsg('分组已删除，成员已回落默认分组');
}
function moveStock(code, gid) {
  const gs = getGroups();
  for (const g of gs) { const i = g.codes.indexOf(code); if (i >= 0) { g.codes.splice(i, 1); break; } }
  const t = gs.find(x => x.id === gid);
  if (t && !t.codes.includes(code)) t.codes.unshift(code);
  saveGroups(gs); hideCtxMenu();
}
function pinStock(code, gid) {
  const gs = getGroups(); const g = gs.find(x => x.id === gid); if (!g) { hideCtxMenu(); return; }
  const i = g.codes.indexOf(code);
  if (i >= 0) g.codes.splice(i, 1);
  g.codes.unshift(code);
  const m = getStockMap(); if (m[code]) { m[code].pinned = true; saveStockMap(m); }
  saveGroups(gs); hideCtxMenu();
}

// ---- 右键菜单 ----
function showCtxMenu(ev, type, id, gid) {
  const menu = document.getElementById('ctx-menu'); if (!menu) return;
  let html = '';
  if (type === 'stock') {
    html = '<div class="ctx-title">移动到分组</div>' +
      getGroups().map(g => `<div class="ctx-item${g.id === gid ? ' cur' : ''}" onclick="moveStock('${id}','${g.id}')">${g.id === gid ? '✓ ' : ''}${escHtml(g.name)}</div>`).join('') +
      `<div class="ctx-sep"></div>` +
      `<div class="ctx-item" onclick="pinStock('${id}','${gid}')">置顶</div>` +
      `<div class="ctx-item danger" onclick="hideCtxMenu();removeFromWatchlist('${id}')">删除</div>`;
  } else {
    html = `<div class="ctx-item" onclick="hideCtxMenu();renameGroupInlineById('${id}')">重命名</div>` +
           `<div class="ctx-item" onclick="hideCtxMenu();sbToggleCollapse('${id}')">折叠/展开</div>`;
    if (id !== 'default') html += `<div class="ctx-item danger" onclick="hideCtxMenu();deleteGroup('${id}')">删除分组</div>`;
  }
  menu.innerHTML = html;
  menu.style.display = 'block';
  menu.style.left = Math.min(ev.clientX, window.innerWidth - menu.offsetWidth - 8) + 'px';
  menu.style.top = Math.min(ev.clientY, window.innerHeight - menu.offsetHeight - 8) + 'px';
}
function hideCtxMenu() { const m = document.getElementById('ctx-menu'); if (m) m.style.display = 'none'; }
document.addEventListener('click', e => { if (!(e.target.closest && e.target.closest('#ctx-menu'))) hideCtxMenu(); });

// ---- 行情轮询：盘中5s / 盘后60s / 页签隐藏暂停（A8/A85-A87） ----
async function sbRefreshQuotes() {
  if (document.hidden) return;
  const codes = [...new Set(getWatchlist().map(s => s.code))];
  if (!codes.length) return;
  let quotes = null;
  try {   // P3：优先批量接口
    const r = await fetch(`${API}/api/quotes?codes=${encodeURIComponent(codes.join(','))}`);
    const j = await r.json();
    if (j && j.quotes) quotes = j.quotes;
  } catch (e) {}
  if (!quotes) {   // 兜底：并行逐只拉取
    quotes = {};
    await Promise.all(codes.map(async c => {
      try { const r = await fetch(`${API}/api/quote?symbol=${c}`); const q = await r.json(); if (!q.error) quotes[c] = q; } catch (e) {}
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
  if (_fxLevel === 'max') changedRows.forEach(code => {
    const el = document.querySelector(`.sb-row[data-code="${code}"] .sb-badge`);
    if (el) { el.classList.add('fx-ring'); setTimeout(() => el.classList.remove('fx-ring'), 1400); }
  });
}
function sbSchedulePolling() {
  if (_sbTimer) clearInterval(_sbTimer);
  _sbTimer = setInterval(sbRefreshQuotes, isMarketOpen() ? 5000 : 60000);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { sbSchedulePolling(); sbRefreshQuotes(); }
});

// ---- 设置弹层 / 导出导入 ----
function toggleSettings() {
  const o = document.getElementById('settings-overlay');
  if (!o) return;
  o.style.display = (o.style.display === 'flex') ? 'none' : 'flex';
  applyFx();
}
function closeSettings(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  const o = document.getElementById('settings-overlay'); if (o) o.style.display = 'none';
}
function closeSettingsForce() { const o = document.getElementById('settings-overlay'); if (o) o.style.display = 'none'; }

function exportWatchlist() {
  const data = { version: 1, exportedAt: new Date().toISOString(), groups: getGroups(), stocks: getStockMap() };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'watchlist-backup-' + new Date().toISOString().slice(0, 10).replace(/-/g, '') + '.json';
  a.click(); URL.revokeObjectURL(a.href);
  showToastMsg('自选股已导出');
}
function importWatchlist(input) {
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
    renderSidebar(); renderWatchlist(); updateBadges(); updateStarButton(currentSymbol);
    showToastMsg(`导入完成，新增 ${cnt} 只自选`);
  };
  rd.readAsText(f, 'utf-8');
}

// 轻量消息 Toast（复用现有 toast 样式容器）
function showToastMsg(msg) {
  const c = document.getElementById('toast-container'); if (!c) return;
  const d = document.createElement('div');
  d.className = 'toast msg-toast'; d.textContent = msg;
  c.appendChild(d);
  setTimeout(() => { d.classList.add('removing'); setTimeout(() => d.remove(), 350); }, 2200);
}

// ===== 启动 =====
applyFx();                 // FX 档位（body class + 设置面板状态）
sidebarLoadState();        // 侧边栏开合记忆
loadSbSection();           // 上次停留的工作台分区
migrateWatchlist();        // 旧自选一次性迁移（在首次渲染前执行）
initCharts();
updateBadges();
loadMode();
// 工作台分区 tab 点击
const _sbTabs = document.getElementById('sb-tabs');
if (_sbTabs) _sbTabs.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('.sb-tab');
  if (b) openSbSection(b.dataset.sb);
});
applySidebar();
renderSidebar();
renderSbSection();
sbSchedulePolling();
sbRefreshQuotes();         // 启动即刷一轮自选行情
// 自动加载上次分析的股票（没有则默认茅台）
const _hist = getHistory();
const _lastSymbol = (_hist.length > 0) ? _hist[0].code : '600519';
analyze(_lastSymbol);

// ===== 小白/专业模式切换 =====
function loadMode() {
  try { _mode = localStorage.getItem('qs_mode') || 'pro'; } catch(e) { _mode = 'pro'; }
  applyMode();
}
function setMode(mode) {
  _mode = mode;
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
  if (_lastSignalData) { renderSummary(_lastSignalData); }
}
function applyMode() {
  document.body.classList.remove('mode-pro', 'mode-simple');
  document.body.classList.add('mode-' + _mode);
  document.getElementById('mt-pro').classList.toggle('active', _mode === 'pro');
  document.getElementById('mt-simple').classList.toggle('active', _mode === 'simple');
}

// ===== 卡片折叠 =====
function toggleCard(headerEl) {
  const card = headerEl.closest('.signal-card, .chanlun-card');
  if (card) card.classList.toggle('collapsed');
}
function collapseCard(cardName, collapse) {
  const card = document.querySelector(`[data-card="${cardName}"]`);
  if (card) card.classList.toggle('collapsed', collapse);
}

// ===== 技术指标计算与渲染 =====
function switchIndicator(ind) {
  _currentIndicator = ind;
  // 更新按钮状态
  document.querySelectorAll('.it-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.ind === ind);
  });
  const chart = document.getElementById('indicator-chart');
  const toolbar = document.getElementById('indicator-toolbar');
  if (ind === 'none') {
    chart.style.display = 'none';
    toolbar.style.display = 'none';
    // 清除BOLL轨道线（如果之前有叠加）
    clearBolloverlay();
  } else {
    // 切换到非BOLL指标时清除BOLL轨道
    if (ind !== 'boll') clearBolloverlay();
    chart.style.display = 'block';
    toolbar.style.display = 'flex';
    setTimeout(() => indicatorChart.resize(), 50);
    renderIndicator(ind);
  }
}

function clearBolloverlay() {
  if (!klineChart || !_klineData.length) return;
  const opt = klineChart.getOption();
  const series = opt.series || [];
  // 如果有BOLL系列（超过5个系列：K线+MA5+MA10+MA20+MA60=5），重新渲染K线清除
  if (series.length > 5) {
    if (_lastSignalData) {
      renderKline(_klineData, _lastSignalData);
      // 重新叠加缠论
      if (_dailyChanlun && !_dailyChanlun.error) {
        applyChanlunDailyOverlay(_dailyChanlun);
      }
    }
  }
}

function renderIndicator(ind) {
  if (ind === 'none' || !_klineData.length) {
    indicatorChart.setOption({}, true);
    return;
  }
  const dates = _klineData.map(k => k.date);
  const closes = _klineData.map(k => k.close);
  const highs = _klineData.map(k => k.high);
  const lows = _klineData.map(k => k.low);
  const volumes = _klineData.map(k => k.volume);

  let series = [];
  let legend = [];
  let yMin, yMax;

  if (ind === 'macd') {
    const r = calcMACD(closes);
    series = [
      { name: 'DIF', type: 'line', data: r.dif, symbol: 'none', lineStyle: { color: C.ma20, width: 1 } },
      { name: 'DEA', type: 'line', data: r.dea, symbol: 'none', lineStyle: { color: C.ma10, width: 1 } },
      { name: 'MACD', type: 'bar', data: r.macd.map(v => ({
        value: v, itemStyle: { color: v >= 0 ? C.up + '88' : C.down + '88' }
      })) },
    ];
    legend = ['DIF', 'DEA', 'MACD'];
  } else if (ind === 'rsi') {
    const rsi6 = calcRSI(closes, 6);
    const rsi12 = calcRSI(closes, 12);
    const rsi24 = calcRSI(closes, 24);
    series = [
      { name: 'RSI6', type: 'line', data: rsi6, symbol: 'none', lineStyle: { color: C.up, width: 1 } },
      { name: 'RSI12', type: 'line', data: rsi12, symbol: 'none', lineStyle: { color: C.ma20, width: 1 } },
      { name: 'RSI24', type: 'line', data: rsi24, symbol: 'none', lineStyle: { color: C.ma10, width: 1 } },
    ];
    legend = ['RSI6', 'RSI12', 'RSI24'];
    yMin = 0; yMax = 100;
  } else if (ind === 'kdj') {
    const r = calcKDJ(highs, lows, closes);
    series = [
      { name: 'K', type: 'line', data: r.k, symbol: 'none', lineStyle: { color: C.up, width: 1 } },
      { name: 'D', type: 'line', data: r.d, symbol: 'none', lineStyle: { color: C.ma20, width: 1 } },
      { name: 'J', type: 'line', data: r.j, symbol: 'none', lineStyle: { color: C.ma10, width: 1 } },
    ];
    legend = ['K', 'D', 'J'];
    yMin = 0; yMax = 100;
  } else if (ind === 'boll') {
    const r = calcBOLL(closes, 20);
    // BOLL直接画在主K线图上
    klineChart.setOption({
      series: [
        {}, {}, {}, {}, {},
        { name: 'BOLL上轨', type: 'line', data: r.upper, symbol: 'none', lineStyle: { color: '#4fc3f7', width: 1, type: 'dashed' } },
        { name: 'BOLL中轨', type: 'line', data: r.mid, symbol: 'none', lineStyle: { color: '#ffeb3b', width: 1 } },
        { name: 'BOLL下轨', type: 'line', data: r.lower, symbol: 'none', lineStyle: { color: '#4fc3f7', width: 1, type: 'dashed' } },
      ]
    });
    indicatorChart.setOption({
      title: { text: 'BOLL已叠加到主图', left: 'center', top: 'center', textStyle: { color: '#666', fontSize: 13 } }
    }, true);
    return;
  } else if (ind === 'wr') {
    const wr6 = calcWR(highs, lows, closes, 6);
    const wr10 = calcWR(highs, lows, closes, 10);
    series = [
      { name: 'WR6', type: 'line', data: wr6, symbol: 'none', lineStyle: { color: C.up, width: 1 } },
      { name: 'WR10', type: 'line', data: wr10, symbol: 'none', lineStyle: { color: C.ma20, width: 1 } },
    ];
    legend = ['WR6', 'WR10'];
    yMin = 0; yMax = 100;
  }

  indicatorChart.setOption({
    backgroundColor: C.bg,
    animation: chartAnim(),   // 用户主动切换副图：max档允许动画（非轮询路径）
    legend: { data: legend, textStyle: { color: C.textDim, fontSize: 10 }, top: 2, itemWidth: 12, itemHeight: 8 },
    xAxis: { type: 'category', data: dates, show: false },
    yAxis: {
      type: 'value',
      min: yMin, max: yMax,
      axisLabel: { color: C.textDim, fontSize: 9 },
      splitLine: { lineStyle: { color: C.grid } },
    },
    grid: { left: 60, right: 50, top: 20, bottom: 5 },
    series: series,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: '#666' } },
      formatter: (params) => {
        if (!params || !params.length) return '';
        let html = `<div style="font-size:11px">${params[0].axisValue}</div>`;
        for (const p of params) {
          if (p.value != null) html += `<div><span style="color:${p.color}">●</span> ${p.seriesName}: ${(+p.value).toFixed(3)}</div>`;
        }
        return html;
      }
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', start: 0, end: 100, show: false },
    ],
  }, true);

  // 重建后跟随主图当前窗口（否则切指标会跳回全量范围）
  try {
    const kd = klineChart.getOption().dataZoom || [];
    const cur = kd[kd.length - 1] || {};
    if (typeof cur.start === 'number') {
      indicatorChart.dispatchAction({ type: 'dataZoom', start: cur.start, end: cur.end });
    }
  } catch (e) {}
}

// EMA计算
function calcEMA(data, period) {
  const result = new Array(data.length).fill(null);
  if (data.length < period) return result;
  const k = 2 / (period + 1);
  let ema = data.slice(0, period).reduce((a, b) => a + b, 0) / period;
  result[period - 1] = ema;
  for (let i = period; i < data.length; i++) {
    ema = data[i] * k + ema * (1 - k);
    result[i] = ema;
  }
  return result;
}

// MACD计算
function calcMACD(closes) {
  const fast = 12, slow = 26, signal = 9;
  const emaFast = calcEMA(closes, fast);
  const emaSlow = calcEMA(closes, slow);
  const dif = closes.map((_, i) => {
    if (emaFast[i] == null || emaSlow[i] == null) return null;
    return +(emaFast[i] - emaSlow[i]).toFixed(4);
  });
  // DEA = EMA(DIF, 9)
  const difValid = dif.map(v => v == null ? 0 : v);
  const dea = calcEMA(difValid, signal).map(v => v == null ? null : +v.toFixed(4));
  const macd = dif.map((d, i) => {
    if (d == null || dea[i] == null) return null;
    return +((d - dea[i]) * 2).toFixed(4);
  });
  return { dif, dea, macd };
}

// RSI计算
function calcRSI(closes, period) {
  const result = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return result;
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1];
    if (change >= 0) avgGain += change; else avgLoss -= change;
  }
  avgGain /= period;
  avgLoss /= period;
  result[period] = avgLoss === 0 ? 100 : +(100 - 100 / (1 + avgGain / avgLoss)).toFixed(2);
  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = change >= 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result[i] = avgLoss === 0 ? 100 : +(100 - 100 / (1 + avgGain / avgLoss)).toFixed(2);
  }
  return result;
}

// KDJ计算
function calcKDJ(highs, lows, closes, period = 9) {
  const kPeriod = 3, dPeriod = 3;
  const k = new Array(closes.length).fill(null);
  const d = new Array(closes.length).fill(null);
  const j = new Array(closes.length).fill(null);
  let prevK = 50, prevD = 50;
  for (let i = period - 1; i < closes.length; i++) {
    let hh = -Infinity, ll = Infinity;
    for (let m = 0; m < period; m++) {
      hh = Math.max(hh, highs[i - m]);
      ll = Math.min(ll, lows[i - m]);
    }
    const rsv = hh === ll ? 50 : ((closes[i] - ll) / (hh - ll)) * 100;
    const curK = (2 / 3) * prevK + (1 / 3) * rsv;
    const curD = (2 / 3) * prevD + (1 / 3) * curK;
    k[i] = +curK.toFixed(2);
    d[i] = +curD.toFixed(2);
    j[i] = +(3 * curK - 2 * curD).toFixed(2);
    prevK = curK;
    prevD = curD;
  }
  return { k, d, j };
}

// BOLL计算
function calcBOLL(closes, period = 20, mult = 2) {
  const mid = new Array(closes.length).fill(null);
  const upper = new Array(closes.length).fill(null);
  const lower = new Array(closes.length).fill(null);
  for (let i = period - 1; i < closes.length; i++) {
    const slice = closes.slice(i - period + 1, i + 1);
    const ma = slice.reduce((a, b) => a + b, 0) / period;
    const variance = slice.reduce((a, b) => a + (b - ma) ** 2, 0) / period;
    const std = Math.sqrt(variance);
    mid[i] = +ma.toFixed(3);
    upper[i] = +(ma + mult * std).toFixed(3);
    lower[i] = +(ma - mult * std).toFixed(3);
  }
  return { mid, upper, lower };
}

// WR (威廉指标) 计算
function calcWR(highs, lows, closes, period) {
  const result = new Array(closes.length).fill(null);
  for (let i = period - 1; i < closes.length; i++) {
    let hh = -Infinity, ll = Infinity;
    for (let m = 0; m < period; m++) {
      hh = Math.max(hh, highs[i - m]);
      ll = Math.min(ll, lows[i - m]);
    }
    result[i] = hh === ll ? 0 : +(((hh - closes[i]) / (hh - ll)) * 100).toFixed(2);
  }
  return result;
}

// ===== 多股一览 =====
async function loadOverview() {
  const el = document.getElementById('wp-content-overview');
  const list = getWatchlist();
  if (!list.length) {
    el.innerHTML = '<div class="wp-empty"><span class="wp-empty-icon">📊</span>还没有自选股<br>添加自选后可查看多股一览</div>';
    return;
  }
  el.innerHTML = '<div class="wp-ov-loading">正在获取行情数据...</div>';

  // 并行获取所有自选股的简要行情
  const results = await Promise.all(list.map(async s => {
    try {
      const r = await fetch(`${API}/api/quote?symbol=${s.code}`);
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
    return `<div class="wp-ov-item" onclick="analyze('${s.code}');closePanel()">
      <span class="wp-ov-code">${s.code}</span>
      <span class="wp-ov-name">${escHtml(s.name)}</span>
      <span class="wp-ov-pct ${pctCls}">${pctStr}</span>
      <span class="wp-ov-score" style="color:${scoreColor}">${scoreStr}</span>
      <span class="wp-ov-sig">${sigTag}</span>
    </div>`;
  }).join('');
}

// ===== 信号档案（真实信号日志，只读） =====
const _journalTypeNames = {
  buy: '买入', strong_buy: '强烈买入', cautious_buy: '谨慎买入',
  breakout_exit: '突破卖出', short_cover: '空头平仓',
  chanlun_buy1: '缠论一买', chanlun_buy2: '缠论二买',
  chanlun_sell1: '缠论一卖', chanlun_sell2: '缠论二卖',
};
let _journalShowDupes = false;
let _journalTypeFilter = '';
let _journalSymbolFilter = '';
// 导出用：最近一次 /api/journal 结果与其过滤条件（frontend-iteration）
window._journalLastRecords = [];
window._journalLastQuery = null;

function _followupMap(rec) {
  const m = {};
  (rec.followups || []).forEach(f => { m[f.horizon] = f; });
  return m;
}

async function loadJournal() {
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
    const r = await fetch(`${API}/api/journal?${qs}`);
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
    return `<tr>
      <td>${rec.trigger_date || ''}</td>
      <td><a href="#" onclick="analyze('${rec.symbol}');closePanel();return false" style="color:#ff9800;text-decoration:none">${rec.symbol}</a></td>
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
      <tbody>${rows || '<tr><td colspan="8" style="padding:16px;color:#666">暂无记录——产生买卖信号后自动落档</td></tr>'}</tbody>
    </table>`;
}

// ===== 信号档案导出（frontend-iteration：纯前端生成，不新增后端接口） =====
function _downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 200);
}

function _csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function _journalExportStem() {
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

function exportJournalCsv() {
  const records = window._journalLastRecords || [];
  if (!records.length) { alert('当前过滤条件下暂无记录可导出'); return; }
  const header = ['信号日', '代码', '类型', '动作', '信号价', '去重标记', '5日%', '10日%', '20日%', '60日%'];
  const lines = [header.join(',')];
  records.forEach(rec => {
    const f = _followupMap(rec);
    const pct = h => (f[h] && f[h].return_pct != null) ? f[h].return_pct : '';
    lines.push([
      rec.trigger_date || '', rec.symbol || '',
      _journalTypeNames[rec.signal_type] || rec.signal_type || '',
      rec.action || '', rec.snapshot_close != null ? rec.snapshot_close : '',
      rec.deduped ? '是' : '否',
      pct(5), pct(10), pct(20), pct(60),
    ].map(_csvCell).join(','));
  });
  // UTF-8 BOM：保证 Excel 直接打开中文不乱码
  _downloadText(_journalExportStem() + '.csv', '\uFEFF' + lines.join('\r\n'), 'text/csv;charset=utf-8');
}

function exportJournalJson() {
  const records = window._journalLastRecords || [];
  if (!records.length) { alert('当前过滤条件下暂无记录可导出'); return; }
  _downloadText(_journalExportStem() + '.json', JSON.stringify(records, null, 2), 'application/json;charset=utf-8');
}

// ===== 核心池管理（可视化维护，变更自动递增池版本） =====
async function poolPost(body) {
  const r = await fetch(`${API}/api/pool`, {
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

async function loadPool() {
  const el = document.getElementById('wp-content-pool');
  el.innerHTML = '<div class="wp-ov-loading">正在读取核心池...</div>';
  let data;
  try {
    const r = await fetch(`${API}/api/pool`);
    data = await r.json();
  } catch (e) {
    el.innerHTML = '<div class="wp-error" style="padding:16px;color:#e57373;font-size:12px">核心池读取失败：' + escHtml(String(e)) + '</div>';
    return;
  }
  _poolLastData = data;
  // 快照同步状态（I7.5 失效提示闭环）
  _poolSnapBanner = '<div style="padding:6px 12px;font-size:11px;color:#888;border-bottom:1px solid #222">未找到历史统计快照——可运行 python -m backtest snapshot 生成</div>';
  try {
    const sr = await fetch(`${API}/api/snapshot-info`);
    const snap = await sr.json();
    if (snap.snapshot_id) {
      if (snap.pool_version === data.version) {
        _poolSnapBanner = `<div style="padding:6px 12px;font-size:11px;color:#81c784;border-bottom:1px solid #222">✓ 快照与核心池同步（${escHtml(snap.snapshot_id)}，基于 v${snap.pool_version}）</div>`;
      } else {
        _poolSnapBanner = `<div style="padding:6px 12px;font-size:11px;color:#ffd54f;background:#3a3320;border-bottom:1px solid #222">⚠ 核心池已更新（当前 v${data.version}），最新快照基于 v${snap.pool_version}——建议重建快照：python -m backtest snapshot</div>`;
      }
    }
  } catch (e) { /* 快照信息不可用时保持引导文案 */ }
  renderPoolPanel();
}

function renderPoolPanel() {
  const el = document.getElementById('wp-content-pool');
  const data = _poolLastData || { version: 0, items: [] };
  const items = data.items || [];
  const cur = (typeof currentSymbol !== 'undefined' && currentSymbol) ? currentSymbol : '';
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
      <span style="color:#666;width:18px">${i + 1}</span>
      <a href="#" onclick="analyze('${it.symbol}');closePanel();return false" style="color:#ff9800;text-decoration:none;min-width:52px">${it.symbol}</a>
      <span style="min-width:80px;color:#ddd" title="${escHtml(it.industry || '')}">${escHtml(it.name || '--')}</span>
      ${it.industry ? `<span style="color:#666;font-size:10px;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(it.industry)}</span>` : ''}
      <input value="${escHtml(it.note || '')}" placeholder="备注" style="flex:1;background:#111;border:1px solid #2a2a2a;color:#bbb;font-size:11px;padding:2px 6px"
        onchange="poolNote('${it.symbol}', this.value)">
      <button onclick="poolMove('${it.symbol}', -1)" ${i === 0 ? 'disabled' : ''} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:0 6px">↑</button>
      <button onclick="poolMove('${it.symbol}', 1)" ${i === visible.length - 1 ? 'disabled' : ''} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:0 6px">↓</button>
      <button onclick="poolRemove('${it.symbol}')" style="background:none;border:1px solid #5a2a2a;color:#e57373;cursor:pointer;padding:0 6px">删</button>
    </div>`).join('');

  el.innerHTML = `
    <div style="display:flex;gap:10px;padding:8px 12px;border-bottom:1px solid #222;font-size:11px;color:#aaa;align-items:center;flex-wrap:wrap">
      <span>池版本 <b style="color:#ff9800">v${data.version}</b></span>
      <span>${countText}</span>
      <input id="pool-add-symbol" placeholder="代码 如 600519" size="9" style="background:#111;border:1px solid #333;color:#ccc;font-size:11px;padding:2px 6px">
      <input id="pool-add-name" placeholder="名称(可选)" size="8" style="background:#111;border:1px solid #333;color:#ccc;font-size:11px;padding:2px 6px">
      <button onclick="poolAdd()" style="background:#ff9800;border:none;color:#000;padding:2px 10px;cursor:pointer;font-size:11px">添加</button>
      <button onclick="poolAddCurrent('${cur}')" ${cur ? '' : 'disabled'} style="background:none;border:1px solid #333;color:#aaa;cursor:pointer;padding:2px 8px;font-size:11px">+ 当前(${cur || '无'})</button>
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
    ${rows || '<div style="padding:20px;color:#666;font-size:12px">核心池为空——手动输入代码添加、分析个股后点「+ 当前」加入，或用「批量导入」粘贴多行代码。<br>核心池将用于信号档案筛选与历史统计。</div>'}`;
}

async function poolAdd() {
  const symbolEl = document.getElementById('pool-add-symbol');
  const nameEl = document.getElementById('pool-add-name');
  const symbol = (symbolEl.value || '').trim();
  if (!symbol) return;
  const data = await poolPost({ action: 'add', symbol, name: (nameEl.value || '').trim() });
  if (!data.ok) alert(data.error || '添加失败');
  loadPool();
}

async function poolAddCurrent(symbol) {
  if (!symbol) return;
  const name = (typeof document.getElementById('stock-name') !== 'undefined'
    && document.getElementById('stock-name')) ? document.getElementById('stock-name').textContent.trim() : '';
  const data = await poolPost({ action: 'add', symbol, name });
  if (!data.ok) alert(data.error || '添加失败');
  loadPool();
}

async function poolRemove(symbol) {
  if (!confirm(`从核心池移除 ${symbol}？`)) return;
  const data = await poolPost({ action: 'remove', symbol });
  if (!data.ok) alert(data.error || '删除失败');
  loadPool();
}

async function poolNote(symbol, note) {
  const data = await poolPost({ action: 'note', symbol, note });
  if (!data.ok) alert(data.error || '备注保存失败');
  else loadPool();
}

async function poolMove(symbol, offset) {
  const data = await poolPost({ action: 'move', symbol, offset });
  if (!data.ok) alert(data.error || '移动失败');
  else loadPool();
}

// ===== 批量导入 / 行业补全（frontend-iteration） =====
function _infoToast(text) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.onclick = () => removeToast(toast);
  toast.innerHTML = `<div style="font-size:12px">${escHtml(text)}</div>`;
  container.appendChild(toast);
  setTimeout(() => removeToast(toast), 5000);
}

function togglePoolImport() {
  _poolImportOpen = !_poolImportOpen;
  renderPoolPanel();
}

async function poolImportSubmit() {
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

async function poolFillIndustry() {
  try {
    const data = await poolPost({ action: 'fill-industry' });
    if (!data.ok) alert(data.error || '行业补全失败');
    else _infoToast(data.filled ? `行业补全完成：更新 ${data.filled} 只` : '池内行业信息完整，无需补全');
    loadPool();
  } catch (e) { alert('行业补全失败：' + String(e)); }
}

// ===== 相邻查看方向一致率统计 =====
function getSignalRecords() {
  try { return JSON.parse(localStorage.getItem(STORAGE_SIGNALS) || '[]'); } catch(e) { return []; }
}
function saveSignalRecords(list) {
  try { localStorage.setItem(STORAGE_SIGNALS, JSON.stringify(list.slice(0, MAX_SIGNAL_RECORDS))); } catch(e) {}
}
function recordSignal(code, name, action, score, price) {
  const records = getSignalRecords();
  records.unshift({ code, name, action, score, price, time: Date.now() });
  saveSignalRecords(records);
}
function calcSignalAccuracy(code) {
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
function renderSignalAccuracy(code) {
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
function checkSignalChange(code, name, action, score, price) {
  const prev = _lastSignal[code];
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
  _lastSignal[code] = { action, score, price, time: Date.now() };
}

function showToast(name, code, oldAction, newAction, price) {
  const container = document.getElementById('toast-container');
  const cls = newAction === '买入' ? 'buy' : newAction === '卖出' ? 'sell' : '';
  const oldColor = oldAction === '买入' ? C.up : oldAction === '卖出' ? C.down : '#ffc107';
  const newColor = newAction === '买入' ? C.up : newAction === '卖出' ? C.down : '#ffc107';
  const toast = document.createElement('div');
  toast.className = `toast ${cls}`;
  toast.onclick = () => removeToast(toast);
  toast.innerHTML = `
    <div style="font-weight:bold;font-size:14px;margin-bottom:4px">${escHtml(name)} (${code})</div>
    <div>信号变更：<span style="color:${oldColor}">${oldAction}</span> → <span style="color:${newColor};font-weight:bold">${newAction}</span></div>
    <div style="font-size:11px;color:#666;margin-top:4px">当前价 ${price ? price.toFixed(2) : '--'}</div>
  `;
  container.appendChild(toast);
  setTimeout(() => removeToast(toast), 8000);
}

function removeToast(el) {
  if (!el || !el.parentNode) return;
  el.classList.add('removing');
  setTimeout(() => { if (el.parentNode) el.remove(); }, 300);
}

function clearWatchChangeBadge() {
  const badge = document.getElementById('watch-change');
  badge.textContent = '0';
  badge.style.display = 'none';
}

// ==================== 扫描功能（原独立 script 块） ====================
// ===== 扫描功能 =====
let _scanTimer = null;

// ---- 扫描结果归档（frontend迭代：本地留存最近30次，供回看/导出） ----
const STORAGE_SCAN_ARCHIVE = 'qs_scan_archive';
const MAX_SCAN_ARCHIVE = 30;

function getScanArchive() {
  try { return JSON.parse(localStorage.getItem(STORAGE_SCAN_ARCHIVE)) || []; } catch (e) { return []; }
}
function saveScanArchive(list) {
  try { localStorage.setItem(STORAGE_SCAN_ARCHIVE, JSON.stringify(list)); return true; }
  catch (e) { showToastMsg('归档失败：浏览器存储空间不足'); return false; }
}
// 幂等签名：同一次运行结果重复渲染（关弹窗后重开）不会重复归档
function _scanRunSig(results, elapsed) {
  const f = results[0] || {}, l = results[results.length - 1] || {};
  return `${results.length}|${elapsed}|${f.symbol || ''}|${l.symbol || ''}`;
}
function archiveScanRun(data) {
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

function openScan() {
  document.getElementById('scan-overlay').classList.add('show');
  // 先拉一次状态，再决定是显示进度还是启动新扫描
  fetch('/api/scan').then(r => r.json()).then(data => {
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

function closeScan(e) {
  if (e && e.target !== document.getElementById('scan-overlay')) return;
  document.getElementById('scan-overlay').classList.remove('show');
  stopScanPolling();
}

function renderScanIdle() {
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-empty">
      <div style="margin-bottom:16px;font-size:15px;color:#aaa">扫描全A股，找出日K和周K同时符合买入信号的股票</div>
      <div style="margin-bottom:8px;color:#666;font-size:13px">扫描范围：成交额前1000只活跃A股</div>
      <div style="margin-bottom:8px;color:#666;font-size:13px">筛选条件：日K买入 + 周K买入（双周期共振）</div>
      <div style="margin-bottom:20px;color:#666;font-size:13px">预计耗时：2-4分钟</div>
      <button class="scan-btn" style="font-size:15px;padding:8px 28px" onclick="startScan()">开始扫描</button>
    </div>`;
}

function startScan() {
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-progress-wrap">
      <div class="scan-stage">正在启动扫描...</div>
      <div class="scan-bar-bg"><div class="scan-bar-fill" style="width:0%"></div></div>
    </div>`;
  fetch('/api/scan?action=start').then(r => r.json()).then(data => {
    if (data.status === 'started' || data.status === 'running') {
      startScanPolling();
    }
  });
}

function startScanPolling() {
  stopScanPolling();
  _scanTimer = setInterval(() => {
    fetch('/api/scan').then(r => r.json()).then(data => {
      if (data.status === 'running') {
        renderScanProgress(data);
      } else if (data.status === 'done') {
        stopScanPolling();
        renderScanResults(data);
      } else if (data.status === 'error') {
        stopScanPolling();
        renderScanError(data);
      }
    }).catch(() => {});
  }, 2000);
}

function stopScanPolling() {
  if (_scanTimer) { clearInterval(_scanTimer); _scanTimer = null; }
}

function renderScanProgress(data) {
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

function renderScanResults(data) {
  const results = data.results || [];
  const elapsed = data.elapsed || 0;
  const archivedRun = archiveScanRun(data);   // 自动归档（幂等）
  if (!results.length) {
    document.getElementById('scan-content').innerHTML = `
      <div class="scan-empty">
        <div style="margin-bottom:12px;color:#aaa">扫描完成，未发现双周期买入信号</div>
        <div style="color:#666;font-size:13px">当前市场可能处于调整期，可稍后再试</div>
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
    <div style="margin-top:10px;color:#666;font-size:11px">本次结果已自动归档，关闭弹窗后仍可在「历史归档」中回看与导出。</div>`;
  document.getElementById('scan-content').innerHTML = html;
}

// 结果表格（实时结果与归档详情共用）
function _scanTableHtml(results) {
  let html = `
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
      <td>${r.symbol}</td>
      <td>${r.name}</td>
      <td style="color:${pctColor}">${r.price ? r.price.toFixed(2) : '-'}<span style="font-size:11px;color:#666"> ${pct}%</span></td>
      <td class="${dAct.cls}">${dAct.text}</td>
      <td>${r.daily_score}</td>
      <td class="${wAct.cls}">${wAct.text}</td>
      <td>${r.weekly_score}</td>
      <td class="scan-combined" style="color:#ff9800">${r.combined_score}</td>
      <td style="font-size:12px;color:#aaa">${r.position_advice ? r.position_advice.split('—')[0].trim() : '-'}</td>
      <td style="color:${(r.risk_reward||0) >= 2 ? '#00b35c' : (r.risk_reward||0) >= 1 ? '#ffc107' : '#ff2d2d'}">${r.risk_reward || '-'}</td>
      <td><button class="scan-analyze-btn" onclick="analyzeFromScan('${r.symbol}')">分析</button></td>
    </tr>`;
  });
  html += '</tbody></table>';
  return html;
}

// ---- 历史归档视图 ----
function _fmtScanTime(ts) {
  const d = new Date(ts);
  const p = n => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function renderScanArchiveList() {
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
          <button class="scan-analyze-btn" onclick="renderArchivedRun('${run.id}')">查看</button>
          <button class="scan-analyze-btn" onclick="exportScanCsv('${run.id}')">CSV</button>
          <button class="scan-analyze-btn scan-del-btn" onclick="deleteScanRun('${run.id}')">删除</button>
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

function renderArchivedRun(id) {
  const run = getScanArchive().find(r => r.id === id);
  if (!run) { renderScanArchiveList(); return; }
  const host = document.getElementById('sb-wide-scan');
  if (!host) return;
  host.innerHTML = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>归档 ${_fmtScanTime(run.finishedAt)}</span>
      <span>命中 <b style="color:#ff9800">${run.count}</b> 只 · 耗时 ${run.elapsed}s</span>
      <button class="scan-btn scan-btn-ghost" style="padding:3px 12px;font-size:12px" onclick="exportScanCsv('${run.id}')">导出 CSV</button>
      <button class="scan-btn" style="margin-left:auto;padding:3px 12px;font-size:12px" onclick="renderScanArchiveList()">返回列表</button>
    </div>
    ${run.count ? _scanTableHtml(run.items) : '<div class="scan-empty">该次扫描未发现双周期买入信号</div>'}
    <div style="margin-top:10px;color:#888;font-size:11px">⚠ 归档为扫描当时快照：价格/涨跌幅为当时数据，「分析」按最新行情重新计算。</div>`;
}

function exportScanCsv(id) {
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

function deleteScanRun(id) {
  if (!confirm('删除这条扫描归档？')) return;
  saveScanArchive(getScanArchive().filter(r => r.id !== id));
  renderScanArchiveList();
}

function clearScanArchive() {
  if (!confirm(`清空全部 ${getScanArchive().length} 条扫描归档？此操作不可恢复。`)) return;
  saveScanArchive([]);
  renderScanArchiveList();
}

function formatScanAction(act) {
  if (!act) return { text: '-', cls: 'scan-action-watch' };
  if (act.includes('强烈')) return { text: '强买', cls: 'scan-action-strong' };
  if (act.includes('买入') && !act.includes('谨慎')) return { text: '买入', cls: 'scan-action-buy' };
  if (act.includes('谨慎')) return { text: '谨慎', cls: 'scan-action-caution' };
  if (act.includes('卖出')) return { text: '卖出', cls: 'scan-action-watch' };
  return { text: '观望', cls: 'scan-action-watch' };
}

function analyzeFromScan(symbol) {
  closeScan();
  analyze(symbol);
}

function renderScanError(data) {
  document.getElementById('scan-content').innerHTML = `
    <div class="scan-empty">
      <div style="margin-bottom:12px;color:#ff4d4d">扫描失败</div>
      <div style="color:#888;font-size:13px">${data.error || '未知错误'}</div>
      <div style="margin-top:16px"><button class="scan-btn" onclick="renderScanIdle()">重试</button></div>
    </div>`;
}

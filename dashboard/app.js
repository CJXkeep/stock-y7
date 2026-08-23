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

  klineChart.setOption(opts);
  volumeChart.setOption(opts);
  flowChart.setOption(opts);
  minuteChart.setOption(opts);
  minuteVolChart.setOption(opts);
  indicatorChart.setOption(opts);

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
    animation: false,
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
      { type: 'inside', start: ds, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false },
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
    animation: false,
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
      { type: 'inside', start: ds, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', start: ds, end: 100, show: false },
    ],
  }, true);

  bindZoomSync();
  updateZoomInfo(ds, 100);
  renderIndicator(_currentIndicator);
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
function bindZoomSync() {
  if (_zoomBound) return;
  _zoomBound = true;
  klineChart.on('datazoom', () => {
    const dz = klineChart.getOption().dataZoom[0];
    if (dz) {
      volumeChart.dispatchAction({ type: 'dataZoom', start: dz.start, end: dz.end });
      indicatorChart.dispatchAction({ type: 'dataZoom', start: dz.start, end: dz.end });
      updateZoomInfo(dz.start, dz.end);
      syncRangeBtns(dz.start, dz.end);
    }
  });
}

function applyRange(days) {
  const total = _klineData.length;
  if (!total) return;
  let s, e;
  if (days === 0 || days >= total) { s = 0; e = 100; }
  else { s = Math.max(0, (1 - days / total) * 100); e = 100; }
  klineChart.dispatchAction({ type: 'dataZoom', start: s, end: e });
  volumeChart.dispatchAction({ type: 'dataZoom', start: s, end: e });
  indicatorChart.dispatchAction({ type: 'dataZoom', start: s, end: e });
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
    animation: false,
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
    animation: false,
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
    animation: false,
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
    animation: false,
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
    <div class="sum-action-row">
      <span class="sum-badge ${badgeClass}">${badgeText}</span>
      ${sText ? `<span class="sum-strength ${sClass}">${sText}</span>` : ''}
      <span class="sum-score-big" style="color:${scoreColor}">${score}分</span>
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
  sl.innerHTML = allSigs.map((s, idx) => {
    const isCore = idx < 2 && allSigs.length > 2;
    const coreTag = isCore ? '<span class="sig-core-tag">核心</span>' : '';
    if (s.type === 'buy') return `<div class="sig-item sig-buy">▲ ${s.text}${coreTag}</div>`;
    else return `<div class="sig-item sig-sell">▼ ${s.text}${coreTag}</div>`;
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
      saveWatchlist(_wl);
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
    series: [
      { name: 'K线', data: _klineData.map(k => [k.open, k.close, k.low, k.high]) },
      { name: 'MA5', data: ma5 },
      { name: 'MA10', data: ma10 },
      { name: 'MA20', data: ma20 },
      { name: 'MA60', data: ma60 },
    ]
  }, false);

  volumeChart.setOption({
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

// --- localStorage 读写 ---
function getWatchlist() {
  try { return JSON.parse(localStorage.getItem(STORAGE_WATCH) || '[]'); } catch(e) { return []; }
}
function saveWatchlist(list) {
  localStorage.setItem(STORAGE_WATCH, JSON.stringify(list));
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
  const list = getWatchlist();
  const idx = list.findIndex(s => s.code === currentSymbol);
  if (idx >= 0) {
    list.splice(idx, 1);
  } else {
    const name = _currentStockName || currentSymbol;
    list.unshift({ code: currentSymbol, name: name, addedAt: Date.now() });
  }
  saveWatchlist(list);
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
  const el = document.getElementById('wp-content-watch');
  const list = getWatchlist();
  if (!list.length) {
    el.innerHTML = '<div class="wp-empty"><span class="wp-empty-icon">☆</span>还没有自选股<br>分析股票后点击 ☆ 添加</div>';
    return;
  }
  el.innerHTML = list.map(s => {
    const tag = sigTag(s.action, s.score);
    return `<div class="wp-item" onclick="analyze('${s.code}');closePanel()">
      <span class="code">${s.code}</span>
      <span class="name">${escHtml(s.name)}</span>
      ${tag}
      <button class="remove" onclick="event.stopPropagation();removeFromWatchlist('${s.code}')" title="移除">×</button>
    </div>`;
  }).join('');
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
function closePanel() {
  document.getElementById('wp-panel').classList.remove('show');
  _panelOpen = false;
  document.getElementById('watch-btn').classList.remove('active');
  document.getElementById('history-btn').classList.remove('active');
}

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
  _currentTab = tab;
  // tab样式
  document.querySelectorAll('.wp-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  // 内容切换
  document.getElementById('wp-content-watch').style.display = tab === 'watch' ? 'block' : 'none';
  document.getElementById('wp-content-history').style.display = tab === 'history' ? 'block' : 'none';
  document.getElementById('wp-content-overview').style.display = tab === 'overview' ? 'block' : 'none';
  document.getElementById('wp-content-journal').style.display = tab === 'journal' ? 'block' : 'none';
  document.getElementById('wp-content-pool').style.display = tab === 'pool' ? 'block' : 'none';
  // 按钮高亮
  document.getElementById('watch-btn').classList.toggle('active', tab === 'watch');
  document.getElementById('history-btn').classList.toggle('active', tab === 'history');
  // 渲染内容
  if (tab === 'watch') renderWatchlist();
  else if (tab === 'history') renderHistory();
  else if (tab === 'overview') loadOverview();
  else if (tab === 'journal') loadJournal();
  else if (tab === 'pool') loadPool();
  // 底部操作栏
  const footer = document.getElementById('wp-footer');
  const list = tab === 'watch' ? getWatchlist() : tab === 'history' ? getHistory() : [];
  if (list.length > 0 && tab !== 'overview') {
    footer.style.display = 'flex';
    document.getElementById('wp-footer-info').textContent = tab === 'watch'
      ? `共 ${list.length} 只自选股`
      : `共 ${list.length} 条记录`;
    document.getElementById('wp-clear-btn').textContent = tab === 'watch' ? '清空自选' : '清空历史';
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

// ===== 启动 =====
initCharts();
updateBadges();
// 加载模式设置
loadMode();
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
  // 小白模式：折叠部分卡片
  if (mode === 'simple') {
    collapseCard('momentum', true);
    collapseCard('levels', true);
    collapseCard('chanlun-daily', true);
    collapseCard('chanlun-minute', true);
    collapseCard('accuracy', true);
    collapseCard('risk', true);
  } else {
    // 专业模式：全部展开
    document.querySelectorAll('.signal-card.collapsed, .chanlun-card.collapsed').forEach(c => c.classList.remove('collapsed'));
  }
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
    animation: false,
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
  if (!results.length) {
    document.getElementById('scan-content').innerHTML = `
      <div class="scan-empty">
        <div style="margin-bottom:12px;color:#aaa">扫描完成，未发现双周期买入信号</div>
        <div style="color:#666;font-size:13px">当前市场可能处于调整期，可稍后再试</div>
        <div style="margin-top:16px"><button class="scan-btn" onclick="renderScanIdle()">重新扫描</button></div>
      </div>`;
    return;
  }
  let html = `
    <div class="scan-stats" style="margin-bottom:12px">
      <span>扫描完成，耗时 <b style="color:#ddd">${elapsed}s</b></span>
      <span>双周期买入: <b style="color:#ff9800">${results.length}</b> 只</span>
      <button class="scan-btn" style="margin-left:auto;padding:3px 12px;font-size:12px" onclick="renderScanIdle()">重新扫描</button>
    </div>
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
  document.getElementById('scan-content').innerHTML = html;
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

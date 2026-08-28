// ==================== 图表层：K线/副图指标/分时/资金流/缠论叠加（improvements #13/#14） ====================
import { C, S } from './shared.js';
import { escHtml, glossarize } from './ui.js';
import { API, fetchWithTimeout } from './api.js';
import { analyze, fxEnabled, chartAnim, resetChartAnim, _initAuth, fmtVol } from './main.js';

// 模块内私有可变状态（仅本域读写，不入共享 S）
let klineChart, volumeChart, flowChart, minuteChart, minuteVolChart, indicatorChart;
let _zoomBound = false;
let _minuteData = null;
let _minuteChanlun = null;
let _minuteYRange = null;
let _currentIndicator = 'none';
export function initCharts() {
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

  _initAuth();  // web-auth：显示退出入口 / 未登录跳登录页

  bindChartTooltip();
}

export function switchView(view) {
  const prevView = S.currentView;
  S.currentView = view;
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
      if (S.currentSymbol) loadMinute(S.currentSymbol);
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
    if (S._dailyChanlun && !S._dailyChanlun.error) {
      document.getElementById('chanlun-daily-card').style.display = 'block';
    }
    setTimeout(() => {
      klineChart.resize();
      volumeChart.resize();
    }, 50);
    // 日K↔周K切换时重新分析
    if (prevView !== view && S.currentSymbol) {
      analyze(S.currentSymbol);
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
export function calcMA(data, period) {
  const result = new Array(data.length).fill(null);
  for (let i = period - 1; i < data.length; i++) {
    let sum = 0;
    for (let j = 0; j < period; j++) sum += data[i - j].close;
    result[i] = +(sum / period).toFixed(3);
  }
  return result;
}

// improvements #14：MA 序列缓存 + 增量尾值更新。
// refreshQuote 每 2s 只变动最后一根蜡烛，无需全量重算 O(n·period)，
// 仅按各周期重算尾部一个值（O(period)），图表结果不变。
export let _maState = null;   // { len, arr5, arr10, arr20, arr60 }
export function _maTail(arr, data, period) {
  const n = data.length;
  if (!arr || n < period) return calcMA(data, period);
  let sum = 0;
  for (let j = 0; j < period; j++) sum += data[n - 1 - j].close;
  arr[n - 1] = +(sum / period).toFixed(3);
  return arr;
}
export function _maSeriesFor(data) {
  if (_maState && _maState.ref === data && _maState.len === data.length) {
    return {
      ma5: _maTail(_maState.arr5, data, 5),
      ma10: _maTail(_maState.arr10, data, 10),
      ma20: _maTail(_maState.arr20, data, 20),
      ma60: _maTail(_maState.arr60, data, 60),
    };
  }
  const s = {
    ref: data,
    len: data.length,
    arr5: calcMA(data, 5), arr10: calcMA(data, 10),
    arr20: calcMA(data, 20), arr60: calcMA(data, 60),
  };
  _maState = s;
  return { ma5: s.arr5, ma10: s.arr10, ma20: s.arr20, ma60: s.arr60 };
}

// ===== K线图 =====
export function renderKline(klines, signal) {
  S._klineData = klines;
  S._lastSignalData = signal;
  const dates = klines.map(k => k.date);
  const ohlc = klines.map(k => [k.open, k.close, k.low, k.high]);
  const { ma5, ma10, ma20, ma60 } = _maSeriesFor(klines);   // improvements #14：统一走缓存

  // 买卖点标记——买入▲在K线下方，卖出▼在K线上方
  const markPoints = [];
  S._signalPoints = [];
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
          label: { show: true, formatter: b.system ? b.system + ' 买入' : '买入', fontSize: 11, fontWeight: 'bold', color: '#fff',
                   backgroundColor: C.up, padding: [2,4], borderRadius: 3, position: 'bottom' },
        });
        const sysPeriodBuy = (b.system || '').match(/(\d+)日/)?.[1] || '20';
        S._signalPoints.push({
          date: dateStr, price: markerY,
          title: `${b.system ? b.system + ' ' : ''}买入信号（海龟法则·做多）`,
          formula: `${b.system || '系统'}：突破${sysPeriodBuy}日最高点 ${b.breakout_price || b.channel_high}\n→ 入场 ${b.entry_price}，止损 ${b.stop_loss}（入场-2×N）`,
          desc: `${b.system || '系统'}：唐奇安通道做多。当股价突破过去${sysPeriodBuy}天的最高点时，触发买入信号。\nN值=${b.current_n || '?'}（ATR，反映日均波动幅度）。\n入场后止损价=入场价-2×N，跌破止损或触及反向通道退出。`,
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
          label: { show: true, formatter: b.system ? b.system + ' 卖出' : '卖出', fontSize: 11, fontWeight: 'bold', color: '#fff',
                   backgroundColor: C.down, padding: [2,4], borderRadius: 3, position: 'top' },
        });
        const sysPeriodSell = (b.system || '').match(/(\d+)日/)?.[1] || '20';
        S._signalPoints.push({
          date: dates[idx], price: markerY,
          title: `${b.system ? b.system + ' ' : ''}卖出信号（海龟法则·做空）`,
          formula: `${b.system || '系统'}：跌破${sysPeriodSell}日最低点 ${b.breakout_price || b.channel_low}\n→ 入场 ${b.entry_price}，止损 ${b.stop_loss}（入场+2×N）`,
          desc: `${b.system || '系统'}：唐奇安通道做空。当股价跌破过去${sysPeriodSell}天的最低点时，触发做空信号。\nN值=${b.current_n || '?'}（ATR，反映日均波动幅度）。\n做空止损价=入场价+2×N，涨到止损或触及反向通道平仓。`,
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
  S._signalLines = [];
  if (signal.breakouts) {
    for (const b of signal.breakouts) {
      const sysName = b.system || '';
      const sysPeriod = (sysName.match(/(\d+)日/) || [null, '20'])[1] || '20';
      const sysLabel = sysName ? sysName + ' ' : '';
      if (b.stop_loss && b.stop_loss > 0) {
        const slLabel = isBearish
          ? `${sysLabel}止损 ${b.stop_loss.toFixed(2)}\n涨到这里就止损`
          : `${sysLabel}止损 ${b.stop_loss.toFixed(2)}\n跌到这里就卖`;
        markLines.push({ yAxis: b.stop_loss, lineStyle: { color: C.down, type: 'dashed', width: 2 },
          label: { formatter: slLabel, color: '#fff', fontSize: 11, fontWeight: 'bold',
            backgroundColor: C.down, padding: [3,6], borderRadius: 3, position: 'insideStartTop' } });
        const nVal = b.current_n || '?';
        S._signalLines.push({
          value: b.stop_loss,
          title: `${sysLabel}${isBearish ? '做空止损价' : '做多止损价'}（海龟法则）`,
          formula: isBearish
            ? `${sysName || '系统'}：止损 = 入场价 + 2 × N = ${b.entry_price} + 2 × ${nVal} = ${b.stop_loss}`
            : `${sysName || '系统'}：止损 = 入场价 - 2 × N = ${b.entry_price} - 2 × ${nVal} = ${b.stop_loss}`,
          desc: isBearish
            ? `${sysName || '系统'}：海龟法则2N止损。N=${nVal}（ATR平均真实波幅，反映股票日均波动幅度）。\n做空止损在入场价上方：如果股价反弹到这里，说明判断错了，认亏平仓。`
            : `${sysName || '系统'}：海龟法则2N止损。N=${nVal}（ATR平均真实波幅，反映股票日均波动幅度）。\n做多止损在入场价下方：如果股价跌到这里，说明判断错了，认亏卖出。`,
        });
      }
      // 持仓也要显示入场价，方便知道成本位置
      if (b.entry_price && b.entry_price > 0 && b.signal !== '观望') {
        const entryColor = isBearish ? C.down : C.up;
        const entryText = isBearish
          ? `${sysName ? sysName + '\n' : ''}做空 ${b.entry_price.toFixed(2)}`
          : `${sysName ? sysName + '\n' : ''}入场 ${b.entry_price.toFixed(2)}`;
        markLines.push({ yAxis: b.entry_price, lineStyle: { color: entryColor, type: 'solid', width: 2 },
          label: { formatter: entryText, color: '#fff', fontSize: 11, fontWeight: 'bold',
            backgroundColor: entryColor, padding: [3,6], borderRadius: 3, position: 'insideStartTop' } });
        S._signalLines.push({
          value: b.entry_price,
          title: `${sysLabel}${isBearish ? '做空入场价' : '做多入场价'}（海龟法则）`,
          formula: isBearish
            ? `${sysName || '系统'}：股价跌破${sysPeriod}日最低点 → 做空入场 ${b.entry_price}\n当时通道下轨=${b.channel_low}，上轨=${b.channel_high}`
            : `${sysName || '系统'}：股价突破${sysPeriod}日最高点 → 做多入场 ${b.entry_price}\n当时通道上轨=${b.channel_high}，下轨=${b.channel_low}`,
          desc: isBearish
            ? `${sysName || '系统'}：唐奇安通道做空。当股价跌破过去${sysPeriod}天的最低点时，触发做空信号。\n入场价=突破时的通道下轨。N值=${b.current_n || '?'}。已持有对应天数，止损价在上方。`
            : `${sysName || '系统'}：唐奇安通道做多。当股价突破过去${sysPeriod}天的最高点时，触发做多信号。\n入场价=突破时的通道上轨。N值=${b.current_n || '?'}。已持有对应天数，止损价在下方。`,
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
        S._signalLines.push({
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
export function bindBoxZoom() {
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
    const total = S._klineData.length;
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

export function findEntryIndex(klines, entryPrice) {
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
export function bindZoomSync() {
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

export function applyRange(days) {
  const total = S._klineData.length;
  if (!total) return;
  let s, e;
  if (days === 0 || days >= total) { s = 0; e = 100; }
  else { s = Math.max(0, (1 - days / total) * 100); e = 100; }
  klineChart.dispatchAction({ type: 'dataZoom', start: s, end: e });   // connect 自动带到量/副图
  updateZoomInfo(s, e);
}

// ===== 鼠标悬浮信号线/标记点 → 显示计算说明 =====
export function bindChartTooltip() {
  let _tooltipBound = false;
  if (_tooltipBound) return;
  _tooltipBound = true;

  const tooltipEl = document.getElementById('signal-tooltip');
  const chartDom = document.getElementById('kline-chart');

  // 用ZRender的mousemove获取像素坐标，再转换为数据值，检测是否靠近信号线/标记点
  let _tipRaf = null;   // improvements #14：rAF 节流，高频 mousemove 每帧至多处理一次
  klineChart.getZr().on('mousemove', function(e) {
    if (_tipRaf) return;
    _tipRaf = requestAnimationFrame(() => {
      _tipRaf = null;
      _tooltipProbe(e);
    });
  });

  function _tooltipProbe(e) {
    if (!S._signalLines.length && !S._signalPoints.length) {
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
    for (const line of S._signalLines) {
      if (line.value > 0 && Math.abs(yVal - line.value) / line.value < 0.012) {
        found = line;
        break;
      }
    }

    // 2. 没找到水平线 → 检测是否靠近标记点（买卖信号）
    if (!found && S._signalPoints.length) {
      let xIdx;
      try {
        xIdx = Math.round(klineChart.convertFromPixel({ xAxisIndex: 0 }, e.offsetX));
      } catch(err) {}

      if (xIdx != null && !isNaN(xIdx)) {
        for (const pt of S._signalPoints) {
          const idx = S._klineData.findIndex(k => k.date === pt.date);
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
        '<div class="stt-title">' + escHtml(found.title) + '</div>' +
        (found.formula ? '<div class="stt-formula">' + escHtml(found.formula) + '</div>' : '') +
        (found.desc ? '<div class="stt-desc">' + escHtml(found.desc) + '</div>' : '');
      tooltipEl.style.display = 'block';
    } else {
      tooltipEl.style.display = 'none';
    }
  }

  klineChart.getZr().on('mouseout', function() {
    tooltipEl.style.display = 'none';
  });
}

export function updateZoomInfo(start, end) {
  const total = S._klineData.length;
  if (!total) return;
  const si = Math.floor(start / 100 * total);
  const ei = Math.min(total - 1, Math.floor(end / 100 * total));
  const sd = S._klineData[si]?.date || '';
  const ed = S._klineData[ei]?.date || '';
  document.getElementById('zoom-info').textContent = `${sd} ~ ${ed} (${ei - si + 1}根)`;
}

export function syncRangeBtns(start, end) {
  const total = S._klineData.length;
  if (!total) return;
  const days = Math.round((end - start) / 100 * total);
  // 单一高亮：取满足条件的最大正向档位；「全部」仅在无正向档位匹配时高亮
  let best = 0;
  document.querySelectorAll('.tb-btn[data-range]').forEach(b => {
    const r = parseInt(b.dataset.range);
    if (r > 0 && end > 99 && Math.abs(days - r) < 5 && r > best) best = r;
  });
  document.querySelectorAll('.tb-btn[data-range]').forEach(b => {
    const r = parseInt(b.dataset.range);
    if (r === 0) b.classList.toggle('active', start === 0 && end === 100 && best === 0);
    else b.classList.toggle('active', r === best);
  });
}

// ===== 缠论分时分析面板 =====
export function renderChanlun(data) {
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
export function renderChanlunDaily(data) {
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
  const currentPrice = data.current_price || (S._klineData.length ? S._klineData[S._klineData.length - 1].close : null);

  let html = '';

  // 白话总结（小白模式显示）
  let plainSummary = '';
  const zsLabel = S.currentView === 'week' ? '缠论周线' : '缠论日线';
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
    html += '<div style="margin-top:6px;font-size:11px;color:#888;border-top:1px solid #0d0d0d;padding-top:4px">中枢列表</div>';
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
    <span>${S.currentView === 'week' ? '周K' : '日K'}: <b style="color:#ddd">${data.kline_count}</b></span>
    <span>合并: <b style="color:#ddd">${data.merged_count}</b></span>
    <span>分型: <b style="color:#ddd">${data.fractal_count}</b></span>
    <span>笔: <b style="color:#ddd">${data.stroke_count}</b></span>
    <span>中枢: <b style="color:#ddd">${data.zhongshu_count}</b></span>
    <span>信号: <b style="color:#ddd">${signals.length}</b></span>
  </div>`;

  el.innerHTML = html;
}

// ===== 日K线缠论图表叠加 =====
export function applyChanlunDailyOverlay(data) {
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
    const candle = S._klineData.find(k => k.date === sigDate);
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
    S._signalPoints.push({
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
export function renderMinute(data, chanlunData) {
  const pre = data.pre_close;
  const prices = data.prices;
  const avgs = data.avg_prices;
  const times = data.times;
  const vols = data.volumes;

  // 固定Y轴范围：用昨收价±10%（或±20%），防止跳动
  // 创业板/科创板/北交所 = ±20%，其他 = ±10%
  if (!_minuteYRange || _minuteYRange.pre !== pre) {
    const is20pct = S.currentSymbol && (
      S.currentSymbol.startsWith('300') || S.currentSymbol.startsWith('688') ||
      S.currentSymbol.startsWith('920') || S.currentSymbol.startsWith('8')
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
export function renderFlow(flows) {
  S._dailyFlows = flows;  // 缓存日级数据，供模式切换时使用
  if (S._flowMode === 'realtime') {
    // 实时模式：不渲染日级图表，由loadRealtimeFlow处理
    loadRealtimeFlow(S.currentSymbol);
    return;
  }
  _renderDailyFlow(flows);
}

export function _renderDailyFlow(flows) {
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
export function switchFlowMode(mode) {
  S._flowMode = mode;
  // 更新按钮状态
  document.getElementById('ft-rt').classList.toggle('ft-active', mode === 'realtime');
  document.getElementById('ft-daily').classList.toggle('ft-active', mode === 'daily');
  if (mode === 'realtime') {
    loadRealtimeFlow(S.currentSymbol);
  } else {
    _renderDailyFlow(S._dailyFlows);
  }
}

export async function loadRealtimeFlow(symbol) {
  if (!symbol) return;
  try {
    const r = await fetchWithTimeout(`${API}/api/realtime_flow?symbol=${symbol}`);
    const data = await r.json();
    renderRealtimeFlow(data);
  } catch(e) {
    const fs = document.getElementById('flow-summary');
    fs.textContent = '实时资金流获取失败';
  }
}

export function renderRealtimeFlow(data) {
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

export function _lastMA(period) {
  try {
    if (!S._klineData || S._klineData.length < period) return null;
    const arr = calcMA(S._klineData, period);
    const v = arr && arr.length ? arr[arr.length - 1] : null;
    return (v == null || isNaN(v)) ? null : v;
  } catch (e) { return null; }
}


export function refreshKlineLastCandle(q) {
  if (!S._klineData.length || !q || !q.price) return;
  // 周K视图不实时刷新最后一根蜡烛（周K是聚合数据，实时更新会误导）
  if (S.currentView === 'week') return;
  const last = S._klineData[S._klineData.length - 1];
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  if (last.date !== today) return; // 非当日不更新

  last.close = q.price;
  last.high = Math.max(last.high, q.high || q.price);
  last.low = Math.min(last.low, q.low || q.price);
  if (q.volume) last.volume = q.volume;
  if (q.amount) last.amount = q.amount;

  const { ma5, ma10, ma20, ma60 } = _maSeriesFor(S._klineData);   // improvements #14：增量更新

  klineChart.setOption({
    animation: false,   // 轮询刷新永远无动画（护栏）
    series: [
      { name: 'K线', data: S._klineData.map(k => [k.open, k.close, k.low, k.high]) },
      { name: 'MA5', data: ma5 },
      { name: 'MA10', data: ma10 },
      { name: 'MA20', data: ma20 },
      { name: 'MA60', data: ma60 },
    ]
  }, false);

  volumeChart.setOption({
    animation: false,   // 轮询刷新永远无动画（护栏）
    series: [{
      data: S._klineData.map(k => ({
        value: k.volume,
        itemStyle: { color: k.close >= k.open ? C.up + '88' : C.down + '88' }
      }))
    }]
  }, false);
}

export async function loadMinute(symbol) {
  try {
    // 确保容器有尺寸（从hidden切过来时ECharts可能还是0x0）
    minuteChart.resize();
    minuteVolChart.resize();
    // 重置Y轴范围（切换股票时）
    _minuteYRange = null;
    // 并行获取分时数据和缠论分析
    const [minuteRes, chanlunRes] = await Promise.all([
      fetchWithTimeout(`${API}/api/minute?symbol=${symbol}`),
      fetchWithTimeout(`${API}/api/chanlun_minute?symbol=${symbol}`),
    ]);
    const data = await minuteRes.json();
    if (S.currentSymbol !== symbol) return;   // 用户已切走：丢弃旧股票分时响应（防分时图也被 A/B 交错）
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
export async function refreshMinuteLight(symbol) {
  if (S.currentSymbol !== symbol) return;   // 用户已切走：过期分时轻量刷新丢弃
  try {
    const r = await fetchWithTimeout(`${API}/api/minute?symbol=${symbol}`);
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

export function resizeAllChartsSafe() {
  [klineChart, volumeChart, flowChart, minuteChart, minuteVolChart, indicatorChart].forEach(c => { try { c && c.resize(); } catch (e) {} });
}

// ===== 技术指标计算与渲染 =====
export function switchIndicator(ind) {
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

export function clearBolloverlay() {
  if (!klineChart || !S._klineData.length) return;
  const opt = klineChart.getOption();
  const series = opt.series || [];
  // 如果有BOLL系列（超过5个系列：K线+MA5+MA10+MA20+MA60=5），重新渲染K线清除
  if (series.length > 5) {
    if (S._lastSignalData) {
      renderKline(S._klineData, S._lastSignalData);
      // 重新叠加缠论
      if (S._dailyChanlun && !S._dailyChanlun.error) {
        applyChanlunDailyOverlay(S._dailyChanlun);
      }
    }
  }
}

export function renderIndicator(ind) {
  if (ind === 'none' || !S._klineData.length) {
    indicatorChart.setOption({}, true);
    return;
  }
  const dates = S._klineData.map(k => k.date);
  const closes = S._klineData.map(k => k.close);
  const highs = S._klineData.map(k => k.high);
  const lows = S._klineData.map(k => k.low);
  const volumes = S._klineData.map(k => k.volume);

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
export function calcEMA(data, period) {
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
export function calcMACD(closes) {
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
export function calcRSI(closes, period) {
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
export function calcKDJ(highs, lows, closes, period = 9) {
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
export function calcBOLL(closes, period = 20, mult = 2) {
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
export function calcWR(highs, lows, closes, period) {
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



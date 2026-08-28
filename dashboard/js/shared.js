// ==================== 跨模块共享常量与状态（improvements #13） ====================
// C：ECharts 全局主题色（原 app.js 顶部常量，多模块引用）。
export const C = {
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

// S：跨模块共享的可变状态（原 app.js 顶层 let 声明集中托管；
// 各模块统一以 S.xxx 读写，保证单一事实来源）。
export const S = {
  currentSymbol: '',      // 当前分析标的
  _currentStockName: '',  // 当前标的显示名（updateQuote 写入，加自选/入池使用）
  currentView: 'dayk',    // dayk | week | minute
  _mode: 'pro',           // pro | simple（小白模式）
  _fxLevel: 'std',        // FX 动效档位
  _klineData: [],         // K线数据缓存
  _signalLines: [],       // 止损/入场/目标水平线
  _signalPoints: [],      // 买卖点标记
  _lastSignalData: null,  // 上次 signal 数据（模式切换免请求重渲染）
  _lastSignal: {},        // 各标的上次信号 { code: {action,...} }
  _dailyChanlun: null,    // 缠论日线分析缓存
  _dailyFlows: null,      // 日级资金流缓存
  _flowMode: 'realtime',  // realtime | daily
  _lastQuote: null,       // {code, q, ts} 当前股 2s quote，供自选批量复用
};

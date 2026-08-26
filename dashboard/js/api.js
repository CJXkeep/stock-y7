// ==================== 网络层（improvements #3/#5/#13） ====================
export const API = '';

export const ERROR_EXPLAIN = {
  kline_empty: '没有找到该代码，可能输错了或已退市，试试搜索框输入名称',
  bad_symbol: '股票代码格式不对，请检查后重试',
  upstream_error: '行情数据源暂时连不上，稍后再试',
};
export const GENERIC_ERROR_TEXT = '分析遇到问题，请稍后重试；若持续出现请查看服务日志';

export function explainError(data) {
  if (!data) return GENERIC_ERROR_TEXT;
  if (data.error_code && ERROR_EXPLAIN[data.error_code]) return ERROR_EXPLAIN[data.error_code];
  // 兼容旧版纯文本错误：按关键词归类
  if (data.error && /K线数据不足|无效代码|不存在/.test(data.error)) return ERROR_EXPLAIN.kline_empty;
  return GENERIC_ERROR_TEXT;
}


export const DEFAULT_FETCH_TIMEOUT = 15000;
export function fetchWithTimeout(url, options = {}, ms = DEFAULT_FETCH_TIMEOUT) {
  const ctl = ('AbortController' in window) ? new AbortController() : null;
  if (!ctl) return fetch(url, options);   // 极旧环境降级为无超时请求
  const timer = setTimeout(() => ctl.abort(), ms);
  return fetch(url, { ...options, signal: ctl.signal }).finally(() => clearTimeout(timer));
}
export function isTimeoutError(err) {
  return !!(err && (err.name === 'AbortError' || err.isTimeout));
}

// --- 面板控制 ---
// （审查P2-4）面板常驻左侧工作台后，旧的弹出层开合空壳已随本次清理移除；
// 模板中的条目点击只保留 analyze 跳转。

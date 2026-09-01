// ==================== 模拟账户大页（sim.html 入口） ====================
// 复用 js/sim.js 的渲染与交互逻辑（页面使用相同的元素 ID），
// 这里只负责：挂载 window 处理器、启动加载与 30 秒自动刷新、窗口 resize。
import { loadSimPanel, saveSimConfig, runSimOnce, resetSimAccount, simBuy, simSell, simBuyPrompt, onSimResize } from './sim.js';

Object.assign(window, {
  saveSimConfig, runSimOnce, resetSimAccount,
  simBuyPrompt, simBuy, simSell,
});

loadSimPanel();
const _spTimer = setInterval(loadSimPanel, 30000);   // 大页 30 秒自动刷新
window.addEventListener('resize', onSimResize);

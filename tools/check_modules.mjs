// 开发期模块链接检查器：用万能 Proxy 桩替代浏览器全局，
// 动态 import 入口模块 → ESM 解析/链接阶段的错误会真实抛出
// （缺失导出名、循环初始化等），执行期 DOM 相关错误仅告警。
const mkStub = () => new Proxy(function () {}, {
  get(t, p) {
    if (p === Symbol.toPrimitive) return () => '';
    if (p === 'then') return undefined;
    if (p === Symbol.toStringTag) return 'Stub';
    return mkStub();
  },
  apply() { return mkStub(); },
  construct() { return mkStub(); },
  set() { return true; },
  has() { return true; },
});
globalThis.window = mkStub();
globalThis.document = mkStub();
globalThis.echarts = mkStub();   // vendor 经典脚本注入的全局
globalThis.history = mkStub();
globalThis.localStorage = mkStub();
globalThis.sessionStorage = mkStub();
globalThis.location = mkStub();
try { Object.defineProperty(globalThis, 'navigator', { value: mkStub(), configurable: true, writable: true }); } catch {}
for (const [k, v] of Object.entries({ window: mkStub(), document: mkStub(), localStorage: mkStub(), sessionStorage: mkStub(), location: mkStub() })) {
  try { globalThis[k] = v; } catch { try { Object.defineProperty(globalThis, k, { value: v, configurable: true }); } catch {} }
}

try {
  await import('../dashboard/js/main.js');
  console.log('MODULE LINK OK');
} catch (e) {
  const msg = String((e && e.message) || e);
  console.error('MODULE FAIL:', msg);
  process.exit(1);
}

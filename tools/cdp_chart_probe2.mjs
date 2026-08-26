// CDP 图表交互探针 v2：真实输入事件（Input.dispatchMouseEvent，含命中测试）。
// 流程：加载页 → 持久关闭新手引导 → 刷新 → 分析股票 → 真实滚轮/拖拽/滑条测试。
// 用法：node tools/cdp_chart_probe2.mjs <页面URL> <cdp端口>
import process from 'node:process';

const PAGE_URL = process.argv[2] || 'http://127.0.0.1:8795/';
const CDP_PORT = process.argv[3] || '9333';

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getTargetWs() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      const page = list.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch (e) { /* 未就绪 */ }
    await sleep(500);
  }
  throw new Error('找不到 CDP page target');
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const exceptions = [];
    ws.onopen = () => resolve({
      send(method, params = {}) {
        return new Promise((res, rej) => {
          const mid = ++id;
          pending.set(mid, { res, rej });
          ws.send(JSON.stringify({ id: mid, method, params }));
        });
      },
      exceptions,
      close() { ws.close(); },
    });
    ws.onerror = e => reject(new Error('WS错误: ' + (e.message || 'unknown')));
    ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      } else if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails;
        exceptions.push((d.exception?.description || d.text || '').slice(0, 300));
      }
    };
  });
}

async function evalJS(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (r.exceptionDetails) {
    return { __err: (r.exceptionDetails.exception?.description || r.exceptionDetails.text || '').slice(0, 400) };
  }
  return r.result?.value;
}

const SETUP = `
(async () => {
  localStorage.setItem('qs_onboarded_v1', '1');
  const ov = document.getElementById('onboard-overlay');
  const wasShown = ov && getComputedStyle(ov).display !== 'none';
  if (ov) ov.style.display = 'none';
  return { 引导浮层首访弹出: !!wasShown };
})()
`;

const LOAD_AND_ANALYZE = `
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  try {
    const m = await import('/js/main.js');
    await m.analyze('600000');
  } catch (e) { return { fatal: 'analyze抛错: ' + String(e) }; }
  const dom = document.getElementById('kline-chart');
  let inst = null;
  for (let i = 0; i < 60; i++) {
    await sleep(250);
    inst = window.echarts ? echarts.getInstanceByDom(dom) : null;
    if (inst) {
      const k = (inst.getOption().series || []).find(x => x.type === 'candlestick');
      if (k && k.data && k.data.length > 10) return { ok: true };
    }
  }
  return { fatal: 'K线数据未加载' };
})()
`;

const STATE = `
(() => {
  const dom = document.getElementById('kline-chart');
  const inst = echarts.getInstanceByDom(dom);
  const r = dom.getBoundingClientRect();
  const cx = Math.round(r.left + r.width / 2);
  const cy = Math.round(r.top + r.height * 0.4);
  const hit = document.elementFromPoint(cx, cy);
  return {
    rect: { left: r.left, top: r.top, w: r.width, h: r.height },
    centerHit: hit ? hit.tagName + '#' + (hit.id || '') + '.' + hit.className : null,
    dz: () => (inst.getOption().dataZoom || []).map(d => ({ t: d.type, s: d.start, e: d.end })),
    _inst: inst,
  };
})()
`;

const main = async () => {
  const wsUrl = await getTargetWs();
  const cdp = await connect(wsUrl);
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');

  // 视口设为桌面尺寸，避免移动端布局干扰
  await cdp.send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

  // ---- 第1次加载：关闭引导（记录它是否弹过） ----
  await cdp.send('Page.navigate', { url: PAGE_URL });
  await sleep(2500);
  const setupRes = await evalJS(cdp, SETUP);
  console.log('[setup]', JSON.stringify(setupRes));

  // ---- 刷新到干净状态再分析 ----
  await cdp.send('Page.navigate', { url: PAGE_URL });
  await sleep(2000);
  const loadRes = await evalJS(cdp, LOAD_AND_ANALYZE);
  console.log('[load+analyze]', JSON.stringify(loadRes));
  if (loadRes && loadRes.fatal) { cdp.close(); setTimeout(() => process.exit(1), 150); return; }

  const stData = await evalJS(cdp, `
    (() => {
      const inst = echarts.getInstanceByDom(document.getElementById('kline-chart'));
      const r = document.getElementById('kline-chart').getBoundingClientRect();
      const cx = Math.round(r.left + r.width / 2), cy = Math.round(r.top + r.height * 0.4);
      const hit = document.elementFromPoint(cx, cy);
      return {
        rect: {left: r.left, top: r.top, right: r.right, bottom: r.bottom, w: r.width, h: r.height},
        centerHit: hit ? hit.tagName + '#' + (hit.id||'') + '.' + hit.className : null,
        dzNow: (inst.getOption().dataZoom||[]).map(d=>({t:d.type,s:d.start,e:d.end})),
      };
    })()
  `);
  console.log('[chart状态]', JSON.stringify(stData));
  const { rect, dzNow } = stData;
  const cx = Math.round(rect.left + rect.w / 2);
  const cy = Math.round(rect.top + rect.h * 0.4);

  const dzSnap = label => evalJS(cdp, `
    (() => { const i = echarts.getInstanceByDom(document.getElementById('kline-chart'));
      return JSON.stringify((i.getOption().dataZoom||[]).map(d=>({t:d.type,s:d.start,e:d.end}))); })()
  `);

  // ---- 测试1：真实滚轮（向内缩放 ×4）----
  for (let i = 0; i < 4; i++) {
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseWheel', x: cx, y: cy, deltaX: 0, deltaY: -120,
    });
    await sleep(150);
  }
  await sleep(400);
  const wheelAfter = JSON.parse(await dzSnap());
  console.log('[滚轮后]', JSON.stringify(wheelAfter));
  const wheelOk = JSON.stringify(wheelAfter) !== JSON.stringify(dzNow);

  // ---- 测试2：双击复位（真实点击×2）----
  for (const t of [1, 2]) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cx, y: cy, button: 'left', buttons: 1, clickCount: t });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cx, y: cy, button: 'left', buttons: 0, clickCount: t });
    await sleep(120);
  }
  await sleep(500);
  const afterDbl = JSON.parse(await dzSnap());

  // ---- 测试3：真实拖拽框选 ----
  const x1 = cx - 100, x2 = cx + 100;
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: x1, y: cy, button: 'left', buttons: 1, clickCount: 1 });
  await sleep(80);
  const boxDuring = await evalJS(cdp, `!!document.querySelector('#kline-chart .zoom-box')`);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x2, y: cy, buttons: 1 });
  await sleep(80);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: x2, y: cy, button: 'left', buttons: 0 });
  await sleep(500);
  const afterDrag = JSON.parse(await dzSnap());
  const boxLeftover = await evalJS(cdp, `!!document.querySelector('#kline-chart .zoom-box')`);
  console.log('[框选中zoom-box]', boxDuring, '| 残留:', boxLeftover);
  console.log('[拖拽后]', JSON.stringify(afterDrag));
  const dragOk = JSON.stringify(afterDrag) !== JSON.stringify(afterDbl);

  // ---- 测试4：底部滑条拖动 ----
  let sliderOk = false;
  try {
    const sliderY = Math.round(rect.bottom - 22);   // slider 高28 bottom8
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: Math.round(rect.left + rect.w / 2), y: sliderY, button: 'left', buttons: 1, clickCount: 1 });
    await sleep(60);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(rect.left + rect.w / 2 - 120), y: sliderY, buttons: 1 });
    await sleep(60);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: Math.round(rect.left + rect.w / 2 - 120), y: sliderY, button: 'left', buttons: 0 });
    await sleep(500);
    const afterSlider = JSON.parse(await dzSnap());
    console.log('[滑条后]', JSON.stringify(afterSlider));
    sliderOk = JSON.stringify(afterSlider) !== JSON.stringify(afterDrag);
  } catch (e) { console.log('[滑条异常]', e.message); }

  console.log('==== 结论 ====');
  console.log(JSON.stringify({
    滚轮缩放生效: wheelOk,
    拖拽框选生效: dragOk,
    底部滑条生效: sliderOk,
    图表中心被谁挡住: stData.centerHit,
  }, null, 2));

  console.log('==== 运行时异常 (' + cdp.exceptions.length + ') ====');
  cdp.exceptions.slice(0, 8).forEach(e => console.log(' -', e));

  cdp.close();
  setTimeout(() => process.exit(0), 200);
};

main().catch(e => { console.error('PROBE_FAIL:', e.message); process.exit(1); });

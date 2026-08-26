// CDP UI 爬测探针：真实点击遍历看板主要交互面，逐步收集未捕获异常。
// 用法：node tools/ui_crawl_probe.mjs <页面URL> <cdp端口>
import process from 'node:process';
import fs from 'node:fs';

const PAGE_URL = process.argv[2] || 'http://127.0.0.1:8795/';
const CDP_PORT = process.argv[3] || '9333';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getTargetWs() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      const page = list.find(t2 => t2.type === 'page');
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
        exceptions.push((d.exception?.description || d.text || '').split('\n')[0].slice(0, 160));
      }
    };
  });
}

async function evalJS(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) return { __err: (r.exceptionDetails.exception?.description || '').slice(0, 300) };
  return r.result?.value;
}

const main = async () => {
  const wsUrl = await getTargetWs();
  const cdp = await connect(wsUrl);
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

  await cdp.send('Page.navigate', { url: PAGE_URL });
  await sleep(2500);
  await evalJS(cdp, `localStorage.setItem('qs_onboarded_v1','1');
    const ov = document.getElementById('onboard-overlay'); if (ov) ov.style.display='none';`);

  // 分析一只股票打底
  await evalJS(cdp, `(async () => { const m = await import('/js/main.js'); await m.analyze('600000'); })()`);
  for (let i = 0; i < 40; i++) { await sleep(250); }

  const clickEl = async (desc, finder, retries = 0) => {
    let r = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const before = cdp.exceptions.length;
      r = await evalJS(cdp, `
        (() => {
          try {
            const el = (${finder});
            if (!el) return { miss: true };
            el.scrollIntoView({ block: 'center' });
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), text: (el.textContent || '').slice(0, 24) };
          } catch (e) { return { err: String(e) }; }
        })()`);
      if (!r || r.miss || r.err) {
        if (attempt < retries) { await sleep(350); continue; }
        console.log(`[SKIP] ${desc}（找不到元素${r && r.err ? ': ' + r.err : ''}）`);
        return;
      }
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', buttons: 1, clickCount: 1 });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', buttons: 0 });
      await sleep(700);
      const added = cdp.exceptions.slice(before);
      console.log(`[${added.length ? 'FAIL' : 'OK'}] ${desc} (文本:${r.text})` + (added.length ? ' ← ' + added.join(' | ') : ''));
      return;
    }
  };

  // —— 顶栏与全局 ——
  await clickEl('打开设置面板', `document.getElementById('mt-settings')`);
  await clickEl('特效档位(setFx=标准)', `document.querySelector('.fx-opt[data-fx="std"]')`);
  await clickEl('关闭设置面板', `document.getElementById('mt-settings')`);
  await clickEl('小白/专业模式切换', `[...document.querySelectorAll('[data-act],[onclick]')].find(x=>/setMode|小白/.test((x.getAttribute('data-act')||'')+(x.title||'')+(x.textContent||'').slice(0,6)))`);
  await clickEl('侧栏开合', `document.getElementById('sb-toggle')`);

  // —— 侧边栏七分区 ——
  for (const sec of ['watch', 'history', 'overview', 'journal', 'pool', 'digest']) {
    await clickEl(`侧栏分区 ${sec}`, `document.querySelector('.sb-tab[data-sb="${sec}"]')`);
  }
  await clickEl('侧栏分区 scan', `document.querySelector('.sb-tab[data-sb="scan"]')`);

  // —— 视图切换 ——
  await clickEl('周线视图', `document.getElementById('view-week')`);
  await sleep(1200);
  await clickEl('分时视图', `document.getElementById('view-minute')`);
  await sleep(1200);
  await clickEl('日K视图', `document.getElementById('view-dayk')`);
  await sleep(800);

  // —— 副图指标与资金流 ——
  await clickEl('指标 MACD', `document.querySelector('.it-btn[data-ind="macd"]')`);
  await clickEl('指标 KDJ', `document.querySelector('.it-btn[data-ind="kdj"]')`);
  await clickEl('指标 关闭(副图)', `document.querySelector('.it-btn[data-ind="none"]')`);
  await clickEl('资金流 今日实时', `document.getElementById('ft-rt')`);
  await clickEl('资金流 近30日', `document.getElementById('ft-daily')`);

  // —— 扫描弹窗 ——
  await clickEl('打开扫描', `document.querySelector('[data-act="openScan"], #btn-scan') || [...document.querySelectorAll('*')].find(x=>x.getAttribute&&x.getAttribute('data-act')==='moreScan')`);
  await sleep(900);
  await clickEl('扫描范围下拉存在性', `document.getElementById('scan-topn')`, 3);
  await clickEl('关闭扫描(遮罩)', `document.getElementById('scan-overlay')`);
  await evalJS(cdp, `document.getElementById('scan-overlay')&&(document.getElementById('scan-overlay').classList.remove('show'));1`);

  // —— 自选星标（写本地+服务端，本地环境可接受） ——
  await clickEl('加自选星标', `document.getElementById('star-btn')`);

  console.log('==== 运行时异常总计:', cdp.exceptions.length, '====');
  cdp.exceptions.slice(0, 15).forEach(e => console.log(' -', e));
  try {
    fs.writeFileSync(new URL('../.comet/tmp/crawl-report.json', import.meta.url),
      JSON.stringify({ 总异常数: cdp.exceptions.length, 异常列表: cdp.exceptions.slice(0, 30) }, null, 2));
  } catch (e) { /* 报告写失败不影响退出码 */ }

  cdp.close();
  setTimeout(() => process.exit(cdp.exceptions.length ? 3 : 0), 200);
};

main().catch(e => { console.error('CRAWL_FAIL:', e.message); process.exit(1); });

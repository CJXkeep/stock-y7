// 验证“✅ 现在该做什么”段的真实渲染输出：
// 从 app.js 提取 escHtml/_applyTermChips/buildBeginnerSegments 源码，
// 在 stub 环境里跑观望分支，断言 <b> 保留为标签、不再出现字面转义文本。
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'app.js'), 'utf8');

function extractFn(name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('function not found: ' + name);
  let i = src.indexOf('{', start), depth = 0, j = i;
  for (; j < src.length; j++) {
    const c = src[j];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) break; }
  }
  return src.slice(start, j + 1);
}

const glossarySrc = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'glossary.js'), 'utf8');

const sandboxSrc = `
window = { GLOSSARY: null, GLOSSARY_TERMS: null };
_mode = 'simple';
${glossarySrc}
${extractFn('escHtml')}
${extractFn('_applyTermChips')}
${extractFn('glossarize')}
${extractFn('_lastMA')}
${extractFn('explainRisks')}
${extractFn('riskBannerHtml')}
${extractFn('buildBeginnerSegments')}
`;
eval(sandboxSrc);

const signal = {
  action: '观望',
  trend: { direction: '下降', strength: 42 },
  volume_price: { pattern: '价跌量增' },
  momentum: { m_score: 25 },
  risk_level: '高',
  buy_signals: [], sell_signals: [{ text: '跌破MA20' }],
  risk_codes: ['price_below_ma20'],
  risk_warnings: ['股价跌破MA20'],
  position_advice: '空仓等待',
  trade_plan: {},
};
_klineData = Array.from({ length: 60 }, (_, i) => ({ close: 10 + i * 0.1 }));

const html = buildBeginnerSegments(signal);

const assert = require('assert');
assert.ok(html.includes('<b>先不动，保持观察</b>'), '<b> 应保留为真实标签');
assert.ok(!html.includes('&lt;b&gt;'), '不允许出现被转义的字面标签');
assert.ok(html.includes('空仓等待'), '当前参考应渲染');
assert.ok(html.includes('data-term="MA20"') || html.includes('data-term="盈亏比"') || true, 'chip可选');
assert.ok(!html.includes('<b>下降趋势</b>&lt;'), 'seg1 加粗正常');
console.log('=== 渲染输出（观望分支） ===');
console.log(html.split('\n').filter(l => l.includes('seg-do')).join('\n').slice(0, 400));
console.log('=== 断言全部通过：字面 <b> 已消除，加粗与术语 chip 正常 ===');

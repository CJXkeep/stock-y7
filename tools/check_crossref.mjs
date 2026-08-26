// 开发期跨模块作用域检查：找出「引用了某模块导出名，但本模块既未导入也未声明」的标识符。
// 这类错误 node --check 与 ESM 链接都不报（运行时才 ReferenceError），是拆分最高危的回归面。
import fs from 'node:fs';
import path from 'node:path';

const dir = new URL('../dashboard/js/', import.meta.url).pathname.replace(/^\/([A-Za-z]):/, '$1:');
const files = fs.readdirSync(dir).filter(f => f.endsWith('.js'));

const BUILTIN = new Set((
  'window document localStorage sessionStorage location navigator history fetch echarts ' +
  'console JSON Math Object Array String Number Boolean Date RegExp Promise Error TypeError ' +
  'parseInt parseFloat isNaN encodeURIComponent decodeURIComponent setTimeout clearTimeout ' +
  'setInterval clearInterval requestAnimationFrame cancelAnimationFrame structuredClone ' +
  'globalThis undefined NaN Infinity arguments this super import meta require module exports process Buffer URL URLSearchParams AbortController FormData Blob FileReader CustomEvent Event KeyboardEvent Map Set Symbol Proxy Reflect'
).split(/\s+/));

// 收集每个模块：顶层声明名 + 导入名
function topLevelNames(src) {
  const names = new Set();
  const re = /^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)/gm;
  let m;
  while ((m = re.exec(src))) names.add(m[1]);
  return names;
}
function importedNames(src) {
  const names = new Set();
  const re = /import\s*\{([^}]*)\}\s*from/g;
  let m;
  while ((m = re.exec(src))) {
    for (let part of m[1].split(',')) {
      part = part.trim();
      if (!part) continue;
      const asMatch = part.match(/\s+as\s+([A-Za-z_$][\w$]*)\s*$/);
      names.add(asMatch ? asMatch[1] : part.split(/\s+as\s+/)[0].trim());
    }
  }
  // import * as X —— 单独扫
  const reNs = /import\s*\*\s*as\s+([A-Za-z_$][\w$]*)/g;
  while ((m = reNs.exec(src))) names.add(m[1]);
  return names;
}

const srcs = {};
for (const f of files) srcs[f] = fs.readFileSync(path.join(dir, f), 'utf8');

const allExports = new Set(); // 全项目导出名（含 S/C 等共享面）
for (const f of files) {
  const re = /^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)/gm;
  let m;
  while ((m = re.exec(srcs[f]))) allExports.add(m[1]);
}

// 去掉字符串/注释/模板插值外的标识符扫描（粗略但够用：先剥注释与字符串）
function stripNoise(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/'(?:\\.|[^'\\\n])*'/g, "''")
    .replace(/"(?:\\.|[^"\\\n])*"/g, '""')
    .replace(/`(?:\\.|[^`\\])*`/g, '``');
}

let problems = 0;
for (const f of files) {
  const local = topLevelNames(srcs[f]);
  const imports = importedNames(srcs[f]);
  const body = stripNoise(srcs[f])
    .replace(/^import[\s\S]*?from\s*[^\n]+/gm, '')   // 去掉 import 行本身
    .replace(/^export[\s\n]+/gm, '');
  // 标识符出现（排除属性访问 .name 与对象键 name: ）
  const idRe = /(?<![\w$.])([A-Za-z_$][\w$]*)\s*(?::)?/g;
  let m;
  const seen = new Set();
  while ((m = idRe.exec(body))) {
    const id = m[1];
    if (seen.has(id)) continue;
    seen.add(id);
    if (BUILTIN.has(id) || local.has(id) || imports.has(id)) continue;
    // 对象键（后跟冒号）或属性位置（前面是 . 或位于 {}. 解构）跳过
    const after = body.slice(m.index + m[0].length, m.index + m[0].length + 1);
    if (after === ':') continue;
    const before = body.slice(Math.max(0, m.index - 1), m.index);
    if (before === '.') continue;
    // 关键判定：该名字是其他模块的导出面 → 本模块漏 import
    if (allExports.has(id)) {
      console.log(`MISSING-IMPORT ${f}: ${id}`);
      problems++;
    }
  }
}
console.log(problems === 0 ? 'CROSSREF OK' : `${problems} problem(s)`);
process.exit(problems === 0 ? 0 : 1);

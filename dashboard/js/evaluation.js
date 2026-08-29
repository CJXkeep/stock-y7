// ==================== 评估与响应闭环只读入口（I8.6a） ====================
// 数据源：/api/evaluation（列表+状态）、/api/evaluation/summary（结构化摘要现算）、
// /api/evaluation/doc（markdown 原文）。只读展示：矫正/生成评估走 CLI（I8.6b/c 后续）。
// 动作注册约定：域模块只导出函数，evalPickSnapshot/evalOpenDoc 注册在 ui.js 的
// DELEGATED_ACTIONS 字面量（避免循环加载期对 const 的顶层赋值 TDZ）。
import { API, fetchWithTimeout, isTimeoutError } from './api.js';
import { escHtml } from './ui.js';

const EVAL_HOST_ID = 'wp-content-eval';
let _evalList = null;
let _evalPicked = null;

async function _get(url) {
  const res = await fetchWithTimeout(url, {}, 15000);
  const data = await res.json().catch(() => null);
  if (!res.ok || !data) throw new Error('请求失败：' + res.status);
  return data;
}

function _noticeHtml(notice) {
  if (!notice) return '';
  const rows = [];
  if (notice.thresholds) rows.push(escHtml(notice.thresholds));
  if (notice.dual_action) rows.push(escHtml(notice.dual_action));
  rows.push('分组 n<' + escHtml(String(notice.sample_min || 10)) + ' 标注「⚠样本不足」');
  if (notice.non_advice) rows.push(escHtml(notice.non_advice));
  return rows.map(t => '<div class="eval-notice-line">' + t + '</div>').join('');
}

function _fmt(v, suffix) {
  if (v === null || v === undefined) return '--';
  const num = typeof v === 'number' ? v.toFixed(2) : v;
  return num + (suffix || '');
}

function _cellHtml(block) {
  if (!block) return '--';
  let text = _fmt(block.win_rate, '%') + ' / ' + _fmt(block.avg_return, '%');
  if (block.insufficient_sample) text += ' ⚠样本不足';
  return escHtml(text);
}

function _statTableHtml(section, title) {
  const horizons = [5, 10, 20, 60];
  let html = '<div class="eval-card"><div class="eval-card-title">' + escHtml(title) + '</div>';
  html += '<table class="eval-table"><tr><th>分组</th><th>n</th>';
  for (const h of horizons) html += '<th>r' + h + ' 胜率/均值%</th>';
  html += '</tr>';
  for (const [name, block] of Object.entries(section || {})) {
    if (name === 'n') continue;
    html += '<tr><td>' + escHtml(name) + '</td><td>' + escHtml(String(block.n || 0)) + '</td>';
    for (const h of horizons) html += '<td>' + _cellHtml((block || {})['r' + h]) + '</td>';
    html += '</tr>';
  }
  html += '</table></div>';
  return html;
}

function _overviewHtml(data) {
  const th = data.effective_thresholds || {};
  const overall = data.overall || {};
  let html = '<div class="eval-card"><div class="eval-card-title">概览 · 快照 ' + escHtml(data.snapshot_id || '') + '</div>';
  html += '<div class="eval-notice-line">参与统计笔数：<b>' + escHtml(String(data.stats_count || 0)) + '</b>'
    + ' · 生效分档阈值：强=' + escHtml(String(th.th_strong || 75)) + ' / 买=' + escHtml(String(th.th_buy || 60))
    + (th.overridden ? '（params_override 覆盖生效）' : '')
    + ' · ' + (data.has_bench ? '超额口径可用' : '本轮无基准（仅绝对口径）') + '</div>';
  const section = { '总体': { ...overall, n: data.stats_count } };
  for (const [name, block] of Object.entries(data.by_action || {})) {
    if (name === 'n') continue;
    section[name] = block;
  }
  html += _statTableHtml(section, '绝对口径（胜率/均值%）');
  html += '</div>';
  return html;
}

function _excessTableHtml(data) {
  const horizons = [5, 10, 20, 60];
  const overall = data.overall || {};
  const byAction = data.by_action || {};
  let html = '<div class="eval-card"><div class="eval-card-title">超额表现（相对沪深300，win_rate=超额胜率）</div>';
  html += '<table class="eval-table"><tr><th>分组</th><th>n</th>';
  for (const h of horizons) html += '<th>r' + h + ' 超额胜率/均值%</th>';
  html += '</tr>';
  const rows = [['总体', { ...overall, n: data.stats_count }]];
  for (const [name, block] of Object.entries(byAction)) {
    if (name === 'n') continue;
    rows.push([name, block]);
  }
  for (const [name, block] of rows) {
    html += '<tr><td>' + escHtml(name) + '</td><td>' + escHtml(String(block.n || 0)) + '</td>';
    for (const h of horizons) html += '<td>' + _cellHtml((block || {})['r' + h + '_excess']) + '</td>';
    html += '</tr>';
  }
  html += '</table></div>';
  return html;
}

function _monoHtml(data) {
  const mono = data.mono || {};
  let html = '<div class="eval-card"><div class="eval-card-title">档位单调性（判据：'
    + (data.has_bench ? '超额均值' : '绝对均值·无基准') + '；只披露不判显著）</div>';
  for (const h of [5, 10, 20, 60]) {
    const block = mono['r' + h] || {};
    const tiers = (block.tiers || []).map(tr =>
      escHtml(tr.tier) + ' n=' + escHtml(String(tr.n)) + '，' + _fmt(tr.avg) + '%').join('；');
    html += '<div class="eval-notice-line">r' + h + '：' + (tiers || '--')
      + '　→　<b>' + escHtml(block.marker || '--') + '</b></div>';
  }
  html += '<div class="eval-notice-line">缺档说明：观望档无 forward return 样本不参与；谨慎买入仅存在于最终 action 口径（重放口径无此档）。</div>';
  html += '</div>';
  return html;
}

function _rulesHtml(data) {
  const rules = data.rules || {};
  let html = '<div class="eval-card"><div class="eval-card-title">响应规则状态（T1–T6，只呈现不执行）</div>';
  html += '<table class="eval-table"><tr><th>规则</th><th>状态</th><th>建议动作</th><th>依据</th></tr>';
  for (const rid of ['T1', 'T2', 'T3', 'T4', 'T5', 'T6']) {
    const rule = rules[rid] || {};
    const ev = rule.evidence || {};
    let basis = '';
    if (rid === 'T1') basis = '单调=' + _fmt(ev.mono_all ? '是' : '否') + '，新增=' + _fmt(ev.new_samples) + '/' + _fmt(ev.gate);
    else if (rid === 'T3') basis = '窗口 ' + _fmt(ev.window_n) + '/' + _fmt(ev.window_cap) + ' 笔，均值 ' + _fmt(ev.r60_excess_avg, '%');
    else if (rid === 'T4') basis = '近 ' + _fmt(ev.window_days) + ' 天 n=' + _fmt(ev.window_n) + '，均值 ' + _fmt(ev.r20_avg, '%');
    else if (rid === 'T5' || rid === 'T6') basis = '各档n=' + JSON.stringify(ev.tier_n || {});
    else basis = '标记=' + JSON.stringify(ev.markers || {});
    html += '<tr><td>' + rid + '</td><td><b>' + escHtml(rule.status || '--') + '</b></td><td>'
      + escHtml(rule.action || '--') + '</td><td class="eval-basis">' + escHtml(basis) + '</td></tr>';
  }
  html += '</table></div>';
  return html;
}

function _renderPicked(data) {
  const host = document.getElementById(EVAL_HOST_ID);
  if (!host) return;
  const picked = _evalPicked || {};
  const listHtml = _listHtml(data);
  let body = '<div class="eval-empty">选择上方快照查看结构化摘要。</div>';
  if (picked.summary) {
    body = _overviewHtml(picked.summary)
      + (picked.summary.has_bench ? _excessTableHtml(picked.summary) : '')
      + _monoHtml(picked.summary)
      + _rulesHtml(picked.summary);
  } else if (picked.error) {
    body = '<div class="eval-empty">' + escHtml(picked.error) + '</div>';
  }
  host.innerHTML = listHtml + '<div id="eval-picked">' + body + '</div>'
    + '<div id="eval-doc" class="eval-card" style="display:none">'
    + '<div class="eval-card-title" id="eval-doc-title"></div>'
    + '<pre class="eval-doc-pre" id="eval-doc-pre"></pre></div>';
  const docs = document.getElementById('eval-doc-list');
  if (docs) docs.style.display = picked.summary ? 'block' : 'none';
}

function _listHtml(data) {
  const results = (data && data.results) || [];
  let html = '<div class="eval-card"><div class="eval-card-title">评估结果目录</div>';
  html += _noticeHtml(data && data.notice);
  if (!results.length) {
    html += '<div class="eval-empty">暂无评估结果。先在 CLI 生成：<br>'
      + '<code>python -m backtest snapshot</code><br>'
      + '<code>python -m backtest replay &lt;id&gt; --workers 8</code><br>'
      + '<code>python -m backtest stats &lt;id&gt;</code><br>'
      + '<code>python -m backtest review &lt;id&gt;</code></div>';
  } else {
    html += '<div class="eval-snap-list">';
    for (const r of results) {
      const active = _evalPicked && _evalPicked.snapshot === r.snapshot_id;
      html += '<button class="eval-snap-item' + (active ? ' active' : '') + '" '
        + 'data-act="evalPickSnapshot" data-snapshot="' + escHtml(r.snapshot_id) + '">'
        + escHtml(r.snapshot_id) + ' · ' + escHtml(String(r.stats_count ?? '--')) + ' 笔'
        + ' · ' + escHtml(r.generated_at || '') + '</button>';
    }
    html += '</div>';
  }
  const usage = data && data.usage_state;
  if (usage && usage.flags && Object.keys(usage.flags).length) {
    const flags = Object.entries(usage.flags).map(([k, v]) => k + '=' + v).join('，');
    html += '<div class="eval-notice-line">当前使用方式矫正：' + escHtml(flags)
      + '（来源 ' + escHtml((usage.last_plan || {}).rule || '矫正计划') + '）</div>';
  }
  html += '</div>';
  if (results.length) {
    html += '<div class="eval-card" id="eval-doc-list" style="display:none">'
      + '<div class="eval-card-title">报告原文</div><div class="eval-doc-btns">'
      + '<button data-act="evalOpenDoc" data-kind="report">report.md</button>'
      + '<button data-act="evalOpenDoc" data-kind="sensitivity">sensitivity.md</button>'
      + '<button data-act="evalOpenDoc" data-kind="review">review.md</button>'
      + '</div></div>';
  }
  return html;
}

export async function loadEvaluation() {
  const host = document.getElementById(EVAL_HOST_ID);
  if (!host) return;
  host.innerHTML = '<div class="eval-empty">评估数据加载中…</div>';
  try {
    _evalList = await _get(API + '/api/evaluation');
    _renderPicked(_evalList);
  } catch (err) {
    host.innerHTML = '<div class="eval-empty">'
      + escHtml(isTimeoutError(err) ? '评估数据加载超时，稍后重试' : (err.message || '加载失败'))
      + '</div>';
  }
}

export async function pickSnapshot(el) {
  const snapshot = el && el.dataset && el.dataset.snapshot;
  if (!snapshot) return;
  _evalPicked = { snapshot, loading: true };
  const pickedEl = document.getElementById('eval-picked');
  if (pickedEl) pickedEl.innerHTML = '<div class="eval-empty">摘要计算中…</div>';
  try {
    const summary = await _get(API + '/api/evaluation/summary?snapshot='
      + encodeURIComponent(snapshot));
    _evalPicked = { snapshot, summary };
  } catch (err) {
    _evalPicked = { snapshot, error: err.message || '摘要加载失败' };
  }
  _renderPicked(_evalList);
}

export async function openDoc(el) {
  const kind = el && el.dataset && el.dataset.kind;
  const snapshot = _evalPicked && _evalPicked.snapshot;
  if (!kind || !snapshot) return;
  const docCard = document.getElementById('eval-doc');
  const docTitle = document.getElementById('eval-doc-title');
  const docPre = document.getElementById('eval-doc-pre');
  if (docCard) docCard.style.display = 'block';
  if (docTitle) docTitle.textContent = kind + '.md · ' + snapshot;
  if (docPre) docPre.textContent = '加载中…';
  try {
    const data = await _get(API + '/api/evaluation/doc?snapshot='
      + encodeURIComponent(snapshot) + '&kind=' + encodeURIComponent(kind));
    if (docPre) docPre.textContent = data.ok ? data.markdown : (data.error || '加载失败');
  } catch (err) {
    if (docPre) docPre.textContent = err.message || '加载失败';
  }
  if (docCard) docCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}


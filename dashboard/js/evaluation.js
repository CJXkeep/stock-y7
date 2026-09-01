// ==================== 评估与响应闭环入口（I8.6a 只读 + I8.6b 后台任务 + I8.6c 矫正） ====================
// 数据源：
//   GET  /api/evaluation                列表+状态+task（后台任务进度）+snapshots（可生成快照）
//   GET  /api/evaluation/summary        结构化摘要现算
//   GET  /api/evaluation/doc            report/sensitivity/review markdown 原文
//   POST /api/evaluation/refresh        后台跑 stats+review
//   POST /api/evaluation/sensitivity    后台跑 sensitivity（表单传阈值）
//   POST /api/correct/validate          矫正计划 dry-run 校验（只读目标）
//   POST /api/correct/execute           签字+二次确认执行
// 动作注册约定：域模块只导出函数，动作注册在 ui.js 的 DELEGATED_ACTIONS 字面量。
import { API, fetchWithTimeout, isTimeoutError } from './api.js';
import { escHtml, showToastMsg } from './ui.js';

const EVAL_HOST_ID = 'wp-content-eval';
let _evalList = null;
let _evalPicked = null;
let _evalPollTimer = null;

// ---------------------------------------------------------------- 网络

async function _get(url) {
  const res = await fetchWithTimeout(url, {}, 15000);
  const data = await res.json().catch(() => null);
  if (!res.ok || !data) throw new Error('请求失败：' + res.status);
  return data;
}

async function _post(url, body) {
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }, 20000);
  const data = await res.json().catch(() => null);
  if (!res.ok || !data) throw new Error('请求失败：' + res.status);
  return data;
}

// ---------------------------------------------------------------- 渲染工具

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

// ---------------------------------------------------------------- 历史趋势（I9.1 series）

function _seriesHtml(data) {
  const series = (data && data.series) || [];
  let html = '<div class="eval-card"><div class="eval-card-title">历史趋势（滚动评估 / 手动评估时间序列）</div>';
  html += '<div class="eval-notice-line">记录的是原始 run_analysis 输出统计口径，'
    + '与信号档案的最终 action 口径不可混用；n&lt;10 视界标 ⚠样本不足。</div>';
  if (!series.length) {
    html += '<div class="eval-empty">暂无历史期数。由月度滚动评估或手动「生成评估」累积，'
      + '≥2 期后此处展示逐期对比。</div>';
  } else {
    html += '<table class="eval-table"><tr><th>时间</th><th>来源</th><th>池版本</th><th>样本</th>'
      + '<th>r20胜率</th><th>r20均值</th><th>r20超额胜率</th><th>r20超额</th>'
      + '<th>r60胜率</th><th>r60均值</th><th>r60超额胜率</th><th>r60超额</th><th>触发规则</th></tr>';
    for (const row of series.slice().reverse()) {
      const o = row.overall || {};
      const r20 = o.r20 || {}, e20 = o.r20_excess || {};
      const r60 = o.r60 || {}, e60 = o.r60_excess || {};
      const triggers = (row.review_triggered || []).map(t => t.rule + ':' + t.status).join('，') || '--';
      html += '<tr><td>' + escHtml(row.created_at || '') + '</td>'
        + '<td>' + escHtml(row.source || '--') + '</td>'
        + '<td>' + escHtml(String(row.pool_version ?? '--')) + '</td>'
        + '<td>' + escHtml(String(row.sample_count ?? '--')) + '</td>'
        + '<td>' + _fmt(r20.win_rate, '%') + '</td><td>' + _fmt(r20.avg_return, '%') + '</td>'
        + '<td>' + _fmt(e20.excess_win_rate, '%') + '</td><td>' + _fmt(e20.excess_mean, '%') + '</td>'
        + '<td>' + _fmt(r60.win_rate, '%') + '</td><td>' + _fmt(r60.avg_return, '%') + '</td>'
        + '<td>' + _fmt(e60.excess_win_rate, '%') + '</td><td>' + _fmt(e60.excess_mean, '%') + '</td>'
        + '<td class="eval-basis">' + escHtml(triggers) + '</td></tr>';
    }
    html += '</table>';
  }
  html += '</div>';
  return html;
}

// ---------------------------------------------------------------- 后台任务状态

function _taskState() {
  return (_evalList && _evalList.task) || {};
}

function _renderTaskCard() {
  const el = document.getElementById('eval-task-status');
  if (!el) return;
  const t = _taskState();
  const statusMap = {
    idle: ['空闲', 'var(--c-text-2, #999)'],
    running: ['运行中', 'var(--c-info, #6aa)'],
    done: ['完成', 'var(--c-up, #4caf50)'],
    error: ['失败', 'var(--c-down, #ff6b6b)'],
  };
  const [label, color] = statusMap[t.status] || [t.status || '--', 'var(--c-text-2, #999)'];
  let html = '<div class="eval-card"><div class="eval-card-title">评估后台任务'
    + '（单进程互斥；stats/敏感性与扫描、速递共享「同时只跑一个」语义）</div>';
  html += '<div class="eval-notice-line">状态：<b style="color:' + color + '">' + escHtml(label) + '</b>'
    + (t.task ? ' · ' + escHtml(t.task) : '')
    + (t.snapshot ? ' · 快照 ' + escHtml(t.snapshot) : '')
    + '</div>';
  if (t.stage) html += '<div class="eval-notice-line">' + escHtml(t.stage) + '</div>';
  if (t.status === 'running') {
    html += '<div class="eval-progress"><div class="eval-progress-bar" style="width:' + Math.max(2, t.progress || 0) + '%"></div></div>';
    html += '<div class="eval-notice-line">进度 ' + escHtml(String(t.progress || 0)) + '%</div>';
  }
  if ((t.started_at && t.status === 'running') || t.error) {
    if (t.started_at) html += '<div class="eval-notice-line">开始于 ' + escHtml(t.started_at) + '</div>';
    if (t.elapsed !== undefined && t.elapsed !== null) html += '<div class="eval-notice-line">耗时 ' + escHtml(String(t.elapsed)) + ' 秒</div>';
  }
  if (t.error) html += '<div class="eval-error">' + escHtml(t.error) + '</div>';
  el.innerHTML = html;
  el.style.display = 'block';
}

function _applyTask(t) {
  _evalList = { ...(_evalList || {}), task: t };
  _renderTaskCard();
  if (t && t.status === 'running') _startPoll();
  else _stopPoll();
}

function _stopPoll() {
  if (_evalPollTimer) { clearInterval(_evalPollTimer); _evalPollTimer = null; }
}

function _startPoll() {
  _stopPoll();
  _evalPollTimer = setInterval(async () => {
    try {
      const data = await _get(API + '/api/evaluation');
      const t = (data && data.task) || {};
      if (t.status === 'running') {
        _evalList = data;
        _renderTaskCard();
      } else {
        _stopPoll();
        _evalList = data;
        _renderPicked(data);          // 完成/失败 → 整体刷新（结果目录随之更新）
        showToastMsg(t.status === 'done' ? '评估任务完成' : '评估任务失败，见详情');
      }
    } catch (e) {
      _stopPoll();
    }
  }, 1500);
}

// ---------------------------------------------------------------- 操作区（I8.6b/c）

function _defaultSnapshot() {
  const picked = _evalPicked && _evalPicked.snapshot;
  if (picked) return picked;
  const snaps = (_evalList && _evalList.snapshots) || [];
  return snaps[0] || '';
}

function _snapshotOptionsHtml() {
  const snaps = (_evalList && _evalList.snapshots) || [];
  const picked = _defaultSnapshot();
  if (!snaps.length) return '<option value="">（无可用快照）</option>';
  return snaps.map(s =>
    '<option value="' + escHtml(s) + '"' + (s === picked ? ' selected' : '') + '>'
    + escHtml(s) + '</option>').join('');
}

function _opsHtml() {
  const snaps = (_evalList && _evalList.snapshots) || [];
  let html = '<div class="eval-card"><div class="eval-card-title">评估产物操作区</div>';
  if (!snaps.length) {
    html += '<div class="eval-empty">暂无已重放的快照。请先在 CLI 生成：<br>'
      + '<code>python -m backtest snapshot --pool data/pool.json</code><br>'
      + '<code>python -m backtest replay &lt;id&gt; --workers 8</code>'
      + '，之后「生成评估/敏感性」即可用。</div>';
  } else {
    html += '<div class="eval-row"><label>快照：<select id="eval-snap-select">'
      + _snapshotOptionsHtml() + '</select></label></div>';
    html += '<div class="eval-row eval-ops-btns">'
      + '<button data-act="evalRefresh">生成评估（stats+review）</button>'
      + '<span class="eval-ops-hint">无前视重放基础上生成报告/results.csv 与 T1-T6 规则状态</span>'
      + '</div>';
    html += '<div class="eval-row eval-ops-btns">'
      + '<button data-act="evalSensitivity">敏感性对照（sensitivity.md）</button>'
      + '<input type="text" id="eval-thresholds" placeholder="75,60 85,80" class="eval-input" value="75,60" style="width:120px">'
      + '<span class="eval-ops-hint">分档阈值组（默认锚点 75,60）</span>'
      + '</div>';
    html += '<div class="eval-row eval-ops-btns">'
      + '<button data-act="evalCorrectToggle">矫正计划</button>'
      + '<span class="eval-ops-hint">先校验门槛（dry-run），全过才可签字执行；与 CLI correct 同一门槛</span>'
      + '</div>';
    html += '<div id="eval-correct"></div>';
  }
  html += '</div>';
  return html;
}

function _goSnap() {
  const sel = document.getElementById('eval-snap-select');
  return sel ? sel.value : _defaultSnapshot();
}

// 生成评估
export async function evalRefresh() {
  const snapshot = _goSnap();
  if (!snapshot) { showToastMsg('没有可用快照，先在 CLI 生成'); return; }
  try {
    const data = await _post(API + '/api/evaluation/refresh', { snapshot });
    if (!data.ok && data.error) { showToastMsg(data.error); return; }
    _applyTask(data);
    showToastMsg(data.message || '评估生成已启动');
  } catch (e) { showToastMsg(e.message || '启动失败'); }
}

// 敏感性对照
export async function evalSensitivity() {
  const snapshot = _goSnap();
  if (!snapshot) { showToastMsg('没有可用快照，先在 CLI 生成'); return; }
  const raw = (document.getElementById('eval-thresholds') || {}).value || '75,60';
  const thresholds = raw.split(/\s+/).filter(Boolean);
  try {
    const data = await _post(API + '/api/evaluation/sensitivity', { snapshot, thresholds });
    if (!data.ok && data.error) { showToastMsg(data.error); return; }
    _applyTask(data);
    showToastMsg(data.message || '敏感性对照已启动');
  } catch (e) { showToastMsg(e.message || '启动失败'); }
}

// 矫正计划表单
function _buildPlan() {
  const actionSel = document.getElementById('eval-correct-action');
  const action = actionSel ? actionSel.value : 'param_change';
  const payload = {};
  const rule = (document.getElementById('eval-correct-rule') || {}).value || 'T4';
  const snap = _goSnap() || ((document.getElementById('eval-correct-snap') || {}).value || '');
  const expectation = (document.getElementById('eval-correct-expectation') || {}).value || '';
  const reviewAt = (document.getElementById('eval-correct-review') || {}).value || '';

  if (action === 'param_change') {
    payload.th_strong = Math.round(Number((document.getElementById('ec-th-strong') || {}).value || 75));
    payload.th_buy = Math.round(Number((document.getElementById('ec-th-buy') || {}).value || 60));
  } else if (action === 'pool_add' || action === 'pool_remove') {
    payload.symbol = ((document.getElementById('ec-symbol') || {}).value || '').trim();
    if (action === 'pool_add') payload.name = ((document.getElementById('ec-name') || {}).value || '').trim();
  } else if (action === 'usage_flag') {
    payload.flag = (document.getElementById('ec-flag') || {}).value || 'push_review_required';
    payload.value = !!((document.getElementById('ec-flag-val') || {}).checked);
  }
  return {
    schema: 'v5.correction-plan.v1',
    action,
    payload,
    rule,
    evidence: { snapshot_id: snap },
    expectation, review_at: reviewAt,
  };
}

function _correctGateList(checks) {
  if (!checks || !checks.length) return '<div class="eval-empty">该动作无额外门槛（菜单内 + 结构校验已过）。</div>';
  return '<div class="eval-gate-list">' + checks.map(c =>
    '<div class="eval-gate-item"><b>' + escHtml(String(c).slice(0, 11)) + '</b>'
    + '<span class="eval-ops-hint">' + escHtml(c) + '</span></div>').join('') + '</div>';
}

export function correctToggle() {
  const wrap = document.getElementById('eval-correct');
  if (!wrap) return;
  const show = wrap.style.display !== 'block';
  wrap.style.display = show ? 'block' : 'none';
  if (!show) return;
  const actionOptions = [
    ['param_change', 'param_change：调整综合分阈值'],
    ['pool_add', 'pool_add：加入核心池'],
    ['pool_remove', 'pool_remove：移出核心池'],
    ['usage_flag', 'usage_flag：使用方式矫正'],
  ].map(([v, l]) => '<option value="' + escHtml(v) + '">' + escHtml(l) + '</option>').join('');
  wrap.innerHTML = '<div class="eval-correct-form">'
    + '<div class="eval-row"><label>动作：<select id="eval-correct-action" data-chgact="evalCorrectAction">'
    + actionOptions + '</select></label></div>'
    + '<div id="eval-correct-payload"></div>'
    + '<div class="eval-row"><label>触发规则：<select id="eval-correct-rule">'
    + ['T1', 'T2', 'T3', 'T4', 'T5', 'T6'].map(r => '<option value="' + r + '"' + (r === 'T4' ? ' selected' : '') + '>' + r + '</option>').join('')
    + '</select></label></div>'
    + '<div class="eval-row"><label>证据快照：<input id="eval-correct-snap" class="eval-input" value="' + escHtml(_defaultSnapshot()) + '" style="width:200px"></label></div>'
    + '<div class="eval-row"><label>预期：<input id="eval-correct-expectation" class="eval-input" placeholder="预期改善" style="width:220px"></label></div>'
    + '<div class="eval-row"><label>复核日期：<input id="eval-correct-review" class="eval-input" placeholder="2026-12-31" style="width:120px"></label></div>'
    + '<div class="eval-row"><button data-act="evalCorrectValidate">校验门槛（dry-run，零写入）</button></div>'
    + '<div id="eval-correct-gates"></div>'
    + '<div id="eval-correct-exec" style="display:none"></div>'
    + '</div>';
  correctPayload();
}

function _payloadFields(action) {
  const el = document.getElementById('eval-correct-payload');
  if (!el) return '';
  let html = '';
  if (action === 'param_change') {
    html += '<div class="eval-row"><label>强阈值：<input id="ec-th-strong" class="eval-input" type="number" min="0" step="1" value="75"></label>'
      + ' 买阈值：<input id="ec-th-buy" class="eval-input" type="number" min="0" step="1" value="60"></label></div>';
  } else if (action === 'pool_add' || action === 'pool_remove') {
    html += '<div class="eval-row"><label>代码：<input id="ec-symbol" class="eval-input" placeholder="600519" style="width:100px"></label>'
      + (action === 'pool_add' ? ' 名称：<input id="ec-name" class="eval-input" placeholder="名称（可选）" style="width:140px">' : '')
      + '</div>';
  } else if (action === 'usage_flag') {
    html += '<div class="eval-row"><label>旗标：<select id="ec-flag">'
      + '<option value="push_review_required">push_review_required</option></select></label>'
      + ' 值：<input type="checkbox" id="ec-flag-val" checked> </div>';
  }
  el.innerHTML = html;
}

export function correctPayload() {
  const sel = document.getElementById('eval-correct-action');
  if (!sel) return;
  _payloadFields(sel.value);
}

export async function correctValidate() {
  const gates = document.getElementById('eval-correct-gates');
  const execWrap = document.getElementById('eval-correct-exec');
  if (!gates) return;
  const plan = _buildPlan();
  gates.innerHTML = '<div class="eval-empty">校验中…</div>';
  if (execWrap) execWrap.style.display = 'none';
  try {
    const data = await _post(API + '/api/correct/validate', { plan });
    if (!data.ok) { gates.innerHTML = '<div class="eval-error">' + escHtml(data.error || '校验被拒绝') + '</div>'; return; }
    gates.innerHTML = '<div class="eval-empty">门槛判定：' + escHtml(data.gate_ok ? 'PASS' : 'FAIL')
      + '（' + escHtml(data.status) + '）</div>' + _correctGateList(data.gate_checks);
    if (data.gate_ok && execWrap) {
      execWrap.style.display = 'block';
      execWrap.innerHTML = '<div class="eval-row"><label>操作人签字：<input id="eval-operator" class="eval-input" placeholder="operator" style="width:140px"></label></div>'
        + '<div class="eval-row"><label><input type="checkbox" id="eval-confirmed"> 我已理解该矫正将改变策略行为</label></div>'
        + '<div class="eval-row"><button data-act="evalCorrectExecute" data-plan="' + escHtml(data.plan_id) + '">执行（二次确认后）</button></div>'
        + '<div id="eval-correct-result"></div>';
    }
  } catch (e) { gates.innerHTML = '<div class="eval-error">' + escHtml(e.message || '校验失败') + '</div>'; }
}

export async function correctExecute(el) {
  const planId = el && el.dataset && el.dataset.plan;
  const operator = (document.getElementById('eval-operator') || {}).value || '';
  const confirmed = !!((document.getElementById('eval-confirmed') || {}).checked);
  const resultEl = document.getElementById('eval-correct-result');
  if (resultEl) resultEl.innerHTML = '<div class="eval-empty">执行中…</div>';
  try {
    const data = await _post(API + '/api/correct/execute', { plan_id: planId, operator, confirmed });
    if (resultEl) resultEl.innerHTML = data.ok
      ? '<div class="eval-gate-item">执行成功：<' + escHtml(data.action) + '> ' + escHtml(data.status)
        + (data.log_line ? ' · 决策日志第 ' + escHtml(String(data.log_line)) + ' 行' : '')
        + (data.applied ? ' · ' + escHtml(JSON.stringify(data.applied)) : '') + '</div>'
        + '<div class="eval-notice-line">引擎下次进程启动生效，可 CLI <code>--rollback</code> 回滚。</div>'
      : '<div class="eval-error">' + escHtml(data.error || '执行失败') + '</div>';
    if (data.ok) { _evalPicked = null; await loadEvaluation(); }
  } catch (e) { if (resultEl) resultEl.innerHTML = '<div class="eval-error">' + escHtml(e.message || '执行失败') + '</div>'; }
}

// ---------------------------------------------------------------- 主体渲染

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
  host.innerHTML = listHtml
    + '<div id="eval-task-status" class="eval-card"></div>'
    + _seriesHtml(data)
    + _opsHtml()
    + '<div id="eval-picked">' + body + '</div>'
    + '<div id="eval-doc" class="eval-card" style="display:none">'
    + '<div class="eval-card-title" id="eval-doc-title"></div>'
    + '<pre class="eval-doc-pre" id="eval-doc-pre"></pre></div>';
  _renderTaskCard();
  const docs = document.getElementById('eval-doc-list');
  if (docs) docs.style.display = picked.summary ? 'block' : 'none';
}

function _listHtml(data) {
  const results = (data && data.results) || [];
  let html = '<div class="eval-card"><div class="eval-card-title">评估结果目录</div>';
  html += _noticeHtml(data && data.notice);
  if (!results.length) {
    html += '<div class="eval-empty">暂无评估结果。先在 CLI 生成：<br>'
      + '<code>python -m backtest snapshot --pool data/pool.json</code><br>'
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
    const t = _evalList && _evalList.task;
    if (t && t.status === 'running') { _renderPicked(_evalList); _startPoll(); }
    else { _renderPicked(_evalList); }
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
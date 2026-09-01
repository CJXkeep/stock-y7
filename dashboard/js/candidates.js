// ==================== 候选池与建议（I9.2/I9.4/I9.5） ====================
// 数据源：
//   GET  /api/candidates                 候选池全量
//   POST /api/candidates                 变更（add/remove/status/note/import）
//   POST /api/candidates/validate        启动候选验证后台任务（单任务互斥）
//   GET  /api/candidates/validate        验证进度轮询
//   GET  /api/advice                     建议单只读摘要
// 设计：候选池与核心池物理分离；候选到核心池唯一通道=建议单 + 人工走矫正页签。
import { API, fetchWithTimeout } from './api.js';
import { escHtml, showToastMsg } from './ui.js';

const CAND_HOST_ID = 'wp-content-candidates';
let _candPollTimer = null;
const STATUS_LABEL = {
  watching: '观察中', validated: '已验证', parked: '搁置',
  promoted: '已入池', rejected: '已拒绝',
};

async function _get(url) {
  const res = await fetchWithTimeout(url, {}, 15000);
  const data = await res.json().catch(() => null);
  if (!res.ok || !data) throw new Error('请求失败：' + res.status);
  return data;
}

async function _post(url, body) {
  const res = await fetchWithTimeout(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }, 20000);
  const data = await res.json().catch(() => null);
  if (!res.ok || !data) throw new Error('请求失败：' + res.status);
  return data;
}

function _escStatus(s) { return STATUS_LABEL[s] || s || '--'; }

// ---------------------------------------------------------------- 渲染

function _listHtml(cands) {
  const items = (cands && cands.items) || [];
  let html = '<div class="eval-card"><div class="eval-card-title">候选池'
    + '（观察名单，与核心池分离；候选→核心池唯一通道 = 建议单 + 人工执行）</div>';
  html += '<div class="eval-notice-line">容量 ' + escHtml(String(items.length))
    + '/' + '30 · version ' + escHtml(String(cands.version || 1))
    + ' · 冷却：入池/拒绝后 20 交易日内不重复入池</div>';
  html += '<div class="eval-row eval-ops-btns">'
    + '<button data-act="candAdd">添加候选</button>'
    + '<button data-act="candImportScan">从最近扫描导入</button>'
    + '<button data-act="candValidateStart">候选验证（无前视重放 + 门槛）</button>'
    + '</div>';
  html += '<div id="cand-validate-status"></div>';
  if (!items.length) {
    html += '<div class="eval-empty">候选池为空。从「任务 → 扫描」结果一键加入，或手动添加。</div>';
  } else {
    html += '<table class="eval-table"><tr><th>代码</th><th>名称</th><th>来源</th>'
      + '<th>状态</th><th>首见</th><th>操作</th></tr>';
    for (const it of items) {
      const first = it.first_action
        ? escHtml(String(it.first_action)) + (it.first_score != null ? ' ' + escHtml(String(it.first_score)) : '')
        : '--';
      html += '<tr><td>' + escHtml(it.symbol) + '</td>'
        + '<td>' + escHtml(it.name || '--') + '</td>'
        + '<td>' + escHtml(it.source || '--') + '</td>'
        + '<td><b>' + escHtml(_escStatus(it.status)) + '</b></td>'
        + '<td>' + first + '</td>'
        + '<td>'
        + '<button data-act="candRemove" data-symbol="' + escHtml(it.symbol) + '">移除</button> '
        + '<button data-act="candStatus" data-symbol="' + escHtml(it.symbol) + '" data-status="parked">搁置</button> '
        + '<button data-act="candStatus" data-symbol="' + escHtml(it.symbol) + '" data-status="watching">恢复观察</button> '
        + '<button data-act="candNote" data-symbol="' + escHtml(it.symbol) + '">备注</button>'
        + '</td></tr>';
    }
    html += '</table>';
  }
  html += '</div>';
  return html;
}

function _adviceHtml(advice) {
  const plans = (advice && advice.plans) || [];
  let html = '<div class="eval-card"><div class="eval-card-title">建议单（只读）</div>';
  html += '<div class="eval-notice-line">建议由候选验证/滚动评估数据生成，'
    + '执行需人工到「档案 → 评估 → 矫正计划」签字。建议器不自动改池。</div>';
  if (!plans.length) {
    html += '<div class="eval-empty">暂无建议单。先运行「候选验证」，再对 PASS 候选生成入池建议。</div>';
  } else {
    html += '<div class="eval-gate-list">';
    for (const p of plans.slice(0, 20)) {
      const sym = (p.payload && p.payload.symbol) || '--';
      const label = p.action === 'pool_add' ? '入池' : p.action === 'pool_remove' ? '出池' : p.action;
      html += '<div class="eval-gate-item"><b>' + escHtml(label) + ' ' + escHtml(sym)
        + '</b><span class="eval-ops-hint">' + escHtml(p.rule || '')
        + (p.advised_at ? ' · ' + escHtml(p.advised_at) : '') + '</span></div>';
    }
    html += '</div>';
    html += '<div class="eval-row eval-ops-btns">'
      + '<button data-act="openArchiveSeg" data-seg="eval">去矫正页签执行 →</button>'
      + '</div>';
  }
  html += '</div>';
  return html;
}

export async function loadCandidates() {
  const host = document.getElementById(CAND_HOST_ID);
  if (!host) return;
  host.innerHTML = '<div class="eval-empty">候选数据加载中…</div>';
  try {
    const [cands, advice, task] = await Promise.all([
      _get(API + '/api/candidates'),
      _get(API + '/api/advice'),
      _get(API + '/api/candidates/validate'),
    ]);
    host.innerHTML = _listHtml(cands) + _adviceHtml(advice) + '<div id="cand-task"></div>';
    _renderTask(task);
    if (task.status === 'running') _startPoll();
  } catch (err) {
    host.innerHTML = '<div class="eval-empty">' + escHtml(err.message || '候选数据加载失败') + '</div>';
  }
}

function _renderTask(task) {
  const el = document.getElementById('cand-validate-status');
  if (!el) return;
  const t = task || {};
  const color = t.status === 'running' ? 'var(--c-info, #6aa)'
    : t.status === 'done' ? 'var(--c-up, #4caf50)'
    : t.status === 'error' ? 'var(--c-down, #ff6b6b)' : 'var(--c-text-2, #999)';
  let html = '<div class="eval-notice-line">验证任务：<b style="color:' + color + '">'
    + escHtml(t.status || 'idle') + '</b>' + (t.stage ? ' · ' + escHtml(t.stage) : '') + '</div>';
  if (t.status === 'running') {
    html += '<div class="eval-progress"><div class="eval-progress-bar" style="width:'
      + Math.max(2, t.progress || 0) + '%"></div></div>';
    html += '<div class="eval-notice-line">进度 ' + escHtml(String(t.progress || 0)) + '%</div>';
  }
  if (t.status === 'done' && t.summary) {
    html += '<div class="eval-notice-line">完成：验证 ' + escHtml(String(t.summary.total || 0))
      + ' 只候选，PASS ' + escHtml(String(t.summary.passed || 0)) + ' 只'
      + (t.summary.snapshot_id ? ' · 快照 ' + escHtml(t.summary.snapshot_id) : '') + '</div>';
  }
  if (t.error) html += '<div class="eval-error">' + escHtml(t.error) + '</div>';
  el.innerHTML = html;
}

function _startPoll() {
  _stopPoll();
  _candPollTimer = setInterval(async () => {
    try {
      const data = await _get(API + '/api/candidates/validate');
      _renderTask(data);
      if (data.status !== 'running') {
        _stopPoll();
        await loadCandidates();
      }
    } catch (e) { _stopPoll(); }
  }, 1500);
}

function _stopPoll() { if (_candPollTimer) { clearInterval(_candPollTimer); _candPollTimer = null; } }

// ---------------------------------------------------------------- 操作

export async function candAdd() {
  const symbol = window.prompt('输入 6 位股票代码：', '');
  if (!symbol) return;
  const name = window.prompt('名称（可选）：', '') || '';
  try {
    const data = await _post(API + '/api/candidates',
      { action: 'add', symbol: symbol.trim(), name: name.trim(), source: 'manual' });
    showToastMsg(data.ok ? '已加入候选' : (data.error || '加入失败'));
    if (data.ok) await loadCandidates();
  } catch (e) { showToastMsg(e.message || '加入失败'); }
}

export async function candRemove(el) {
  const symbol = el && el.dataset && el.dataset.symbol;
  if (!symbol) return;
  try {
    const data = await _post(API + '/api/candidates', { action: 'remove', symbol });
    showToastMsg(data.ok ? '已移除' : (data.error || '移除失败'));
    if (data.ok) await loadCandidates();
  } catch (e) { showToastMsg(e.message || '移除失败'); }
}

export async function candStatus(el) {
  const symbol = el && el.dataset && el.dataset.symbol;
  const status = el && el.dataset && el.dataset.status;
  if (!symbol || !status) return;
  try {
    const data = await _post(API + '/api/candidates', { action: 'status', symbol, status });
    showToastMsg(data.ok ? '状态已更新' : (data.error || '更新失败'));
    if (data.ok) await loadCandidates();
  } catch (e) { showToastMsg(e.message || '更新失败'); }
}

export async function candNote(el) {
  const symbol = el && el.dataset && el.dataset.symbol;
  if (!symbol) return;
  const note = window.prompt('备注：', '') || '';
  try {
    const data = await _post(API + '/api/candidates', { action: 'note', symbol, note: note.trim() });
    showToastMsg(data.ok ? '备注已保存' : (data.error || '保存失败'));
    if (data.ok) await loadCandidates();
  } catch (e) { showToastMsg(e.message || '保存失败'); }
}

// 从最近一次扫描结果批量导入候选（仅保留双周期买入结果）
export async function candImportScan() {
  try {
    const scan = await _get(API + '/api/scan');
    const results = (scan && scan.results) || [];
    if (!results.length) { showToastMsg('最近扫描无结果可导入'); return; }
    const items = results.map(r => ({
      symbol: String(r.symbol || '').padStart(6, '0'),
      name: r.name || '', first_action: r.weekly_action || r.daily_action || '',
      first_score: r.combined_score != null ? r.combined_score : undefined,
    }));
    const data = await _post(API + '/api/candidates', { action: 'import', items });
    showToastMsg(data.ok ? '已导入 ' + (data.added || 0) + ' 只（跳过 ' + (data.skipped || 0) + '）'
      : (data.error || '导入失败'));
    if (data.ok) await loadCandidates();
  } catch (e) { showToastMsg(e.message || '导入失败'); }
}

export async function candValidateStart() {
  try {
    const data = await _post(API + '/api/candidates/validate', {});
    if (!data.ok) { showToastMsg(data.error || '启动失败'); return; }
    showToastMsg(data.message || '候选验证已启动');
    _renderTask(data);
    _startPoll();
  } catch (e) { showToastMsg(e.message || '启动失败'); }
}

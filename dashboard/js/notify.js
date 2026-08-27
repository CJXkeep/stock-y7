// ==================== 钉钉推送设置（notify-dingtalk：自选买入信号主动推送） ====================
import { API, fetchWithTimeout } from './api.js';
import { showToastMsg, escHtml } from './ui.js';

const STATUS_EL = () => document.getElementById('notify-status');

function _statusText(data) {
  const st = data.state || {};
  const bits = [];
  bits.push(data.enabled ? '已启用' : '未启用');
  if (!data.configured && data.enabled) bits.push('⚠ 未配置有效 webhook');
  const statusMap = {
    running: '巡检中', waiting_market: '等待开盘', error: '异常',
    idle: '', busy: '巡检中',
  };
  const stLabel = statusMap[st.status] || '';
  if (stLabel) bits.push(stLabel);
  bits.push(`自选 ${data.watchlist_count} 只`);
  if (st.last_run) bits.push(`最近巡检 ${st.last_run}`);
  if (st.last_found > 0) bits.push(`本轮新信号 ${st.last_found}`);
  bits.push(`累计推送 ${st.pushed_total || 0} 条`);
  let text = bits.join(' · ');
  if (st.last_error) text += `\n最近错误：${st.last_error}`;
  return text;
}

function _fillForm(data) {
  const enabled = document.getElementById('notify-enabled');
  const interval = document.getElementById('notify-interval');
  const webhook = document.getElementById('notify-webhook');
  const secret = document.getElementById('notify-secret');
  if (enabled) enabled.checked = !!data.enabled;
  if (interval) interval.value = String(data.interval_min || 5);
  // 脱敏回显：仅当输入框为空时填入掩码提示，避免覆盖用户正在编辑的内容
  if (webhook && !webhook.value && data.webhook_masked) webhook.placeholder = data.webhook_masked;
  if (secret && !secret.value && data.has_secret) secret.placeholder = '已保存（留空保持不变，输入则覆盖）';

  // ---- push 配置：级别 / 范围 / 阈值 ----
  const push = data.push || {};
  const levels = new Set(push.levels || []);
  ['strong_buy', 'buy', 'cautious_buy'].forEach((lv) => {
    const el = document.getElementById('notify-level-' + lv);
    if (el) el.checked = levels.has(lv);
  });

  const scope = push.scope || {};
  const enabledGroups = new Set((scope.enabled_groups || []).map(String));
  const groupBox = document.getElementById('notify-group-list');
  if (groupBox) {
    const groups = data.watchlist_groups || [];
    groupBox.innerHTML = groups.length
      ? groups.map((g) =>
          `<label class="notify-switch"><input type="checkbox" data-group-id="${escHtml(String(g.id))}" ${enabledGroups.has(String(g.id)) ? 'checked' : ''}> ${escHtml(g.name || g.id)}</label>`
        ).join('')
      : '<span class="settings-hint">暂无分组（默认推送全部自选）</span>';
  }

  const disabledSymbols = new Set((scope.disabled_symbols || []).map(String));
  const symBox = document.getElementById('notify-symbol-list');
  if (symBox) {
    const stocks = data.watchlist_stocks || [];
    symBox.innerHTML = stocks.length
      ? stocks.map((s) =>
          `<label class="notify-stock-item"><span>${escHtml(s.name || '')} <span class="code">${escHtml(s.code)}</span></span><input type="checkbox" data-stock-code="${escHtml(String(s.code))}" ${disabledSymbols.has(String(s.code)) ? '' : 'checked'}></label>`
        ).join('')
      : '<span class="settings-hint">自选为空</span>';
  }

  const th = push.thresholds || {};
  const ms = document.getElementById('notify-min-score');
  if (ms) ms.value = (th.min_score != null && Number(th.min_score) > 0) ? String(th.min_score) : '';
  const mp = document.getElementById('notify-min-pct');
  if (mp) mp.value = (th.min_pct_change != null && String(th.min_pct_change) !== '') ? String(th.min_pct_change) : '';
}

// 拉取配置与状态；openSettings 时与启动时各调一次
export async function loadNotifySettings() {
  try {
    const resp = await fetchWithTimeout(`${API}/api/notify`);
    if (!resp.ok) return;
    const data = await resp.json();
    _fillForm(data);
    if (STATUS_EL()) STATUS_EL().textContent = _statusText(data);
  } catch (e) { /* 静默：设置面板不因状态接口失败而不可用 */ }
}

export function refreshNotifyStatus() { return loadNotifySettings(); }

function _readForm() {
  const levels = ['strong_buy', 'buy', 'cautious_buy']
    .filter((lv) => { const el = document.getElementById('notify-level-' + lv); return el && el.checked; });
  const enabled_groups = Array.from(document.querySelectorAll('#notify-group-list input[type=checkbox]:checked'))
    .map((el) => el.getAttribute('data-group-id'));
  const disabled_symbols = Array.from(document.querySelectorAll('#notify-symbol-list input[type=checkbox]'))
    .filter((el) => !el.checked)
    .map((el) => el.getAttribute('data-stock-code'));

  const msRaw = (document.getElementById('notify-min-score') || {}).value || '';
  const mpRaw = (document.getElementById('notify-min-pct') || {}).value || '';
  let min_score = 0;
  if (msRaw !== '') { const n = parseInt(msRaw, 10); if (!Number.isNaN(n)) min_score = n; }
  let min_pct_change = null;
  if (mpRaw !== '') { const n = parseFloat(mpRaw); if (!Number.isNaN(n)) min_pct_change = n; }

  return {
    enabled: !!(document.getElementById('notify-enabled') || {}).checked,
    webhook: (document.getElementById('notify-webhook') || {}).value || '',
    secret: (document.getElementById('notify-secret') || {}).value || '',
    interval_min: parseInt((document.getElementById('notify-interval') || {}).value || '5', 10),
    push: {
      levels,
      scope: { enabled_groups, disabled_symbols },
      thresholds: { min_score, min_pct_change },
    },
  };
}

export async function saveNotifySettings() {
  const form = _readForm();
  try {
    const resp = await fetchWithTimeout(`${API}/api/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'save', ...form }),
    });
    const data = await resp.json();
    if (data.ok) {
      showToastMsg('推送配置已保存');
      // 保存成功后清空输入框，避免明文 webhook 长时间留在 DOM
      const w = document.getElementById('notify-webhook'), s = document.getElementById('notify-secret');
      if (w) w.value = '';
      if (s) s.value = '';
    } else {
      showToastMsg(data.error || '保存失败');
    }
  } catch (e) {
    showToastMsg('保存请求失败，请稍后再试');
  }
  loadNotifySettings();
}

export async function testNotify() {
  const form = _readForm();
  try {
    showToastMsg('测试消息发送中…');
    const resp = await fetchWithTimeout(`${API}/api/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'test', webhook: form.webhook, secret: form.secret }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? '✅ 测试消息已发送，请查看钉钉' : `❌ ${data.error || '发送失败'}`);
  } catch (e) {
    showToastMsg('测试请求失败，请稍后再试');
  }
}

export async function runNotifyOnce() {
  try {
    const resp = await fetchWithTimeout(`${API}/api/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'run_once', force: true }),
    });
    const data = await resp.json();
    showToastMsg(data.ok ? '已触发一轮自选巡检，稍后刷新看结果' : (data.error || '触发失败'));
    if (data.ok) {
      // 巡检为后台执行：延迟两次刷新状态行
      setTimeout(loadNotifySettings, 4000);
      setTimeout(loadNotifySettings, 12000);
    }
  } catch (e) {
    showToastMsg('请求失败，请稍后再试');
  }
}

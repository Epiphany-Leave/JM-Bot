'use strict';

const $ = (id) => document.getElementById(id);
let authenticated = false;
let sessionVersion = 0;
let pollTimer;
let latestStatus = null;
let savedPermissions = null;
let joinedGroups = [];
let permissionsLoaded = false;
let editVersion = 0;
let testSending = false;
let qrVisible = false;
let qrLoading = false;

function feedback(id, message, error = false) {
  const element = $(id);
  element.textContent = message || '';
  element.hidden = !message;
  element.classList.toggle('error', error);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) showLogin();
    const message = data.message || data.error || `请求未完成（${response.status}）`;
    throw new Error(data.next_step ? `${message}\n${data.next_step}` : message);
  }
  return data;
}

function showLogin() {
  sessionVersion += 1;
  authenticated = false;
  clearTimeout(pollTimer);
  $('app-view').hidden = true;
  $('login-view').hidden = false;
}

function showApp() {
  authenticated = true;
  $('login-view').hidden = true;
  $('app-view').hidden = false;
}

function setTab(name) {
  const titles = { overview: '运行概览', qq: 'QQ 登录', permissions: '使用权限' };
  if (!titles[name]) name = 'overview';
  for (const button of document.querySelectorAll('[data-tab]')) {
    const selected = button.dataset.tab === name;
    button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  for (const panel of document.querySelectorAll('.tab-panel')) panel.hidden = panel.id !== `tab-${name}`;
  $('page-title').textContent = titles[name];
  history.replaceState(null, '', `#${name}`);
}

function badge(id, message, kind) {
  $(id).textContent = message;
  $(id).className = `pill ${kind}`;
}

function updateTestButton() {
  $('test-submit').disabled = testSending || !latestStatus?.ready || !!latestStatus?.busy || !$('test-target').value;
}

function refreshQrImage() {
  qrVisible = true;
  $('qr-image').hidden = true;
  $('qr-placeholder').hidden = false;
  $('qr-placeholder').textContent = '正在获取二维码…';
  $('qr-image').src = `/api/qq/qrcode?t=${Date.now()}`;
}

function renderStatus(status) {
  latestStatus = status;
  const qq = status.qq || {};
  const online = !!qq.isLogin && !qq.isOffline;
  const needsPermissions = online && status.config_synced && status.onebot_connected && !status.ready;
  badge('header-state', status.ready ? '已就绪' : '等待连接', status.ready ? 'green' : 'amber');
  $('ready-title').textContent = status.ready ? (status.busy ? '正在处理，请稍候' : '已就绪，可以开始使用') : needsPermissions ? '连接完成，设置使用权限' : '还差一步，连接你的机器人';
  $('next-step').textContent = status.next_step || (status.ready ? '在已授权的私聊或群聊中发送 /jm 指令即可。' : '前往 QQ 登录，完成连接后即可开始使用。');
  $('connect-qq').hidden = status.ready;
  $('connect-qq').textContent = needsPermissions ? '设置使用权限 →' : '前往 QQ 登录 →';
  $('connect-qq').dataset.target = needsPermissions ? 'permissions' : 'qq';
  $('qq-name').textContent = online ? (qq.nickname || 'QQ 已登录') : '尚未登录';
  $('qq-status').textContent = online ? (qq.user_id ? `QQ ${qq.user_id} · 在线` : '账号在线') : (qq.loginError || (status.napcat_reachable ? '请扫码并在手机上确认' : 'QQ 服务正在连接'));
  $('channel-state').textContent = status.onebot_connected ? '已连接' : '等待连接';
  $('channel-detail').textContent = status.onebot_connected ? (status.config_synced ? '消息与配置均已就绪' : '正在同步机器人设置') : 'QQ 登录后会自动建立连接';
  const task = status.current_task;
  const stages = { downloading: '正在下载', download: '正在下载', converting: '正在合成 PDF', uploading: '正在上传', upload: '正在上传', completed: '处理完成' };
  $('task-state').textContent = status.busy ? (task?.album_id ? `JM ${task.album_id}` : '正在处理') : '空闲';
  $('task-detail').textContent = status.busy ? (stages[task?.stage] || task?.stage || '正在处理，请耐心等待') : '单次处理一本，完成后可继续';
  feedback('global-error', status.error);
  const result = status.last_result;
  $('result-empty').hidden = !!result;
  $('result-content').hidden = !result;
  if (result) {
    const success = ['success', 'completed', 'ok'].includes(result.status);
    badge('result-status', success ? '处理完成' : result.path ? '文件已保留' : '处理结果', success ? 'green' : 'amber');
    $('result-message').textContent = result.message || '请查看任务状态。';
    $('retained-file').hidden = !result.path;
    $('result-path').textContent = result.path || '';
  }
  const phaseHints = {
    qrcode_scanned: '二维码已扫描，请在手机 QQ 上确认登录。',
    initializing: 'QQ 正在初始化，请稍候，登录状态会自动更新。',
    reconnecting: 'QQ 正在重新连接，请稍候。',
  };
  const progress = phaseHints[qq.loginPhase];
  $('qr-title').textContent = online ? 'QQ 已连接' : progress ? '正在连接 QQ' : '扫码登录';
  $('qr-hint').textContent = online ? `${qq.nickname || '机器人账号'}已在线，可以前往设置使用权限。` : (qq.loginError || progress || '使用手机 QQ 扫描二维码，并在手机上确认。');
  $('refresh-qr').hidden = online;
  if (online) {
    qrVisible = false;
    $('qr-image').hidden = true;
    $('qr-placeholder').hidden = false;
    $('qr-placeholder').textContent = '✓ 账号已连接';
  } else if (!status.napcat_reachable || progress) {
    qrVisible = false;
    $('qr-image').hidden = true;
    $('qr-placeholder').hidden = false;
    $('qr-placeholder').textContent = status.napcat_reachable ? progress : 'QQ 服务正在启动，请稍候';
  } else if (!qrVisible) {
    refreshQrImage();
  }
  $('refresh-qr').disabled = qrLoading || !status.napcat_reachable;
  updateTestButton();
}

async function pollStatus() {
  clearTimeout(pollTimer);
  const version = sessionVersion;
  try {
    const status = await api('/api/status');
    if (version !== sessionVersion) return;
    if (!authenticated) {
      showApp();
      loadPermissions();
    }
    renderStatus(status);
  } catch (error) {
    if (authenticated) {
      feedback('global-error', `暂时无法获取状态。${error.message}`, true);
      latestStatus = null;
      badge('header-state', '状态暂不可用', 'amber');
      updateTestButton();
    }
  }
  if (authenticated) pollTimer = setTimeout(pollStatus, 3000);
}

function selectedGroups() {
  return [...document.querySelectorAll('#group-list input:checked')].map((input) => input.value);
}

function renderGroups(selected) {
  const allowed = new Set(selected.map(String));
  const groups = new Map(joinedGroups.map((group) => [String(group.group_id), group.group_name || '未命名群聊']));
  for (const id of allowed) if (!groups.has(id)) groups.set(id, '已保存的授权群（当前列表未返回）');
  $('group-list').replaceChildren();
  if (!groups.size) {
    $('group-list').textContent = '暂无可选群聊。QQ 登录并加入群后，点击刷新群列表。';
    return;
  }
  for (const [id, name] of groups) {
    const label = document.createElement('label');
    label.className = 'group-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = id;
    checkbox.checked = allowed.has(id);
    const text = document.createElement('span');
    const title = document.createElement('strong');
    title.textContent = name;
    const number = document.createElement('small');
    number.textContent = id;
    text.append(title, number);
    label.append(checkbox, text);
    $('group-list').append(label);
  }
}

function renderTestTargets() {
  const previous = $('test-target').value;
  $('test-target').replaceChildren();
  const names = new Map(joinedGroups.map((group) => [String(group.group_id), group.group_name]));
  for (const [kind, ids] of [['group', savedPermissions?.allowed_groups || []], ['private', savedPermissions?.allowed_users || []]]) {
    for (const id of ids) {
      const option = document.createElement('option');
      option.value = `${kind}:${id}`;
      option.textContent = kind === 'group' ? `群聊 · ${names.get(String(id)) || id}（${id}）` : `私聊 · QQ ${id}`;
      $('test-target').append(option);
    }
  }
  if (!$('test-target').options.length) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '请先设置使用权限';
    $('test-target').append(empty);
  } else if ([...$('test-target').options].some((option) => option.value === previous)) {
    $('test-target').value = previous;
  }
  updateTestButton();
}

async function loadGroups(selected = selectedGroups()) {
  $('reload-groups').disabled = true;
  feedback('groups-feedback', '正在读取机器人加入的群聊…');
  try {
    const result = await api('/api/groups');
    // Read selection after the request so edits made while loading survive.
    const current = permissionsLoaded ? selectedGroups() : selected;
    joinedGroups = result.groups || [];
    renderGroups(current);
    renderTestTargets();
    feedback('groups-feedback', joinedGroups.length ? '' : '暂无群聊。请先确认 QQ 已在线并加入了群。');
  } catch (error) {
    feedback('groups-feedback', `未能获取群列表：${error.message}。已保存的群权限会保留。`, true);
  } finally {
    $('reload-groups').disabled = false;
  }
}

async function loadPermissions() {
  permissionsLoaded = false;
  $('save-permissions').disabled = true;
  try {
    const permissions = await api('/api/permissions');
    savedPermissions = {
      allowed_users: (permissions.allowed_users || []).map(String),
      allowed_groups: (permissions.allowed_groups || []).map(String),
      group_allow_all: permissions.group_allow_all !== false,
    };
    $('allowed-users').value = savedPermissions.allowed_users.join('\n');
    document.querySelector(`[name="group-mode"][value="${savedPermissions.group_allow_all ? 'all' : 'listed'}"]`).checked = true;
    renderGroups(savedPermissions.allowed_groups);
    permissionsLoaded = true;
    editVersion = 0;
    $('unsaved').hidden = true;
    feedback('permissions-feedback', '');
    renderTestTargets();
    await loadGroups(savedPermissions.allowed_groups);
  } catch (error) {
    feedback('permissions-feedback', `权限未加载，请重新登录后再试。${error.message}`, true);
  } finally {
    $('save-permissions').disabled = !permissionsLoaded;
  }
}

function markDirty() {
  editVersion += 1;
  $('unsaved').hidden = false;
  feedback('permissions-feedback', '');
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  feedback('login-error', '');
  try {
    await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ password: $('password').value }) });
    $('password').value = '';
    showApp();
    loadPermissions();
    await pollStatus();
  } catch (error) {
    feedback('login-error', error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('logout').addEventListener('click', async () => {
  $('logout').disabled = true;
  try {
    await api('/api/auth/logout', { method: 'POST', body: '{}' });
    showLogin();
    $('password').focus();
  } catch (error) {
    feedback('global-error', error.message, true);
  } finally {
    $('logout').disabled = false;
  }
});

for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => setTab(button.dataset.tab));
$('connect-qq').addEventListener('click', () => setTab($('connect-qq').dataset.target || 'qq'));
$('go-permissions').addEventListener('click', () => setTab('permissions'));
$('test-target').addEventListener('change', updateTestButton);
$('allowed-users').addEventListener('input', markDirty);
$('permissions-form').addEventListener('change', markDirty);
$('reload-groups').addEventListener('click', () => loadGroups());

$('permissions-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!permissionsLoaded) return;
  const raw = $('allowed-users').value.trim();
  const ids = raw ? raw.split(/[\s,，、;；]+/).filter(Boolean) : [];
  if (ids.some((id) => !/^[0-9]+$/.test(id) || /^0+$/.test(id))) {
    feedback('permissions-feedback', '请只填写正整数 QQ 号，用逗号或换行分隔。', true);
    $('allowed-users').focus();
    return;
  }
  const permissions = {
    allowed_users: [...new Set(ids.map((id) => id.replace(/^0+/, '')))],
    allowed_groups: selectedGroups(),
    group_allow_all: document.querySelector('[name="group-mode"]:checked').value === 'all',
  };
  const version = editVersion;
  $('save-permissions').disabled = true;
  feedback('permissions-feedback', '正在保存…');
  try {
    await api('/api/permissions', { method: 'PUT', body: JSON.stringify(permissions) });
    savedPermissions = permissions;
    if (version === editVersion) $('unsaved').hidden = true;
    feedback('permissions-feedback', version === editVersion ? '已保存，新的使用权限已生效。' : '已保存。你刚刚的修改仍需再次保存。');
    renderTestTargets();
  } catch (error) {
    feedback('permissions-feedback', `保存未完成：${error.message}`, true);
  } finally {
    $('save-permissions').disabled = false;
  }
});

$('refresh-qr').addEventListener('click', async () => {
  qrLoading = true;
  $('refresh-qr').disabled = true;
  feedback('qr-feedback', '正在刷新二维码…');
  try {
    await api('/api/qq/refresh', { method: 'POST', body: '{}' });
    refreshQrImage();
    feedback('qr-feedback', '二维码已刷新，请用手机 QQ 扫描。');
  } catch (error) {
    feedback('qr-feedback', error.message, true);
  } finally {
    qrLoading = false;
    $('refresh-qr').disabled = !latestStatus?.napcat_reachable;
  }
});
$('qr-image').addEventListener('load', () => {
  if (!qrVisible) return;
  $('qr-image').hidden = false;
  $('qr-placeholder').hidden = true;
});
$('qr-image').addEventListener('error', () => {
  qrVisible = false;
  $('qr-image').hidden = true;
  $('qr-placeholder').hidden = false;
  $('qr-placeholder').textContent = '二维码正在准备，稍后会自动重试';
});

$('test-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (testSending || $('test-submit').disabled) return;
  const [kind, target_id] = $('test-target').value.split(':');
  if (!target_id) return;
  testSending = true;
  updateTestButton();
  feedback('test-feedback', '正在发送测试 PDF，请稍候…');
  try {
    const result = await api('/api/test-file', { method: 'POST', body: JSON.stringify({ kind, target_id }) });
    const failed = ['error', 'failed', 'uncertain'].includes(result.status);
    const path = result.path ? `\nNAS 保留文件：${result.path}` : '';
    feedback('test-feedback', `${result.message || '请求已处理，请在 QQ 中检查文件。'}${path}`, failed);
    await pollStatus();
  } catch (error) {
    feedback('test-feedback', `发送结果未确认，请先检查 QQ，不会自动重发。\n${error.message}`, true);
  } finally {
    testSending = false;
    updateTestButton();
  }
});

setTab(location.hash.slice(1));
api('/api/auth/session').then((session) => {
  if (session.authenticated) pollStatus();
}).catch((error) => feedback('login-error', error.message, true));

export const DEVICE_GATEWAY_BASE_V268 = '/workspace/device-gateways';
export const DEVICE_GATEWAY_PROTOCOL_V268 = 'procurement-simulator-v1';
export const DEVICE_GATEWAY_LABEL_V268 = '采购参数测试设备';
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function needV268(value, message = '保存记录无法核对，原申请保留，请先重新读取。') { if (!value) { const error = Error(message); error.localMessage = message; throw error; } }
export const isUUIDV268 = value => typeof value === 'string' && uuidPattern.test(value);
export const copyV268 = value => JSON.parse(JSON.stringify(value));
export const sameV268 = (left, right) => JSON.stringify(left) === JSON.stringify(right);
export function deviceGatewayScopeV268(account) {
  needV268(isUUIDV268(account?.user_id) && isUUIDV268(account?.workspace_id), '当前账号或工作区无法核对，暂不能接入设备。');
  return {actor_id: account.user_id, workspace_id: account.workspace_id};
}
export const deviceGatewayPrefixV268 = scope => 'device-gateway-v268:' + scope.workspace_id + ':' + scope.actor_id + ':';
export function safeServiceOriginV268(value) {
  needV268(typeof value === 'string' && value.trim() === value && value.length <= 2048, '请填写服务地址。');
  let address; try { address = new URL(value); } catch { needV268(false, '请填写完整的 HTTP 或 HTTPS 服务地址。'); }
  needV268(['http:', 'https:'].includes(address.protocol) && !address.username && !address.password && !address.search && !address.hash, '服务地址不能包含账号、密码、查询参数或片段；访问令牌请填在独立输入框。');
  needV268(value === address.origin, '服务地址仅填写协议、主机和端口，不带目录或末尾斜杠。');
  return value;
}
export function blankDeviceGatewayFormV268() { return {name: '', origin: '', reason: '', status: 'paused', actions: [], device_external_id: ''}; }
export function checkedDeviceGatewayFormV268(value) {
  const allowed = Object.keys(blankDeviceGatewayFormV268());
  needV268(value && typeof value === 'object' && Object.keys(value).every(key => allowed.includes(key)));
  const next = {...blankDeviceGatewayFormV268(), ...value};
  needV268(['name', 'origin', 'reason', 'device_external_id'].every(key => typeof next[key] === 'string') && next.name.length <= 160 && next.origin.length <= 2048 && next.reason.length <= 2000 && ['enabled', 'paused'].includes(next.status) && Array.isArray(next.actions) && next.actions.every(action => typeof action === 'string'));
  needV268(!/[?@#]/.test(next.origin), '服务地址不能包含账号、密码、查询参数或片段；该输入未保存。');
  return copyV268(next);
}
// No credential, credential digest, or server encryption material may enter a browser journal.
export function assertSafeGatewayDataV268(value) {
  if (!value || typeof value !== 'object') return value;
  for (const [key, item] of Object.entries(value)) {
    needV268(!/(token|secret|password|ciphertext|encrypted)/i.test(key), '服务器返回了不应显示或缓存的凭据内容，已停止采用。');
    assertSafeGatewayDataV268(item);
  }
  return value;
}
export function checkedConnectionV268(value) {
  assertSafeGatewayDataV268(value);
  needV268(value && isUUIDV268(value.id) && Number.isSafeInteger(value.version) && value.version > 0 && Number.isSafeInteger(value.credential_revision) && value.credential_revision > 0 && typeof value.name === 'string' && value.protocol === DEVICE_GATEWAY_PROTOCOL_V268 && ['enabled', 'paused'].includes(value.status));
  safeServiceOriginV268(value.origin);
  needV268(value.grants === undefined || Array.isArray(value.grants));
  if (value.latest_discovery) { checkedDiscoveryV268(value.latest_discovery); needV268(value.latest_discovery.connection_id === value.id); }
  for (const grant of value.grants || []) { needV268(grant && isUUIDV268(grant.id) && grant.connection_id === value.id && typeof grant.device_external_id === 'string' && Array.isArray(grant.actions) && grant.actions.every(action => typeof action === 'string') && typeof grant.effective === 'boolean' && Number.isSafeInteger(grant.connection_version) && Number.isSafeInteger(grant.credential_revision)); if (grant.integrity_error) needV268(grant.integrity_error === 'grant_integrity_invalid' && grant.effective === false); }
  return copyV268(value);
}
export function checkedDiscoveryV268(value) {
  assertSafeGatewayDataV268(value);
  needV268(value && isUUIDV268(value.id) && isUUIDV268(value.connection_id) && ['queued', 'running', 'succeeded', 'failed', 'invalid'].includes(value.status) && Number.isSafeInteger(value.connection_version) && value.connection_version > 0 && Number.isSafeInteger(value.credential_revision) && value.credential_revision > 0 && typeof value.effective === 'boolean' && Array.isArray(value.devices));
  if (value.status === 'invalid') needV268(value.effective === false && value.integrity_error === 'discovery_integrity_invalid' && value.devices.length === 0);
  for (const device of value.devices) needV268(device && typeof device.external_id === 'string' && device.external_id.length > 0 && typeof device.name === 'string' && Array.isArray(device.actions) && device.actions.every(action => typeof action === 'string') && device.simulation === true && device.origin_kind === 'simulated_equipment' && device.field_verified === false && device.physical_device_connected === false);
  return copyV268(value);
}
const actionLabels = {
  read: '读取数据', 'data.read': '读取数据', read_data: '读取数据', telemetry: '读取测量值', 'telemetry.read': '读取测量值',
  snapshot: '读取单帧', 'snapshot.read': '读取单帧', stream: '读取视频', 'stream.read': '读取视频', status: '读取状态', 'status.read': '读取状态',
  'camera.snapshot': '拍摄画面', 'camera.stream': '读取视频', 'camera.ptz': '云台转向', ptz: '云台转向',
  'drone.mission': '下发无人机任务', mission: '下发无人机任务', capture: '触发采集', stop: '请求停止',
  get_status: '读取状态', read_telemetry: '读取测量数据', set_ptz: '云台转向', start_mission: '下发无人机任务', return_to_dock: '返回机库', cancel_mission: '取消无人机任务',
};
export const actionLabelV268 = action => actionLabels[action] || '其他声明能力（' + action + '）';
export const isReadActionV268 = action => /(^|[._:])(read|status|telemetry|stream)([._:]|$)/.test(action) || ['snapshot', 'get_status', 'read_telemetry'].includes(action);
export function deviceFamilyViewV268(device) {
  const family = device?.family || device?.device_family || device?.kind;
  if (['weather', 'weather_station', 'sensor', 'meteorological'].includes(family)) return {label: '气象传感器', note: '只读取测量数据，不提供设备控制。'};
  if (['camera', 'video_camera', 'ptz_camera'].includes(family)) return {label: '相机', note: '持续观测设备不要求执行时段；读取与控制分别授权。'};
  if (['drone', 'uav', 'drone_dock'].includes(family)) return {label: '无人机', note: '执行时段和已确认航线仍需后续执行配置，当前没有飞行或任务执行入口。'};
  return {label: '设备声明', note: '设备类别和现场身份仍待核对。'};
}
export function discoveryIsCurrentV268(connection, discovery) { return !!connection && connection.status === 'enabled' && !!discovery && discovery.connection_id === connection.id && discovery.connection_version === connection.version && discovery.credential_revision === connection.credential_revision && discovery.status === 'succeeded' && discovery.effective === true && !discovery.integrity_error && /^[a-f0-9]{64}$/.test(discovery.catalogue_digest || ''); }
export function discoveryErrorV268(code) {
  return ({
    discovery_admission_invalid: '原发现申请的受理依据无法核对，不能据此授权。请联系管理员核查后再测试。',
    discovery_actor_revoked: '原申请人的管理权限已改变，不能继续采用这份发现。请管理员重新核对。',
    discovery_connection_changed: '连接版本、令牌或启用状态已变化。请刷新当前连接，再重新测试。',
    discovery_attempts_exhausted: '后台测试次数已用尽。请先核对服务状态，再明确发起新的测试。',
    discovery_authentication_failed: '网关拒绝当前访问令牌。请核对并更换令牌，再重新测试。',
    discovery_protocol_failed: '网关响应协议不符合接入约定。请管理员核对测试服务后再测试。',
    discovery_response_bounds: '网关响应超出允许的时间或大小。请管理员调整服务响应后再测试。',
    discovery_timeout: '读取设备声明超时。请核对服务状态与网络，再重新测试。',
    discovery_network_failed: '暂时无法连接测试网关。请核对服务地址与网络，再重新测试。',
    discovery_catalogue_invalid: '设备目录不符合采购参数测试契约，不能据此授权。请管理员核对测试服务。',
  })[code] || '测试或读取没有完成。请核对服务地址、令牌和网关状态，再重新测试。';
}
export const deviceGatewayTimeV268 = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '暂无记录';
export function gatewayErrorV268(error) {
  if (error?.code === 'DEVICE_SCOPE_CHANGED') return '登录身份或工作区已变化。原申请保留，未采用迟到结果；请在原账号工作区重新核对。';
  if (error?.status === 401) return '登录状态需要重新核对，原申请保留。';
  if (error?.status === 403) return '当前账号没有此操作权限，原申请保留。';
  if (error?.status === 409) return '连接版本或原申请已变化。请核对原申请与当前连接后再继续。';
  if (error?.status === 422 || error?.status === 400) return '输入或接入范围未通过服务器核对。请先核对原申请和服务地址。';
  return error?.localMessage || '暂时无法确认服务器结果，原申请保留。请先“核对原申请”，不要另建重复申请。';
}

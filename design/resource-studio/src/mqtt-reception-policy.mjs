export const RECEPTION_POLL_MS = 5000;
export const RECEPTION_REPORT_MAX_AGE_MS = 45000;
export const RECEPTION_HISTORY_LIMIT = 20;
const CLOCK_SKEW_MS = 10000;

const states = {
  waiting_receiver: {label: '等待后台接收服务', tone: 'neutral', detail: '此来源已授权，后台接收服务尚未报告连接。'},
  connecting: {label: '正在连接数据来源', tone: 'pending', detail: '后台正在建立连接，尚未确认主题接收。'},
  subscribing: {label: '正在确认接收主题', tone: 'pending', detail: '后台已连接，正在确认已登记主题的接收权限。'},
  receiving: {label: '后台正在接收', tone: 'good', detail: '已订阅已确认主题，后台会自动接收新报文。尚无原件也可以保持已订阅状态。'},
  backoff: {label: '等待自动重试', tone: 'warning', detail: '接收暂时中断，后台会按计划重试。'},
  blocked: {label: '接收需要处理', tone: 'warning', detail: '后台接收遇到问题，请联系运维核查。'},
  stopped: {label: '后台接收已停止', tone: 'neutral', detail: '后台当前没有继续接收，已保存的原件和处理记录保留。'},
  standby: {label: '等待接收名额', tone: 'pending', detail: '后台正在安排接收此来源，尚未确认连接。'},
  paused: {label: '接收已暂停', tone: 'neutral', detail: '此来源未授权继续接收，原件和处理记录保留。'},
};

const errors = {
  broker_unreachable: '暂时无法连接数据来源，后台会按计划重试。',
  broker_rejected: '数据来源拒绝连接，请联系运维核对接入授权。',
  subscription_rejected: '已登记主题的接收被拒绝，请联系运维核对主题权限。',
  credentials_missing: '接收所需的认证尚未登记，请联系运维补齐。',
  permission_changed: '接收权限已变化，请重新核对授权。',
  configuration_changed: '来源约定已变化，等待后台采用当前配置。',
  storage_failed: '原件保存暂时未完成，请联系运维检查保存服务。',
  lease_lost: '后台接收任务正在重新安排，等待新的报告。',
  connection_lost: '来源连接中断，后台会按计划重试。',
  capacity_wait: '正在等待后台接收名额。',
  stopped: '后台接收已停止。',
  ack_failed: '原件已保存，来源尚未收到接收确认；后台会重连并核对重复报文。',
  packet_rejected: '收到的数据超出当前接入约定，尚未确认接收。请联系运维核对报文大小和协议。',
};

function timestamp(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

export function receptionEpoch(gateway) {
  return JSON.stringify([gateway?.id, gateway?.version, gateway?.config_hash, gateway?.status]);
}

export function receptionErrorMessage(code) {
  if (!code) return '';
  return typeof code === 'string' && Object.hasOwn(errors, code) ? errors[code] : '接收需要核查，请联系运维查看接收记录。';
}

export function currentReceptionReport(gateway, report) {
  return !!(gateway?.id && gateway.version != null && gateway.config_hash && report &&
    report.gateway_id === gateway.id && report.gateway_version === gateway.version &&
    report.configuration_hash === gateway.config_hash);
}

export function olderReceptionReport(previous, incoming) {
  if (!previous || !incoming || previous.gateway_id !== incoming.gateway_id ||
      previous.gateway_version !== incoming.gateway_version ||
      previous.configuration_hash !== incoming.configuration_hash) return false;
  const earlier = timestamp(incoming.last_reported_at), later = timestamp(previous.last_reported_at);
  return later !== null && (earlier === null || earlier < later);
}

export function receptionHistory(report) {
  if (!Array.isArray(report?.history)) return [];
  return report.history.filter(item => item && typeof item === 'object').map((item, index) => {
    const time = timestamp(item.at);
    const known = typeof item.state === 'string' && Object.hasOwn(states, item.state);
    return {
      at: time === null ? null : new Date(time).toISOString(), time, index,
      label: item.state === 'receiving' ? '后台曾确认接收' : known ? states[item.state].label : '接收状态待核查',
      error: receptionErrorMessage(item.error_code),
      version: Number.isSafeInteger(item.gateway_version) && item.gateway_version > 0 ? item.gateway_version : null,
    };
  }).sort((a, b) => (b.time ?? -Infinity) - (a.time ?? -Infinity) || b.index - a.index)
    .slice(0, RECEPTION_HISTORY_LIMIT);
}

export function receptionView(gateway, report, {now = Date.now(), receivedAt, requestState = 'ready'} = {}) {
  const history = report?.gateway_id === gateway?.id ? receptionHistory(report) : [];
  const unknown = (reason, detail) => ({state: 'unknown', label: '当前接收状态未知', tone: 'neutral',
    detail, reason, receiving: false, history});
  const known = state => ({state, ...states[state], reason: '', receiving: state === 'receiving', history});

  if (!gateway?.id) return {state: 'unsaved', label: '尚未保存来源连接', tone: 'neutral',
    detail: '保存来源连接后可查看后台接收状态。', reason: 'unsaved', receiving: false, history: []};
  // Local authorization revocation takes effect in the UI before a polling response arrives.
  if (gateway.status === 'paused') return known('paused');
  if (requestState === 'error') return unknown('request_failed', '暂时无法读取后台接收报告。上次记录仅作历史参考，可刷新再试。');
  if (requestState === 'outdated') return unknown('outdated_report', '后台报告时间早于上次记录，当前接收状态需要重新核查。历史记录保留。');
  if (requestState === 'loading') return unknown('loading', '正在读取后台接收报告…');
  if (gateway.status !== 'active') return unknown('authorization_unknown', '接收授权需要核查，尚不能确认后台接收。');
  if (!currentReceptionReport(gateway, report)) return unknown('configuration_mismatch',
    '尚未取得与当前来源约定一致的后台报告。历史记录保留，等待新的报告。');
  if (report.authorization_state === 'paused') return known('paused');
  if (report.authorization_state !== 'active') return unknown('authorization_unknown', '后台未确认当前接收授权，请核查授权状态。');

  const reported = timestamp(report.last_reported_at);
  const serverTime = timestamp(report.server_time);
  const clock = serverTime !== null && Number.isFinite(receivedAt) ? serverTime + (now - receivedAt) : now;
  const ttl = Number.isFinite(report.heartbeat_ttl_seconds) && report.heartbeat_ttl_seconds > 0
    ? Math.min(RECEPTION_REPORT_MAX_AGE_MS, report.heartbeat_ttl_seconds * 1000) : RECEPTION_REPORT_MAX_AGE_MS;
  const invalidTtl = report.heartbeat_ttl_seconds != null &&
    (!Number.isFinite(report.heartbeat_ttl_seconds) || report.heartbeat_ttl_seconds <= 0);
  const withReportError = detail => detail + (report.error_code ? ' ' + receptionErrorMessage(report.error_code) : '');
  // The API can confirm a current authorization/configuration block without a receiver heartbeat.
  // This exception can never grant a receiving claim, and expires with the GET response itself.
  if (report.authoritative_block === true && report.state === 'blocked') {
    const responseAge = now - receivedAt;
    if (serverTime !== null && Number.isFinite(receivedAt) && Number.isFinite(responseAge) &&
        responseAge >= 0 && responseAge <= ttl && !invalidTtl) {
      const blocked = known('blocked');
      if (report.error_code) blocked.detail = receptionErrorMessage(report.error_code);
      return blocked;
    }
    return unknown('block_confirmation_expired', withReportError('尚未取得新鲜的后台核查报告，当前接收状态需要重新确认。'));
  }
  const unreportedWaiting = report.state === 'waiting_receiver' && report.last_reported_at == null;
  if (!unreportedWaiting && (reported === null || !Number.isFinite(clock) ||
      invalidTtl || (report.server_time != null && serverTime === null) || clock - reported > ttl || reported - clock > CLOCK_SKEW_MS)) {
    return unknown('report_expired', withReportError('后台报告已过期或时间无法核对。最后连通和原件记录仅作历史参考，可刷新再试。'));
  }
  if (report.state === 'stale') return unknown('heartbeat_stale', withReportError('后台接收报告已不再新鲜，当前是否连通需要核查。历史原件保留。'));
  if (typeof report.state !== 'string' || !Object.hasOwn(states, report.state)) return unknown('state_unknown', '接收需要核查，请联系运维查看接收记录。');
  if (report.state === 'receiving' && report.heartbeat_fresh !== true) {
    return unknown('heartbeat_stale', withReportError('后台没有新鲜的接收报告，当前是否连通需要核查。历史原件保留。'));
  }
  if (report.state === 'receiving' && report.error_code != null && report.error_code !== '') {
    return unknown('report_conflict', receptionErrorMessage(report.error_code));
  }
  const view = known(report.state);
  if (report.error_code) view.detail = receptionErrorMessage(report.error_code);
  return view;
}

export function receptionTime(value, empty = '暂无记录') {
  const time = timestamp(value);
  return time === null ? empty : new Date(time).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
}

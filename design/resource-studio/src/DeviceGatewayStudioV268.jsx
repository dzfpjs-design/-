import React, {useEffect, useRef, useState} from 'react';
import {DeviceGatewayClientV268} from './device-gateway-client-v268.mjs';
import {DEVICE_GATEWAY_LABEL_V268, blankDeviceGatewayFormV268, actionLabelV268, isReadActionV268, deviceFamilyViewV268, discoveryIsCurrentV268, discoveryErrorV268, deviceGatewayTimeV268} from './device-gateway-ui-v268.mjs';
import './device-gateway-v268.css';
import DeviceGatewayToolsV269 from './DeviceGatewayToolsV269';

const runtimeJSON = async (...args) => (await import('./runtime-api')).json(...args);
const noop = () => {};
function Button({primary, children, ...props}) { return <button type="button" {...props}>{children}</button>; }
function InputField({label, children}) { return <label className="device-gateway-field-v268"><span>{label}</span>{children}</label>; }
const operationLabel = operation => ({create: '保存测试连接', discover: '测试与读取设备声明', grant: '保存设备网关授权', status: '调整连接状态', credential: '更换访问令牌', register_tool_v269: '将采样动作加入业务能力'})[operation] || '原操作';
const initialState = () => ({connections: [], detail: null, discovery: null, selectedID: '', form: blankDeviceGatewayFormV268(), pending: null, writable: false, loading: true, busy: false, error: '', notice: '', formSaved: true});

export function DeviceGatewayViewV268({state = initialState(), token = '', setToken = noop, edit = noop, select = noop, perform = noop, Btn = Button, Field = InputField, localError = '', clearToken = noop, readTools = null}) {
  const {detail, discovery, form, pending} = state;
  const disabled = state.busy || !state.writable || !!pending;
  const current = discoveryIsCurrentV268(detail, discovery);
  const device = discovery?.devices.find(item => item.external_id === form.device_external_id);
  const readActions = device?.actions.filter(isReadActionV268) || [];
  const controlActions = device?.actions.filter(action => !isReadActionV268(action)) || [];
  const selectedActions = device ? form.actions.filter(action => device.actions.includes(action)) : [];
  const pendingSecret = pending && ['create', 'credential'].includes(pending.operation);
  function actionGroup(actions, title) {
    if (!actions.length) return null;
    return <fieldset className="device-gateway-action-group-v268" disabled={disabled || !current}><legend>{title}</legend>{actions.map(action => <label key={action} className="device-gateway-check-v268"><input type="checkbox" checked={selectedActions.includes(action)} onChange={event => edit({actions: event.target.checked ? [...selectedActions, action] : selectedActions.filter(value => value !== action)})}/><span>{actionLabelV268(action)}</span></label>)}</fieldset>;
  }
  return <section className="device-gateway-v268" aria-label="持久设备连接与授权">
    <div className="device-gateway-heading-v268"><div><h2>设备连接与授权</h2><p>先核对服务与设备声明，再按设备允许需要的能力。</p></div><span className="device-gateway-badge-v268">{DEVICE_GATEWAY_LABEL_V268}</span></div>
    <p className="device-gateway-boundary-v268">本区接入采购参数生成的测试设备。它们没有经过现场身份核验，也不表示物理传感器、相机或无人机已经连接。现有 MQTT 数据接收独立保留。</p>
    {!state.writable && !state.loading && <p className="device-gateway-warning-v268">当前仅可读取。修改需要管理员权限、有效会话和浏览器跨页面锁支持。</p>}
    {(state.error || localError) && <p className="error" role="alert">{localError || state.error}</p>}
    {state.notice && <p className="device-gateway-notice-v268" role="status">{state.notice}</p>}
    {state.loading ? <p role="status">正在读取已保存的设备连接…</p> : <>
      <div className="device-gateway-select-v268"><Field label="已保存的设备连接"><select id="control-gateway-add" value={state.selectedID} disabled={state.busy || !!pending} onChange={event => select(event.target.value)}><option value="">新增测试连接</option>{state.connections.map(connection => <option key={connection.id} value={connection.id}>{connection.name} · {connection.status === 'enabled' ? '已启用' : '已暂停'}</option>)}</select></Field><Btn disabled={state.busy} onClick={() => perform('refresh')}>刷新连接清单</Btn></div>
      {state.hasMore && <p>当前只列出部分连接；尚未提供完整检索，请保留原连接并联系管理员核对。</p>}
      {!state.connections.length && <p>还没有已保存的设备连接。填写服务地址和访问令牌后，可先保存一条测试连接。</p>}
      {pending && <section className="device-gateway-recovery-v268" aria-label="核对原申请"><h3>原申请尚待确认</h3><p>{operationLabel(pending.operation)}的结果尚待核对。原操作和申请号已保留；本页不会自动重发。</p><dl><div><dt>原操作</dt><dd>{operationLabel(pending.operation)}</dd></div><div><dt>原申请号</dt><dd className="device-gateway-reference-v268">{pending.request_id}</dd></div>{pending.meta.name && <div><dt>连接名称</dt><dd>{pending.meta.name}</dd></div>}{pending.meta.origin && <div><dt>服务地址</dt><dd>{pending.meta.origin}</dd></div>}{pending.meta.reason && <div><dt>操作说明</dt><dd>{pending.meta.reason}</dd></div>}</dl><p>先核对保存回执。尚未找到回执不代表原操作一定没有发生；明确重试会沿用同一申请号与原操作内容。</p>
        {pendingSecret && <><Field label="重新输入原申请使用的访问令牌"><input type="password" autoComplete="off" maxLength={4096} value={token} disabled={state.busy || !state.writable} onChange={event => setToken(event.target.value)}/></Field><p>令牌从未保存到浏览器。请重新输入原令牌；修改令牌不能改变这份原申请。</p></>}
        <div className="device-gateway-buttons-v268"><Btn disabled={state.busy} onClick={() => perform('recover')}>核对原申请</Btn><Btn disabled={state.busy || !state.writable || (pendingSecret && !token)} onClick={() => perform('retry')}>明确按原申请重试</Btn></div>
      </section>}
      {!detail ? <fieldset className="device-gateway-form-v268" disabled={disabled}><legend>保存测试连接</legend><div className="device-gateway-fields-v268"><Field label="连接名称"><input value={form.name} maxLength={160} onChange={event => edit({name: event.target.value})} placeholder="例如：第一批采购参数测试设备"/></Field><Field label="服务地址"><input value={form.origin} maxLength={2048} inputMode="url" onChange={event => edit({origin: event.target.value})} placeholder="http://127.0.0.1:端口"/></Field></div><Field label="访问令牌"><input type="password" autoComplete="off" maxLength={4096} value={token} onChange={event => setToken(event.target.value)}/></Field><p>令牌只暂存当前输入框，由服务器加密保存；浏览器恢复记录不含令牌。服务地址仅填协议、主机和端口，末尾不加斜杠，不含账号或查询参数。</p><Btn primary disabled={disabled || !form.name.trim() || !form.origin || !token || !state.formSaved} onClick={() => perform('create')}>保存测试连接</Btn></fieldset> : <>
        <article className="device-gateway-connection-v268"><div className="device-gateway-heading-v268"><h3>{detail.name}</h3><span>{detail.status === 'enabled' ? '连接已启用' : '连接已暂停'}</span></div><dl><div><dt>服务地址</dt><dd>{detail.origin}</dd></div><div><dt>已保存版本</dt><dd>第 {detail.version} 版</dd></div><div><dt>访问令牌</dt><dd>{detail.credential_configured ? '已在服务器加密保存' : '尚未配置'}</dd></div><div><dt>建立时间</dt><dd>{deviceGatewayTimeV268(detail.created_at)}</dd></div></dl><p>启用仅允许按授权范围访问网关，不启动采集、云台控制或无人机作业。</p></article>
        <Field label="本次操作说明"><textarea value={form.reason} maxLength={2000} disabled={disabled} onChange={event => edit({reason: event.target.value})} placeholder="说明本次测试、授权、暂停或更换令牌的目的"/></Field>
        <div className="device-gateway-buttons-v268"><Btn disabled={disabled || !form.reason.trim()} onClick={() => perform('status', {status: detail.status === 'enabled' ? 'paused' : 'enabled'})}>{detail.status === 'enabled' ? '暂停连接' : '启用连接'}</Btn><Btn primary disabled={disabled || detail.status !== 'enabled' || !form.reason.trim() || ['queued', 'running'].includes(discovery?.status)} onClick={() => perform('discover')}>测试连接并读取设备声明</Btn></div>
        {detail.status === 'paused' && <p>先填写操作说明并启用连接，再测试服务和读取设备声明。暂停不删除历史发现或授权记录。</p>}
        <details className="device-gateway-credential-v268"><summary>更换访问令牌</summary><Field label="新访问令牌"><input type="password" autoComplete="off" maxLength={4096} value={token} disabled={disabled} onChange={event => setToken(event.target.value)}/></Field><p>更换后旧发现与授权将过期，需要重新读取并核对设备。旧记录保留。</p><Btn disabled={disabled || !token || !form.reason.trim()} onClick={() => perform('credential')}>保存新访问令牌</Btn></details>
        <section className="device-gateway-discovery-v268" aria-label="设备清单"><div className="device-gateway-heading-v268"><h3>已读取的设备声明</h3>{discovery && <span>{({queued: '等待后台测试', running: '正在测试与读取', succeeded: current ? '当前版本已读取' : '历史发现已过期', failed: '测试未完成', invalid: '发现依据核对失败'})[discovery.status]}</span>}</div>
          {!discovery ? <p>尚未测试连接。保存配置或启用连接不能证明服务连通。</p> : <><p>建立于 {deviceGatewayTimeV268(discovery.created_at)} · 对应连接第 {discovery.connection_version} 版。</p>{['queued', 'running'].includes(discovery.status) && <p role="status">后台正在处理；本页仅在当前连接启用时更新状态。离开页面会停止本页等待，服务器记录保留。</p>}{discovery.status === 'failed' && <p className="error" role="alert">{discoveryErrorV268(discovery.error_code)} 原发现记录保留。</p>}{discovery.integrity_error && <p className="error" role="alert">这份发现记录的完整性无法核对，不能据此授权。请重新测试。</p>}{!current && discovery.status === 'succeeded' && <p className="device-gateway-warning-v268">这份发现已不适用于当前连接或令牌版本。请重新读取设备声明，再保存新的授权。</p>}{discovery.status === 'succeeded' && !discovery.devices.length && <p>本次服务返回了空设备清单。请核对采购参数和服务设置；空清单不表示现场没有设备。</p>}
            <div className="device-gateway-devices-v268">{discovery.devices.map(item => { const family = deviceFamilyViewV268(item); return <article key={item.external_id}><h4>{item.name}</h4><span className="device-gateway-badge-v268">{family.label} · 测试设备</span><p>{family.note}</p><p>现场身份未核验 · 物理设备未接通</p><p>声明能力：{item.actions.map(actionLabelV268).join('、') || '暂无能力'}</p></article>; })}</div>
          </>}
        </section>
        <section className="device-gateway-grant-v268" aria-label="按设备保存网关授权"><h3>允许此网关提供哪些设备能力</h3><p>读取与控制分别勾选。本次只保存网关授权，不代表物理身份确认、业务自动化批准或设备执行批准。</p><Field label="选择设备声明"><select value={device ? form.device_external_id : ''} disabled={disabled || !current} onChange={event => edit({device_external_id: event.target.value, actions: []})}><option value="">请选择已读取的测试设备</option>{discovery?.devices.map(item => <option key={item.external_id} value={item.external_id}>{item.name}（测试设备）</option>)}</select></Field>{device && <p>{deviceFamilyViewV268(device).note}</p>}<div className="device-gateway-action-columns-v268">{actionGroup(readActions, '允许读取')}{actionGroup(controlActions, '允许控制能力')}</div><Btn primary disabled={disabled || !current || !device || !selectedActions.length || !form.reason.trim()} onClick={() => perform('grant', {discovery_job_id: discovery.id, device_external_id: device.external_id, actions: selectedActions})}>保存此设备的网关授权</Btn></section>
        <section className="device-gateway-grants-v268" aria-label="已保存授权"><h3>已保存的设备授权</h3>{!detail.grants?.length ? <p>尚未保存设备授权。</p> : <ul>{detail.grants.map(grant => <li key={grant.id}><strong>{discovery?.devices.find(item => item.external_id === grant.device_external_id)?.name || '历史设备声明'}</strong><span>{grant.integrity_error ? '授权依据核对失败，不能采用' : grant.effective && grant.connection_version === detail.version && grant.credential_revision === detail.credential_revision ? '当前网关授权' : '历史授权已过期'}</span><p>{grant.actions.map(actionLabelV268).join('、')}</p><small>保存于 {deviceGatewayTimeV268(grant.created_at)} · 仅网关范围</small></li>)}</ul>}</section>
      </>}
      {token && <div className="device-gateway-buttons-v268"><Btn disabled={state.busy} onClick={clearToken}>清除当前输入框中的令牌</Btn><small>令牌未保存到浏览器。离开前可主动清除。</small></div>}
      {!state.formSaved && <p className="error" role="alert">当前编辑尚未可靠保存，操作入口已停用。请保留页面并重试编辑保存。</p>}
    </>}
    {detail && readTools && <DeviceGatewayToolsV269 connection={detail} writable={state.writable} busy={state.busy} pending={pending} receipt={state.toolReceipt} readTools={readTools} onRegister={extra => perform('register_tool_v269',extra)} Btn={Btn}/>}
  </section>;
}

export default function DeviceGatewayStudioV268({account, api = runtimeJSON, Btn = Button, Field = InputField, onBusy = noop, onRegisterGuard = noop}) {
  const [state, setState] = useState(initialState), [token, setToken] = useState(''), [localError, setLocalError] = useState('');
  const scopeKey = [account?.workspace_id, account?.user_id, account?.role].join(':');
  const currentView = state.viewScope === scopeKey;
  const visibleState = currentView ? state : initialState();
  const client = useRef(null), accountRef = useRef(account), tokenRef = useRef(token), busyRef = useRef(onBusy), registerRef = useRef(onRegisterGuard);
  accountRef.current = account; tokenRef.current = token; busyRef.current = onBusy; registerRef.current = onRegisterGuard;
  useEffect(() => {
    const scope = {actor: account?.user_id, workspace: account?.workspace_id, role: account?.role};
    const isCurrent = () => accountRef.current?.user_id === scope.actor && accountRef.current?.workspace_id === scope.workspace && accountRef.current?.role === scope.role;
    let instance;
    try { instance = new DeviceGatewayClientV268({account, api, isCurrent, onChange: value => { if (isCurrent()) setState({...value, viewScope: scopeKey}); }}); client.current = instance; setToken(''); setLocalError(''); setState({...initialState(), viewScope: scopeKey}); instance.start().catch(noop); }
    catch (error) { setState({...initialState(), viewScope: scopeKey, loading: false, error: error.localMessage || '当前会话无法核对，设备连接暂不能读取。'}); }
    const guard = () => { const allowed = !!instance && instance.canLeave() && !tokenRef.current; if (!allowed) setLocalError(tokenRef.current ? '输入框中仍有未提交令牌。请先保存或主动清除，再离开。' : '设备连接的原申请或编辑尚待核对，请先处理本区提示。'); return allowed; };
    registerRef.current?.(guard);
    return () => { instance?.dispose(); if (client.current === instance) client.current = null; busyRef.current?.(false); registerRef.current?.(null, guard); };
  }, [account?.user_id, account?.workspace_id, account?.role, api]);
  useEffect(() => { busyRef.current?.(visibleState.busy); }, [visibleState.busy]);
  useEffect(() => {
    if (state.detail?.status !== 'enabled' || !['queued', 'running'].includes(state.discovery?.status) || state.pending) return;
    const instance = client.current;
    const timer = setInterval(() => { if (client.current === instance && !instance.busy) instance.pollDiscovery().catch(noop); }, 3000);
    return () => clearInterval(timer);
  }, [state.selectedID, state.detail?.status, state.discovery?.id, state.discovery?.status, !!state.pending]);
  function edit(patch) { setLocalError(''); try { client.current?.edit(patch); } catch (error) { setLocalError(error.localMessage || '编辑尚未可靠保存，请保留页面重试。'); } }
  async function perform(operation, extra) {
    const instance = client.current; if (!instance) return; setLocalError('');
    try {
      if (operation === 'refresh') await instance.refresh();
      else if (operation === 'recover') await instance.recover();
      else if (operation === 'retry') await instance.retry(token);
      else if (operation === 'create') await instance.create(token);
      else await instance.command(operation, extra, token);
    } catch { /* The controller emits a bounded, credential-free error. */ }
    finally { if (['retry', 'create', 'credential'].includes(operation)) setToken(''); }
  }
  return <DeviceGatewayViewV268 state={visibleState} token={currentView ? token : ''} setToken={value => { setToken(value); setLocalError(''); }} readTools={() => client.current?.readTools()} clearToken={() => { setToken(''); setLocalError(''); }} edit={edit} select={id => client.current?.select(id).catch(noop)} perform={perform} Btn={Btn} Field={Field} localError={currentView ? localError : ''}/>;
}

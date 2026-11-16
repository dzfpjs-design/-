import React, {useEffect, useRef, useState} from 'react';
import {actionLabelV268} from './device-gateway-ui-v268.mjs';

const reason = value => ({current:'当前配置可供业务流程选用',connection_paused:'连接已暂停',authorization_changed:'原授权已变化，请核对后重新登记',capability_not_published:'当前能力未发布',integrity_invalid:'登记依据核对失败，请保留记录并联系管理员'})[value] || '配置状态尚待核对';

export default function DeviceGatewayToolsV269({connection, writable, busy, pending, receipt, readTools, onRegister, Btn}) {
  const owner = connection.id + ':' + connection.version + ':' + connection.credential_revision
    + ':' + JSON.stringify(connection.grants.map(row => [row.id,row.version,row.effective,row.integrity_error]));
  const [state,setState] = useState({owner:null,loading:true,tools:[],error:'',hasMore:false});
  const current = useRef(owner), reader = useRef(readTools), generation = useRef(0);
  current.current = owner; reader.current = readTools;
  async function load() {
    const ticket = ++generation.current, source = owner;
    setState({owner:source,loading:true,tools:[],error:'',hasMore:false});
    try {
      const value = await reader.current();
      if (ticket !== generation.current || current.current !== source) return;
      if (!Array.isArray(value?.tools) || typeof value.has_more !== 'boolean'
          || value.tools.some(row => row.connection_id !== connection.id || typeof row.integrity_valid !== 'boolean' || typeof row.configuration_current !== 'boolean')) throw Error();
      setState({owner:source,loading:false,tools:value.tools,error:'',hasMore:value.has_more});
    } catch {
      if (ticket === generation.current && current.current === source) setState({owner:source,loading:false,tools:[],error:'业务能力清单暂时无法核对，已保存登记保留；请重试读取。',hasMore:false});
    }
  }
  useEffect(() => { load(); return () => { generation.current++; }; }, [owner,receipt?.request_id]);
  const shown = state.owner === owner ? state : {loading:true,tools:[],error:'',hasMore:false};
  const grants = connection.grants.filter(grant => grant.effective === true && !grant.integrity_error);
  return <section className="device-gateway-tools-v269" aria-label="设备业务能力登记">
    <h3>可供业务流程使用的采样能力</h3>
    <p>将已授权的读取动作加入能力清单，由业务驱动智能体在获批流程中选择使用。登记前请填写上方“本次操作说明”。</p>
    {shown.loading ? <p role="status">正在读取已登记的业务能力…</p> : shown.error ? <p role="alert">{shown.error}</p> : <>
      {!grants.length && <p>当前没有可登记的设备读取授权。请先核对设备清单和读取权限。</p>}
      {grants.map(grant => <div key={grant.id}><h4>{connection.latest_discovery?.devices.find(device => device.external_id === grant.device_external_id)?.name || grant.device_external_id}</h4>
        {grant.actions.filter(action => ['get_status','read_telemetry'].includes(action)).map(action => {
          const registered = shown.tools.find(tool => tool.device_external_id === grant.device_external_id && tool.action === action && tool.configuration_current && tool.integrity_valid && tool.status === 'published');
          return <div className="device-gateway-capability-v269" key={action}><span>{actionLabelV268(action)}</span>{registered ? <span>已加入业务能力 · 版本 {registered.release_version}</span> : <Btn disabled={!writable || busy || !!pending || connection.status !== 'enabled'} onClick={() => onRegister({grant_id:grant.id,grant_expected_version:grant.version,action})}>加入业务能力：{actionLabelV268(action)}</Btn>}</div>;
        })}</div>)}
      {shown.tools.length > 0 && <details><summary>查看登记历史（{shown.tools.length}）</summary>{shown.tools.map(tool => <p key={tool.id}>{actionLabelV268(tool.action)} · 版本 {tool.release_version} · {reason(tool.availability_reason)}</p>)}</details>}
      {shown.hasMore && <p>当前显示部分登记历史，请保留原申请号以便查回完整记录。</p>}
    </>}
    <Btn disabled={shown.loading || busy} onClick={load}>重新读取业务能力清单</Btn>
  </section>;
}

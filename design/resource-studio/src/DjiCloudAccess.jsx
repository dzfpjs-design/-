import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import ManagedMqttAuthorizations from './ManagedMqttAuthorizations';

const empty=()=>({name:'',broker:'',device_sn:''});
const stamp=value=>{if(!value)return '未知';const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未知'};
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const normal=value=>typeof value==='string'&&value.length<=200&&!/[\u0000-\u001f\u007f]/.test(value);
const reference=value=>value===''||(typeof value==='string'&&/^GA_MQTT_[A-Z0-9_]{1,80}$/.test(value));
const notice='仅接收已上云的机场状态，暂不支持设备上云或飞行控制。';
const receiverLabel={connecting:'正在连接',subscribing:'正在订阅状态',receiving:'正在接收',backoff:'连接中断，等待重试',blocked:'接入已阻止',stopped:'已停止',standby:'等待其他接收器',paused:'已暂停',stale:'接收器状态已过期',waiting_receiver:'等待接收器'};
const receiverError={broker_unreachable:'无法连接服务器',broker_rejected:'服务器地址未获授权',subscription_rejected:'服务器拒绝订阅',credentials_missing:'服务端凭据不可用',permission_changed:'接入所有者权限已变化',configuration_changed:'接入配置已变化',storage_failed:'原始报文保存失败',lease_lost:'接收器工作权已失效',connection_lost:'连接已中断',capacity_wait:'等待接收容量',ack_failed:'接收确认失败',packet_rejected:'报文未被接纳',receiver_unavailable:'接收状态暂不可用'};

/** Read-only DJI Cloud API 1 status through an explicitly authorised MQTT broker. */
export default function DjiCloudAccess(props){
 const scope=[props.account?.workspace_id,props.account?.user_id,props.account?.role].join(':');
 return <Panel key={scope} {...props}/>;
}
function Panel({account,Btn,request=json,onRegisterGuard,onOpenMqtt}){
 const scope=account?.workspace_id+':'+account?.user_id,key='dji-cloud:'+scope+':pending',draftKey='dji-cloud:'+scope+':draft';
 const [options,setOptions]=useState(null),[connections,setConnections]=useState(null),[page,setPage]=useState(1),[hasMore,setHasMore]=useState(false),[form,setForm]=useState(empty),[pending,setPending]=useState(null),[selected,setSelected]=useState(''),[reading,setReading]=useState(null),[stale,setStale]=useState(false),[confirmEnable,setConfirmEnable]=useState(''),[confirmTool,setConfirmTool]=useState(''),[busy,setBusy]=useState(false),[editor,setEditor]=useState(false),[editorAttempt,setEditorAttempt]=useState(0),[error,setError]=useState(''),[statusError,setStatusError]=useState(''),[message,setMessage]=useState(''),[storageError,setStorageError]=useState(false),[attempt,setAttempt]=useState(0);
 const alive=useRef(false),current=useRef({}),selectedRef=useRef(''),seq=useRef(0),guardRef=useRef(onRegisterGuard),mqttGuard=useRef(null),working=useRef(false);
 selectedRef.current=selected;guardRef.current=onRegisterGuard;current.current={pending,busy,storageError};
 const req=(path,opts={})=>request(path,{...opts,headers:{...(opts.headers||{}),'X-Workspace-ID':account.workspace_id}});
 const role=account.role,admin=role==='admin';
 const runLock=fn=>{if(!navigator.locks?.request)throw Error('当前浏览器无法保护多标签页的原请求，请使用支持安全锁的浏览器。');return navigator.locks.request(key+':operation',{mode:'exclusive'},fn)};
 function readPending(){const raw=localStorage.getItem(key);if(!raw)return null;const v=JSON.parse(raw);
  if(!v||v.scope!==scope||!['create','status','publish_tool'].includes(v.kind)||!uuid(v.request_id)||!v.body||v.body.request_id!==v.request_id)throw Error('本机原请求无法核对');
  if(v.kind==='create'){
   const b=v.body,managed=normal(b.authorization_id)&&!!b.authorization_id&&b.broker===undefined&&b.username_ref===undefined&&b.password_ref===undefined;
   const legacy=normal(b.broker)&&!!b.broker&&b.authorization_id===undefined&&reference(b.username_ref)&&reference(b.password_ref)&&Boolean(b.username_ref)===Boolean(b.password_ref);
   if(!normal(b.name)||!b.name.trim()||!/^[A-Za-z0-9_-]{1,64}$/.test(b.device_sn)||!normal(v.expected_broker)||!v.expected_broker||!(managed||legacy))throw Error('原接入内容无法核对');
  }
  if(v.kind==='status'&&(!normal(v.id)||!['active','paused'].includes(v.body.status)||!Number.isSafeInteger(v.body.expected_version)))throw Error('原状态请求无法核对');
  if(v.kind==='publish_tool'&&(!normal(v.id)||v.body.confirm!==true||!Number.isSafeInteger(v.body.expected_version)))throw Error('原能力发布请求无法核对');
  return v;
 }
 async function load(nextPage=1,append=false){const [o,c]=await Promise.all([admin?req('/dji/cloud/options'):Promise.resolve({brokers:[]}),req('/dji/cloud/connections?page='+nextPage)]);
  if(!Array.isArray(o?.brokers)||!Array.isArray(c?.connections)||c.page!==nextPage||typeof c.has_more!=='boolean')throw Error('接入清单无法核对');
  if(alive.current){setOptions(o);setPage(nextPage);setHasMore(c.has_more);setConnections(old=>append?[...(old||[]),...c.connections.filter(x=>!(old||[]).some(y=>y.id===x.id))]:c.connections);if(!append)setSelected(old=>c.connections.some(x=>x.id===old)?old:'');}return c.connections;
 }
 useEffect(()=>{alive.current=true;setOptions(null);setConnections(null);setError('');setMessage('');setStorageError(false);setPending(null);setReading(null);
  try{setPending(readPending());const raw=localStorage.getItem(draftKey);if(raw){const x=JSON.parse(raw);if(!x||Object.keys(empty()).some(k=>!normal(x[k])))throw Error();setForm(Object.fromEntries(Object.keys(empty()).map(k=>[k,x[k]])))}else setForm(empty())}catch{setStorageError(true);setError('本机原请求或草稿无法读取，已暂停操作；原记录保留。')}
  load().catch(e=>{if(alive.current)setError('无法读取大疆来源：'+e.message)});
  const changed=e=>{if(e.key===key){try{setPending(readPending())}catch{setStorageError(true);setError('本机原请求无法核对，已暂停操作。')}}};window.addEventListener('storage',changed);
  return()=>{alive.current=false;seq.current++;window.removeEventListener('storage',changed)};
 },[scope]);
 useEffect(()=>{if(!admin)return;let stopped=false,release;setEditor(false);
  if(!navigator.locks?.request){setError('当前浏览器无法保护多标签页草稿，已暂停编辑。');return}
  navigator.locks.request(key+':editor',{mode:'exclusive',ifAvailable:true},async lock=>{
   if(stopped)return;if(!lock){setEditor(false);return}
   try{const raw=localStorage.getItem(draftKey);if(raw){const x=JSON.parse(raw);if(Object.keys(empty()).some(k=>!normal(x[k])))throw Error();setForm(Object.fromEntries(Object.keys(empty()).map(k=>[k,x[k]])))}setPending(readPending());setEditor(true);await new Promise(resolve=>{release=resolve})}catch{setStorageError(true);setError('草稿或原请求无法读取，已暂停编辑。')}
  }).catch(()=>{if(!stopped)setError('无法保护草稿，已暂停编辑。')});return()=>{stopped=true;release?.()};
 },[scope,role,editorAttempt]);
 useEffect(()=>{const guard=()=>!working.current&&!current.current.busy&&!current.current.pending&&!current.current.storageError&&(!mqttGuard.current||mqttGuard.current());guardRef.current?.(guard);return()=>guardRef.current?.(null,guard)},[]);
 useEffect(()=>{const source=connections?.find(x=>x.id===selected);let stopped=false,timer,controller;const n=++seq.current;setReading(null);setStale(false);setStatusError('');setConfirmEnable('');setConfirmTool('');if(!source)return()=>{};
  async function poll(){controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);try{const v=await req('/dji/cloud/connections/'+encodeURIComponent(source.id)+'/status',{signal:controller.signal});
    if(v?.connection?.id!==source.id)throw Error('来源范围已变化');
    if(!stopped&&alive.current&&n===seq.current){setReading(v);setStale(false);setConnections(old=>(old||[]).map(x=>x.id===source.id&&v.connection.version>=x.version?v.connection:x));setStatusError('')}
   }catch(e){if(!stopped&&alive.current&&n===seq.current){setStale(true);setStatusError('读取设备状态失败：'+(e.message||'请稍后重试'))}}
   finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(poll,15000)}}
  poll();return()=>{stopped=true;clearTimeout(timer);controller?.abort()};
 },[selected,attempt,account.workspace_id,account.user_id]);
 function edit(patch){const next={...form,...patch};setForm(next);try{localStorage.setItem(draftKey,JSON.stringify(next));setStorageError(false);setError('')}catch{setStorageError(true);setError('草稿无法保存在本机，请检查浏览器存储。')}
 }
 async function retain(value){await runLock(()=>{if(!alive.current)throw Error('工作区已变化');if(readPending())throw Error('已有原请求待核对');const raw=JSON.stringify(value);localStorage.setItem(key,raw);if(localStorage.getItem(key)!==raw)throw Error('本机无法保存原请求');setPending(value)})}
 async function release(value){await runLock(()=>{if(!alive.current)throw Error('工作区已变化，原请求保留');const stored=readPending();if(!stored||stored.request_id!==value.request_id||JSON.stringify(stored)!==JSON.stringify(value))throw Error('原请求已变化，请重新核对');localStorage.removeItem(key);if(localStorage.getItem(key)!==null)throw Error('原请求无法安全清理');setPending(null)})}
 async function resolved(value,reply){if(reply?.request_id!==value.request_id||!['succeeded','cancelled'].includes(reply.state))throw Error('服务器原请求结果尚未确定');
  if(reply.state==='succeeded'&&value.kind!=='publish_tool'&&(!reply.connection||typeof reply.connection.id!=='string'))throw Error('接入回执无法核对');
  if(reply.state==='succeeded'&&value.kind==='publish_tool'&&(reply.connection_id!==value.id||reply.capability?.handler!=='dji_cloud_dock_status_v1'||reply.capability?.status!=='published'||typeof reply.capability?.id!=='string'))throw Error('能力发布回执与原请求不一致');
  if(reply.state==='succeeded'&&value.kind==='create'&&(reply.connection.name!==value.body.name||reply.connection.broker!==value.expected_broker||reply.connection.device_sn!==value.body.device_sn||reply.connection.topic!=='thing/product/'+value.body.device_sn+'/osd'||reply.connection.status!=='paused'||reply.connection.version!==1))throw Error('接入回执与原请求不一致');
  if(reply.state==='succeeded'&&value.kind==='status'&&(reply.connection.id!==value.id||reply.connection.status!==value.body.status||reply.connection.version!==value.body.expected_version+1))throw Error('状态回执与原请求不一致');
  await release(value);if(!alive.current)return;
  if(value.kind==='create'&&reply.state==='succeeded'){setForm(empty());try{localStorage.removeItem(draftKey)}catch{setStorageError(true);setError('操作已保存，但本机草稿无法清除；请检查浏览器存储。')}}
  setMessage(reply.state==='cancelled'?'原请求已安全结束。':value.kind==='publish_tool'?'只读工具已发布；设备控制能力未开放。':'操作已保存，正在重新读取状态。');
  try{const prior=reply.connection||(reply.connection_id&&connections?.find(x=>x.id===reply.connection_id));const rows=await load();if(prior){if(!rows.some(x=>x.id===prior.id))setConnections(old=>[prior,...(old||[])]);setSelected(prior.id);setAttempt(x=>x+1)}}catch{setError('操作已保存，但暂时无法刷新清单；请重新读取。')}
 }
 async function perform(kind,body,id,expectedBroker){if(busy||working.current||pending||storageError||!admin||!editor)return;working.current=true;setBusy(true);setError('');setMessage('');
  const value={scope,kind,request_id:body.request_id,body,...(id?{id}:{}),...(kind==='create'?{expected_broker:expectedBroker}:{})};try{await retain(value);
   const path=kind==='create'?'/dji/cloud/connections':'/dji/cloud/connections/'+encodeURIComponent(id)+(kind==='publish_tool'?'/capability':'/status');
   const reply=await req(path,{method:'POST',body:JSON.stringify(body)});await resolved(value,reply)
  }catch(e){if(alive.current)setError('操作结果尚未核对：'+e.message+'。请先核对原请求，不要重复提交。')}
  finally{working.current=false;if(alive.current)setBusy(false)}
 }
 async function recover(cancel=false){if(!pending||working.current||busy)return;working.current=true;setBusy(true);setError('');try{
   const path='/dji/cloud/operations/'+encodeURIComponent(pending.request_id)+(cancel?'/cancel':'');
   const reply=await req(path,cancel?{method:'POST'}:{});await resolved(pending,reply)
  }catch(e){if(alive.current)setError('原请求尚未核对：'+e.message+'。记录仍保留。')}
  finally{working.current=false;if(alive.current)setBusy(false)}
 }
 async function create(e){e.preventDefault();if(!form.name.trim()||!form.broker||!/^[A-Za-z0-9_-]{1,64}$/.test(form.device_sn)){setError('请填写名称、服务器和正确的机场序列号。');return}
  const chosen=options?.brokers.find(x=>x.value===form.broker);if(!chosen){setError('请选择已授权的 MQTT 服务。');return}
  const body={request_id:crypto.randomUUID(),name:form.name.trim(),device_sn:form.device_sn};
  if(chosen.authorization_id)body.authorization_id=chosen.authorization_id;else Object.assign(body,{broker:chosen.broker||chosen.value,username_ref:'',password_ref:''});
  await perform('create',body,null,chosen.broker||chosen.value);
 }
 async function refreshAuthorizations(){try{const o=await req('/dji/cloud/options');if(!Array.isArray(o?.brokers))throw Error('授权选项无法核对');if(alive.current){setOptions(o);setAttempt(x=>x+1)}}catch(e){if(alive.current)setError('服务器授权已更改，但无法刷新选择：'+e.message)}}
 const source=connections?.find(x=>x.id===selected),capability=reading?.connection?.capability??reading?.capability??source?.capability??null,unavailable=source?.configuration_valid===false||source?.authorization_available===false,disabled=busy||!!pending||storageError||!editor;
 return <section className="local-model-card" aria-label="大疆 Cloud API 只读接入"><h2>大疆 Cloud API 只读接入</h2><p>{notice}</p><details><summary>接入条件</summary><p>本入口不会完成大疆设备上云握手。请先在大疆或第三方上云平台完成设备上云，并准备已授权的 MQTT 接入。当前按 Cloud API 1 机场状态格式接入；精灵 4 RTK 须另行核对其遥控器与 SDK。</p></details>
  {onOpenMqtt&&<Btn onClick={onOpenMqtt}>查看传感器网关</Btn>}
  <ManagedMqttAuthorizations account={account} Btn={Btn} request={request} onChanged={refreshAuthorizations} onRegisterGuard={(guard,previous)=>{if(guard)mqttGuard.current=guard;else if(!previous||mqttGuard.current===previous)mqttGuard.current=null;}}/>
  {error&&<p role="alert">{error}</p>}{statusError&&<p role="alert">{statusError} <Btn onClick={()=>setAttempt(x=>x+1)}>重新读取状态</Btn></p>}{message&&<p role="status">{message}</p>}
  {pending&&<div className="media-form"><h3>上次操作结果待核对</h3><p>刷新后仍可找回原请求；核对前不会再提交新的接入或状态修改。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn> <Btn disabled={busy} onClick={()=>recover(true)}>安全结束原请求</Btn></div>}
  {admin&&!editor&&!storageError&&<p role="status">此账号的另一个页面正在编辑。关闭原页面后可在这里接手。 <Btn onClick={()=>setEditorAttempt(x=>x+1)}>重新取得编辑权</Btn></p>}
  {admin&&<details open={!connections?.length}><summary>添加大疆机场状态来源</summary>
   {!options?<p>正在读取允许的服务器…</p>:!options.brokers.length?<p>服务器尚未配置允许接入的 MQTT 地址，请由系统管理员先完成网络授权。</p>:<form onSubmit={create}><fieldset disabled={disabled}>
    <label>来源名称 <input value={form.name} maxLength={100} onChange={e=>edit({name:e.target.value})} placeholder="例如：林场北部机场"/></label>
    <label>已允许的 MQTT 服务 <select value={form.broker} onChange={e=>edit({broker:e.target.value})}><option value="">请选择</option>{options.brokers.map(x=><option key={x.value} value={x.value}>{x.label}</option>)}</select></label>
    <label>机场设备序列号 <input value={form.device_sn} maxLength={64} onChange={e=>edit({device_sn:e.target.value})} autoComplete="off"/></label>
    <p>系统将读取 <code>thing/product/{form.device_sn||'设备序列号'}/osd</code>。请核对设备和主题；收到报文不等于完成设备身份认证。</p>
    <p>请选择已登记的服务器授权；历史服务器地址仅支持匿名接入。</p><Btn type="submit" disabled={disabled}>保存来源</Btn></fieldset></form>}
  </details>}
  <h3>已登记的机场状态来源</h3>{connections===null?<p>正在读取…</p>:!connections.length?<p>尚未登记来源。</p>:<label>选择来源 <select value={selected} onChange={e=>{setSelected(e.target.value);setError('')}}><option value="">请选择</option>{connections.map(x=><option key={x.id} value={x.id}>{x.name} · {x.status==='active'?'已启用':'已暂停'}</option>)}</select></label>}{hasMore&&<Btn disabled={busy} onClick={()=>load(page+1,true).catch(e=>setError('无法读取更多来源：'+e.message))}>加载更多来源</Btn>}
  {source&&<article className="media-form"><h3>{source.name}</h3><p>设备序列号：{source.device_sn}；接收：{source.status==='active'?'已启用':'已暂停'}。保存配置不代表设备已连接。</p><p>服务器：{source.broker}；状态主题：{source.topic}。</p>
   {source.configuration_valid===false&&<p role="alert">该来源的服务器或主题已在其他位置改变，请重新登记并核对；当前不能启用。</p>}
   {source.authorization_available===false&&<p role="alert">MQTT 服务授权已停用或不可用，不能接收新状态，智能体工具也不可读取。请核对上方服务授权。</p>}
   {admin&&(source.status==='active'?<Btn disabled={disabled} onClick={()=>perform('status',{request_id:crypto.randomUUID(),expected_version:source.version,status:'paused'},source.id)}>暂停接收</Btn>:<Btn disabled={disabled||unavailable} onClick={()=>setConfirmEnable(source.id)}>启用接收</Btn>)}
   {admin&&confirmEnable===source.id&&source.status!=='active'&&<div><p>请确认上述服务器、序列号和状态主题属于您要接入的机场。启用后仅接收状态，不会发送飞行指令。</p><Btn disabled={disabled} onClick={()=>{setConfirmEnable('');perform('status',{request_id:crypto.randomUUID(),expected_version:source.version,status:'active'},source.id)}}>确认启用接收</Btn> <Btn onClick={()=>setConfirmEnable('')}>取消</Btn></div>}
   <h4>供智能体读取机场状态</h4>{capability?.status==='published'?<p>已发布只读工具：{capability.name}；{unavailable||source.status!=='active'?'当前接入不可用，工具不能读取。':'接入暂停或授权变化后工具将不能读取。'}</p>:capability?<p role="alert">只读工具已登记，但当前未发布或记录需核对，请联系管理员处理。</p>:!reading?<p>正在核对工具状态…</p>:<p>尚未为此来源发布智能体只读工具。</p>}
   {admin&&!capability&&<Btn disabled={disabled||!reading||source.status!=='active'||unavailable} onClick={()=>setConfirmTool(source.id)}>配置只读工具</Btn>}
   {admin&&confirmTool===source.id&&!capability&&<div><p>智能体将仅可读取当前机场已保存的状态和接收情况；此能力不包含起飞、航线、返航或其他设备控制。设备状态需核实。</p><Btn disabled={disabled||source.status!=='active'||unavailable} onClick={()=>{setConfirmTool('');perform('publish_tool',{request_id:crypto.randomUUID(),expected_version:source.version,confirm:true},source.id)}}>确认发布只读工具</Btn> <Btn onClick={()=>setConfirmTool('')}>取消</Btn></div>}
   {stale&&reading&&<p role="status">状态更新失败，下方为上次成功读取的记录，可能已经过期。</p>}
   {!reading?<p>{stale?'暂时无法读取状态，请检查连接后重试。':'正在读取已保存的状态…'}</p>:<><p>接收器：{receiverLabel[reading.receiver?.state]||'尚未确认'}；最近心跳：{stamp(reading.receiver?.last_reported_at)}。</p>{reading.receiver?.error_code&&<p role="status">接收提示：{receiverError[reading.receiver.error_code]||'状态需要核对'}{reading.receiver.next_retry_at?'；计划重试时间：'+stamp(reading.receiver.next_retry_at):''}。</p>}
    {!reading.latest?<p>尚未收到此机场的状态报文。</p>:!reading.latest.parsed?<p role="alert">最新报文无法按大疆状态格式核对，原件已保留。接收时间：{stamp(reading.latest.received_at)}。</p>:<>
     <p>设备报告时间：{stamp(reading.latest.reported?.reported_at)}；系统接收时间：{stamp(reading.latest.received_at)}{reading.latest.retained?'；该报文为保留消息':''}。</p>
     <p>设备状态码：{reading.latest.reported?.mode_code??'未知'}；飞行器在舱：{reading.latest.reported?.aircraft_in_dock===1?'设备报告在舱':reading.latest.reported?.aircraft_in_dock===0?'设备报告不在舱':'未知'}；电量：{reading.latest.reported?.battery_percent==null?'未知':reading.latest.reported.battery_percent+'%'}。</p>
     <p>风速：{reading.latest.reported?.wind_m_s==null?'未知':reading.latest.reported.wind_m_s+' 米/秒'}；温度：{reading.latest.reported?.temperature_c==null?'未知':reading.latest.reported.temperature_c+' ℃'}。</p>
     <p>{reading.latest.reported?.position?`设备报告坐标：${reading.latest.reported.position.latitude}，${reading.latest.reported.position.longitude}。`:reading.latest.reported?.position_note||'设备坐标未知。'} {reading.latest.state_note}</p>
    </>}</>}
   <p>所示状态为设备报文中的声明，不代表设备身份、实时在线或满足安全起飞条件。</p>
  </article>}
 </section>;
}

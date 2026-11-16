import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const initial=()=>({broker_authorization_id:'',dock_sn:'',aircraft_sn:'',organization_id:'',organization_name:'',ntp_host:''});
const sensitive=()=>({dock_binding_code:'',aircraft_binding_code:'',app_id:'',app_key:'',app_license:''});
const plainKeys=Object.keys(initial());
const uuid=v=>typeof v==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
const clean=(v,n=256)=>typeof v==='string'&&v.length<=n&&!/[\u0000-\u001f\u007f]/.test(v);
const validDraft=v=>v&&Object.keys(v).sort().join(',')===plainKeys.slice().sort().join(',')&&plainKeys.every(k=>clean(v[k]));
const stateLabels={waiting:'等待接收器',connecting:'正在连接',subscribing:'正在订阅',receiving:'正在接收',backoff:'连接中断，等待重试',blocked:'已阻止',stopped:'已停止'};

/** Cloud API 1 onboarding; credentials live only in the mounted component. */
export default function DjiOnboardingStudio(props){
 return <Panel key={[props.account?.workspace_id,props.account?.user_id,props.account?.role].join(':')} {...props}/>;
}
function Panel({account,Btn,onRegisterGuard,request=json,enableVerified=false}){
 const scope=account?.workspace_id+':'+account?.user_id,key='dji-onboarding:'+scope+':pending',draftKey='dji-onboarding:'+scope+':draft';
 const [form,setForm]=useState(initial),[secrets,setSecrets]=useState(sensitive),[broker,setBroker]=useState(null),[experimental,setExperimental]=useState(false),[rows,setRows]=useState(null),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[editor,setEditor]=useState(false),[attempt,setAttempt]=useState(0),[error,setError]=useState(''),[notice,setNotice]=useState(''),[badStorage,setBadStorage]=useState(false),[acl,setAcl]=useState(false),[confirmed,setConfirmed]=useState(false),[confirmStatus,setConfirmStatus]=useState(null);
 const alive=useRef(false),working=useRef(false),guardRef=useRef(onRegisterGuard),current=useRef({});guardRef.current=onRegisterGuard;current.current={busy,pending,badStorage};
 const req=(path,options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 function pendingRead(){const raw=localStorage.getItem(key);if(!raw)return null;const v=JSON.parse(raw);
  if(!v||v.scope!==scope||!uuid(v.request_id)||!['create','status'].includes(v.kind)||!v.expected||typeof v.expected!=='object')throw Error('原请求无效');
  if(v.kind==='create'&&(!clean(v.expected.dock_sn,64)||!clean(v.expected.aircraft_sn,64)||!clean(v.expected.organization_name,128)||!clean(v.expected.broker_authorization_id,64)))throw Error('原请求范围无效');
  if(v.kind==='status'&&(!clean(v.expected.id,64)||!Number.isSafeInteger(v.expected.version)||typeof v.expected.enabled!=='boolean'))throw Error('原状态请求无效');return v;
 }
 async function load(){const responses=await Promise.all([req('/dji/onboarding/configurations'),...(account.role==='admin'?[req('/mqtt/authorizations')]:[])]);const a=responses[0];if(!Array.isArray(a?.configurations))throw Error('配置清单无法核对');if(account.role==='admin'&&!Array.isArray(responses[1]?.authorizations))throw Error('服务授权无法核对');if(alive.current){setRows(a.configurations);setExperimental(a.experimental_enabled===true);if(account.role==='admin')setBroker(responses[1])}return a}
 useEffect(()=>{alive.current=true;setError('');setNotice('');setSecrets(sensitive());setPending(null);setBadStorage(false);setAcl(false);setConfirmed(false);setConfirmStatus(null);try{const draft=JSON.parse(localStorage.getItem(draftKey)||'null');if(draft){if(!validDraft(draft))throw Error();setForm(draft)}setPending(pendingRead())}catch{setBadStorage(true);setError('本机草稿或原请求无法核对；请检查浏览器存储，原记录保留。')}
  load().catch(()=>{if(alive.current)setError('无法读取上云配置，请检查登录、工作区及后台连接。')});return()=>{alive.current=false};
 },[scope]);
 useEffect(()=>{let stopped=false,release;setEditor(false);if(account.role!=='admin')return;
  if(!navigator.locks?.request){setError('浏览器无法保护多标签编辑，请使用支持安全锁的浏览器。');return}
  navigator.locks.request(key+':editor',{mode:'exclusive',ifAvailable:true},async lock=>{if(stopped||!lock)return;try{const draft=JSON.parse(localStorage.getItem(draftKey)||'null');if(draft&&!validDraft(draft))throw Error();setForm(draft||initial());setPending(pendingRead());setSecrets(sensitive());setEditor(true);await new Promise(resolve=>{release=resolve})}catch{setBadStorage(true);setError('本机草稿或原请求无法核对。')}}).catch(()=>{if(!stopped)setError('无法保护多标签编辑。')});
  return()=>{stopped=true;release?.()};
 },[scope,attempt]);
 useEffect(()=>{const guard=()=>!working.current&&!current.current.busy&&!current.current.pending&&!current.current.badStorage;guardRef.current?.(guard);return()=>guardRef.current?.(null,guard)},[]);
 function edit(patch){const value={...form,...patch};setForm(value);try{if(!validDraft(value))throw Error();localStorage.setItem(draftKey,JSON.stringify(value));setError('')}catch{setBadStorage(true);setError('草稿无法安全保留，请检查输入和浏览器存储。')}}
 function locked(fn){if(!navigator.locks?.request)throw Error('无法保护原请求');return navigator.locks.request(key+':journal',{mode:'exclusive'},fn)}
 async function retain(value){await locked(()=>{if(!alive.current)throw Error('工作区已变化');if(pendingRead())throw Error('已有原请求');localStorage.setItem(key,JSON.stringify(value));if(localStorage.getItem(key)!==JSON.stringify(value))throw Error('无法保存请求');setPending(value)})}
 async function clear(value){await locked(()=>{if(!alive.current)throw Error('工作区已变化');const prior=pendingRead();if(!prior||JSON.stringify(prior)!==JSON.stringify(value))throw Error('原请求已变化');localStorage.removeItem(key);if(localStorage.getItem(key)!==null)throw Error('无法安全清除');setPending(null)})}
 async function finish(value,reply){if(reply?.request_id!==value.request_id||!['succeeded','cancelled'].includes(reply.state))throw Error('回执无法核对');
  if(reply.state==='succeeded'){
   const c=reply.configuration,e=value.expected;if(!c||typeof c.id!=='string'||!Number.isSafeInteger(c.version))throw Error('配置回执不完整');
   if(value.kind==='create'&&(c.dock_sn!==e.dock_sn||c.aircraft_sn!==e.aircraft_sn||c.organization_name!==e.organization_name||c.broker_authorization_id!==e.broker_authorization_id||c.enabled!==false))throw Error('回执与原配置不一致');
   if(value.kind==='status'&&(c.id!==e.id||c.version!==e.version+1||c.enabled!==e.enabled))throw Error('回执与原操作不一致');
  }
  await clear(value);if(!alive.current)return;setSecrets(sensitive());setConfirmStatus(null);setAcl(false);setConfirmed(false);
  if(reply.state==='succeeded'&&value.kind==='create'){setForm(initial());try{localStorage.removeItem(draftKey)}catch{setBadStorage(true);setError('配置已保存，但本机草稿无法清除。')}}
  setNotice(reply.state==='cancelled'?'原请求已安全结束。':value.kind==='create'?'配置已保存并暂停；设备身份、许可证与 MQTT 主题隔离须独立核验。':'上云接收状态已保存。');
  try{await load()}catch{if(alive.current)setError('操作已保存，但无法刷新清单；请重新读取。')}
 }
 async function create(e){e.preventDefault();if(working.current||busy||pending||!editor||badStorage||!experimental)return;
  if(!acl||!confirmed){setError('请先确认设备与 MQTT 主题权限。');return}const selected=broker?.authorizations?.find(x=>x.id===form.broker_authorization_id&&x.enabled);if(!selected){setError('请选择已启用的 MQTT 服务授权。');return}
  const values={...form,...secrets};if(Object.values(values).some(x=>!String(x).trim())){setError('请填写全部设备、组织与上云凭据。');return}
  const rid=crypto.randomUUID(),value={scope,kind:'create',request_id:rid,expected:{dock_sn:form.dock_sn,aircraft_sn:form.aircraft_sn,organization_name:form.organization_name,broker_authorization_id:form.broker_authorization_id}};
  working.current=true;setBusy(true);setError('');setNotice('');try{await retain(value);const {broker_authorization_id,...config}=values;const reply=await req('/dji/onboarding/configurations',{method:'POST',body:JSON.stringify({request_id:rid,broker_authorization_id,config,confirm:true,broker_acl_confirmed:true})});await finish(value,reply)}catch{if(alive.current)setError('保存结果尚未核对。请先核对或安全结束原请求，不要重复提交。')}finally{setSecrets(sensitive());working.current=false;if(alive.current)setBusy(false)}
 }
 async function changeStatus(row){if(working.current||busy||pending||!editor||badStorage||(!row.enabled&&(!experimental||!enableVerified)))return;const rid=crypto.randomUUID(),enabled=!row.enabled,value={scope,kind:'status',request_id:rid,expected:{id:row.id,version:row.version,enabled}};working.current=true;setBusy(true);setError('');setNotice('');try{await retain(value);const reply=await req('/dji/onboarding/configurations/'+encodeURIComponent(row.id)+'/status',{method:'POST',body:JSON.stringify({request_id:rid,version:row.version,enabled,confirm:true})});await finish(value,reply)}catch{if(alive.current)setError('状态结果尚未核对，请先核对或安全结束原请求。')}finally{working.current=false;if(alive.current)setBusy(false)}}
 async function recover(cancel=false){if(!pending||working.current||busy)return;const value=pending;working.current=true;setBusy(true);setError('');try{const path='/dji/onboarding/operations/'+encodeURIComponent(value.request_id)+(cancel?'/cancel':'');await finish(value,await req(path,cancel?{method:'POST'}:{}))}catch{if(alive.current)setError('原请求尚未核对，记录仍保留；请稍后核对或安全结束。')}finally{working.current=false;if(alive.current)setBusy(false)}}
 const disabled=busy||!!pending||!editor||badStorage;
 return <section className="local-model-card" aria-label="大疆设备上云配置"><h2>大疆设备上云配置（Cloud API 1）</h2><p>用于兼容 Cloud API 1 的机场及其飞机。它不支持 Cloud API 2 官方模拟器，也不会验证大疆许可证真伪或设备真实身份；收到回复不代表已上云或可以飞行。</p><p>必须使用专用 MQTT over TLS 服务，并由服务管理员按客户端身份限制该机场上云主题的读写；上云回复包含应用凭据，ACL 配错可能泄露凭据。请求也可能被重放，当前不能防止所有重放攻击。请先在上方登记 MQTT 服务授权。</p>{!experimental&&<p role="status">受控测试默认关闭，当前不能登记或启用；已有运行中的接收仍可暂停。</p>}{experimental&&!enableVerified&&<p role="status">设备真实身份、许可证和 MQTT 主题隔离尚未通过现场验证。当前不开放启用；已有运行中的接收仍可暂停。</p>}<p>设备绑定码、应用 ID、Key 和许可证仅在当前页面内存中使用，提交后由服务端加密保存，不写入浏览器存储。刷新或断线后若原请求未保存，需重新输入这些凭据。</p>
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy&&<p role="status">正在保存或核对，请稍候。</p>}
 {pending&&<div><h3>上次操作结果待核对</h3><p>为避免重复提交，请核对原请求；服务器尚未保存时可安全结束后重新填写。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn> <Btn disabled={busy} onClick={()=>recover(true)}>安全结束原请求</Btn></div>}
 {account.role==='admin'&&!editor&&!badStorage&&<p>此账号的另一个页面正在编辑。关闭其他页面后可接手。 <Btn onClick={()=>setAttempt(x=>x+1)}>接手编辑</Btn></p>}
 {account.role==='admin'&&<details><summary>新增设备上云配置</summary>{!broker?<p>正在读取 MQTT 服务授权…</p>:!broker.enabled||!broker.key_ready?<p>MQTT 授权服务或加密密钥不可用，请先恢复。</p>:<form onSubmit={create}><fieldset disabled={disabled||!experimental}>
 <label>已授权 MQTT 服务 <select value={form.broker_authorization_id} onChange={e=>edit({broker_authorization_id:e.target.value})} required><option value="">请选择</option>{broker.authorizations.filter(x=>x.enabled).map(x=><option key={x.id} value={x.id}>{x.name} · {x.broker}</option>)}</select></label>
 <label>机场序列号 <input value={form.dock_sn} onChange={e=>edit({dock_sn:e.target.value})} maxLength={64} required/></label>
 <label>飞机序列号 <input value={form.aircraft_sn} onChange={e=>edit({aircraft_sn:e.target.value})} maxLength={64} required/></label>
 <label>组织标识 <input value={form.organization_id} onChange={e=>edit({organization_id:e.target.value})} maxLength={100} required/></label>
 <label>组织名称 <input value={form.organization_name} onChange={e=>edit({organization_name:e.target.value})} maxLength={128} required/></label>
 <label>时间服务器 <input value={form.ntp_host} onChange={e=>edit({ntp_host:e.target.value})} maxLength={253} placeholder="例如 ntp.example.com" required/></label>
 <label>机场绑定码 <input type="password" value={secrets.dock_binding_code} onChange={e=>setSecrets({...secrets,dock_binding_code:e.target.value})} autoComplete="off" required/></label>
 <label>飞机绑定码 <input type="password" value={secrets.aircraft_binding_code} onChange={e=>setSecrets({...secrets,aircraft_binding_code:e.target.value})} autoComplete="off" required/></label>
 <label>大疆应用 ID <input value={secrets.app_id} onChange={e=>setSecrets({...secrets,app_id:e.target.value})} autoComplete="off" required/></label>
 <label>大疆应用 Key <input type="password" value={secrets.app_key} onChange={e=>setSecrets({...secrets,app_key:e.target.value})} autoComplete="new-password" required/></label>
 <label>大疆应用许可证 <input type="password" value={secrets.app_license} onChange={e=>setSecrets({...secrets,app_license:e.target.value})} maxLength={32768} autoComplete="off" required/></label>
 <label><input type="checkbox" checked={acl} onChange={e=>setAcl(e.target.checked)}/> 我已核对专用 MQTT 服务的客户端身份与该机场主题 ACL，避免其他设备读取上云凭据。</label>
 <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> 我已核对机场、飞机、组织及 Cloud API 1 许可信息。</label>
 <Btn type="submit" disabled={!acl||!confirmed}>保存并暂停</Btn></fieldset></form>}</details>}
 <h3>已保存的上云配置</h3>{rows===null?<p>正在读取…</p>:!rows.length?<p>尚未保存上云配置。</p>:rows.map(row=><article className="media-form" key={row.id}><h4>机场 {row.dock_sn} · 飞机 {row.aircraft_sn}</h4><p>组织：{row.organization_name}；上云接收：{row.enabled?'已启用':'已暂停'}；接收器：{stateLabels[row.receiver_state]||'状态待核对'}。</p><p>最近处理：{row.last_stage||'尚无'}；报告时间：{row.receiver_reported_at||'未知'}。设备认证与可飞状态仍需独立核验。</p>
 {account.role==='admin'&&<>{confirmStatus?.id===row.id?<div><p>确认{row.enabled?'暂停':'启用'}该机场的 Cloud API 1 上云接收？启用前请再次确认专用 MQTT ACL 与设备范围。</p><Btn disabled={disabled||(!row.enabled&&(!enableVerified||!experimental))} onClick={()=>changeStatus(row)}>确认{row.enabled?'暂停':'启用'}</Btn> <Btn disabled={busy} onClick={()=>setConfirmStatus(null)}>返回</Btn></div>:<Btn disabled={disabled||(!row.enabled&&(!enableVerified||!experimental))} onClick={()=>setConfirmStatus(row)}>{row.enabled?'暂停接收':'启用接收'}</Btn>}</>}
 </article>)}<Btn disabled={busy} onClick={()=>load().catch(()=>setError('无法刷新上云配置，请稍后重试。'))}>刷新上云配置</Btn></section>;
}

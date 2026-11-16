import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
const blank={name:'',grant_id:'',unit_label:''};
export default function OpcuaGatewayStudio({account,Btn,onBusy=()=>{},transport=json,refreshToken=0}){
 const scope=account?.workspace_id+':'+account?.user_id,key='opcua-pending:'+scope,draftKey='opcua-draft:'+scope;
 const [form,setForm]=useState(blank),[grants,setGrants]=useState([]),[connections,setConnections]=useState([]),[selected,setSelected]=useState(''),[binding,setBinding]=useState(null),[readings,setReadings]=useState([]),[entity,setEntity]=useState(''),[field,setField]=useState(''),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState('');
 const current=useRef({scope,selected,generation:0,alive:true});if(current.current.scope!==scope||current.current.selected!==selected)current.current={scope,selected,generation:current.current.generation+1,alive:true};
 const valid=(id,epoch)=>current.current.alive&&current.current.scope===scope&&(id===undefined||current.current.selected===id)&&(epoch===undefined||current.current.generation===epoch);
 const request=(path,opts={})=>transport(path,{...opts,headers:{...(opts.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function refresh(){const [g,c]=await Promise.all([request('/opcua/grants'),request('/opcua/connections')]);if(valid()){setGrants(g.grants);setConnections(c.connections)}}
 async function load(id,epoch=current.current.generation){if(!id)return;const [b,r]=await Promise.all([request('/opcua/connections/'+id+'/binding'),request('/opcua/connections/'+id+'/readings')]);if(valid(id,epoch)){setBinding(b);setReadings(r.readings)}}
 useEffect(()=>{let active=true;current.current.alive=true;try{const d=localStorage.getItem(draftKey);if(d)setForm({...blank,...JSON.parse(d)});const raw=localStorage.getItem(key);if(raw){const saved=JSON.parse(raw);if(saved.scope!==scope||!saved.body?.request_id)throw Error();setPending(saved)}}catch{setError('本机原请求或输入无法读取，请检查浏览器存储。')}if(account?.role==='admin')refresh().catch(e=>{if(active)setError(e.message)});return()=>{active=false;current.current.alive=false}},[scope]);
 useEffect(()=>{if(refreshToken&&account?.role==='admin')refresh().catch(e=>{if(current.current.alive)setError(e.message)});},[refreshToken]);
 useEffect(()=>{const epoch=current.current.generation;setBinding(null);setReadings([]);setEntity('');setField('');if(selected)load(selected,epoch).catch(e=>{if(valid(selected,epoch))setError(e.message)})},[scope,selected]);
 const change=e=>{const next={...form,[e.target.name]:e.target.value};try{localStorage.setItem(draftKey,JSON.stringify(next));setForm(next)}catch{setError('本机无法保存输入，请检查浏览器存储。')}};
 async function execute(attempt){if(busy)return;setBusy(true);onBusy(true);setError('');setNote('');try{
  if(!navigator.locks?.request)throw Error('浏览器不能保护原请求。');
  await navigator.locks.request(key,{mode:'exclusive'},async()=>{const old=localStorage.getItem(key);if(old&&JSON.parse(old).body.request_id!==attempt.body.request_id)throw Error('已有待核对请求，请先处理。');localStorage.setItem(key,JSON.stringify(attempt));setPending(attempt);
   const result=await request(attempt.path,{method:'POST',body:JSON.stringify(attempt.body)});if(!valid())throw Error('账号或工作区已变化。');
   if(result?.request_id!==attempt.body.request_id||result?.kind!==attempt.type||!['succeeded','failed'].includes(result?.state))throw Error('操作回执无法核对。');
   if(result.state==='failed')throw Error(result.result?.error||'读取失败');
   if(attempt.type==='register'){setSelected(result.result.connection_id);setForm(blank);localStorage.removeItem(draftKey);setNote('连接已保存，可确认对象并读取。')}
   if(attempt.type==='bind'||attempt.type==='read'){const epoch=current.current.generation;await load(attempt.connectionId,epoch);setNote(attempt.type==='bind'?'对象关联已保存。':'读取完成，结果已保存。')}
   await refresh();if(localStorage.getItem(key)!==JSON.stringify(attempt))throw Error('本机原请求已变化。');localStorage.removeItem(key);setPending(null);
  });
 }catch(e){if(valid())setError((e.message||'操作未完成')+'；原请求保留，可重试或核对结束。')}finally{if(valid()){setBusy(false);onBusy(false)}}}
 async function resolve(){if(!pending||busy)return;setBusy(true);onBusy(true);setError('');try{
  if(!navigator.locks?.request)throw Error('浏览器不能保护原请求。');
  await navigator.locks.request(key,{mode:'exclusive'},async()=>{const raw=localStorage.getItem(key);if(!raw||JSON.stringify(JSON.parse(raw))!==JSON.stringify(pending))throw Error('本机原请求已变化。');
   const body={kind:pending.type,connection_id:pending.type==='register'?null:pending.connectionId,submission:pending.body};
   const result=await request('/opcua/operations/'+encodeURIComponent(pending.body.request_id)+'/resolve',{method:'POST',body:JSON.stringify(body)});
   if(!valid()||result?.request_id!==pending.body.request_id||result?.kind!==pending.type||!['succeeded','cancelled'].includes(result?.state))throw Error('原请求回执无法核对。');
   if(localStorage.getItem(key)!==raw)throw Error('本机原请求已变化。');localStorage.removeItem(key);setPending(null);setNote(result.state==='succeeded'?'已核对：原操作已保存。':'已核对：原请求已结束，不会再执行。');if(account?.role==='admin')refresh().catch(e=>setError('原请求已核对，列表刷新失败：'+e.message));
  });
 }catch(e){if(valid())setError((e.message||'无法核对')+'；原请求保留。')}finally{if(valid()){setBusy(false);onBusy(false)}}}
 function register(e){e.preventDefault();if(pending)return;execute({scope,type:'register',path:'/opcua/connections',body:{...form,request_id:crypto.randomUUID()}})}
 function run(type){if(pending||!selected)return;const row=connections.find(x=>x.id===selected);if(!row)return;const body={request_id:crypto.randomUUID(),version:row.version,...(type==='bind'?{entity_id:entity,field}:{})};execute({scope,type,connectionId:selected,path:'/opcua/connections/'+selected+(type==='bind'?'/binding':'/readings'),body})}
 const grant=grants.find(x=>x.id===form.grant_id);
 return <section className="card"><h2>OPC UA 传感器</h2><p>通过已授权地址和证书安全读取指定节点，不发送控制指令。设备时间和质量由来源报告，需结合设备状态核查。</p>
 {error&&<p role="alert">{error}</p>}{note&&<p role="status">{note}</p>}
 {pending&&<aside className="info">上次请求尚未核对。{account?.role==='admin'&&<Btn disabled={busy} onClick={()=>execute(pending)}>重试原请求</Btn>}<Btn disabled={busy} onClick={resolve}>核对并结束原请求</Btn></aside>}
 {account?.role!=='admin'?<p>当前账号不能管理设备；可核对并结束本人原请求。</p>:<>
 {!grants.length?<p>当前工作区没有可用的 OPC UA 授权。请联系管理员配置设备地址、节点和证书。</p>:<form onSubmit={register}><label>连接名称<input name="name" value={form.name} onChange={change} required maxLength={100}/></label><label>已授权设备与节点<select name="grant_id" value={form.grant_id} onChange={change} required><option value="">请选择</option>{grants.map(g=><option key={g.id} value={g.id}>{g.host}:{g.port} · {g.node_id}</option>)}</select></label>{grant&&<p>服务端证书 SHA-256：{grant.server_sha256}<br/>客户端证书 SHA-256：{grant.client_sha256}<br/>安全方式：Basic256Sha256 加密连接</p>}<label>单位（来自设备资料，未自动核实）<input name="unit_label" value={form.unit_label} onChange={change} maxLength={40}/></label><Btn type="submit" primary disabled={busy||!!pending}>保存连接</Btn></form>}
 <h3>已保存连接</h3><label>选择连接<select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">请选择</option>{connections.map(c=><option key={c.id} value={c.id}>{c.name} · {c.source.node_id}</option>)}</select></label>
 {selected&&binding&&(binding.binding?<p>已关联：{binding.entities.find(e=>e.id===binding.binding.entity_id)?.label||'已保存对象'} · {binding.binding.field}。如需更改，请新建连接。</p>:<div><h3>关联业务对象（可选）</h3><p>关联后新读数会保存为待核查观测，不自动更新对象状态。</p><label>业务对象<select value={entity} onChange={e=>{setEntity(e.target.value);setField('')}}><option value="">请选择</option>{binding.entities.map(e=><option key={e.id} value={e.id}>{e.label} · {e.type}</option>)}</select></label><label>数值字段<select value={field} onChange={e=>setField(e.target.value)}><option value="">请选择</option>{(binding.entities.find(e=>e.id===entity)?.fields||[]).map(f=><option key={f.name} value={f.name}>{f.label}{f.unit?' · '+f.unit:''}</option>)}</select></label><Btn disabled={!entity||!field||busy||!!pending} onClick={()=>run('bind')}>确认关联</Btn>{!binding.entities.length&&<p>暂无单位匹配的业务对象数值字段。</p>}</div>)}
 <Btn disabled={!selected||busy||!!pending} onClick={()=>run('read')}>读取一次</Btn>
 {selected&&<div><h3>最近采集记录</h3>{!readings.length?<p>暂无记录。</p>:readings.map(r=><div key={r.id}><p>{r.value??'无数值'} {r.unit} · 质量：{({good:'良好',uncertain:'不确定',bad:'不良'})[r.quality]}（{r.status_name}） · {r.observation_id?'已保存待核查观测':r.association_error?'关联未完成：'+r.association_error:'未关联业务对象'}</p><p>设备报告时间：{r.source_timestamp||'未知'} · 设备服务器时间：{r.server_timestamp||'未知'} · 系统接收时间：{r.received_at}</p></div>)}</div>}
 </>}
 </section>;
}

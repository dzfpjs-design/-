import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const empty={name:'',grant_id:'',function:3,address:0,value_type:'uint16',word_order:'big',scale:1,offset:0,unit_label:''};
/** A saved connection and explicit, read-only acquisition; no device commands. */
export default function ModbusGatewayStudio({account,Btn,Field,onBusy=()=>{},transport=json,refreshToken=0}){
 const scope=account?.workspace_id+':'+account?.user_id;
 const key='modbus-pending:'+scope,draftKey='modbus-draft:'+scope;
 const [form,setForm]=useState(empty),[grants,setGrants]=useState([]),[connections,setConnections]=useState([]),[selected,setSelected]=useState(''),[readings,setReadings]=useState([]),[bindingInfo,setBindingInfo]=useState(null),[entityId,setEntityId]=useState(''),[field,setField]=useState(''),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState('');
 const current=useRef({scope,selected,alive:true,generation:0});
 if(current.current.scope!==scope||current.current.selected!==selected)current.current={scope,selected,alive:true,generation:current.current.generation+1};
 const isCurrent=(expectedScope,id,generation)=>current.current.alive&&current.current.scope===expectedScope&&(id===undefined||current.current.selected===id)&&(generation===undefined||current.current.generation===generation);
 const request=(path,options={})=>transport(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function refresh(){const [g,c]=await Promise.all([request('/modbus/grants'),request('/modbus/connections')]);if(isCurrent(scope)){setGrants(g.grants);setConnections(c.connections);}}
 async function history(id,generation=current.current.generation){if(!id){if(isCurrent(scope,id,generation))setReadings([]);return;}const r=await request('/modbus/connections/'+id+'/readings');if(isCurrent(scope,id,generation))setReadings(r.readings);}
 useEffect(()=>{let alive=true;current.current.alive=true;try{const draft=localStorage.getItem(draftKey);if(draft)setForm({...empty,...JSON.parse(draft)});const raw=localStorage.getItem(key);if(raw){const saved=JSON.parse(raw);if(saved.scope!==scope||!saved.path||!saved.body?.request_id)throw Error();setPending(saved);}}catch{setError('无法读取本机待核对请求，请检查浏览器存储。');}if(account?.role==='admin')refresh().catch(e=>{if(alive)setError(e.message)});return()=>{alive=false;current.current.alive=false};},[scope]);
 useEffect(()=>{if(refreshToken&&account?.role==='admin')refresh().catch(e=>{if(current.current.alive)setError(e.message)});},[refreshToken]);
 useEffect(()=>{const generation=current.current.generation;setBindingInfo(null);setReadings([]);setEntityId('');setField('');history(selected,generation).catch(e=>{if(isCurrent(scope,selected,generation))setError(e.message)});if(selected){const id=selected;request('/modbus/connections/'+id+'/binding').then(value=>{if(isCurrent(scope,id,generation))setBindingInfo(value)}).catch(e=>{if(isCurrent(scope,id,generation))setError(e.message)});}},[scope,selected]);
 async function execute(attempt,isRetry=false){
  if(busy)return;
  setError('');setNote('');setBusy(true);onBusy(true);
  try{
   if(!navigator.locks?.request)throw Error('浏览器不能保护操作，请使用支持安全锁的浏览器。');
   await navigator.locks.request(key,{mode:'exclusive'},async()=>{
    const old=localStorage.getItem(key);
    if(old && JSON.parse(old).body.request_id!==attempt.body.request_id)throw Error('已有待核对请求，请先重试原请求。');
    localStorage.setItem(key,JSON.stringify(attempt));setPending(attempt);
    const r=await request(attempt.path,{method:'POST',body:JSON.stringify(attempt.body)});
    if(!isCurrent(scope))throw Error('账号或工作区已变化，原请求保留。');
    if(attempt.type==='register'){
     if(r.connection?.request_id!==attempt.body.request_id)throw Error('保存回执无法核对，请重试原请求。');
     setSelected(r.connection.id);setForm(empty);localStorage.removeItem(draftKey);setNote('连接已保存，可进行只读采集。');
    }else if(attempt.type==='bind'){
     if(r.binding?.request_id!==attempt.body.request_id)throw Error('关联回执无法核对，请重试原请求。');
     const generation=current.current.generation;const binding=await request('/modbus/connections/'+attempt.connectionId+'/binding');if(isCurrent(scope,attempt.connectionId,generation))setBindingInfo(binding);setNote('对象关联已保存，后续采集会保存为时间未知的待核查观测。');
    }else{
     if(r.reading?.request_id!==attempt.body.request_id)throw Error('采集回执无法核对，请重试原请求。');
     setNote(r.reading.state==='succeeded'?'采集完成，结果已保存。':'读取未完成：'+r.reading.error+'。可再次读取。');
     await history(attempt.connectionId);
    }
    await refresh();localStorage.removeItem(key);setPending(null);
   });
  }catch(e){setError((e.message||'操作未完成')+(localStorage.getItem(key)?'；原请求已保留，请核对或重试。':''));}
  finally{setBusy(false);onBusy(false);}
 }
 async function resolvePending(){
  if(!pending||busy)return;
  setError('');setNote('');setBusy(true);onBusy(true);
  try{
   if(!navigator.locks?.request)throw Error('浏览器不能保护原请求，请使用支持安全锁的浏览器。');
   await navigator.locks.request(key,{mode:'exclusive'},async()=>{
    const raw=localStorage.getItem(key);if(!raw)throw Error('本机原请求已变化，请刷新核对。');
    const original=JSON.parse(raw);
    if(original.scope!==scope||original.type!==pending.type||original.body?.request_id!==pending.body?.request_id||JSON.stringify(original)!==JSON.stringify(pending))throw Error('本机原请求已变化，请刷新核对。');
    const body={kind:original.type,connection_id:original.type==='register'?null:original.connectionId,submission:original.body};
    const receipt=await request('/modbus/operations/'+encodeURIComponent(original.body.request_id)+'/resolve',{method:'POST',body:JSON.stringify(body)});
    if(!isCurrent(scope))throw Error('账号或工作区已变化，原请求保留。');
    if(receipt?.request_id!==original.body.request_id||receipt?.kind!==original.type||!['succeeded','cancelled'].includes(receipt?.state))throw Error('服务端回执无法核对，原请求保留。');
    const latest=localStorage.getItem(key);if(latest!==raw)throw Error('本机原请求已变化，原请求保留。');
    localStorage.removeItem(key);setPending(null);
    setNote(receipt.state==='succeeded'?'已核对：原操作已保存。':'已核对：原请求已结束，不会再执行。');
    if(account?.role==='admin')refresh().catch(e=>setError('原请求已核对，列表刷新失败：'+e.message));
   });
  }catch(e){setError((e.message||'暂时无法核对')+'；原请求保留，请稍后重试。');}
  finally{setBusy(false);onBusy(false);}
 }
 function submit(e){e.preventDefault();if(pending)return;const body={...form,request_id:crypto.randomUUID(),function:Number(form.function),address:Number(form.address),scale:Number(form.scale),offset:Number(form.offset)};execute({scope,type:'register',path:'/modbus/connections',body});}
 function bind(){if(pending)return;const row=connections.find(x=>x.id===selected);if(!row||!entityId||!field)return;execute({scope,type:'bind',connectionId:row.id,path:'/modbus/connections/'+row.id+'/binding',body:{request_id:crypto.randomUUID(),version:row.version,entity_id:entityId,field}});}
 function acquire(){if(pending)return;const row=connections.find(x=>x.id===selected);if(!row)return;execute({scope,type:'read',connectionId:row.id,path:'/modbus/connections/'+row.id+'/readings',body:{request_id:crypto.randomUUID(),version:row.version}});}
 const change=e=>{const next={...form,[e.target.name]:e.target.value};try{localStorage.setItem(draftKey,JSON.stringify(next));setForm(next);}catch{setError('本机无法保存输入，请检查浏览器存储。');}};
 const label=(name,title,type='text',extra={})=><label>{title}<input name={name} type={type} value={form[name]} onChange={change} {...extra}/></label>;
 return <section className="card"><h2>Modbus TCP 传感器</h2><p>从管理员授权的设备寄存器手动读取数据；此处不会控制设备。确认业务对象和字段后，采集结果可保存为观测数据，设备实际测量时间仍未知，不自动判定对象状态或异常。</p>
 {error&&<p role="alert">{error}</p>}{note&&<p role="status">{note}</p>}
 {pending&&<aside className="info">上次请求尚未核对。服务端会核对原结果，并在没有成功记录时安全结束原请求。{account?.role==='admin'&&<Btn disabled={busy} onClick={()=>execute(pending,true)}>重试原请求</Btn>}<Btn disabled={busy} onClick={resolvePending}>核对并结束原请求</Btn></aside>}
 {account?.role!=='admin'?<p>当前账号无法管理设备；如有待核对请求，仍可核对并结束本人原请求。</p>:!grants.length?<p>当前工作区没有设备读取授权，请联系系统管理员配置允许访问的设备和寄存器范围。</p>:<form onSubmit={submit}>
 {label('name','连接名称')}
 <label>已授权设备<select name="grant_id" value={form.grant_id} onChange={change} required><option value="">请选择</option>{grants.map(g=><option key={g.id} value={g.id}>{g.host}:{g.port} · 站号 {g.unit} · 地址 {g.start}–{g.end}</option>)}</select></label>
 <label>读取类型<select name="function" value={form.function} onChange={change}><option value="3">保持寄存器（功能码 3）</option><option value="4">输入寄存器（功能码 4）</option></select></label>
 {label('address','起始寄存器地址（从 0 开始）','number',{min:0,max:65535,required:true})}
 <label>数据类型<select name="value_type" value={form.value_type} onChange={change}><option value="uint16">无符号 16 位整数</option><option value="int16">有符号 16 位整数</option><option value="uint32">无符号 32 位整数</option><option value="int32">有符号 32 位整数</option><option value="float32">32 位浮点数</option></select></label>
 {!form.value_type.endsWith('16')&&<label>寄存器顺序<select name="word_order" value={form.word_order} onChange={change}><option value="big">高位寄存器在前</option><option value="little">低位寄存器在前</option></select></label>}
 {label('scale','倍率','number',{step:'any',required:true})}{label('offset','偏移量','number',{step:'any',required:true})}{label('unit_label','单位（可选）')}
 <Btn primary disabled={busy||!!pending} type="submit">保存连接</Btn></form>}
 {account?.role==='admin'&&<><h3>已保存连接</h3><label>选择连接<select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">请选择</option>{connections.map(c=><option key={c.id} value={c.id}>{c.name} · 地址 {c.address}</option>)}</select></label>
 {selected&&bindingInfo&&(bindingInfo.binding?<p>已关联业务对象和字段：{bindingInfo.entities.find(e=>e.id===bindingInfo.binding.entity_id)?.label||'已保存对象'} · {bindingInfo.binding.field}。更改对象请新建连接，原数据保留。</p>:<div><h3>关联业务对象（可选）</h3><p>选择后，后续读取会写入观测数据；若暂不关联，读取结果仅保存在采集记录。</p><label>业务对象<select value={entityId} onChange={e=>{setEntityId(e.target.value);setField('')}}><option value="">请选择</option>{bindingInfo.entities.map(e=><option key={e.id} value={e.id}>{e.label} · {e.type}</option>)}</select></label><label>数值字段<select value={field} onChange={e=>setField(e.target.value)}><option value="">请选择</option>{(bindingInfo.entities.find(e=>e.id===entityId)?.fields||[]).map(f=><option key={f.name} value={f.name}>{f.label}{f.unit?' · '+f.unit:''}</option>)}</select></label><Btn disabled={!entityId||!field||busy||!!pending} onClick={bind}>确认关联</Btn>{!bindingInfo.entities.length&&<p>暂无单位匹配的业务对象数值字段，请先完成业务对象配置。</p>}</div>)}
 <Btn disabled={!selected||busy||!!pending} onClick={acquire}>读取一次</Btn>
 {selected&&<div><h3>最近采集记录</h3>{!readings.length?<p>暂无记录。</p>:readings.map(r=><div key={r.id}>{r.state==='succeeded'?<p>{r.value} {r.unit_label} · {r.observation_id?'已保存待核查观测':r.association_error?'关联未完成：'+r.association_error:'未关联业务对象'} · 服务器读取时间 {new Date(r.read_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · 原始寄存器 {r.register_values.join(', ')}</p>:<p>读取失败：{r.error}</p>}</div>)}<p>设备未提供观测时间，读取时间不能作为实际测量时间。</p></div>}</>}
 </section>;
}

import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
const name=c=>c.protocol==='modbus'?'Modbus TCP':'OPC UA';
const displayTime=value=>{if(!value)return '未知';const d=new Date(value);return Number.isNaN(d.getTime())?'未知':d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});};
const blank={key:'',interval:'300',maxAge:'3600',mode:'none',threshold:'',baseVersion:0};

/** Explicit, persisted authorization for periodic read-only acquisition. */
export default function SensorAutomationStudio({account,Btn,onBusy=()=>{},request=json}){
 const scope=account?.workspace_id+':'+account?.user_id,prefix='sensor-auto:'+scope,key=prefix+':pending',draftKey=prefix+':draft';
 const [items,setItems]=useState([]),[page,setPage]=useState(1),[more,setMore]=useState(false),[form,setForm]=useState(blank),[pending,setPending]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[recent,setRecent]=useState(null);
 const alive=useRef(true),running=useRef(false),scopeRef=useRef(scope),roleRef=useRef(account?.role),selectionRef=useRef(form.key);scopeRef.current=scope;roleRef.current=account?.role;selectionRef.current=form.key;
 const active=()=>alive.current&&scopeRef.current===scope&&roleRef.current===account?.role;
 const req=(path,opts={})=>request(path,{...opts,headers:{...(opts.headers||{}),'X-Workspace-ID':account.workspace_id}});
 async function load(next=page){const data=await req('/sensor-automation/options?page='+next);if(!Array.isArray(data.connections))throw Error('设备清单无法核对');if(active()){setItems(data.connections);setPage(next);setMore(!!data.has_more)}return data.connections}
 async function turn(next){setLoading(true);try{await load(next)}catch(e){if(active())setError('读取设备列表失败：'+e.message)}finally{if(active())setLoading(false)}}
 useEffect(()=>{alive.current=true;setLoading(true);try{const a=localStorage.getItem(key);if(a){const value=JSON.parse(a);if(value.scope!==scope||!value.body?.request_id)throw Error();setPending(value)}const d=localStorage.getItem(draftKey);if(d)setForm({...blank,...JSON.parse(d)})}catch{setError('本机未完成的请求或输入无法读取，请保留记录并联系管理员。')}
  if(account?.role==='admin')load().catch(e=>{if(active())setError(e.message)}).finally(()=>{if(active())setLoading(false)});else setLoading(false);return()=>{alive.current=false};},[scope,account?.role]);
 const selected=items.find(c=>c.protocol+':'+c.connection_id===form.key);
 const changed=!!selected&&(selected.policy?.generation||0)!==form.baseVersion;
 async function loadData(){if(!selected)return;const requested=form.key;try{const value=await req('/sensor-automation/data?connection_id='+encodeURIComponent(selected.connection_id));if(!Array.isArray(value.records))throw Error('记录无法核对');if(active()&&selectionRef.current===requested)setRecent({key:requested,...value})}catch(e){if(active()&&selectionRef.current===requested)setError('归集记录读取失败：'+e.message)}}
 function edit(update){const value={...form,...update};try{localStorage.setItem(draftKey,JSON.stringify(value));if(localStorage.getItem(draftKey)!==JSON.stringify(value))throw Error();setForm(value);setError('')}catch{setError('输入尚未保存到本机，请检查浏览器存储。')}}
 function choose(value){const item=items.find(c=>c.protocol+':'+c.connection_id===value),p=item?.policy;edit({key:value,interval:String(p?.interval_seconds??300),maxAge:String(p?.max_age_seconds??3600),mode:p?.threshold?.operator??'none',threshold:p?.threshold?String(p.threshold.value):'',baseVersion:p?.generation||0});setNotice('')}
 async function submit(enabled,existing){if(running.current||busy)return;running.current=true;setBusy(true);onBusy(true);setError('');setNotice('');try{
  if(!navigator.locks?.request)throw Error('浏览器无法保护原请求，请使用支持安全锁的浏览器。');
  await navigator.locks.request(key,{mode:'exclusive'},async()=>{let a=existing||pending;
   if(!a){if(!selected||(enabled&&!selected.available)||(!enabled&&!selected.policy))throw Error('请先选择已关联业务对象且授权有效的连接。');
    const interval=Number(form.interval),maxAge=Number(form.maxAge),threshold=form.mode==='none'?null:{operator:form.mode,value:Number(form.threshold)};
    if(!Number.isInteger(interval)||interval<60||interval>86400||!Number.isInteger(maxAge)||maxAge<60||maxAge>86400||threshold&&(!form.threshold.trim()||!Number.isFinite(threshold.value)||Math.abs(threshold.value)>1e12))throw Error('请核对间隔、时效与阈值。');
    const body={request_id:crypto.randomUUID(),protocol:selected.protocol,connection_id:selected.connection_id,connection_version:selected.connection_version,expected_version:form.baseVersion,enabled,interval_seconds:interval,max_age_seconds:maxAge,threshold};a={scope,body};
    if(localStorage.getItem(key))throw Error('已有未完成请求，请先核对。');localStorage.setItem(key,JSON.stringify(a));if(localStorage.getItem(key)!==JSON.stringify(a))throw Error('原请求无法保存，未发送。');setPending(a);
   }else if(a.scope!==scope)throw Error('账号或工作区已变化。');
   const result=await req('/sensor-automation/policy',{method:'POST',body:JSON.stringify(a.body)});if(!active())return;
   const r=result?.policy;if(!r||r.protocol!==a.body.protocol||r.connection_id!==a.body.connection_id||r.enabled!==a.body.enabled||r.interval_seconds!==a.body.interval_seconds||JSON.stringify(r.threshold)!==JSON.stringify(a.body.threshold))throw Error('保存结果与原请求不一致，请继续核对。');
   const saved=localStorage.getItem(key);if(!saved||JSON.parse(saved).body.request_id!==a.body.request_id)throw Error('本机原请求已变化，请继续核对。');
   localStorage.removeItem(key);setPending(null);edit({baseVersion:r.generation});await load();setNotice(a.body.enabled?'自动采集已启用，采集结果与线索仍需核查。':'自动采集已暂停，历史数据保留。');
  });
 }catch(e){if(active())setError((localStorage.getItem(key)?'原请求保留，可核对或重试：':'操作未完成：')+(e.message||'请稍后再试'))}finally{running.current=false;if(active()){setBusy(false);onBusy(false)}}}
 async function recover(){if(!pending)return;try{const saved=await req('/sensor-automation/policy-requests/'+encodeURIComponent(pending.body.request_id));if(!saved?.policy)throw Error('原请求记录无法核对');await submit(false,pending)}catch(e){if(e.status===404)await submit(false,pending);else setError('原请求仍待核对：'+e.message)}}
 async function cancel(){if(!pending||running.current)return;running.current=true;setBusy(true);onBusy(true);setError('');try{
  if(!navigator.locks?.request)throw Error('浏览器无法保护原请求。');
  await navigator.locks.request(key,{mode:'exclusive'},async()=>{const raw=localStorage.getItem(key);if(!raw||JSON.stringify(JSON.parse(raw))!==JSON.stringify(pending))throw Error('本机原请求已变化。');
   const res=await req('/sensor-automation/policy-requests/'+encodeURIComponent(pending.body.request_id)+'/cancel',{method:'POST',body:JSON.stringify(pending.body)});
   if(!active()||res?.request_id!==pending.body.request_id||!['cancelled','succeeded'].includes(res.state))throw Error('原请求回执无法核对。');
   if(res.state==='succeeded'&&(!res.policy||res.policy.connection_id!==pending.body.connection_id||res.policy.protocol!==pending.body.protocol||res.policy.enabled!==pending.body.enabled))throw Error('已保存结果与原请求不一致。');
   localStorage.removeItem(key);setPending(null);if(res.state==='succeeded')edit({baseVersion:res.policy.generation});if(account?.role==='admin')await load();setNotice(res.state==='succeeded'?'原设置已保存，请核对当前状态。':'原请求已安全结束，历史数据保留。');
  });
 }catch(e){if(active())setError('原请求仍待核对：'+e.message)}finally{running.current=false;if(active()){setBusy(false);onBusy(false)}}}
 if(account?.role!=='admin'&&!pending&&!error)return null;
 return <section className="local-model-card" aria-label="传感器自动采集"><h2>传感器自动采集</h2><p>选择已关联业务对象的传感器，设置读取间隔；可选阈值用于产生待核实线索，不自动认定现场异常或控制设备。授权人失去管理员权限时采集会暂停。</p>
 {loading&&<p role="status">正在读取连接…</p>}{error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {pending&&<aside className="info"><p>上次设置结果尚待核对，原请求已保留。</p>{account?.role==='admin'&&<Btn disabled={busy} onClick={recover}>核对或恢复原请求</Btn>}<Btn disabled={busy} onClick={cancel}>核对并安全结束原请求</Btn></aside>}
 {account?.role!=='admin'?<p>当前账号不能修改采集配置，但可以核对并结束本人原请求。</p>:<>
 <Btn disabled={busy||!!pending} onClick={()=>load().catch(e=>setError('状态刷新失败：'+e.message))}>刷新采集状态</Btn>
 <label>选择传感器<select aria-label="自动采集传感器" value={form.key} disabled={busy||!!pending||loading} onChange={e=>choose(e.target.value)}><option value="">请选择</option>{items.map(c=><option key={c.protocol+':'+c.connection_id} value={c.protocol+':'+c.connection_id}>{c.name} · {name(c)}{c.available?'':' · 尚不可用'}</option>)}</select></label>
 <div aria-label="设备列表分页"><Btn disabled={loading||busy||!!pending||page===1} onClick={()=>turn(page-1)}>上一页</Btn> 第 {page} 页 <Btn disabled={loading||busy||!!pending||!more} onClick={()=>turn(page+1)}>下一页</Btn></div>
 {!items.length&&!loading&&<p>还没有可用连接，请先登记设备并确认业务对象与字段。</p>}
 {selected&&<><p>关联字段：{selected.field||'尚未关联'} {selected.unit||''}；{selected.available?'授权可用':selected.reason||'授权不可用'}</p>{changed&&<p role="alert">采集设置已变化，请先载入最新设置并核对。 <Btn disabled={busy||!!pending} onClick={()=>choose(form.key)}>载入最新设置</Btn></p>}{selected.policy&&<p>当前：{selected.policy.enabled?'已启用':'已暂停'} · {selected.policy.state==='error'?'上次读取失败':selected.policy.state==='paused'?'已暂停':'按计划检查'}{selected.policy.last_read_at?' · 最近读取 '+new Date(selected.policy.last_read_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):''}</p>}{selected.policy?.last_error&&<p role="alert">{selected.policy.last_error}</p>}
 <label>采集间隔（秒）<input aria-label="采集间隔" type="number" min="60" max="86400" value={form.interval} disabled={busy||!!pending} onChange={e=>edit({interval:e.target.value})}/></label>
 {selected.protocol==='opcua'&&<label>设备时间超过多久视为过期（秒）<input aria-label="采集时效" type="number" min="60" max="86400" value={form.maxAge} disabled={busy||!!pending} onChange={e=>edit({maxAge:e.target.value})}/></label>}<p>设备未提供时间时保留“时间未知”，不替设备补造时间。</p>
 <label>线索条件<select aria-label="线索条件" value={form.mode} disabled={busy||!!pending} onChange={e=>edit({mode:e.target.value})}><option value="none">不设置阈值</option><option value="above">高于</option><option value="below">低于</option></select></label>{form.mode!=='none'&&<label>阈值（{selected.unit||'无单位'}）<input aria-label="线索阈值" type="number" step="any" value={form.threshold} disabled={busy||!!pending} onChange={e=>edit({threshold:e.target.value})}/></label>}
 <Btn primary disabled={busy||!!pending||!selected.available||changed} onClick={()=>submit(true)}>保存并启用</Btn>{selected.policy?.enabled&&<Btn disabled={busy||!!pending||changed} onClick={()=>submit(false)}>暂停自动采集</Btn>}
 <Btn disabled={busy} onClick={loadData}>查看最近归集数据</Btn>{recent?.key===form.key&&<div><h3>最近归集数据</h3>{recent.unverified_count>0&&<p role="alert">有 {recent.unverified_count} 条记录依据无法核对，原记录保留。</p>}{recent.has_more&&<p>仅显示最近 50 条记录。</p>}{recent.records.length===0?<p>尚无已归集数据。</p>:recent.records.map(r=><p key={r.reading_id}>{r.object_name} · {r.field_name}：{r.value??'无数值'} {r.unit} · {r.quality==='good'?'设备报告质量良好':'质量待核查'} · 设备时间 {displayTime(r.source_timestamp)} · 接收时间 {displayTime(r.received_at)}</p>)}</div>}</>}
 </>}
 </section>;
}

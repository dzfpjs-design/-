import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {perceptionObjectState,assertPerceptionObjects,assertPerceptionRequestRelease,retainPerceptionPending} from './perception-entity-contract.mjs';
import {perceptionObjectOptions,perceptionSelectionNote} from './perception-object-fields';
import {manualCanonical,registrationDefinition,registrationAttributes,checkedManualIdentity,checkedRegistrationResolution,displayManualValue} from './manual-registration-contract.mjs';
import ReportedObservationReview from './ReportedObservationReview';
import DataDeliveryStudio from './DataDeliveryStudio.jsx';

const clock=()=>new Date(Date.now()+8*3600000).toISOString().slice(0,16);
const copy=value=>JSON.parse(JSON.stringify(value));
const stage={received:'等待本地处理',processed:'本地处理已完成',failed:'处理失败'};
function initialCache(storage,key,scope){
 const raw=storage.getItem(key),saved=raw===null?{}:JSON.parse(raw);
 if(!saved||typeof saved!=='object'||Array.isArray(saved)||saved.broken||saved.scope&&manualCanonical(saved.scope)!==manualCanonical(scope))throw Error('原登记草稿或账号范围无法核对，请保留浏览器记录。');
 const cache={object:'',source:'',rule:'',values:{},when:clock(),pending:null,saved:null,attempts:[],...saved};
 if(!cache.values||typeof cache.values!=='object'||Array.isArray(cache.values)||!Array.isArray(cache.attempts)||cache.pending&&(!cache.pending.source_record_id||!cache.pending.source_id||!cache.pending.attributes))throw Error('原登记请求无法完整恢复，请保留原记录。');
 return {raw,cache};
}
function registrationControl({field,value,disabled,onChange}){
 const props={'aria-label':'登记字段 '+field.label,disabled};
 const control=field.enum?<select {...props} value={field.enum.findIndex(item=>Object.is(item,value))<0?'':String(field.enum.findIndex(item=>Object.is(item,value)))} onChange={e=>onChange(e.target.value===''?'':field.enum[Number(e.target.value)])}><option value="">请选择</option>{field.enum.map((item,index)=><option key={index} value={index}>{item===true?'是':item===false?'否':String(item)}</option>)}</select>:field.type==='boolean'?<select {...props} value={value===true?'true':value===false?'false':''} onChange={e=>onChange(e.target.value===''?'':e.target.value==='true')}><option value="">未填写</option><option value="true">是</option><option value="false">否</option></select>:<input {...props} type={field.type==='string'?(field.format==='date'?'date':'text'):'number'} step={field.type==='integer'?1:'any'} value={value??''} min={field.minimum} max={field.maximum} onChange={e=>onChange(e.target.value)}/>;
 return <label key={field.key}>{field.label}{field.unit?'（'+field.unit+'）':''} · {field.required?'必填':'选填'}{control}{field.pattern&&<small>文字格式由服务器核对；原文不会自动修改。</small>}{field.minLength!==undefined&&<small>至少 {field.minLength} 个字符。</small>}{field.maxLength!==undefined&&<small>至多 {field.maxLength} 个字符。</small>}{field.minimum!==undefined&&<small>不小于 {field.minimum}。</small>}{field.maximum!==undefined&&<small>不大于 {field.maximum}。</small>}{field.exclusiveMinimum!==undefined&&<small>须大于 {field.exclusiveMinimum}。</small>}{field.exclusiveMaximum!==undefined&&<small>须小于 {field.exclusiveMaximum}。</small>}{field.multipleOf!==undefined&&<small>须为 {field.multipleOf} 的倍数，由服务器核对。</small>}</label>;
}
export default function ManualObservationForm({instanceId,account,objects,releaseID,enabled,observations=[],hasMore=false,Btn,onRefresh,onRegisterGuard}){
 const scope={workspace_id:account.workspace_id,user_id:account.user_id,username:account.username,instance_id:instanceId},identity=manualCanonical(scope),key='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId,base='/workspace/instances/'+instanceId;
 const current=useRef(null),epoch=useRef(0),operation=useRef(null);
 const deliveryGuards=useRef(new Map()),guardCallback=useRef(onRegisterGuard);guardCallback.current=onRegisterGuard;
 function registerDeliveryGuard(key,fn,previous){if(fn)deliveryGuards.current.set(key,fn);else if(!previous||deliveryGuards.current.get(key)===previous)deliveryGuards.current.delete(key);}
 useEffect(()=>{const guard=()=>{for(const check of deliveryGuards.current.values())if(check()===false)return false;return true;};guardCallback.current?.(guard);return()=>guardCallback.current?.(null,guard);},[]);
 const [cache,setCache]=useState(null),[options,setOptions]=useState({sources:[],rules:[]}),[role,setRole]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[conflict,setConflict]=useState(null);
 current.current={...(current.current||{}),identity,scope,cache};
 useEffect(()=>{
  const generation=++epoch.current;operation.current=null;setBusy(false);setOptions({sources:[],rules:[]});setRole(null);setError('');setNotice('');setConflict(null);
  try{const value=initialCache(localStorage,key,scope);current.current.raw=value.raw;current.current.cache=value.cache;setCache(value.cache);}catch(e){setCache(null);setError(e.message);}
  refreshOptions(generation).catch(e=>{if(epoch.current===generation)setError(e.message);});
  return()=>{epoch.current++;};
 },[identity,enabled]);
 async function refreshOptions(generation=epoch.current){
  const user=checkedManualIdentity(await json('/me',{headers:{'X-Workspace-ID':scope.workspace_id}}),account);
  const value=enabled?await json(base+'/reported-observation-options'):{sources:[],rules:[]};
  if(epoch.current===generation){setRole(user.role);setOptions(value);}
  return user;
 }
 function commit(next){
  if(current.current.identity!==identity||!current.current.cache)throw Error('当前登记范围已变化，原稿保留。');
  if(localStorage.getItem(key)!==current.current.raw)throw Error('另一页面已更新登记草稿，请刷新核对，原请求未覆盖。');
  const value={...next,scope},encoded=JSON.stringify(value);localStorage.setItem(key,encoded);
  if(localStorage.getItem(key)!==encoded)throw Error('登记恢复记录未完整保存，未发送请求。');
  current.current.raw=encoded;current.current.cache=value;setCache(value);return value;
 }
 function edit(patch){try{commit({...current.current.cache,...patch});setError('');}catch(e){setError(e.message);}}
 const eligible=perceptionObjectState(objects,cache?.object,releaseID).usable,writable=enabled&&['admin','operator'].includes(role),frozen=!!cache?.pending,cap=options.rules.find(row=>row.id===cache?.rule);
 let definition=null,definitionIssue='';try{if(cap)definition=registrationDefinition(cap);}catch(e){definitionIssue=e.message;}
 async function act(fn,{write=false}={}){
  if(operation.current||!cache)return;const token={identity,epoch:epoch.current};operation.current=token;setBusy(true);setError('');const active=()=>operation.current===token&&current.current.identity===identity&&epoch.current===token.epoch;
  try{const user=checkedManualIdentity(await json('/me',{headers:{'X-Workspace-ID':scope.workspace_id}}),account);if(!active())return;setRole(user.role);if(write&&(!enabled||!['admin','operator'].includes(user.role)))throw Object.assign(Error('当前账号或实例不能新增登记。'),{status:403});await fn(active);}
  catch(e){if(active()){if(e.status===401||e.status===403)setRole(null);setError(e.message+' 原输入和请求保留。');}}
  finally{if(active()){operation.current=null;setBusy(false);}}
 }
 async function resolve(body,active){
  const value=checkedRegistrationResolution(await json(base+'/reported-observations/resolve',{method:'POST',body:JSON.stringify(body)}),body);
  if(!active())return null;
  if(value.found&&value.request_matches){commit({...current.current.cache,saved:value.record.id,saved_matches:true,last_failure:null});setConflict(null);setNotice('已按原编号和完整内容核对保存的登记。');}
  else if(value.found){setConflict(value.record);setNotice('同编号已有其他正文，本次输入未被保存；请对照已保存的原登记。');}
  else setNotice('尚未查到原编号，送达状态仍未知；重试仍使用同一编号和完整原文。');
  return value;
 }
 async function submit(){await act(async active=>{
  let body=current.current.cache.pending;
  if(!body){
   const value=current.current.cache,selected=options.rules.find(row=>row.id===value.rule),source=options.sources.find(row=>row.id===value.source);
   assertPerceptionObjects(objects,[value.object],releaseID);
   if(!selected||!source||!value.when)throw Error('请选择当前对象、人工来源、本地处理方式并填写登记时间。');
   const attributes=registrationAttributes(selected,value.values),when=new Date(value.when+'+08:00');if(!Number.isFinite(when.getTime()))throw Error('登记时间无效。');
   const fixed=value.correction_identity;
   if(fixed&&source.id!==fixed.source_id)throw Error('修正尝试必须保留原来源和原登记编号。');
   body={release_id:releaseID,source_id:source.id,source_record_id:fixed?.source_record_id||'reported-'+crypto.randomUUID(),entity_id:value.object,extractor_id:selected.id,attributes,observed_at:when.toISOString(),business_day:new Intl.DateTimeFormat('sv-SE',{timeZone:source.timezone}).format(when),startup_mode:'manual'};
   if(fixed&&Object.prototype.hasOwnProperty.call(fixed,'processing_version'))body.processing_version=fixed.processing_version;
   commit({...value,pending:body,last_failure:null,definition_at_send:{id:selected.id,version:selected.version??null,content_hash:selected.content_hash??null,fields:registrationDefinition(selected).fields}});
  }
  assertPerceptionRequestRelease(body.release_id,releaseID);assertPerceptionObjects(objects,[body.entity_id],releaseID);
  try{
   const result=await json(base+'/reported-observations',{method:'POST',body:JSON.stringify(body)});if(!active())return;
   if(result.source_id!==body.source_id||result.source_record_id!==body.source_record_id||result.entity_id!==body.entity_id||result.data?.extractor_id!==body.extractor_id)throw Error('服务器登记回执与原请求不一致，请按原编号核对。');
   const value=await resolve(body,active);if(value?.found&&value.request_matches)await onRefresh();
  }catch(e){if(active()&&e.status===422)commit({...current.current.cache,last_failure:{status:422,detail:e.message}});throw e;}
 },{write:true});}
 async function recover(){await act(async active=>{const body=current.current.cache.pending;if(!body)return;const value=await resolve(body,active);if(value?.found&&value.request_matches)await onRefresh();});}
 async function correct(){await act(async active=>{
  const value=current.current.cache;if(!value.pending||value.last_failure?.status!==422)throw Error('只有已收到明确输入错误的原尝试可进入修正。');
  const resolved=await resolve(value.pending,active);if(!resolved||resolved.found)return;
  const fixed={source_id:value.pending.source_id,source_record_id:value.pending.source_record_id};if(Object.prototype.hasOwnProperty.call(value.pending,'processing_version'))fixed.processing_version=value.pending.processing_version;
  commit({...value,pending:null,last_failure:null,source:fixed.source_id,correction_identity:fixed,attempts:[...value.attempts,{body:copy(value.pending),fields:value.definition_at_send?.fields||[],status:'rejected_422',failure:value.last_failure}]});
  setNotice('原422尝试已完整保留。现在可修正输入，仍使用原来源和同一编号；服务器若已有原正文将拒绝不同内容。');
 });}
 function next(){try{
  const value=current.current.cache;if(!value.saved)throw Error('请先核对原登记是否已保存。');
  commit({...value,pending:null,saved:null,saved_matches:null,last_failure:null,correction_identity:null,values:{},when:clock(),attempts:[...value.attempts,{body:copy(value.pending),fields:value.definition_at_send?.fields||[],status:value.saved_matches===false?'conflict_not_saved':'saved',record_id:value.saved}]});setConflict(null);setNotice('上条登记请求与输入已保留，可填写下一条。');
 }catch(e){setError(e.message);}}
 function acknowledgeConflict(){try{if(!conflict||!current.current.cache.pending)throw Error('请先核对同编号保存的原登记。');commit({...current.current.cache,saved:conflict.id,saved_matches:false});setNotice('已保留本次未保存的修正稿，并核对同编号已有原登记。可明确选择登记下一条；不会将修正稿当作已保存。');}catch(e){setError(e.message);}}
 async function refresh(){await act(async active=>{await refreshOptions();await onRefresh();if(active())setNotice('已刷新服务器登记与当前权限，原输入和请求保留。');});}
 return <details><summary>人工登记与回执核查</summary><p>登记已有资料，按已发布方式在本地处理；不连接设备、不上传文件或调用云模型。登记时间不证明设备真实采集时间。</p>
  {!cache?<p>原登记草稿尚不能恢复，未开放发送。</p>:<>
   {!options.sources.length||!options.rules.length?<p>需先登记人工来源及本地处理能力；本页不模拟数据或处理成功。</p>:<>
    <label>登记对象<select aria-label="规则登记对象" value={cache.object} disabled={busy||frozen||!writable} onChange={e=>edit({object:e.target.value})}><option value="">请选择对象</option>{perceptionObjectOptions(objects,cache.object,releaseID)}</select></label>{perceptionSelectionNote(objects,cache.object,releaseID)}
    <label>人工数据来源<select aria-label="人工数据来源" value={cache.source} disabled={busy||frozen||!writable||!!cache.correction_identity} onChange={e=>edit({source:e.target.value})}><option value="">请选择已登记来源</option>{options.sources.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
    <label>本地处理方式<select aria-label="本地处理方式" value={cache.rule} disabled={busy||frozen||!writable} onChange={e=>edit({rule:e.target.value,values:{}})}><option value="">请选择已发布方式</option>{options.rules.map(row=><option key={row.id} value={row.id}>{row.name}{row.registration_mode==='passthrough'?' · 原样登记':row.registration_mode==='unsupported'?' · 暂不支持':''}</option>)}</select></label>
    {definition?.mode==='passthrough'?<p>原样登记：保留填写的属性，不推断阈值，也不判断现场是否正常。</p>:definition?.mode==='threshold'?<><p>按已登记阈值核查，命中只形成待核查线索。</p>{(cap.rules||[]).map((rule,index)=><p key={index}>{definition.fields.find(field=>field.key===rule.field)?.label||'已登记字段'} {rule.operator==='gt'?'大于':'小于'} {rule.threshold} 时报告待核查线索。</p>)}</>:null}
    {definitionIssue&&<p role="alert">{definitionIssue}</p>}
    {definition?.fields.map(field=>registrationControl({field,value:cache.values[field.key],disabled:busy||frozen||!writable,onChange:value=>edit({values:{...current.current.cache.values,[field.key]:value}})}))}
    <label>登记时间（北京时间）<input aria-label="人工登记时间" type="datetime-local" value={cache.when} disabled={busy||frozen||!writable} onChange={e=>edit({when:e.target.value})}/></label>
   </>}
   <div className="actions"><Btn primary disabled={busy||!writable||!eligible||!!cache.saved||(!cache.pending&&!definition)} onClick={submit}>{cache.pending?'按原编号重试登记':'保存登记并本地处理'}</Btn>{cache.pending&&!cache.saved&&<Btn disabled={busy} onClick={recover}>核对原登记请求</Btn>}{cache.pending&&!cache.saved&&cache.last_failure?.status===422&&<Btn disabled={busy} onClick={correct}>保留原请求并修正输入</Btn>}<Btn disabled={busy||!cache.saved} onClick={next}>登记下一条</Btn><Btn disabled={busy} onClick={refresh}>刷新登记方式和权限</Btn></div>
   {cache.pending&&!cache.saved&&<Btn disabled={busy} onClick={()=>{try{retainPerceptionPending(localStorage,key,current.current.cache.pending,'pending');commit({...current.current.cache,pending:null,last_failure:null});setNotice('原编号、原文与草稿已独立保留；后台登记未取消，请先核对历史，不把新登记当作原请求的成功回执。');}catch(e){setError(e.message);}}}>保留原登记请求，取消本机等待</Btn>}
   {cache.saved&&<p role="status">{cache.saved_matches===false?'同编号已有原登记；本次修正未保存，输入已保留。':'原编号与正文已在服务器核对保存；本地处理、归集和人工核查分别见下方。'}</p>}
   {!!cache.attempts.length&&<details><summary>已保留 {cache.attempts.length} 份登记尝试</summary>{cache.attempts.map((attempt,index)=><article key={index}><p>{attempt.status==='saved'?'原登记已保存':attempt.status==='conflict_not_saved'?'修正稿因同编号正文冲突未保存':'原尝试收到输入错误，后续修正没有覆盖它'}</p>{attempt.body&&<><p>登记时间：{new Date(attempt.body.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</p>{(attempt.fields||[]).map(field=><p key={field.key}>{field.label}：{displayManualValue(attempt.body.attributes?.[field.key])}{field.unit?' '+field.unit:''}</p>)}</>}</article>)}</details>}
  </>}{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {conflict&&<div role="alert"><p>本次修正没有被采用；请核对同编号已保存的原登记。原输入与尝试完整保留，不会另换编号重复登记。</p><DataDeliveryStudio {...{instanceId,account,Btn}} observationId={conflict.id} entityId={conflict.entity_id} sourceName="同编号原登记" draftChannel="manual-conflict" onReload={onRefresh} onRegisterGuard={(fn,previous)=>registerDeliveryGuard('conflict:'+conflict.id,fn,previous)}/><ReportedObservationReview record={conflict} {...{instanceId,account,Btn,onRefresh}}/>{!cache?.saved&&<Btn disabled={busy} onClick={()=>{for(const check of deliveryGuards.current.values())if(check()===false){setError('收件说明或原申请尚未保存，冲突记录保留。');return;}acknowledgeConflict();}}>保留修正稿并结束冲突核对</Btn>}</div>}
  {observations.map(row=><article key={row.id}><p>{objects.find(object=>object.id===row.entity_id)?.name||'原登记对象'} · {stage[row.status]||'需核查'} · 数据管理：{row.data_received?'原记录标记已接收，独立回执见下方':row.data_delivery?.status==='blocked'?'接收受阻':row.data_delivery?.status==='needs_review'?'接收依据需核查':'等待送达'}{row.error&&<span> · {row.error}</span>}</p>{row.data_delivery?.error&&<p className="error" role="alert">{row.data_delivery.error}</p>}<DataDeliveryStudio {...{instanceId,account,Btn}} observationId={row.id} entityId={row.entity_id} sourceName={objects.find(object=>object.id===row.entity_id)?.name||'原登记观测'} draftChannel="manual" onReload={onRefresh} onRegisterGuard={(fn,previous)=>registerDeliveryGuard(row.id,fn,previous)}/>{row.retry_available&&<Btn disabled={busy||!writable||!perceptionObjectState(objects,row.entity_id,releaseID).usable} onClick={()=>act(async()=>{assertPerceptionObjects(objects,[row.entity_id],releaseID);await json('/observations/'+row.id+'/retry',{method:'POST',body:JSON.stringify({expected_version:row.version,reason:'核对保存的原登记后重试本地处理'})});await onRefresh();},{write:true})}>重试保存的本地处理</Btn>}<ReportedObservationReview record={row} {...{instanceId,account,Btn,onRefresh}}/></article>)}
  {hasMore&&<p>这里只列最近50条登记；未确认的原登记可用上方原请求核对，不依赖这份清单。</p>}
 </details>;
}

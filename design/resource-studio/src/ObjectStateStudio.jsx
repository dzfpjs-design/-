import React,{useEffect,useRef,useState} from 'react';
import {perceptionObjectState,assertPerceptionObjects} from './perception-entity-contract.mjs';
import {perceptionObjectOptions,perceptionSelectionNote} from './perception-object-fields';
import {json} from './runtime-api';
import ObjectStateBackground from './ObjectStateBackground';
import StateInputIssue from './StateInputIssue';
import NativeCandidateQueryV139 from './NativeCandidateQueryV139';
import DataDeliveryStudio from './DataDeliveryStudio.jsx';
import {deliveryUUID,deliveryPendingScopes} from './data-delivery-v245-protocol.mjs';
import {assertCandidateQueryDraftSavedV140} from './native-candidate-query-v139.mjs';
import {ObjectStateBackgroundControllerV267,stateBackgroundScope,stateBackgroundPrefix,stateBackgroundCanonical} from './object-state-background-v267.mjs';
import {measurementUnit} from './measurement-unit-v342.mjs';
const statuses={observed:'已核对登记记录',conflict:'登记结果存在冲突',insufficient:'状态依据不足',blocked_integrity:'原状态缓存未通过核对'};
const reasons={missing_value:'字段原值未填写',incomplete_input:'有待处理或失败的新输入；已有值不是这些输入的处理结果',input_scan_limit:'未完成输入超过显示上限，请核查历史处理范围',missing:'缺少所需字段',stale:'登记时间已超过本次核查期限',conflict:'同一登记时刻的值或单位需要核对',excluded_evidence:'最新登记有未通过核对或被拒绝的依据',scan_limit:'已达到扫描上限，需要核查历史范围'};
const valueText=value=>value===null?'未确定':typeof value==='boolean'?(value?'是':'否'):Array.isArray(value)?value.map(valueText).join('；'):typeof value==='object'?Object.entries(value).map(([k,v])=>k+'：'+valueText(v)).join('；'):String(value);
function StateRecord({instanceId,entityId,recordId,stateHash,Btn,onFailure,onDataDelivery}){
 const [record,setRecord]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const life=useRef(0);useEffect(()=>()=>{life.current++;},[]);
 async function load(){const epoch=life.current;setBusy(true);setError('');try{
  const value=await json(`/workspace/instances/${instanceId}/objects/${entityId}/state-records/${recordId}?expected_state_hash=${stateHash}`);
  if(life.current===epoch)setRecord(value);
 }catch(e){if(life.current===epoch){setRecord(null);setError(e.message);onFailure();}}
 finally{if(life.current===epoch)setBusy(false);}}
 return <details><summary>查看登记与提取依据</summary><Btn disabled={busy} onClick={load}>{busy?'正在读取原依据…':'读取原登记与回执'}</Btn>
 {error&&<p role="alert">{error}</p>}{record&&<>
 <p>{record.source_name} · {record.original_request_saved?'原登记内容与提取回执已核对':'旧提取回执已核对，原登记时间凭证缺失'}。来源时间和现场真实性仍待核实。</p>
 <h5>原登记内容</h5><dl>{Object.entries(record.original_attributes).map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd>{valueText(v)}</dd></React.Fragment>)}</dl>
 <h5>提取回执</h5><dl>{Object.entries(record.extracted_attributes).map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd>{valueText(v)}</dd></React.Fragment>)}</dl>
 <p>文件依据：{record.evidence_count?record.evidence_count+'份，已核对原件摘要':'未关联文件，已核对登记与提取回执'}。人工核查：{record.review==='rejected'?'已拒绝':record.review?'已保存确认':'尚未核查'}。</p>
 {record.review_reason&&<p>{record.review_reason}</p>}{onDataDelivery&&<Btn disabled={busy} onClick={()=>onDataDelivery(record.source_name)}>核对这条登记的数据收件</Btn>}
 </>}</details>;
}
export default function ObjectStateStudio({instanceId,objects,releaseID,Btn,formKey,canWrite=true,account,onOpenTask,onRegisterGuard,request=json,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const [selected,setSelected]=useState(''),[storedSaved,setSaved]=useState(null),[savedScope,setSavedScope]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0),pending=useRef(null);
 const [waiting,setWaiting]=useState(false),[cacheError,setCacheError]=useState('');
 const controller=useRef(null),[backgroundRead,setBackgroundRead]=useState(null),writeAllowed=useRef(false);
 const paused=useRef(null),[pausedScope,setPausedScope]=useState(null),[legacyTickets,setLegacyTickets]=useState([]);
 const [deliveryRecord,setDeliveryRecord]=useState(null),[deliveryRecoveries,setDeliveryRecoveries]=useState([]),deliveryRecordOwner=useRef(''),deliveryGuard=useRef(null),guardCallback=useRef(onRegisterGuard);guardCallback.current=onRegisterGuard;
 function registerDeliveryGuard(fn,previous){if(fn||!previous||deliveryGuard.current===previous)deliveryGuard.current=fn;}
 useEffect(()=>{const guard=()=>{if(controller.current&&!controller.current.guard())return false;return deliveryGuard.current?deliveryGuard.current():true;};guardCallback.current?.(guard);return()=>guardCallback.current?.(null,guard);},[]);
 function deliveryAvailable(){if(deliveryGuard.current&&deliveryGuard.current()===false){setError('当前收件说明或原申请尚未完整保存，请先重试保存；当前观测保留。');return false;}return true;}
 const cacheKey=(formKey||'ga-object-state:'+instanceId)+':state-refresh';
 const ownerScope=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId,releaseID,cacheKey]);
 const scope=JSON.stringify([ownerScope,selected]),renderScope=useRef(scope);renderScope.current=scope;
 const saved=savedScope===scope?storedSaved:null;
 function persist(identity=selected,delivery=deliveryRecord){const encoded=JSON.stringify({selected:identity,pending:pending.current,data_delivery:delivery});storage.setItem(cacheKey,encoded);if(storage.getItem(cacheKey)!==encoded)throw Error('状态核查选择和原请求未完整保存');}
 useEffect(()=>{generation.current++;setSaved(null);setSavedScope(null);setSelected('');deliveryRecordOwner.current='';setDeliveryRecord(null);setError('');setBusy(false);pending.current=null;setWaiting(false);setCacheError('');try{const restored=JSON.parse(storage.getItem(cacheKey)||'null');if(restored){if(typeof restored.selected!=='string'||(restored.pending&&(!restored.pending.request_id||restored.pending.entity_id!==restored.selected)))throw Error('原本地核对请求无法恢复，请保留浏览器记录。');pending.current=restored.pending||null;setWaiting(!!restored.pending);setSelected(restored.selected);if(restored.data_delivery){if(!deliveryUUID(restored.data_delivery.observation_id)||restored.data_delivery.entity_id!==restored.selected)throw Error('原观测选择无法恢复，原收件记录仍保留。');deliveryRecordOwner.current=ownerScope;setDeliveryRecord(restored.data_delivery);}}}catch(e){setCacheError(e.message);}return()=>{generation.current++;};},[ownerScope,storage]);
 writeAllowed.current=canWrite&&!cacheError&&perceptionObjectState(objects,selected,releaseID).usable&&!pending.current;
 useEffect(()=>{setBackgroundRead(null);paused.current=null;setPausedScope(null);if(!selected||!account){
  // The previous effect already disposed its controller. Its guarded finally
  // cannot release a busy flag emitted before the owner reset cleared selection.
  setBusy(false);return;
 }const ownScope=scope;let c;try{c=new ObjectStateBackgroundControllerV267({account,instanceId,entityId:selected,request,storage,tabStorage,locks,current:()=>renderScope.current===ownScope,canRequest:()=>writeAllowed.current});}catch(e){setError(e.message);return;}controller.current=c;c.onChange=()=>{if(renderScope.current!==ownScope)return;setBackgroundRead({scope:ownScope,busy:c.busy,verified:c.verified,denied:c.denied,value:c.value,error:c.error,notice:c.notice,pending:c.pending,receipt:c.receipt,loaded:c.loaded,durable:c.guard()});setBusy(c.busy);if(c.verified&&c.value?.stored_state){const s=c.value.stored_state;setSaved({state:{...s.value,state_version:s.version,state_hash:s.state_hash,evaluated_at:s.evaluated_at},background:c.value.background});setSavedScope(ownScope);}};c.start();const timer=setInterval(()=>{if(!c.busy&&!c.denied&&paused.current!==ownScope)c.refresh();},5000);return()=>{clearInterval(timer);c.dispose();if(controller.current===c)controller.current=null;};},[scope,request,storage,tabStorage,locks]);
 function retainLegacy(){const original=pending.current;if(!original)return;const raw=storage.getItem(cacheKey),record=JSON.parse(raw),owned=stateBackgroundScope(account,instanceId,original.entity_id);if(record?.pending?.request_id!==original.request_id||record.selected!==original.entity_id)throw Error('旧版等待记录已变化；未离开或覆盖。');const key=stateBackgroundPrefix(owned)+'legacy-waiting:'+crypto.randomUUID(),encoded=JSON.stringify({schema:'object-state-legacy-waiting-v267',scope:owned,cache_raw:raw});storage.setItem(key,encoded);if(storage.getItem(key)!==encoded||storage.getItem(cacheKey)!==raw)throw Error('旧版原申请尚未完整保留，请先重试。');return original;}
 function change(identity){if(!deliveryAvailable())return;try{if(controller.current&&!controller.current.guard())throw Error('原后台申请尚未完整保存在本机，请先重试保存。');assertCandidateQueryDraftSavedV140(account,instanceId);}catch(e){setError(e.message);return;}const original=pending.current;try{if(original)retainLegacy();pending.current=null;persist(identity,null);}catch(e){pending.current=original;setCacheError('对象选择尚未保存：'+e.message);return;}setWaiting(false);generation.current++;setDeliveryRecord(null);setSelected(identity);setSaved(null);setSavedScope(null);setError('');setBusy(false);}
 function findLegacy(){try{const rows=[],prefix='ga-object-state-background-v267:';for(let i=0;i<storage.length;i++){const key=storage.key(i);if(!key?.startsWith(prefix)||!key.includes('}:legacy-waiting:'))continue;let keyScope;try{keyScope=JSON.parse(key.slice(prefix.length,key.indexOf('}:legacy-waiting:')+1));}catch{if(key.includes('"actor_id":"'+account.user_id+'"')&&key.includes('"workspace_id":"'+account.workspace_id+'"'))throw Error('本机旧申请目录有不可读项，原字节保留。');continue;}if(keyScope?.actor_id!==account.user_id||keyScope.workspace_id!==account.workspace_id||keyScope.instance_id!==instanceId)continue;const exactScope=stateBackgroundScope(account,instanceId,keyScope.entity_id),raw=storage.getItem(key),item=JSON.parse(raw);if(item.schema!=='object-state-legacy-waiting-v267'||stateBackgroundCanonical(item.scope)!==stateBackgroundCanonical(exactScope)||!key.startsWith(stateBackgroundPrefix(exactScope)+'legacy-waiting:'))throw Error('本机旧申请范围无法核对，原字节保留。');const old=JSON.parse(item.cache_raw);if(old.selected!==exactScope.entity_id||old.pending?.entity_id!==old.selected||!old.pending.request_id)throw Error('本机旧申请原文无法核对，原字节保留。');rows.push({key,raw,scope:exactScope,original:old});}setLegacyTickets(rows);setError('');}catch(e){setError(e.message);}}
 function restoreLegacy(ticket){if(!deliveryAvailable())return;const previous=pending.current;try{if(controller.current&&!controller.current.guard())throw Error('本页原申请尚未保存。');if(storage.getItem(ticket.key)!==ticket.raw)throw Error('原保留记录已变化，未恢复。');if(previous)retainLegacy();pending.current=ticket.original.pending;persist(ticket.scope.entity_id,null);generation.current++;setSelected(ticket.scope.entity_id);setWaiting(true);setDeliveryRecord(null);setSaved(null);setSavedScope(null);setError('');}catch(e){pending.current=previous;setError(e.message);}}
 function openDelivery(observationId,sourceName){if(!deliveryAvailable())return;if(!deliveryUUID(observationId)||!deliveryUUID(selected)){setError('原观测或对象编号尚不能核对，未切换收件记录。');return;}const next={observation_id:observationId,entity_id:selected,source_name:sourceName||'所选观测'};try{persist(selected,next);deliveryRecordOwner.current=ownerScope;setDeliveryRecord(next);setError('');}catch(e){setCacheError(e.message);}}
 function findDeliveryRecoveries(){try{setDeliveryRecoveries(deliveryPendingScopes(storage,account,instanceId));setError('');}catch(e){setError('本机原申请目录尚不能读取，原记录保留。');}}
 function restoreDelivery(item){if(!deliveryAvailable())return;try{if(controller.current&&!controller.current.guard())throw Error('本页原后台申请尚未完整保存，请先重试保存；未切换到其他对象。');assertCandidateQueryDraftSavedV140(account,instanceId);const next={observation_id:item.observation_id,entity_id:item.entity_id,source_name:'保留的观测收件申请'};persist(item.entity_id,next);generation.current++;setSelected(item.entity_id);setSaved(null);setSavedScope(null);deliveryRecordOwner.current=ownerScope;setDeliveryRecord(next);setError('');}catch(e){setError(e.message);}}
 async function load(download=false){const c=controller.current;if(!c||c.busy)return;setError('');await c.refresh();if(download&&c.active&&c.current()&&c.verified&&c.value?.stored_state){const blob=new Blob([JSON.stringify(c.value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='对象状态与核查依据.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}
 async function requestRefresh(){
  if(!selected||!canWrite||cacheError||pending.current)return;try{assertPerceptionObjects(objects,[selected],releaseID);await controller.current?.submit();}catch(e){setError(e.message+' 原申请保留。');}
 }
 async function recoverLegacy(){const c=controller.current,original=pending.current,originalScope=scope,raw=storage.getItem(cacheKey);if(!c||!original)return;const found=await c.recoverLegacy(original.request_id);if(!found?.found||renderScope.current!==originalScope)return;if(!locks?.request){setError('原申请已核对；当前仅只读显示，未清除旧等待记录。');return;}try{if(storage.getItem(cacheKey)!==raw||stateBackgroundCanonical(JSON.parse(storage.getItem(c.journal.prefix+'legacy-receipt:'+original.request_id)))!==stateBackgroundCanonical(found.request))throw Error('原回执尚未完整保存或另一页面已改变等待记录，未清除。');pending.current=null;persist();setWaiting(false);}catch(e){pending.current=original;setCacheError(e.message);}}
 const currentRead=backgroundRead?.scope===scope?backgroundRead:null,state=currentRead?.denied?null:saved?.state,eligible=perceptionObjectState(objects,selected,releaseID).usable,verified=currentRead?.verified&&currentRead.value?.cache.status==='saved';
 return <section className="media-form object-state-studio">
  <h3>对象状态与依据</h3>
  <p>核对已登记数据及原提取回执，保留缺少、过期和冲突的信息。这里不把档案属性或模型候选当作现场现状。</p>
  <label>选择核查对象<select aria-label="状态核查对象" disabled={busy||!!cacheError} value={selected} onChange={e=>change(e.target.value)}>
   <option value="">请选择对象</option>{perceptionObjectOptions(objects,selected,releaseID)}
  </select></label>{perceptionSelectionNote(objects,selected,releaseID)}
  <div className="actions"><Btn disabled={busy||!selected} onClick={()=>load()}>{busy?'正在核对依据…':'核对已保存状态'}</Btn>
  <Btn disabled={busy||!selected||!saved} onClick={()=>load(true)}>导出状态与依据</Btn>
  {canWrite&&<Btn disabled={busy||!selected||!eligible||!!cacheError||waiting||!!currentRead?.pending||!currentRead?.verified||!currentRead.value?.permissions.can_request_new||!locks?.request} onClick={requestRefresh}>请求后台本地重新核对</Btn>}</div>
  {!locks?.request&&<p role="status">本页不支持多窗口写保护；可读取状态与本人原回执，暂不能创建或重试提交后台请求。</p>}
  {account&&<NativeCandidateQueryV139 {...{account,instanceId,releaseID,objects,Btn,onOpenTask}} selectedObject={selected} draftChannel="object-state"/>}
  {waiting&&<p role="status">旧版本原后台申请仍保留；只核对原号，不因当前版本或权限变化改签。<Btn disabled={busy} onClick={recoverLegacy}>核对旧版原后台申请</Btn></p>}
  {currentRead?.pending&&<aside aria-label="原后台申请待核对"><p>{currentRead.durable?'原申请已保留；未查到回执也不表示未执行，不自动重发或更换编号。':'原申请暂留本页，尚未完整保存；请先重试保存，不能据此离开或发送。'}</p><Btn disabled={busy||!currentRead.durable} onClick={()=>controller.current?.recover()}>只核对原后台申请</Btn>{canWrite&&<Btn disabled={busy||!locks?.request||!currentRead.verified||!currentRead.value?.permissions.can_request_new||!currentRead.durable} onClick={()=>controller.current?.resend()}>按原编号重试提交</Btn>}{!currentRead.durable&&<Btn disabled={busy||!locks?.request} onClick={()=>controller.current?.repair()}>重试保存原后台申请</Btn>}</aside>}
  {(currentRead?.pending||waiting)&&<Btn disabled={busy||currentRead?.durable===false} onClick={()=>{if(controller.current&&!controller.current.guard()){setError('原申请尚未完整保留，请先重试保存。');return;}paused.current=scope;setPausedScope(scope);}}>暂时离开，保留原请求</Btn>}
  {pausedScope===scope&&<p role="status">已停止本页自动读取；原申请和后台任务没有取消，可以选择其他对象。<Btn disabled={busy} onClick={()=>{paused.current=null;setPausedScope(null);load();}}>恢复读取后台进度</Btn></p>}
  <Btn disabled={busy} onClick={findLegacy}>查找本机保留的旧后台申请</Btn>{legacyTickets.filter(t=>t.scope.actor_id===account?.user_id&&t.scope.workspace_id===account?.workspace_id&&t.scope.instance_id===instanceId).map((t,i)=><p key={t.key}><Btn disabled={busy} onClick={()=>restoreLegacy(t)}>恢复{objects.find(o=>o.id===t.scope.entity_id)?.name||'原对象'}的第{i+1}份旧后台申请</Btn></p>)}
  {currentRead?.notice&&<p role="status">{currentRead.notice}</p>}{currentRead?.error&&<p role="alert">{currentRead.error} 此前读回记录保留，本次未重新核对。</p>}
  {currentRead?.value&&<ObjectStateBackground background={currentRead.value.background} stateHash={state?.state_hash} stateVersion={state?.state_version} cache={currentRead.value.cache} verified={currentRead.verified} permissions={currentRead.value.permissions}/>}
  {cacheError&&<p role="alert">{cacheError} 已阻止创建新后台请求。</p>}
  {error&&<p role="alert" className="error">{error}</p>}
  <Btn disabled={busy} onClick={findDeliveryRecoveries}>查找本机保留的收件申请</Btn>{deliveryRecoveries.filter(item=>item.actor_id===account?.user_id&&item.workspace_id===account?.workspace_id&&item.instance_id===instanceId).map((item,i)=><p key={item.observation_id}><Btn disabled={busy||waiting} onClick={()=>restoreDelivery(item)}>恢复{objects.find(o=>o.id===item.entity_id)?.name||'原对象'}的第{i+1}份收件申请</Btn></p>)}
  {deliveryRecordOwner.current===ownerScope&&deliveryRecord&&<DataDeliveryStudio key={ownerScope+':'+deliveryRecord.observation_id} {...{account,instanceId,Btn}} observationId={deliveryRecord.observation_id} entityId={deliveryRecord.entity_id} sourceName={deliveryRecord.source_name} draftChannel="object-state" initialOpen onRegisterGuard={registerDeliveryGuard} onReload={()=>load()}/>}
  {!objects.length&&<p>先登记业务对象，再核查其数据和状态。</p>}
  {state&&<>{!eligible&&<p role="status">以下为已保存的历史核查；当前对象资格待恢复，不能当作新的状态核对或设备授权。</p>}
   {!verified&&<p role="alert">以下是此前读回的状态，当前缓存或读取结果尚未通过核对；不能当作本次更新成功。</p>}
   <h4>{statuses[state.status]||'需要核查'} · 第{state.state_version}版</h4>
   <p>现场状态：尚未核实。登记时间未经现场校验，记录完整不代表对象正常。</p>
   <p>已保存：{new Date(state.evaluated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）。同样依据重复核查沿用本版。</p>
   <p>{state.requirements_defined?'所需字段与各字段核查期限来自已批准监测策略。':'尚未设置已批准监测字段；本次仅核对已有登记，不判断全部信息是否齐全。'}{!state.requirements_defined&&<>登记超过{state.freshness_seconds/3600}小时会提示过期。</>}</p>
   {Object.keys(state.fields).length?<table className="evidence-values"><thead><tr><th>字段</th><th>登记值与依据</th></tr></thead><tbody>{Object.entries(state.fields).map(([key,field])=><tr key={key}><td>{key}</td><td>
    {field.conflicted?<><strong>值或单位需要核对，尚未确定</strong>{field.candidates.map((c,i)=><p key={i}>{c.source_name}：{valueText(c.value)}{typeof c.value==='number'&&<> · {measurementUnit(c.measurement_unit,c.value)}</>}</p>)}</>:<strong>{valueText(field.value)}{typeof field.value==='number'&&<> · {measurementUnit(field.measurement_unit,field.value)}</>}</strong>}
    <p>登记时间：{new Date(field.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间，来源时间待核）。{field.observation_ids.length}条登记已核对原提取回执。核查期限：{field.freshness_seconds/3600}小时。</p>{field.candidates.map(c=><StateRecord key={c.observation_id+':'+state.state_hash} instanceId={instanceId} entityId={selected} recordId={c.observation_id} stateHash={state.state_hash} Btn={Btn} onDataDelivery={name=>openDelivery(c.observation_id,name||c.source_name)} onFailure={()=>{setSaved(null);setError('原依据暂无法核对，已收起状态数值。请恢复连接后重新核对已保存状态。');}}/>)}
   </td></tr>)}</tbody></table>:<p>尚无通过核对、可采用的登记字段。资料少或处理失败时不能判定正常。</p>}
   {state.input_issues?.length>0&&<><h4>待处理或失败的新输入</h4><p>下方原输入尚未产生成功结果；上方成功登记仅供核查。</p>{state.input_issues.map(issue=><div key={issue.observation_id}><StateInputIssue issue={issue} instanceId={instanceId} entityId={selected} stateHash={state.state_hash} formKey={cacheKey} canWrite={canWrite&&eligible} Btn={Btn} onReload={()=>load()}/><Btn disabled={busy} onClick={()=>openDelivery(issue.observation_id,issue.source_name)}>查看此输入的数据收件</Btn></div>)}{state.inputs_truncated&&<p>这里只显示最近20条未完成输入，不能视为全部处理记录。</p>}</>}
   {state.corrected_inputs?.length>0&&<details><summary>已另存更正的失败登记（{state.corrected_inputs.length}条）</summary><p>原失败没有删除。已核对独立更正的处理回执和数据管理送达，不代表现场状态已核实。</p>{state.corrected_inputs.map(issue=><div key={issue.observation_id}><StateInputIssue issue={issue} instanceId={instanceId} entityId={selected} stateHash={state.state_hash} formKey={cacheKey} canWrite={canWrite&&eligible} Btn={Btn} onReload={()=>load()}/><Btn disabled={busy} onClick={()=>openDelivery(issue.observation_id,issue.source_name)}>查看此输入的数据收件</Btn></div>)}</details>}
   <h4>需要补充或核查</h4>{state.gaps.length?<ul>{state.gaps.map((g,i)=><li key={i}>{g.field?g.field+'：':''}{reasons[g.reason]||g.message||'需核查依据'}</li>)}</ul>:<p>已有登记字段在本次期限内，未发现登记冲突；现场仍需核实。</p>}
   {state.unclocked_candidates.length>0&&<p>{state.unclocked_candidates.length}条采集时间未知的候选未用于状态。{account?'可在本页“查找已接收的变化候选”检索已接收的本地学习型变化候选，其它资料仍请查看原处理记录。':'请在原处理记录核查。'}</p>}
   {state.excluded_records.length>0&&<details><summary>查看未采用的登记（{state.excluded_records.length}条）</summary>{state.excluded_records.map(r=><article key={r.observation_id}><p>{r.message}</p><Btn disabled={busy} onClick={()=>openDelivery(r.observation_id,'未采用的登记')}>核对这条未采用登记的数据收件</Btn></article>)}</details>}
   {(state.truncated||state.candidates_truncated)&&<p role="alert">本次显示达到扫描上限，不能视为已核查全部资料。</p>}
   {state.history?.length>0&&<details><summary>查看历史状态版本</summary>{state.history.map((h,i)=><p key={i}>第{h.version}版 · {statuses[h.status]||'原版本'} · {h.evaluated_at?new Date(h.evaluated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'旧记录日期待核'}。{h.integrity_failed?'原缓存数值未采用，已重新核对原登记。':'历史判断不代表当前状态。'}</p>)}{state.history_truncated&&<p>这里只保留最近10个状态快照；原登记记录仍在服务器。</p>}</details>}
  </>}
 </section>;
}

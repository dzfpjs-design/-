import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {withDraftCacheLock} from './draft-cache-contract.mjs';
import {editReview,selectedEditOperations,editSelectionProblems,makeEditPending,editJournalKey,readEditPending,writeEditPending,clearEditPending,editReceipt,editRequestState,fencedEditConflict,archiveFencedEditPending} from './setup-edit-contract.mjs';

const labels={name:'名称',title:'名称',domain:'业务领域',goal:'目标',trigger:'启动条件',output:'产出',steps:'步骤',purpose:'步骤说明',agent:'执行角色',role:'负责人',condition:'判断条件',next_label:'下一步',else_label:'未满足时',nextId:'下一步',elseId:'未满足时',next:'下一步',otherwise:'未满足时',citations:'原文依据',inferenceReason:'推断说明',inference_reason:'推断说明',quote:'原文',branch:'分支',order:'顺序',value:'内容'};
const types={set_profile:'实例说明',add_business:'新增业务',remove_business:'移除业务',rename_business:'业务名称',set_business:'业务说明',set_step:'步骤字段',set_basis:'原文依据与推断',add_step:'新增步骤',remove_step:'移除步骤',reorder_steps:'步骤顺序'};
const fieldLabel=row=>row.field_label===row.type?types[row.type]||'修改内容':row.field_label;
const hidden=new Set(['id','key','handle','sourceId','material_id','block_id','step_id','business_id','step_handle','business_handle','execution','planning_tools']);
const opaque=/^(?:(?:n_)?[bs]_[a-z0-9]+|edit_[0-9]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;
export function EditValue({value,Citation}){
 if(value===null||value===undefined||value==='')return <span>未设置</span>;
 if(typeof value==='boolean')return <span>{value?'是':'否'}</span>;
 if(typeof value==='string'||typeof value==='number')return <span>{typeof value==='string'&&opaque.test(value)?'对应业务或步骤（需核对显示名称）':String(value)}</span>;
 if(Array.isArray(value))return value.length?<ul>{value.map((item,i)=><li key={i}><EditValue value={item} Citation={Citation}/></li>)}</ul>:<span>无</span>;
 if(typeof value==='object'){
  if(typeof value.quote==='string'&&typeof value.material_id==='string'&&typeof value.block_id==='string')return <Citation citation={value}/>;
  const entries=Object.entries(value).filter(([key])=>!hidden.has(key));
  return entries.length?<dl>{entries.map(([key,item])=><React.Fragment key={key}><dt>{labels[key]||'补充内容'}</dt><dd><EditValue value={item} Citation={Citation}/></dd></React.Fragment>)}</dl>:<span>定位信息不作为修改内容展示</span>;
 }
 return <span>内容暂不能核对，请保留建议并重新读取。</span>;
}

export default function UnderstandingEditReview({record,account,Btn,Citation,Explanation,prepareDraft,onRecord,onApplied,onPending,locks=globalThis.navigator?.locks}){
 const key=editJournalKey(account,record.id),scope=useRef(key);scope.current=key;
 const [selected,setSelected]=useState([]),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[retryable,setRetryable]=useState(false);
 const [conflict,setConflict]=useState(null);
 const canEdit=['admin','operator'].includes(account?.role);let rows=[],invalid='';
 try{rows=editReview(record);}catch(e){invalid=e.message;}
 useEffect(()=>()=>{scope.current=null;},[]);
 useEffect(()=>{
  setError('');setNotice('');setRetryable(false);setConflict(null);
  try{const original=readEditPending(localStorage,key);setPending(original);setSelected(original&&original.body.result_hash===record.result_hash&&original.action!=='undo'?[...original.body.operation_ids]:selectedEditOperations(record));}
  catch(e){setError(e.message);setPending(null);}
 },[key,record.result_hash]);
 useEffect(()=>{onPending?.(!!pending||!!invalid);return()=>onPending?.(false);},[key,pending,invalid]);
 function current(expected){if(expected!==scope.current)throw Error('账号或建议已变化，原操作请求保留；请在原记录核对。');}
 async function acknowledge(remote,original,expected,payload){
  current(expected);const receipt=editReceipt(remote,original);if(!receipt)throw Error('服务器操作回执尚未核对，原请求保留。');
  onRecord(remote);
  if(original.action!=='review'){
   const saved=payload||await json('/setup/draft');current(expected);
   if(!Number.isInteger(saved.revision)||saved.revision<receipt.revision||!saved.configuration)throw Error('当前配置回执尚未核对，原操作保留；不会采用旧配置。');
   await onApplied({...saved,analysis:remote},false);current(expected);
  }
  const cleared=await withDraftCacheLock(expected,()=>{current(expected);return clearEditPending(localStorage,expected,original);},locks);
  if(!cleared)throw Error('另一页面的原操作已变化，不清除其请求。');
  setPending(null);setRetryable(false);setConflict(null);setSelected(selectedEditOperations(remote));
  setNotice(receipt.status==='noop'?'已核对：本次操作内容相同，未更改配置版本或流程确认。':original.action==='review'?'审核选择已保存到服务器。':original.action==='undo'?'原应用已撤销并保存为新版本。':'选中字段已保存为新配置版本；请核对受影响流程。');
 }
 async function inspect(original,expected){
  const remote=await json('/setup/understanding/'+encodeURIComponent(original.record_id));current(expected);onRecord(remote);
  const state=editRequestState(remote,original);setRetryable(state==='retryable');
  setConflict(state==='conflict'?fencedEditConflict(remote,original):null);
  if(state==='confirmed'){await acknowledge(remote,original,expected);return true;}
  setNotice(state==='conflict'?'服务器版本或建议已变化。原请求保留，不自动重做或覆盖后来的修改；可返回配置核对历史。':'服务器尚未找到原操作回执。请先核对，再用同一请求重试；不会另起云调用。');
  return false;
 }
 async function post(original,expected){
  try{
   const response=await json('/setup/understanding/'+encodeURIComponent(original.record_id)+'/edits/'+original.action,{method:'POST',body:JSON.stringify(original.body)});current(expected);
   await acknowledge(original.action==='review'?response:response.analysis,original,expected,original.action==='review'?null:response);
  }catch(e){
   current(expected);setError(e.message+' 原操作请求已保留。');
   try{await inspect(original,expected);}catch(readError){setError(e.message+' '+readError.message+' 原请求保留，请先核对结果。');}
  }
 }
 async function begin(action,choice=selected){
  if(!canEdit||busy||pending||invalid)return;setBusy(true);setError('');setNotice('');const expected=key;
  try{
   const revision=action==='review'?undefined:await prepareDraft(false);current(expected);
   const original=makeEditPending(action,record,choice,{revision,request_id:crypto.randomUUID()});
   await withDraftCacheLock(expected,()=>{current(expected);writeEditPending(localStorage,expected,original);},locks);setPending(original);
   const remote=await json('/setup/understanding/'+encodeURIComponent(record.id));current(expected);onRecord(remote);
   const state=editRequestState(remote,original);
   if(state==='confirmed')await acknowledge(remote,original,expected);
   else if(state==='retryable')await post(original,expected);
   else{setRetryable(false);setConflict(fencedEditConflict(remote,original));setNotice('服务器版本已变化，原选择与请求已保留；请核对原记录，不自动提交到新版本。');}
  }catch(e){setError(e.message);}finally{if(expected===scope.current)setBusy(false);}
 }
 async function recover(retry=false){
  if(busy||!pending||(retry&&!canEdit))return;const original=pending,expected=key;setBusy(true);setError('');
  try{if(!await inspect(original,expected)&&retry){const latest=await json('/setup/understanding/'+encodeURIComponent(original.record_id));current(expected);if(editRequestState(latest,original)!=='retryable')throw Error('原操作当前不能重试，原请求保留，不覆盖后来的修改。');await post(original,expected);}}
  catch(e){setConflict(null);setError(e.message);}finally{if(expected===scope.current)setBusy(false);}
 }
 async function releaseConflict(){
  if(!canEdit||busy||!pending||!conflict)return;const original=pending,expected=key;setBusy(true);setError('');
  try{
   const remote=await json('/setup/understanding/'+encodeURIComponent(original.record_id));current(expected);
   if(editReceipt(remote,original)){await acknowledge(remote,original,expected);return;}
   const selection=await withDraftCacheLock(expected,()=>{current(expected);return archiveFencedEditPending(localStorage,expected,original,remote,account);},locks);
   current(expected);onRecord(remote);setSelected(selection.operation_ids);setPending(null);setConflict(null);setRetryable(false);
   setNotice('原请求已完整保留。已读取服务器当前保存的选择，请重新逐项核对；没有自动提交修改或调用云模型。');
  }catch(e){setConflict(null);setError(e.message);try{const active=readEditPending(localStorage,expected);current(expected);setPending(active);}catch{}}
  finally{if(expected===scope.current)setBusy(false);}
 }
 function toggle(id){if(busy||pending||!canEdit)return;const choice=selected.includes(id)?selected.filter(value=>value!==id):[...selected,id];setSelected(choice);begin('review',choice);}
 let problems=[];if(!invalid)problems=editSelectionProblems(record,selected);
 const names=new Map(rows.map(row=>[row.id,row.target_label+' · '+fieldLabel(row)]));
 return <section aria-label="逐项修改审核">
  <h3>只采用需要修改的字段</h3><p>逐项核对原值、新值和依据。未选修改不会采用，关联项不会自动勾选；角色建议不会授予账号或设备权限。</p>
  <p>模型提出的是待核对建议；原文引用存在不代表修改含义正确，没有建议也不代表已全面核实。请特别核对未要求的步骤、分支和能力影响。</p>
  <p>每条建议的引用保存在本次审核记录；如需修改流程中的“资料依据”，请同时核对并选择相应的依据修改。</p>
  {(invalid||error)&&<p role="alert" className="error">{invalid||error}</p>}{notice&&<p role="status">{notice}</p>}
  {rows.map(row=><article className="understanding-business" key={row.id}>
   <h4>{record.status==='review'&&<input type="checkbox" aria-label={'采用修改：'+row.target_label+' · '+fieldLabel(row)} checked={selected.includes(row.id)} disabled={!canEdit||busy||!!pending||!!invalid||(row.blocked_reasons.length>0&&!selected.includes(row.id))} onChange={()=>toggle(row.id)}/>} {row.target_label} · {fieldLabel(row)}</h4>
   <div className="understanding-comparison"><div><h5>原值</h5><EditValue value={row.before} Citation={Citation}/></div><div><h5>建议值</h5><EditValue value={row.after} Citation={Citation}/></div></div>
   {row.effects.length>0&&<section><strong>影响与能力核对</strong><ul>{row.effects.map((text,i)=><li key={i}>{text}</li>)}</ul></section>}
   {row.dependencies.length>0&&<p>需一并核对：{row.dependencies.map(id=>names.get(id)||'另一项关联修改').join('；')}。请分别勾选。</p>}
   {row.blocked_reasons.map((text,i)=><p className="error" role="status" key={i}>暂不能采用：{text}</p>)}
   {row.citations.map((citation,i)=><Citation key={i} citation={citation}/>)}<Explanation text={row.inference_reason} hasCitation={row.citations.length>0}/>
  </article>)}
  {!invalid&&!rows.length&&<p>本次没有可采用的字段修改，当前配置保持。</p>}
  {problems.length>0&&<ul role="status">{problems.map((text,i)=><li key={i}>{text}</li>)}</ul>}
  {Array.isArray(record.result?.questions)&&record.result.questions.length>0&&<section><h4>还需要明确的内容</h4><ul>{record.result.questions.map((question,i)=><li key={i}>{question}</li>)}</ul></section>}
  {pending&&<section aria-label="原字段操作恢复"><p>原{({review:'审核选择',apply:'应用',undo:'撤销'})[pending.action]}请求已保存，结果核对前不会另起操作。返回配置或刷新后，可在本记录继续核对。</p><Btn disabled={busy} onClick={()=>recover()}>核对原操作结果</Btn><Btn disabled={busy||!canEdit||!retryable} onClick={()=>recover(true)}>核对后重试原操作</Btn>{conflict&&<><p>服务器新版本已阻止原请求执行，且当前未查到原回执。可完整保留原请求，再读取当前保存的选择重新核对；不会自动重基线、应用或调用模型。</p><Btn disabled={busy||!canEdit} onClick={releaseConflict}>保留原请求并重新核对选择</Btn></>}</section>}
  {record.status==='review'&&<Btn primary disabled={!canEdit||busy||!!pending||!!invalid||!selected.length||problems.length>0} onClick={()=>begin('apply')}>{busy?'正在核对保存…':'应用选中的字段修改'}</Btn>}
  {record.status==='applied'&&(record.edit_noop?<p role="status">本次内容相同，没有实际修改；未因本次操作更改配置版本或流程确认，不需要撤销。</p>:<Btn disabled={!canEdit||busy||!!pending||!!invalid} onClick={()=>begin('undo')}>撤销本次字段修改</Btn>)}
 </section>;
}

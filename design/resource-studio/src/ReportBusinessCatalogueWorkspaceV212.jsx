import React,{useCallback,useEffect,useRef,useState} from 'react';
import ReportBusinessCatalogueGenerationV211 from './ReportBusinessCatalogueGenerationV211';
import ReportBusinessCatalogueV211 from './ReportBusinessCatalogueV211';
import {checkedCurrentBindingV260,checkedCurrentWorkingV260,currentCatalogueScopeV260} from './catalogue-current-context-v260-protocol.mjs';

// Generation and human review keep separate persisted records. Opening an
// answer never confirms it, adopts configuration, or starts an execution task.
export default function ReportBusinessCatalogueWorkspaceV212({account,producer,models,request,Btn,storage,tabStorage,locks,prepareDraft,currentAvailable=true,onRegisterGuard,onConfirmed,onFlowSource}){
 const scope=[account?.user_id,account?.workspace_id,account?.role,producer?.id].join(':');
 const [selection,setSelection]=useState(null),[error,setError]=useState(''),[confirmation,setConfirmation]=useState(null),[managedSelection,setManagedSelection]=useState(null);
 const consumer=selection?.scope===scope?selection.row:null,confirmed=confirmation?.scope===scope?confirmation:null,managed=managedSelection?.scope===scope&&managedSelection?.consumer_id===consumer?.id?managedSelection.binding:null;
 const generationGuard=useRef(null),generationRefresh=useRef(null),editorGuard=useRef(null),latest=useRef(null),selectionEpoch=useRef(0);
 latest.current={scope,account,producer,consumer,onRegisterGuard,onConfirmed,onFlowSource,currentAvailable};
 const guard=useCallback(options=>{
  try{return (!generationGuard.current||generationGuard.current(options))&&(!editorGuard.current||editorGuard.current(options));}
  catch{return false;}
 },[]);
 useEffect(()=>{onRegisterGuard?.(guard);return()=>onRegisterGuard?.(null,guard);},[guard,onRegisterGuard]);
 const registerGeneration=useCallback((fn,previous)=>{if(fn)generationGuard.current=fn;else if(!previous||generationGuard.current===previous)generationGuard.current=null;},[]);
 const registerGenerationRefresh=useCallback((fn,previous)=>{if(fn)generationRefresh.current=fn;else if(!previous||generationRefresh.current===previous)generationRefresh.current=null;},[]);
 const activitySettled=useCallback(async()=>{const expected=scope;if(latest.current.scope!==expected)return false;const refresh=generationRefresh.current;if(!refresh)return false;try{const ok=await refresh();return latest.current.scope===expected&&ok!==false;}catch{return false;}},[scope]);
 const registerEditor=useCallback((fn,previous)=>{if(fn)editorGuard.current=fn;else if(!previous||editorGuard.current===previous)editorGuard.current=null;},[]);
 const generationAvailable=useCallback(()=>{
  const value=latest.current.currentAvailable;
  try{return (typeof value==='function'?value():value!==false)&&(!editorGuard.current||editorGuard.current({write:true}));}
  catch{return false;}
 },[]);
 const editorAvailable=useCallback(()=>{
  const value=latest.current.currentAvailable;
  try{return (typeof value==='function'?value():value!==false)&&(!generationGuard.current||generationGuard.current({write:true}));}
  catch{return false;}
 },[]);
 function select(row){
  if(latest.current.scope!==scope)return false;
  if(editorGuard.current&&!editorGuard.current()){setError('当前目录输入或原申请尚未可靠保护，请保留原稿并先重试保存。');return false;}
  if(row?.producer_id!==producer?.id||row?.generation_profile!=='catalogue-v211'||!Number.isSafeInteger(row?.revision)||row.revision<1){
   setError('目录与原报告或原活动版本无法核对，当前人工稿保留。');return false;
  }
  const partial=row.origin_mode==='failed_catalogue_partial_v212';
  if(partial?!(row.schema==='report-business-catalogue-partial-view-v212'&&row.status==='failed'&&row.call_status==='failed'&&row.catalogue_partial?.schema==='report-business-catalogue-partial-projection-v212'&&row.catalogue===null&&row.result===null&&row.manual_value===null):!(['review','needs_clarification'].includes(row.status)&&row.catalogue?.schema==='report-business-catalogue-projection-v211')){
   setError('完整建议或部分建议的来源类型无法核对，原人工稿保持不变。');return false;
  }
  selectionEpoch.current++;setError('');setConfirmation(null);setManagedSelection(null);setSelection({scope,row});return true;
 }
 async function openCurrentWorking(binding,working,checkEntry){const source=consumer,epoch=selectionEpoch.current;if(!source||latest.current.scope!==scope||latest.current.consumer?.id!==source.id)return false;try{if(!guard()||checkEntry&&checkEntry()===false)throw Error('原目录或副本申请尚未可靠保护，请先核对原记录。');const checked=checkedCurrentBindingV260(binding,currentCatalogueScopeV260(account,binding.source_draft_ref.id));if(checked.source_pins.producer_id!==producer.id||checked.source_pins.consumer_id!==source.id||checked.source_pins.source_revision!==source.revision)throw Error('副本不属于这份真实原报告活动，旧目录保留。');await checkedCurrentWorkingV260(working,checked,{workspace_id:account.workspace_id,actor_id:account.user_id,producer_id:producer.id,consumer_id:source.id,projection_hash:source.origin_mode==='failed_catalogue_partial_v212'?source.catalogue_partial.projection_hash:source.catalogue.projection_hash,revision:source.revision,...(source.origin_mode==='failed_catalogue_partial_v212'?{origin_mode:source.origin_mode}:{})});if(latest.current.scope!==scope||latest.current.consumer?.id!==source.id||selectionEpoch.current!==epoch||checkEntry&&checkEntry()===false)return false;if(!guard())throw Error('核对期间有新的人稿或申请，未切换目录。');selectionEpoch.current++;setManagedSelection({scope,consumer_id:source.id,binding:checked});setError('');return true;}catch(e){if(latest.current.scope===scope)setError(e.message);return false;}}
 function exitCurrentWorking(value,checkEntry){if(latest.current.scope!==scope)return false;try{if(!managed||!value?.source||value.binding!==null||value.from_binding?.id!==managed.id||value.from_binding?.record_hash!==managed.record_hash||value.source.id!==managed.source_draft_ref.id||value.source.id[14]==='5'||value.source.actor_id!==account.user_id||value.source.workspace_id!==account.workspace_id||value.source.origin.consumer_id!==consumer?.id)throw Error('来源目录尚未通过当前任务范围核对，当前副本保留。');if(!guard()||checkEntry&&checkEntry()===false)throw Error('工作副本人稿或原申请尚未可靠保护，请先核对。');selectionEpoch.current++;setManagedSelection(null);setError('');return true;}catch(error){if(latest.current.scope===scope)setError(error.message);return false;}}
 useEffect(()=>{selectionEpoch.current++;setSelection(null);setConfirmation(null);setManagedSelection(null);setError('');},[scope]);
 return <section aria-label="业务清单整理与核对">
  {error&&<p role="alert">{error}</p>}
  <ReportBusinessCatalogueGenerationV211 {...{account,producer,models,request,Btn,storage,locks,prepareDraft}} currentAvailable={generationAvailable} onRegisterGuard={registerGeneration} onRegisterRefresh={registerGenerationRefresh} onConsumer={select}/>
  {consumer&&<ReportBusinessCatalogueV211 key={[scope,consumer.id,managed?.id||'original',managed?.current_working_draft_ref.id||''].join(':')} producerId={producer.id} revision={consumer.revision} account={account} consumer={consumer} managedBinding={managed} sourceTitle={producer.title||producer.name||''} onOpenCurrentWorking={openCurrentWorking} onExitCurrentWorking={exitCurrentWorking} originMode={consumer.origin_mode==='failed_catalogue_partial_v212'?consumer.origin_mode:undefined} expectedProjectionHash={consumer.origin_mode==='failed_catalogue_partial_v212'?consumer.catalogue_partial.projection_hash:undefined} api={request} {...{storage,tabStorage,locks,models}} currentAvailable={editorAvailable} onRegisterGuard={registerEditor} onActivitySettled={activitySettled} onFlowSource={onFlowSource} onConfirmed={async(plan,detail)=>{if(latest.current.scope!==scope)return;setConfirmation({scope,plan,detail});await latest.current.onConfirmed?.(plan,detail);}}/>}
  {confirmed&&<p role="status">业务范围已保存并确认。可从已确认目录选择业务进入流程规划；尚未启动业务任务。</p>}
 </section>;
}

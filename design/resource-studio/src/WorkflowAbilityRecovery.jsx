import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {abilityTaskScope,readTaskAbilities,writeAbility,checkedAbilityReceipt,sameAbilityVisit} from './workflow-ability-contract.mjs';
export default function WorkflowAbilityRecovery({task,account,Btn,accessBlocked=false,onPermissionIssue}){
 const taskScope=abilityTaskScope(account,task.id),scopeKey=JSON.stringify(taskScope),visit=task.workflow_runtime.receipts.length;
 const [records,setRecords]=useState([]),[probes,setProbes]=useState({}),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[denied,setDenied]=useState(false);
 const life=useRef(0),writing=useRef(false),renderScope=useRef(scopeKey);renderScope.current=scopeKey;const active=epoch=>epoch===life.current&&renderScope.current===scopeKey;
 function load(){setRecords(readTaskAbilities(localStorage,taskScope));}
 useEffect(()=>{life.current++;writing.current=false;setBusy(false);setRecords([]);setProbes({});setError('');setNotice('');setDenied(false);try{load();}catch(e){setError(e.message);}return()=>{life.current++;};},[scopeKey]);
 useEffect(()=>{try{load();}catch(e){setError(e.message);}},[scopeKey,task.version,task.workflow_runtime.current_step_id,visit]);
 useEffect(()=>{setDenied(false);},[task.permissions]);
 const currentStep=task.business?.flow.steps.find(row=>row.id===task.workflow_runtime.current_step_id),currentAutomatic=currentStep&&currentStep.agent!=='人工确认'&&!['workflow_cancelled','workflow_completed','workflow_pending'].includes(task.status);
 const visible=records.filter(item=>!currentAutomatic||item.scope.step_id!==task.workflow_runtime.current_step_id||item.scope.visit!==visit),canProbe=!accessBlocked&&!denied&&task.permissions?.can_probe_ability===true;
 async function recover(item,archive=false){if(writing.current||busy||!canProbe||!item.journal.pending)return;const epoch=life.current;writing.current=true;setBusy(true);setError('');try{
  const pending=item.journal.pending,receipt=await json('/workspace/tasks/'+encodeURIComponent(task.id)+'/ability-requests/'+pending.body.request_id);await checkedAbilityReceipt(receipt,pending,item.scope);if(!active(epoch))return;
  if(receipt.saved){writeAbility(localStorage,item.scope,{...item.journal,pending:null,history:[...item.journal.history,{body:pending.body,status:'saved',minimum_version:receipt.task_version,request_hash:receipt.request_hash}]},item.journal);load();setNotice('本账号旧步骤原请求已核实保存；执行结果仍以原步骤处理回执为准。');return;}
  if(archive){if(receipt.task_version<=pending.body.version||sameAbilityVisit(receipt,item.scope)&&receipt.task_status!=='workflow_cancelled')throw Error('旧请求尚未取得版本失效依据，请继续保留原编号核对');
   writeAbility(localStorage,item.scope,{...item.journal,pending:null,history:[...item.journal.history,{body:pending.body,status:'version_superseded',minimum_version:receipt.task_version,proof:receipt}]},item.journal);load();setNotice('旧请求内容和核对依据已保留在本机；送达状态仍未证实，旧版本已失效。不会在当前步骤重发。');
  }else{setProbes(value=>({...value,[JSON.stringify(item.scope)]:receipt}));setNotice('未查到本账号旧请求的保存记录。任务进入后续步骤不代表此请求已保存，原内容继续保留。');}
 }catch(e){if(active(epoch)){if([401,403].includes(e.status)){setDenied(true);onPermissionIssue?.(e);}setError(e.message+' 旧请求内容和原编号保留。');}}finally{if(active(epoch)){writing.current=false;setBusy(false);}}}
 if(!visible.length&&!error&&!notice)return null;
 return <section className="media-form" aria-label="旧自动步骤请求恢复"><h4>旧步骤请求核对</h4><p>任务已经进入后续状态时，原未核实请求仍单独保留；这里只核对旧编号，不会重发旧步骤。</p>{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{visible.map((item,index)=>{const probe=probes[JSON.stringify(item.scope)],name=task.business?.flow.steps.find(row=>row.id===item.scope.step_id)?.name||'原能力步骤';return <article key={JSON.stringify(item.scope)}><strong>{name} · 原步骤记录</strong>{item.journal.pending&&<><p>原请求送达结果仍待核对，不能用当前任务状态代替本账号原回执。</p><Btn disabled={busy||!canProbe} onClick={()=>recover(item)}>核对旧请求 {index+1}</Btn>{probe&&!probe.saved&&probe.task_version>item.journal.pending.body.version&&(!sameAbilityVisit(probe,item.scope)||probe.task_status==='workflow_cancelled')&&<Btn disabled={busy||!canProbe} onClick={()=>recover(item,true)}>保留旧请求及版本核对依据 {index+1}</Btn>}</>}{item.journal.history.length>0&&<details><summary>已保留本机请求历史（{item.journal.history.length}）</summary>{item.journal.history.map((row,i)=><p key={i}>{row.status==='saved'?'本账号原请求保存已核实':'旧版本已失效，送达状态仍未证实'}。原执行内容、{row.body.retry?'明确重试选择':'原步骤选择'}和核对记录保留。</p>)}</details>}</article>;})}</section>;
}

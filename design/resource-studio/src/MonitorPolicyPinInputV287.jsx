import React,{useEffect,useRef,useState} from 'react';
import {monitorPolicyScopeKey,monitorPolicyDraftValue,monitorPolicyDraft,loadMonitorPolicyOptions,
 checkedMonitorPolicySelection,monitorPolicyOptionLabel,monitorPolicyHumanText} from './monitor-policy-pin-v287.mjs';

export default function MonitorPolicyPinInputV287({field,value,scope,selectedObjects,objectsReady=true,requestRole,requestJSON,disabled=false,onChange,onOptions}){
 const [read,setRead]=useState(null),[retry,setRetry]=useState(0),latest=useRef(null),api=useRef(requestJSON),report=useRef(onOptions);
 api.current=requestJSON;report.current=onOptions;
 let stamp=null,scopeIssue='';try{if(!objectsReady)throw Error('请先选择并核对本次任务对象的当前版本；原策略选择仍保留。');stamp=monitorPolicyScopeKey(scope,selectedObjects,requestRole);}catch(error){scopeIssue=error.message;}
 latest.current=stamp;
 useEffect(()=>{
  if(!stamp){setRead(null);report.current?.({scope_key:null,value:null});return;}
  const controller=new AbortController(),current=key=>latest.current===key&&!controller.signal.aborted;
  setRead({scope_key:stamp,value:null,error:''});report.current?.({scope_key:stamp,value:null});
  loadMonitorPolicyOptions(api.current,scope,selectedObjects,requestRole,{signal:controller.signal,isCurrent:current})
   .then(value=>{if(current(stamp)){const record={scope_key:stamp,value,error:''};setRead(record);report.current?.(record);}})
   .catch(()=>{if(current(stamp)){const record={scope_key:stamp,value:null,error:'可用观测策略暂未完整读到。原选择保留，请重试读取，或明确选择不自动补充观测。'};setRead(record);report.current?.(record);}});
  return ()=>{controller.abort();if(latest.current===stamp)report.current?.({scope_key:stamp,value:null});};
 },[stamp,retry]);
 const current=read?.scope_key===stamp?read:null,options=current?.value?.options||[];
 let pin,selectionIssue='';try{pin=monitorPolicyDraftValue(value,{nullable:field.nullable});}catch(error){selectionIssue=error.message;}
 const index=pin?options.findIndex(row=>row.policy_id===pin.policy_id&&row.approved_hash===pin.approved_hash):-1;
 if(pin&&!selectionIssue)try{checkedMonitorPolicySelection(value,field,scope,selectedObjects,requestRole,current);}catch(error){selectionIssue=error.message;}
 const selected=value.mode==='null'?'none':value.mode==='value'?(index>=0?'option:'+index:'retained'):'';
 function choose(choice){
  if(choice==='none'){onChange(monitorPolicyDraft(null));return;}
  if(choice===''){onChange({mode:'unset',raw:''});return;}
  const match=/^option:(\d+)$/.exec(choice),row=match&&options[Number(match[1])];if(!row)return;
  const next=monitorPolicyDraft({policy_id:row.policy_id,approved_hash:row.approved_hash});
  try{checkedMonitorPolicySelection(next,field,scope,selectedObjects,requestRole,current);onChange(next);}
  catch{const record={scope_key:stamp,value:null,error:'策略条件已变化；原选择保留，请重新读取后明确选择。'};setRead(record);report.current?.(record);}
 }
 return <article className="media-form"><label>{field.label}{field.required?'（请选择）':''}
  <select aria-label={field.label} value={selected} disabled={disabled||!stamp} onChange={event=>choose(event.target.value)}>
   <option value="">请明确选择</option>{field.nullable&&<option value="none">不自动补充观测</option>}
   {value.mode==='value'&&index<0&&<option value="retained" disabled>原选择待重新核对（已保留）</option>}
   {options.map((row,index)=><option key={row.policy_id} value={'option:'+index} disabled={Date.parse(row.valid_until)<=Date.now()}>{monitorPolicyOptionLabel(row)}</option>)}
  </select></label>
  <p>选择已批准策略仅固定本次任务的观测范围；实际采集仍须核对流程、授权、预算和独立收件。</p>
  {scopeIssue?<p role="status">{scopeIssue}</p>:!current?.value&&!current?.error?<p role="status">正在读取本次对象的可用策略；原选择保留。</p>:null}
  {current?.error&&<p role="alert">{current.error}</p>}
  {current?.value&&!options.length&&<p role="status">本次对象暂无可选择的已批准策略。不会自动选择或清空原值。</p>}
  {selectionIssue&&<p role="alert">{selectionIssue}</p>}
  {value.mode==='null'&&<p role="status">已明确选择不自动补充观测；本任务会保留这个空值。</p>}
  {index>=0&&<small>{monitorPolicyHumanText(options[index].scope_label)} · 有效至 {monitorPolicyHumanText(options[index].valid_until)}</small>}
  <button type="button" disabled={disabled||!stamp} onClick={()=>setRetry(value=>value+1)}>重新读取可用策略</button>
 </article>;
}

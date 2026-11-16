import React,{useEffect,useId,useRef,useState} from 'react';
import {checkedBusinessDataHistoryV204,businessDataHistoryPathV204} from './business-data-history-v204.mjs';
const labels={info:'处理记录',succeeded:'已完成',failed:'未完成',request:'已提交请求'};
const when=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
export default function BusinessDataHistoryV204({controller,subjectType,subjectID,sourceID,Btn,disabled=false}){
 const owner=controller.key+':'+controller.generation+':'+sourceID+':'+subjectType+':'+subjectID,liveOwner=useRef(owner);liveOwner.current=owner;
 const region=useId(),alive=useRef(false),sequence=useRef(0),abort=useRef(null);
 const blank=()=>({owner,open:false,entries:null,next:null,continuation:null,error:'',retryCursor:null,loading:false});
 const [state,setState]=useState(blank),current=state.owner===owner?state:blank();
 const title=subjectType==='read'?'原资料处理记录':'导入批次处理记录';
 function subjectMatches(){return owner===controller.key+':'+controller.generation+':'+sourceID+':'+subjectType+':'+subjectID&&controller.draft.selected===sourceID&&(subjectType==='read'?controller.entry().read_id===subjectID:controller.entry().import_id===subjectID);}
 function matches(){return alive.current&&liveOwner.current===owner&&controller.active&&!controller.denied&&subjectMatches();}
 useEffect(()=>{alive.current=true;sequence.current++;abort.current?.abort();setState(blank());return()=>{alive.current=false;sequence.current++;abort.current?.abort();};},[owner]);
 async function load(cursor=null){
  if(!matches())return;let token;
  try{controller.check();abort.current?.abort();const signal=new AbortController();abort.current=signal;token=++sequence.current;setState(old=>({...old,owner,open:true,loading:true,error:''}));
   const value=checkedBusinessDataHistoryV204(await controller.request(businessDataHistoryPathV204(subjectType,subjectID,cursor),{signal:signal.signal}),subjectType,subjectID);
   if(!matches()||token!==sequence.current||signal.signal.aborted)return;
   if(cursor!==null&&value.next_cursor===cursor)throw Error('较早记录的分页位置未前进，已保留当前记录，请刷新后重试。');
   setState(old=>{if(old.owner!==owner)return old;const existing=cursor===null?[]:old.entries||[],seen=new Set(existing.map(row=>row.id));return {...old,entries:[...existing,...value.entries.filter(row=>!seen.has(row.id))],next:value.next_cursor,continuation:value.continuation,loading:false,error:'',retryCursor:null};});
  }catch(error){if(matches()&&token===sequence.current)setState(old=>old.owner===owner?{...old,loading:false,error:error.message,retryCursor:cursor}:old);}
 }
 function toggle(){if(current.open){sequence.current++;abort.current?.abort();setState({...current,open:false,loading:false});}else load();}
 if(controller.denied||!controller.active||!subjectMatches()||sessionStorage.getItem('ga-setup-csrf-user')!==controller.scope.actor_id||sessionStorage.getItem('ga-setup-csrf-workspace')!==controller.scope.workspace_id)return null;
 return <section className="business-data-history" aria-label={title}>
 <Btn disabled={!current.open&&disabled} aria-expanded={current.open} aria-controls={region} onClick={toggle}>{current.open?'收起处理记录':'查看处理记录'}</Btn>
 {current.open&&<div id={region}><h4>{title}</h4><p>只查看已保存的处理经过，不重新读取来源或发起导入。</p>
 <Btn disabled={current.loading||disabled} onClick={()=>load()}>刷新处理记录</Btn>
 {current.loading&&<p role="status">正在读取处理记录…</p>}
 {current.error&&<p role="alert">{current.error}<Btn disabled={current.loading||disabled} onClick={()=>load(current.retryCursor)}>重试读取处理记录</Btn></p>}
 {current.continuation&&<p>{current.continuation.count===null?'后台继续处理次数暂不能核实':'后台继续处理次数：'+current.continuation.count}{current.continuation.notice?'。'+current.continuation.notice:''}</p>}
 {current.entries?.length===0&&!current.loading&&<p>尚无已保存的处理记录。</p>}
 {current.entries?.map(entry=><article className="media-form" key={entry.id}><h5>{entry.label} · {labels[entry.status]}</h5><p><time dateTime={entry.at}>{when(entry.at)}</time> · {entry.actor||'当时操作人未记录'}</p>{entry.detail&&<p role={entry.status==='failed'?'note':undefined}>{entry.detail}</p>}</article>)}
 {current.next&&<Btn disabled={current.loading||disabled} onClick={()=>load(current.next)}>加载更早记录</Btn>}
 </div>}
 </section>;
}

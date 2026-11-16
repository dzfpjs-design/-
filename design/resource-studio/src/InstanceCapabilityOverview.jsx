import React,{useEffect,useId,useState} from 'react';
import {json} from './runtime-api';
import {releasedResources,enabledWorkflowSteps} from './instance-capability-overview.mjs';

// Read-only: all visible resources come from this instance's active release.
export default function InstanceCapabilityOverview({instanceId,instance,release,account,Btn,request=json,onObservation,onDevice,onDocuments,onTasks}){
 const [open,setOpen]=useState(false),[refresh,setRefresh]=useState(0),[actionError,setActionError]=useState(''),[state,setState]=useState({loading:false,formal:null,publicSources:null,errors:[]});
 const panelId=useId();
 const active=instance?.id===instanceId&&release?.id&&release.instance_id===instanceId&&instance.active_release_id===release.id&&['active','partially_active'].includes(instance.status);
 useEffect(()=>{
  if(!open||!active){setState({loading:false,formal:null,publicSources:null,errors:[]});return;}
  const controller=new AbortController();let current=true;
  setState({loading:true,formal:null,publicSources:null,errors:[]});
  const id=encodeURIComponent(instanceId);
  Promise.allSettled([
   request('/instance-observations/'+id,{signal:controller.signal}),
   request('/public-observations?instance_id='+id,{signal:controller.signal})
  ]).then(([formal,publicData])=>{
   if(!current)return;
   const errors=[];let formalRows=null,publicRows=null;
   if(formal.status==='fulfilled'&&formal.value?.instance_id===instanceId&&Array.isArray(formal.value.sources))formalRows=formal.value.sources;
   else errors.push('摄像头和 WMS 来源状态暂未读到');
   if(publicData.status==='fulfilled'&&publicData.value?.instance_id===instanceId&&Array.isArray(publicData.value.sources))publicRows=publicData.value.sources;
   else errors.push('公开资料来源状态暂未读到');
   setState({loading:false,formal:formalRows,publicSources:publicRows,errors});
  });
  return()=>{current=false;controller.abort();};
 },[open,active,instanceId,release?.id,account?.user_id,account?.workspace_id,account?.role,request,refresh]);
 const resources=active?releasedResources(instance,release,state.formal,state.publicSources):null;
 const steps=active?enabledWorkflowSteps(instance,release):[];
 function openTarget(handler){
  try{if(typeof handler!=='function'||handler()===false){setActionError('对应入口暂不可用，请刷新工作区后重试。');return;}setActionError('');}
  catch{setActionError('对应入口暂不可用，请刷新工作区后重试。');}
 }
 return <section className="local-model-card" aria-label="当前实例能力与观测">
  <div className="section-title"><div><h2>当前实例能力与观测</h2><p>查看本实例发布版本配置了哪些资源，以及从哪里查看接收到的资料。</p></div><Btn aria-expanded={open} aria-controls={panelId} onClick={()=>setOpen(value=>!value)}>{open?'收起能力与观测':'查看能力与观测'}</Btn></div>
  <div id={panelId} hidden={!open}>{open&&<>
   {!active?<p role="status">当前实例的启用版本尚未读到或已暂停；请刷新工作区后核对。</p>:<>
    <p>以下来自当前发布版本；列入配置不等于设备在线、模型通过领域评估或允许自动执行。</p>
    {state.loading&&<p role="status">正在核对本实例的观测来源…</p>}
    {!!state.errors.length&&<p role="alert">{state.errors.join('；')}。<Btn onClick={()=>setRefresh(value=>value+1)}>重试核对</Btn></p>}
    <h3>本版本配置的资源</h3>
    {!resources?<p role="alert">发布版本的资源清单无法读取，请刷新工作区。</p>:!resources.length?<p>此版本未配置资源。可以回到初始化配置页面接入。</p>:<ul>{resources.map((r,index)=><li key={r.key+':'+index}><strong>{r.name}</strong> · {r.category} · {r.state} · {r.association}</li>)}</ul>}
    <h3>流程中的能力步骤</h3>
    {!steps.length?<p>已启用业务中没有读到能力步骤；人工步骤仍可在业务任务中处理。</p>:<ul>{steps.map(s=><li key={s.key}>{s.business} · {s.name} <span>（能否执行以任务检查结果为准）</span></li>)}</ul>}
   </>}
   {actionError&&<p role="alert">{actionError}</p>}
   <div className="actions"><Btn onClick={()=>openTarget(onObservation)}>查看当日视频、音频与影像</Btn><Btn onClick={()=>openTarget(onDevice)}>查看设备数据与每日整理</Btn><Btn onClick={()=>openTarget(onDocuments)}>管理运行期资料</Btn><Btn disabled={!active} onClick={()=>openTarget(onTasks)}>查看业务任务</Btn></div>
  </>}</div>
 </section>;
}

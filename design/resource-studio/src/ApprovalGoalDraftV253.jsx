import React,{useEffect,useRef,useState} from 'react';
import TaskGoalFields from './TaskGoalFields.jsx';
import {typedRequestJSON} from './typed-numbers-v132.mjs';
import {approvalGoalMetadataV253,ownedApprovalGoalV253,approvalGoalErrorV253} from './approval-goal-v253.mjs';
export default function ApprovalGoalDraftV253({task,account,approval,draft,controller,disabled=false,localError='',onAction,onRegisterGuard,onRegisterValidation}){
 const latest=useRef(),safe=useRef(true),epoch=useRef(0),[stored,setStored]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const owner=typedRequestJSON([account.user_id,account.workspace_id,account.role,task.id,approval.task_version]);latest.current={owner,draft,onAction,onRegisterGuard,onRegisterValidation,disabled};const info=stored?.owner===owner?stored.info:null;
 let editor=null,editorIssue='';if(draft?.goal_editor_v253)try{editor=ownedApprovalGoalV253(draft,task.id);}catch(e){editorIssue=e.message;}
 useEffect(()=>{safe.current=true;const check=()=>latest.current.owner===owner&&safe.current&&!localError;latest.current.onRegisterGuard?.(check);return()=>latest.current.onRegisterGuard?.(null,check);},[owner,localError]);
 useEffect(()=>{setLoading(false);setError('');},[owner]);
 async function read(){const ticket=++epoch.current,stamp=owner;setLoading(true);setError('');try{const reply=await controller.read('/workspace/tasks/'+task.id+'/goal');if(latest.current.owner!==stamp||ticket!==epoch.current)return null;const found=approvalGoalMetadataV253(reply,task,approval);setStored({owner:stamp,info:found});return found;}catch(e){if(latest.current.owner===stamp&&ticket===epoch.current)setError(e.message);return null;}finally{if(latest.current.owner===stamp&&ticket===epoch.current)setLoading(false);}}
 useEffect(()=>{if(editor)read();return()=>{epoch.current++;};},[owner,!!editor]);
 function act(kind,extra={}){setError('');try{if(latest.current.owner!==owner||latest.current.disabled)throw Error('当前审批范围或操作状态已变化，原补证稿保留。');const ok=latest.current.onAction({kind,expectedEditor:editor,info,...extra});safe.current=ok===true;if(!safe.current)setError('本次补证输入尚未完整保存在本机，仍可继续修改纠正或重试保存。');}catch(e){setError(e.message);}}
 async function begin(){const found=info||await read();if(!found)return;if(latest.current.owner!==owner)return;act('begin',{info:found});}
 const activeIssue=editor&&info&&editor.metadata_pin!==info.pin?'原任务字段或单位目录已变化，原目标与输入保留。当前不能用新目录覆盖；请核对保留稿与原任务配置。':'',fieldError=approvalGoalErrorV253(draft),hasGoal=draft?.value.goal_spec!==null;
 const validation=editorIssue||activeIssue||(editor&&!info?'原任务字段目录尚未完整核对，补证原稿保留，请先重新读取。':'');
 useEffect(()=>{const check=()=>validation;latest.current.onRegisterValidation?.(check);return()=>latest.current.onRegisterValidation?.(null,check);},[owner,validation]);
 return <section className="media-form" aria-label="明确本次补证要求"><h3>明确本次补证要求</h3><p>在同一异常任务里确定还需要哪些资料、何时算资料齐全。这里只保存审批草稿，不创建新任务、不直接定义运行目标，也不启动设备。</p>
  {[error,editorIssue,activeIssue].filter(Boolean).map((text,index)=><p role="alert" key={index}>{text}</p>)}
  {!editor&&hasGoal?<p>本任务已有完整目标，当前补证表单不能保证无损表达，原目标保持原样。可在原目标内容中核对，不能用默认条件重建或缩减。</p>:!editor?<><p>原目标明确为空，继续保留此设置。只有明确添加后才开始填写补证要求。</p><button type="button" disabled={disabled||loading} onClick={begin}>明确本次补证要求</button></>:<>
   {!editor.open?<><p>本机补证稿已保留并收起；尚未保存到服务器的修改仍需处理。</p><button type="button" disabled={disabled} onClick={()=>act('open')}>继续编辑原补证要求</button></>:<>
    {loading&&<p role="status">正在读取原任务字段和已有工具目录…</p>}
    {info?<><p>关联原任务对象：{info.metadata.entity_ids.map(id=>info.metadata.entity_names?.[id]||task.object_snapshots?.find(row=>row.id===id)?.reference||task.object_snapshots?.find(row=>row.id===id)?.name||'原任务登记对象').join('、')||'尚无对象'}。不在本表单改绑其他对象。</p>
     <TaskGoalFields context="approval" disabled={disabled||!!activeIssue} metadata={info.metadata} form={editor.form} title={draft.value.title} onChange={form=>act('change',{form})}/>
     {fieldError&&<p role="alert">{fieldError} 原输入已保存，尚不能保存修订或提交审批。</p>}
     <p>资料充分、字段标准、原业务流程和最终独立验收分别核对。修改目标后，旧审批结论不继续授权新的目标；仍须按原版本规则办理。</p>
     <details><summary>原业务已有工具与授权边界</summary>{info.tools.error&&<p role="alert">{info.tools.error}</p>}{info.tools.tools.length?info.tools.tools.map(tool=><p key={tool.id}>{tool.name} · {tool.step_names.join('、')||'原业务登记工具'}；本表单不增加调用权限。</p>):<p>当前没有可列出的原业务工具条件，或目录尚未就绪；不据此授予设备权限。</p>}<p>启动后按已批准策略匹配可用来源，执行前再次核对授权。具体来源与设备尚未在本页匹配；本次不新增设备、作业区域或自动化额度。</p></details>
    </>:<p>原任务字段目录尚未完整读取，原补证稿保留，暂不改写目标。</p>}
    <div className="approval-actions"><button type="button" disabled={disabled||loading} onClick={read}>重新读取原任务字段目录</button><button type="button" disabled={disabled} onClick={()=>act('retry_save')}>重试保存本机补证稿</button><button type="button" disabled={disabled} onClick={()=>act('close')}>收起并保留本次补证输入</button><button type="button" disabled={disabled} onClick={()=>act('remove')}>保留原稿副本，移除本次补证目标</button></div>
    {localError&&<p role="alert">本机保存失败时仍可修改补证输入；保存恢复前不会提交服务器或离开。</p>}
   </>}
  </>}
 </section>;
}

import React,{useEffect,useRef,useState} from 'react';
import {checkedHTTPApprovalTaskSource} from './http-signal-approval-v247-protocol.mjs';
import {numericDisplay} from './typed-numbers-v132.mjs';

export default function HTTPApprovalTaskSourceV247({task,controller,account}){
 const [state,setState]=useState(null),[retry,setRetry]=useState(0),latest=useRef();
 const key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,controller.key,task.id,task.instance_id,task.version]);latest.current=key;
 useEffect(()=>{
  let live=true;setState({key,busy:true,value:null,error:''});
  controller.read('/workspace/tasks/'+task.id+'/signal-approval-source')
   .then(value=>{const checked=checkedHTTPApprovalTaskSource(value,task);if(live&&latest.current===key)setState({key,busy:false,value:checked,error:''});})
   .catch(error=>{if(live&&latest.current===key)setState({key,busy:false,value:null,error:error.message});});
  return()=>{live=false;};
 },[controller,key,retry]);
 if(state?.key!==key||state.busy)return <p role="status">正在独立核对是否存在原 HTTP 异常依据…</p>;
 if(state.error)return <aside className="approval-evidence" aria-label="异常来源尚未核对"><h4>原异常依据暂未读回</h4><p role="alert">{state.error} 当前不能把来源状态当作已收件或已核实。</p><button type="button" onClick={()=>setRetry(value=>value+1)}>重新核对原异常依据</button></aside>;
 if(!state.value?.found)return null;
 const source=state.value.source;
 if(!source)return <aside className="approval-evidence" aria-label="原异常依据完整性受阻"><h4>原始数据核对未通过</h4><p role="alert">{state.value.integrity_issue} 原任务与审批记录继续保留；未补造数据或现场结论。</p><button type="button" onClick={()=>setRetry(value=>value+1)}>重新核对原异常依据</button></aside>;
 return <article className="approval-evidence" aria-label="HTTP 异常原依据与独立收件"><h4>本次草稿来自已保存的 HTTP 异常</h4>
  <p>对象：{source.object.name}；来源：{source.source.name}。</p>
  <p><strong>模拟来源，未经现场验证。</strong> 原登记声明时间：{new Date(source.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）；来源时钟尚未认证。</p>
  <table><thead><tr><th>原始数据字段</th><th>接收到的数值</th><th>声明单位</th></tr></thead><tbody>{source.fields.map(field=><tr key={field.key}><td>{field.label}</td><td>{numericDisplay(field.value)}</td><td>{field.unit||'未声明'}</td></tr>)}</tbody></table>
  <p>数据管理智能体已接收，并核对原始数据一致。记录完整性及阈值命中不证明现场异常。</p>
  <p>这是正式审批草稿的来源依据。提交审批、各级批准、明确开始执行及独立验收仍分别办理。</p>
  <button type="button" onClick={()=>setRetry(value=>value+1)}>重新核对原异常依据</button>
 </article>;
}

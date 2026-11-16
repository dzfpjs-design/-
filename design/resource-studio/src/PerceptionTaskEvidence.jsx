import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {EvidenceView} from './VideoStudio';
const labels={person:'人',cat:'猫',dog:'狗',car:'汽车',sofa:'沙发',remote:'遥控器'};
const receptionText={
 pending:'数据管理接收：正在等待接收。',
 received:'数据管理接收：已收到并核对保存依据。',
 blocked:'数据管理接收：接收受阻，原视频和识别结果已保留，请核对接收记录。',
 integrity_problem:'数据管理接收：接收依据未通过核对，原视频和识别结果已保留，请联系管理员核查。',
 not_available:'数据管理接收：尚无可核验的独立接收记录。'
};
export default function PerceptionTaskEvidence({task,account,Btn}){
 const [run,setRun]=useState(null),[error,setError]=useState('');
 const [reception,setReception]=useState(null),[receptionError,setReceptionError]=useState('');
 const receptionSequence=useRef(0);
 const event=task.perception_event;
 const receiptKey=task.id+':'+event.run_id;
 const currentReception=reception?.key===receiptKey?reception.value:null;
 const currentReceptionError=receptionError?.key===receiptKey?receptionError.message:'';
 const alert=run?.alerts?.find(item=>item.id===event.video_alert_id);
 async function load(){const value=await json('/videos/'+event.run_id);if(!value.frames.some(f=>f.id===event.frame_id))throw Error('任务所引用的画面缺失，请恢复原件后再处理。');setRun(value);setError('');}
 async function loadReception(){
  const sequence=++receptionSequence.current,key=receiptKey;setReception(null);setReceptionError('');
  try{
   const value=await json('/workspace/tasks/'+encodeURIComponent(task.id)+'/video-data-reception');
   if(value.schema!=='video-candidate-data-reception-v349'||!Object.hasOwn(receptionText,value.state)||
      value.data_reception_verified!==(value.state==='received'))throw Error('接收状态无法核对，请重试。');
   if(sequence===receptionSequence.current)setReception({key,value});
  }catch(cause){if(sequence===receptionSequence.current)setReceptionError({key,message:cause.message||'接收状态暂时无法读取。'});}
 }
 useEffect(()=>{let active=true;setRun(null);setError('');load().catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[event.run_id,event.frame_id]);
 useEffect(()=>{loadReception();return()=>{receptionSequence.current++;};},[task.id,event.run_id]);
 return <div className="perception-task-evidence"><h3>视频候选线索</h3><p>视频内 {event.from_seconds.toFixed(1)}—{event.to_seconds.toFixed(1)} 秒持续出现“{labels[event.label]||event.label}”候选；本片段轨迹 {event.track_ref}。</p><p>本地模型结果只关联该观察场景，不代表对象身份或已确认异常。来源是否对应现场尚未核实，拍摄时间尚未校准；核查结果不会自动更新当前对象状态或执行设备动作。</p><p role={currentReceptionError?'alert':'status'}>{currentReceptionError?'数据管理接收：暂时无法核对。'+currentReceptionError:currentReception?receptionText[currentReception.state]:'正在核对数据管理接收状态…'} <Btn onClick={loadReception}>刷新接收状态</Btn></p>{run?.name&&<p>视频来源：{run.name}</p>}{alert?.status==='dismissed'&&<p role="alert">此视频线索已被核查排除。原任务和画面保留；新的审批和执行需要重新核对。</p>}{error?<p className="error" role="alert">{error}<Btn onClick={()=>load().catch(e=>setError(e.message))}>重试读取视频依据</Btn></p>:run?<EvidenceView run={run} frame={run.frames.find(f=>f.id===event.frame_id)} workspaceId={account.workspace_id}/>:<p>正在核对保存的视频和画面…</p>}</div>;
}

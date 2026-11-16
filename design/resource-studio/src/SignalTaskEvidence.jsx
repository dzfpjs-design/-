import React,{useEffect,useState} from 'react';
import {json} from './runtime-api';
import {SignalBasis} from './SignalStudio';
export default function SignalTaskEvidence({task,Btn}){
 const [saved,setSaved]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{setSaved(null);setError('');},[task.id]);
 async function load(){setBusy(true);setError('');try{const value=await json('/workspace/instances/'+task.instance_id+'/signals/'+task.perception_signal.signal_id+'/export');setSaved(value);}catch(e){setSaved(null);setError(e.message+' 原说明仍保留，完成流程前须恢复依据。');}finally{setBusy(false);}}
 return <section className="media-form"><h3>多模感知送来的待核查线索</h3><p>此任务保存了发起时的异常和交接说明；完成前会重新核对原依据，不自动更新对象状态。</p>{task.perception_signal.note&&<p>交接说明：{task.perception_signal.note}</p>}<Btn disabled={busy} onClick={load}>核对原异常与登记依据</Btn>{error&&<p role="alert">{error}</p>}{saved&&<SignalBasis basis={saved.basis}/>}</section>;
}

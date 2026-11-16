import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api as binary} from './runtime-api.js';
import {pixelCanonical} from './pixel-change-v249-protocol.mjs';
import {PixelWorkspaceSessionV250} from './pixel-workspace-session-v250.mjs';
import {checkedNativePixelEntry} from './workflow-pixel-results-v250-protocol.mjs';
import {PixelMachineRecordV250} from './TaskPixelResultsV250.jsx';
export default function NativePixelReceiptV250({account,instanceId,record,Btn,onOpenTask,request=json,readBinary=binary,tabStorage=globalThis.sessionStorage}){
 const latest=useRef(),[stored,setStored]=useState(null),[error,setError]=useState('');let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=pixelCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,record,token]);latest.current={owner,onOpenTask};
 const access=useMemo(()=>new PixelWorkspaceSessionV250({account,session:tabStorage,request,readBinary,current:()=>latest.current.owner===owner}),[owner,request,readBinary,tabStorage]);
 useEffect(()=>{let live=true;setError('');setStored(null);(async()=>{await access.identity();if(!record.native_pixel_result_v250)throw Error(record.integrity_error||'本条机器成果原始依据尚未通过核对，暂不显示数值。');const scope={workspace_id:account.workspace_id,instance_id:instanceId},entry=await checkedNativePixelEntry(record.native_pixel_result_v250,scope,{dataOnly:true});if(record.id!==entry.reception.observation_id||record.entity_id!==entry.entity_id||record.data_received!==entry.data_values_available)throw Error('机器成果与原收件记录不匹配，暂不显示计算值。');await access.identity();if(live&&latest.current.owner===owner)setStored({owner,entry});})().catch(e=>{if(live&&latest.current.owner===owner)setError(e.message);});return()=>{live=false;};},[owner,access]);
 const entry=stored?.owner===owner?stored.entry:null;
 async function openTask(id){setError('');try{await access.identity();const check=()=>access.check();check();await latest.current.onOpenTask?.(id,check);check();}catch(e){if(latest.current.owner===owner)setError(e.message);}}
 return <article className="media-form" aria-label="像素机器成果收件记录"><h4>本地两期图像比较 · 机器成果收件</h4><p>按机器成果登记日归集；不是拍摄日，也不是带地图坐标的影像观测。候选不会自动更新对象状态。</p>{error?<p role="alert">{error}</p>:!entry?<p role="status">正在核对这份原始成果与独立收件依据…</p>:<PixelMachineRecordV250 {...{entry,Btn}} dataOnly onOpenTask={onOpenTask?openTask:undefined} readOriginal={(row,side)=>access.bytes('/workspace/tasks/'+row.task_id+'/pixel-results/'+row.id+'/originals/'+side,row.sources[side])} readArtifact={(row,kind)=>access.bytes('/workspace/tasks/'+row.task_id+'/pixel-results/'+row.id+'/artifacts/'+kind,row.result.artifacts.find(value=>value.kind===kind))}/>}</article>;
}

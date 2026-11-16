import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {EvidenceView} from './VideoStudio';
export default function SavedVideoEvidence({runId,expectedSha,entityIds,instanceId,account,Btn}){
 const [open,setOpen]=useState(false),[run,setRun]=useState(null),[frameId,setFrameId]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reloadVersion,setReloadVersion]=useState(0);const epoch=useRef(0);
 const key='ga-saved-video-frame:'+account.workspace_id+':'+account.username+':'+runId;
 async function load(){const token=epoch.current;setBusy(true);setError('');try{const value=await json('/videos/'+runId),binding=value.source_context?.domain_binding;
  if(value.input_sha256!==expectedSha||binding?.instance_id!==instanceId||!entityIds.includes(binding?.entity_id))throw Error('保存视频与本次对象引用不一致，请保留核查并恢复来源。');
  if(token===epoch.current){setRun(value);setReloadVersion(v=>v+1);try{setFrameId(localStorage.getItem(key)||'')}catch{setFrameId('')}}
 }catch(e){if(token===epoch.current)setError(e.message)}finally{if(token===epoch.current)setBusy(false)}}
 useEffect(()=>{epoch.current++;setOpen(false);setRun(null);setError('');return()=>{epoch.current++}},[runId,key]);
 const frame=run?.frames.find(f=>f.id===frameId)||run?.frames[0];
 return <div><Btn disabled={busy} onClick={()=>{if(open)setOpen(false);else{setOpen(true);load()}}}>{open?'收起原视频与保存画面':'查看原视频与保存画面'}</Btn>{open&&<div className="media-form">{error?<p role="alert">{error}<Btn disabled={busy} onClick={load}>重新读取视频依据</Btn></p>:busy?<p>正在核对保存的视频与对象引用…</p>:run&&<><p>原视频已保存；本地处理了 {run.frames.length} 张分析画面。拍摄时间未核实，候选轨迹不代表档案身份，采样间隔不能认定无异常。</p>{run.frames.length>0&&<label>选择保存画面<select aria-label="补充视频保存画面" value={frame?.id||''} onChange={e=>{try{localStorage.setItem(key,e.target.value);setFrameId(e.target.value)}catch{setError('画面选择尚未保存到浏览器；服务器原件仍保留。')}}}>{run.frames.map(f=><option key={f.id} value={f.id}>第 {f.seconds.toFixed(1)} 秒 · {f.tracks.length} 条候选轨迹</option>)}</select></label>}<EvidenceView key={run.id+':'+reloadVersion} run={run} frame={frame} workspaceId={account.workspace_id}/><a href={'/api/videos/'+run.id+'/original?workspace_id='+encodeURIComponent(account.workspace_id)}>下载此次原视频</a></>}</div>}</div>
}

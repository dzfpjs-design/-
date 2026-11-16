import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api} from './runtime-api.js';
import {pixelCanonical} from './pixel-change-v249-protocol.mjs';
import {PixelWorkspaceSessionV250} from './pixel-workspace-session-v250.mjs';
import {checkedNativeSoundRecord} from './task-sound-result-protocol.mjs';
import {SoundRunEvidence} from './WorkflowTaskSound.jsx';
export default function NativeSoundReceipt({account,instanceId,record,Btn,onOpenTask,request=json,readBinary=api,tabStorage=globalThis.sessionStorage}){
 const latest=useRef(),[stored,setStored]=useState(null),[error,setError]=useState('');let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}const owner=pixelCanonical([account.user_id,account.workspace_id,account.role,instanceId,record,token]);latest.current={owner,onOpenTask};
 const access=useMemo(()=>new PixelWorkspaceSessionV250({account,session:tabStorage,request,readBinary,current:()=>latest.current.owner===owner}),[owner,request,readBinary,tabStorage]);
 useEffect(()=>{let live=true;setStored(null);setError('');(async()=>{await access.identity();const value=await checkedNativeSoundRecord(record,{workspace_id:account.workspace_id,instance_id:instanceId});await access.identity();if(live&&latest.current.owner===owner)setStored({owner,value});})().catch(e=>{if(live&&latest.current.owner===owner)setError(e.message);});return()=>{live=false;};},[owner,access]);
 const value=stored?.owner===owner?stored.value:null,entry=value?.native_sound_result_v1;
 async function openTask(){setError('');try{await access.identity();const check=()=>access.check();check();await latest.current.onOpenTask?.(entry.run.task_id,check);check();}catch(e){if(latest.current.owner===owner)setError(e.message);}}
 return <article className="media-form" aria-label="环境声音成果收件记录"><h4>环境声音候选成果 · 独立收件</h4><p>按机器成果登记日整理，不是录制日期。声源身份、对象覆盖和现场事件尚未核实，不自动更新对象状态。</p>{error?<p role="alert">声音原始依据尚未核对，机器分数已隐藏：{error}</p>:!value?<p role="status">正在核对原声音成果与独立收件依据…</p>:!entry?<p role="alert">{value.integrity_error}</p>:<><p>登记日：{value.received_day}（北京时间登记日）。{entry.data_values_available?'已独立接收候选成果，仍需人工核查。':'尚未独立接收，数据面板不展示机器分数。'}</p><SoundRunEvidence {...{account,instanceId,Btn,request,readBinary}} run={entry.run} reception={entry.reception} dataOnly/>{onOpenTask&&<Btn onClick={openTask}>打开原任务核对声音候选</Btn>}{entry.run.reviews.length>0&&<details><summary>人工核查说明（当前读取）</summary>{entry.run.reviews.map(item=><p key={item.request_id}>{({matches:'符合',false_positive:'误报',uncertain:'不确定'})[item.verdict]}：{item.note}。此说明不等于原业务全部办理或正式验收。</p>)}</details>}</>}</article>;
}

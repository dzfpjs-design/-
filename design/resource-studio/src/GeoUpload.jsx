import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {UploadJournal,uploadKey} from './geo-recovery.mjs';
export default function GeoUpload({account,catalog,disabled,Btn,onSaved}){
 const [journal,setJournal]=useState(null),[intent,setIntent]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const input=useRef(),file=useRef(null),control=useRef(null),active=useRef(null),key=uploadKey(account.workspace_id,account.username);
 async function confirmed(j,row){j.confirm(row);file.current=null;if(active.current===j.key){setIntent(null);setNotice('原请求对应的影像已保存，服务器已核对原件；可继续查看空间信息和RGB设置。');try{await onSaved(row);}catch(e){setError('影像已保存，清单暂未更新：'+e.message+' 请刷新影像记录。');}}}
 async function reconcile(j=journal){if(!j?.intent)return;const ctl=new AbortController();control.current=ctl;setBusy(true);setError('');try{await confirmed(j,await json('/geo/uploads/'+encodeURIComponent(j.intent.request_id),{signal:ctl.signal}));}catch(e){if(active.current===j.key){if(e.status===404)setNotice('原请求尚未确认保存。请重新选择原文件，用原请求重试；不会另建上传记录。');else setError(e.message);}}finally{if(control.current===ctl)control.current=null;if(active.current===j.key)setBusy(false);}}
 useEffect(()=>{active.current=key;file.current=null;setError('');setNotice('');try{const j=new UploadJournal(localStorage,key);setJournal(j);setIntent(j.intent);if(j.intent)reconcile(j);}catch(e){setJournal(null);setError(e.message+' 上传入口暂时不可用，原记录保留。');}return()=>{active.current=null;control.current?.abort();};},[key]);
 async function upload(chosen){if(!chosen||!journal)return;const ctl=new AbortController();control.current=ctl;setBusy(true);setError('');setNotice('');try{
  if(!/\.tiff?$/i.test(chosen.name)||!chosen.size||chosen.size>512*1024*1024)throw Error('请选择非空且不超过512MB的本地GeoTIFF文件。');
  const header=new Uint8Array(await chosen.slice(0,4).arrayBuffer());if(!(['73,73,42,0','77,77,0,42','73,73,43,0','77,77,0,43'].includes([...header].join(','))))throw Error('文件头不是TIFF，请重新选择GeoTIFF原件。');
  if(active.current!==journal.key)return;if(ctl.signal.aborted)throw Object.assign(Error('已停止等待，原请求与已保存记录保留。'),{name:'AbortError'});
  const request=journal.begin(chosen,()=>crypto.randomUUID());file.current=chosen;setIntent(request);setNotice('正在上传并核对原件。请求已暂存；收到服务器确认后才称为已保存。');
  const body=new FormData();body.append('file',chosen);body.append('request_id',request.request_id);let row;
  try{row=await json('/geo/scenes',{method:'POST',body,signal:ctl.signal});}catch(e){if(active.current!==journal.key||ctl.signal.aborted)throw e;try{row=await json('/geo/uploads/'+encodeURIComponent(request.request_id),{signal:ctl.signal});}catch{throw e;}}
  await confirmed(journal,row);
 }catch(e){if(active.current===journal.key){setIntent(journal.intent);setError(e.message+' 原请求及本地原文件保持不变，请核对保存状态或用原请求重试。');}}finally{if(control.current===ctl)control.current=null;if(active.current===journal.key)setBusy(false);}}
 const allowed=['admin','operator'].includes(account.role),blocked=disabled||busy||!journal||journal.key!==key||!allowed||!catalog?.runtime_installed;
 return <section>{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}{intent&&<p className="info">待核对上传：{intent.name} · {Math.round(intent.size/1024)} KB。只在此浏览器暂存请求信息，不暂存完整影像；刷新后可核对结果或重选同一文件重试。</p>}
 <input ref={input} type="file" hidden accept=".tif,.tiff,image/tiff" aria-label="选择GeoTIFF原件" onChange={e=>{const chosen=e.target.files?.[0];e.target.value='';upload(chosen);}}/>
 <div className="actions"><Btn primary disabled={blocked} onClick={()=>input.current.click()}>{intent?'重新选择原文件并重试':'上传GeoTIFF影像'}</Btn>{intent&&<Btn disabled={disabled||busy} onClick={()=>reconcile()}>核对原上传是否已保存</Btn>}{intent&&file.current&&<Btn disabled={blocked} onClick={()=>upload(file.current)}>使用原上传请求重试</Btn>}</div>
 {busy&&control.current&&<Btn onClick={()=>control.current?.abort()}>停止等待并保留原请求</Btn>}{busy&&<p role="status">正在核对原件或保存结果，请勿开始另一项上传；离开后可从原请求恢复。</p>}</section>;
}

import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const recordKey=r=>r.kind+':'+r.id;
const stamp=x=>x?new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未知';
const quality=x=>({good:'设备报告正常，仍待核实',unverified:'待核实',bad:'异常',uncertain:'不确定'})[x]||'未知';
const freshness=x=>({unknown:'未知',stale:'可能已过期',future:'设备时间异常',reported_recent:'设备报告时间较近，待核实'})[x]||'未知';
const validSelection=x=>Array.isArray(x)&&x.length<=20&&x.every(r=>r&&['data','alert'].includes(r.kind)&&typeof r.id==='string'&&/^[a-f0-9]{64}$/.test(r.record_hash));

// Remount at an identity boundary; pending request IDs remain scoped to their original task.
export default function NativeSensorEvidence(props){
 const scope=[props.account.workspace_id,props.account.user_id,props.task.id].join(':');
 return <EvidenceSection key={scope} {...props} scope={scope}/>;
}
function EvidenceSection(props){
 const [open,setOpen]=useState(false),Btn=props.Btn;
 return <section className="local-model-card" aria-label="任务设备证据"><h3>设备数据与线索</h3><p>查看本任务对象的设备读数和线索，保存为本地证据。</p><Btn onClick={()=>setOpen(!open)}>{open?'收起设备证据':'查看设备证据'}</Btn>{open&&<Panel {...props}/>}</section>;
}
function Panel({account,task,Btn,scope,request=json}){
 const base='/sensor-native/tasks/'+encodeURIComponent(task.id),pendingKey='sensor-packet:'+scope+':pending',draftKey='sensor-packet:'+scope+':draft';
 const [data,setData]=useState(null),[history,setHistory]=useState(null),[historyError,setHistoryError]=useState(''),[selected,setSelected]=useState([]),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[storageError,setStorageError]=useState(false);
 const alive=useRef(true),running=useRef(false),sequence=useRef(0);
 const req=(path,options={})=>request(path,{...options,headers:{...(options.headers||{}),'X-Workspace-ID':account.workspace_id}});
 const lock=fn=>{if(!navigator.locks?.request)throw Error('浏览器不支持安全核对多标签页，请更换浏览器。');return navigator.locks.request(pendingKey,{mode:'exclusive'},fn)};
 function readPending(){const raw=localStorage.getItem(pendingKey);if(!raw)return null;const value=JSON.parse(raw);if(value.scope!==scope||typeof value.request_id!=='string'||!validSelection(value.selection)||!Number.isInteger(value.expected_task_version))throw Error('原请求记录无法核对');return value}
 async function load(){const serial=++sequence.current;const value=await req(base+'/context');if(!Array.isArray(value?.records)||!Array.isArray(value?.packets)||!Number.isInteger(value.task_version)||value.cloud_analysis_supported!==false)throw Error('任务证据状态无法核对');if(alive.current&&serial===sequence.current)setData(value);return value}
 async function loadHistory(page=1){const value=await req(base+'/packets?page='+page);if(!Array.isArray(value?.packets)||value.page!==page||typeof value.has_more!=='boolean')throw Error('证据包历史无法核对');if(alive.current){setHistory(value);setHistoryError('')}return value}
 useEffect(()=>{alive.current=true;try{setPending(readPending());const saved=sessionStorage.getItem(draftKey);if(saved){const parsed=JSON.parse(saved);if(!validSelection(parsed))throw Error();setSelected(parsed)}}catch{setStorageError(true);setError('本机原请求或选择无法核对，已暂停提交。')}
  load().catch(e=>{if(alive.current)setError(e.message||'设备证据读取失败')});
  loadHistory().catch(e=>{if(alive.current)setHistoryError(e.message||'证据包历史读取失败')});
  const changed=e=>{if(e.key===pendingKey){try{setPending(readPending())}catch{setStorageError(true);setError('原请求无法核对，已暂停提交。')}}};window.addEventListener('storage',changed);
  return()=>{alive.current=false;sequence.current++;window.removeEventListener('storage',changed)};
 },[scope]);
 function select(next){try{sessionStorage.setItem(draftKey,JSON.stringify(next));setSelected(next);setError('')}catch{setStorageError(true);setError('选择未能保存在本机，已暂停提交。')}}
 async function release(entry){await lock(()=>{const saved=readPending();if(!saved||saved.request_id!==entry.request_id||saved.scope!==entry.scope)throw Error('原请求已变化，请重新核对');localStorage.removeItem(pendingKey);if(localStorage.getItem(pendingKey)!==null)throw Error('原请求状态未能更新')});if(alive.current)setPending(null)}
 async function complete(entry,result){if(!result||!['saved','cancelled'].includes(result.state)||result.state==='saved'&&(!result.packet||!Array.isArray(result.packet.records)||result.packet.records.length!==entry.selection.length||entry.selection.some(s=>!result.packet.records.some(r=>recordKey(r)===recordKey(s)&&r.record_hash===s.record_hash))))throw Error('保存结果无法核对');await loadHistory();await load().catch(()=>{});await release(entry);if(!alive.current)return;if(result.state==='saved'){sessionStorage.removeItem(draftKey);setSelected([]);setMessage('设备证据包已保存到此任务，刷新后可重新查看。')}else setMessage('原请求已安全结束，原选择仍保留。')}
 async function save(){if(running.current||busy||pending||storageError||!data)return;running.current=true;setBusy(true);setError('');setMessage('');let entry;
  try{if(!selected.length||selected.length>20)throw Error('请选择 1 至 20 条记录。');entry={scope,request_id:crypto.randomUUID(),expected_task_version:data.task_version,selection:selected};await lock(()=>{if(readPending())throw Error('存在尚未核对的原请求');localStorage.setItem(pendingKey,JSON.stringify(entry));if(!readPending())throw Error('原请求未保存')});if(alive.current)setPending(entry);
   const value=await req(base+'/packets',{method:'POST',body:JSON.stringify({request_id:entry.request_id,expected_task_version:entry.expected_task_version,selection:entry.selection})});await complete(entry,value);
  }catch(e){if(alive.current)setError(entry&&localStorage.getItem(pendingKey)?'保存结果尚未核对，请先核对或安全结束原请求。':(e.message||'未能提交，请重试。'))}finally{running.current=false;if(alive.current)setBusy(false)}}
 async function recover(cancel){if(!pending||running.current||busy)return;running.current=true;setBusy(true);setError('');try{const value=await req(base+'/requests/'+encodeURIComponent(pending.request_id)+(cancel?'/cancel':''),cancel?{method:'POST',body:'{}'}:{});if(value.state==='not_found'){setMessage('尚未找到原请求。可安全结束后再保存。');return}await complete(pending,value)}catch(e){if(alive.current)setError('原请求仍需核对，请稍后重试。')}finally{running.current=false;if(alive.current)setBusy(false)}}
 const chosen=new Set(selected.map(recordKey));
 const changed=!!data&&selected.some(s=>!data.records.some(r=>recordKey(r)===recordKey(s)&&r.record_hash===s.record_hash));
 return <div><p>选择属于本任务对象的已保存设备读数和线索，形成可追溯的本地证据包。数据和线索均待核实；是否用于云端分析需另行核对并授权。</p>
  {error&&<p role="alert">{error} <Btn disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>重新读取</Btn></p>}{message&&<p role="status">{message}</p>}
  {pending&&<aside className="info"><p>上次保存结果尚待核对；原选择已保留。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn> <Btn disabled={busy} onClick={()=>recover(true)}>安全结束原请求</Btn></aside>}
  {!data?<p>{error?'设备证据尚未读取，请核对权限并重试。':'正在读取设备证据…'}</p>:data.read_only?<p>这是其他负责人的任务；您可以查看其已保存的证据包。</p>:<><h4>可选择的记录</h4>{data.has_more&&<p>这里只显示最近的记录，更早的数据仍保留。</p>}{!data.records.length&&<p>当前任务对象暂无可核对的设备数据。</p>}
   {data.records.map(r=><label key={recordKey(r)} style={{display:'block',margin:'0.5em 0'}}><input type="checkbox" disabled={busy||!!pending||storageError||(!chosen.has(recordKey(r))&&selected.length>=20)||!/^[a-f0-9]{64}$/.test(r.record_hash||'')} checked={chosen.has(recordKey(r))} onChange={e=>select(e.target.checked?[...selected,{kind:r.kind,id:r.id,record_hash:r.record_hash}]:selected.filter(x=>recordKey(x)!==recordKey(r)))}/> {r.kind==='alert'?'待核实线索':'设备读数'} · {r.object_name} · {r.field_name}：{r.value} {r.unit} · {r.device_name} · 设备时间：{stamp(r.source_timestamp)} · 接收：{stamp(r.received_at)} · 质量：{quality(r.quality)} · 时效：{freshness(r.freshness)}</label>)}
   <Btn disabled={busy||!!pending||storageError||changed||!selected.length||selected.length>20} onClick={save}>保存所选设备证据</Btn> <Btn disabled={busy||!!pending||!selected.length||storageError} onClick={()=>select([])}>清空选择</Btn>{selected.length===20&&<p>一次最多选择 20 条。</p>}{changed&&<p role="alert">所选记录已变化或不在当前列表，请重新选择后保存。</p>}
   </>}
   <h4>已保存的证据包</h4>{historyError&&<p role="alert">{historyError} <Btn disabled={busy} onClick={()=>loadHistory().catch(e=>setHistoryError(e.message))}>重新读取历史</Btn></p>}{!history&&!historyError&&<p>正在读取证据包历史…</p>}{history&&!history.packets.length&&<p>尚未保存设备证据包。</p>}{history?.packets.map(packet=><article key={packet.id} className="info"><p>保存时间：{stamp(packet.created_at)} · {packet.records.length} 条记录</p>{packet.records.map(r=><p key={recordKey(r)}>{r.kind==='alert'?'待核实线索':'设备读数'} · {r.object_name} · {r.field_name}：{r.value} {r.unit} · {r.device_name} · 设备时间：{stamp(r.source_timestamp)} · 质量：{quality(r.quality)} · 时效：{freshness(r.freshness)}</p>)}<p>{packet.notice}</p></article>)}{history&&<p><Btn disabled={busy||history.page<=1} onClick={()=>loadHistory(history.page-1).catch(e=>setHistoryError(e.message))}>上一页</Btn> 第 {history.page} 页 <Btn disabled={busy||!history.has_more} onClick={()=>loadHistory(history.page+1).catch(e=>setHistoryError(e.message))}>下一页</Btn></p>}
 </div>;
}

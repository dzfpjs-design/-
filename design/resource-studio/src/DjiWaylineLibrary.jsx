import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';

const MAX=20*1024*1024;
const heightMode=value=>({WGS84:'WGS84 椭球高',relativeToStartPoint:'相对起飞点高度',realTimeFollowSurface:'实时仿地高度'}[value]||(value?'未识别（'+value+'）':'未声明'));
const uuid=x=>typeof x==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(x);
const valid=x=>x&&uuid(x.request_id)&&typeof x.name==='string'&&x.name.length>0&&x.name.length<=200&&/^[0-9a-f]{64}$/.test(x.sha256)&&Number.isSafeInteger(x.size)&&x.size>0&&x.size<=MAX;

/** Imports a real DJI KMZ; no flight or cloud publication occurs in this panel. */
export default function DjiWaylineLibrary({account,Btn,onRegisterGuard,request=json,requestFile=api}){
 const scope=[account?.workspace_id,account?.user_id,account?.role].join(':'),key='dji-wayline:'+account?.workspace_id+':'+account?.user_id+':pending';
 const [rows,setRows]=useState(null),[pending,setPending]=useState(null),[file,setFile]=useState(null),[selected,setSelected]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[editor,setEditor]=useState(false),[attempt,setAttempt]=useState(0),[page,setPage]=useState(1),[more,setMore]=useState(false),[unavailable,setUnavailable]=useState(0);
 const alive=useRef(false),working=useRef(false),guard=useRef(onRegisterGuard),input=useRef(null),state=useRef({});guard.current=onRegisterGuard;state.current={busy,pending};
 const scoped=(path,opt={})=>request(path,{...opt,headers:{...(opt.headers||{}),'X-Workspace-ID':account.workspace_id}});
 function readPending(){const raw=localStorage.getItem(key);if(!raw)return null;const x=JSON.parse(raw);if(!valid(x))throw Error('原上传请求无法核对，已停止操作');return x;}
 async function load(n=1){const value=await scoped('/dji/waylines?page='+n);if(!Array.isArray(value.waylines)||value.page!==n||typeof value.has_more!=='boolean')throw Error('航线列表无法核对');if(alive.current){setRows(value.waylines);setPage(n);setMore(value.has_more);setUnavailable(Number.isSafeInteger(value.unavailable_count)?value.unavailable_count:0)}}
 useEffect(()=>{alive.current=true;setRows(null);setSelected(null);setFile(null);setPending(null);setError('');setNote('');try{setPending(readPending())}catch(e){setError(e.message)}load().catch(e=>{if(alive.current)setError('无法读取航线资料：'+e.message)});return()=>{alive.current=false}},[scope]);
 useEffect(()=>{if(account.role!=='admin')return;let stopped=false,release;setEditor(false);
  if(!navigator.locks?.request){setError('当前浏览器无法保护多标签上传，请使用支持安全锁的浏览器。');return}
  navigator.locks.request(key+':editor',{mode:'exclusive',ifAvailable:true},async lock=>{if(stopped)return;if(!lock)return;try{setPending(readPending());setEditor(true);await new Promise(resolve=>{release=resolve})}catch(e){setError(e.message)}}).catch(()=>{if(!stopped)setError('航线上传锁不可用，请重新进入。')});
  return()=>{stopped=true;release?.()};
 },[scope,attempt]);
 useEffect(()=>{const g=()=>!working.current&&!state.current.busy&&!state.current.pending;guard.current?.(g);return()=>guard.current?.(null,g)},[]);
 async function settle(saved,expected){if(saved?.state==='cancelled'){localStorage.removeItem(key);setPending(null);setFile(null);setNote('原上传已安全结束。');return}
  const item=saved?.wayline;if(saved?.state!=='saved'||!item||item.summary?.sha256!==expected.sha256||item.name!==expected.name||item.summary?.bytes!==expected.size)throw Error('服务器保存结果与原文件不一致');
  localStorage.removeItem(key);setPending(null);setFile(null);setSelected(item);setNote('航线原件已保存并核对摘要。仅完成结构检查，不能据此起飞。');try{await load()}catch(e){setError('航线已保存，但列表暂未更新：'+e.message)}}
 async function recover(cancel=false){if(!pending||busy)return;setBusy(true);working.current=true;setError('');setNote('');try{const v=await scoped('/dji/waylines/operations/'+pending.request_id+(cancel?'/cancel':''),cancel?{method:'POST'}:{});await settle(v,pending)}catch(e){if(e.status===404)setNote('服务器尚未找到原请求，请重新选择同一文件并重试，或安全结束。');else setError('原请求尚待核对：'+e.message)}finally{setBusy(false);working.current=false}}
 async function choose(chosen){if(!chosen||busy||!editor||account.role!=='admin')return;setBusy(true);working.current=true;setError('');setNote('');try{
  if(!/\.kmz$/i.test(chosen.name)||chosen.size<=0||chosen.size>MAX)throw Error('请选择不超过 20 MB 的 DJI KMZ 文件。');
  const sha=await hashBytes(await chosen.arrayBuffer());let original=readPending();
  if(original&&(original.name!==chosen.name||original.size!==chosen.size||original.sha256!==sha))throw Error('选择的文件与原请求不一致，请选择原文件或先安全结束。');
  if(!original){original={request_id:crypto.randomUUID(),name:chosen.name,size:chosen.size,sha256:sha};const text=JSON.stringify(original);localStorage.setItem(key,text);if(localStorage.getItem(key)!==text)throw Error('无法保留原请求，未上传。');setPending(original)}
  setFile(chosen);const data=new FormData();data.append('request_id',original.request_id);data.append('file',chosen);
  let result;try{result=await scoped('/dji/waylines',{method:'POST',body:data})}catch(e){try{result=await scoped('/dji/waylines/operations/'+original.request_id)}catch{throw e}}
  await settle(result,original);
 }catch(e){setError(e.message+' 原请求保留，请核对后重试。')}finally{setBusy(false);working.current=false}}
 async function download(row){setBusy(true);setError('');try{const res=await requestFile('/dji/waylines/'+row.id+'/download',{headers:{'X-Workspace-ID':account.workspace_id}});const bytes=await res.arrayBuffer();if(await hashBytes(bytes)!==row.summary.sha256)throw Error('原件摘要不一致');const url=URL.createObjectURL(new Blob([bytes],{type:'application/vnd.google-earth.kmz'}));const a=document.createElement('a');a.href=url;a.download=row.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){setError('下载失败：'+e.message)}finally{setBusy(false)}}
 async function open(row){setBusy(true);setError('');try{const value=await scoped('/dji/waylines/'+row.id);if(value.id!==row.id||value.summary?.sha256!==row.summary.sha256||!Array.isArray(value.summary?.routes))throw Error('航线详情与列表不一致');setSelected(value)}catch(e){setError('无法读取航线详情：'+e.message)}finally{setBusy(false)}}
 if(!account?.workspace_id)return null;
 return <section className="local-model-card" aria-label="大疆航线资料"><h2>大疆航线资料</h2><p>上传已规划的 DJI KMZ 航线并核对航点。文件仅保存在工作区；格式检查不能替代航线安全、空域和设备适配核验。</p>
 {error&&<p role="alert">{error}</p>}{note&&<p role="status">{note}</p>}
 {account.role==='admin'&&!editor&&<p>另一标签页可能正在编辑。<Btn onClick={()=>setAttempt(x=>x+1)}>重新获取编辑权</Btn></p>}
 {account.role==='admin'&&<><input ref={input} type="file" accept=".kmz" hidden aria-label="选择 DJI KMZ 航线" onChange={e=>{const f=e.target.files?.[0];e.target.value='';choose(f)}}/><Btn disabled={busy||!editor} onClick={()=>input.current?.click()}>{pending?'重新选择原文件':'上传 DJI 航线文件'}</Btn></>}
 {pending&&<div><p>航线上传结果待核对：{pending.name}。刷新不会重发；原件不保存在浏览器。</p><Btn disabled={busy} onClick={()=>recover(false)}>核对原请求</Btn><Btn disabled={busy} onClick={()=>recover(true)}>安全结束原请求</Btn>{file&&account.role==='admin'&&<Btn disabled={busy||!editor} onClick={()=>choose(file)}>使用原文件重试</Btn>}</div>}
 {busy&&<p role="status">正在核对或保存，请稍候。</p>}{unavailable>0&&<p role="alert">本页有 {unavailable} 条航线原件或记录无法核对，已保留，请联系管理员恢复。</p>}{rows===null?<p>正在读取航线资料…</p>:!rows.length?<p>当前页无可核对的航线文件。</p>:<ul>{rows.map(row=><li key={row.id}><button type="button" disabled={busy} onClick={()=>open(row)}>{row.name}</button> · {row.summary.point_count} 个航点 · 仅结构核对</li>)}</ul>}
 <div><Btn disabled={busy||page<=1} onClick={()=>load(page-1).catch(e=>setError(e.message))}>上一页</Btn> 第 {page} 页 <Btn disabled={busy||!more} onClick={()=>load(page+1).catch(e=>setError(e.message))}>下一页</Btn></div>
 {selected&&<article><h3>{selected.name}</h3><p>{selected.summary.notice}</p><p>航线数：{selected.summary.route_count}；航点数：{selected.summary.point_count}；SHA-256：{selected.summary.sha256}</p><p>{selected.dispatch_reason}</p>{selected.summary.routes.map((route,i)=><div key={i}><h4>航线 {route.wayline_id??i+1} · 高度模式：{heightMode(route.height_mode)}</h4><ol>{route.points.slice(0,50).map(point=><li key={point.index}>航点 {point.index}：经度 {point.longitude}，纬度 {point.latitude}；执行高度 {point.execute_height??'未声明'} 米</li>)}</ol>{route.points.length>50&&<p>仅显示前 50 个航点，可下载原件核对全部航点。</p>}</div>)}<Btn disabled={busy} onClick={()=>download(selected)}>下载已核对原件</Btn></article>}</section>;
}

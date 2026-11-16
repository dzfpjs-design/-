import React,{useEffect,useState} from 'react';
import {json} from './runtime-api';
import MediaDailyDelivery from './MediaDailyDelivery';
export default function VideoDailyStudio({operator,account,onViewVideo,Btn}){
 const [rows,setRows]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function load(){setRows(await json('/media/daily'));}
 useEffect(()=>{load().catch(e=>setError(e.message));const timer=setInterval(()=>load().catch(e=>setError(e.message)),10000);return()=>clearInterval(timer);},[]);
 async function refresh(){setBusy(true);setError('');try{setRows(await json('/media/daily/reconcile',{method:'POST'}));}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <div className="media-monitor"><h3>每天整理视频资料</h3><p>后台自动整理最近八天的手动读取、自动读取和上传视频，保存整理候选并独立送交数据管理智能体。按读取请求或资料上传日期归类，不把它当成未经校准的拍摄日期。更早的未完成记录会继续核对；迟到数据更新原日期的回执。当天记录仍可更新，空日期不会生成“已完成”结果。</p><Btn disabled={busy||!operator} onClick={refresh}>{busy?'正在整理并保存…':'刷新归集记录'}</Btn>{error&&<p className="error" role="alert">{error}</p>}
 {!rows.length?<p>还没有已保存的视频归集记录。后台完成整理后会在这里显示。</p>:rows.map(r=><details className="media-session" key={r.id}><summary>{r.received_day} · {r.source_name} · {r.status==='incomplete'?'有失败、缺失或待处理项':'原件与提取结果已归集，时间与质量待核'}{r.provisional?' · 当天更新中':''}</summary><p>手动读取 {r.manual_attempts} 次 · 自动读取 {r.automatic_attempts} 次 · 上传 {r.uploads} 次 · 原件 {r.unique_originals} 份</p><p>保存分析画面 {r.saved_frames} 张 · 业务候选线索 {r.business_candidates} 条 · 对象核查草稿 {r.business_tasks} 个</p><p>迟到接收 {r.late_received_records} 项 · 失败/停止/不完整 {r.failed_or_partial_ids.length} 项 · 待处理 {r.pending_ids.length} 项 · 拍摄时间尚未核验 {r.unverified_source_clock_records} 项。本次整理候选已保存；独立收讫状态如下。</p><MediaDailyDelivery modality="video" dailyID={r.id} account={account} Btn={Btn}/>{r.records.map(record=><div className="media-session" key={record.id}><p>{record.mode==='automatic'?'自动读取':record.mode==='manual'?'手动读取':'上传资料'} · {record.capture_status==='failed'?'读取失败':record.capture_status==='cancelled'?'已停止':record.capture_status==='partial'?'视频不完整':record.evidence_id?'原件已保存':'等待原件'} · {record.processing_status==='succeeded'?'本地分析完成':record.processing_status==='failed'?'本地分析失败':'尚未完成分析'}</p>{record.run_id&&<Btn onClick={()=>onViewVideo(record.run_id)}>查看归集依据与视频</Btn>}</div>)}</details>)}
 </div>;
}

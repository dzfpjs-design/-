import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {SavedAction} from './DataNeedStudio';
import {acquisitionLabel,reviewBody,reviewChoices} from './native-acquisition-contract.mjs';
const stamp=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未保存';
const value=x=>x==null?'未填写':typeof x==='boolean'?(x?'是':'否'):Array.isArray(x)?x.join('、'):typeof x==='object'?'结构化内容，需回原资料核对':String(x);
export default function NativeAcquisitionStudio({task,account,fields,Btn,onUpdated}){
 const base='/workspace/tasks/'+task.id+'/goal/acquisitions';
 const [data,setData]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const life=useRef(0),inflight=useRef(false);
 async function load(){const epoch=life.current;const saved=await json(base);if(epoch===life.current)setData(saved);return saved;}
 useEffect(()=>{life.current++;setData(null);setError('');load().catch(e=>setError(e.message));return()=>{life.current++;};},[base]);
 async function run(action){if(inflight.current)return;const epoch=life.current;inflight.current=true;setBusy(true);setError('');setNotice('');try{await action();if(epoch!==life.current)return;await load();await onUpdated?.();}catch(e){if(epoch===life.current)setError(e.message+' 原记录与人工草稿保留；刷新后只核对原请求，不重发设备指令。');}finally{inflight.current=false;if(epoch===life.current)setBusy(false);}}
 async function check(row){await run(async()=>{await json(base+'/'+row.id+'/receipt',{method:'POST',body:JSON.stringify({expected_version:row.version,reason:'从原业务任务只查询已派发采集的回执，不重发设备指令'})});setNotice('原请求回执已保存；设备结束、资料归集和业务目标验收分别核对。');});}
 return <section className="original-acquisitions" aria-label="原目标设备补采与回执"><div className="section-title"><h4>设备补采与原回执</h4><Btn disabled={busy} onClick={()=>run(()=>load())}>{busy?'正在核对原回执…':'刷新补采记录'}</Btn></div>
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{!data&&!error&&<p>正在读取原目标的设备补采记录…</p>}
 {data?.parent_ended&&data.items.length>0&&<p>原目标已停止、截止或验收。仍可核对已下发请求和迟到资料，不恢复旧目标，也不重发设备指令。</p>}
 {data?.items.length===0&&<p>当前原目标没有已保存的设备补采记录。</p>}{data?.truncated&&<p>这里只显示最早100条原记录，其余仍保存在服务器；不能据此认定所有采集已结束。</p>}
 {data?.items.map(row=><article className="media-session" key={row.id}><h5>{row.asset_title}</h5><p>{row.source_name} · {row.action==='read_existing'?'读取既有资料':'补充观察'}</p><strong>{acquisitionLabel(row)}</strong><p>需要核对：{row.required_fields.map(f=>fields[f]||'原业务字段').join('、')}。原计划截止：{stamp(row.deadline)}。</p>
 {row.reservation_held&&<p>设备占用仍保留，不能重复下发或换设备重做来掩盖未知结果。</p>}{row.receipt.receipt_id&&<p>供应商原回执：{row.receipt.receipt_id} · 最近核对：{stamp(row.receipt_checked_at)}。</p>}
 {['blocked_connector','blocked_resource'].includes(row.status)&&<p>{row.reason}</p>}
 {row.can_reconcile&&<><p>此操作只查询原请求的回执，不启动新的采集。目标结束不代表设备已物理停止。</p><Btn disabled={busy} onClick={()=>check(row)}>核对原设备回执</Btn></>}
 {row.closing_observation_id&&<p>原采集已有合格且归集的资料；这不代表原业务目标已完成或现场状态正常。</p>}
 {row.observations.items.map((record,i)=><section key={record.id}><h5>原采集资料 {i+1} · {record.review==='confirmed'?'已核查':record.review==='rejected'?'已拒绝':'待核查'}</h5><p>原登记时间：{record.observed_at?stamp(record.observed_at):'尚未核实'}。来源时间与现场真实性仍需独立核实。</p>
 {record.problem?<p>{record.problem}</p>:<><table><thead><tr><th>业务字段</th><th>原登记输入</th><th>提取回执</th></tr></thead><tbody>{row.required_fields.map(f=><tr key={f}><td>{fields[f]||'原业务字段'}</td><td>{value(record.original_values?.[f])}</td><td>{value(record.extracted_values?.[f])}</td></tr>)}</tbody></table>
 {record.saved_review&&<p>已保存核查说明：{record.saved_review.reason}</p>}<p>核查这份资料只保存原采集结果，不恢复已停止的业务目标，也不代替目标验收。</p>
 <SavedAction storageKey={'ga-native-acquisition-review:'+account.workspace_id+':'+account.username+':'+task.id+':'+record.id} endpoint={'/observations/'+record.id+'/verify'} canWrite={record.can_verify&&!busy} Btn={Btn} choices={reviewChoices} label="保存原采集资料核查" makeBody={decision=>reviewBody(record,decision)} onSaved={async()=>{await load();await onUpdated?.();}}/></>}
 </section>)}{row.observations.truncated&&<p>这里只显示最近10份关联资料，其他原记录仍保留；不代表全部资料已经核查。</p>}
 </article>)}
 </section>;
}

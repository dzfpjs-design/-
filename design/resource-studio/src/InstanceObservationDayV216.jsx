import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api} from './runtime-api';
import {InstanceObservationDayControllerV216,chinaObservationDay,observationStatusLabels} from './instance-observation-day-v216.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import './instance-observation-day-v216.css';

const Button=({children,...props})=><button type="button" {...props}>{children}</button>;
const labels={video:'视频资料',audio:'音频资料',imagery:'影像资料'};
function chinaTime(value){if(!value)return '尚无独立收讫时间';return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'medium',hour12:false}).format(new Date(value));}
export default function InstanceObservationDayV216({account,instanceId,releaseId,instanceName,request=json,readFile=api,tabStorage=globalThis.sessionStorage,onViewTask,Btn=Button}){
 const [,draw]=useState(0),[filter,setFilter]=useState('all'),latest=useRef(null),lifetime=useRef(null);if(!lifetime.current)lifetime.current=createRetainedControllerLifecycle();
 let token='';try{token=tabStorage?.getItem('ga-setup-csrf')||'';}catch{}
 const scopeKey=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId,releaseId,token]);latest.current={scopeKey,onViewTask};
 const holder=useMemo(()=>{if(!account?.user_id||!account?.workspace_id||!instanceId||!releaseId||!token)return {c:null,error:'当前账号或实例启用版本尚未读到，请刷新工作区后查看。'};try{return {c:new InstanceObservationDayControllerV216({account,instanceId,releaseId,request,readFile,session:tabStorage,currentAvailable:()=>latest.current.scopeKey===scopeKey,onViewTask:(id,checkEntry)=>latest.current.onViewTask?.(id,checkEntry)}),error:''};}catch(e){return {c:null,error:e.message};}},[scopeKey,request,readFile,tabStorage]);
 const c=holder.c;
 useEffect(()=>{if(!c)return;const lease=lifetime.current.setup(c,scopeKey);if(!lease)return;let mounted=true;c.onChange=()=>{if(mounted&&c.active&&!c.suspended)draw(n=>n+1);};c.refresh().catch(()=>{});return()=>{mounted=false;lifetime.current.cleanup(lease);};},[c,scopeKey]);
 const act=async fn=>{let ticket=c?.generation;try{const pending=fn();ticket=c?.generation;await pending;}catch(e){if(c?.active&&!c.suspended&&!c.denied&&ticket===c.generation&&latest.current.scopeKey===scopeKey){c.error=e.message;c.notify();}}};
 const data=c&&!c.denied?c.data:null;
 return <section className="instance-observation-day-v216" aria-label="本实例当日视频音频影像资料"><div className="section-title"><div><h3>本实例当日资料</h3><p>视频、音频与影像按明确的业务和对象关联展示。{instanceName&&data?'当前实例：'+instanceName+'。':''}这里只读已保存记录，不发起处理或独立接收。</p></div></div>
 {!c?<p role="status">{holder.error}</p>:<>
 <div className="observation-day-controls"><label>资料日期（接收或处理日期）<input type="date" aria-label="当日资料日期" value={c.day} disabled={c.denied} onChange={e=>act(()=>c.refresh(e.target.value))}/></label><Btn disabled={c.denied} onClick={()=>act(()=>c.refresh())}>{c.busy?'重新读取所选日期':'重新读取当日资料'}</Btn><Btn disabled={c.denied} onClick={()=>act(()=>c.refresh(chinaObservationDay()))}>查看今天的资料</Btn></div>
 <p>日期默认按中国时间。接收、处理和现场采集日期含义不同；此处不能证明管理范围当天的真实状态，也不是生态监测结论。</p>
 {c.busy&&<p role="status">正在读取并核对原资料，请稍候…</p>}{(c.error||c.denied)&&<p role="alert">{c.denied?'实际账号或权限已变化，原资料已隐藏，请重新打开当前账号的工作区。':c.error}</p>}
 {c.stale&&<p role="status">{c.busy?'正在更新。':'本次更新未成功。'}下方保留此前读到的修订，不代表最新记录；原件与详情暂不打开。</p>}
 {!data&&!c.busy&&!c.denied&&<p role="status">当前日期资料尚未读取成功，数量未知，不能据此判断没有资料。</p>}
 {data&&<>
 <div className="observation-day-filters" role="group" aria-label="按资料类型查看">{[['all','全部资料'],...Object.entries(labels)].map(([key,label])=><Btn key={key} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}</Btn>)}</div>
 <p className="info">{data.scope_note} {data.coverage_note}</p><p>{data.time_note}</p>{data.status==='partial'&&<p role="alert">部分资料或来源仍待核对，当前展示不完整；未知数量不会记为零。</p>}
 {data.empty_message&&!c.stale&&<p role="status">{data.empty_message}</p>}
 {data.sections.filter(s=>filter==='all'||s.modality===filter).map(section=><section className="observation-day-section" key={section.modality} aria-label={section.label}><div className="section-title"><h4>{section.label}</h4><span>{section.count_complete?'已读取'+section.record_count+'条':section.record_count===null?'记录数量尚未核实':'至少已读到'+section.record_count+'条'}</span></div>
 {section.truncated&&<p role="alert">本次读取达到上限，当前清单不完整，请到原详细整理入口核对。</p>}{section.issues.map(issue=><p role="alert" key={issue.code}>{issue.message}</p>)}
 {section.count_complete&&!section.records.length&&!c.stale&&<p>所选日期尚无可核实绑定到当前启用版本的{section.label}，不包含独立上传或未绑定资料。</p>}
 {section.records.map(row=><article className="observation-day-record media-form" key={row.id}><div className="section-title"><h5>{row.title}</h5><span>{row.status_label}</span></div><p><strong>业务：</strong>{row.binding.business_name}</p><p><strong>{row.binding.task_id?'关联任务对象集合：':'关联观测场景：'}</strong>{row.binding.entities.map(e=>e.name).join('、')}</p><p className="info">{row.binding.label}</p>
 <p><strong>已保存候选：</strong>{row.candidate.summary}</p><p>核对状态：{row.candidate.review}</p>{row.candidate.quality.map((q,i)=><p role="status" key={i}>{q}</p>)}
 <div className="observation-day-dates"><span>原整理日期：{row.dates.collection_day}</span><span>接收日期：{row.dates.received_day||'尚未核实'}</span><span>处理请求日期：{row.dates.processing_request_day||'尚未核实'}</span><span>处理保存日期：{row.dates.processing_completed_day||'尚未核实'}</span><span>现场采集时间：尚未核实</span></div>
 <p>整理第{row.collection.revision}次修订 · {row.collection.ack.label}{row.collection.ack.verified_received?' · 独立收讫于'+chinaTime(row.collection.ack.received_at):'；未当作数据管理已经收到。'}</p>{row.source_changed_since_collection===true&&<p role="status">原处理记录之后有新修订；这里的候选仍属于该整理修订，需对照当前详情。</p>}
 <div className="actions">{row.binding.task_id&&(onViewTask?<Btn disabled={c.busy||c.stale} onClick={()=>act(()=>c.openTask(row))}>打开原业务任务</Btn>:<a className="btn" href={row.task_url}>打开原业务任务</a>)}{row.detail_url&&<Btn disabled={c.busy||c.stale||!row.originals_verified} onClick={()=>act(()=>c.readDetail(row))}>读取原处理当前详情</Btn>}{row.originals.map((original,i)=><Btn key={original.evidence_id} disabled={c.busy||c.stale||!row.originals_verified} onClick={()=>act(()=>c.original(row,original))}>核对并下载原件 {i+1}</Btn>)}</div>
 {!row.originals_verified&&<p>原整理来源尚未核实，暂不从此卡片打开原件或候选详情；可在下方原详细整理入口恢复。</p>}
 {c.download?.record_id===row.id&&!c.stale&&<p><a className="btn" href={c.download.url} download={c.download.name}>下载已核对原件</a></p>}
 {c.detail?.record_id===row.id&&!c.stale&&<section aria-label="原处理当前详情"><h6>原处理当前详情</h6><p>当前状态：{observationStatusLabels[c.detail.status]||'处理状态待核对'} · {c.detail.version_label}{c.detail.version?'第'+c.detail.version+'版':'版本尚未读到'}；{c.detail.collection_version_label}{row.source_version_at_collection===null?'版本待核':'第'+row.source_version_at_collection+'版'}。</p><p>{c.detail.note}</p>{c.detail.changed===true&&<p role="status">当前处理或核查与整理时不同，请在原详细整理入口核对后续修订。</p>}</section>}
 </article>)}
 </section>)}
 {data.same_evidence_links.length>0&&<details><summary>跨资料类型的同一原文件关联 · {data.same_evidence_links.length}组</summary>{data.same_evidence_links.map((link,i)=><p key={i}>{link.records.map(r=>labels[r.modality]).join('、')}：{link.label}</p>)}<p>这些关联没有确认同一物种、同一个体或同一现场事件。</p></details>}
 <p>完整音频、影像、视频依据和原接收、重试等操作继续保留在下方详细整理入口；查看此处不会执行这些操作。</p>
 </>}
 </>}
 </section>;
}

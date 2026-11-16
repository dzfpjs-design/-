import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {SignalBasis} from './SignalStudio';
const decisions={abnormal:'属于业务异常',not_abnormal:'不属于业务异常',uncertain:'尚不能判断'};
const empty={note:'',decision:'',override_reason:'',revision_reason:''};
function Review({initial,task,account,Btn,onLoad}){
 const key=['ga-native-review',account.workspace_id,account.username,task.id,initial.id].join(':'),cache=useRef(null),life=useRef(0);
 if(cache.current===null){try{cache.current=JSON.parse(localStorage.getItem(key)||'null')||{};}catch{cache.current={broken:true};}}
 const [saved,setSaved]=useState(initial),[form,setForm]=useState(cache.current.form||initial.linked_review_draft||initial.business_review||empty),[dirty,setDirty]=useState(!!cache.current.dirty),[pending,setPending]=useState(cache.current.pending||null),[busy,setBusy]=useState(false),[error,setError]=useState(cache.current.broken?'浏览器恢复记录无法读取，已保存审核仍在服务器。':''),[notice,setNotice]=useState('');
 useEffect(()=>()=>{life.current++;},[]);
 useEffect(()=>{setSaved(initial);if(!dirty&&!pending)setForm(initial.linked_review_draft||initial.business_review||empty);},[initial]);
 function store(next={}){if(cache.current.broken)throw Error('请先核对服务器审核记录，损坏的浏览器草稿仍保留。');localStorage.setItem(key,JSON.stringify({form,dirty,pending,...next}));}
 function change(field,value){try{const next={...form,[field]:value};store({form:next,dirty:true});setForm(next);setDirty(true);setError('');}catch(e){setError(e.message);}}
 async function act(fn){const epoch=life.current;setBusy(true);setError('');setNotice('');try{await fn();}catch(e){if(life.current===epoch)setError(e.message+' 原输入与请求保留。');}finally{if(life.current===epoch)setBusy(false);}}
 async function readLatest(){const result=await json('/workspace/tasks/'+task.id+'/linked-signals'),row=result.records.find(r=>r.id===saved.id);if(!row)throw Error('未在当前任务找到关联线索，请核对原任务。');return row;}
 async function save(final){
  let request=pending;
  if(!request){request={operation:final?'review':'draft',body:{version:saved.version,request_id:crypto.randomUUID(),note:form.note||'',decision:form.decision||null,override_reason:form.override_reason||'',revision_reason:form.revision_reason||''}};store({pending:request});setPending(request);}
  let row;
  try{row=await json('/workspace/instances/'+task.instance_id+'/signals/'+saved.id+'/original-task-'+(request.operation==='review'?'review':'draft'),{method:request.operation==='review'?'POST':'PUT',body:JSON.stringify(request.body)});}
  catch(e){if([400,422].includes(e.status)){try{const current=await readLatest();if(current.version===request.body.version&&!current.linked_review_requests.some(r=>r.request_id===request.body.request_id)){store({pending:null});setPending(null);setNotice('服务器确认本次未保存，输入保留，请修正后提交。');}}catch{}}throw e;}
  setSaved(row);setPending(null);setDirty(false);const next=row.linked_review_draft||row.business_review||empty;setForm(next);store({form:next,dirty:false,pending:null});setNotice(request.operation==='review'?'补充业务判断及数据候选回执已保存，原流程未改变。':'审核草稿已保存到服务器。');await onLoad();
 }
 async function recover(){const row=await readLatest();setSaved(row);const accepted=pending&&row.linked_review_requests.some(r=>r.request_id===pending.body.request_id);setPending(null);store({pending:null});if(accepted){const next=row.linked_review_draft||row.business_review||empty;setForm(next);setDirty(false);store({form:next,dirty:false,pending:null});setNotice('已核对原请求保存回执。');}else setNotice('已读取最新服务器版本，当前输入仍保留，可对照后保存。');}
 const writable=['admin','operator'].includes(account.role),frozen=busy||!!pending||!writable||cache.current.broken;
 return <article className="media-form"><h4>{saved.business_review?'已保存补充判断':'待补充核查'} · {saved.basis?.object_name||'原线索对象'}</h4><p>原任务交接说明：{saved.source_task_link.note||'未填写'}</p><p>审核不会重新运行模型或修改原流程。</p>
 {saved.integrity_issue?<p role="alert">{saved.integrity_issue}，原审核草稿和判断保留。</p>:<SignalBasis basis={saved.basis}/>}
 {saved.business_review&&<div className="media-form"><strong>服务器已保存判断：{decisions[saved.business_review.decision]}</strong><p>{saved.business_review.note}</p>{saved.business_review.revision_reason&&<p>改判原因：{saved.business_review.revision_reason}</p>}<p>已保存数据管理候选回执，未更新对象或发布经验。</p></div>}
 <label>补充核查结论<textarea aria-label="原任务补充核查结论" value={form.note||''} disabled={frozen} onChange={e=>change('note',e.target.value)}/></label>
 <label>业务判断<select aria-label="原任务补充业务判断" value={form.decision||''} disabled={frozen} onChange={e=>change('decision',e.target.value)}><option value="">请选择判断</option>{Object.entries(decisions).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
 {saved.business_review&&<label>改判原因<input aria-label="原任务补充改判原因" value={form.revision_reason||''} disabled={frozen} onChange={e=>change('revision_reason',e.target.value)}/></label>}
 <details><summary>负责人及管理员代为核查</summary><p>由关联时固定的原流程负责人核查。管理员代为核查需要说明原因。</p><label>管理员代核查原因<input aria-label="原任务管理员代核查原因" value={form.override_reason||''} disabled={frozen} onChange={e=>change('override_reason',e.target.value)}/></label></details>
 {dirty&&<p>有尚未保存到服务器的审核输入，浏览器恢复记录已保留。</p>}{pending&&<p>原保存请求尚待确认，输入已锁定；可重试同一请求或核对最新保存记录。</p>}
 <div className="actions"><Btn disabled={busy||!writable||!!pending} onClick={()=>act(()=>save(false))}>保存补充审核草稿</Btn><Btn primary disabled={busy||!writable||!!saved.integrity_issue} onClick={()=>act(()=>save(true))}>{pending?'重试原审核保存请求':saved.business_review?'保存改判并保留原判断':'保存补充业务判断'}</Btn><Btn disabled={busy} onClick={()=>act(recover)}>核对最新审核，保留输入</Btn></div>
 {saved.business_review_history.length>0&&<details><summary>查看此前补充判断（{saved.business_review_history.length}）</summary>{saved.business_review_history.map((r,i)=><div className="media-form" key={i}><strong>{decisions[r.decision]}</strong><p>{r.note}</p><p>{new Date(r.at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</p></div>)}</details>}
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}</article>;
}
export default function LinkedSignalReviews({task,account,Btn}){
 const [records,setRecords]=useState([]),[error,setError]=useState(''),[more,setMore]=useState(false);const life=useRef(0);
 async function load(){const epoch=life.current;const value=await json('/workspace/tasks/'+task.id+'/linked-signals');if(epoch===life.current){setRecords(value.records);setMore(value.has_more);setError('');}}
 useEffect(()=>{life.current++;setRecords([]);setError('');load().catch(e=>setError('补充审核暂无法读取：'+e.message));return()=>{life.current++;};},[task.id]);
 if(!records.length&&!error)return null;
 return <section className="media-form"><h3>原分析任务的补充线索审核</h3><p>原业务流程和处理回执保持原样；下列补充审核单独保存。{records.filter(r=>!r.business_review).length} 条线索尚待业务判断。</p><Btn onClick={()=>load().catch(e=>setError(e.message))}>读取保存的补充审核</Btn>{error&&<p role="alert">{error}</p>}{records.map(row=><Review key={task.id+':'+row.id} initial={row} {...{task,account,Btn}} onLoad={load}/>)}{more&&<p>只显示最近100条关联，历史保留在服务器。</p>}</section>;
}

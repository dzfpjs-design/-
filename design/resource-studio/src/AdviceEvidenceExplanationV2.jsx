import React,{useCallback,useEffect,useRef,useState} from 'react';
import './advice-evidence-explanation-v2.css';

const KINDS={capability_declaration:'已登记能力',observation:'现场观测',other:'其他依据'};
const SCOPES={provided_source_text:'已提供的资料',provided_excerpt:'资料摘录',
 not_reviewed:'资料范围待核',unknown:'资料范围待核'};
const SOURCE_TYPES={observation:'观测记录',capability:'能力资料',evidence:'业务资料',packet:'资料包',archive_record:'归档记录'};
const FRESHNESS={current:'在原设定有效期内',stale:'已过期',unknown:'时效待核',not_applicable:'不适用'};
const OBSERVATION={
 not_cited:'未引用现场观测，设备状态待核。',
 current:'已引用现场观测，设备当前状态仍需确认。',
 stale:'观测已过期，现场状态待核。',
 unknown:'观测时效待核，现场状态待核。',
};

function observedTime(value){
 if(typeof value!=='string')return null;
 const parts=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/i.exec(value);
 if(!parts)return null;
 const [year,month,day,hour,minute,second]=parts.slice(1).map(Number);
 if(month<1||month>12||day<1||day>new Date(Date.UTC(year,month,0)).getUTCDate()||hour>23||minute>59||second>59)return null;
 const time=new Date(value);return Number.isFinite(time.getTime())?time:null;
}

function sourceNames(types){
 if(!Array.isArray(types)||types.length===0)return '来源待核';
 return [...new Set(types.map(type=>Object.hasOwn(SOURCE_TYPES,type)?SOURCE_TYPES[type]:'来源待核'))].join('、');
}

// Do not turn malformed or internally inconsistent response data into an
// evidence conclusion. The server remains responsible for source validation.
function checkedEvidence(value){
 if(!value||typeof value!=='object'||typeof value.has_recommendation!=='boolean'||!Array.isArray(value.items)||
  !['capability_count','observation_count','other_count','partial_source_count'].every(k=>Number.isSafeInteger(value[k])&&value[k]>=0)||
  !Object.hasOwn(OBSERVATION,value.observation_state))return null;
 const counts={capability_declaration:0,observation:0,other:0};
 for(const item of value.items){
  if(!item||typeof item!=='object'||!Object.hasOwn(KINDS,item.kind)||!Object.hasOwn(SCOPES,item.source_scope)||
   !Object.hasOwn(FRESHNESS,item.freshness)||!(item.observed_at==null||typeof item.observed_at==='string'))return null;
  for(const name of ['observed_start','observed_end'])
   if(item[name]!==undefined&&item[name]!==null&&(typeof item[name]!=='string'||!observedTime(item[name])))return null;
  if(item.kind==='observation'&&item.freshness==='not_applicable')return null;
  if(item.kind!=='observation'&&item.freshness!=='not_applicable')return null;
  if(item.observed_at!=null&&!observedTime(item.observed_at))return null;
  if(item.observed_start&&item.observed_end&&observedTime(item.observed_start)>observedTime(item.observed_end))return null;
  if(item.kind==='observation'&&['current','stale'].includes(item.freshness)&&!observedTime(item.observed_at)&&
   !(item.observed_start&&item.observed_end))return null;
  counts[item.kind]++;
 }
 if(counts.capability_declaration!==value.capability_count||counts.observation!==value.observation_count||counts.other!==value.other_count)return null;
 const observations=value.items.filter(item=>item.kind==='observation');
 if((observations.length===0)!==(value.observation_state==='not_cited'))return null;
 if(value.observation_state==='current'&&observations.some(item=>item.freshness!=='current'))return null;
 if(value.observation_state==='stale'&&!observations.some(item=>item.freshness==='stale'))return null;
 if(value.observation_state==='unknown'&&!observations.some(item=>item.freshness==='unknown'))return null;
 return value;
}

function checkedPage(page){
 if(!page||typeof page!=='object'||typeof page.enabled!=='boolean'||!Array.isArray(page.items)||
  !(page.next_cursor===undefined||page.next_cursor===null||typeof page.next_cursor==='string'))throw Error('invalid page');
 return {enabled:page.enabled,items:page.items,next_cursor:page.next_cursor||null};
}

function EvidenceItem({item,index}){
 const data=item?.state==='ready'?checkedEvidence(item.evidence):null;
 if(!data)return <article className="media-form"><h5>第 {index+1} 条</h5>
  <p role="status">依据待核实，暂无法展示。</p></article>;
 return <article className="media-form"><h5>{data.has_recommendation?'建议 '+(index+1):'暂无推荐方案'}</h5>
  <dl className="advice-evidence-counts">
   <div><dt>已登记能力</dt><dd>{data.capability_count} 条</dd></div>
   <div><dt>现场观测</dt><dd>{data.observation_count} 条</dd></div>
   <div><dt>其他依据</dt><dd>{data.other_count} 条</dd></div>
  </dl>
  <p>{OBSERVATION[data.observation_state]}</p>
  {data.partial_source_count>0&&<p>包含资料摘录，尚不能代表全文。</p>}
  {data.items.some(entry=>entry.kind==='observation'&&entry.freshness==='unknown')&&data.observation_state!=='unknown'&&
   <p>部分观测时效待核。</p>}
  {data.items.length>0&&<details><summary>查看来源与时间</summary>
   {data.items.map((entry,i)=><div key={i}>
    <p><strong>依据 {i+1}：{KINDS[entry.kind]}</strong> · 来源：{sourceNames(entry.source_types)} · {SCOPES[entry.source_scope]}</p>
    {entry.kind==='observation'&&<p>观测时间：{entry.observed_at?
     <time dateTime={entry.observed_at}>{observedTime(entry.observed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）</time>:
     entry.observed_start||entry.observed_end?<>{entry.observed_start?<time dateTime={entry.observed_start}>{observedTime(entry.observed_start).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</time>:'开始待核'}
      {' 至 '}{entry.observed_end?<time dateTime={entry.observed_end}>{observedTime(entry.observed_end).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</time>:'结束待核'}（北京时间）</>:'未提供'}；{FRESHNESS[entry.freshness]}。</p>}
   </div>)}
  </details>}
 </article>;
}

export default function AdviceEvidenceExplanationV2({account,task,requestJSON}){
 const scope=JSON.stringify([account?.workspace_id,account?.user_id,account?.role,task?.id]);
 const base='/workspace/tasks/'+encodeURIComponent(task?.id||'')+'/business-advice/evidence-explanation';
 const [snapshot,setSnapshot]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState('');
 const generation=useRef(0),inFlight=useRef(false);
 const refresh=useCallback(async()=>{
  if(inFlight.current)return;
  const current=generation.current;inFlight.current=true;setBusy('refresh');setError('');
  try{const page=checkedPage(await requestJSON(base));
   if(generation.current===current)setSnapshot({scope,...page});
  }catch{if(generation.current===current){setSnapshot(null);setError('暂时无法读取建议依据，请刷新重试。');}}
  finally{if(generation.current===current){inFlight.current=false;setBusy('');}}
 },[base,requestJSON,scope]);
 useEffect(()=>{
  generation.current++;inFlight.current=false;setSnapshot(null);setError('');setBusy('');
  if(account?.workspace_id&&account?.user_id&&task?.id)refresh();
  return()=>{generation.current++;inFlight.current=false;};
 },[scope,refresh]);
 const loadMore=async()=>{
  const page=snapshot?.scope===scope?snapshot:null;
  if(inFlight.current||!page?.enabled||!page.next_cursor)return;
  const current=generation.current,cursor=page.next_cursor;inFlight.current=true;setBusy('page');setError('');
  try{const next=checkedPage(await requestJSON(base+'?cursor='+encodeURIComponent(cursor)));
   if(generation.current===current){
    if(!next.enabled){setSnapshot({scope,...next});return;}
    setSnapshot(old=>old?.scope===scope&&old.next_cursor===cursor?{...next,scope,items:[...old.items,...next.items]}:old);
   }
  }catch{if(generation.current===current)setError('加载更多依据失败，请重试或刷新。');}
  finally{if(generation.current===current){inFlight.current=false;setBusy('');}}
 };
 const data=snapshot?.scope===scope?snapshot:null;
 if((!data&&!error)||data?.enabled===false)return null;
 return <section className="media-form advice-evidence-explanation" aria-label="建议依据">
  <div className="advice-evidence-heading"><div><h4>建议依据</h4>
   <p>查看建议参考的能力和现场观测。</p></div>
   <button type="button" disabled={!!busy} onClick={refresh}>{busy==='refresh'?'正在刷新…':'刷新'}</button></div>
  {error&&<p role="alert">{error}</p>}
  {data?.items.length===0&&<p>当前业务暂无建议依据。</p>}
  {data?.items.map((item,index)=><EvidenceItem key={index} item={item} index={index}/>)}
  {data?.next_cursor&&<button type="button" disabled={!!busy} onClick={loadMore}>{busy==='page'?'正在加载…':'加载更多建议'}</button>}
 </section>;
}

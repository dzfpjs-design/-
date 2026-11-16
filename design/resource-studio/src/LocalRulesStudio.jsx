import React,{useEffect,useRef,useState} from 'react';
import {api,json} from './runtime-api';
import {numericObjectOptions} from './mqtt-ui-policy.mjs';
import {newRuleDraft,savedRuleDraft,ruleDefinition,sameRuleDefinition,ruleSamples} from './local-rule-policy.mjs';
import {mergeWorkspacePatch} from './workspace-patches.mjs';

const states={draft:'规则草稿',validated:'已保存本地试算，待确认发布',published:'已发布，定义已冻结',revoked:'已撤销，历史保留'};
export default function LocalRulesStudio({account,workspace,onCommit,onSave,Btn,Field,initialEntity='',suggestions=[],onPublished,onBusy}){
 const [rows,setRows]=useState([]),[objects,setObjects]=useState([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [selected,setSelected]=useState(()=>workspace.localRulesSelection||'new'),[total,setTotal]=useState(0),[cursor,setCursor]=useState(''),[limited,setLimited]=useState(false);
 const latest=useRef(workspace),alive=useRef(true);latest.current=workspace;
 const admin=account.role==='admin',canEdit=['admin','operator'].includes(account.role);
 const row=rows.find(r=>r.id===selected),draft=canEdit?workspace.localRuleDrafts?.[selected]:(row?savedRuleDraft(row):newRuleDraft()),object=objects.find(o=>o.id===draft?.entity_id);
 const frozen=row&&!['draft','validated'].includes(row.status);
 const unchanged=!!row&&!!draft&&sameRuleDefinition(draft,row),checked=unchanged&&row.last_check,locked=busy||!canEdit||frozen||!!draft?.pendingCreate;
 function persist(patch){let next;if(!onCommit(current=>{next=mergeWorkspacePatch(current,latest.current,patch);return next;}))throw Error('编辑未能保存，请先处理页面上方的保存提示。');latest.current=next;}
 function edit(patch){try{persist({localRuleDrafts:{...latest.current.localRuleDrafts,[selected]:{...draft,...patch}}});setNotice('');}catch(e){setError(e.message);}}
 function adopt(record,extra={}){setRows(old=>[record,...old.filter(x=>x.id!==record.id)]);persist({localRulesSelection:record.id,localRuleDrafts:{...latest.current.localRuleDrafts,...(selected==='new'?{new:newRuleDraft(initialEntity,suggestions)}:{}),[record.id]:{...savedRuleDraft(record),...extra}}});setSelected(record.id);}
 async function load(append=false){const res=await api('/local-rules?limit=50'+(append&&cursor?'&cursor='+encodeURIComponent(cursor):'')),saved=await res.json();
  if(!alive.current)return;setRows(old=>append?[...old,...saved.filter(x=>!old.some(o=>o.id===x.id))]:saved);setTotal(Number(res.headers.get('X-Total-Count')));setCursor(res.headers.get('X-Next-Cursor')||'');}
 useEffect(()=>{alive.current=true;(async()=>{
  await load();const [es,cs]=await Promise.all([json('/entities'),json('/capabilities')]);if(!alive.current)return;
  setObjects(numericObjectOptions(es,cs));setLimited(es.length===100||cs.length===100);setLoading(false);
 })().catch(e=>{if(alive.current){setError(e.message);setLoading(false);}});return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(loading||!canEdit)return;try{
  if(!latest.current.localRuleDrafts?.[selected])persist({localRuleDrafts:{...latest.current.localRuleDrafts,[selected]:row?savedRuleDraft(row):newRuleDraft(initialEntity,suggestions)}});
 }catch(e){setError(e.message);}},[loading,selected,row?.id]);
 async function action(fn){setBusy(true);onBusy?.(true);setError('');setNotice('');try{if(canEdit)await onSave();await fn();}catch(e){setError(e.message);}finally{onBusy?.(false);if(alive.current)setBusy(false);}}
 function choose(id){try{if(canEdit)persist({localRulesSelection:id});setSelected(id);setError('');setNotice('');}catch(e){setError(e.message);}}
 async function current(){const record=await json('/local-rules/'+selected);setRows(old=>[record,...old.filter(x=>x.id!==record.id)]);
  if(!sameRuleDefinition(draft,record))throw Error('服务器规则与本页编辑不同，请先保存草稿或明确采用服务器版本。当前输入保留。');return record;}
 async function save(){await action(async()=>{
  let record;
  if(!row){
   const body=draft.pendingCreate||{...ruleDefinition(draft),request_id:draft.requestId};
   // Freeze the exact request before transmission: a lost response is retried with the original UUID and definition.
   edit({pendingCreate:body});await onSave();
   try{record=await json('/local-rules',{method:'POST',body:JSON.stringify(body)});}catch(e){if(e.status>=400&&e.status<500&&e.status!==409)edit({pendingCreate:null});throw e;}
  }else{
   const latestRow=await json('/local-rules/'+row.id);
   if(sameRuleDefinition(draft,latestRow))record=latestRow;
   else{if(draft.baseVersion!==latestRow.version)throw Error('规则已由另一页面更新。当前编辑保留，请对照服务器版本后再保存。');
    record=await json('/local-rules/'+row.id,{method:'PUT',body:JSON.stringify({...ruleDefinition(draft),expected_version:draft.baseVersion})});}
  }
  if(!row)setTotal(n=>n+1);adopt(record,{samples:draft.samples||{}});await onSave();setNotice('规则草稿已保存，可用示例数值试算。未创建观测或操作设备。');
 });}
 async function check(){await action(async()=>{
  let record=await current();const values=ruleSamples(draft);
  if(!record.last_check||JSON.stringify(record.last_check.values)!==JSON.stringify(values))record=await json('/local-rules/'+row.id+'/check',{method:'POST',body:JSON.stringify({expected_version:record.version,values})});
  adopt(record,{publishReason:draft.publishReason||'',confirmed:false});await onSave();setNotice('本地试算已保存。示例仅用于核对条件，不代表现场观测或设备正常。');
 });}
 async function publish(){await action(async()=>{
  if(!draft.confirmed||!draft.publishReason?.trim())throw Error('请填写发布说明，并明确确认使用已核对的条件。');
  let record=await current();if(!record.last_check)throw Error('请先对已保存的当前规则试算。');
  if(record.status!=='published')record=await json('/local-rules/'+row.id+'/publish',{method:'POST',body:JSON.stringify({expected_version:record.version,reason:draft.publishReason,confirm:true})});
  adopt(record);await onSave();if(onPublished)await onPublished(record);setNotice('本地规则已发布，可按业务类型和单位关联传感来源。发布没有自动启动设备或处理现场数据。');
 });}
 function fresh(copy=false){try{const original=latest.current.localRuleDrafts?.new;
  if(original?.pendingCreate){choose('new');setError('还有一条创建请求未确认，请先重试保存原请求。原草稿未覆盖。');return;}
  if(original&&(original.title?.trim()||original.rationale?.trim()||original.rules?.some(r=>r.threshold))&&!confirm('当前还有已填写的新规则草稿。替换它以登记另一条规则？'))return;
  const d=copy&&row?{...savedRuleDraft(row),requestId:crypto.randomUUID(),baseVersion:null,confirmed:false,publishReason:''}:newRuleDraft(initialEntity,suggestions);
  persist({localRulesSelection:'new',localRuleDrafts:{...latest.current.localRuleDrafts,new:d}});setSelected('new');setError('');setNotice(copy?'已复制为新草稿，旧规则和历史关联保留。':'');}catch(e){setError(e.message);}}
 if(loading)return <p role="status">正在读取本地规则与业务对象…</p>;
 return <section className="local-rules-studio" aria-label="本地数值规则">
  <div className="section-title"><h2>本地数值规则</h2><span className="badge">本地运行 · 无云调用</span></div>
  <p>按已确认的业务字段登记阈值。阈值由你依据业务要求确定；试算不使用模型，不改对象状态，也不触发设备操作。</p>
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <div className="gateway-fields"><Field label="选择本地规则"><select value={selected} disabled={busy} onChange={e=>choose(e.target.value)}><option value="new">新规则草稿</option>{rows.map(r=><option key={r.id} value={r.id}>{r.title} · {states[r.status]}</option>)}</select></Field><div className="actions"><Btn disabled={busy} onClick={()=>action(()=>load())}>刷新规则清单</Btn>{cursor&&<Btn disabled={busy} onClick={()=>action(()=>load(true))}>查看更早规则</Btn>}</div></div>
  <p>共 {total} 条规则，清单已读取 {rows.length} 条。{limited?'业务对象选择器目前仅读取最新 100 条对象和能力；更多记录检索尚未开放。':''}</p>
  {selected!=='new'&&!row?<><p>此规则不在已读取清单中，原编辑保留。</p><Btn disabled={busy} onClick={()=>action(async()=>{const r=await json('/local-rules/'+selected);setRows(old=>[r,...old]);})}>读取所选规则</Btn></>:draft&&<>
   {row&&<p className="info">{states[row.status]} · 保存版本 {row.version}{row.context_warning?' · '+row.context_warning:''}</p>}
   {!objects.length&&<p className="info">还没有已发布业务包的数值对象。请先生成并启用业务实例、登记对象。本页不创建占位业务。</p>}
   <fieldset disabled={locked} className="mqtt-editor"><div className="gateway-fields">
    <Field label="规则名称"><input maxLength={200} value={draft.title} onChange={e=>edit({title:e.target.value})}/></Field>
    <Field label="规则对应的业务对象"><select value={draft.entity_id} onChange={e=>edit({entity_id:e.target.value,rules:newRuleDraft().rules,samples:{},confirmed:false})}><option value="">请选择已登记对象</option>{objects.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select></Field>
   </div><Field label="阈值依据与适用说明"><textarea value={draft.rationale} maxLength={2000} onChange={e=>edit({rationale:e.target.value})} placeholder="填写规程、资料来源或经确认的业务要求；说明此阈值适用于什么情况。"/></Field>
   <p>对象用于核对字段定义。规则可复用于同一业务包、同一类型的对象，且单位必须一致。</p>
   {draft.rules.map((r,i)=><div className="local-rule-row" key={r.id}>
    <Field label={'判断字段 '+(i+1)}><select value={r.field} onChange={e=>{const f=object?.fields.find(x=>x.key===e.target.value);edit({rules:draft.rules.map(x=>x.id===r.id?{...x,field:e.target.value,unit:f?.unit||''}:x),confirmed:false});}}><option value="">请选择数值字段</option>{object?.fields.map(f=><option key={f.key} value={f.key}>{f.label}</option>)}</select></Field>
    <Field label={'判断条件 '+(i+1)}><select value={r.operator} onChange={e=>edit({rules:draft.rules.map(x=>x.id===r.id?{...x,operator:e.target.value}:x),confirmed:false})}><option value="gt">大于</option><option value="lt">小于</option></select></Field>
    <Field label={'阈值 '+(i+1)}><input inputMode="decimal" value={r.threshold} onChange={e=>edit({rules:draft.rules.map(x=>x.id===r.id?{...x,threshold:e.target.value}:x),confirmed:false})}/></Field>
    <Field label={'单位 '+(i+1)}><input value={r.unit} maxLength={80} readOnly={object?.fields.find(f=>f.key===r.field)?.unit!==undefined} onChange={e=>edit({rules:draft.rules.map(x=>x.id===r.id?{...x,unit:e.target.value}:x),confirmed:false})}/></Field>
    <Btn disabled={draft.rules.length===1} aria-label={'移除条件 '+(i+1)} onClick={()=>edit({rules:draft.rules.filter(x=>x.id!==r.id),confirmed:false})}>移除</Btn>
   </div>)}
   <Btn disabled={draft.rules.length>=32} onClick={()=>edit({rules:[...draft.rules,...newRuleDraft().rules],confirmed:false})}>添加数值条件</Btn></fieldset>
   <div className="actions">{!frozen&&<Btn primary disabled={busy||!canEdit||!objects.length} onClick={save}>{draft.pendingCreate?'重试保存原请求':'保存规则草稿'}</Btn>}
    {row&&canEdit&&<Btn disabled={busy} onClick={()=>{if(confirm('采用服务器已保存的规则替换本页编辑？当前未提交输入将被替换。'))action(async()=>{adopt(await json('/local-rules/'+row.id));await onSave();});}}>采用服务器规则</Btn>}
    {frozen&&<Btn disabled={busy||!canEdit} onClick={()=>fresh(true)}>复制为新草稿</Btn>}{selected!=='new'&&<Btn disabled={busy||!canEdit} onClick={()=>fresh()}>登记另一条规则</Btn>}
   </div>{draft.pendingCreate&&<p className="info">原创建请求正在等待确认，输入已保留。请重试同一请求；不要另建一条规则。</p>}
   {row&&<section className="local-rule-check"><h3>示例数值试算</h3><p>先保存规则，再填写示例数值。试算结果会保存，可在刷新或服务重启后核对。</p>
    {!frozen&&<fieldset disabled={busy||!canEdit||!unchanged} className="mqtt-editor"><div className="gateway-fields">{[...new Set(draft.rules.map(r=>r.field))].filter(Boolean).map(f=><Field key={f} label={'示例值：'+(object?.fields.find(x=>x.key===f)?.label||f)}><input inputMode="decimal" value={draft.samples?.[f]||''} onChange={e=>edit({samples:{...draft.samples,[f]:e.target.value},confirmed:false})}/></Field>)}</div><Btn disabled={!unchanged||row.context_available===false} onClick={check}>试算并核对规则</Btn></fieldset>}
    {!unchanged&&<p className="info">本页有未保存修改，原试算仅对应服务器旧规则。保存后需要重新试算。</p>}
    {row.last_check?<div className="local-rule-results"><p>已保存的试算 · {new Date(row.last_check.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</p>{row.last_check.results.map(r=><p key={r.id}><strong>{object?.fields.find(f=>f.key===r.field)?.label||r.field}</strong>：示例 {r.value} {r.unit||'（未声明单位）'}，{r.operator==='gt'?'大于':'小于'} {r.threshold} → <strong>{r.matched?'触发候选线索':'未触发此条件'}</strong></p>)}<p>这是示例条件结果，不是现场状态，也不证明对象安全。</p></div>:<p>当前保存版本还没有试算结果。</p>}
   </section>}
   {row&&!frozen&&<section className="local-rule-publish"><h3>确认发布</h3><Field label="发布说明"><textarea maxLength={2000} value={draft.publishReason||''} disabled={busy||!admin} onChange={e=>edit({publishReason:e.target.value})}/></Field>
    <label className="check-row"><input type="checkbox" checked={!!draft.confirmed} disabled={busy||!admin||!checked} onChange={e=>edit({confirmed:e.target.checked})}/>我已核对业务字段、单位、阈值依据和保存的试算结果</label>
    {!admin&&<p>发布须由管理员确认。</p>}<Btn primary disabled={busy||!admin||!checked||!draft.confirmed||!draft.publishReason?.trim()||row.context_available===false} onClick={publish}>发布本地规则</Btn>
    <p>发布后冻结当前定义；修改须复制为新草稿，并重新核对，不覆盖已有来源关联。</p></section>}
  </>}
 </section>;
}

import React,{useEffect,useRef,useState} from 'react';
import {api,json} from './runtime-api';
import {localRuleOptions} from './mqtt-ui-policy.mjs';
import {compatibleRule} from './local-rule-policy.mjs';
import {mergeWorkspacePatch} from './workspace-patches.mjs';
import {associationKey,newAssociationDraft,savedAssociationDraft,associationRequest,associationStatusRequest,sameAssociation,mayFinishOriginDraft,associationPendingConfirmed,rejectedAssociationDraft,savedAssociationTransport,associationHeading,associationState,associationDestination,associationDestinationIssue} from './mqtt-association-policy.mjs';
import LocalRulesStudio from './LocalRulesStudio';
import './mqtt-association.css';

export default function MqttAssociationsPanel({gateway,account,workspace,onCommit,onSave,Btn,Field,onBusy,onRuleBusy,onChanged,onRefreshSource}){
 const [rows,setRows]=useState([]),[objects,setObjects]=useState([]),[caps,setCaps]=useState([]),[loading,setLoading]=useState(true);
 const [objectQuery,setObjectQuery]=useState(''),[objectCursor,setObjectCursor]=useState(''),[objectTotal,setObjectTotal]=useState(0),[ruleQuery,setRuleQuery]=useState(''),[ruleCursor,setRuleCursor]=useState(''),[ruleTotal,setRuleTotal]=useState(0),[choicesBusy,setChoicesBusy]=useState(false);
 const [selected,setSelected]=useState('new'),[history,setHistory]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[ruleBusy,setRuleBusy]=useState(false),[showRules,setShowRules]=useState(false);
 const [waiting,setWaiting]=useState(false),requestController=useRef(null);
 const latest=useRef(workspace),alive=useRef(true),epoch=useRef(0),objectEpoch=useRef(0),ruleEpoch=useRef(0),choicePending=useRef(0),busyReporter=useRef(onBusy);latest.current=workspace;busyReporter.current=onBusy;
 const admin=account.role==='admin',locked=busy||ruleBusy||choicesBusy,row=rows.find(r=>r.id===selected),key=associationKey(gateway.id,selected);
 const stored=workspace.mqttBindingDrafts?.[key];
 const draft=admin?stored:(row?savedAssociationDraft(gateway,row):null),object=objects.find(o=>o.id===draft?.entity_id);
 const destination=associationDestination(object),destinationIssue=associationDestinationIssue(draft,object);
 const measurements=Object.values(gateway.configuration.mapping),rules=localRuleOptions(caps).filter(c=>compatibleRule(c,object,draft?.field_links,measurements));
 const currentRules=()=>localRuleOptions(caps).filter(c=>compatibleRule(c,objects.find(o=>o.id===latest.current.mqttBindingDrafts?.[key]?.entity_id),latest.current.mqttBindingDrafts?.[key]?.field_links,measurements));
 function persist(patch){let next;if(!onCommit(current=>{next=mergeWorkspacePatch(current,latest.current,patch);return next;}))throw Error('编辑尚未保存，请先处理页面上方的保存提示。');latest.current=next;}
 function journal(draftKey,value){persist({mqttBindingDrafts:{...latest.current.mqttBindingDrafts,[draftKey]:value}});}
 function edit(patch){try{journal(key,{...latest.current.mqttBindingDrafts?.[key],...patch});setNotice('');}catch(e){setError(e.message);}}
 function chooseObject(identity){const selected=objects.find(value=>value.id===identity),target=associationDestination(selected);edit({entity_id:identity,instance_release_id:target.available?target.instance_release_id:null,field_links:{},extractor_id:''});}
 function valid(ticket){return alive.current&&epoch.current===ticket;}
 async function load(ticket=epoch.current){const saved=await json('/mqtt/gateways/'+gateway.id+'/routes');if(valid(ticket))setRows(saved);return saved;}
 function normalizeObject(o){return {...o,instance_name:o.instance?.instance_name||'',instance_current:associationDestination(o).available};}
 function choiceStarted(){choicePending.current++;setChoicesBusy(true);}
 function choiceFinished(){choicePending.current=Math.max(0,choicePending.current-1);if(alive.current&&choicePending.current===0)setChoicesBusy(false);}
 useEffect(()=>{busyReporter.current?.(locked);},[locked]);
 useEffect(()=>()=>busyReporter.current?.(false),[]);
 useEffect(()=>()=>requestController.current?.abort(),[]);
 async function sendSavedRequest(path,method,request,ticket){const controller=new AbortController();requestController.current=controller;setWaiting(true);
  try{const transport=savedAssociationTransport(path,method,request,controller.signal);return await json(transport.path,transport.options);}
  finally{if(requestController.current===controller){requestController.current=null;if(valid(ticket))setWaiting(false);}}
 }
 async function choices(ticket=epoch.current,append=false){const request=++objectEpoch.current;
  const response=await api('/mqtt/domain-options?limit=50&q='+encodeURIComponent(objectQuery)+(append&&objectCursor?'&after='+encodeURIComponent(objectCursor):''));
  const found=(await response.json()).map(normalizeObject);if(!valid(ticket)||objectEpoch.current!==request)return;
  setObjects(old=>append?[...old,...found.filter(x=>!old.some(o=>o.id===x.id))]:[...old.filter(o=>o.id===latest.current.mqttBindingDrafts?.[key]?.entity_id&&!found.some(x=>x.id===o.id)),...found]);setObjectTotal(Number(response.headers.get('X-Total-Count')));setObjectCursor(response.headers.get('X-Next-Cursor')||'');
 }
 async function ruleChoices(entityID,ticket=epoch.current,append=false){const request=++ruleEpoch.current;if(!entityID){setCaps([]);setRuleCursor('');setRuleTotal(0);return;}
  const response=await api('/mqtt/rule-options?entity_id='+encodeURIComponent(entityID)+'&limit=50&q='+encodeURIComponent(ruleQuery)+(append&&ruleCursor?'&after='+encodeURIComponent(ruleCursor):''));
  const found=await response.json();if(!valid(ticket)||ruleEpoch.current!==request)return;
  setCaps(old=>append?[...old,...found.filter(x=>!old.some(o=>o.id===x.id))]:[...old.filter(c=>c.id===latest.current.mqttBindingDrafts?.[key]?.extractor_id&&!found.some(x=>x.id===c.id)),...found]);setRuleTotal(Number(response.headers.get('X-Total-Count')));setRuleCursor(response.headers.get('X-Next-Cursor')||'');
 }
 useEffect(()=>{alive.current=true;const ticket=++epoch.current;setLoading(true);setBusy(false);setRows([]);setSelected('new');setHistory(null);setError('');setNotice('');
  Promise.all([load(ticket),choices(ticket)]).then(()=>{if(valid(ticket))setLoading(false);}).catch(e=>{if(valid(ticket)){setError(e.message);setLoading(false);}});
  return()=>{alive.current=false;epoch.current++;objectEpoch.current++;ruleEpoch.current++;};
 },[gateway.id,gateway.version]);
 useEffect(()=>{if(loading||!admin||selected!=='new'&&!row||latest.current.mqttBindingDrafts?.[key])return;
  try{journal(key,row?savedAssociationDraft(gateway,row):newAssociationDraft(gateway,latest.current.mqttBindingDrafts?.[gateway.id+':binding']));}catch(e){setError(e.message);}
 },[loading,selected,row?.id,key,admin]);
 useEffect(()=>{const entityID=draft?.entity_id,extractorID=draft?.extractor_id,ticket=epoch.current;let cancelled=false;choiceStarted();
  (async()=>{if(entityID){const chosen=normalizeObject(await json('/mqtt/domain-options/'+encodeURIComponent(entityID)));if(cancelled||!valid(ticket))return;setObjects(old=>[chosen,...old.filter(o=>o.id!==chosen.id)]);}
   await ruleChoices(entityID,ticket);if(extractorID){const chosen=await json('/mqtt/rule-options/'+encodeURIComponent(extractorID)+'?entity_id='+encodeURIComponent(entityID));if(!cancelled&&valid(ticket))setCaps(old=>[chosen,...old.filter(c=>c.id!==chosen.id)]);}
  })().catch(e=>{if(!cancelled&&valid(ticket))setError(e.message+' 原关联与草稿保留。');}).finally(choiceFinished);
  return()=>{cancelled=true;ruleEpoch.current++;};
 },[draft?.entity_id,draft?.extractor_id,gateway.id]);
 async function action(fn){if(locked)return;setBusy(true);setError('');setNotice('');const ticket=epoch.current;
  try{if(admin)await onSave();await fn(ticket);}catch(e){if(valid(ticket))setError(e.message);}finally{if(valid(ticket))setBusy(false);}
 }
 function choose(id){if(locked)return;setSelected(id);setHistory(null);setError('');setNotice('');}
 async function save(){await action(async ticket=>{
  const input=latest.current.mqttBindingDrafts?.[key];if(!input)throw Error('关联草稿尚未保存，请稍后重试。');
  const body=associationRequest(input,gateway,row,objects,currentRules()),path=input.pendingPath||(row?'/mqtt/routes/'+row.id:'/mqtt/gateways/'+gateway.id+'/routes'),method=input.pendingMethod||(row?'PUT':'POST'),originKey=input.pendingOriginKey||key;
  // Commit the exact UUID and request to setup CAS before creating any new association version.
  journal(key,{...input,pending:JSON.parse(JSON.stringify(body)),pendingPath:path,pendingMethod:method,pendingOriginKey:originKey});await onSave();
  let saved;try{saved=await sendSavedRequest(path,method,body,ticket);}catch(e){
   const rejected=rejectedAssociationDraft(latest.current.mqttBindingDrafts?.[key],e);
   if(rejected&&valid(ticket)){journal(key,rejected);await onSave();await load(ticket);setNotice('本次关联修改未提交。你的输入与原被拒绝请求均已保存，请对照当前版本后重新确认。');}
   throw e;
  }
  if(!valid(ticket))return;
  const savedKey=associationKey(gateway.id,saved.id);
  const matchesRequest=sameAssociation(body,saved),settledDraft=matchesRequest?savedAssociationDraft(gateway,saved):{...input,requestId:crypto.randomUUID(),pending:null,pendingPath:null,pendingMethod:null,pendingOriginKey:null};
  setRows(old=>[saved,...old.filter(x=>x.id!==saved.id)]);setSelected(saved.id);setHistory(null);
  setNotice('关联已由服务器保存。正在同步编辑草稿；同步失败时仍可核对这条已保存记录。');
  // Show the committed row immediately. Keep the original transport operation frozen until draft CAS acknowledges it.
  journal(savedKey,{...settledDraft,rejectedRequests:input.rejectedRequests||[],pending:body,pendingPath:path,pendingMethod:method,pendingOriginKey:originKey,confirmedRequest:body});
  await onSave();if(!valid(ticket))return;
  journal(savedKey,{...settledDraft,rejectedRequests:input.rejectedRequests||[],confirmedRequest:body});
  if(mayFinishOriginDraft(latest.current.mqttBindingDrafts,originKey,savedKey,body))journal(originKey,newAssociationDraft(gateway));
  await onSave();if(!valid(ticket))return;
  setNotice(!matchesRequest?'原保存请求已确认，但当前关联后来又被修改。你的输入已保留，请对照服务器当前版本再决定。':body.include_existing?'关联已保存为新版本。已明确请求处理尚未完成的既有原件；完成的历史结果保留。':'关联已保存。以后收到的数据使用此版本，之前原件和已完成结果保留。');
  await onChanged?.();
 });}
 async function setStatus(target){await action(async ticket=>{
  const statusKey=gateway.id+':route-status:'+target.id,previous=latest.current.mqttBindingDrafts?.[statusKey];
  const body=previous?.pending||associationStatusRequest(target,target.status==='active'?'paused':'active');
  journal(statusKey,{...previous,pending:body});await onSave();
  let saved;try{saved=await sendSavedRequest('/mqtt/routes/'+target.id+'/status','POST',body,ticket);}catch(e){
   const rejected=rejectedAssociationDraft(latest.current.mqttBindingDrafts?.[statusKey],e);
   if(rejected&&valid(ticket)){journal(statusKey,rejected);await onSave();await load(ticket);setNotice('本次暂停或恢复未提交，原请求依据已保留。已重新读取当前关联，请核对后按当前需要确认。');}
   throw e;
  }if(!valid(ticket))return;
  setRows(old=>old.map(x=>x.id===saved.id?saved:x));
  setNotice(saved.status==='paused'?'此对象关联已由服务器暂停，原件与历史记录保留。正在同步页面草稿。':'此对象关联已由服务器恢复。默认只处理以后收到的数据，正在同步页面草稿。');
  journal(statusKey,{...previous,pending:body,confirmedRequest:body});await onSave();if(!valid(ticket))return;
  journal(statusKey,{...previous,pending:null,confirmedRequest:body});const editKey=associationKey(gateway.id,target.id),currentDraft=latest.current.mqttBindingDrafts?.[editKey];
  if(currentDraft&&!currentDraft.pending&&currentDraft.baseVersion===target.version&&sameAssociation(currentDraft,target))journal(editKey,{...currentDraft,baseVersion:saved.version});
  await onSave();if(!valid(ticket))return;
  setNotice(saved.status!==body.status?'原暂停或恢复请求已确认，但服务器当前状态后来又有修改。请以清单当前状态为依据重新核对。':saved.status==='paused'?'此对象关联已暂停。其他关联继续接收，原件与历史处理记录保留。':'此对象关联已恢复。默认只处理以后收到的数据，不补做历史。');await onChanged?.();
 });}
 async function revisions(target){await action(async ticket=>{const records=await json('/mqtt/routes/'+target.id+'/revisions');if(valid(ticket))setHistory({routeID:target.id,records});});}
 async function adopt(){await action(async ticket=>{const saved=await load(ticket),current=saved.find(r=>r.id===selected);if(!current)throw Error('此关联不在当前可见列表中，原编辑保留。');
  if(latest.current.mqttBindingDrafts?.[key]?.pending)throw Error('原保存请求尚未确认。请先重试同一请求，再采用服务器关联。');
  if(valid(ticket)){journal(key,{...savedAssociationDraft(gateway,current),baseHash:gateway.config_hash,rejectedRequests:latest.current.mqttBindingDrafts?.[key]?.rejectedRequests||[]});await onSave();setNotice('已按当前来源采用服务器关联重新编辑。请核对主题、字段和单位，保存修改会另建版本。');}
 });}
 async function reconcileEdit(){await action(async ticket=>{
  const input=latest.current.mqttBindingDrafts?.[key];if(input?.pending)throw Error('原请求结果尚未确认，请先重试原请求。');
  if(!row){await onRefreshSource?.();if(valid(ticket))setNotice('已重新读取来源配置。请按当前来源核对主题、字段和规则后再确认新增关联。');return;}
  const saved=await load(ticket),current=saved.find(r=>r.id===row.id);if(!current)throw Error('此关联不在当前可见列表中，原编辑保留。');
  if(valid(ticket)){journal(key,{...input,baseVersion:current.version,baseHash:gateway.config_hash,requestId:crypto.randomUUID()});await onSave();setNotice('你的修改已保留，并按服务器当前版本重新核对。请检查字段、规则和历史处理范围后再保存。');}
 });}
 async function confirmDestination(){await action(async ticket=>{
  const input=latest.current.mqttBindingDrafts?.[key];if(!input||input.pending)throw Error('原请求尚待确认，不能自动改签业务目的地。请先核对原保存回执。');
  const chosen=normalizeObject(await json('/mqtt/domain-options/'+encodeURIComponent(input.entity_id))),target=associationDestination(chosen);if(!target.available)throw Error(target.reason);
  if(!valid(ticket))return;setObjects(old=>[chosen,...old.filter(value=>value.id!==chosen.id)]);journal(key,{...input,instance_release_id:target.instance_release_id,requestId:crypto.randomUUID(),include_existing:false});await onSave();setNotice('已明确采用服务器当前业务目的地，字段和规则输入保留。历史处理范围已重置为只处理以后数据；如需处理旧原件，请重新明确勾选后确认保存。设备控制授权仍各自确认。');
 });}
 function statusLabel(target){const pending=workspace.mqttBindingDrafts?.[gateway.id+':route-status:'+target.id]?.pending;return pending?'重试'+(pending.status==='paused'?'暂停':'恢复')+'原请求':target.status==='active'?'暂停此关联':'恢复此关联';}
 if(loading)return <p role="status">正在读取对象关联与已发布的本地规则…</p>;
 return <section className="mqtt-associations" aria-label="业务对象关联">
  <div className="association-heading"><h3>一份来源数据，供多个业务对象使用</h3><Btn disabled={locked} onClick={()=>action(async ticket=>{await Promise.all([load(ticket),choices(ticket)]);})}>刷新关联清单</Btn></div>
  <p>不用复制连接。每个对象单独确认本地规则和字段；暂停或修改一条关联，不替换其他对象的处理依据。</p>
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {waiting&&<div className="info" role="status"><p>原请求已保存，正在等待服务器结果。停止等待只结束本页等待，后台可能仍会保存；之后请重试原请求核对。</p><Btn onClick={()=>requestController.current?.abort()}>停止等待，保留原请求</Btn></div>}
  {!admin&&<p className="info">当前可以查看关联与历史。新增、修改、暂停和恢复由管理员确认。</p>}
  <div className="mqtt-association-list">{!rows.length?<p>此来源还没有业务对象关联。可先接收原件，生成业务后再确认关联。</p>:rows.map(target=><article className={'mqtt-association-card'+(selected===target.id?' active':'')} key={target.id}>
   <div className="association-heading"><strong>{associationHeading(target)}</strong><span className="badge">第 {target.revision} 版 · {target.status==='paused'?'已暂停':'已确认'}</span></div>
   <p>{target.topic} · 本地规则：{target.rule_name||'已发布本地规则'}</p>
   <p>{Object.entries(target.field_links||{}).map(([measurement,field])=>{const option=objects.find(o=>o.id===target.entity_id)?.fields.find(f=>f.key===field);return measurement+' → '+(option?.label||field)+'（'+(target.field_units?.[field]||target.field_units?.[measurement]||'单位未声明')+'）';}).join('；')}</p>
   <p className="info">{associationState(target)}</p><div className="actions"><Btn disabled={locked} onClick={()=>choose(target.id)}>{admin?'修改此关联':'查看此关联'}</Btn><Btn disabled={locked} onClick={()=>revisions(target)}>查看旧版本</Btn><Btn disabled={locked||!admin||(target.status==='paused'&&target.needs_review&&!workspace.mqttBindingDrafts?.[gateway.id+':route-status:'+target.id]?.pending)} onClick={()=>setStatus(target)}>{statusLabel(target)}</Btn></div>
  </article>)}</div>
  {history&&<section className="mqtt-association-history" aria-label="关联版本历史"><div className="association-heading"><h4>保存过的关联版本</h4><Btn disabled={locked} onClick={()=>setHistory(null)}>收起旧版本</Btn></div><p>旧版本只供核对，历史处理结果继续保留原版本的对象和规则依据。</p>
   {history.records.map(h=><article className="mqtt-association-card" key={h.binding_id||h.id+'-'+h.revision}><strong>第 {h.revision} 版 · {associationHeading(h)}</strong><p>本地规则：{h.rule_name||'已发布本地规则'} · 主题：{h.topic}</p><p>{Object.entries(h.field_links||{}).map(([m,f])=>m+' → '+(objects.find(o=>o.id===h.entity_id)?.fields.find(x=>x.key===f)?.label||f)).join('；')}</p></article>)}</section>}
  <div className="association-toolbar"><h4>{row?(admin?'修改所选关联':'所选关联'):'新增一个对象关联'}</h4><Btn disabled={locked||!admin} onClick={()=>choose('new')}>新增并列关联</Btn></div>
  {selected!=='new'&&!row?<p className="error">所选关联已不在可见清单中，编辑草稿保留。请刷新清单核对。</p>:draft?<section className="mqtt-association-editor">
   {!objects.length&&<p className="info">还没有可用的业务数值对象。请先完成业务配置、生成实例并登记对象。</p>}
   <fieldset className="mqtt-editor" disabled={locked||!admin||!!draft.pending||!objects.length}>
    <div className="gateway-fields"><Field label="关联数据主题"><select value={draft.topic||''} onChange={e=>edit({topic:e.target.value})}><option value="">请选择来源已确认的主题</option>{Object.entries(gateway.configuration.topics).map(([topic,device])=><option key={topic} value={topic}>{device} · {topic}</option>)}</select></Field>
     <Field label="业务实例与对象"><select aria-label="关联业务实例与对象" value={draft.entity_id||''} onChange={e=>chooseObject(e.target.value)}><option value="">请选择已有对象</option>{objects.map(o=><option key={o.id} value={o.id} disabled={!o.instance_current}>{o.instance_name?o.instance_name+' · ':''}{o.label}{!o.instance_current?'（实例当前不可用）':''}</option>)}</select></Field></div>
    <div className="gateway-fields"><Field label="按对象名称查找"><input maxLength={100} value={objectQuery} onChange={e=>{setObjectQuery(e.target.value);setObjectCursor('');}} placeholder="输入对象名称或已登记编号"/></Field><div className="actions"><Btn disabled={locked} onClick={()=>action(ticket=>choices(ticket))}>查找对象</Btn>{objectCursor&&<Btn disabled={locked} onClick={()=>action(ticket=>choices(ticket,true))}>读取更多对象</Btn>}</div></div><p>当前查询共 {objectTotal} 个对象；清单已读 {objects.length} 个（包括所选对象）。</p>
    {measurements.map(m=><Field key={m.attribute} label={m.attribute+' → 业务字段（单位 '+(m.unit||'未声明')+'）'}><select value={draft.field_links?.[m.attribute]||''} onChange={e=>edit({field_links:{...draft.field_links,[m.attribute]:e.target.value},extractor_id:''})}><option value="">请确认对应字段</option>{object?.fields.filter(f=>f.unit===undefined||(f.unit||null)===(m.unit||null)).map(f=><option key={f.key} value={f.key}>{f.label}</option>)}</select></Field>)}
    <Field label="已发布的本地数值规则"><select value={draft.extractor_id||''} onChange={e=>edit({extractor_id:e.target.value})}><option value="">请选择已核对的规则</option>{rules.map(c=><option key={c.id} value={c.id}>{c.data.description||c.name} · {c.release_version}</option>)}</select></Field>
    <div className="gateway-fields"><Field label="按规则名称查找"><input maxLength={100} value={ruleQuery} onChange={e=>{setRuleQuery(e.target.value);setRuleCursor('');}} placeholder="输入本地规则名称"/></Field><div className="actions"><Btn disabled={locked||!object} onClick={()=>action(ticket=>ruleChoices(object.id,ticket))}>查找规则</Btn>{ruleCursor&&<Btn disabled={locked||!object} onClick={()=>action(ticket=>ruleChoices(object.id,ticket,true))}>读取更多规则</Btn>}</div></div><p>当前查询共 {ruleTotal} 条规则；选择框只显示与字段和单位一致的规则。</p>
    {!rules.length&&<p>没有与对象、字段及单位一致的已发布本地规则。可在下方登记、试算并发布。</p>}
    <label className="check-row"><input type="checkbox" checked={draft.include_existing===true} onChange={e=>edit({include_existing:e.target.checked})}/>同时处理之前收到、且尚未完成此对象关联处理的原件。已完成的历史结果保留，不自动重新计算。</label>
   </fieldset>
   <p>默认只用于以后收到的数据。修改后另存为新版本；来源接收和设备控制权限保持各自确认。</p>
   {object?.instance&&<p>已读取的业务目的地：{object.instance.instance_name||'业务实例'} · 第 {object.instance.instance_release_number??'待核实'} 版。{draft.instance_release_id?'草稿保留原确认目的地；版本变化时须重新明确核对。':'草稿尚未确认实例版本。'}</p>}
   {draft.entity_id&&destinationIssue&&<div className="info" role="status"><p>{destinationIssue}</p>{!draft.pending&&destination.available&&<Btn disabled={locked||!admin} onClick={confirmDestination}>核对并采用当前业务目的地</Btn>}</div>}
   {(draft.rejectedRequests?.length>0||row&&draft.baseVersion!==row.version)&&<div className="info"><p>{draft.rejectedRequests?.length?'之前有 '+draft.rejectedRequests.length+' 次保存因版本冲突被明确拒绝。原请求与编辑均保留。':'服务器当前保存版本与本页编辑依据不同。你的输入保留。'}</p><p>{row?'本页编辑依据保存版本 '+draft.baseVersion+'；服务器当前保存版本 '+row.version+'。':'请先刷新当前来源配置，再重新核对。'}</p><Btn disabled={locked||!admin||!!draft.pending} onClick={()=>{if(confirm('保留你的字段和规则修改，以服务器当前保存版本为依据重新核对？不会自动保存关联或处理原件。'))reconcileEdit();}}>保留我的修改，按当前版本重新核对</Btn></div>}
   {draft.pending&&<p className="info">{associationPendingConfirmed(draft)?'关联已由服务器保存，编辑草稿同步尚未完成。':'保存结果尚未确认。'}UUID 与内容已保存。重试使用同一请求，输入暂时锁定，避免重复关联。{object?.instance&&(!draft.pending.instance_release_id||draft.pending.instance_release_id!==object.instance.instance_release_id)&&'原请求没有当前目的地标记，或固定于原实例版本，不能自动改签。可重试原请求核对保存回执；采用新目的地须在原结果核对后另行明确确认。'}</p>}
   <div className="actions"><Btn primary disabled={locked||!admin||(!draft.pending&&(!objects.length||!draft.topic||!object||!draft.extractor_id||!!destinationIssue))} onClick={save}>{draft.pending?'重试保存原请求':row?'保存为新关联版本':'确认新增关联'}</Btn>{row&&<Btn disabled={locked||!admin||!!draft.pending} onClick={()=>{if(confirm('用服务器当前关联替换本页未提交输入？历史处理记录保留。'))adopt();}}>采用服务器关联重新编辑</Btn>}</div>
   {draft.baseHash!==gateway.config_hash&&<><p className="error">来源约定已改变。请采用当前关联重新核对；原输入不会自动覆盖。</p>{!row&&<Btn disabled={locked||!admin||!!draft.pending} onClick={()=>{if(confirm('按当前来源重新选择主题、字段和规则？业务对象保留，未提交的旧字段对应将替换。'))edit({baseHash:gateway.config_hash,topic:'',field_links:{},extractor_id:''});}}>按当前来源重新核对草稿</Btn>}</>}
  </section>:admin?<p>正在准备关联草稿…</p>:<p>请选择清单中的关联查看。</p>}
  <Btn disabled={locked} onClick={()=>setShowRules(v=>!v)}>{showRules?'收起本地规则管理':'打开本地规则管理'}</Btn>
  {showRules&&<LocalRulesStudio account={account} workspace={workspace} onCommit={onCommit} onSave={onSave} Btn={Btn} Field={Field} initialEntity={draft?.entity_id||''}
   suggestions={measurements.filter(m=>draft?.field_links?.[m.attribute]).map(m=>({field:draft.field_links[m.attribute],unit:m.unit}))}
   onPublished={async()=>{await ruleChoices(draft?.entity_id);}} onBusy={value=>{setRuleBusy(value);onRuleBusy?.(value);}}/>}
 </section>;
}

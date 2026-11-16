import {evidencePath} from './evidence-path.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {businessNames,changeBusinesses} from '../src/business-schema.js';
import {defaults,normalize} from '../src/automation-schema.js';
import {withoutSecrets} from '../src/catalog.js';
import {stableStringify} from '../src/content-signature.js';
import {definiteReviewConflict,readReviewJournal,saveReviewRequest,rejectReviewRequest,reviewJournalKey,withReviewJournalLock,sameReviewRequest,checkReviewReceipt} from '../src/automation-review-contract.mjs';
import {prepareAutomationPersistence,materializeEmptyReviewPolicy,automationConfigurationIssue,runtimeAutomationDisplay} from '../src/automation-policy-contract.mjs';
import {prepareCurrentAutomationReview} from '../src/prepare-automation-review.mjs';
const before=JSON.parse(await readFile(evidencePath('safe-automation-v22/browser/before-usable-ui.json'),'utf8'));
const after=JSON.parse(await readFile(evidencePath('safe-automation-v22/browser/after-business-list-recovery.json'),'utf8'));
const config=snapshot=>{const data=snapshot.records.setup_drafts[0].data;return typeof data==='string'?JSON.parse(data):data;};
const B=config(before),empty=config(after),name='资料补核';
const rows=()=>[...businessNames(B).map(name=>({original:name,name})),{name}];
const source=await readFile(new URL('../src/AutomationStudio.jsx',import.meta.url),'utf8');
function actualPersist(workspace,next){let commits=[];const text=source.slice(source.indexOf('function persist(next)'),source.indexOf('\n function change(next)'));
 const fn=new Function('runtime','writable','reviewPending','reviewLocked','liveWorkspace','businesses','normalize','withoutSecrets','prepareAutomationPersistence','stableStringify','onCommitWorkspace','setSaveError','setNotice','KEY',text+';return persist;');
 const persist=fn(true,true,null,false,{current:workspace},businessNames(workspace),normalize,withoutSecrets,prepareAutomationPersistence,stableStringify,value=>{commits.push(value);return true;},()=>{},()=>{},'unused');
 const accepted=persist(next);return {accepted,commits};
}
test('actual B new business keeps complete raw manual and six suggestion rules through the next navigation save',()=>{
 const next=changeBusinesses(B,rows()),safe=defaults([name],true).policies[name];
 assert.deepEqual(next.automation.policies[name],safe);
 const nextNavigation=structuredClone(next);assert.deepEqual(nextNavigation.automation.policies[name],safe);
 for(const [key,policy]of Object.entries(B.automation.policies))assert.deepEqual(next.automation.policies[key],policy);
});
test('navigation leaves actual server17 empty policy intact; explicit review materializes just this business',()=>{
 assert.deepEqual(empty.automation.policies[name].rules,{});
 const visible=normalize(empty.automation,businessNames(empty),empty.businessAliases,true),result=actualPersist(empty,visible);
 assert.equal(result.accepted,true);assert.equal(result.commits.length,0);
 const confirmed=materializeEmptyReviewPolicy(empty.automation,visible,businessNames(empty),empty.businessAliases,name);
 assert.equal(confirmed.changed,true);assert.deepEqual(confirmed.automation.policies[name].triggers,['人工发起']);assert.equal(Object.keys(confirmed.automation.policies[name].rules).length,6);
 for(const key of Object.keys(empty.automation.policies))if(key!==name)assert.deepEqual(confirmed.automation.policies[key],empty.automation.policies[key]);
});
test('reviewing an empty aliased policy leaves other confirmations and unknown fields intact',()=>{
 const saved={version:1,extension:{keep:true},policies:{old:{rules:{},custom:'kept',flow_review:{status:'needs_review'}},other:{...defaults(['other'],true).policies.other,flow_review:{status:'confirmed',hash:'original'}}}};
 const aliases={renamed:'old'},names=['renamed','other'],visible=normalize(saved,names,aliases,true),result=materializeEmptyReviewPolicy(saved,visible,names,aliases,'renamed');
 assert.equal(result.changed,true);assert.deepEqual(result.automation.policies.old,saved.policies.old);assert.deepEqual(result.automation.policies.other,saved.policies.other);assert.deepEqual(result.automation.extension,saved.extension);
 assert.equal(result.automation.policies.renamed.custom,'kept');assert.equal(Object.keys(result.automation.policies.renamed.rules).length,6);assert.deepEqual(result.automation.policies.renamed.flow_review,saved.policies.old.flow_review);
 const modified=structuredClone(visible);modified.policies.renamed.rules.write.mode='自动运行';assert.throws(()=>materializeEmptyReviewPolicy(saved,modified,names,aliases,'renamed'),/页面规则与服务器不同/);
 const legacy=structuredClone(saved);legacy.policies.old.timezone='UTC';const masked=normalize(legacy,names,aliases,true);assert.throws(()=>materializeEmptyReviewPolicy(legacy,masked,names,aliases,'renamed'),/已保存的自动化设置与当前页面显示不同/);assert.equal(legacy.policies.old.timezone,'UTC');
});
test('explicit review waits for saved rules and refuses changed business or failed persistence',async()=>{
 const workspace={businesses:['A','B'],automation:{version:1,policies:{A:{rules:{},unknown:'kept'},B:{...defaults(['B'],true).policies.B,flow_review:{status:'confirmed',hash:'preserve'}}}}};
 const state={workspace:structuredClone(workspace),revision:10,business:'A',actor:'u',pending:null,commits:0};
 const ctx=()=>({workspaceId:'w',userId:state.actor,username:'reviewer',business:state.business,names:['A','B'],aliases:{},visible:normalize(state.workspace.automation,['A','B'],{},true)});
 const readDraft=async()=>({revision:state.revision,configuration:structuredClone(state.workspace)});
 const commit=change=>{state.commits++;state.pending=change(structuredClone(state.workspace));return true;};
 const flush=async()=>{if(state.pending){state.workspace=state.pending;state.pending=null;state.revision++;state.workspace.automation.policies.A.flow_review={status:'needs_review'};}return state.revision;};
 assert.equal(await prepareCurrentAutomationReview({context:ctx,flush,readDraft,commit}),11);assert.equal(state.commits,1);assert.equal(Object.keys(state.workspace.automation.policies.A.rules).length,6);assert.deepEqual(state.workspace.automation.policies.B,workspace.automation.policies.B);
 const before=state.commits;assert.equal(await prepareCurrentAutomationReview({context:ctx,flush,readDraft,commit}),11);assert.equal(state.commits,before);
 const fresh={...state,workspace:structuredClone(workspace),revision:20,pending:null,business:'A'};Object.assign(state,fresh);let sent=0;
 await assert.rejects(async()=>{await prepareCurrentAutomationReview({context:ctx,flush:async()=>{throw Error('冲突');},readDraft,commit});sent++;},/冲突/);assert.equal(sent,0);
 Object.assign(state,fresh);await assert.rejects(async()=>{await prepareCurrentAutomationReview({context:ctx,flush:async()=>{state.business='B';return 20;},readDraft,commit});sent++;},/业务已变化/);assert.equal(sent,0);
 Object.assign(state,fresh);await assert.rejects(async()=>{await prepareCurrentAutomationReview({context:ctx,flush:async()=>20,readDraft,commit:()=>false});sent++;},/尚未可靠保存/);assert.equal(sent,0);
 Object.assign(state,fresh);let waits=0;await assert.rejects(async()=>{await prepareCurrentAutomationReview({context:ctx,flush:async()=>{if(++waits===2)state.actor='different-user';return 20;},readDraft,commit});sent++;},/账号、工作区或业务已变化/);assert.equal(sent,0);
});
test('only exact precommit empty-rule409 is a known rejection in addition to exact CAS rejection',()=>{
 const message='请先配置此业务的自动化范围，空规则占位不能确认为执行授权';assert.equal(definiteReviewConflict({status:409,message}),true);
 for(const error of [{status:404,message},{status:503,message},{status:409,message:message+'。'},{status:409,message:'未知冲突'}])assert.equal(definiteReviewConflict(error),false);
});
test('new, renamed, aliased and inactive businesses never overwrite any existing policy or resource association',()=>{
 const legacy={...defaults(['旧业务'],true).policies['旧业务'],triggers:['异常事件'],extension:{keep:1},rules:{...defaults(['旧业务'],true).policies['旧业务'].rules,custom:{keep:'unknown'}}};
 const workspace={...B,businesses:['旧业务'],automation:{version:1,custom:{keep:2},policies:{'旧业务':legacy,'已移除业务':legacy}},businessAliases:{'别名业务':'旧业务'},resources:[{id:'r',businesses:['旧业务','外部'],unknown:3}],gaps:[]};
 const next=changeBusinesses(workspace,[{original:'旧业务',name:'改名业务'},{name:'已移除业务'},{name:'别名业务'},{name:'全新业务'}]);
 assert.deepEqual(next.automation.policies['旧业务'],legacy);assert.deepEqual(next.automation.policies['已移除业务'],legacy);assert.equal(Object.hasOwn(next.automation.policies,'改名业务'),false);assert.equal(Object.hasOwn(next.automation.policies,'别名业务'),false);
 assert.deepEqual(next.automation.policies['全新业务'],defaults(['全新业务'],true).policies['全新业务']);assert.deepEqual(next.automation.custom,{keep:2});assert.deepEqual(next.resources[0].businesses,['改名业务','外部']);assert.deepEqual(workspace.resources[0].businesses,['旧业务','外部']);
});
test('raw persistence keeps legacy fields and unknown actions while materializing only absent displayed fields',()=>{
 const saved=defaults(['旧业务'],true);saved.custom={keep:1};saved.policies['旧业务'].timezone='UTC';saved.policies['旧业务'].rules.write.receipt=false;saved.policies['旧业务'].rules.write.cancel=false;saved.policies['旧业务'].rules.write.idempotent=false;saved.policies['旧业务'].rules.extra={mode:'custom',evidence:2};saved.policies['旧业务'].unknown={keep:3};
 const visible=normalize(saved,['旧业务'],{},true),same=prepareAutomationPersistence(saved,visible,['旧业务']);assert.deepEqual(same.automation,saved);assert.equal(same.changed,false);
 visible.policies['旧业务'].rules.observe.mode='执行前确认';const edited=prepareAutomationPersistence(saved,visible,['旧业务']).automation;
 assert.equal(edited.policies['旧业务'].rules.observe.mode,'执行前确认');assert.deepEqual(edited.policies['旧业务'].rules.write,saved.policies['旧业务'].rules.write);assert.deepEqual(edited.policies['旧业务'].rules.extra,saved.policies['旧业务'].rules.extra);assert.equal(edited.policies['旧业务'].timezone,'UTC');assert.deepEqual(edited.custom,saved.custom);
});
test('opening a sparse saved policy does not add display defaults or change its confirmation',()=>{
 const saved={version:1,policies:{'巡护':{rules:{write:{mode:'仅给出建议'}},flow_review:{status:'confirmed',actor_id:'operator'}}},custom:{keep:true}};
 const visible=runtimeAutomationDisplay(saved,['巡护']);
 const unchanged=prepareAutomationPersistence(saved,visible,['巡护']);
 assert.equal(unchanged.changed,false);assert.deepEqual(unchanged.automation,saved);
 visible.policies['巡护'].rules.write.mode='执行前确认';
 const changed=prepareAutomationPersistence(saved,visible,['巡护']);
 assert.equal(changed.changed,true);assert.equal(changed.automation.policies['巡护'].rules.write.mode,'执行前确认');
 assert.deepEqual(Object.keys(changed.automation.policies['巡护'].rules),['write']);
 assert.equal(changed.automation.equipment,undefined);assert.deepEqual(changed.automation.custom,saved.custom);
});
test('runtime display and persistence never mutate saved custom device constraints or extensions',()=>{
 const saved=defaults(['旧业务'],true);saved.equipment.devices={d:{type:'camera',custom:true,rule:{receipt:false,cancel:false,timezone:'UTC',customEvidence:{keep:1}}}};const original=JSON.stringify(saved),visible=runtimeAutomationDisplay(saved,['旧业务']),prepared=prepareAutomationPersistence(saved,visible,['旧业务']);
 assert.equal(JSON.stringify(saved),original);assert.equal(prepared.automation.equipment.devices.d.rule.receipt,false);assert.equal(prepared.automation.equipment.devices.d.rule.cancel,false);assert.equal(prepared.automation.equipment.devices.d.rule.timezone,'UTC');assert.deepEqual(prepared.automation.equipment.devices.d.rule.customEvidence,{keep:1});
 const bad={...saved,equipment:{devices:{d:{type:'unknown',custom:false}}}},before=JSON.stringify(bad);assert.match(automationConfigurationIssue(bad),/原业务、规则和本页输入保留/);runtimeAutomationDisplay(bad,['旧业务']);assert.equal(JSON.stringify(bad),before);
});
test('malformed saved automation blocks both business changes and actual persist without dropping original records',()=>{
 for(const bad of [null,[],{},'broken',{policies:{},equipment:[]},{policies:{},equipment:{devices:{d:null}}},{policies:{},equipment:{types:{camera:{caps:'bad'}}}},{policies:{x:{budget:{}}}},{policies:{x:{rules:{write:{cooldown:null}}}}},{version:2,policies:{}},{policies:{x:{triggers:['unknown'],rules:{}}}},{policies:{x:{rules:{write:{scope:null}}}}},{policies:{x:{rules:{write:{receipt:'yes'}}}}},{policies:[]},{policies:{x:null}},{policies:{x:{rules:null}}},{policies:{x:{triggers:'manual',rules:{}}}},{policies:{x:{rules:{write:{mode:'unknown'}}}}}]){
  const workspace={...B,automation:bad},original=JSON.stringify(workspace);assert.throws(()=>changeBusinesses(workspace,rows()),error=>error.code==='AUTOMATION_CONFIG_INVALID'&&!/reading|undefined|map/.test(error.message));assert.match(automationConfigurationIssue(bad),/原业务、规则和本页输入保留/);
  const result=actualPersist(workspace,defaults(businessNames(workspace),true));assert.equal(result.accepted,false);assert.equal(result.commits.length,0);assert.equal(JSON.stringify(workspace),original);
 }
});
test('editing is blocked instead of silently erasing existing unknown sensitive fields',()=>{
 const workspace={...B,automation:structuredClone(B.automation)};workspace.automation.legacy_extension={token_history:'preserve'};
 const original=structuredClone(workspace),visible=runtimeAutomationDisplay(workspace.automation,businessNames(workspace),workspace.businessAliases),first=businessNames(workspace)[0];
 visible.policies[first].rules.write.mode=visible.policies[first].rules.write.mode==='自动运行'?'仅给出建议':'自动运行';
 const result=actualPersist(workspace,visible);assert.equal(result.accepted,false);assert.equal(result.commits.length,0);assert.deepEqual(workspace,original);
});
test('actual successful business save passes submitted target and adopts entire verified server configuration before closing',async()=>{
 const text=await readFile(new URL('../src/RuntimeSetup.jsx',import.meta.url),'utf8'),start=text.indexOf('async function verifyBusinessListSave('),end=text.indexOf('\n async function refreshMaterials',start),{businessListBasis}=await import('../src/business-list-contract.mjs');
 const account={workspace_id:'w',username:'human',user_id:'user-human'},accountRef={current:account},key={current:'draft'},prior={revision:16,async wait(){}};const sync={current:prior},submitted=changeBusinesses(B,rows()),server={revision:16,configuration:{...submitted,serverExtension:{proof:1},automation:{...submitted.automation,policies:{...submitted.automation.policies,[name]:{...submitted.automation.policies[name],flow_review:{status:'conservative_safe'}}}}}};let adopted=null;
 const fn=new Function('accountRef','key','sync','readRuntimeDraft','businessListBasis','adoptServerDraft',text.slice(start,end)+';return verifyBusinessListSave;')(accountRef,key,sync,async()=>server,businessListBasis,async saved=>{adopted=saved;});
 assert.equal(await fn(submitted),server);assert.equal(adopted,server);assert.deepEqual(adopted.configuration.serverExtension,{proof:1});
 adopted=null;await assert.rejects(fn({...submitted,businesses:['wrong']}),/与本次保存不同/);assert.equal(adopted,null);
});
test('actual pending recovery GET404 keeps exact journal, same UUID retry empty-policy409 archives exact scope then unlocks',async()=>{
 const panel=await readFile(new URL('../src/AutomationReviewPanel.jsx',import.meta.url),'utf8'),values=new Map(),storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)},account={workspace_id:'w',username:'human',user_id:'user-human',role:'operator'},key=reviewJournalKey(account),original={body:{revision:17,business_id:'business_0123456789abcdef',request_id:'11111111-2222-4333-8444-555555555555'},business_name:name,created_at:'original'};
 saveReviewRequest(storage,key,original);const states={},live={current:{account,pending:original,onPending:value=>{states.pending=value;live.current.pending=value;}}};let posted=null;const priorStorage=globalThis.localStorage;globalThis.localStorage=storage;
 const json=async(path,options)=>{if(!options?.method)throw Object.assign(Error('notfound'),{status:404});posted=JSON.parse(options.body);throw Object.assign(Error('请先配置此业务的自动化范围，空规则占位不能确认为执行授权'),{status:409});};
 const imports={live,writable:true,busy:false,begin:()=>1,active:()=>true,finish(){},json,saved(){throw Error('must not adopt');},withReviewJournalLock:(_key,operation)=>operation(),reviewJournalKey,sameReviewRequest,readReviewJournal,definiteReviewConflict,rejectReviewRequest,checkReviewReceipt,adoptRead(){},setNotFound:value=>states.notFound=value,setError:value=>states.error=value,setReady:value=>states.ready=value,setRejected:value=>states.rejected=value,setNotice:value=>states.notice=value,controller:{current:null}};
 function extract(start,end){return new Function(...Object.keys(imports),panel.slice(panel.indexOf(start),panel.indexOf(end,panel.indexOf(start)))+';return '+start.match(/function (\w+)/)[1]+';')(...Object.values(imports));}
 try{await extract('async function probe()','\n let row=')();assert.equal(states.notFound,true);assert.deepEqual(readReviewJournal(storage,key),original);assert.equal(states.pending,undefined);
  await extract('async function post(original)','\n async function confirmCurrent') (original);assert.deepEqual(posted,original.body);assert.equal(readReviewJournal(storage,key),null);assert.equal(states.pending,null);assert.equal(states.rejected,true);const archived=JSON.parse(storage.getItem(key+':rejected:'+original.body.request_id));assert.deepEqual(archived.original,original);assert.equal(archived.scope_key,key);
 }finally{if(priorStorage===undefined)delete globalThis.localStorage;else globalThis.localStorage=priorStorage;}
});
test('rejection archive storage failure preserves pending request and every prior UUID history',()=>{
 const values=new Map(),storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)},key=reviewJournalKey({workspace_id:'w',username:'human',user_id:'user-human'}),pending={body:{revision:17,business_id:'business_0123456789abcdef',request_id:'11111111-2222-4333-8444-555555555555'},business_name:name};
 storage.setItem(key+':rejected:older','original old history');saveReviewRequest(storage,key,pending);const failing={...storage,setItem(target,value){if(target===key+':rejected')throw Error('quota');storage.setItem(target,value);}};
 assert.throws(()=>rejectReviewRequest(failing,key,pending,'precise terminal'),/quota/);assert.deepEqual(readReviewJournal(storage,key),pending);assert.equal(storage.getItem(key+':rejected:older'),'original old history');assert.deepEqual(JSON.parse(storage.getItem(key+':rejected:'+pending.body.request_id)).original,pending);
});
test('actual POST unknown409,503 and interruption never unlock or change the original request',async()=>{
 const panel=await readFile(new URL('../src/AutomationReviewPanel.jsx',import.meta.url),'utf8');
 for(const error of [Object.assign(Error('unknown conflict'),{status:409}),Object.assign(Error('请先配置此业务的自动化范围，空规则占位不能确认为执行授权'),{status:503}),Object.assign(Error('stopped'),{name:'AbortError'})]){
  const values=new Map(),storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)},account={workspace_id:'w',username:'human',user_id:'user-human'},key=reviewJournalKey(account),original={body:{revision:17,business_id:'business_0123456789abcdef',request_id:'11111111-2222-4333-8444-555555555555'},business_name:name};saveReviewRequest(storage,key,original);let pendingChanged=false,calls=0;
  const imports={writable:true,busy:false,live:{current:{account,onPending(){pendingChanged=true;}}},begin:()=>1,active:()=>true,finish(){},json:async(_path,options)=>{calls++;assert.deepEqual(JSON.parse(options.body),original.body);throw error;},saved(){throw Error('unexpected saved');},withReviewJournalLock:(_key,operation)=>operation(),reviewJournalKey,sameReviewRequest,readReviewJournal,definiteReviewConflict,rejectReviewRequest,controller:{current:null},setReady(){},setRejected(){},setError(){}};
  const prior=globalThis.localStorage;globalThis.localStorage=storage;try{const start=panel.indexOf('async function post(original)'),end=panel.indexOf('\n async function confirmCurrent',start),post=new Function(...Object.keys(imports),panel.slice(start,end)+';return post;')(...Object.values(imports));await post(original);assert.equal(calls,1);assert.equal(pendingChanged,false);assert.deepEqual(readReviewJournal(storage,key),original);assert.equal(storage.getItem(key+':rejected'),null);}finally{if(prior===undefined)delete globalThis.localStorage;else globalThis.localStorage=prior;}
 }
});

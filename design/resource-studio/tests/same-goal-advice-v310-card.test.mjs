// Actual new Card/React DOM, with checked server DTOs and command callbacks as
// explicit IO fixtures. This does not prove HTTP, storage, money or authority;
// the controller and Root's target browser verify those separate boundaries.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let folder,compiled;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'same-goal-advice-card-v310-'));
  const projectReact=join(project,'node_modules/react/index.js'),reactURL=pathToFileURL(projectReact).href;
  const previousNodeEnvironment=process.env.NODE_ENV;let output;
  try{output=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},
   build:{ssr:join(project,'src/SameGoalAdviceV310.jsx'),write:false,minify:false,rollupOptions:{external:name=>name==='react'||name===projectReact||name===reactURL,
    output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'||name===projectReact||name===reactURL?reactURL:name}}}});}
  finally{if(previousNodeEnvironment===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previousNodeEnvironment;}
  const chunk=output.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);
  const file=join(folder,'Card.mjs');await writeFile(file,chunk.code);return (await import(pathToFileURL(file).href)).SameGoalAdviceContentV310;
 })();return compiled;
}
const id=n=>'31000000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>structuredClone(value);
function fixture(){
 const input={schema:'same-goal-advice-cloud-input-v302',sources:[{source_ref:'data_packet',kind:'packet',readability:'readable'},
  {source_ref:'document_evidence',kind:'evidence',readability:'partial'}],candidates:[{candidate_ref:'candidate_a',proposal:'核对现有记录后安排人工复核'}],
  facts:[{fact_ref:'fact_temperature',value:21,unit:'Cel',time_scope:{start:'2026-11-02T11:50:00Z',end:'2026-11-02T11:50:00Z'},
   source_refs:[{source_ref:'data_packet',quote:'当前温度为21℃；软件测试资料，不代表现场事实。'}]}],known_conflicts:[]};
 const documents=[['data','本次数据与接收记录','evaluation_text'],['business_document','批准的业务资料','approved_excerpt']].map(([role,title,source_scope],index)=>({role,title,text:index?'已批准的原文摘录，整份报告及现场真实性仍需核验。':'本次温度21℃，测试接收已保存。',
  evidence_ref:{evidence_id:id(10+index),sha256:'a'.repeat(64),size_bytes:100,media_type:'text/plain'},version:2,source_scope,as_of:'2026-11-02T12:00:00Z',consent:{status:'not_authorized',grants:[]}}));
 const model={name:'本任务指定分析模型',provider:'deepseek',model:'explicit-test-model',release_version:'1.0.0',available:true};
 return {account:{user_id:id(1),workspace_id:id(2),role:'operator'},loading:false,busy:false,denied:false,localSaved:true,error:'',notice:'',pending:null,history:[],selectedPlanId:id(4),notFound:false,
  reads:{context:'ready',history:'ready',plan:'ready',documents:'ready',preview:'ready'},
  context:{schema:'same-goal-advice-context-v310',goal_purpose:'核对原目标下的业务处理建议',data_as_of:'2026-11-02T13:00:00Z',model,
   document_sources:[{selection:{kind:'approved_source_set',source_set_ref:{source_set_id:id(5),source_set_hash:'b'.repeat(64)}},title:'本任务批准的需求说明',description:'保留已批准的完整摘录',excerpt_count:4}],
   permissions:{can_prepare:true,can_authorize:true,can_preview:true,can_start:true,can_review:true},blocked_reasons:[]},
  plan:{schema:'same-goal-advice-operation-v302',plan_id:id(4),phase:'awaiting_consent',data_as_of:'2026-11-02T12:00:00Z',human_review_available:false,result:null,reviews:[]},
  documents:{schema:'same-goal-advice-documents-v310',model,documents},
  preview:{schema:'same-goal-advice-preview-view-v307',model,currency:'CNY',maximum_cost_minor_units:12,configured_ceiling_minor_units:100,
   price_checked_at:'2026-11-02T12:00:00Z',price_expires_at:'2026-11-02T14:00:00Z',latest_for_plan:true,can_start:false,start_endpoint_available:true,requires_current_checks:true,
   messages:[{role:'system',content:'固定分析要求：仅提出供人工审核的意见。'},{role:'user',content:[{type:'text',text:JSON.stringify({question:'比较已经批准的资料',decision_input:input})},{type:'text',text:'匿名后的原文摘录。'}]}]},
  draft:{selected_document_source:null,prepare_reason:'',consent_reasons:{},consent_checked:{},preview_reason:'',preview_checked:false,start_reason:'',review_decision:'',review_reason:'',cancel_reason:''},
  list:{records:[]}};
}
function ready(view){
 view.plan.phase='ready_for_review';view.plan.human_review_available=true;
 view.plan.result={model_value:{candidate_reviews:[{candidate_ref:'candidate_a',suitability:'uncertain',reasoning:'只比较已提供的事实，仍需人工核查。',supporting_fact_refs:['fact_temperature'],blocking_conditions:[]}],
  source_reviews:[{source_ref:'data_packet',assessment:'reviewed',reason:'已读原数据。'},{source_ref:'document_evidence',assessment:'reviewed',reason:'仅审阅已提供的摘录。'}],conflicts:[],
  recommendation:{candidate_ref:'candidate_a',reason:'建议先做人工复核，未授予设备执行权限。',supporting_fact_refs:['fact_temperature']}}};
 view.plan.source_review_scope={schema:'same-goal-advice-provided-source-review-v309',sources:[
  {source_ref:'data_packet',review_scope:'provided_source_text_only'},{source_ref:'document_evidence',review_scope:'provided_excerpt_only'}],full_document_verified:false,source_authenticity_verified:false,can_dispatch:false};
 return view;
}

async function harness(fn){
 const errors=[],network=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){network.push(url);throw Error('network forbidden');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://same-goal-advice-ui.invalid/',resources:new NoResources(),virtualConsole:vc});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,
  localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 const view=fixture(),commands=[],patches=[],container=document.getElementById('root');let root;
 try{
  const Content=await component(),{createRoot}=await import('react-dom/client');
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  const element=()=>React.createElement(Content,{view:copy(view),account:view.account,
   onAction:(...args)=>commands.push(args),onDraft:patch=>{patches.push(copy(patch));view.draft={...view.draft,...copy(patch)};root.render(element());}});
  const render=async()=>{await act(async()=>root.render(element()));};
  const click=async node=>{assert.ok(node,'expected actual control');await act(async()=>node.click());};
  const field=label=>[...container.querySelectorAll('input,textarea,select')].find(node=>node.getAttribute('aria-label')===label);
  const edit=async(label,value)=>{const node=field(label);assert.ok(node);await act(async()=>{Object.getOwnPropertyDescriptor(node.tagName==='INPUT'?dom.window.HTMLInputElement.prototype:dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
  await render();assert.deepEqual(errors,[],'initial React render must succeed before user interactions');await fn({view,commands,patches,container,dom,render,click,edit,field,text:()=>container.textContent,
   button:label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label)});
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}

test('linked and old unlinked analyses explain their distinct relation to the stopped step',{concurrency:false},async()=>harness(async h=>{
 h.view.plan.continues_stopped_reasoning=true;await h.render();
 assert.match(h.container.querySelector('[aria-label="原步骤后续分析"]').textContent,/仍需完成后续办理和目标核验/);
 assert.equal(h.container.querySelector('[aria-label="原分析需要核对"]'),null);
 h.view.plan.continues_stopped_reasoning=false;h.view.plan.stopped_reasoning_unlinked=true;await h.render();
 assert.match(h.container.querySelector('[aria-label="原分析需要核对"]').textContent,/不能直接发送/);
 assert.equal(h.container.querySelector('[aria-label="原步骤后续分析"]'),null);
 h.view.draft.preview_checked=true;h.view.draft.start_reason='已核对';await h.render();
 assert.equal(h.button('开始分析').disabled,true);
}));

test('initial evaluation limitation is shown before local preparation without disabling it',{concurrency:false},async()=>harness(async h=>{
 h.view.plan=null; h.view.preview=null;
 h.view.context.blocked_reasons=[{code:'initial_evaluation_preview_limited',message:'当前是首次目标评价，可以在本地整理资料；现版本暂不能据此生成模型发送预览。已有记录保留。'}];
 h.view.draft.selected_document_source=h.view.context.document_sources[0].selection;
 h.view.draft.prepare_reason='整理本地资料';
 await h.render();
 const notice=h.container.querySelector('[aria-label="首次评价的分析限制"]');
 const input=h.field('本次整理资料的说明');
 assert.ok(notice && input && (notice.compareDocumentPosition(input)&h.dom.window.Node.DOCUMENT_POSITION_FOLLOWING));
 assert.match(notice.textContent,/不能.*生成.*预览/);
 assert.equal(h.button('整理资料').disabled,false);
 h.view.reads.context='failed';await h.render();
 assert.equal(h.container.querySelector('[aria-label="首次评价的分析限制"]'),null);
}));

test('each original has a separate model authorization and exact Evidence callback, never an implicit blanket grant',{concurrency:false},async()=>harness(async h=>{
 assert.equal(h.container.querySelectorAll('[aria-label="本次数据与接收记录"]').length,1);
 assert.equal(h.container.querySelectorAll('[aria-label="批准的业务资料"]').length,1);
 const original=h.container.querySelector('[aria-label="批准的业务资料"]');
 assert.equal(original.querySelector('details').open,false);
 assert.equal([...original.querySelectorAll('button')].find(node=>node.textContent==='确认授权').disabled,true);
 await h.edit('授权说明：批准的业务资料','本次仅授权已核对的摘录。');
 await h.click(original.querySelector('input[type=checkbox]'));
 const authorize=[...original.querySelectorAll('button')].find(node=>node.textContent==='确认授权');
 assert.equal(authorize.disabled,false);await h.click(authorize);
 assert.deepEqual(h.commands,[['authorizeEvidence',id(11)]]);
 assert.equal(h.view.draft.consent_checked[id(10)],undefined);
}));

test('cold-ready result places original data time and limited excerpt scope beside an initially empty human decision',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);await h.render();const form=h.container.querySelector('[aria-label="保存本次人工决定"]');assert.ok(form);
 assert.equal(form.querySelectorAll('input:checked').length,0);assert.equal(h.button('保存本次决定').disabled,true);
 const basis=h.container.querySelector('[aria-label="作出决定前的资料时间与审阅范围"]');
 assert.ok(basis.textContent.includes('仅审阅已提供的摘录'));assert.ok(basis.textContent.includes('未核验整份文档'));
 assert.ok(basis.textContent.includes('20:00:00'));assert.ok(!basis.textContent.includes('21:00:00'));
 const radio=[...form.querySelectorAll('input[type=radio]')].find(node=>node.value==='needs_revision');await h.click(radio);
 await h.edit('作出此决定的理由','需要先补充原报告未覆盖的现场依据。');
 assert.equal(h.button('保存本次决定').disabled,false);await h.click(h.button('保存本次决定'));
 assert.deepEqual(h.commands,[['review']]);assert.equal(h.view.draft.review_decision,'needs_revision');
}));

test('a saved answer cannot open review during finalization or when current review permission is absent',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.plan.phase='closing';h.view.plan.human_review_available=false;await h.render();
 assert.equal(h.container.querySelector('[aria-label="保存本次人工决定"]'),null);assert.ok(h.text().includes('结果保存中，完成后可审核'));
 h.view.plan.phase='ready_for_review';h.view.plan.human_review_available=true;h.view.context.permissions.can_review=false;
 h.view.draft.review_decision='accepted';h.view.draft.review_reason='原人稿继续保留';await h.render();
 assert.equal(h.button('保存本次决定').disabled,true);assert.equal(h.field('作出此决定的理由').value,'原人稿继续保留');
}));

test('missing original document read blocks saving a review while retaining the human choice and reason',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.documents=null;h.view.draft.review_decision='rejected';h.view.draft.review_reason='等待原依据读回后保存。';await h.render();
 assert.equal(h.button('保存本次决定').disabled,true);assert.equal(h.field('作出此决定的理由').value,'等待原依据读回后保存。');
 assert.ok(h.text().includes('依据尚未读回'));assert.deepEqual(h.commands,[]);
}));

test('actual anonymous text is deliberately expandable and Start needs fresh displayed confirmation plus a separate endpoint flag',{concurrency:false},async()=>harness(async h=>{
 assert.equal(h.container.querySelector('.same-advice-sent').open,false);
 assert.ok(h.text().includes('¥0.12'));assert.ok(h.text().includes('实际费用以供应商账单为准'));
 assert.equal(h.button('开始分析').disabled,true);
 h.view.draft.preview_checked=true;h.view.draft.start_reason='已核对当前匿名全文和本次上限';await h.render();
 assert.equal(h.view.preview.can_start,false);assert.equal(h.button('开始分析').disabled,false);
 h.view.preview.start_endpoint_available=false;await h.render();assert.equal(h.button('开始分析').disabled,true);
 assert.deepEqual(h.commands,[]);
}));

test('pending commands block fresh writes and only expose explicit original recovery and exact-body continuation',{concurrency:false},async()=>harness(async h=>{
 h.view.pending={operation:'start'};h.view.notFound=true;h.view.draft.preview_checked=true;h.view.draft.start_reason='已保存的人稿';await h.render();
 assert.equal(h.button('开始分析').disabled,true);
 await h.click(h.button('查看处理结果'));await h.click(h.button('继续原申请'));
 assert.deepEqual(h.commands,[['recover'],['resendOriginal']]);
}));

test('saved command with incomplete result blocks new work and an explicitly rejected original has a separate retain-and-edit action',{concurrency:false},async()=>harness(async h=>{
 h.view.needsCheck=true;h.view.draft.preview_checked=true;h.view.draft.start_reason='保留原开始理由';await h.render();
 assert.equal(h.button('开始分析').disabled,true);assert.ok(h.text().includes('建议结果待核对'));
 await h.click(h.button('核对结果'));assert.deepEqual(h.commands,[['recover']]);
 h.view.needsCheck=false;h.view.pending={operation:'start'};h.view.pendingRejected={status:409,message:'原价格依据已过期，原正文保留。'};await h.render();
 await h.click(h.button('修改输入'));assert.deepEqual(h.commands,[['recover'],['releaseRejected']]);
}));

test('blocked new configuration keeps saved historical advice and human reason visible without offering a new prepare',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.context.permissions.can_prepare=false;h.view.context.blocked_reasons=[{code:'model_unavailable',message:'指定模型当前已停用。',section:'model'}];
 h.view.draft.review_reason='已经写下的核查理由不应因模型停用丢失。';await h.render();
 assert.ok(h.text().includes('指定模型当前已停用'));assert.ok(h.text().includes('建议先做人工复核'));
 assert.equal(h.field('作出此决定的理由').value,h.view.draft.review_reason);assert.equal(h.button('整理资料'),undefined);
}));

test('model quotations stay escaped and technical errors do not dump JSON, internal identities or debug details',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.plan.result.model_value.recommendation.reason='<img src="https://example.invalid/private" onerror="alert(1)">原模型文字';
 h.view.error='{"invocation_id":"'+id(80)+'","traceback":"private-stack"}';await h.render();
 assert.equal(h.container.querySelector('img'),null);assert.ok(h.text().includes('<img src='));
 assert.ok(!h.text().includes(id(80))&&!h.text().includes('private-stack'));
 assert.ok(h.text().includes('操作未完成'));
}));

test('ordinary business users never see administrator fee commands even when a DTO flag is present',{concurrency:false},async()=>harness(async h=>{
 h.view.context.permissions.can_register_price=true;h.view.context.permissions.can_configure_quota=true;await h.render();
 assert.equal(h.container.querySelector('[aria-label="管理员费用设置"]'),null);
 assert.equal(h.button('更新官方价格依据'),undefined);assert.equal(h.button('保存本次分析费用上限'),undefined);
 assert.deepEqual(h.commands,[]);
}));

test('administrator can explicitly register official price without a Plan but currency is never guessed for quota',{concurrency:false},async()=>harness(async h=>{
 h.view.account.role='admin';h.view.plan=null;h.view.preview=null;h.view.documents=null;
 h.view.context.permissions.can_register_price=true;h.view.context.permissions.can_configure_quota=true;
 h.view.context.price={status:'unavailable',currency:null,checked_at:null,expires_at:null};h.view.context.quota={status:'unavailable',limits:null};await h.render();
 assert.equal(h.container.querySelector('[aria-label="管理员费用设置"]').open,false);
 assert.equal(h.button('更新官方价格依据').disabled,true);assert.equal(h.field('本次分析费用上限').disabled,true);
 await h.edit('更新官方价格依据的说明','按当前官方价目更新本任务费用依据。');await h.click(h.button('更新官方价格依据'));
 assert.deepEqual(h.commands,[['registerPrice']]);assert.ok(h.text().includes('费用上限仅适用于本次分析'));
 assert.ok(h.text().includes('币种待官方价格核对'));assert.equal(h.button('保存本次分析费用上限').disabled,true);
}));

test('administrator quota keeps money as exact human text and submits only the fixed controller command',{concurrency:false},async()=>harness(async h=>{
 h.view.account.role='admin';h.view.plan=null;h.view.context.permissions.can_configure_quota=true;h.view.context.permissions.can_register_price=false;
 h.view.context.price={status:'ready',currency:'CNY',checked_at:'2026-11-02T12:00:00Z',expires_at:'2026-11-02T13:00:00Z'};
 h.view.context.quota={status:'ready',limits:{max_cost_minor_units:25,currency:'CNY'},expires_at:'2026-11-02T14:00:00Z'};await h.render();
 await h.edit('本次分析费用上限','0.10');await h.edit('配置本次费用上限的说明','仅为本任务一次分析设置金额上限。');
 assert.equal(h.view.draft.quota_cost,'0.10');assert.equal(typeof h.view.draft.quota_cost,'string');
 assert.equal(h.button('保存本次分析费用上限').disabled,false);assert.equal(h.button('更新官方价格依据').disabled,true);
 await h.click(h.button('保存本次分析费用上限'));assert.deepEqual(h.commands,[['configureQuota']]);
 assert.ok(h.text().includes('¥0.25'));assert.ok(h.text().includes('金额最多两位小数'));
}));

test('cancellation consumes the explicit original server permission rather than inferring from a queued or running phase',{concurrency:false},async()=>harness(async h=>{
 assert.equal(h.button('确认取消'),undefined);
 h.view.plan.phase='send_queued';h.view.plan.cancel_available=true;await h.render();
 assert.equal(h.button('确认取消').disabled,true);
 await h.edit('取消本次申请的原因','原申请尚未实际调用模型，明确取消并保留资料。');
 assert.equal(h.button('确认取消').disabled,false);await h.click(h.button('确认取消'));
 assert.deepEqual(h.commands,[['cancel']]);
 h.view.plan.cancel_available=false;await h.render();assert.equal(h.button('确认取消'),undefined);
}));

test('preparation explanation remains an audit draft while the actual question comes only from the saved preview',{concurrency:false},async()=>harness(async h=>{
 const originalPlan=copy(h.view.plan),originalPreview=copy(h.view.preview);
 h.view.plan=null;h.view.preview=null;h.view.documents=null;await h.render();
 assert.equal(h.field('这次希望分析什么'),undefined);assert.ok(h.field('本次整理资料的说明'));
 assert.ok(h.text().includes('分析问题沿用任务内容'));assert.equal(h.container.querySelector('[aria-label="本次实际分析问题"]'),null);
 await h.edit('本次整理资料的说明','只记录本次整理申请的说明，不是替换模型问题。');
 assert.equal(h.view.draft.prepare_reason,'只记录本次整理申请的说明，不是替换模型问题。');
 h.view.plan=originalPlan;h.view.preview=copy(originalPreview);await h.render();
 let question=h.container.querySelector('[aria-label="本次实际分析问题"]');assert.ok(question);
 assert.ok(question.textContent.includes('比较已经批准的资料'));assert.ok(!question.textContent.includes(h.view.draft.prepare_reason));
 ready(h.view);await h.render();assert.equal(h.container.querySelectorAll('[aria-label="本次实际分析问题"]').length,1);
 question=h.container.querySelector('[aria-label="本次实际分析问题"]');assert.ok(question.textContent.includes('比较已经批准的资料'));
 assert.deepEqual(h.view.preview,originalPreview);assert.deepEqual(h.commands,[]);
}));

test('official USD amounts retain their own currency in preview and quota without conversion or unsafe-number formatting',{concurrency:false},async()=>harness(async h=>{
 h.view.account.role='admin';h.view.context.permissions.can_configure_quota=true;
 h.view.context.price={status:'ready',currency:'USD',checked_at:'2026-11-02T12:00:00Z',expires_at:'2026-11-02T13:00:00Z'};
 h.view.context.quota={status:'ready',limits:{max_cost_minor_units:25,currency:'USD'},expires_at:'2026-11-02T14:00:00Z'};
 h.view.preview.currency='USD';await h.render();
 assert.ok(h.container.querySelector('.same-advice-price').textContent.includes('US$0.12'));
 assert.ok(h.text().includes('US$1.00')&&h.text().includes('US$0.25'));assert.ok(!h.text().includes('¥'));
 h.view.preview.maximum_cost_minor_units=0;await h.render();assert.ok(h.container.querySelector('.same-advice-price').textContent.includes('US$0.00'));
 for(const invalid of [-1,Number.MAX_SAFE_INTEGER+1,12.5]){
  h.view.preview.maximum_cost_minor_units=invalid;await h.render();assert.ok(h.container.querySelector('.same-advice-price').textContent.includes('费用尚未核对'));
 }
 h.view.preview.maximum_cost_minor_units=12;h.view.preview.currency='EUR';await h.render();
 assert.ok(h.container.querySelector('.same-advice-price').textContent.includes('费用尚未核对'));assert.deepEqual(h.commands,[]);
}));

test('only an already reviewed round can explicitly prepare another round, keeping the previous answer, decision and reasons',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);await h.render();assert.equal(h.button('整理资料'),undefined);assert.equal(h.button('新增建议'),undefined);
 h.view.plan.phase='reviewed';h.view.plan.human_review_available=false;
 h.view.plan.reviews=[{decision:'accepted',reason:'已核对原建议，保留本次采用决定。',reviewed_at:'2026-11-02T12:20:00Z'}];
 h.view.draft.selected_document_source=copy(h.view.context.document_sources[0].selection);
 h.view.draft.prepare_reason='在原目标下继续整理下一份资料。';h.view.draft.review_reason='上一份人工作业理由继续保留。';
 const originalResult=copy(h.view.plan.result),originalReviews=copy(h.view.plan.reviews),originalReason=h.view.draft.review_reason;
 h.view.context.permissions.can_prepare=false;await h.render();assert.equal(h.button('新增建议').disabled,true);
 assert.ok(h.text().includes('上一份建议和决定仍可查看'));assert.ok(h.text().includes(originalReviews[0].reason));
 h.view.context.permissions.can_prepare=true;await h.render();assert.equal(h.button('新增建议').disabled,false);
 await h.click(h.button('新增建议'));assert.deepEqual(h.commands,[['prepare']]);
 assert.deepEqual(h.view.plan.result,originalResult);assert.deepEqual(h.view.plan.reviews,originalReviews);assert.equal(h.view.draft.review_reason,originalReason);
}));

test('a cold-checked original answer appears while current context and history are still loading with all writes disabled',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.context=null;h.view.list=null;h.view.documents=null;h.view.preview=null;h.view.busy=true;h.view.loading=true;
 h.view.reads={context:'loading',history:'loading',plan:'ready',documents:'loading',preview:'loading'};
 h.view.draft.review_decision='accepted';h.view.draft.review_reason='这份原答的待保存理由仍保留。';await h.render();
 assert.ok(h.text().includes('建议先做人工复核'));assert.ok(h.text().includes('仅审阅已提供的摘录'));
 assert.ok(h.container.querySelector('[aria-label="作出决定前的资料时间与审阅范围"]').textContent.includes('20:00:00'));
 assert.ok(h.text().includes('正在核对办理条件'));assert.ok(h.text().includes('正在读取历史建议'));
 assert.equal(h.container.querySelector('[role="alert"]'),null);assert.equal(h.button('保存本次决定').disabled,true);
 assert.equal(h.field('作出此决定的理由').value,h.view.draft.review_reason);assert.deepEqual(h.commands,[]);
}));

test('failed current context cannot reuse an earlier permission object while independently read originals remain visible',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.reads.context='failed';h.view.reads.history='failed';h.view.list=null;
 h.view.draft.review_decision='accepted';h.view.draft.review_reason='保留已经填写的决定理由。';await h.render();
 assert.ok(h.text().includes('建议先做人工复核'));assert.ok(h.text().includes('办理条件暂未读回'));
 assert.ok(h.text().includes('历史建议暂未读回'));assert.ok(!h.text().includes('暂无历史建议'));
 assert.equal(h.button('保存本次决定').disabled,true);assert.equal(h.field('作出此决定的理由').value,h.view.draft.review_reason);
 assert.equal(h.container.querySelector('[aria-label="管理员费用设置"]'),null);assert.deepEqual(h.commands,[]);
}));

test('document failure and a separately loading preview do not erase the proved answer or masquerade as empty sources',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.documents=null;h.view.preview=null;h.view.busy=true;h.view.loading=true;
 h.view.reads.documents='failed';h.view.reads.preview='loading';await h.render();
 assert.ok(h.text().includes('建议先做人工复核'));assert.ok(h.text().includes('资料暂未读回'));
 assert.ok(h.text().includes('正在读取发送预览'));
 assert.ok(!h.text().includes('资料尚未读取'));assert.equal(h.button('保存本次决定').disabled,true);
 assert.deepEqual(h.commands,[]);
}));

test('permission denial hides all sensitive successful branches even during a progressive read',{concurrency:false},async()=>harness(async h=>{
 ready(h.view);h.view.denied=true;h.view.loading=true;h.view.busy=true;h.view.reads.context='loading';
 h.view.draft.review_reason='必须隐藏的本机人工理由';await h.render();
 assert.ok(h.text().includes('资料已隐藏'));assert.ok(!h.text().includes('建议先做人工复核'));
 assert.ok(!h.text().includes('已批准的原文摘录')&&!h.text().includes('必须隐藏的本机人工理由'));
 assert.equal(h.container.querySelector('[aria-label="保存本次人工决定"]'),null);assert.equal(h.container.querySelector('textarea'),null);
 assert.deepEqual(h.commands,[]);
}));

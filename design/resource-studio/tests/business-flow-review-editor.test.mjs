// Real JSX/React DOM over the actual controller and runtime JSON transport.
// Public synthetic HTTP/storage only: this is not browser, DB or model proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';
import {nestedPlan,causality as nestedCausality,diagnostics as nestedDiagnostics,STEP_PATH,DECISION_PATH} from './nested-flow-review-fixture.mjs';
import {executionFixture} from './nested-business-flow-execution-fixture.mjs';
import {initialNestedConfiguration} from '../src/nested-execution-fields.mjs';
import {graphWireDigest} from '../src/full-semantic-graph-wire.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const id=n=>'10000000-0000-4000-8000-'+n.toString(16).padStart(12,'0');
const copy=value=>structuredClone(value),scope={producerId:id(1),actorId:id(2),workspaceId:id(3)},attempt=id(4);
const base='/api/setup/business-flow-reviews',A='w:p1/s1',B='w:p2/s1',UNKNOWN='w:p999/s1',UNUSED='w:p3/s1';
const rawA='公开原文A的完整内容。',rawB='公开原文B的完整内容。';
const attribution=()=>({citations:[A],inferred:false,reason:'公开原文中的明确职责，仍待人工核对。'});
function original(){
 return {schema:'report-business-flow-wire-v182',binding_hash:'a'.repeat(64),business:{name:'范围测试业务',flows:[{
  flow_id:'flow_one',name:'巡查与核对',trigger:{kind:'event',description:'收到异常线索后启动',attribution:attribution()},start_node:'check',end_nodes:['archive'],
  nodes:[{node_id:'check',name:'核对线索',actor:'business_agent',role:'核对员',responsibility:'核对线索并决定后续处置',inputs:['异常线索'],outputs:['核对结果'],attribution:attribution()},
   {node_id:'archive',name:'保存结论',actor:'data_agent',role:'档案员',responsibility:'完整保存结论与出处',inputs:['核对结果'],outputs:['核对档案'],attribution:attribution()}],
  edges:[{kind:'condition',from_node:'check',to_node:'archive',condition:'核对结论已填写',attribution:attribution()},
   {kind:'wait',from_nodes:['check'],to_node:'archive',mode:'all',attribution:attribution()},
   {kind:'parallel',from_node:'check',to_node:'archive',group:'parallel_one',attribution:attribution()}],
  deliverables:[{name:'核对档案',description:'保留出处的完整核对档案',produced_by:['archive'],attribution:attribution()}]
 }],questions:[{question:'仍需核对设备协议',reason:'资料尚未明确，不能推测远控能力',citations:[UNKNOWN,B],retained_detail:{original_values:[false,0,null,'完整保留']}}]},
 evidence:[{span_ref:A,quote:rawA,retained_note:{original:true}},{span_ref:B,quote:'公开'},{span_ref:A,quote:'重复保留待核'},{span_ref:UNUSED,quote:'未使用但暂存的原引用'},{span_ref:UNKNOWN,quote:'原回答中的未知引用'}],
 retained_top:{unrecognized:['保存时原样保留',1,true]}};
}
function diagnostics(value){return {schema:'business-flow-review-diagnostics-v1',available:true,value:copy(value),
 issues:[{code:'quote_mismatch',path:'/evidence/1/quote',message:'请核对原文B的完整引用',span_ref:B},{code:'question_pending',path:'/business/questions/0/question',message:'请核对设备协议问题',span_ref:null}],
 source_evidence:[{span_ref:A,source_text:rawA,text_origin:'platform_resolved_source'},{span_ref:B,source_text:rawB,text_origin:'platform_resolved_source'}],
 structural_valid:false,source_valid:false,semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};}
const tick=()=>new Promise(done=>setImmediate(done));
let directory,module;
const priorSession=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
test.before(async()=>{
 assert.equal(process.env.NODE_ENV,'development','run the actual React DOM regression with NODE_ENV=development');
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,writable:true,value:{getItem:()=>null}});
 directory=await mkdtemp(join(tmpdir(),'business-flow-review-editor-'));
 const output=await build({root:project,configFile:false,envDir:directory,mode:'test',cacheDir:join(directory,'cache'),logLevel:'silent',build:{ssr:join(project,'src/BusinessFlowReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
 const path=join(directory,'editor.mjs');await writeFile(path,output.output.find(item=>item.type==='chunk'&&item.isEntry).code);module=await import(pathToFileURL(path).href);
});
test.after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});if(priorSession)Object.defineProperty(globalThis,'sessionStorage',priorSession);else delete globalThis.sessionStorage;});

test('citation usage counts all actual owner paths without repairing unknown or duplicate values',()=>{
 const value=original(),before=copy(value),uses=module.reviewCitationUses(value);
 assert.equal(uses.filter(use=>use.span_ref===A).length,7);
 assert.deepEqual(uses.filter(use=>use.span_ref===UNKNOWN),[{span_ref:UNKNOWN,path:'/business/questions/0/citations/0',label:'待核问题 1'}]);
 assert.equal(uses.filter(use=>use.span_ref===UNUSED).length,0);
 assert.deepEqual(value,before,'viewing usage preserves every source value and type');
 value.business.questions[0].citations.push(B);assert.equal(module.reviewCitationUses(value).filter(use=>use.span_ref===B).length,2);
});

test('removal undo restores only its complete item and refuses missing arrays or an already restored item',()=>{
 const before=original(),item=copy(before.business.questions[0]),removal={path:['business','questions'],index:0,item,original_array:copy(before.business.questions)};
 const later=copy(before);later.business.questions.splice(0,1);later.business.flows[0].nodes[0].role='删除后的新岗位';later.retained_top.extra='后来输入必须保留';
 const current=copy(later),restored=module.reviewRestoreRemoval(later,removal);
 assert.deepEqual(restored.business.questions,[item]);assert.equal(restored.business.flows[0].nodes[0].role,'删除后的新岗位');assert.equal(restored.retained_top.extra,'后来输入必须保留');assert.deepEqual(later,current,'undo projection does not mutate its caller input');
 assert.throws(()=>module.reviewRestoreRemoval(restored,removal),/条目已经存在/);
 assert.throws(()=>module.reviewRestoreRemoval({...later,business:null},removal),/原数组已变化/);
 assert.throws(()=>module.reviewRestoreRemoval(later,{...removal,path:['business','flows']}),/不能安全撤销/);
});

test('actual editor repairs questions and evidence through durable edits, preserves the immutable model, and saves the exact CAS body',{timeout:30000,concurrency:false},async()=>{
 class NoResources extends ResourceLoader{fetch(){throw Error('External DOM resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://review-editor.invalid/',resources:new NoResources(),pretendToBeVisual:true});
 const session=dom.window.sessionStorage,storage=dom.window.localStorage;
 session.setItem('ga-setup-csrf','synthetic-editor-token');session.setItem('ga-setup-csrf-user',scope.actorId);session.setItem('ga-setup-csrf-workspace',scope.workspaceId);
 const baseline=original(),immutable=copy(baseline),calls=[],commands=new Map(),row={attempt_id:attempt,name:baseline.business.name,version:0,value:copy(baseline),note:'原人工说明',baseline_value:copy(baseline),diagnostics:diagnostics(baseline),source_binding:{schema:'business-flow-review-source-v1',attempt_id:attempt},saved:false,source_available:true,can_apply:false,can_publish:false,can_execute:false,semantic_quality_verified:false};
 const componentAttempt=id(14),componentBaseline=original();componentBaseline.business.name='另一未完成流程部分';componentBaseline.business.flows[0].name='独立复核中的已核流程';
 const componentImmutable=copy(componentBaseline),detailSourceBinding={schema:'business-flow-component-review-source-v186',attempt_id:componentAttempt,generation_profile:'flow-v185'};
 const componentRow={attempt_id:componentAttempt,name:componentBaseline.business.name,version:0,value:copy(componentBaseline),note:'原失败回答中提取的流程部分，仍待人工核对',baseline_value:copy(componentBaseline),diagnostics:diagnostics(componentBaseline),source_binding:copy(detailSourceBinding),saved:false,source_available:true,can_apply:false,can_publish:false,can_execute:false,semantic_quality_verified:false,
  generation_review:{original_protocol_valid:false,location_issues:[{path:'/findings/0/location',message:'公开合成定位格式未通过，原失败保留'}],findings:[{resolution:'unresolved',explanation:'公开合成复核发现仍待人工核对',resolution_explanation:'原机器定位未通过格式检查，流程部分另存未完成',basis:'both',citations:[A]}]}};
 const fakeCandidates=()=>[{attempt_id:attempt,name:row.name,status:'failed',saved_version:row.version},{attempt_id:componentAttempt,name:componentRow.name,status:'failed',saved_version:componentRow.version}];
 const fetcher=async(url,options={})=>{
  const path=String(url),method=options.method||'GET';calls.push({path,method,body:options.body});
  assert.equal(options.headers?.['X-Workspace-ID'],scope.workspaceId);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-editor-token');
  let result;
  if(path==='/api/me')result={user_id:scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  else if(path===base+'/candidates?producer_id='+scope.producerId)result={items:fakeCandidates()};
  else if(path===base+'/attempts/'+attempt&&method==='GET')result=copy(row);
  else if(path===base+'/attempts/'+componentAttempt&&method==='GET')result=copy(componentRow);
  else if(path.startsWith(base+'/requests/')){
   const nonce=path.split('/requests/')[1].split('?')[0],expected=new URLSearchParams(path.split('?')[1]).get('expected_sha256'),stored=commands.get(nonce);
   if(stored)assert.equal(stored.sha,expected);
   result={schema:'business-flow-review-original-result-v1',request_id:nonce,found:!!stored,body_sha256:expected,result:stored?.result||null};
  }else if(path===base+'/attempts/'+attempt+'/save'&&method==='POST'){
   const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['request_id','expected_version','value','note']);assert.equal(body.expected_version,row.version);
   const working=JSON.parse(storage.getItem(session.getItem(`business-flow-review:v1:${scope.workspaceId}:${scope.actorId}:${scope.producerId}:tab-pointer`)));
   assert.equal(working.pending.raw,options.body,'the original full raw request was durably journaled before POST');
   const sha=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(options.body))).toString('hex');
   result={schema:'business-flow-review-save-result-v1',id:id(8),attempt_id:attempt,request_id:body.request_id,version:row.version+1,status:'incomplete',source_binding:copy(row.source_binding),baseline_value:copy(baseline),value:copy(body.value),note:body.note,diagnostics:diagnostics(body.value),source_binding_hash:'b'.repeat(64),baseline_hash:'c'.repeat(64),semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
   commands.set(body.request_id,{sha,result:copy(result)});Object.assign(row,copy(result),{saved:true});
  }else throw Error('Unexpected test transport path: '+method+' '+path);
  return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
 };
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,options,fn)=>{assert.equal(options.mode,'exclusive');return fn();}}});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,localStorage:storage,sessionStorage:session,fetch:fetcher,crypto:webcrypto,requestAnimationFrame:fn=>setTimeout(fn,0),cancelAnimationFrame:clearTimeout,IS_REACT_ACT_ENVIRONMENT:true};
 const previous=new Map(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const container=dom.window.document.getElementById('root');let root,guard;
 const text=()=>container.textContent||'',buttons=(owner=container)=>[...owner.querySelectorAll('button')];
 const button=(label,owner=container)=>{const found=buttons(owner).find(node=>node.textContent===label);assert.ok(found,'actual DOM button: '+label);return found;};
 const pointer=()=>session.getItem(`business-flow-review:v1:${scope.workspaceId}:${scope.actorId}:${scope.producerId}:tab-pointer`);
 const working=()=>JSON.parse(storage.getItem(pointer())),value=()=>working().forms[attempt].value;
 async function until(check,label){const deadline=Date.now()+5000;while(Date.now()<deadline){await act(async()=>{await tick();});if(check())return;}assert.ok(check(),label);}
 async function click(label,owner=container){const node=button(label,owner);assert.equal(node.disabled,false,'enabled actual control: '+label);await act(async()=>{node.click();});}
 async function input(label,next){const node=container.querySelector(`[aria-label="${label}"]`);assert.ok(node,'actual input: '+label);const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,next);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});}
 const evidenceRows=()=>[...container.querySelectorAll('.bfr-right [data-review-path]')].filter(node=>/^\/evidence\/\d+$/.test(node.dataset.reviewPath));
 const evidenceRow=quote=>{const found=evidenceRows().find(node=>[...node.querySelectorAll('.bfr-source-text')].some(paragraph=>paragraph.textContent===quote));assert.ok(found,'current evidence row: '+quote);return found;};
 try{
  const {createRoot}=await import('react-dom/client');root=createRoot(container);
  let refreshToken=0,openRequest=null;const render=()=>root.render(React.createElement(module.default,{...scope,refreshToken,openRequest,onRegisterGuard:(next,previousGuard)=>{if(next)guard=next;else if(guard===previousGuard)guard=null;}}));
  await act(async()=>{render();});await until(()=>text().includes('业务候选')&&buttons().some(node=>node.textContent.includes('范围测试业务')),'actual identity and candidate load finished');
  const candidate=buttons().find(node=>node.textContent.startsWith('范围测试业务'));await act(async()=>{candidate.click();});
  await until(()=>!!container.querySelector('input[aria-label="业务名称（本次范围固定）"]'),'actual detail and working draft loaded');
  assert.equal(container.querySelector('input[aria-label="业务名称（本次范围固定）"]').readOnly,true);
  const evidenceSummary=[...container.querySelectorAll('summary')].find(node=>node.textContent.startsWith('查看全部依据'));assert.ok(evidenceSummary);await act(async()=>{evidenceSummary.click();});assert.equal(evidenceSummary.parentElement.open,true,'human opens the evidence cleanup list');
  assert.equal(value().business.name,baseline.business.name);assert.equal(button('移除此依据（可撤销）',evidenceRow('公开')).disabled,true,'the last used B evidence cannot be deleted');
  assert.equal(button('移除此依据（可撤销）',evidenceRow('原回答中的未知引用')).disabled,true,'unknown evidence still in use is not silently removed');

  // Locate a quote error from its span_ref, showing original text and the
  // current quote together; only the explicit action changes the human draft.
  const quoteIssue=container.querySelector('.bfr-issue');await act(async()=>{quoteIssue.click();});
  await until(()=>container.querySelector('[data-focused-source]')?.textContent.includes(rawB),'quote issue opens its exact platform source');
  const sourcePanel=container.querySelector('[data-focused-source]');assert.ok(sourcePanel.textContent.includes('公开'));
  await click('将此原文填入引用 2',sourcePanel);assert.equal(value().evidence[1].quote,rawB);assert.equal(row.baseline_value.evidence[1].quote,'公开');
  const questionIssue=[...container.querySelectorAll('.bfr-issue')].find(node=>node.textContent.includes('设备协议问题'));await act(async()=>{questionIssue.click();});
  await until(()=>dom.window.document.activeElement?.getAttribute('aria-label')==='待核问题 1','question issue focuses the real question field');

  const questionBefore=copy(value().business.questions[0]);await click('移除此待核问题 1（可撤销）');assert.deepEqual(value().business.questions,[]);
  await input('负责岗位 / 角色','移除后补充的核对岗位');assert.equal(value().business.flows[0].nodes[0].role,'移除后补充的核对岗位');
  await click('撤销最近一次结构修改');assert.deepEqual(value().business.questions,[questionBefore],'undo restores all fields including unknown fields and original citation order');
  assert.equal(value().business.flows[0].nodes[0].role,'移除后补充的核对岗位','item undo preserves later edits elsewhere');
  const guardedBefore=copy(value());await click('撤销最近一次结构修改');assert.deepEqual(value(),guardedBefore);assert.ok(text().includes('结构修改后还有其他输入，未整稿回退'),'general snapshot undo cannot erase later human work');
  const question=()=>container.querySelector('[data-review-path="/business/questions/0"]');
  assert.ok(question().textContent.includes('原引用不在当前业务范围'));
  await click('移除此关联原文 1（可撤销）',question());assert.deepEqual(value().business.questions[0].citations,[B]);
  const unknownRow=evidenceRow('原回答中的未知引用');assert.match(unknownRow.textContent,/使用 0 次/);await click('移除此依据（可撤销）',unknownRow);assert.equal(value().evidence.some(entry=>entry.span_ref===UNKNOWN),false);
  const citationDetails=question().querySelector('details');await act(async()=>{citationDetails.querySelector('summary').click();});assert.equal(citationDetails.open,true,'human opens complete question citation choices');
  const chooseA=[...question().querySelectorAll('label')].find(label=>label.textContent.trim()==='原文片段 1')?.querySelector('input[type="checkbox"]');assert.ok(chooseA);await act(async()=>{chooseA.click();});assert.deepEqual(value().business.questions[0].citations,[B,A]);
  await click('移除此关联原文 1（可撤销）',question());assert.deepEqual(value().business.questions[0].citations,[A]);
  assert.match(evidenceRow(rawB).textContent,/使用 0 次/);await click('移除此依据（可撤销）',evidenceRow(rawB));
  const duplicated=evidenceRow('重复保留待核');assert.match(duplicated.textContent,/使用 8 次/);await click('删除此重复依据（保留另一条，可撤销）',duplicated);
  assert.equal(value().evidence.filter(entry=>entry.span_ref===A).length,1);assert.deepEqual(value().evidence[0].retained_note,{original:true});
  await click('移除此依据（可撤销）',evidenceRow('未使用但暂存的原引用'));
  await input('本次修订说明','删除依据之后补充的说明');await click('撤销最近一次结构修改');assert.ok(value().evidence.some(entry=>entry.span_ref===UNUSED));assert.equal(working().forms[attempt].note,'删除依据之后补充的说明','evidence undo preserves the later note');
  await click('移除此依据（可撤销）',evidenceRow('未使用但暂存的原引用'));
  assert.deepEqual(value().evidence,[baseline.evidence[0]]);assert.deepEqual(value().retained_top,baseline.retained_top);
  assert.equal(button('移除此依据（可撤销）',evidenceRow(rawA)).disabled,true);

  await click('原模型稿（只读）');const read=container.querySelector('.bfr-readonly');
  for(const phrase of ['收到异常线索后启动','开始步骤：核对线索','结束步骤：保存结论','输入：异常线索','满足条件后进行','核对结论已填写','全部完成后继续','并行分组 1','产生步骤：保存结论','仍需核对设备协议','原回答中的未知引用'])assert.ok(read.textContent.includes(phrase),'complete original comparison shows '+phrase);
  assert.equal(read.querySelectorAll('input,textarea,select').length,0,'original comparison is entirely read-only');
  await click('编辑草稿');const beforeSave=copy(value());assert.equal(working().forms[attempt].dirty,true);assert.equal(guard(),true,'durable editing permits guarded navigation');
  const journalBeforeListRefresh=storage.getItem(pointer()),focused=container.querySelector('textarea'),
   detailReadsBefore=calls.filter(call=>call.path===base+'/attempts/'+attempt&&call.method==='GET').length,
   listsBefore=calls.filter(call=>call.path.includes('/candidates?')).length;
  focused.focus();focused.setSelectionRange(1,2);refreshToken=1;await act(async()=>{render();});
  await until(()=>calls.filter(call=>call.path.includes('/candidates?')).length>listsBefore,'new candidate refresh only updates the candidate list');
  assert.equal(storage.getItem(pointer()),journalBeforeListRefresh,'list refresh preserves every unsaved working draft byte');
  assert.equal(container.querySelector('textarea'),focused,'list refresh does not replace the editor or its focused control');
  assert.equal(dom.window.document.activeElement,focused);assert.equal(focused.selectionStart,1);assert.equal(focused.selectionEnd,2);
  assert.equal(calls.filter(call=>call.path===base+'/attempts/'+attempt&&call.method==='GET').length,detailReadsBefore,'new machine candidate cannot reload or select a human draft');

  // A user may explicitly open the separately saved component after the list
  // refresh. The old dirty form stays byte-for-byte in its own journal entry;
  // source diagnostics must disclose that the original AI review still failed.
  const originalDirtyFormRaw=JSON.stringify(working().forms[attempt]),originalBaselineRaw=JSON.stringify(row.baseline_value),
   componentReadsBefore=calls.filter(call=>call.path===base+'/attempts/'+componentAttempt&&call.method==='GET').length;
  assert.equal(working().selected,attempt);assert.equal(working().forms[attempt].dirty,true);assert.equal(guard(),true);
  openRequest={attemptId:componentAttempt};await act(async()=>{render();});
  await until(()=>working().selected===componentAttempt&&container.querySelector('input[aria-label="业务名称（本次范围固定）"]')?.value===componentBaseline.business.name&&guard()===true,'explicit openRequest selects the new saved component through normal detail GET');
  assert.ok(calls.filter(call=>call.path===base+'/attempts/'+componentAttempt&&call.method==='GET').length>componentReadsBefore);
  assert.equal(JSON.stringify(working().forms[attempt]),originalDirtyFormRaw,'opening another candidate preserves every old human form byte and dirty state');
  assert.equal(JSON.stringify(row.baseline_value),originalBaselineRaw,'the old immutable model is unchanged');assert.deepEqual(baseline,immutable);
  assert.deepEqual(working().forms[componentAttempt].value,componentImmutable);assert.deepEqual(componentRow.baseline_value,componentImmutable);
  for(const phrase of ['原AI复核未通过定位格式检查','只提取了其中通过流程结构与原文引用核对的流程部分','不代表原复核成功','共有 1 处定位问题','公开合成复核发现仍待人工核对'])assert.ok(text().includes(phrase),'component-only source warning remains visible: '+phrase);
  assert.equal(componentRow.generation_review.original_protocol_valid,false);assert.equal(componentRow.can_apply,false);
  assert.equal(calls.filter(call=>call.method==='POST').length,0,'explicitly viewing the component cannot save, adopt or execute it');
  openRequest=null;await act(async()=>{render();});
  const originalCandidate=buttons().find(node=>node.textContent.startsWith('范围测试业务'));assert.ok(originalCandidate);
  await act(async()=>{originalCandidate.click();});
  await until(()=>working().selected===attempt&&container.querySelector('input[aria-label="业务名称（本次范围固定）"]')?.value===baseline.business.name&&guard()===true,'explicitly selecting the original candidate restores the prior human edits');
  assert.equal(JSON.stringify(working().forms[attempt]),originalDirtyFormRaw);assert.deepEqual(value(),beforeSave);assert.equal(working().forms[attempt].dirty,true);
  assert.equal(working().forms[attempt].note,'删除依据之后补充的说明');assert.deepEqual(working().forms[componentAttempt].value,componentImmutable,'new component keeps its own separate unfinished form');
  assert.deepEqual(row.baseline_value,immutable);assert.deepEqual(componentRow.baseline_value,componentImmutable);

  await click('保存未完成草稿');await until(()=>row.version===1&&text().includes('草稿已保存并核对原回执'),'normal exact CAS save completed');
  const posts=calls.filter(call=>call.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].body).value,beforeSave);assert.equal(JSON.parse(posts[0].body).expected_version,0);
  assert.ok(calls.findIndex(call=>call.path.includes('/requests/'))<calls.indexOf(posts[0]));assert.deepEqual(row.baseline_value,immutable);assert.deepEqual(baseline,immutable);assert.equal(row.can_apply,false);assert.equal(row.status,'incomplete');
  await act(async()=>{root.unmount();});assert.equal(guard,null);root=createRoot(container);await act(async()=>{render();});
  await until(()=>!!container.querySelector('input[aria-label="业务名称（本次范围固定）"]'),'refresh restores the selected actual draft');assert.deepEqual(value(),beforeSave);assert.equal(working().forms[attempt].version,1);assert.equal(calls.filter(call=>call.method==='POST').length,1,'refresh never resends a saved command');
  // The existing private work is still visible after the real save/remount.
  // A genuine browser focus event must remove it when the session changes,
  // while retaining every journal byte under its original account namespace.
  await click('原模型稿（只读）');
  assert.ok(text().includes(baseline.business.name)&&container.querySelector('.bfr-readonly')?.textContent.includes(rawA),
   'private business and original evidence are visible before the identity changes');
  const snapshot=()=>Object.fromEntries(Array.from({length:storage.length},(_,i)=>{const key=storage.key(i);return[key,storage.getItem(key)];}));
  const oldStorage=snapshot(),saveCount=calls.filter(call=>call.method==='POST'&&call.path===base+'/attempts/'+attempt+'/save').length;
  session.setItem('ga-setup-csrf-user',id(99));
  await act(async()=>{dom.window.dispatchEvent(new dom.window.Event('focus'));});
  await until(()=>text().includes('会话已变化')&&container.querySelectorAll('input,textarea,select').length===0,
   'actual focus check conceals all private editor controls after a session identity switch');
  for(const phrase of [baseline.business.name,componentBaseline.business.name,'巡查与核对','核对线索',rawA,rawB])
   assert.equal(text().includes(phrase),false,'private business/evidence no longer remains in the DOM: '+phrase);
  assert.equal(container.querySelector('.bfr-readonly'),null);
  assert.deepEqual(snapshot(),oldStorage,'identity concealment preserves the entire original account journal');
  assert.equal(calls.filter(call=>call.method==='POST'&&call.path===base+'/attempts/'+attempt+'/save').length,saveCount,
   'focus/identity recovery must not issue another save command');
 }finally{
  if(root)await act(async()=>{root.unmount();});
  for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}dom.window.close();
 }
});

test('actual parent keeps original-candidate causal warnings after a saved input edit clears dirty, including remount',{timeout:30000,concurrency:false},async()=>{
 class NoResources extends ResourceLoader{fetch(){throw Error('External DOM resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://review-causal-save.invalid/',resources:new NoResources(),pretendToBeVisual:true});
 const session=dom.window.sessionStorage,storage=dom.window.localStorage,causalAttempt=id(190),calls=[],commands=new Map();
 session.setItem('ga-setup-csrf','synthetic-causal-save-token');session.setItem('ga-setup-csrf-user',scope.actorId);session.setItem('ga-setup-csrf-workspace',scope.workspaceId);
 const baseline=original(),immutable=copy(baseline),causality={schema:'business-flow-causal-source-view-v190',current_draft_matches_original:true,original_candidate_only:true,runtime_entry_binding_verified:false,rows:[{
  flow_index:0,flow_name:baseline.business.flows[0].name,node_id:'check',step_name:'核对线索',input_name:'异常线索',sources:[{kind:'step',step_name:'整理异常线索',output_name:'原候选线索汇总',actor:'data_agent',role:'资料管理员'},{kind:'entry',label:'流程触发或配置需提供的资料（待绑定）'}],citations:[A],
 }]},originalCausality=copy(causality);
 const row={attempt_id:causalAttempt,name:baseline.business.name,version:0,value:copy(baseline),note:'原人工说明',baseline_value:copy(baseline),diagnostics:diagnostics(baseline),source_binding:{schema:'business-flow-review-source-v1',attempt_id:causalAttempt,generation_profile:'flow-v190'},generation_causality:copy(causality),saved:false,source_available:true,can_apply:false,can_publish:false,can_execute:false,semantic_quality_verified:false};
 const prefix=`business-flow-review:v1:${scope.workspaceId}:${scope.actorId}:${scope.producerId}:`,pointer=()=>session.getItem(prefix+'tab-pointer'),working=()=>JSON.parse(storage.getItem(pointer())),form=()=>working()?.forms[causalAttempt];
 const fetcher=async(url,options={})=>{
  const path=String(url),method=options.method||'GET';calls.push({path,method,body:options.body});
  assert.equal(options.headers?.['X-Workspace-ID'],scope.workspaceId);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-causal-save-token');
  let result;
  if(path==='/api/me')result={user_id:scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  else if(path===base+'/candidates?producer_id='+scope.producerId)result={items:[{attempt_id:causalAttempt,name:row.name,status:'needs_clarification',saved_version:row.version}]};
  else if(path===base+'/attempts/'+causalAttempt&&method==='GET')result=copy(row);
  else if(path.startsWith(base+'/requests/')){
   const nonce=path.split('/requests/')[1].split('?')[0],expected=new URLSearchParams(path.split('?')[1]).get('expected_sha256'),stored=commands.get(nonce);
   if(stored)assert.equal(stored.sha,expected);
   result={schema:'business-flow-review-original-result-v1',request_id:nonce,found:!!stored,body_sha256:expected,result:stored?.result||null};
  }else if(path===base+'/attempts/'+causalAttempt+'/save'&&method==='POST'){
   const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['request_id','expected_version','value','note']);assert.equal(body.expected_version,row.version);
   assert.equal(working().pending.raw,options.body,'the real controller persists the original save bytes before HTTP');
   const sha=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(options.body))).toString('hex');
   // The actual save receipt omits generation_causality. The controller keeps
   // the previously read true DTO, while the immutable model stays unchanged.
   result={schema:'business-flow-review-save-result-v1',id:id(191),attempt_id:causalAttempt,request_id:body.request_id,version:row.version+1,status:'incomplete',source_binding:copy(row.source_binding),baseline_value:copy(immutable),value:copy(body.value),note:body.note,diagnostics:diagnostics(body.value),source_binding_hash:'b'.repeat(64),baseline_hash:'c'.repeat(64),semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
   assert.equal(Object.hasOwn(result,'generation_causality'),false);
   commands.set(body.request_id,{sha,result:copy(result)});Object.assign(row,copy(result),{saved:true});
  }else throw Error('Unexpected causal save transport path: '+method+' '+path);
  return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
 };
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,options,fn)=>{assert.equal(options.mode,'exclusive');return fn();}}});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,localStorage:storage,sessionStorage:session,fetch:fetcher,crypto:webcrypto,requestAnimationFrame:fn=>setTimeout(fn,0),cancelAnimationFrame:clearTimeout,IS_REACT_ACT_ENVIRONMENT:true};
 const previous=new Map(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const container=dom.window.document.getElementById('root');let root,guard;
 const buttons=()=>[...container.querySelectorAll('button')],text=()=>container.textContent||'',card=()=>container.querySelector('[aria-label="资料来源核对"]');
 async function until(check,label){const deadline=Date.now()+5000;while(Date.now()<deadline){await act(async()=>{await tick();});if(check())return;}assert.ok(check(),label);}
 async function click(label){const node=buttons().find(button=>button.textContent===label);assert.ok(node,'actual DOM button: '+label);assert.equal(node.disabled,false);await act(async()=>{node.click();});}
 const assertOriginalWarning=()=>{
  assert.ok(card()?.querySelector('[role="alert"]')?.textContent.includes('本稿已修改'),'saved values that differ from the immutable model remain changed');
  for(const phrase of ['仅对应原 AI 候选','不能当作当前稿的来源核验结果','原候选所需资料：异常线索','整理异常线索','原候选线索汇总','流程入口资料：待绑定','尚未证明该资料真实存在'])assert.ok(card().textContent.includes(phrase),phrase);
  assert.equal(card().textContent.includes('人工另行补充的现场照片'),false,'original sources are not rebound to the edited input name');
  assert.deepEqual(row.baseline_value,immutable);assert.deepEqual(row.generation_causality,originalCausality);
 };
 try{
  const {createRoot}=await import('react-dom/client');root=createRoot(container);
  const render=()=>root.render(React.createElement(React.StrictMode,null,React.createElement(module.default,{...scope,onRegisterGuard:(next,old)=>{if(next)guard=next;else if(guard===old)guard=null;}})));
  await act(async()=>{render();});await until(()=>buttons().some(button=>button.textContent.startsWith(row.name)),'normal current-identity candidate list loads');
  const candidate=buttons().find(button=>button.textContent.startsWith(row.name));await act(async()=>{candidate.click();});
  await until(()=>!!card()&&!!form(),'actual parent loads the v190 model and source DTO');
  assert.equal(form().dirty,false);assert.deepEqual(form().value,immutable);assert.equal(card().querySelector('[role="alert"]'),null,'matching untouched model has no changed-draft warning');
  const input=container.querySelector('textarea[aria-label="需要哪些输入（每行一项）"]');assert.ok(input);assert.equal(input.value,'异常线索');
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'人工另行补充的现场照片');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  assert.equal(form().dirty,true);assert.deepEqual(form().value.business.flows[0].nodes[0].inputs,['人工另行补充的现场照片']);assertOriginalWarning();
  const changed=copy(form().value);await click('保存未完成草稿');
  await until(()=>form()?.version===1&&form().dirty===false&&text().includes('草稿已保存并核对原回执'),'normal CAS receipt really clears dirty');
  assert.deepEqual(form().value,changed);assert.equal(working().pending,null);assert.equal(row.version,1);assertOriginalWarning();
  const posts=calls.filter(call=>call.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].body).value,changed);assert.ok(calls.findIndex(call=>call.path.includes('/requests/'))<calls.indexOf(posts[0]));
  assert.ok(container.querySelector('.bfr-state').textContent.includes('当前稿已与读取版本对齐'),'server-save alignment must not turn into source verification');
  await click('查看原文依据 1');await until(()=>container.querySelector('[data-focused-source]')?.textContent.includes(rawA),'the original citation still opens the original platform source');assertOriginalWarning();
  await act(async()=>{root.unmount();});assert.equal(guard,null);root=createRoot(container);await act(async()=>{render();});
  await until(()=>!!card()&&form()?.version===1,'a fresh parent reads the saved value and unchanged original baseline');
  assert.equal(form().dirty,false);assert.deepEqual(form().value,changed);assertOriginalWarning();assert.equal(calls.filter(call=>call.method==='POST').length,1,'remount cannot resave or adopt the candidate');assert.deepEqual(baseline,immutable);
 }finally{
  if(root)await act(async()=>{root.unmount();});
  for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}dom.window.close();
 }
});

test('actual parent viewer with no local qualification reads server history, opens its saved recursive configuration and never requests execution context or writes',{timeout:30000,concurrency:false},async()=>{
 const f=await executionFixture(),ctx=await f.context(0),value=initialNestedConfiguration(ctx),saved_at='2026-10-24T20:30:00+00:00';value.flows[0].business_name='服务器保存的只读业务配置';value.buffers.user_number_text='1e';const value_hash=await graphWireDigest(value,{crypto:webcrypto}),q={...f.q,version:1,status:'engineering_reviewed',request_id:id(901),save_request_id:id(902),value_hash:await graphWireDigest(f.q.value,{crypto:webcrypto}),source_snapshot_hash:'b'.repeat(64),review_record_hash:'d'.repeat(64),human_review:'not_human_review',reviewed_at:saved_at,reviewed_by:f.scope.actorId,source_binding:copy(f.detail.source_binding),ack_scope:true,ack_inferences:true,reason:'公开服务器历史中的独立工程检查，仍非人审'};
 f.dto={schema:'business-flow-nested-configuration-view-v1',id:id(903),actor_id:f.scope.actorId,workspace_id:f.scope.workspaceId,qualification_id:q.id,qualification_hash:q.qualification_hash,source_plan_hash:ctx.source_plan_hash,scope_binding_hash:ctx.scope_binding_hash,saved:true,version:1,value,value_hash,saved_at,history:[{version:1,request_id:id(904),body_sha256:'e'.repeat(64),value_hash,saved_at}],review_mode:q.review_mode,simulation_only:true,value_schema:q.value_schema,current_source_checked:false,scope_binding_checked:false,semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};f.c.dispose();f.mode.readOnly=true;
 class NoResources extends ResourceLoader{fetch(){throw Error('External DOM resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://viewer-recursive-history.invalid/',resources:new NoResources(),pretendToBeVisual:true}),session=dom.window.sessionStorage,storage=dom.window.localStorage,calls=[];session.setItem('ga-setup-csrf','public-nested-fixture');session.setItem('ga-setup-csrf-user',f.scope.actorId);session.setItem('ga-setup-csrf-workspace',f.scope.workspaceId);
 const fetcher=async(url,options={})=>{const path=String(url),method=options.method||'GET';calls.push({path,method});assert.equal(method,'GET','a demoted original owner performs read-only requests through the real parent transport');assert.equal(options.headers?.['X-Workspace-ID'],f.scope.workspaceId);assert.equal(options.headers?.['X-CSRF-Token'],'public-nested-fixture');const unprefixed=path.slice('/api'.length);let result;if(unprefixed==='/setup/business-flow-reviews/attempts/'+f.detail.attempt_id+'/nested-reviews')result=[q];else if(unprefixed.startsWith('/setup/business-flow-executions/'))return new Response(await f.typedHTTP(unprefixed,options),{status:200,headers:{'Content-Type':'application/json'}});else result=await f.options.api(unprefixed,options);return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:f.options.locks});const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,localStorage:storage,sessionStorage:session,fetch:fetcher,crypto:webcrypto,requestAnimationFrame:fn=>setTimeout(fn,0),cancelAnimationFrame:clearTimeout,IS_REACT_ACT_ENVIRONMENT:true},previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const container=dom.window.document.getElementById('root'),buttons=(owner=container)=>[...owner.querySelectorAll('button')];let root;
 async function until(check,label){const deadline=Date.now()+6000;while(Date.now()<deadline){await act(async()=>{await tick();});if(check())return;}assert.ok(check(),label);}
 async function click(label,owner=container){const matches=buttons(owner).filter(button=>button.textContent===label);assert.equal(matches.length,1,'unique actual parent control '+label);assert.equal(matches[0].disabled,false,'read-only owner can use '+label);await act(async()=>matches[0].click());}
 try{assert.equal(storage.length,0,'a fresh local device has no selected or cached qualification');const{createRoot}=await import('react-dom/client');root=createRoot(container);await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(module.default,{...f.scope}))));await until(()=>buttons().some(button=>button.querySelector('strong')?.textContent===f.detail.name),'viewer sees its real candidate list');const candidate=buttons().find(button=>button.querySelector('strong')?.textContent===f.detail.name);assert.equal(candidate.disabled,false);await act(async()=>candidate.click());await until(()=>!!container.querySelector('[aria-label="递归流程版本核对"]'),'the real parent dispatches recursive saved-version review');assert.equal(container.querySelector('[aria-label="递归流程实际执行配置"]'),null,'no qualification has been selected from a prior local device');const review=container.querySelector('[aria-label="递归流程版本核对"]');assert.equal(review.querySelector('select').disabled,true,'viewer cannot change or submit a review mode');await click('读取递归核对历史',review);await until(()=>!!container.querySelector('[aria-label="已保存的递归核对历史"]'),'history arrives through the normal GET route');const history=container.querySelector('[aria-label="已保存的递归核对历史"]');assert.ok(history.textContent.includes('工程模拟检查（非人工业务审查）'));await click('打开此核对版本的递归执行配置',history);await until(()=>container.querySelector('[aria-label="递归流程实际执行配置"]')?.textContent.includes('服务器保存的只读业务配置'),'the parent selects the server qualification and child reads the saved draft');const editor=container.querySelector('[aria-label="递归流程实际执行配置"]');assert.ok(editor.textContent.includes('当前账号为只读'));assert.ok(editor.textContent.includes('仅工程模拟（SIM）'));assert.equal(editor.querySelector('input,textarea,select'),null,'no editable or adoptable mapping is manufactured for the viewer');const retained=container.querySelector('[aria-label="保留的执行草稿与原请求"]');assert.ok(retained);await click('打开此版本的执行配置',retained);assert.ok(calls.some(call=>call.path.endsWith('/nested-reviews')));assert.ok(calls.some(call=>call.path.endsWith('/qualifications/'+q.id+'/nested-draft')));assert.equal(calls.some(call=>call.path.includes('/nested-context')),false);assert.equal(calls.some(call=>call.method!=='GET'),false);assert.equal(f.posts().length,0);const journal=JSON.parse(storage.getItem(session.getItem(`business-flow-review:v1:${f.scope.workspaceId}:${f.scope.actorId}:${f.scope.producerId}:tab-pointer`)));assert.equal(journal.executionSelected[f.detail.attempt_id],q.id);assert.equal(journal.nestedExecutionEditors['qualification:'+q.id].form.value_raw.includes('1e'),true,'original incomplete numeric buffer survives the parent read path');}
 finally{if(root)await act(async()=>root.unmount());for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}dom.window.close();}
});

for(const recoveredComponent of [false,true])test('actual '+(recoveredComponent?'194 independently recovered failed':'193')+' parent edits recursive branches, preserves human work through lost save/reload, and blocks old adoption UI',{timeout:30000,concurrency:false},async()=>{
 class NoResources extends ResourceLoader{fetch(){throw Error('External DOM resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://nested-review.invalid/',resources:new NoResources(),pretendToBeVisual:true}),session=dom.window.sessionStorage,storage=dom.window.localStorage;
 session.setItem('ga-setup-csrf','synthetic-nested-token');session.setItem('ga-setup-csrf-user',scope.actorId);session.setItem('ga-setup-csrf-workspace',scope.workspaceId);
 const nestedAttempt=id(193),baseline=nestedPlan();baseline.business.flows[0].phases[0].decision.inputs=['当前业务资料\n换行仍为同一项','重复资料','重复资料'];baseline.business.flows[0].completion.outputs=['主报告\n同项附页','重复报告','重复报告'];baseline.business.flows.push(copy(baseline.business.flows[0]));baseline.business.flows[1].name='原稿独立第二流程';baseline.business.flows[1].completion.name='第二流程归档工作';
 const immutable=copy(baseline),sourceView=nestedCausality(),calls=[],commands=new Map();sourceView.rows[0].input_name=baseline.business.flows[0].phases[0].decision.inputs[0];let loseReply=true;
 const row={attempt_id:nestedAttempt,name:baseline.business.name,version:0,value:copy(baseline),note:'递归原稿待核对',baseline_value:copy(baseline),diagnostics:nestedDiagnostics(baseline),source_binding:{schema:'business-flow-nested-review-source-v193',attempt_id:nestedAttempt,generation_profile:'flow-v193',value_schema:baseline.schema},generation_causality:copy(sourceView),saved:false,source_available:true,can_apply:false,can_publish:false,can_execute:false,semantic_quality_verified:false};
 if(recoveredComponent){row.source_binding={...row.source_binding,schema:'business-flow-nested-component-review-source-v194',original_protocol_valid:false};delete row.generation_causality;row.generation_recovery={schema:'business-flow-nested-component-view-v194',component_path:'/plan',component_only:true,original_protocol_valid:false,failure_code:'original_protocol_validation_failed',issues:[{code:'source_contract_mismatch',path:DECISION_PATH+'/inputs/0'},{code:'original_causality_failed',path:'/causality'}],runtime_entry_binding_verified:false,can_apply:false,can_publish:false,can_execute:false};}
 const recoveryOriginal=copy(row.generation_recovery),assertRecovery=()=>{if(!recoveredComponent)return;assert.ok(container.querySelector('[aria-label="原回答未通过来源核对"]')?.textContent.includes('原回答的输入来源核对未通过；已单独恢复流程草稿供修订，原失败记录保留'));assert.equal(sourceCard(),null,'a failed sidecar cannot appear as causal verification');assert.equal(row.source_binding.original_protocol_valid,false);assert.deepEqual(row.generation_recovery,recoveryOriginal);};
 const prefix=`business-flow-review:v1:${scope.workspaceId}:${scope.actorId}:${scope.producerId}:`,pointer=()=>session.getItem(prefix+'tab-pointer'),working=()=>JSON.parse(storage.getItem(pointer())),form=()=>working()?.forms[nestedAttempt];
 const fetcher=async(url,options={})=>{
  const path=String(url),method=options.method||'GET';calls.push({path,method,body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspaceId);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-nested-token');let result;
  if(path==='/api/me')result={user_id:scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  else if(path===base+'/candidates?producer_id='+scope.producerId)result={items:[{attempt_id:nestedAttempt,name:row.name,status:'needs_clarification',saved_version:row.version}]};
  else if(path===base+'/attempts/'+nestedAttempt&&method==='GET')result=copy(row);
  else if(path.startsWith(base+'/requests/')){const nonce=path.split('/requests/')[1].split('?')[0],sha=new URLSearchParams(path.split('?')[1]).get('expected_sha256'),stored=commands.get(nonce);if(stored)assert.equal(stored.sha,sha);result={schema:'business-flow-review-original-result-v1',request_id:nonce,found:!!stored,body_sha256:sha,result:stored?copy(stored.result):null};}
  else if(path===base+'/attempts/'+nestedAttempt+'/save'&&method==='POST'){
   const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['request_id','expected_version','value','note']);assert.equal(body.expected_version,row.version);assert.equal(working().pending.raw,options.body);assert.equal(body.value.schema,'report-business-flow-nested-plan-v192');assert.equal(body.value.business.flows[0].nodes,undefined,'the parent submits the recursive AST, never a flat replacement');
   const sha=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(options.body))).toString('hex');result={schema:'business-flow-review-save-result-v1',id:id(194),attempt_id:nestedAttempt,request_id:body.request_id,version:row.version+1,status:'incomplete',source_binding:copy(row.source_binding),baseline_value:copy(immutable),value:copy(body.value),note:body.note,diagnostics:nestedDiagnostics(body.value),source_binding_hash:'b'.repeat(64),baseline_hash:'c'.repeat(64),semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};commands.set(body.request_id,{sha,result:copy(result)});Object.assign(row,copy(result),{saved:true});if(loseReply){loseReply=false;throw Error('synthetic reply lost after durable save');}
  }else throw Error('Unexpected nested transport path: '+method+' '+path);
  return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
 };
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,options,fn)=>{assert.equal(options.mode,'exclusive');return fn();}}});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,localStorage:storage,sessionStorage:session,fetch:fetcher,crypto:webcrypto,requestAnimationFrame:fn=>setTimeout(fn,0),cancelAnimationFrame:clearTimeout,IS_REACT_ACT_ENVIRONMENT:true};
 const previous=new Map(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const container=dom.window.document.getElementById('root');let root,guard;
 const text=()=>container.textContent||'',buttons=(owner=container)=>[...owner.querySelectorAll('button')],at=path=>container.querySelector('[data-review-path="'+(Array.isArray(path)?'/'+path.join('/'):path)+'"]');
 async function until(check,label){const deadline=Date.now()+5000;while(Date.now()<deadline){await act(async()=>{await tick();});if(check())return;}assert.ok(check(),label);}
 async function click(label,owner=container){const button=buttons(owner).find(node=>node.textContent===label);assert.ok(button,'actual nested control: '+label);assert.equal(button.disabled,false);await act(async()=>{button.click();});}
 async function typeAt(path,next){const owner=at(path),node=owner?.querySelector('input,textarea');assert.ok(node,'actual recursive field at '+path);const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,next);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});}
 const sourceCard=()=>container.querySelector('[aria-label="递归步骤资料来源核对"]');
 try{
  const {createRoot}=await import('react-dom/client');root=createRoot(container);const render=()=>root.render(React.createElement(React.StrictMode,null,React.createElement(module.default,{...scope,onRegisterGuard:(next,old)=>{if(next)guard=next;else if(guard===old)guard=null;}})));
  await act(async()=>{render();});await until(()=>buttons().some(button=>button.textContent.startsWith(row.name)),'193 identity and candidate list load');const candidate=buttons().find(button=>button.textContent.startsWith(row.name));await act(async()=>{candidate.click();});await until(()=>!!container.querySelector('[aria-label="递归流程编辑"]')&&!!form(),'actual parent dispatches nested plan editor');
  for(const phrase of ['条件分支','并行协作','循环核查','空分支：直接结束本分支','全部分支完成后继续','核对已保存的递归流程'])assert.ok(text().includes(phrase),phrase);
  const nestedReview=container.querySelector('[aria-label="递归流程版本核对"]');assert.ok(nestedReview,'recursive authoring dispatches the independent saved-version review family');assert.equal(nestedReview.querySelector('[aria-label="递归流程核对方式"]').value,'author_self_review');assert.equal(buttons(nestedReview).find(button=>button.textContent==='检查已保存的递归流程').disabled,true,'the unsaved candidate must be saved and reviewed before execution configuration is offered');assert.equal(container.querySelector('[aria-label="保存版本的作者核对"]'),null,'the legacy flat author-review family is not used for the recursive plan');assert.equal(container.querySelector('[aria-label="递归流程实际执行配置"]'),null,'no execution configuration is opened or authority invented before a saved qualification');assert.equal(buttons().some(button=>button.textContent==='配置此核对版本的实际执行'),false);
  assert.equal(buttons().some(button=>button.textContent==='＋ 添加连线'),false);assert.equal(calls.some(call=>/author-review|execution/.test(call.path)),false);if(recoveredComponent){assertRecovery();assert.ok(at(DECISION_PATH+'/inputs/0').parentElement.querySelector('[data-original-recovery-issue]'));assert.equal(at(DECISION_PATH+'/inputs/1').parentElement.querySelector('[data-original-recovery-issue]'),null,'recovery issue follows the exact original input ordinal, not duplicate names');assert.equal(buttons().filter(button=>button.textContent.startsWith('定位原候选资料问题')).length,1,'generic causality issue is not guessed as an AST field');await click('定位原候选资料问题 1');await until(()=>dom.window.document.activeElement===at(DECISION_PATH+'/inputs/0').querySelector('textarea'),'the original issue focuses its exact recursive input field');}else assert.equal(sourceCard().querySelector('[role="alert"]'),null);
  await typeAt(DECISION_PATH+'/role','修订后的值班负责人');await typeAt(DECISION_PATH+'/inputs/0','修订后的完整业务资料\n新的现场照片');assert.deepEqual(form().value.business.flows[0].phases[0].decision.inputs,['修订后的完整业务资料\n新的现场照片','重复资料','重复资料'],'a newline edits one ordinal and does not split or deduplicate ports');await typeAt('/business/flows/0/completion/outputs/1','仅修改第二项报告\n项内附注');assert.deepEqual(form().value.business.flows[0].completion.outputs,['主报告\n同项附页','仅修改第二项报告\n项内附注','重复报告']);if(recoveredComponent)assertRecovery();else{assert.ok(sourceCard().textContent.includes('本稿已修改'));assert.ok(sourceCard().textContent.includes('原候选所需资料：当前业务资料'));}
  const beforeMove=copy(form().value),step=at([...STEP_PATH,0]).parentElement,moveStepDown=step.querySelector(':scope > .bfr-row > button[aria-label$="下移"]');assert.ok(moveStepDown,'the action item has its own explicit move control, distinct from its input/output item controls');assert.equal(moveStepDown.disabled,false);await act(async()=>{moveStepDown.click();});assert.equal(form().value.business.flows[0].phases[0].branches[0].phases[0].branches[0].phases[0].steps[0].name,'记录通知回执');await click('撤销最近一次结构修改');assert.deepEqual(form().value,beforeMove);
  const branchPath='/business/flows/0/phases/0/branches/1',branch=at(branchPath),deleteButton=[...branch.querySelectorAll(':scope > .bfr-row button')].find(button=>button.textContent.startsWith('删除'));assert.ok(deleteButton);await act(async()=>{deleteButton.click();});assert.deepEqual(form().value,beforeMove,'opening deletion confirmation does not mutate the draft');await click('取消删除');assert.deepEqual(form().value,beforeMove);
  const removeAgain=[...at(branchPath).querySelectorAll(':scope > .bfr-row button')].find(button=>button.textContent.startsWith('删除'));await act(async()=>{removeAgain.click();});await click('确认删除所选内容');assert.equal(form().value.business.flows[0].phases[0].branches.length,1);await click('撤销最近一次结构修改');assert.deepEqual(form().value,beforeMove,'undo restores the entire empty conditional branch');
  const emptyBranch=at(branchPath),addSteps=buttons(emptyBranch).find(button=>button.textContent.includes('添加依次完成步骤'));assert.ok(addSteps);await act(async()=>{addSteps.click();});assert.equal(form().value.business.flows[0].phases[0].branches[1].phases[0].kind,'steps');await click('撤销最近一次结构修改');assert.deepEqual(form().value,beforeMove);
  const actionOwner=at(DECISION_PATH),details=[...actionOwner.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent==='阶段 1条件核对的依据与说明');assert.ok(details);await act(async()=>{details.querySelector('summary').click();});const choices=[...details.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent==='选择或调整关联原文');assert.ok(choices);await act(async()=>{choices.querySelector('summary').click();});await until(()=>!!details.querySelector('input[aria-label="阶段 1条件核对关联原文 2"]'),'lazy source picker mounts only after opening');const choose=details.querySelector('input[aria-label="阶段 1条件核对关联原文 2"]');await act(async()=>{choose.click();});assert.deepEqual(form().value.business.flows[0].phases[0].decision.attribution.citations,[A,B]);await click('查看关联原文 1',details);assert.ok(container.querySelector('[data-focused-source]')?.textContent.includes('公开原文A'));
  await click('原稿独立第二流程');const deleteFlow=buttons().find(button=>button.textContent==='删除工作流程 2');assert.ok(deleteFlow);await act(async()=>{deleteFlow.click();});await click('确认删除所选内容');assert.equal(form().value.business.flows.length,1);await click('AI计划生成的流程（只读）');assert.ok(text().includes('原稿独立第二流程')&&text().includes('第二流程归档工作'),'original flow 2 remains fully readable after the human draft deletes it');await click('编辑草稿');
  await click('＋ 添加工作流程');await click('＋ 添加工作流程');assert.equal(form().value.business.flows.length,3);await click('AI计划生成的流程（只读）');assert.ok(text().includes('原稿独立第二流程')&&text().includes('第二流程归档工作'),'a human cursor at new flow 3 cannot hide either of the two original source flows');await click('编辑草稿');await click('修复建议审批与并行补核');
  const changed=copy(form().value);await click('保存未完成草稿');await until(()=>row.version===1&&!!working().pending&&buttons().some(button=>button.textContent==='查询原保存结果'&&!button.disabled),'lost save reply retains the original pending command');const pending=copy(working().pending),stashKey=pointer(),stash=storage.getItem(stashKey);assert.equal(calls.filter(call=>call.method==='POST').length,1);assert.deepEqual(JSON.parse(pending.raw).value,changed);assert.deepEqual(row.baseline_value,immutable);
  await act(async()=>{root.unmount();});root=createRoot(container);await act(async()=>{render();});await until(()=>!!container.querySelector('[aria-label="递归流程编辑"]')&&!!working().pending,'remount restores the recursive edits and original command');assert.equal(storage.getItem(stashKey),stash);assert.deepEqual(working().pending,pending);assert.deepEqual(form().value,changed);assert.equal(guard(),true);
  await click('查询原保存结果');await until(()=>working().pending===null&&form().version===1&&!form().dirty,'original receipt is recovered without another POST');assert.deepEqual(form().value,changed);if(recoveredComponent){assertRecovery();assert.ok(at(DECISION_PATH+'/inputs/0').parentElement.querySelector('[data-original-recovery-issue]'),'clearing dirty after a successful save cannot erase the original input-source failure');assert.equal(row.generation_causality,undefined);}else{assert.ok(sourceCard().textContent.includes('本稿已修改'));assert.ok(sourceCard().textContent.includes('只对应原 AI'));assert.deepEqual(row.generation_causality,sourceView);}assert.equal(calls.filter(call=>call.method==='POST').length,1);assert.equal(calls.some(call=>/author-review|execution/.test(call.path)),false);
  await click('AI计划生成的流程（只读）');assert.ok(container.querySelector('[aria-label="递归流程只读对照"]'));assert.equal(container.querySelector('[aria-label="递归流程只读对照"]').querySelectorAll('input,textarea,select').length,0);assert.ok(text().includes('确认处置建议'));await click('编辑草稿');assert.deepEqual(form().value,changed);if(recoveredComponent){delete row.generation_recovery;await click('刷新已保存候选');const candidates=[...container.querySelectorAll('.bfr-left > button')].filter(button=>button.querySelector('strong')?.textContent===row.name);assert.equal(candidates.length,1,'the actual candidate control is identified by its business name separately from its saved-status label');assert.equal(candidates[0].disabled,false);await act(async()=>{candidates[0].click();});await until(()=>!!container.querySelector('[aria-label="递归流程编辑"]')&&container.querySelector('[aria-label="原回答未通过来源核对"]')?.textContent.includes('详细问题暂不可读取'),'recovered draft is actually reread with only its immutable source binding');assert.ok(container.querySelector('[aria-label="原回答未通过来源核对"]'),'binding alone retains the failure warning when optional recovery details are unavailable');assert.equal(sourceCard(),null);}
  for(const hidden of ['flow-v193',DECISION_PATH,'/entry_ports/0','binding_hash'])assert.equal(text().includes(hidden),false,'internal identifiers do not label the human workflow: '+hidden);
  const snapshot=()=>Object.fromEntries(Array.from({length:storage.length},(_,i)=>{const key=storage.key(i);return[key,storage.getItem(key)];})),journal=snapshot();session.setItem('ga-setup-csrf-user',id(99));await act(async()=>{dom.window.dispatchEvent(new dom.window.Event('focus'));});await until(()=>text().includes('会话已变化')&&!container.querySelector('[aria-label="递归流程编辑"]'),'identity change conceals recursive drafts and original source');assert.equal(text().includes('递归业务核对'),false);assert.equal(text().includes('公开原文A'),false);assert.deepEqual(snapshot(),journal);assert.equal(calls.filter(call=>call.method==='POST').length,1);assert.deepEqual(baseline,immutable);
 }finally{if(root)await act(async()=>{root.unmount();});for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}dom.window.close();}
});

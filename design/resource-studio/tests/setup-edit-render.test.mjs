import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {createServer} from 'vite';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mount} from '../scripts/react-event-harness.mjs';
import {editJournalKey,readEditPending,makeEditPending,writeEditPending} from '../src/setup-edit-contract.mjs';

const hash='a'.repeat(64),account={workspace_id:'edit-workspace',username:'operator',role:'operator'};
const row=(id,changes={})=>({id,type:'step.update',target_label:'核查业务 / 审核步骤',field_label:'负责人',before:'旧负责人',after:'新负责人',citations:[],inference_reason:'只改负责人，仍需核对',effects:['未改步骤和能力绑定保留','本步骤需重新核对规划能力'],dependencies:[],blocked_reasons:[],...changes});
const record=(changes={})=>({id:'analysis-a',contract_version:6,edit_mode:true,status:'review',version:3,result_hash:hash,result:{operations:[],questions:[]},edit_review:[row('op-a')],review_selection:{operation_ids:[],result_hash:hash},edit_receipts:[],materials:[],provider:'qwen',model:'text',source_revision:8,completed_batches:1,total_batches:1,...changes});
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
async function environment(run){
 const previous=Object.fromEntries(['fetch','localStorage','sessionStorage','window','location'].map(key=>[key,globalThis[key]]));
 const local=storage();Object.assign(globalThis,{localStorage:local,sessionStorage:storage(),window:{addEventListener(){},removeEventListener(){}},location:{search:''}});
 const dir=await mkdtemp(join(tmpdir(),'scoped-edit-render-')),components=[];
 const server=await createServer({configFile:false,cacheDir:join(dir,'cache'),server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{await run({local,server,mount:(Component,props)=>{const ui=mount(Component,props);components.push(ui);return ui;}});}
 finally{components.forEach(ui=>ui.unmount());await server.close();await rm(dir,{recursive:true,force:true});Object.assign(globalThis,previous);}
}
const Citation=({citation})=>React.createElement('blockquote',{},citation.quote),Explanation=({text})=>text?React.createElement('p',{},'模型说明待核对：'+text):null;
const baseProps={account,Btn:'button',Citation,Explanation,prepareDraft:async()=>8,onPending(){},locks:{request:async(key,options,run)=>run()}};
function elements(value,out=[]){if(Array.isArray(value))value.forEach(item=>elements(item,out));else if(value&&typeof value==='object'&&value.props){out.push(value);elements(value.props.children,out);}return out;}

test('real v6 review shows human field differences, nearby evidence/effects, and never legacy whole-business controls',async()=>environment(async({server})=>{
 globalThis.fetch=async()=>{throw Error('static render has no requests');};
 const {default:Review}=await server.ssrLoadModule('/src/UnderstandingEditReview.jsx');
 const value=record({edit_review:[row('private-operation',{before:{role:'旧负责人',id:'private-step',execution:{secret:'private-secret'}},after:{role:'新负责人'},citations:[{material_id:'private-material',block_id:'private-block',quote:'核对依据原文'}]}),row('private-dependent',{target_label:'归档步骤',dependencies:['private-operation'],blocked_reasons:['原资料已变化']})]});
 const html=renderToString(React.createElement(Review,{...baseProps,record:value,onRecord(){},onApplied(){}}));
 for(const text of ['旧负责人','新负责人','负责人','影响与能力核对','未改步骤和能力绑定保留','本步骤需重新核对规划能力','核对依据原文','原文引用存在不代表修改含义正确','请分别勾选','原资料已变化'])assert.ok(html.includes(text));
 assert.doesNotMatch(html,/private-operation|private-dependent|private-step|private-secret|private-material|private-block|<pre|checked=""/);
 const {default:Dialog}=await server.ssrLoadModule('/src/UnderstandingDialog.jsx');
 const props={Dialog:({children})=>React.createElement('section',{},children),Btn:'button',Field:({children})=>React.createElement('label',{},children),materials:[],workspace:{instanceProfile:{domain:'核查'},instanceObjects:[]},account,onClose(){},onApplied(){},prepareDraft(){}};
 const actual=renderToString(React.createElement(Dialog,{...props,initialRecord:value}));assert.match(actual,/逐项修改审核/);assert.doesNotMatch(actual,/应用选中的建议|撤销本次应用|新增对象建议/);
 const unknown=renderToString(React.createElement(Dialog,{...props,initialRecord:{...value,contract_version:99,result:{anything:'future'}}}));
 assert.match(unknown,/尚未支持的版本/);assert.doesNotMatch(unknown,/应用选中的建议|用已保存原答重新核对|撤销本次字段修改/);
 const noop=renderToString(React.createElement(Review,{...baseProps,record:record({status:'applied',edit_noop:true}),onRecord(){},onApplied(){}}));assert.match(noop,/没有实际修改/);assert.doesNotMatch(noop,/撤销本次字段修改/);
}));

test('actual component journals before POST and recovers lost apply response using current later-human draft',async()=>environment(async({server,local,mount})=>{
 const {default:Review}=await server.ssrLoadModule('/src/UnderstandingEditReview.jsx');
 let saved=record(),draft={revision:8,configuration:{note:'before'}},applyLost=true;const posts=[],adopted=[];
 const props={...baseProps,record:saved,onRecord(value){props.record=value;},onApplied(value){adopted.push(value);}};
 const key=editJournalKey(account,saved.id);
 globalThis.fetch=async(path,options={})=>{
  const method=options.method||'GET';let answer;
  if(method==='GET'&&path==='/api/setup/understanding/analysis-a')answer=saved;
  else if(method==='GET'&&path==='/api/setup/draft')answer=draft;
  else if(method==='POST'&&path.includes('/edits/')){
   const action=path.split('/').at(-1),body=JSON.parse(options.body),pending=readEditPending(local,key);assert.equal(pending.action,action);assert.deepEqual(pending.body,body);posts.push({action,body});
   const receipt={request_id:body.request_id,action,status:action==='review'?'review':'applied',revision:action==='review'?8:9,result_hash:hash,operation_ids:body.operation_ids};
   saved={...saved,version:saved.version+1,status:action==='review'?'review':'applied',review_selection:{operation_ids:body.operation_ids,result_hash:hash},edit_receipts:[...saved.edit_receipts,receipt]};
   if(action==='review')answer=saved;
   else{draft={revision:10,configuration:{note:'later human draft'}};if(applyLost){applyLost=false;throw TypeError('lost response after commit');}answer={analysis:saved,...draft,receipt};}
  }else throw Error('unexpected request '+method+' '+path);
  return new Response(JSON.stringify(answer),{headers:{'Content-Type':'application/json'}});
 };
 const ui=mount(Review,props);await ui.flush();await ui.input('采用修改：核查业务 / 审核步骤 · 负责人',true);await ui.flush();assert.equal(readEditPending(local,key),null);assert.equal(posts.length,1);
 await ui.click('应用选中的字段修改');assert.equal(posts.filter(row=>row.action==='apply').length,1);assert.notEqual(posts[0].body.request_id,posts[1].body.request_id);
 assert.equal(adopted.length,1);assert.equal(adopted[0].revision,10);assert.equal(adopted[0].configuration.note,'later human draft');assert.equal(readEditPending(local,key),null);
}));

test('reload retains an unknown original review and only explicitly retries its exact body after reading',async()=>environment(async({server,local,mount})=>{
 const {default:Review}=await server.ssrLoadModule('/src/UnderstandingEditReview.jsx');let saved=record(),lose=true;const posts=[],events=[];
 const props={...baseProps,record:saved,onRecord(value){props.record=value;},onApplied(){throw Error('review does not adopt configuration');}};
 globalThis.fetch=async(path,options={})=>{
  if(!options.method){events.push('read');return new Response(JSON.stringify(saved),{headers:{'Content-Type':'application/json'}});}
  assert.equal(path,'/api/setup/understanding/analysis-a/edits/review');const body=JSON.parse(options.body);posts.push(body);events.push('post');assert.deepEqual(readEditPending(local,editJournalKey(account,saved.id)).body,body);
  if(lose){lose=false;throw TypeError('not delivered');}
  const receipt={request_id:body.request_id,action:'review',status:'review',revision:8,result_hash:hash,operation_ids:body.operation_ids};
  saved={...saved,version:4,review_selection:{operation_ids:body.operation_ids,result_hash:hash},edit_receipts:[receipt]};return new Response(JSON.stringify(saved),{headers:{'Content-Type':'application/json'}});
 };
 const first=mount(Review,props);await first.flush();await first.input('采用修改：核查业务 / 审核步骤 · 负责人',true);await first.flush();assert.equal(posts.length,1);assert.ok(readEditPending(local,editJournalKey(account,saved.id)));first.unmount();
 const second=mount(Review,props);await second.flush();assert.equal(posts.length,1);assert.equal(second.byAria('采用修改：核查业务 / 审核步骤 · 负责人').props.checked,true);
 await second.click('核对原操作结果');assert.equal(posts.length,1);await second.click('核对后重试原操作');assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);assert.equal(events[events.lastIndexOf('post')-1],'read');assert.equal(readEditPending(local,editJournalKey(account,saved.id)),null);
}));

test('SetupStudio edit entry is explicit and binding review only clears the exact current snapshot marker',async()=>environment(async({server,mount})=>{
 const {default:Studio}=await server.ssrLoadModule('/src/SetupStudio.jsx');const updates=[],understood=[];
 const auto={id:'auto-step',name:'自动核查',purpose:'原说明',agent:'业务智能体',role:'',output:'候选',sourceId:'',citation:'人工核对',condition:'',nextId:'human-step',elseId:'end',execution:{capability_id:'kept-cap'},planning_tools:[{capability_id:'old-human-tool'}],bindingReview:{reason:'行为变化'}};
 const human={...auto,id:'human-step',name:'人工审核',agent:'人工确认',role:'负责人',nextId:'end',execution:undefined,planning_tools:[{capability_id:'kept-tool',allow_external:false}]};
 delete human.execution;
const workspace={version:1,instanceProfile:{name:'核查',domain:'核查',goal:'原目标'},businesses:['业务'],materials:[],resources:[],flows:{'业务':{trigger:'人工启动',output:'记录',steps:[auto,human]}},setupNotes:{'业务':'只改负责人'}};
 const props={runtime:true,account,step:1,workspace,onCommit(value){updates.push(value);return true;},onUnderstand(...value){understood.push(value);},Icon:'span',Btn:'button',Field:'label',Dialog:'section'};
 const ui=mount(Studio,props);await ui.flush();await ui.click('预览修改');assert.deepEqual(understood,[['只改负责人','edit','new']]);
 let child=elements(ui.render()).find(node=>node.type?.name==='WorkflowAbilityConfig');assert.ok(child);
 child.props.onRemoveIncompatibleBinding(structuredClone(child.props.node),'execution');await ui.flush();assert.equal(updates.length,0);
 child.props.onRemoveIncompatibleBinding(structuredClone(child.props.node),'planning_tools');await ui.flush();assert.equal(Object.hasOwn(updates.at(-1).flows['业务'].steps[0],'planning_tools'),false);assert.deepEqual(updates.at(-1).flows['业务'].steps[0].bindingReview,auto.bindingReview);
 child=elements(ui.render()).find(node=>node.type?.name==='WorkflowAbilityConfig');const original=structuredClone(child.props.node);
 child.props.onChange({capability_id:'changed-cap'});await ui.flush();assert.deepEqual(updates.at(-1).flows['业务'].steps[0].bindingReview,auto.bindingReview);
 const priorCount=updates.length;child.props.onBindingReviewed(original);await ui.flush();assert.equal(updates.length,priorCount,'stale snapshot must not clear marker');
 child=elements(ui.render()).find(node=>node.type?.name==='WorkflowAbilityConfig');child.props.onBindingReviewed(structuredClone(child.props.node));await ui.flush();
 const steps=updates.at(-1).flows['业务'].steps;assert.equal(Object.hasOwn(steps[0],'bindingReview'),false);assert.equal(steps[0].execution.capability_id,'changed-cap');assert.deepEqual(steps[1],human);
}));

test('reload of a fenced request explicitly preserves the original before loading current server choices',async()=>environment(async({server,local,mount})=>{
 const {default:Review}=await server.ssrLoadModule('/src/UnderstandingEditReview.jsx');
 let saved=record({edit_review:[row('op-a'),row('op-b',{target_label:'归档步骤'})]}),lose=true;const posts=[];let reads=0;
 const key=editJournalKey(account,saved.id),props={...baseProps,record:saved,onRecord(value){props.record=value;},onApplied(){throw Error('review recovery must not apply');}};
 globalThis.fetch=async(path,options={})=>{
  if(!options.method){assert.equal(path,'/api/setup/understanding/analysis-a');reads++;return new Response(JSON.stringify(saved));}
  assert.ok(path.endsWith('/edits/review'));const body=JSON.parse(options.body);posts.push(body);
  if(lose){lose=false;throw TypeError('lost before delivery');}
  const receipt={request_id:body.request_id,action:'review',status:'review',revision:8,result_hash:hash,operation_ids:body.operation_ids};
  saved={...saved,version:saved.version+1,review_selection:{operation_ids:body.operation_ids,result_hash:hash},edit_receipts:[receipt]};return new Response(JSON.stringify(saved));
 };
 const first=mount(Review,props);await first.flush();await first.input('采用修改：核查业务 / 审核步骤 · 负责人',true);await first.flush();const original=readEditPending(local,key);assert.ok(original);first.unmount();
 saved={...saved,version:4,review_selection:{operation_ids:['op-b'],result_hash:hash}};props.record=saved;
 const ui=mount(Review,props);await ui.flush();await ui.click('核对原操作结果');const before=reads;await ui.click('保留原请求并重新核对选择');assert.ok(reads>before,'release must GET again');
 assert.equal(posts.length,1);assert.equal(readEditPending(local,key),null);const archive=JSON.parse(local.getItem(key+':retained:'+original.body.request_id));assert.deepEqual(archive.original,original);assert.equal(archive.observation.current_version,4);
 assert.equal(ui.byAria('采用修改：核查业务 / 审核步骤 · 负责人').props.checked,false);assert.equal(ui.byAria('采用修改：归档步骤 · 负责人').props.checked,true);
 await ui.input('采用修改：核查业务 / 审核步骤 · 负责人',true);await ui.flush();assert.equal(posts.length,2);assert.notEqual(posts[1].request_id,posts[0].request_id);assert.deepEqual(posts[1].operation_ids,['op-b','op-a']);
}));

test('real recovery keeps pending on archive failure and never deletes a later page request',async()=>environment(async({server,local,mount})=>{
 const {default:Review}=await server.ssrLoadModule('/src/UnderstandingEditReview.jsx'),original=makeEditPending('review',record(),['op-a'],{request_id:'10000000-0000-4000-8000-000000000011'}),key=editJournalKey(account,'analysis-a');
 writeEditPending(local,key,original);const saved=record({version:4}),props={...baseProps,record:saved,onRecord(value){props.record=value;},onApplied(){throw Error('must not apply');}};
 globalThis.fetch=async(path,options={})=>{assert.equal(options.method,undefined);return new Response(JSON.stringify(saved));};
 const ui=mount(Review,props);await ui.flush();await ui.click('核对原操作结果');const set=local.setItem;local.setItem=(name,value)=>{if(name.includes(':retained:'))throw Error('quota');set(name,value);};
 await ui.click('保留原请求并重新核对选择');assert.deepEqual(readEditPending(local,key),original);
 local.setItem=set;await ui.click('核对原操作结果');const newer=makeEditPending('review',saved,[],{request_id:'10000000-0000-4000-8000-000000000012'});
 local.setItem=(name,value)=>{set(name,value);if(name.includes(':retained:'))set(key,JSON.stringify(newer));};
 await ui.click('保留原请求并重新核对选择');assert.deepEqual(readEditPending(local,key),newer);assert.deepEqual(JSON.parse(local.getItem(key+':retained:'+original.body.request_id)).original,original);
}));

test('legacy existing upsert is explained and cannot apply, while saved selection can be removed and history stays truthful',async()=>environment(async({server,mount})=>{
 const {default:Dialog}=await server.ssrLoadModule('/src/UnderstandingDialog.jsx');
 const business={operation:'upsert',original_name:'现有业务',name:'现有业务',trigger:'人工启动',output:'记录',steps:[],citations:[],inference_reason:'旧建议'};
 let saved={...record(),contract_version:5,edit_mode:false,result:{domain:'核查',objects:[],businesses:[business],questions:[]},review_selection:{names:['现有业务'],object_names:[],accept_domain:false},before_context:{businesses:['现有业务'],flows:{'现有业务':{trigger:'人工启动',output:'记录',steps:[]}}}};
 const props={Dialog:({children})=>React.createElement('section',{},children),Btn:'button',Field:({children})=>React.createElement('label',{},children),materials:[],workspace:{businesses:['现有业务'],instanceProfile:{domain:'核查'},instanceObjects:[]},account,initialRecord:saved,onClose(){},onApplied(){throw Error('legacy update must not apply');},prepareDraft:async()=>8};
 const staticHTML=renderToString(React.createElement(Dialog,props));assert.match(staticHTML,/已有业务请使用逐项修改入口，原建议保留/);
 const historical=renderToString(React.createElement(Dialog,{...props,initialRecord:{...saved,status:'applied'}}));assert.doesNotMatch(historical,/已有业务请使用逐项修改入口/);
 const posts=[];globalThis.fetch=async(path,options={})=>{let value;if(!options.method)value=path.endsWith('/models')?[]:path==='/api/setup/understanding'?[saved]:saved;else{assert.ok(path.endsWith('/review'));posts.push(JSON.parse(options.body));saved={...saved,version:4,review_selection:posts.at(-1)};value=saved;}return new Response(JSON.stringify(value));};
 const ui=mount(Dialog,props);await ui.flush();assert.equal(ui.button('应用选中的建议').props.disabled,true);assert.equal(ui.byAria('应用建议：现有业务').props.disabled,false);
 await ui.input('应用建议：现有业务',false);await ui.flush();assert.equal(ui.byAria('应用建议：现有业务').props.disabled,true);assert.deepEqual(posts.at(-1).names,[]);assert.equal(ui.button('应用选中的建议').props.disabled,true);
}));

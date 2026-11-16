import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {applyJournalKey,makeApplyPending,readApplyPending,digestApplyValue,digestApplyText,normalizedApplyBody,verifyApplyReceipt} from '../src/understanding-apply-contract.mjs';
import {stableStringify} from '../src/content-signature.js';

const account={workspace_id:'synthetic-apply-workspace',user_id:'synthetic-actor',username:'operator',role:'operator'};
const hash='a'.repeat(64),configurationHash='b'.repeat(64),nonce='10000000-0000-4000-8000-000000000001';
const workspace=()=>({instanceProfile:{name:'合成配置',domain:'核查',goal:'保留人工目标'},instanceObjects:[],businesses:[],materials:[],flows:{}});
const record=()=>({id:'analysis-a',version:2,contract_version:5,status:'review',result_hash:hash,apply_basis:{revision:7,configuration_hash:configurationHash},provider:'qwen',model:'synthetic',source_revision:7,completed_batches:1,total_batches:1,materials:[],result:{domain:'核查',objects:[],questions:[],businesses:[{name:'新增业务',operation:'upsert',trigger:'人工',output:'记录',steps:[],citations:[],inference_reason:'仅供合成测试'}]},review_selection:{names:['新增业务'],object_names:[],accept_domain:false}});
const finish=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const nodes=(value,out=[])=>{if(Array.isArray(value))value.forEach(item=>nodes(item,out));else if(value&&typeof value==='object'&&value.props){out.push(value);nodes(value.props.children,out);}return out;};
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):['string','number'].includes(typeof value)?String(value):'';
async function receiptFor(pending,configuration={...workspace(),businesses:['新增业务']}){const value={schema:'setup-understanding-apply-v1',status:'applied',...pending.scope,request_id:pending.body.request_id,command_hash:pending.command_hash,original_version:pending.body.version,original_revision:pending.body.revision,result_hash:pending.body.result_hash,configuration_hash:pending.body.configuration_hash,draft_id:'draft-a',applied_revision:8,applied_configuration_hash:await digestApplyValue(configuration)};return {...value,receipt_hash:await digestApplyValue(value)};}
let Component,temporary;
test.before(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'understanding-apply-events-'));
 const output=await build({configFile:false,cacheDir:join(temporary,'cache'),logLevel:'silent',build:{ssr:resolve('src/UnderstandingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:id}}}});
 // runtime-api reads sessionStorage on import. This dummy has no session values.
 const previous=globalThis.sessionStorage;globalThis.sessionStorage={getItem:()=>null};
 try{const target=join(temporary,'UnderstandingDialog.mjs');await writeFile(target,output.output.find(item=>item.type==='chunk'&&item.isEntry).code);Component=(await import(pathToFileURL(target))).default;}finally{if(previous===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=previous;}
});
test.after(async()=>{await rm(temporary,{recursive:true,force:true});});
async function environment(run){
 const previous=Object.fromEntries(['fetch','localStorage','sessionStorage','window'].map(key=>[key,globalThis[key]])),previousNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator');
 const cache=new Map(),storage={getItem:key=>cache.get(key)??null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};
 Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,window:{addEventListener(){},removeEventListener(){}}});Object.defineProperty(globalThis,'navigator',{value:{locks:{request:async(key,options,fn)=>fn()}},configurable:true});
 const instances=[];let props;
 const setup=(changes={})=>{props={account,Dialog:'section',Btn:'button',Field:'label',materials:[],workspace:workspace(),initialRecord:record(),prepareDraft:async()=>7,onClose(){},onApplied(){},...changes};const ui=mount(Component,props);instances.push(ui);return ui;};
 try{await run({storage,cache,setup,getProps:()=>props});}finally{instances.forEach(ui=>ui.unmount());Object.assign(globalThis,previous);if(previousNavigator)Object.defineProperty(globalThis,'navigator',previousNavigator);else delete globalThis.navigator;}
}
function fakeServer({storage,lost=false,save=true,current=true,receiptReadFails=false,onPost,onRead}={}){
 const state={remote:record(),receipt:null,bodies:[],calls:[],rejectApply:false,draft:{revision:8,configuration:{...workspace(),businesses:['新增业务']}}};
 globalThis.fetch=async(path,options={})=>{
  const method=options.method||'GET';state.calls.push({path,method});
  if(path.endsWith('/models'))return finish([]);
  if(path==='/api/setup/understanding')return finish([state.remote]);
  if(path==='/api/setup/understanding/analysis-a')return finish(state.remote);
  if(path==='/api/setup/draft')return finish(state.draft);
  if(path.includes('/apply-receipts/')){onRead?.(state);if(!state.receipt||receiptReadFails)return finish({detail:'服务器暂未查到此原应用回执'},404);return finish({state:'saved',analysis:state.remote,receipt:state.receipt,current_matches:current,current_revision:current?8:9,...(current?{revision:8,configuration_encoding:'python-json-sha256-v1',configuration_json:stableStringify(state.draft.configuration)}:{})});}
  if(path.endsWith('/apply')){
   const pending=readApplyPending(storage,applyJournalKey(account),account),body=JSON.parse(options.body);assert.ok(pending,'journal is persisted and readable before POST');assert.deepEqual(pending.body,body);state.bodies.push(body);await onPost?.(state,pending);
   if(state.rejectApply)return finish({detail:'配置已修改，原请求不能应用'},409);
   if(save){state.receipt=await receiptFor(pending);state.remote={...state.remote,status:'applied',version:3};}
   if(lost||!save)throw new TypeError('synthetic lost response');
   return finish({state:'saved',analysis:state.remote,receipt:state.receipt,current_matches:true,current_revision:8,...state.draft});
  }
  throw Error('unexpected synthetic request '+method+' '+path);
 };
 return state;
}

test('normalized defaults and digest match the server command contract; corrupted scope/body/receipt fails closed',async()=>{
 const pending=await makeApplyPending(account,record(),{names:['新增业务']},{revision:7,request_id:nonce,workspace:workspace()});
 assert.equal(pending.command_hash,'24d8c74e34e56343e60264ef4d7dca8f8db21a0e6a1da7d47de6786f3f95328f');
 assert.deepEqual(normalizedApplyBody(pending.body),{version:2,revision:7,names:['新增业务'],object_names:[],accept_domain:false,request_id:nonce,result_hash:hash,configuration_hash:configurationHash});
 assert.equal(await digestApplyValue({z:2,a:'中文'}),'27eefaad6ae48bcded34c16186dd52e5789b31add91282164c18d65ccf52e6aa');
 const receipt=await receiptFor(pending),reply={state:'saved',receipt,analysis:{...record(),status:'applied'},current_matches:false,current_revision:9};await verifyApplyReceipt(reply,pending);
 for(const change of [{actor_id:'another-actor'},{configuration_hash:'d'.repeat(64)},{command_hash:'d'.repeat(64)},{applied_revision:10}])await assert.rejects(()=>verifyApplyReceipt({...reply,receipt:{...receipt,...change}},pending));
 const pythonJSON='{"numeric":1.0,"text":"中文"}',pythonValue={...receipt,applied_configuration_hash:await digestApplyText(pythonJSON)};delete pythonValue.receipt_hash;const pythonReceipt={...pythonValue,receipt_hash:await digestApplyValue(pythonValue)},pythonReply={...reply,receipt:pythonReceipt,current_matches:true,current_revision:8,revision:8,configuration_encoding:'python-json-sha256-v1',configuration_json:pythonJSON};await verifyApplyReceipt(pythonReply,pending);assert.deepEqual(pythonReply.configuration,{numeric:1,text:'中文'});
 await assert.rejects(()=>verifyApplyReceipt({...pythonReply,configuration_json:'{"numeric":2.0,"text":"中文"}'},pending));
});

test('actual dialog recovers a lost successful apply response by readback without another POST',async()=>environment(async({storage,cache,setup})=>{
 let adopted=0;const server=fakeServer({storage,lost:true}),ui=setup({onApplied:async saved=>{adopted++;assert.equal(saved.revision,8);}});await ui.flush();await ui.click('应用选中的建议');
 assert.equal(server.bodies.length,1);assert.equal(adopted,1);assert.equal(readApplyPending(storage,applyJournalKey(account),account),null);assert.ok([...cache.keys()].some(key=>key.includes(':saved:')));
 const order=server.calls.filter(call=>call.path.includes('/apply')).map(call=>call.method);assert.deepEqual(order,['GET','POST','GET','GET']);assert.ok(text(ui.render()).includes('原应用回执与当前配置已核对'));
}));

test('unknown response survives reopen; only explicit retry rechecks then sends the exact original body and nonce',async()=>environment(async({storage,setup})=>{
 const initial=fakeServer({storage,save:false}),ui=setup();await ui.flush();await ui.click('应用选中的建议');const pending=readApplyPending(storage,applyJournalKey(account),account);assert.ok(pending);assert.equal(ui.button('应用选中的建议').props.disabled,true);ui.unmount();
 const later=fakeServer({storage}),second=setup({initialRecord:null});await second.flush();assert.equal(later.bodies.length,0);assert.ok(readApplyPending(storage,applyJournalKey(account),account));await second.click('核对后重试原应用');assert.equal(later.bodies.length,1);assert.deepEqual(later.bodies[0],initial.bodies[0]);assert.equal(readApplyPending(storage,applyJournalKey(account),account),null);
}));

test('changed server draft or local human draft confirms saved receipt without adopting an old configuration',async()=>{
 for(const kind of ['server','local','workspace'])await environment(async({storage,setup,getProps})=>{
  let adopted=0,ui;const server=fakeServer({storage,lost:true,current:kind!=='server',onPost:async()=>{if(kind==='local')storage.setItem('ga-setup:'+account.username+':'+account.workspace_id,JSON.stringify({revision:7,pending:true,configuration:{human:'later input'}}));if(kind==='workspace'){getProps().workspace={...workspace(),instanceProfile:{...workspace().instanceProfile,goal:'后来的人工目标'}};ui.render();}}});
  ui=setup({onApplied:async()=>{adopted++;}});await ui.flush();await ui.click('应用选中的建议');assert.equal(server.bodies.length,1);assert.equal(adopted,0);assert.equal(readApplyPending(storage,applyJournalKey(account),account),null);assert.ok(text(ui.render()).includes('没有覆盖当前配置'));
 });
});

test('successful POST with failed readback or failed adoption preserves confirmed journal and never enables replay',async()=>{
 for(const kind of ['readback','adoption'])await environment(async({storage,setup})=>{
  let adopted=0;const server=fakeServer({storage,receiptReadFails:kind==='readback'}),ui=setup({onApplied:async()=>{adopted++;throw Error('synthetic current draft synchronization failed');}});await ui.flush();await ui.click('应用选中的建议');
  const pending=readApplyPending(storage,applyJournalKey(account),account);assert.ok(pending?.confirmed_receipt);assert.equal(server.bodies.length,1);assert.equal(ui.button('核对后重试原应用').props.disabled,true);assert.equal(nodes(ui.render()).some(node=>typeof node.props.onClick==='function'&&text(node.props.children)==='应用选中的建议'),false);assert.ok(text(ui.render()).includes('原应用已保存'));await ui.click('核对原应用回执');assert.equal(server.bodies.length,1);assert.equal(adopted,kind==='adoption'?2:0);
 });
});

test('journal write/readback failure or another page CAS prevents POST',async()=>{
 for(const kind of ['throw','silent','conflict'])await environment(async({storage,setup})=>{
  const server=fakeServer({storage}),oldSet=storage.setItem;
  if(kind==='throw')storage.setItem=()=>{throw Error('synthetic storage quota');};
  if(kind==='silent')storage.setItem=()=>{};
  if(kind==='conflict'){const other=await makeApplyPending(account,record(),{names:['新增业务']},{revision:7,request_id:nonce,workspace:workspace()});oldSet(applyJournalKey(account),JSON.stringify(other));}
  const ui=setup();await ui.flush();if(kind!=='conflict')await ui.click('应用选中的建议');assert.equal(server.bodies.length,0);
 });
});

test('a 404 then stale-draft 409 can explicitly end waiting only after a durable exact backup; history stays read-only',async()=>environment(async({storage,cache,setup})=>{
 const server=fakeServer({storage,save:false}),ui=setup();await ui.flush();await ui.click('应用选中的建议');const original=readApplyPending(storage,applyJournalKey(account),account);server.rejectApply=true;server.draft.revision=9;await ui.click('核对后重试原应用');assert.equal(server.bodies.length,2);assert.ok(readApplyPending(storage,applyJournalKey(account),account));
 await ui.click('保留原请求并结束等待');assert.ok(text(ui.render()).includes('原操作仍可能稍后完成'));await ui.click('确认保留原请求并结束等待');assert.equal(readApplyPending(storage,applyJournalKey(account),account),null);assert.equal(server.bodies.length,2);
 const archive=[...cache.entries()].find(([key])=>key.includes(':ended:'));assert.ok(archive);assert.deepEqual(JSON.parse(archive[1]).pending,original);await ui.click('重新提出理解要求');assert.ok(text(ui.render()).includes('本次使用的资料'));ui.unmount();
 const second=setup({initialRecord:null});await second.flush();await second.click('查看原建议当前状态');assert.equal(server.bodies.length,2);assert.equal(cache.get(archive[0]),archive[1]);
}));

test('failed or silent unresolved-request backup never clears the active original journal',async()=>{
 for(const kind of ['throw','silent'])await environment(async({storage,setup})=>{
  const server=fakeServer({storage,save:false}),ui=setup();await ui.flush();await ui.click('应用选中的建议');const original=readApplyPending(storage,applyJournalKey(account),account),write=storage.setItem;
  storage.setItem=(key,value)=>{if(key.includes(':ended:')){if(kind==='throw')throw Error('synthetic backup quota');return;}write(key,value);};await ui.click('保留原请求并结束等待');await ui.click('确认保留原请求并结束等待');assert.deepEqual(readApplyPending(storage,applyJournalKey(account),account),original);assert.equal(server.bodies.length,1);assert.ok(text(ui.render()).includes('未结束等待'));
 });
});

test('known-success archive failure or silent drop preserves the confirmed active journal until verified backup',async()=>{
 for(const kind of ['throw','silent'])await environment(async({storage,cache,setup})=>{
  const server=fakeServer({storage}),write=storage.setItem;let adopted=0;storage.setItem=(key,value)=>{if(key.includes(':saved:')){if(kind==='throw')throw Error('synthetic saved-receipt quota');return;}write(key,value);};const ui=setup({onApplied:async()=>{adopted++;}});await ui.flush();await ui.click('应用选中的建议');
  const original=readApplyPending(storage,applyJournalKey(account),account);assert.ok(original?.confirmed_receipt);assert.equal(server.bodies.length,1);assert.equal(adopted,1);assert.equal(ui.button('核对后重试原应用').props.disabled,true);storage.setItem=write;await ui.click('核对原应用回执');assert.equal(server.bodies.length,1);assert.equal(readApplyPending(storage,applyJournalKey(account),account),null);const archived=JSON.parse(cache.get(applyJournalKey(account)+':saved:'+original.body.request_id));assert.deepEqual(archived,original);
 });
});

test('read-only same actor can reconcile existing receipt; a late response cannot apply into another account',async()=>environment(async({storage,setup,getProps})=>{
 const pending=await makeApplyPending(account,record(),{names:['新增业务']},{revision:7,request_id:nonce,workspace:workspace()});storage.setItem(applyJournalKey(account),JSON.stringify(pending));const server=fakeServer({storage});server.receipt=await receiptFor(pending);server.remote={...record(),status:'applied',version:3};let adopted=0;
 const viewer=setup({account:{...account,role:'viewer'},onApplied:async()=>{adopted++;}});for(let i=0;i<20&&adopted===0;i++)await viewer.flush();assert.equal(server.bodies.length,0);assert.equal(adopted,1,text(viewer.render()));assert.equal(readApplyPending(storage,applyJournalKey(account),account),null);viewer.unmount();
 let release,started;const waiting=new Promise(resolve=>{release=resolve;}),began=new Promise(resolve=>{started=resolve;});const late=fakeServer({storage,onPost:async()=>{started();await waiting;}}),ui=setup({onApplied:async()=>{adopted++;}});await ui.flush();const clicking=ui.click('应用选中的建议');await began;
 getProps().account={...account,user_id:'another-actor',username:'other'};ui.render();await ui.flush();release();await clicking;assert.equal(adopted,1);assert.ok(readApplyPending(storage,applyJournalKey(account),account));assert.equal(late.bodies.length,1);
}));

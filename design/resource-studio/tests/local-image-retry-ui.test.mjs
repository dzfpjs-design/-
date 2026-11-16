import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';
import {localImageRetryScope,localImageRetryKey,readLocalImageRetry,stageLocalImageRetry,localImageRetryReceipt} from '../src/local-image-retry.mjs';

const account={workspace_id:'workspace-a',user_id:'operator-a',username:'核查员',role:'operator'},instanceId='instance-a',releaseID='release-r2';
const object={id:'original-r1-entity',version:7,package_id:'original-r1-package',object_compatibility:{usable:true,status:'compatible',target_release_id:releaseID,source_label:'原对象',source_type:'登记对象',source_release_number:1}};
const base='/api/workspace/instances/'+instanceId,mainKey='ga-local-observation:'+account.workspace_id+':'+account.username+':'+instanceId;
const savedForm={entity_id:object.id,extractor_id:'local-model',selected:'observation-a',drafts:{'observation-a':'保留的人工说明原文'}};
const failed=(patch={})=>({id:'observation-a',version:3,status:'failed',entity_id:object.id,created_at:'2026-10-16T09:00:00Z',request_id:'original-upload-request',extractor_id:'local-model',evidence_id:'original-evidence',source_sha256:'a'.repeat(64),source_clock:null,output:null,error:'原处理失败，原图保留',integrity_error:null,data_received:false,review:null,retry_available:true,retry_reason:'可核对原图后重试',retry_attempts:1,retry_limit:3,retry_target_release_id:releaseID,retry_requests:[],retry_receipts_verified:true,...patch});
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const memory=()=>{const rows=new Map();return {getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key)};};
const canonical=value=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}':JSON.stringify(value);
const serverHash=value=>createHash('sha256').update(canonical(value)).digest('hex');
const receipt=(body,patch={})=>({request_id:body.request_id,request_hash:serverHash(body),actor_id:account.user_id,original_version:body.expected_version,queued_version:body.expected_version+1,attempt:2,at:'2026-10-16T09:10:00Z',...patch});
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):typeof value==='string'?value:'';
const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild');let Form;
async function component(){if(!Form){const built=await build({entryPoints:[fileURLToPath(new URL('../src/LocalObservationStudio.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',logLevel:'silent',plugins:[{name:'react',setup(b){b.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});Form=(await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'))).default;}return Form;}
async function environment(run){const keys=['fetch','localStorage','sessionStorage','setInterval','clearInterval'],old=Object.fromEntries(keys.map(key=>[key,globalThis[key]])),storage=memory(),mounted=[];Object.assign(globalThis,{localStorage:storage,sessionStorage:memory(),setInterval:()=>1,clearInterval(){}});storage.setItem(mainKey,JSON.stringify({form:savedForm,action:null}));try{await run({storage,mount:(C,p)=>{const ui=mount(C,p);mounted.push(ui);return ui;}});}finally{mounted.forEach(ui=>ui.unmount());Object.assign(globalThis,old);}}
async function fixture(env,{row=failed(),post,me=account,detail}={}){
 const state={row,posts:[],gets:[],me},props={account,instanceId,objects:[object],releaseID,Btn:'button',Field:'label'};
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){const body=JSON.parse(options.body);state.posts.push({path,body});if(post)return post(state,body);state.row={...state.row,version:body.expected_version+1,status:'received',retry_available:false,retry_attempts:2,retry_requests:[receipt(body)]};return response({id:state.row.id});}state.gets.push(path);if(path==='/api/me')return response(state.me);if(path.endsWith('/local-observation-models'))return response([{id:'local-model',name:'已登记本地模型',version:'1',deployment:'local',candidate_only:true}]);if(path.endsWith('/local-observation-daily'))return response([]);if(path===base+'/local-observations')return response({records:[state.row],has_more:false});if(path===base+'/local-observations/'+state.row.id)return detail?detail(state):response(state.row);throw Error('unexpected '+path);};
 const ui=env.mount(await component(),props);await ui.flush();return {ui,state,props,scope:localImageRetryScope(account,instanceId,row)};
}

test('old R1 failure stays readable but current Entity compatibility cannot grant a new retry',()=>environment(async env=>{
 const {ui,state}=await fixture(env,{row:failed({retry_available:false,retry_reason:'原处理固定于R1，旧授权不迁移',retry_target_release_id:'release-r1'})});assert.equal(ui.button('核对原图后重试本地处理').props.disabled,true);assert.match(text(ui.render()),/原处理固定于R1/);await ui.click('核对原图后重试本地处理');assert.deepEqual(state.posts,[]);assert.deepEqual(JSON.parse(env.storage.getItem(mainKey)).form.drafts,savedForm.drafts);assert.match(text(ui.render()),/采集时间：未知/);
}));

test('current R2 failure stages a fixed nonce and acknowledges only its exact server retry receipt',()=>environment(async env=>{
 const {ui,state,scope}=await fixture(env);assert.equal(ui.button('核对原图后重试本地处理').props.disabled,false);await ui.click('核对原图后重试本地处理');assert.equal(state.posts.length,1);const body=state.posts[0].body;assert.equal(state.posts[0].path,'/api/observations/observation-a/retry');assert.match(body.request_id,/^[a-zA-Z0-9_-]{12,100}$/);assert.equal(body.expected_version,3);assert.equal(Object.hasOwn(body,'release_id'),false);const saved=readLocalImageRetry(env.storage,scope).value;assert.equal(saved.pending,null);assert.deepEqual(saved.history[0].pending.body,body);assert.equal(saved.history[0].receipt.request_hash,serverHash(body));assert.deepEqual(JSON.parse(env.storage.getItem(mainKey)).form.drafts,savedForm.drafts);assert.match(text(ui.render()),/后台尚可能失败/);
}));

test('unknown retry response survives remount and resends exactly the original nonce and body',()=>environment(async env=>{
 let accepted=false;const {ui,state,scope,props}=await fixture(env,{post:(s,body)=>{if(!accepted)throw Error('response lost before receipt');s.row={...s.row,status:'received',version:4,retry_available:false,retry_attempts:2,retry_requests:[receipt(body)]};return response({id:s.row.id});}});await ui.click('核对原图后重试本地处理');const pending=readLocalImageRetry(env.storage,scope).value.pending;assert.ok(pending);assert.equal(state.posts.length,1);ui.unmount();accepted=true;const resumed=env.mount(await component(),props);await resumed.flush();await resumed.click('核对并恢复原处理重试');assert.equal(state.posts.length,2);assert.deepEqual(state.posts[1].body,pending.body);assert.equal(readLocalImageRetry(env.storage,scope).value.pending,null);
}));

test('a lost POST with a committed receipt is recovered by GET without a second processing request',()=>environment(async env=>{
 const {ui,state,scope}=await fixture(env,{post:(s,body)=>{s.row={...s.row,status:'received',version:4,retry_available:false,retry_attempts:2,retry_requests:[receipt(body)]};throw Error('response lost after commit');}});await ui.click('核对原图后重试本地处理');assert.equal(state.posts.length,1);assert.equal(readLocalImageRetry(env.storage,scope).value.pending,null);assert.equal(readLocalImageRetry(env.storage,scope).value.history.length,1);
}));

test('release change permits only old receipt reads and never retags an unknown pending retry',()=>environment(async env=>{
 const scope=localImageRetryScope(account,instanceId,failed()),journal=await stageLocalImageRetry(env.storage,scope,readLocalImageRetry(env.storage,scope),failed(),releaseID),raw=journal.raw;
 const {ui,state,props}=await fixture(env,{row:failed({retry_available:false,retry_reason:'原实例版本已变化'})});props.releaseID='release-r3';ui.render();await ui.flush();await ui.click('核对并恢复原处理重试');assert.deepEqual(state.posts,[]);assert.equal(env.storage.getItem(localImageRetryKey(scope)),raw);assert.match(text(ui.render()),/目前只能核对原回执/);
 state.row={...state.row,status:'received',version:4,retry_requests:[receipt(journal.value.pending.body)]};props.objects=[];props.account={...account,role:'viewer'};ui.render();await ui.click('核对并恢复原处理重试');assert.deepEqual(state.posts,[]);assert.equal(readLocalImageRetry(env.storage,scope).value.pending,null);
}));

test('forged hash or foreign actor receipts cannot clear the original pending journal or cause a new POST',()=>environment(async env=>{
 const scope=localImageRetryScope(account,instanceId,failed()),journal=await stageLocalImageRetry(env.storage,scope,readLocalImageRetry(env.storage,scope),failed(),releaseID),body=journal.value.pending.body;
 for(const patch of [{request_hash:'b'.repeat(64)},{actor_id:'another-actor'},{original_version:2}]){const row=failed({version:4,retry_requests:[receipt(body,patch)]});await assert.rejects(localImageRetryReceipt(row,journal,scope),/无法核对/);}
 const {ui,state}=await fixture(env,{row:failed({version:4,retry_available:false,retry_requests:[receipt(body,{actor_id:'another-actor'})]})});await ui.click('核对并恢复原处理重试');assert.deepEqual(state.posts,[]);assert.equal(env.storage.getItem(localImageRetryKey(scope)),journal.raw);
}));

test('another tab replacing a pending retry cannot be cleared by the older successful response',()=>environment(async env=>{
 let replacement;const {ui,state,scope}=await fixture(env,{post:(s,body)=>{const key=localImageRetryKey(localImageRetryScope(account,instanceId,s.row)),value=JSON.parse(env.storage.getItem(key)),newBody={...body,request_id:'another-tab-fixed-request'};replacement={...value,pending:{...value.pending,body:newBody,body_hash:serverHash(newBody)}};env.storage.setItem(key,JSON.stringify(replacement));s.row={...s.row,version:4,status:'received',retry_available:false,retry_requests:[receipt(body)]};return response({id:s.row.id});}});await ui.click('核对原图后重试本地处理');assert.equal(state.posts.length,1);assert.deepEqual(readLocalImageRetry(env.storage,scope).value,replacement);assert.deepEqual(JSON.parse(env.storage.getItem(mainKey)).form.drafts,savedForm.drafts);
}));

test('journal write failure or changed current actor blocks POST and preserves the human form',()=>environment(async env=>{
 const {ui,state}=await fixture(env);const write=env.storage.setItem;env.storage.setItem=(key,value)=>{if(key.startsWith('ga-local-image-retry:'))throw Error('quota');write(key,value);};await ui.click('核对原图后重试本地处理');assert.deepEqual(state.posts,[]);assert.deepEqual(JSON.parse(env.storage.getItem(mainKey)).form.drafts,savedForm.drafts);env.storage.setItem=write;state.me={...account,user_id:'another-actor'};await ui.click('核对原图后重试本地处理');assert.deepEqual(state.posts,[]);
}));

test('a release change during the fresh eligibility read blocks the late handler before staging or POST',()=>environment(async env=>{
 let delayed=false,release;const gate=new Promise(resolve=>release=resolve);const {ui,state,props,scope}=await fixture(env,{detail:async s=>{if(delayed)await gate;return response(s.row);}});delayed=true;const running=ui.click('核对原图后重试本地处理');await new Promise(resolve=>setImmediate(resolve));props.releaseID='release-r3';ui.render();release();await running;assert.deepEqual(state.posts,[]);assert.equal(readLocalImageRetry(env.storage,scope).value.pending,null);assert.deepEqual(JSON.parse(env.storage.getItem(mainKey)).form.drafts,savedForm.drafts);
}));

test('missing server qualification and reached retry limits fail closed despite a compatible Entity',()=>environment(async env=>{
 for(const patch of [{retry_available:undefined},{retry_receipts_verified:false},{retry_attempts:3},{retry_target_release_id:null}]){const {ui,state}=await fixture(env,{row:failed(patch)});assert.equal(ui.button('核对原图后重试本地处理').props.disabled,true);await ui.click('核对原图后重试本地处理');assert.deepEqual(state.posts,[]);ui.unmount();}
}));

import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';
import {generationHash,generationBody,generationScope,generationKey,listGenerations,stageGeneration} from '../src/instance-generation.mjs';

const account={workspace_id:'workspace-a',user_id:'admin-a',username:'管理员',role:'admin'},scope=generationScope(account),hash='a'.repeat(64),nonce='00000000-0000-4000-8000-000000000001';
const workspace={businesses:[],instanceProfile:{name:'林业实例',domain:'林业',goal:'保留人工目标'},instanceObjects:[{name:' 样地 ',fields:[{label:' 面积 ',kind:'数值',required:true,unit:' 公顷 '}]}],instanceRoleBindings:{'管理员':'admin-a'}};
const checked={configuration_hash:hash,general_issues:[],businesses:[],data_plan:[],sensor_sources:[],relation_definitions:[]};
const instance=id=>({id,name:'林业实例',domain:'林业',created_at:'2026-10-16T10:12:34Z',version:1,status:'draft'});
const body=patch=>generationBody({revision:5,objects:workspace.instanceObjects,role_bindings:workspace.instanceRoleBindings,confirmed_configuration_hash:hash,request_id:nonce,...patch});
const release=(value,number=1)=>({id:'release-'+(value.instance_id||value.request_id),instance_id:value.instance_id||'instance-'+value.request_id,release_number:number,configuration_hash:hash,created_at:'2026-10-16T10:12:35Z',status:'draft',snapshot:{businesses:[],relation_definitions:[],sensor_sources:[]},available_business_ids:[]});
async function accepted(value,number=1){const row=release(value,number);return {saved:true,request_id:value.request_id,request_hash:await generationHash(value),actor_id:scope.actor_id,workspace_id:scope.workspace_id,generation_intent:value.generation_intent,instance_id:row.instance_id,release_id:row.id,release_number:row.release_number,configuration_hash:hash,release:row};}
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
const memory=()=>{const rows=new Map();return {get length(){return rows.size;},key:index=>[...rows.keys()][index]??null,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key)};};
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):['string','number'].includes(typeof value)?String(value):'';
const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild');let Form;
async function component(){if(!Form){const built=await build({entryPoints:[fileURLToPath(new URL('../src/RuntimeReviewStudio.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',loader:{'.css':'empty'},logLevel:'silent',plugins:[{name:'react',setup(b){b.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});Form=(await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'))).default;}return Form;}
async function environment(run){const keys=['fetch','localStorage','sessionStorage'],old=Object.fromEntries(keys.map(key=>[key,globalThis[key]])),storage=memory(),mounted=[];Object.assign(globalThis,{localStorage:storage,sessionStorage:memory()});storage.setItem('existing-human-draft',JSON.stringify(workspace));try{await run({storage,mount:(C,p)=>{const ui=mount(C,p);mounted.push(ui);return ui;}});}finally{mounted.forEach(ui=>ui.unmount());Object.assign(globalThis,old);}}
async function fixture(env,options={}){
 const state={me:account,posts:[],gets:[],receipts:new Map(),instances:[instance('existing')],readbackFailure:false,listingFailure:false,revision:5,...options.state};
 const props={account,workspace:structuredClone(workspace),initialInstanceId:'',Btn:'button',Field:'label',onSave:async()=>state.revision,onCommit(value){props.workspace=value;return true;},onEdit(){},onWorkspace(){},...options.props};
 globalThis.fetch=async(path,request={})=>{if(request.method==='POST'){const value=JSON.parse(request.body);state.posts.push({path,body:value,exact:request.body});if(path==='/api/instances/check')return response(checked);assert.equal(path,'/api/instances');assert.ok(env.storage.getItem(generationKey(scope,value.request_id)),'exact body was saved before POST');if(options.post)return options.post(state,value);const receipt=await accepted(value,value.instance_id?2:1);state.receipts.set(value.request_id,receipt);state.instances.push(instance(receipt.instance_id));return response({...receipt.release,generation_receipt:Object.fromEntries(Object.entries(receipt).filter(([key])=>key!=='release'))});}
  state.gets.push(path);if(path==='/api/me')return options.me?options.me(state):response(state.me);if(path==='/api/instances/members')return response([]);if(path==='/api/instances'){if(state.listingFailure)throw Error('list disconnected');return response(state.instances);}if(path.startsWith('/api/instances/generation-requests/')){const id=path.split('/').at(-1);if(state.readbackFailure&&state.receipts.has(id))throw Error('readback disconnected');return options.receipt?options.receipt(state,id):response(state.receipts.get(id)||{saved:false,request_id:id});}if(path.endsWith('/releases')){const id=path.split('/').at(-2);return options.releases?options.releases(state,id):response([...state.receipts.values()].filter(row=>row.instance_id===id).map(row=>row.release));}throw Error('unexpected '+path);
 };
 const ui=env.mount(await component(),props);await ui.flush();return {ui,state,props};
}
async function generate(ui){await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);await ui.click('保存生成版本');}
const generationPosts=state=>state.posts.filter(row=>row.path==='/api/instances');

test('real component saves explicit independent request before POST, verifies receipt and reads same release',()=>environment(async env=>{
 const {ui,state}=await fixture(env);await generate(ui);const [post]=generationPosts(state);assert.equal(post.body.generation_intent,'new_instance');assert.equal(post.body.instance_id,null);assert.equal(post.body.objects[0].name,'样地');assert.equal(post.body.objects[0].fields[0].unit,'公顷');const journal=listGenerations(env.storage,scope)[0];assert.equal(journal.value.status,'saved');assert.equal(journal.value.exact_body,post.exact);assert.ok(state.gets.indexOf('/api/instances/generation-requests/'+post.body.request_id)<state.gets.indexOf('/api/instances/'+journal.value.receipt.instance_id+'/releases'));assert.match(text(ui.render()),/保存并读回核对|服务器并读回核对/);assert.equal(env.storage.getItem('existing-human-draft'),JSON.stringify(workspace));
}));

test('same configuration creates another independent nonce and selected existing instance sends new_release',()=>environment(async env=>{
 const {ui,state}=await fixture(env);await generate(ui);await ui.input('选择业务实例','');await ui.flush();await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);await ui.click('保存生成版本');assert.equal(generationPosts(state).length,2);assert.notEqual(generationPosts(state)[0].body.request_id,generationPosts(state)[1].body.request_id);assert.equal(listGenerations(env.storage,scope).length,2);await ui.input('选择业务实例','existing');await ui.flush();await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);await ui.click('保存生成版本');assert.equal(generationPosts(state)[2].body.generation_intent,'new_release');assert.equal(generationPosts(state)[2].body.instance_id,'existing');assert.match(text(ui.byAria('选择业务实例')),/创建于.*2026/);
}));

test('lost committed POST is recovered by exact receipt GET without a second POST',()=>environment(async env=>{
 const {ui,state}=await fixture(env,{post:async(s,value)=>{s.receipts.set(value.request_id,await accepted(value));throw Error('response lost');}});await generate(ui);assert.equal(generationPosts(state).length,1);assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.doesNotMatch(text(ui.render()),/暂时无法连接服务器/);
}));

test('unknown request survives remount and draft/selection changes, retry uses only original nonce and body',()=>environment(async env=>{
 let lost=true;const {ui,state,props}=await fixture(env,{post:async(s,value)=>{if(lost)throw Error('unknown');const saved=await accepted(value);s.receipts.set(value.request_id,saved);return response({...saved.release,generation_receipt:Object.fromEntries(Object.entries(saved).filter(([key])=>key!=='release'))});}});await generate(ui);const pending=listGenerations(env.storage,scope)[0];assert.equal(pending.value.status,'pending');ui.unmount();props.workspace={...props.workspace,instanceProfile:{...props.workspace.instanceProfile,goal:'刷新后保留新人工目标'}};const next=env.mount(await component(),props);await next.flush();assert.equal(generationPosts(state).length,1);await next.input('选择业务实例','existing');lost=false;await next.click('核对并恢复原生成请求');assert.equal(generationPosts(state).length,2);assert.equal(generationPosts(state)[1].exact,pending.value.exact_body);assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.equal(props.workspace.instanceProfile.goal,'刷新后保留新人工目标');
}));

test('remount discovers committed receipt automatically without changing current instance selection',()=>environment(async env=>{
 const pending=await stageGeneration(env.storage,scope,body());const saved=await accepted(pending.value.body);const {ui,state}=await fixture(env,{state:{receipts:new Map([[nonce,saved]])}});assert.equal(ui.byAria('选择业务实例').props.value,'');assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.equal(generationPosts(state).length,0);
}));

test('failed readback retains confirmed success and recovery is GET only; failed listing preserves saved success',()=>environment(async env=>{
 const {ui,state}=await fixture(env);state.readbackFailure=true;await generate(ui);assert.equal(listGenerations(env.storage,scope)[0].value.status,'confirmed');assert.match(text(ui.render()),/服务器已返回保存回执/);assert.match(text(ui.render()),/读回已保存版本/);state.readbackFailure=false;state.listingFailure=true;await ui.click('读回已保存版本');assert.equal(generationPosts(state).length,1);assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.match(text(ui.render()),/实例清单暂未刷新/);
}));

test('fresh role loss only reads own original receipt and never retries POST',()=>environment(async env=>{
 await stageGeneration(env.storage,scope,body());const {ui,state}=await fixture(env,{state:{me:{...account,role:'viewer'}}});await ui.click('核对并恢复原生成请求');assert.equal(generationPosts(state).length,0);assert.equal(listGenerations(env.storage,scope)[0].value.status,'pending');assert.match(text(ui.render()),/无生成权限/);state.receipts.set(nonce,await accepted(body()));await ui.click('核对并恢复原生成请求');assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.equal(generationPosts(state).length,0);
}));

test('changed actor and journal quota block POST and protect original human draft',()=>environment(async env=>{
 const {ui,state}=await fixture(env);await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);const write=env.storage.setItem;env.storage.setItem=(key,value)=>{if(key.startsWith('ga-instance-generation:'))throw Error('quota');write(key,value);};await ui.click('保存生成版本');assert.equal(generationPosts(state).length,0);env.storage.setItem=write;state.me={...account,user_id:'other'};await ui.click('保存生成版本');assert.equal(generationPosts(state).length,0);assert.doesNotMatch(text(ui.render()),/保留人工目标/);assert.equal(env.storage.getItem('existing-human-draft'),JSON.stringify(workspace));
}));

test('corrupt or foreign server receipts retain pending and never overwrite with server hash',()=>environment(async env=>{
 for(const patch of [{actor_id:'other'},{request_hash:'b'.repeat(64)},{instance_id:'other'}]){const storage=env.storage;for(let i=storage.length-1;i>=0;i--)if(storage.key(i).startsWith('ga-instance-generation:'))storage.removeItem(storage.key(i));await stageGeneration(storage,scope,body());const bad={...await accepted(body()),...patch};const {ui,state}=await fixture(env,{state:{receipts:new Map([[nonce,bad]])}});await ui.click('核对并恢复原生成请求');assert.equal(generationPosts(state).length,0);assert.equal(listGenerations(storage,scope)[0].value.status,'pending');ui.unmount();}
}));

test('new request while old pending requires explicit retention and does not retag original',()=>environment(async env=>{
 const {ui,state}=await fixture(env,{post:()=>{throw Error('not sent');}});await generate(ui);const original=listGenerations(env.storage,scope)[0];await ui.input('选择业务实例','existing');await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);assert.equal(ui.button('保存生成版本').props.disabled,true);await ui.click('保存生成版本');assert.equal(generationPosts(state).length,1);await ui.input('保留原请求并允许另一次生成',true);await ui.click('保存生成版本');assert.equal(generationPosts(state).length,2);assert.equal(generationPosts(state)[1].body.instance_id,'existing');assert.notEqual(generationPosts(state)[1].body.request_id,original.value.body.request_id);assert.equal(env.storage.getItem(generationKey(scope,original.value.body.request_id)),original.raw);
}));

test('late receipt after A to B to A selection changes updates journal without stealing current target',()=>environment(async env=>{
 const pending=await stageGeneration(env.storage,scope,body()),saved=await accepted(pending.value.body);let slow=false,finish;const wait=new Promise(resolve=>finish=resolve);const {ui,state}=await fixture(env,{receipt:async(s,id)=>{if(slow)await wait;return response(s.receipts.get(id)||{saved:false,request_id:id});}});state.receipts.set(nonce,saved);slow=true;const recovering=ui.click('核对并恢复原生成请求');await new Promise(resolve=>setImmediate(resolve));await ui.input('选择业务实例','existing');await ui.input('选择业务实例','');finish();await recovering;assert.equal(ui.byAria('选择业务实例').props.value,'');assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.match(text(ui.render()),/当前实例选择与草稿保留/);assert.equal(generationPosts(state).length,0);
}));

test('pending journals are private to actor and workspace when component account changes',()=>environment(async env=>{
 await stageGeneration(env.storage,scope,body());const {ui,state,props}=await fixture(env);props.account={...account,user_id:'other'};state.me=props.account;ui.render();await ui.flush();assert.doesNotMatch(text(ui.render()),/生成记录与恢复/);assert.equal(listGenerations(env.storage,scope).length,1);assert.deepEqual(listGenerations(env.storage,generationScope(props.account)),[]);
}));

test('server revision changing after preview blocks new request before journal and POST',()=>environment(async env=>{
 const {ui,state}=await fixture(env);await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);state.revision=6;await ui.click('保存生成版本');assert.equal(generationPosts(state).length,0);assert.equal(listGenerations(env.storage,scope).length,0);assert.match(text(ui.render()),/版本已变化/);
}));

test('R1 receipt remains recoverable outside the latest hundred releases without changing R101 activation choices',()=>environment(async env=>{
 await stageGeneration(env.storage,scope,body({instance_id:'existing'}));const old=await accepted(body({instance_id:'existing'})),latest={...old.release,id:'release-r101',release_number:101,snapshot:{businesses:[{id:'current-business',name:'当前业务',available:true}],relation_definitions:[],sensor_sources:[]},available_business_ids:['current-business']};
 const {ui,state}=await fixture(env,{props:{initialInstanceId:'existing'},releases:()=>response([latest])});await ui.input('启用业务 当前业务',true);assert.equal(ui.byAria('启用业务 当前业务').props.checked,true);state.receipts.set(nonce,old);await ui.click('核对并恢复原生成请求');assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.equal(ui.byAria('选择业务实例').props.value,'existing');assert.equal(ui.byAria('启用业务 当前业务').props.checked,true);assert.match(text(ui.render()),/核对历史第 1 版/);assert.match(text(ui.render()),/核对保存的第 1 版摘要/);assert.equal(generationPosts(state).length,0);
}));

test('two rapid invocations of the same generate event stage and send only one explicit request',()=>environment(async env=>{
 const {ui,state}=await fixture(env);await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);const event=ui.button('保存生成版本').props.onClick;await Promise.all([event(),event()]);await ui.flush();assert.equal(generationPosts(state).length,1);assert.equal(listGenerations(env.storage,scope).length,1);
}));

test('corrupt saved journal is retained with a recoverable error instead of crashing real component rendering',()=>environment(async env=>{
 const original=await stageGeneration(env.storage,scope,body()),saved=await accepted(original.value.body),record={...original.value,status:'saved',receipt:Object.fromEntries(Object.entries(saved).filter(([key])=>key!=='release')),release:saved.release},key=generationKey(scope,nonce);
 for(const patch of [{release:{...record.release,snapshot:{businesses:[null]}}},{release:{...record.release,snapshot:{businesses:[{id:'b',name:{bad:'value'},available:true}]},available_business_ids:['b']}},{receipt:{...record.receipt,actor_id:'other'}},{release:{...record.release,instance_id:'other'}}]){const raw=JSON.stringify({...record,...patch});env.storage.setItem(key,raw);const {ui,state}=await fixture(env);assert.doesNotThrow(()=>ui.render());assert.match(text(ui.render()),/原生成请求或回执无法核对/);assert.doesNotMatch(text(ui.render()),/核对保存的第/);await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);assert.equal(ui.button('保存生成版本').props.disabled,true);await ui.click('保存生成版本');assert.equal(generationPosts(state).length,0);assert.equal(env.storage.getItem(key),raw);ui.unmount();}
}));


test('configuration check binds the selected actual instance and selection clears prior check',()=>environment(async env=>{
 const {ui,state}=await fixture(env);await ui.click('检查当前配置');assert.equal(Object.hasOwn(state.posts.at(-1).body,'instance_id'),false);
 await ui.input('选择业务实例','existing');await ui.flush();assert.throws(()=>ui.byAria('确认当前业务配置生成版本'),/control:/);
 await ui.click('检查当前配置');assert.equal(state.posts.at(-1).body.instance_id,'existing');
 await ui.input('选择业务实例','');await ui.flush();assert.throws(()=>ui.byAria('确认当前业务配置生成版本'),/control:/);
 assert.equal(generationPosts(state).length,0);assert.ok(env.storage.getItem('existing-human-draft'));
}));

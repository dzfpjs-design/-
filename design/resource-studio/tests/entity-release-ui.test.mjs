import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';

const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild');
const account={workspace_id:'workspace-a',user_id:'user-a',username:'负责人',role:'operator'},releaseID='release-new';
const entity=(id='original-entity',changes={})=>({id,version:7,package_id:'package-original',entity_type:'object-measurement',attributes:{field_name:'原对象',field_value:20},object_compatibility:{usable:true,status:'compatible',reason:'',target_release_id:releaseID,source_release_number:1,source_label:'原对象',source_type:'测量对象'},...changes});
const memory=()=>{const rows=new Map();return {get length(){return rows.size;},key:index=>[...rows.keys()][index]??null,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,String(value)),removeItem:key=>rows.delete(key)};};
const finish=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const compiled=new Map();
async function component(name){
 if(!compiled.has(name)){
  const result=await build({entryPoints:[fileURLToPath(new URL('../src/'+name+'.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',logLevel:'silent',loader:{'.css':'empty'},plugins:[{name:'reuse-react',setup(bundler){bundler.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});
  compiled.set(name,import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64')));
 }
 return (await compiled.get(name)).default;
}
function nodes(value,out=[]){if(Array.isArray(value))value.forEach(item=>nodes(item,out));else if(value&&typeof value==='object'&&value.props){out.push(value);nodes(value.props.children,out);}return out;}
async function environment(run){
 const keys=['fetch','localStorage','sessionStorage','window','location','history','setInterval','clearInterval'],previous=Object.fromEntries(keys.map(key=>[key,globalThis[key]])),navigatorDescriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator'),storage=memory(),session=memory(),timers=[],components=[],tails=new Map();
 for(const [key,value] of Object.entries({'ga-setup-csrf':'synthetic-entity-release-csrf','ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(key,value);
 const locks={request(key,_options,fn){const result=(tails.get(key)||Promise.resolve()).then(fn);tails.set(key,result.catch(()=>{}));return result;}};
 Object.defineProperty(globalThis,'navigator',{configurable:true,writable:true,value:{locks}});
 Object.assign(globalThis,{localStorage:storage,sessionStorage:session,window:{addEventListener(){},removeEventListener(){}},location:{search:''},history:{replaceState(){}},setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval(){}});
 try{await run({storage,timers,mount:(Component,props)=>{const value=mount(Component,props);components.push(value);return value;}});}
 finally{components.forEach(value=>value.unmount());Object.assign(globalThis,previous);if(navigatorDescriptor)Object.defineProperty(globalThis,'navigator',navigatorDescriptor);else delete globalThis.navigator;}
}

test('actual choices display original source/version, disable incompatible rows, and retain missing selections',async()=>{
 const Choices=await component('TaskObjectChoices'),original=entity(),blocked=entity('blocked',{object_compatibility:{...original.object_compatibility,usable:false,status:'incompatible',reason:'字段单位已变化',source_label:'旧字段对象'}});
 const html=renderToStaticMarkup(React.createElement(Choices,{objects:[original,blocked],selected:[original.id,'not-loaded'],releaseID,onChange(){}}));
 for(const text of ['原对象','可沿用','来源第 1 版','对象第 7 版','字段单位已变化','原选择保留'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/package-original|original-entity|object-measurement|<pre/);
 const ui=mount(Choices,{objects:[original,blocked],selected:[],releaseID,onChange(){throw Error('cannot select an incompatible object');}});
 assert.equal(ui.byAria('任务对象 旧字段对象').props.disabled,true);
 await ui.input('任务对象 旧字段对象',true);ui.unmount();
});

test('an invalid selected object can be explicitly unchecked without clearing the rest of the draft',async()=>{
 const Choices=await component('TaskObjectChoices'),original=entity(),blocked=entity('blocked',{object_compatibility:{...original.object_compatibility,usable:false,status:'incompatible',reason:'源版本不可读取',source_label:'待核对象'}}),draft={title:'保留人稿',selected:[blocked.id,'not-loaded',original.id]},before=structuredClone(draft);
 const props={objects:[original,blocked],selected:draft.selected,releaseID,onChange:value=>{draft.selected=value;props.selected=value;}},ui=mount(Choices,props);
 assert.equal(ui.byAria('任务对象 待核对象').props.checked,true);assert.equal(ui.byAria('任务对象 待核对象').props.disabled,false);
 await ui.input('任务对象 待核对象',false);assert.deepEqual(draft.selected,['not-loaded',original.id]);assert.equal(draft.title,before.title);
 await ui.input('取消未载入的已选对象 1',false);assert.deepEqual(draft.selected,[original.id]);ui.unmount();
});

async function workspaceFixture(env,{object=entity(),pending=null,selection=[object.id]}={}){
 const Workspace=await component('BusinessWorkspace'),step={id:'review',agent:'人工确认',role:'负责人',name:'核对',purpose:'核对原资料',output:'结论',condition:''},business={id:'business-a',name:'核验',available:true,flow:{steps:[step]}};
 const release={id:releaseID,package_id:'package-new',configuration_hash:'configuration-new',snapshot:{object_types:[{key:'object-measurement',name:'测量对象',fields:[{key:'field_name',label:'名称',kind:'文本',required:true},{key:'field_value',label:'数值',kind:'数值',required:false}]}],businesses:[business]}},posts=[],lookups=[];
 const key='ga-workflow-forms:'+account.workspace_id+':'+account.username+':instance-a';
 env.storage.setItem(key,JSON.stringify({title:'保留任务人稿',business:business.id,showTask:true,selectedObjects:selection,goalForm:{enabled:false,fields:[],purpose:''},creationAction:pending}));
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/me'){assert.ok(!options.method||options.method==='GET');return finish(account);}
  if(options.method==='POST'&&path==='/api/workspace/instances/instance-a/task-requests/lookup'){
   const body=JSON.parse(options.body);assert.ok(pending,'only an existing original request is looked up in this fixture');assert.deepEqual(body,pending);lookups.push(body);
   return finish({schema:'workflow-task-request-recovery-v1',found:false,request:null});
  }
  if(options.method==='POST'){
   assert.equal(path,'/api/workspace/instances/instance-a/tasks','task creation and read-only lookup are distinct routes');
   assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-entity-release-csrf');
   posts.push(JSON.parse(options.body));throw Error('response lost; keep original request');
  }
  if(path==='/api/instances')return finish([{id:'instance-a',name:'测量实例',status:'partially_active',active_release_id:release.id,enabled_business_ids:[business.id]}]);
  if(path.endsWith('/releases'))return finish([release]);
  if(path==='/api/instances/instance-a/objects')return finish([object]);
  if(path.includes('goal-tool-options'))return finish({detail:'工具清单未提供'},409);
  return finish([]);
 };
 const ui=env.mount(Workspace,{instanceId:'instance-a',account,Btn:'button',Field:'label'});await ui.flush();return {ui,posts,lookups,key,object};
}

test('real workspace submits original compatible IDs under new release and preserves a lost-response request',async()=>environment(async env=>{
 const {ui,posts,key,object}=await workspaceFixture(env),before=structuredClone(object);
 assert.equal(ui.button('发起业务任务').props.disabled,false);assert.equal(ui.button('保存并打开任务').props.disabled,false);
 const choices=nodes(ui.render()).find(node=>node.type?.name==='TaskObjectChoices');assert.ok(choices);assert.equal(choices.props.objects[0].package_id,'package-original');
 await ui.click('保存并打开任务');assert.equal(posts.length,1);assert.deepEqual(posts[0].entity_ids,[object.id]);assert.equal(posts[0].release_id,releaseID);assert.deepEqual(object,before);
 const saved=JSON.parse(env.storage.getItem(key));assert.equal(saved.title,'保留任务人稿');assert.deepEqual(saved.selectedObjects,[object.id]);assert.deepEqual(saved.creationAction,posts[0]);
}));

test('real workspace blocks stale cached selections even if a disabled handler is invoked, keeping the human draft',async()=>environment(async env=>{
 const bad=entity();bad.object_compatibility={...bad.object_compatibility,usable:false,status:'incompatible',reason:'字段单位发生变化'};
 const {ui,posts,key}=await workspaceFixture(env,{object:bad});assert.equal(ui.button('保存并打开任务').props.disabled,true);
 await ui.click('保存并打开任务');assert.equal(posts.length,0);const saved=JSON.parse(env.storage.getItem(key));assert.equal(saved.title,'保留任务人稿');assert.deepEqual(saved.selectedObjects,[bad.id]);assert.equal(saved.creationAction,null);
}));

test('original pending task requests remain retryable with the exact old body after eligibility changes',async()=>environment(async env=>{
 const original=entity(),bad=entity(original.id,{object_compatibility:{...original.object_compatibility,usable:false,status:'incompatible',reason:'版本已变化'}}),pending={release_id:'release-before',business_id:'business-a',entity_ids:[original.id],image_ids:[],scene_ids:[],title:'原请求人稿',request_id:'original-task-request'};
 const {ui,posts,lookups,key}=await workspaceFixture(env,{object:bad,pending});assert.equal(ui.button('核对原发起请求').props.disabled,false);
 const originalForm=env.storage.getItem(key);assert.equal(env.storage.getItem(key+':task-create-pending'),null,'this is the real pre-outbox form-only legacy contract');
 await ui.click('核对原发起请求');assert.deepEqual(lookups,[pending]);assert.deepEqual(posts,[],'a negative original lookup does not itself create a Task');
 const migrated=env.storage.getItem(key+':task-create-pending');assert.ok(migrated);assert.equal(JSON.parse(migrated).schema,'workflow-task-creation-outbox-v1');assert.deepEqual(JSON.parse(migrated).payload,pending);assert.equal(env.storage.getItem(key+':task-create-outbox:'+pending.request_id),migrated);
 const backups=Array.from({length:env.storage.length},(_,index)=>env.storage.key(index)).filter(name=>name.startsWith(key+':task-create-legacy-form:'+pending.request_id+':'));assert.equal(backups.length,1);assert.equal(env.storage.getItem(backups[0]),originalForm,'the actual complete legacy form was backed up before migration');
 assert.equal(ui.button('按原内容重试发起').props.disabled,false);await ui.click('按原内容重试发起');
 assert.deepEqual(lookups,[pending,pending]);assert.deepEqual(posts,[pending]);assert.deepEqual(JSON.parse(env.storage.getItem(key)).creationAction,pending);
 assert.equal(env.storage.getItem(key+':task-create-pending'),migrated);assert.equal(env.storage.getItem(backups[0]),originalForm);assert.equal(Array.from({length:env.storage.length},(_,index)=>env.storage.key(index)).filter(name=>name.startsWith(key+':task-create-legacy-form:'+pending.request_id+':')).length,1);
}));

test('malformed cached selections keep the original browser record and cannot invoke a new task',async()=>environment(async env=>{
 const selection={unreadable:['original-entity']},{ui,posts,key}=await workspaceFixture(env,{selection}),before=env.storage.getItem(key);
 assert.equal(ui.button('保存并打开任务').props.disabled,true);await ui.click('保存并打开任务');assert.equal(posts.length,0);
 assert.equal(env.storage.getItem(key),before);assert.deepEqual(JSON.parse(before).selectedObjects,selection);
}));

test('source definition failure still lets an authorized person save a draft while workflow advancement stays blocked',async()=>environment(async env=>{
 const Workspace=await component('BusinessWorkspace'),step={id:'human',agent:'人工确认',role:'负责人',name:'人工核对',purpose:'核对原资料',output:'说明'},business={id:'business-a',name:'核验',available:true,flow:{steps:[step]}},release={id:releaseID,package_id:'package-new',snapshot:{object_types:[],businesses:[business]}};
 let task={id:'saved-task',version:4,status:'workflow_waiting',title:'已保存任务',business_name:'核验',instance_release_number:2,business,roles:{负责人:{username:account.username}},object_snapshots:[],workflow_runtime:{current_step_id:step.id,drafts:{},receipts:[]},native_execution_blocked:true,permissions:{current_actor_role:'operator',can_save_note:true,can_complete_step:false,can_run_ability:false,can_retry_ability:false,can_cancel:true,reason:'原对象来源已停用；当前处理说明仍可保存，流程推进与能力执行暂不可用'}};
 const puts=[];globalThis.location.search='?task='+task.id;
 globalThis.fetch=async(path,options={})=>{
  if(options.method==='PUT'){assert.ok(path.endsWith('/note'));const body=JSON.parse(options.body);puts.push(body);task={...task,version:task.version+1,workflow_runtime:{...task.workflow_runtime,drafts:{[step.id]:body}}};return finish(task);}
  assert.notEqual(options.method,'POST','saving the draft must not dispatch or advance a flow');
  if(path==='/api/instances')return finish([{id:'instance-a',status:'partially_active',active_release_id:release.id,enabled_business_ids:[business.id]}]);
  if(path.endsWith('/releases'))return finish([release]);
  if(path==='/api/workspace/instances/instance-a/tasks')return finish([task]);
  if(path==='/api/workspace/tasks/'+task.id)return finish(task);
  if(path.includes('goal-tool-options'))return finish({detail:'工具暂不可用'},409);
  return finish([]);
 };
 let ui=env.mount(Workspace,{instanceId:'instance-a',account,Btn:'button',Field:'label'});await ui.flush();
 await ui.input('业务步骤处理结果','来源待恢复；先保存已完成的人工核对说明');assert.equal(ui.button('保存处理说明').props.disabled,false);assert.equal(ui.button('保存结果并继续流程').props.disabled,true);
 await ui.click('保存处理说明');assert.equal(puts.length,1);assert.equal(task.workflow_runtime.drafts[step.id].note,'来源待恢复；先保存已完成的人工核对说明');assert.deepEqual(task.workflow_runtime.receipts,[]);
 ui.unmount();ui=env.mount(Workspace,{instanceId:'instance-a',account,Btn:'button',Field:'label'});await ui.flush();assert.equal(ui.byAria('业务步骤处理结果').props.value,task.workflow_runtime.drafts[step.id].note);assert.equal(ui.button('保存结果并继续流程').props.disabled,true);
 const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):typeof value==='string'?value:'';
 assert.match(text(ui.render()),/保存处理说明不会推进流程/);
}));

import test from 'node:test';
import assert from 'node:assert/strict';
import {checkedTaskInputMetadata,createTaskInputDraft,editTaskInput,buildTaskGraphInputs,persistTaskCreation,lookupTaskCreation,readTaskForm,readTaskCreation,saveTaskForm,completeTaskCreation,preserveTaskInputDraft,readTaskInputHistory,retireTaskCreation,preserveTaskFormJournal,readTaskFormJournals} from '../src/graph-task-inputs.mjs';
const scope={actor_id:'actor',workspace_id:'workspace',instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:'a'.repeat(64)};
const schema={type:'object',properties:{watched:{type:'string',title:'等待结果对应对象',format:'uuid'},count:{type:'integer',title:'检查数量',minimum:0}},required:['watched','count'],additionalProperties:false};
const metadata=()=>({schema:'workflow-graph-task-inputs-v1',instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:scope.configuration_hash,business_name:'合成业务',mode:'graph',graph_hash:'b'.repeat(64),binding_hash:'c'.repeat(64),hash:'d'.repeat(64),execution_granted:false,input_schema:schema,user_input_schema:schema,system_fields:['entity_ids'],entity_fields:[{field:'watched',label:'等待结果对应对象',required:true,waits:['等待本地分析']} ]});
const change=(draft,key,mode,raw)=>editTaskInput(draft,metadata(),scope,key,{mode,raw});
test('task association never picks the first object and integer zero remains an explicit answer',()=>{
 const meta=metadata();let draft=createTaskInputDraft(meta,scope);assert.equal(draft.values.watched.mode,'unset');
 draft=change(draft,'count','value','0');assert.throws(()=>buildTaskGraphInputs(meta,scope,draft,['first','second']),/请选择/);
 draft=change(draft,'watched','value','second');assert.deepEqual(buildTaskGraphInputs(meta,scope,draft,['first','second']),{count:0,watched:'second'});
 const restored=JSON.parse(JSON.stringify(draft));assert.deepEqual(buildTaskGraphInputs(meta,scope,restored,['second']),{count:0,watched:'second'});
 assert.throws(()=>buildTaskGraphInputs(meta,scope,restored,['first']),/本次已选对象/);assert.equal(restored.values.watched.raw,'second');
});
test('actor, workspace, release, business and descriptor drift cannot silently migrate a saved input',()=>{
 const draft=createTaskInputDraft(metadata(),scope);
 for(const key of ['actor_id','workspace_id','release_id','business_id','configuration_hash'])assert.throws(()=>buildTaskGraphInputs(metadata(),{...scope,[key]:'changed'},draft,[]));
 const changed=metadata();changed.hash='e'.repeat(64);assert.throws(()=>buildTaskGraphInputs(changed,scope,draft,[]));
 assert.equal(draft.scope.actor_id,'actor');
});
test('injected source fields and unselected or forbidden entity IDs are refused',()=>{
 const meta=metadata();meta.entity_fields[0].field='entity_ids';assert.throws(()=>checkedTaskInputMetadata(meta,scope));
 const restricted=metadata();restricted.user_input_schema=structuredClone(schema);restricted.user_input_schema.properties.watched.enum=['allowed'];
 let draft=createTaskInputDraft(restricted,scope);draft.values.count={mode:'value',raw:'1'};draft.values.watched={mode:'value',raw:'other'};
 assert.throws(()=>buildTaskGraphInputs(restricted,scope,draft,['other']),/声明的范围/);
});
test('all automatic graph inputs need no invented manual value, unsupported structure is explicitly blocked',()=>{
 const meta=metadata();meta.entity_fields=[];meta.user_input_schema={type:'object',properties:{},required:[],additionalProperties:false};
 assert.deepEqual(buildTaskGraphInputs(meta,scope,null,['object']),{});
 meta.user_input_schema.properties.extra={type:'object',properties:{nested:{type:'string'}}};assert.throws(()=>createTaskInputDraft(meta,scope));
});
test('complete original creation body including graph_inputs is persisted and read back before transport',()=>{
 const store=new Map(),storage={setItem:(key,value)=>store.set(key,value),getItem:key=>store.get(key)??null};
 const payload={release_id:'release',business_id:'business',entity_ids:['second'],graph_inputs:{watched:'second',count:0},request_id:'original',title:'合成核对任务'};
 assert.equal(persistTaskCreation(storage,'form',{graphInputDraft:{values:{note:'保留草稿'}}},payload),JSON.stringify(payload));
 assert.deepEqual(JSON.parse(storage.getItem('form')).creationAction,payload);
 assert.throws(()=>persistTaskCreation({...storage,getItem:()=>null},'form',{},payload),/尚未可靠保存/);
});
test('read-only creation lookup validates original account and request without creating a new task',async()=>{
 const payload={request_id:'original',release_id:'release',business_id:'business'},calls=[];
 const request={request_id:'original',actor_id:'actor',instance_id:'instance',release_id:'release',business_id:'business',task_id:'saved-task',creation_hash:'f'.repeat(64)};
 const api=async(path,options)=>{calls.push({path,...options});return {schema:'workflow-task-request-recovery-v1',found:true,request};};
 const found=await lookupTaskCreation(api,scope,payload);assert.equal(found.request.task_id,'saved-task');assert.ok(calls[0].path.endsWith('/task-requests/lookup'));assert.deepEqual(JSON.parse(calls[0].body),payload);
 await assert.rejects(()=>lookupTaskCreation(async()=>({schema:'workflow-task-request-recovery-v1',found:true,request:{...request,actor_id:'another'}}),scope,payload),/账号/);
 assert.deepEqual(await lookupTaskCreation(async()=>({schema:'workflow-task-request-recovery-v1',found:false,request:null}),scope,payload),{schema:'workflow-task-request-recovery-v1',found:false,request:null});
});

const storageFixture=()=>{const map=new Map();return {get length(){return map.size;},key:i=>[...map.keys()][i],getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,String(value)),removeItem:key=>map.delete(key)};};
test('immutable original creation survives stale second-window form writes and reload',()=>{
 const store=storageFixture(),payload={request_id:'original-request',release_id:'release',business_id:'business',entity_ids:['second'],graph_inputs:{watched:'second',count:0}};
 const form={title:'原任务',business:'business',selectedObjects:['second'],graphInputDraft:createTaskInputDraft(metadata(),scope)};
 saveTaskForm(store,'shared',{...form,creationAction:null});persistTaskCreation(store,'shared',form,payload);
 assert.throws(()=>saveTaskForm(store,'shared',{...form,title:'另一窗口的新输入',creationAction:null}),/另一窗口/);
 assert.deepEqual(readTaskForm(store,'shared').creationAction,payload);assert.equal(readTaskForm(store,'shared').title,'原任务');
 store.setItem('shared',JSON.stringify({...form,creationAction:null,title:'人为破坏旧缓存'}));
 assert.deepEqual(readTaskForm(store,'shared').creationAction,payload);assert.equal(readTaskForm(store,'shared').title,'原任务');
 completeTaskCreation(store,'shared',{...form,creationAction:null},payload);assert.equal(readTaskCreation(store,'shared'),null);
 assert.ok(store.getItem('shared:task-create-outbox:original-request'));assert.equal(readTaskForm(store,'shared').creationAction,null);
});
test('every input revision remains recoverable in its original actor and business scope',()=>{
 const store=storageFixture(),original=createTaskInputDraft(metadata(),scope),form={business:'business',title:'第一份',selectedObjects:['second'],graphInputDraft:original};
 preserveTaskInputDraft(store,'form',form);original.values.count={mode:'value',raw:'2'};preserveTaskInputDraft(store,'form',{...form,title:'第二份'});
 const rows=readTaskInputHistory(store,'form',scope);assert.equal(rows.length,2);assert.ok(rows.some(row=>row.title==='第一份'&&row.draft.values.count.mode==='unset'));
 assert.deepEqual(readTaskInputHistory(store,'form',{...scope,actor_id:'other'}),[]);
 assert.equal(rows.find(row=>row.title==='第二份').draft.values.count.raw,'2');
});
test('retiring an unknown original verifies the durable server record before allowing a new draft',async()=>{
 const payload={request_id:'original',release_id:'release',business_id:'business',title:'合成任务'},reason='保留原输入后修改',calls=[];
 const retirement={schema:'workflow-task-request-retirement-v1',actor_id:scope.actor_id,instance_id:scope.instance_id,request_id:payload.request_id,original:payload,reason,creation_hash:'a'.repeat(64),command_hash:'b'.repeat(64)};
 const api=async(path,options)=>{calls.push({path,...options});return path.endsWith('/retire')?retirement:{schema:'workflow-task-request-recovery-v1',found:false,request:null,retirement};};
 assert.deepEqual(await retireTaskCreation(api,scope,payload,reason),retirement);assert.equal(calls.length,2);assert.ok(calls[1].path.endsWith('/lookup'));
 await assert.rejects(()=>retireTaskCreation(async(path)=>path.endsWith('/retire')?retirement:{schema:'workflow-task-request-recovery-v1',found:false,request:null},scope,payload,reason),/尚未独立读回/);
 await assert.rejects(()=>lookupTaskCreation(async()=>({schema:'workflow-task-request-recovery-v1',found:false,request:null,retirement:{...retirement,original:{...payload,title:'changed'}}}),scope,payload),/不一致/);
});

test('a late second-tab completion cannot replace a new draft after the original was settled',()=>{
 const store=storageFixture(),payload={request_id:'original-request',title:'原任务'},old={title:'原任务',business:'business',selectedObjects:[],creationAction:payload};
 persistTaskCreation(store,'form',old,payload);completeTaskCreation(store,'form',{title:'',creationAction:null},payload);
 const fresh={title:'新稿逐字保留',business:'business',selectedObjects:['other'],creationAction:null};saveTaskForm(store,'form',fresh);
 assert.throws(()=>completeTaskCreation(store,'form',{title:'旧窗口清空',creationAction:null},payload),/未覆盖当前草稿/);
 assert.throws(()=>saveTaskForm(store,'form',old),/已结清/);assert.deepEqual(readTaskForm(store,'form'),fresh);
});

test('per-window journals preserve independent scopes without making a snapshot on every keypress',()=>{
 const store=storageFixture(),form={business:'business',title:'正在填写',selectedObjects:['second'],graphInputDraft:createTaskInputDraft(metadata(),scope)};
 for(let i=0;i<100;i++){form.graphInputDraft.values.count={mode:'value',raw:String(i)};preserveTaskInputDraft(store,'form',form,'window-a');}
 assert.equal(readTaskInputHistory(store,'form',scope).length,1);assert.equal(readTaskInputHistory(store,'form',scope)[0].draft.values.count.raw,'99');
 form.graphInputDraft.values.count.raw='另一窗口未送达';preserveTaskInputDraft(store,'form',form,'window-b');assert.equal(readTaskInputHistory(store,'form',scope).length,2);
 assert.ok(readTaskInputHistory(store,'form',scope).some(row=>row.draft.values.count.raw==='99'));
});

test('a stale tab title without graph inputs has its own durable and scoped full-form journal',()=>{
 const store=storageFixture(),form={title:'另一窗口逐字保留的标题',business:'business',selectedObjects:['second'],graphInputDraft:null,creationAction:null};
 preserveTaskFormJournal(store,'form',form,scope,'window-b');saveTaskForm(store,'form',{...form,title:'共享旧标题'});
 assert.equal(readTaskFormJournals(store,'form',scope)[0].form.title,form.title);
 assert.deepEqual(readTaskFormJournals(store,'form',{...scope,actor_id:'other'}),[]);
 for(let i=0;i<100;i++)preserveTaskFormJournal(store,'form',{...form,title:String(i)},scope,'window-b');
 assert.equal(readTaskFormJournals(store,'form',scope).length,1);assert.equal(readTaskFormJournals(store,'form',scope)[0].form.title,'99');
});

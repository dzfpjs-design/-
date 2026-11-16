import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {mount,installEventBrowser} from '../scripts/react-event-harness.mjs';
import {NativeGoalJournal,nativeGoalScope} from '../src/native-goal-request-v132.mjs';
import {proposalJournalKey,preserveQuerySeed,proposalSeedKey} from '../src/native-query-proposal-contract.mjs';
import {queryMetadata} from './native-query-fixtures.mjs';
import {defaultNativeGoal} from '../src/native-goal-contract.mjs';

const account={workspace_id:'offline-w',user_id:'offline-u',username:'operator',role:'operator'};
const finish=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
const evidence={source:'archive',minimum_verification:'recorded',minimum_records_per_entity:1,time:{mode:'archive',timezone:'Asia/Shanghai'}};
const catalog={objects:[{id:'object-a',label:'场景甲'}],fields:[{key:'title',label:'标题',type:'string',unit:null}],relation_types:[],query_tools:[]};
const initial=(mode='goal_draft')=>({schema:'native-query-proposal-v1',task_id:'task-a',task_version:2,mode,draft:null,current:null,history:[],models:[{id:'qwen',name:'已配置通义',provider:'qwen',model:'qwen-plus'}],can_manage:true,can_create:true,scope_error:null,catalog,adopted_goal_draft:null,reference_query:null});
async function environment(run){
 const previous={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage,window:globalThis.window},cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};
 Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,window:{addEventListener(){},removeEventListener(){}}});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 let component;try{const {default:Workspace}=await server.ssrLoadModule('/src/NativeQueryProposals.jsx');await run({Workspace,storage,cache,mount:(props={})=>(component=mount(Workspace,{task:{id:'task-a'},account,Btn:'button',goal:{goal_confirmed:false},...props}))});}finally{component?.unmount();await server.close();Object.assign(globalThis,previous);}
}

test('real React event save uses fixed UUID, preserves typing after lost response and restores shared server draft after reload',async()=>environment(async({mount,storage,cache})=>{
 let view=initial(),lost=true,guard,saveCalls=0;const commands=new Map();
 globalThis.fetch=async(path,options={})=>{const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
  if(path.includes('/supporting-queries'))return finish({task_id:'task-a',task_version:view.task_version,items:[],permissions:{can_execute:false}});
  if(path.endsWith('/requests/recover')){assert.deepEqual(body.original_request,commands.get(body.original_request.request_id)?.body);return finish({saved:true,abandoned:false,task_version:view.task_version,current:null,draft:view.draft});}
  if(method==='GET')return finish(view);
  if(path.endsWith('/draft')){saveCalls++;assert.equal(body.expected_version,view.task_version);view={...view,task_version:view.task_version+1,draft:{question:body.question,evidence:body.evidence,revision:(view.draft?.revision||0)+1}};commands.set(body.request_id,{body});if(lost){lost=false;throw Error('offline response dropped after durable save');}return finish(view);}
  throw Error('unexpected '+path);
 };
 let component=mount({onRegisterGuard:value=>{guard=value;}});await component.flush();
 await component.input('自然语言查询需求','档案标题含热，均值20');await component.input('已核对查询资料与时间范围',true);await component.click('随任务保存查询需求');
 const journal=JSON.parse(storage.getItem(proposalJournalKey(account,'task-a')));assert.ok(journal.pending.body.request_id);assert.equal(saveCalls,1);
 await component.input('自然语言查询需求','稍后补充的人工说明仍需保留');await component.click('核对原查询请求是否已保存');assert.equal(saveCalls,1);assert.equal(component.byAria('自然语言查询需求').props.value,'稍后补充的人工说明仍需保留');
 await guard();await component.flush();assert.equal(view.draft.question,'稍后补充的人工说明仍需保留');assert.equal(saveCalls,2);assert.equal(JSON.parse(storage.getItem(proposalJournalKey(account,'task-a'))).pending,null);
 component.unmount();cache.clear();component=mount();await component.flush();assert.equal(component.byAria('自然语言查询需求').props.value,view.draft.question);
}));

test('confirmed-goal path requires cloud consent, meaning and ranked-subset confirmation, separate adoption and separate read-only execution',async()=>environment(async({mount})=>{
 let view=initial('reference_only'),support={task_id:'task-a',task_version:2,items:[],permissions:{can_execute:true,execute_reason:null}},cloud=0,executed=0;const calls=[];
 const proposal=()=>({id:'proposal-a',version:1,status:'needs_review',created_at:'2026-10-14T00:00:00Z',candidate:{status:'candidate',summary:'核对返回子集',fields:['title'],relation_query:null,tool_steps:[],result_criterion:null,claims:[],questions:[],limitations:[]},constraints:{requires_ranked_subset_confirmation:true,evidence_requirement:evidence,limitations:[]},review_hash:'a'.repeat(64),catalog});
 globalThis.fetch=async(path,options={})=>{const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;calls.push({path,method,body});
  if(path.includes('/supporting-queries')){if(method==='POST'){executed++;assert.equal(body.proposal_id,'proposal-a');assert.equal(body.expected_version,view.task_version);support={...support,task_version:view.task_version,items:[{id:'run-a',proposal_id:'proposal-a',status:'succeeded',reference_only:true,current_valid:true,result_summary:{fields:[],rows:[],record_count:0,limitations:[]}}]};}return finish(support);}
  if(method==='GET')return finish(view);
  if(path.endsWith('/draft')){view={...view,task_version:view.task_version+1,draft:{question:body.question,evidence:body.evidence,revision:1}};return finish(view);}
  if(path.endsWith('/preview'))return finish({preview_hash:'b'.repeat(64),task_id:'task-a',task_version:view.task_version,draft_revision:view.draft.revision,input_bytes:99,messages:[{role:'user',content:'已确认字段与原说明；无档案值'}],schema:{type:'object'},options:{max_tokens:100},model:{id:'qwen'}});
  if(path.endsWith('/review')){assert.equal(body.meaning_confirmed,true);assert.equal(body.ranked_subset_confirmed,true);view={...view,task_version:view.task_version+1,current:{...view.current,status:'confirmed',version:2}};return finish(view);}
  if(path.endsWith('/adopt')){assert.equal(executed,0);view={...view,task_version:view.task_version+1,current:{...view.current,status:'adopted',version:3},reference_query:{proposal_id:'proposal-a'}};return finish(view);}
  cloud++;assert.equal(body.cloud_authorized,true);assert.equal(body.model_profile_id,'qwen');view={...view,task_version:view.task_version+1,current:proposal(),can_create:false};return finish(view);
 };
 const component=mount({goal:{goal_confirmed:true,goal:{purpose:'原目标不得改写'}}});await component.flush();
 await component.input('自然语言查询需求','查询本任务的已有档案');await component.input('已核对查询资料与时间范围',true);await component.click('随任务保存查询需求');await component.input('查询建议操作说明','核对已有范围，原目标保留');await component.input('查询建议云模型','qwen');await component.click('核对生成建议的全部发送范围');
 assert.equal(cloud,0);assert.equal(component.button('批准这次发送并生成建议').props.disabled,true);await component.input('批准本次查询建议云发送',true);await component.click('批准这次发送并生成建议');assert.equal(cloud,1);
 await component.input('确认查询建议含义 proposal-a',true);assert.equal(component.button('确认此建议含义').props.disabled,true);await component.input('确认仅计算检索返回子集 proposal-a',true);await component.click('确认此建议含义');assert.equal(executed,0);
 await component.click('采用为本任务只读补充查询');assert.equal(executed,0);await component.click('执行本任务只读补充查询');assert.equal(executed,1);
 assert.equal(calls.some(call=>/\/goal(?:\/|$)/.test(call.path)),false);assert.equal(calls.some(call=>call.body?.goal_spec),false);
}));

test('new Task creation mode keeps the previous manual purpose and fields and creates no technical prerequisites',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 let component;try{const {default:Fields}=await server.ssrLoadModule('/src/TaskGoalFields.jsx');const props={form:{enabled:true,purpose:'先前人工目标',fields:['saved-field'],source:'archive'},metadata:{fields:{},field_kinds:{},entity_ids:[]},onChange:next=>{props.form=next;}};
  component=mount(Fields,props);await component.input('先发起任务让智能体理解查询需求',true);assert.equal(props.form.enabled,false);assert.equal(props.form.purpose,'先前人工目标');assert.deepEqual(props.form.fields,['saved-field']);await component.input('发起任务查询需求','先让智能体理解这句话');await component.input('先发起任务让智能体理解查询需求',false);assert.equal(props.form.enabled,true);assert.equal(props.form.purpose,'先前人工目标');assert.equal(props.form.queryQuestion,'先让智能体理解这句话');
 }finally{component?.unmount();await server.close();}
});

test('late A save cannot populate B task or clear A original pending journal',async()=>environment(async({mount,storage})=>{
 const task={id:'task-a'};let resolveSave;const saveResponse=new Promise(resolve=>{resolveSave=resolve;});
 globalThis.fetch=async(path,options={})=>{const isB=path.includes('/tasks/task-b/'),view={...initial(),task_id:isB?'task-b':'task-a'};
  if(path.includes('/supporting-queries'))return finish({task_id:view.task_id,task_version:2,items:[],permissions:{can_execute:false}});
  if(options.method==='PUT')return saveResponse;
  return finish(view);
 };
 const component=mount({task});await component.flush();await component.input('自然语言查询需求','A任务人工输入');const saving=component.click('随任务保存查询需求');await Promise.resolve();
 task.id='task-b';component.render();await component.flush();assert.equal(component.byAria('自然语言查询需求').props.value,'');
 resolveSave(finish({...initial(),task_version:3,draft:{question:'A任务人工输入',evidence:null,revision:1}}));await saving;
 assert.equal(component.byAria('自然语言查询需求').props.value,'');assert.equal(JSON.parse(storage.getItem(proposalJournalKey(account,'task-a'))).pending.body.question,'A任务人工输入');assert.equal(storage.getItem(proposalJournalKey(account,'task-b')),null);
}));

test('support history appends and lost cancellation recovers with its own exact wire and UUID',async()=>environment(async({mount,storage})=>{
 let view=initial('reference_only'),cancelled=false,cancelBody;const run={id:'run-current',proposal_id:'proposal-a',status:'queued',can_cancel:true,current_valid:false,reference_only:true,result_summary:{fields:[],rows:[],record_count:0,limitations:[]}},older={...run,id:'run-older',status:'sufficient',can_cancel:false};
 globalThis.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):null;
  if(path.endsWith('/requests/recover')){assert.equal(body.operation,'cancel');assert.equal(body.run_id,'run-current');assert.deepEqual(body.original_request,cancelBody);assert.deepEqual(Object.keys(body).sort(),['action','operation','original_request','reason','run_id']);return finish({status:'saved',run_id:'run-current',can_resend:false});}
  if(path.endsWith('/run-current/cancel')){cancelBody=body;cancelled=true;view={...view,task_version:3};throw Error('saved cancellation response lost');}
  if(path.includes('/supporting-queries'))return finish({task_id:'task-a',task_version:view.task_version,items:path.includes('cursor=')?[older]:[{...run,...(cancelled?{status:'cancelled',can_cancel:false}:{})}],permissions:{can_execute:false},has_more:!path.includes('cursor='),next_cursor:path.includes('cursor=')?null:'cursor-a'});
  return finish(view);
 };
 const component=mount({goal:{goal_confirmed:true}});await component.flush();await component.input('查询建议操作说明','停止当前查询，保留回执');await component.click('更早的查询记录');
 const find=(node)=>Array.isArray(node)?node.map(find).find(Boolean):node&&typeof node==='object'&&node.props?(node.type?.name==='SupportingQueryResults'?node:find(node.props.children)):null;
 let output=find(component.render());assert.deepEqual(output.props.view.items.map(item=>item.id),['run-current','run-older']);await output.props.onCancel(run);await component.flush();assert.equal(JSON.parse(storage.getItem(proposalJournalKey(account,'task-a'))).pending.operation,'support_cancel');
 await component.click('核对原查询请求是否已保存');assert.equal(JSON.parse(storage.getItem(proposalJournalKey(account,'task-a'))).pending,null);output=find(component.render());assert.equal(output.props.view.items.find(item=>item.id==='run-current').status,'cancelled');assert.ok(output.props.view.items.some(item=>item.id==='run-older'));
}));

test('NativeGoalStudio imports an adopted goal only after snapshot check and preserves the full human draft for recovery',async()=>{
 const previous={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};const cache=new Map(),storage={get length(){return cache.size;},key:index=>[...cache.keys()][index]??null,getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,String(value)),removeItem:key=>cache.delete(key)};Object.assign(globalThis,{localStorage:storage,sessionStorage:storage});const browser=installEventBrowser({account,storage});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let component;
 try{const {default:Studio}=await server.ssrLoadModule('/src/NativeGoalStudio.jsx');const key='ga-native-goal:'+account.workspace_id+':'+account.username+':task-a',oldForm={...defaultNativeGoal('人工原目标'),fields:['name'],reason:'人工原理由'};storage.setItem(key,JSON.stringify({form:oldForm,pending:null}));
  const goal={task_id:'task-a',version:3,goal_confirmed:false,task_status:'workflow_waiting',can_manage:true,...queryMetadata};let writes=0;globalThis.fetch=async(path,options={})=>{assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-event-csrf');if(options.method&&options.method!=='GET')writes++;return finish(path==='/api/me'?account:goal);};
  component=mount(Studio,{task:{id:'task-a',title:'原任务'},account,Btn:'button',onChanged(){}});await component.flush();
  const find=(node)=>Array.isArray(node)?node.map(find).find(Boolean):node&&typeof node==='object'&&node.props?(node.type?.name==='NativeQueryProposals'?node:find(node.props.children)):null;
  let props=find(component.render()).props;const stale=props.goalDraftSnapshot;await component.input('业务目标用途','人工稍后又改了说明');props=find(component.render()).props;
  const adopted={proposal_id:'proposal-a',goal_spec:{purpose:'已确认建议的目的',required_fields:['count'],evidence:{source:'archive',time:{mode:'archive'},minimum_verification:'recorded'},criteria:[{kind:'evidence_sufficient'},{kind:'human_confirmation'}],data_query:null,relation_query:null}};
  assert.throws(()=>props.onGoalDraft(adopted,stale),/已有新输入/);assert.equal(component.byAria('业务目标用途').props.value,'人工稍后又改了说明');
  props.onGoalDraft(adopted,props.goalDraftSnapshot);await component.flush();assert.equal(component.byAria('业务目标用途').props.value,'已确认建议的目的');const backup=JSON.parse(storage.getItem(key+':proposal-human-backups'));assert.equal(backup[0].form.purpose,'人工稍后又改了说明');assert.equal(backup[0].form.reason,'人工原理由');assert.deepEqual(backup[0].form.fields,['name']);assert.equal(writes,0);
  await component.click('恢复人工目标稿 1');assert.equal(component.byAria('业务目标用途').props.value,'人工稍后又改了说明');const journal=new NativeGoalJournal({storage,scope:nativeGoalScope(account,'task-a'),legacyKey:key}),working=JSON.parse(storage.getItem(browser.sessionStorage.getItem(journal.pointerKey)));assert.equal(working.form.reason,'人工原理由');assert.deepEqual(working.form.fields,['name']);assert.equal(journal.pending(),null);assert.deepEqual(JSON.parse(storage.getItem(key)),{form:oldForm,pending:null});assert.equal(writes,0);
 }finally{component?.unmount();await server.close();browser.restore();Object.assign(globalThis,previous);}
});

test('newly created task carries typed purpose into a durable shared query draft without confirming goal or evidence',async()=>environment(async({mount,storage})=>{
 let view=initial(),saves=0;preserveQuerySeed(storage,account,'task-a',{queryQuestion:'发起时输入的自然语言说明',purpose:'原手写目标',source:'archive',hours:24,confirmation:'confirmed'});
 globalThis.fetch=async(path,options={})=>{if(path.includes('/supporting-queries'))return finish({task_id:'task-a',task_version:view.task_version,items:[],permissions:{can_execute:false}});if(options.method==='PUT'){const body=JSON.parse(options.body);saves++;assert.equal(body.question,'发起时输入的自然语言说明');assert.equal(body.evidence,null);view={...view,task_version:3,draft:{question:body.question,evidence:null,revision:1}};return finish(view);}assert.equal(options.method,undefined);return finish(view);};
 const component=mount();await component.flush();assert.equal(saves,1);assert.equal(view.draft.question,'发起时输入的自然语言说明');assert.equal(component.byAria('自然语言查询需求').props.value,view.draft.question);assert.equal(component.byAria('已核对查询资料与时间范围').props.checked,false);assert.equal(storage.getItem(proposalSeedKey(account,'task-a')),null);
}));

// Real mounted workspace, controllers, hooks and production Btn/Field. Only
// transport/storage failure is synthetic. Root runs this; it is not a DB proof.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act,useEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {defaultNativeGoal} from '../src/native-goal-contract.mjs';
import {graphWireRequestJSON,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';
import {webcrypto} from 'node:crypto';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{
  temp=await mkdtemp(join(tmpdir(),'business-workspace-real-react-'));
  const entry=join(temp,'entry.jsx'),app=join(project,'src/App.jsx');
  await writeFile(entry,'export {default as BusinessWorkspace} from '+JSON.stringify(join(project,'src/BusinessWorkspace.jsx'))+';\nexport {__TaskTestBtn as Btn,__TaskTestField as Field} from '+JSON.stringify(app)+';\n');
  const output=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',plugins:[{
   // Expose the actual production functions unchanged, rather than replacing
   // them or any workspace child with a test implementation.
   name:'expose-existing-workspace-primitives',enforce:'pre',transform(code,id){if(id.split('?')[0]===app)return code+'\nexport {Btn as __TaskTestBtn,Field as __TaskTestField};\n';},
  }],build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=output.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'BusinessWorkspace.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();
 return bundle;
}

const copy=v=>JSON.parse(JSON.stringify(v));
const id=n=>'30000000-0000-4000-8000-'+String(n).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),username:'合成任务操作员',role:'operator'},instanceId=id(3),releaseId=id(4),businessId=id(5),packageId=id(6),taskId=id(7),objectA=id(8),objectB=id(9),H='a'.repeat(64);
const formKey='ga-workflow-forms:'+account.workspace_id+':'+account.username+':'+instanceId;
const scope={actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instanceId,release_id:releaseId,business_id:businessId,configuration_hash:H};
function fixture(){
 const field={key:'name',label:'对象名称',kind:'文本',required:true,unit:null},flow={steps:[{id:'human-source',name:'核对业务对象',agent:'人工确认',role:'负责人',purpose:'核对机器候选依据',output:'核对说明',citations:[]}],citations:[]};
 const business={id:businessId,name:'来源绑定业务核对',available:true,execution_mode:'confirmed_graph_workflow',flow};
 const release={id:releaseId,instance_id:instanceId,package_id:packageId,number:4,configuration_hash:H,snapshot:{object_types:[{key:'site',name:'业务对象',fields:[field,{key:'reading',label:'观测数值',kind:'数值',required:false,unit:null},{key:'quantity',label:'登记数量',kind:'整数',required:false,unit:null}]}],businesses:[business],relationships:[]}};
 const instance={id:instanceId,name:'合成业务工作区',domain:'公开合成业务',status:'partially_active',active_release_id:releaseId,enabled_business_ids:[businessId]};
 const objects=[objectA,objectB].map((identity,index)=>({id:identity,entity_type:'site',type_name:'业务对象',version:1,name:['一号样地','二号样地'][index],reference:['一号样地','二号样地'][index],attributes:{name:['一号样地','二号样地'][index]},object_compatibility:{status:'current',usable:true,target_release_id:releaseId,source_release_number:4,source_type:'业务对象',source_label:['一号样地','二号样地'][index],reason:null}}));
 const schema={type:'object',properties:{watched:{type:'string',title:'等待结果对应对象',format:'uuid'},count:{type:'integer',title:'检查数量',minimum:0}},required:['watched','count'],additionalProperties:false};
 const metadata={schema:'workflow-graph-task-inputs-v1',...Object.fromEntries(['instance_id','release_id','business_id','configuration_hash'].map(k=>[k,scope[k]])),business_name:business.name,mode:'graph',graph_hash:'b'.repeat(64),binding_hash:'c'.repeat(64),hash:'d'.repeat(64),execution_granted:false,input_schema:schema,user_input_schema:schema,system_fields:['entity_ids'],entity_fields:[{field:'watched',label:'等待结果对应对象',required:true,waits:['等待已登记来源结果']}]};
 const goalOptions={schema:'native-goal-tools-v1',instance_id:instanceId,release_id:releaseId,business_id:businessId,configuration_hash:H,package_id:packageId,hash:H,tools:[],query_tools:[],relation_types:[],entities:objects,entities_truncated:false};
 function savedTask(payload){return {id:taskId,workspace_id:account.workspace_id,version:1,title:payload.title,business_name:business.name,business_id:businessId,instance_id:instanceId,instance_release_id:releaseId,instance_release_number:4,status:'workflow_waiting',creation_request_id:payload.request_id,creation_hash:'e'.repeat(64),entity_ids:copy(payload.entity_ids),business,roles:{负责人:{user_id:account.user_id,username:account.username}},object_snapshots:[],workflow_runtime:{current_step_id:null,receipts:[],drafts:{}},graph_runtime:{graph_hash:'b'.repeat(64),version:1,cancel_epoch:0,status:'waiting',clock:100,commands:[],activations:{}},graph_pending_humans:[],graph_jobs:{},graph_waits:[],permissions:{current_actor_role:'operator',can_cancel_task:true}};}
 return {instance,release,objects,metadata,goalOptions,savedTask};
}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources are forbidden in this synthetic DOM test');}}

async function harness(run,settings={}){
 const data=fixture();if(settings.numericRaw)data.metadata.user_input_schema.properties.count.type='number';const errors=[],unexpected=[],calls=[],windows=[],events=[],state={task:null,request:null,postBody:null,failWrites:false,failRelease:false,holdStorageEvents:true,objectOriginal:null,objectRow:null,objectRetired:null,me:null,switchAfterObjectLookup:false},nativeResponse=globalThis.Response;
 const corePaths=['/api/instances','/api/instances/'+instanceId+'/releases','/api/instances/'+instanceId+'/objects',...['tasks','observations','daily'].map(name=>'/api/workspace/instances/'+instanceId+'/'+name)],coreGates=new Set();
 function holdCore({name=data.instance.name,failRelease=false}={}){
  // Delay only the six real bucket transports. Each reply captures this
  // round's fixture at request setup; no controller, hook, or GET helper is
  // replaced, and releasing a stale round cannot borrow newer response data.
  const gate={paths:[],released:false,failRelease,values:new Map([[corePaths[0],[{...copy(data.instance),name}]],[corePaths[1],[copy(data.release)]],[corePaths[2],copy(data.objects)],[corePaths[3],state.task?[copy(state.task)]:[]],[corePaths[4],[]],[corePaths[5],[]]])};
  gate.wait=new Promise(resolve=>{gate.release=()=>{if(gate.released)return;gate.released=true;if(state.coreGate===gate)state.coreGate=null;resolve();};});
  coreGates.add(gate);state.coreGate=gate;return gate;
 }
 if(settings.learnedReceipt){
  state.receiptRow={id:id(30),entity_id:objectA,received_day:'2026-10-21',data_received:false,integrity_error:null,record:{modality:'imagery',name:'原本地变化候选',attributes:{'差异候选像素数':3,'未知像素数':1},review_state:'unreviewed',task_id:id(31)},graph_message_support:{schema:'native-learned-geo-graph-receipt-support-v141',status:'new_inputs_only',can_replay_as_new_message:false,message:'原处理、接收与等待分别核对。'}};
  state.receiptStage={schema:'native-learned-geo-graph-receipt-stages-v141',observation_id:state.receiptRow.id,observation_version:7,source_id:id(32),entity_id:objectA,task_id:state.receiptRow.record.task_id,collected:true,data_received:false,data_status:'blocked',data_message:'原接收受阻，结果仍保留。',deliveries:[],waiting_match:'not_checked',waiting_message:'请到相应等待任务核对原窗口。',candidate_only:true,source_time_verified:false,physical_state_verified:false};
  state.receiptPosts=0;state.receiptOriginalBody=null;
 }
 if(settings.humanTask){
  state.task=data.savedTask({title:'原链接中的人工核查任务',request_id:id(20),entity_ids:[objectA]});
  const schema={type:'object',additionalProperties:false,required:['note'],properties:{note:{type:'string',title:'原任务人工说明'}}};
  state.task.graph_runtime.activations.human={id:'human',node_id:'human-node',version:0,epoch:0,status:'ready',output:null,draft:null};
  state.task.graph_pending_humans=[{activation_id:'human',node_id:'human-node',source_step_id:'human-source',role:'负责人',actor_id:account.user_id,version:0,can_edit:true,output_schema:schema,draft:null}];
  state.task.graph_node_labels={human:{name:'原人工核查步骤',kind:'human',source_step_id:'human-source',output_schema:schema}};
 }
 if(settings.videoCandidate){
  state.task=data.savedTask({title:'原视频候选线索',request_id:id(20),entity_ids:[objectA]});
  for(const key of ['graph_runtime','graph_pending_humans','graph_jobs','graph_waits'])delete state.task[key];
  state.task.version=4;state.task.status='workflow_pending';state.task.video_workflow_inbox=true;
  state.task.workflow_runtime={current_step_id:'human-source',drafts:{},receipts:[],max_steps:100};
  state.task.perception_event={run_id:id(70),frame_id:id(71),video_alert_id:id(72),from_seconds:0.5,to_seconds:1.5,label:'cat',track_ref:'1:1'};
  state.task.permissions={current_actor_role:'operator',can_save_note:false,can_complete_step:false,can_run_ability:false,reason:'待交接'};
 }
 if(settings.ordinaryTask){
  state.task=data.savedTask({title:'原普通人工核查任务',request_id:id(20),entity_ids:[objectA]});
  for(const key of ['graph_runtime','graph_pending_humans','graph_jobs','graph_waits'])delete state.task[key];
  state.task.version=4;state.task.instance_release_id=id(41);state.task.instance_release_number=3;
  state.task.business=copy(state.task.business);delete state.task.business.execution_mode;
  state.task.business.flow.steps[0].condition='已逐项核对原业务依据';
  state.task.workflow_runtime={current_step_id:'human-source',drafts:{},receipts:[],max_steps:100};
  state.task.object_snapshots=[{id:objectA,type_name:'业务对象',version:1,attributes:{name:'一号样地'},fields:[{key:'name',label:'对象名称',kind:'文本',unit:null}]}];
  state.task.permissions={current_actor_role:'operator',can_save_note:true,can_complete_step:true,can_cancel:true,can_probe_ability:true,reasons:{}};
  state.ordinaryNotePuts=0;state.ordinaryCompletionPosts=0;state.ordinaryCompletionBody=null;
 }
 const graphRouteOptions={schema:'signal-graph-routing-options-v134',instance_id:instanceId,instance_version:1,release_id:releaseId,ttl_seconds:86400,max_tasks_limit:100,max_entities:30,sources:[],extractors:[],entities:[],businesses:[],can_enable:false,blocking_reason:'此合成任务场景未登记完整流程异常授权'};
 graphRouteOptions.options_hash=await graphWireDigest(graphRouteOptions,{crypto:webcrypto});
 const graphRouteList={schema:'signal-graph-routing-response-v134',instance_id:instanceId,instance_version:1,routes:[],receipt:null,current_matches:null,entity_state_updated:false,source_time_verified:false,device_actions:false};
 const globalNames=['window','document','navigator','HTMLElement','Event','StorageEvent','localStorage','sessionStorage','location','history','addEventListener','removeEventListener','fetch','IS_REACT_ACT_ENVIRONMENT'];
 const descriptors=Object.fromEntries(globalNames.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));let active=null,storage,restoreStorage;
 const tails=new Map(),locks={request(name,_options,fn){const result=(tails.get(name)||Promise.resolve()).then(fn);tails.set(name,result.catch(()=>{}));return result;}};
 function assign(name,value){Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});}
 function activate(tab){active=tab;for(const [name,value] of Object.entries({window:tab.dom.window,document:tab.dom.window.document,navigator:tab.dom.window.navigator,HTMLElement:tab.dom.window.HTMLElement,Event:tab.dom.window.Event,StorageEvent:tab.dom.window.StorageEvent,localStorage:storage,sessionStorage:tab.dom.window.sessionStorage,location:tab.dom.window.location,history:tab.dom.window.history,addEventListener:tab.dom.window.addEventListener.bind(tab.dom.window),removeEventListener:tab.dom.window.removeEventListener.bind(tab.dom.window),IS_REACT_ACT_ENVIRONMENT:true}))assign(name,value);}
 const response=(value,status=200)=>new nativeResponse(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const canonicalResponse=value=>{const canonical=graphWireRequestJSON(value);return response({schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value:JSON.parse(canonical)});};
 function ancillary(path){
  const base='/api/workspace/instances/'+instanceId;
  const names=['signals','reported-observations','reported-observation-options','candidate-watch-options','candidate-watches','signal-inbox','signal-routing','data-needs','native-receipts','local-observation-models','local-observations','local-observation-daily'];
  return names.some(name=>path===base+'/'+name||path.startsWith(base+'/'+name+'/'))
   ||path.startsWith('/api/instances/'+instanceId+'/relations')
   ||['/api/audio-daily','/api/imagery-daily','/api/evidence-assessments','/api/evidence-questions/models','/api/evidence-questions','/api/mqtt/instances/'+instanceId+'/days'].includes(path);
 }
 async function fetchSynthetic(url,options={}){
  const original=typeof url==='string'?url:url.url;
  if(typeof original!=='string'||!original.startsWith('/api/')){unexpected.push({url:original});throw Error('Network access forbidden');}
  const parsed=new URL(original,'https://synthetic-workspace.invalid'),path=parsed.pathname,method=options.method||'GET';calls.push({path,query:parsed.search,method,body:options.body,headers:options.headers});
  if(method==='GET'){
   // The two newer optional services explicitly report disabled in this legacy-workflow fixture.
   if(path==='/api/workspace/tasks/'+taskId+'/business-advice/offline-delegations')return response({enabled:false,items:[]});
   if(path==='/api/workspace/tasks/'+taskId+'/business-advice-v2/availability')return response({enabled:false,has_unfinished:false});
   const gate=state.coreGate;
   if(gate&&corePaths.includes(path)){
    assert.equal(options.headers['X-Workspace-ID'],account.workspace_id,'each bucket GET keeps its frozen workspace header');assert.equal(options.headers['X-CSRF-Token'],'synthetic-workspace-csrf','bucket coalescing does not replace the actual scoped requester');
    gate.paths.push(path);await gate.wait;
    return gate.failRelease&&path===corePaths[1]?response({detail:'此轮发布版本暂不可读；原任务和请求保留。'},503):response(gate.values.get(path));
   }
   if(path==='/api/me')return response({...account,...(state.me||{})});
   if(path==='/api/instances')return response([state.replacementActive?{...data.instance,active_release_id:id(40)}:data.instance]);
   if(path==='/api/instances/'+instanceId+'/releases')return state.failRelease?response({detail:'合成发布版本列表暂不可读；原任务与请求保留。'},503):response([state.replacementActive?{...data.release,id:id(40),number:5}:data.release]);
   if(path==='/api/instances/'+instanceId+'/objects')return response(data.objects);
   if(path==='/api/workspace/instances/'+instanceId+'/tasks')return response(state.task?[state.task]:[]);
   if(['/api/workspace/instances/'+instanceId+'/observations','/api/workspace/instances/'+instanceId+'/daily'].includes(path))return response([]);
   if(path==='/api/workspace/instances/'+instanceId+'/graph-task-inputs'){assert.equal(parsed.searchParams.get('release_id'),state.replacementActive?id(40):releaseId);assert.equal(parsed.searchParams.get('business_id'),businessId);return response(state.replacementActive?{...data.metadata,release_id:id(40)}:data.metadata);}
   if(path==='/api/workspace/instances/'+instanceId+'/pixel-change/task-options'){assert.equal(parsed.searchParams.get('release_id'),state.replacementActive?id(40):releaseId);assert.equal(parsed.searchParams.get('business_id'),businessId);return response({schema:'workflow-pixel-task-options-v250',instance_id:instanceId,release_id:state.replacementActive?id(40):releaseId,business_id:businessId,pixel_pair_required:false,provider:'task_pixel_pair_refs',single_entity:true,execution_mode:'confirmed_graph_workflow',capabilities:[],legacy_images_allowed:false,legacy_image_provider:null});}
   if(path==='/api/workspace/instances/'+instanceId+'/businesses/'+businessId+'/goal-tool-options')return response(state.replacementActive?{...data.goalOptions,release_id:id(40)}:data.goalOptions);
   if(['/api/workspace/instances/'+instanceId+'/input-images','/api/workspace/instances/'+instanceId+'/input-scenes','/api/geo/scenes'].includes(path))return response([]);
   if(path==='/api/workspace/tasks/'+taskId&&state.task)return response(state.task);
   if(settings.videoCandidate&&path==='/api/videos/'+id(70))return settings.videoDismissed?response({id:id(70),input_sha256:H,alerts:[{id:id(72),status:'dismissed'}],frames:[{id:id(71),evidence_id:id(73),frame_sha256:H,width:20,height:20,tracks:[],utc:null}]}):response({detail:'测试中原视频暂不可读'},503);
   if(settings.videoDismissed&&(path==='/api/videos/'+id(70)+'/original'||path==='/api/evidence/'+id(73)+'/content'))return response({detail:'测试中原视频暂不可读'},503);
   if(settings.videoCandidate&&path==='/api/workspace/tasks/'+taskId+'/video-handoff-context')return response({schema:'video-workflow-context-v347',source_task_id:taskId,source_task_version:4,instance_id:instanceId,release_id:releaseId,business_id:businessId,entity_id:objectA,basis_hash:H,linked_task_id:null,source_clock_verified:false,physical_state_verified:false,data_reception_verified:false});
   if(settings.videoCandidate&&path==='/api/workspace/tasks/'+taskId+'/video-data-reception')return response({schema:'video-candidate-data-reception-v349',state:'not_available',data_reception_verified:false,observation_id:null});
   // This fixture exercises an ordinary task, without a released V288
   // advice binding. Keep the real new card mounted and explicitly reject
   // its configuration read; do not replace the component or grant a model.
   if(path==='/api/workspace/tasks/'+taskId+'/business-advice/context'&&state.task)return response({detail:'此合成任务未配置同目标业务建议；原人工流程继续保留。'},409);
   if(path==='/api/workspace/tasks/'+taskId+'/business-advice/preparations'&&state.task)return response({schema:'same-goal-advice-operation-v302/list',task_id:taskId,records:[]});
   if(path==='/api/workspace/tasks/'+taskId+'/pixel-results'&&state.task)return response({schema:'workflow-pixel-results-v250',task_id:taskId,instance_id:instanceId,release_id:state.task.instance_release_id,business_id:state.task.business_id,pixel_pair_snapshot:null,records:[]});
   // Keep the real Task sound sibling mounted. This ordinary synthetic Task
   // has no chosen original audio or sound runs and grants no review write.
   // Only its exact read is declared; every other sound URL/mutation fails.
   if(method==='GET'&&path==='/api/workspace/tasks/'+taskId+'/sounds'&&state.task)return response({schema:'task-sound-results-v1',task_id:taskId,instance_id:state.task.instance_id,release_id:state.task.instance_release_id,business_id:state.task.business_id,sound_snapshots:[],records:[],review_access:{allowed:false,reason:'此合成任务没有环境声音原音或处理记录。',current_step_name:null}});
   if(settings.ordinaryTask&&path==='/api/workspace/tasks/'+taskId+'/goal')return response({detail:'此合成普通任务未配置业务目标；原人工步骤仍可核对。'},503);
   if(settings.ordinaryTask&&path==='/api/workspace/tasks/'+taskId+'/linked-signals')return response({records:[],has_more:false});
   if(settings.learnedReceipt&&path==='/api/workspace/instances/'+instanceId+'/native-receipts')return state.failReceiptList?response({detail:'合成多模收件目录暂不可读；原单条结果仍保留。'},503):response({available:0,not_ready:0,truncated:false,candidates:[],records:[{...state.receiptRow,data_received:state.receiptStage.data_received}],daily:[],history_count:0,has_more:false});
   if(settings.learnedReceipt&&path==='/api/workspace/instances/'+instanceId+'/graph-event-native-learned-geo-producers/observations/'+state.receiptRow.id+'/stages')return canonicalResponse(state.receiptStage);
   // The new sibling remains a real mounted component. Its actual closed
   // read-only wire explicitly reports no configured routing capabilities;
   // this fixture does not grant or exercise automatic graph execution.
   if(path==='/api/workspace/instances/'+instanceId+'/signal-graph-routing-v134/options')return canonicalResponse(graphRouteOptions);
   if(path==='/api/workspace/instances/'+instanceId+'/signal-graph-routing-v134')return canonicalResponse(graphRouteList);
   // The actual native-source sibling also stays mounted. These two exact
   // reads expose an empty existing directory, with no model or registration
   // authority. Any other native endpoint or mutation remains unexpected.
   const nativeSources='/api/workspace/instances/'+instanceId+'/graph-event-native-producers';
   if(path===nativeSources+'/options'){
    assert.deepEqual([...parsed.searchParams],[['limit','20']]);
    return canonicalResponse({schema:'graph-event-native-audio-producer-options-v1',instance_id:instanceId,sources:[],installations:[],next_after:null,can_register:false,reason:'此合成任务场景未登记本地语音来源；不会启动模型或创建来源。'});
   }
   if(path===nativeSources){
    assert.deepEqual([...parsed.searchParams],[['limit','20']]);
    return canonicalResponse({schema:'graph-event-native-audio-producer-page-v1',instance_id:instanceId,items:[],next_after:null,can_register:false});
   }
   const learnedSources='/api/workspace/instances/'+instanceId+'/graph-event-native-learned-geo-producers';
   if(path===learnedSources+'/options'){
    assert.deepEqual([...parsed.searchParams],[['limit','20']]);
    return canonicalResponse({schema:'graph-event-native-learned-geo-producer-options-v1',instance_id:instanceId,sources:[],capabilities:[],next_after:null,can_register:false,reason:'此合成任务场景没有已登记学习型变化来源；不会安装、推理或登记来源。'});
   }
   if(path===learnedSources){
    assert.deepEqual([...parsed.searchParams],[['limit','20']]);
    return canonicalResponse({schema:'graph-event-native-learned-geo-producer-page-v1',instance_id:instanceId,items:[],next_after:null,can_register:false});
   }
   // These real sibling panels remain mounted and display their explicit
   // unavailable states. No incomplete successful protocol is fabricated.
   if(ancillary(path))return response({detail:'此合成传输未提供附属收件；当前面板明确不可用。'},503);
  }
  if(settings.ordinaryTask&&method==='PUT'&&path==='/api/workspace/tasks/'+taskId+'/note'){
   const body=JSON.parse(options.body);assert.deepEqual(body,{version:state.task.version,step_id:'human-source',note:'配置慢读不能丢失的原说明',branch:true,override_reason:'',perception_decision:null});
   state.ordinaryNotePuts++;if(state.ordinaryNoteGate)await state.ordinaryNoteGate.promise;
   state.task={...state.task,version:state.task.version+1,workflow_runtime:{...state.task.workflow_runtime,drafts:{'human-source':{note:body.note,branch:body.branch,override_reason:body.override_reason,perception_decision:body.perception_decision,actor:account.user_id,at:'2026-10-21T10:00:00+00:00'}}}};return response(state.task);
  }
  if(settings.ordinaryTask&&method==='POST'&&path==='/api/workspace/tasks/'+taskId+'/complete-step'){
   assert.equal(state.ordinaryCompletionPosts,0,'only the single explicit original completion may be submitted in this fixture');const body=JSON.parse(options.body);
   assert.deepEqual(Object.keys(body).sort(),['version','step_id','note','branch','override_reason','perception_decision','request_id'].sort());assert.equal(body.version,state.task.version);assert.equal(body.step_id,'human-source');assert.equal(body.note,'配置慢读不能丢失的原说明');assert.equal(body.branch,true);assert.equal(typeof body.request_id,'string');
   state.ordinaryCompletionPosts++;state.ordinaryCompletionBody=options.body;
   throw TypeError('Synthetic original human completion reply lost; its outcome remains unknown');
  }
  if(settings.learnedReceipt&&method==='POST'&&path==='/api/workspace/instances/'+instanceId+'/graph-event-native-learned-geo-producers/observations/'+state.receiptRow.id+'/retry-data-delivery'){
   assert.equal(state.receiptPosts,0,'only one explicit retry of the original result is allowed');assert.deepEqual(JSON.parse(options.body),{expected_version:7,reason:'恢复原件和权限后，按原结果重试数据接收'});state.receiptPosts++;state.receiptOriginalBody=options.body;
   state.receiptStage={...state.receiptStage,observation_version:8,data_received:true,data_status:'received',data_message:'同一原结果的实际接收回执可核对。'};
   throw TypeError('Synthetic reply lost after the original data reception was saved');
  }
  const objectPath='/api/instances/'+instanceId+'/objects';
  if(method==='POST'&&path===objectPath){
   const pending=JSON.parse(storage.getItem(formKey+':object-create-v132'));assert.equal(pending.exact_body,options.body);assert.equal(state.objectOriginal,null);state.objectOriginal=options.body;
   const body=JSON.parse(options.body);assert.equal(body.attributes.name,'公开合成登记');if(settings.objectRaw)assert(options.body.includes('"reading":'+settings.objectRaw));
   state.objectRow={...data.objects[0],id:id(10),reference:body.reference,attributes:body.attributes};throw TypeError('Synthetic object reply lost after save');
  }
  if(method==='POST'&&(path===objectPath+'/requests/lookup-v132'||path===objectPath+'/requests/retire-v132')){
   const body=JSON.parse(options.body),original=body.original||body,exact=body.original?null:options.body;
   if(state.objectOriginal)assert.equal(exact,state.objectOriginal);
   if(body.original){assert.equal(state.objectRow,null);state.objectRetired={request_id:original.request_id,request_hash:H};}
   if(state.switchAfterObjectLookup&&state.objectRow)state.me={user_id:id(99)};return response({schema:'object-request-recovery-v132',actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instanceId,request_id:original.request_id,request_hash:H,found:!!state.objectRow,object:state.objectRow,retirement:state.objectRetired});
  }
  if(method==='POST'&&path==='/api/workspace/instances/'+instanceId+'/tasks'){
   assert.equal(state.postBody,null,'only one new task submission is allowed');assert.equal(typeof options.body,'string');
   assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-workspace-csrf');
   const payload=JSON.parse(options.body),pending=JSON.parse(storage.getItem(formKey+':task-create-pending'));
   assert.deepEqual(pending.payload,JSON.parse(JSON.stringify(payload)),'display mirror is ordinary JSON only');assert.equal(pending.exact_body,options.body,'independent exact original precedes POST');
   assert.equal(storage.getItem(formKey+':task-create-outbox:'+payload.request_id),storage.getItem(formKey+':task-create-pending'));
   assert.deepEqual(payload.graph_inputs,{count:settings.numericRaw?Number(settings.numericRaw):7,watched:settings.videoCandidate?objectA:objectB});if(settings.numericRaw)assert(options.body.includes('"count":'+settings.numericRaw));assert.deepEqual(payload.entity_ids,settings.videoCandidate?[objectA]:[objectA,objectB]);
   if(settings.videoCandidate)assert.deepEqual(payload.video_handoff,{source_task_id:taskId,source_task_version:4,basis_hash:H});
   state.postBody=options.body;state.task=data.savedTask(payload);state.request={request_id:payload.request_id,actor_id:account.user_id,instance_id:instanceId,release_id:releaseId,business_id:businessId,task_id:taskId,creation_hash:state.task.creation_hash};
   throw TypeError('Synthetic response lost after original Task was saved');
  }
  if(method==='POST'&&path==='/api/workspace/instances/'+instanceId+'/task-requests/lookup'){
   assert.equal(options.body,state.postBody,'lookup uses the complete original body, including original graph inputs');
   return response({schema:'workflow-task-request-recovery-v1',found:true,request:state.request});
  }
  unexpected.push({path,method});throw Error('Unexpected synthetic transport '+method+' '+path);
 }
 function flushStorageEvents(){for(const item of events.splice(0))for(const tab of windows)if(tab!==item.writer)tab.dom.window.dispatchEvent(new tab.dom.window.StorageEvent('storage',{key:item.key,oldValue:item.oldValue,newValue:item.newValue,url:item.writer?.dom.window.location.href||'https://synthetic-workspace.invalid/',storageArea:storage}));}
 // Native details queues its real toggle event as a browser task. Flush that
 // task before the React/microtask turns; never synthesize a toggle event or
 // directly mount the retained child from this transport-only fixture.
 const settle=async()=>{await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});for(let i=0;i<12;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 try{
  for(const name of ['A','B']){
   const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
   const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-workspace.invalid/?view=workspace&instance='+instanceId+((settings.humanTask||settings.videoCandidate)?'&task='+taskId:''),resources:new NoResources(),virtualConsole:console}),tab={name,dom,container:dom.window.document.getElementById('root'),root:null,guard:null,probe:{setup:0,cleanup:0}};
   // jsdom lacks browser scrolling; this platform-only shim has no product logic.
   Object.defineProperty(dom.window.HTMLElement.prototype,'scrollIntoView',{configurable:true,value(){}});
   Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});
   for(const [key,value] of Object.entries({'ga-setup-csrf':'synthetic-workspace-csrf','ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf-username':account.username}))dom.window.sessionStorage.setItem(key,value);
   windows.push(tab);
  }
  storage=windows[0].dom.window.localStorage;
  // Use the same native Storage object in two independent DOM windows. Their
  // sessionStorage and mounted React roots remain separate, as in real tabs.
  Object.defineProperty(windows[1].dom.window,'localStorage',{configurable:true,value:storage});
  const prototype=windows[0].dom.window.Storage.prototype,original=prototype.setItem;
  Object.defineProperty(prototype,'setItem',{configurable:true,writable:true,value:function(key,value){
   if(this===storage){if(state.failWrites||state.failSourcePrefix&&String(key).startsWith(state.failSourcePrefix)&&String(key).includes(':form:'))throw new windows[0].dom.window.DOMException('Synthetic storage quota exceeded','QuotaExceededError');const oldValue=this.getItem(key);original.call(this,key,value);if(oldValue!==String(value)){events.push({writer:active,key:String(key),oldValue,newValue:String(value)});if(!state.holdStorageEvents)queueMicrotask(flushStorageEvents);}}
   else original.call(this,key,value);
  }});restoreStorage=()=>Object.defineProperty(prototype,'setItem',{configurable:true,writable:true,value:original});
  storage.setItem(formKey,JSON.stringify({type:'site',attributes:{},reference:'',showObject:false,showTask:true,business:businessId,title:'初始任务草稿',selectedObjects:[objectA,objectB],selectedImages:[],selectedAudio:[],selectedScenes:[],comparisonScenes:[],comparisonConfirmed:false,goalForm:{...defaultNativeGoal(),enabled:false},graphInputDraft:null,creationStatus:null,creationAction:null,objectAction:null}));events.length=0;
  activate(windows[0]);assign('fetch',fetchSynthetic);
  const {createRoot}=await import('react-dom/client'),{BusinessWorkspace,Btn,Field}=await components();
  function Probe({tab}){useEffect(()=>{tab.probe.setup++;return()=>{tab.probe.cleanup++;};},[tab]);return null;}
  async function mount(tab){activate(tab);assert.equal(tab.root,null);tab.root=createRoot(tab.container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>tab.root.render(React.createElement(React.StrictMode,null,React.createElement(Probe,{tab}),React.createElement(BusinessWorkspace,{instanceId,account,Btn,Field,onBack(){},onRegisterGuard:value=>{tab.guard=value;}}))));await settle();}
  async function unmount(tab){if(!tab.root)return;activate(tab);const root=tab.root;tab.root=null;await act(async()=>root.unmount());await settle();}
  function field(tab,label){const value=tab.container.querySelector('[aria-label="'+label+'"]');assert.ok(value,tab.name+' actual DOM field '+label);return value;}
  function button(tab,label){const value=[...tab.container.querySelectorAll('button')].find(n=>n.textContent.trim()===label);assert.ok(value,tab.name+' actual DOM button '+label);return value;}
  async function edit(tab,label,value){activate(tab);const input=field(tab,label);assert.equal(input.disabled,false,label+' enabled');const proto=input.tagName==='SELECT'?tab.dom.window.HTMLSelectElement.prototype:input.tagName==='TEXTAREA'?tab.dom.window.HTMLTextAreaElement.prototype:tab.dom.window.HTMLInputElement.prototype;
   await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(input,value);input.dispatchEvent(new tab.dom.window.Event(input.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();}
  async function click(tab,label){activate(tab);const control=button(tab,label);assert.equal(control.disabled,false,label+' enabled');await act(async()=>control.click());await settle();}
  async function guard(tab){activate(tab);assert.equal(typeof tab.guard,'function');let result;await act(async()=>{result=await tab.guard();});await settle();return result;}
  function beforeUnload(tab){activate(tab);const event=new tab.dom.window.Event('beforeunload',{cancelable:true});tab.dom.window.dispatchEvent(event);return event;}
  function section(tab,label){const node=[...tab.container.querySelectorAll('details')].find(value=>[...value.children].some(child=>child.tagName==='SUMMARY'&&child.textContent.trim()===label));assert.ok(node,tab.name+' actual details '+label);return node;}
  async function toggle(tab,label,open=true){activate(tab);const node=section(tab,label);if(node.open!==open){await act(async()=>node.querySelector('summary').click());await settle();}assert.equal(node.open,open,label+' actual native open state');return node;}
  await run({A:windows[0],B:windows[1],state,calls,storage,mount,unmount,edit,click,field,button,guard,beforeUnload,settle,flushStorageEvents,activate,section,toggle,holdCore,corePaths});
  for(const tab of windows)await unmount(tab);
  assert.deepEqual(unexpected,[],'every transport route must be explicit');assert.deepEqual(errors,[],'no React or jsdom runtime errors');
 }finally{
  state.failWrites=false;
  state.ordinaryNoteGate?.release();
  for(const gate of coreGates)gate.release();
  for(const tab of windows)if(tab.root){activate(tab);try{await act(async()=>tab.root.unmount());}finally{tab.root=null;}}
  restoreStorage?.();for(const tab of windows)tab.dom.window.close();
  for(const name of globalNames){if(descriptors[name])Object.defineProperty(globalThis,name,descriptors[name]);else delete globalThis[name];}
 }
}

const sourceSection={audio:'本实例的本地语音结果来源与原请求找回',learned:'本实例的变化候选来源与原请求找回'};
const sourceBase=family=>'/api/workspace/instances/'+instanceId+(family==='audio'?'/graph-event-native-producers':'/graph-event-native-learned-geo-producers');
const sourceReads=(calls,family)=>calls.filter(row=>row.method==='GET'&&(row.path===sourceBase(family)||row.path.startsWith(sourceBase(family)+'/')));
const deviceReads=calls=>calls.filter(row=>row.method==='GET'&&['/api/workspace/instances/'+instanceId+'/native-receipts','/api/workspace/instances/'+instanceId+'/local-observation-models','/api/workspace/instances/'+instanceId+'/local-observations','/api/workspace/instances/'+instanceId+'/local-observation-daily','/api/mqtt/instances/'+instanceId+'/days','/api/audio-daily','/api/imagery-daily'].includes(row.path));

test('V142 actual workspace initially closed source and device sections do not mount or issue their reads',async()=>harness(async h=>{
 await h.mount(h.A);assert.ok(h.A.probe.setup>=2&&h.A.probe.cleanup>=1,'actual StrictMode replay remains enabled');
 for(const label of [...Object.values(sourceSection),'设备数据与每日整理'])assert.equal(h.section(h.A,label).open,false);
 assert.ok(h.A.container.querySelector('.native-graph-sources-v136')===null,'unopened Audio form is absent');assert.ok(h.A.container.querySelector('.native-learned-graph-sources-v141')===null,'unopened learned form is absent');
 assert.equal(sourceReads(h.calls,'audio').length,0,'unopened Audio section must not query source options or records');assert.equal(sourceReads(h.calls,'learned').length,0,'unopened learned section must not query source options or records');assert.equal(deviceReads(h.calls).length,0,'unopened device section must not query receipts, local analysis, or daily records');
 assert.ok(h.calls.some(row=>row.path==='/api/workspace/instances/'+instanceId+'/tasks'),'core saved Task history still loads');assert.ok(h.calls.some(row=>row.path==='/api/workspace/instances/'+instanceId+'/observations'),'core business inbox still loads');assert.equal(h.calls.some(row=>row.method==='POST'),false);
}));

for(const family of ['audio','learned'])test('V142 first opening '+family+' loads only that source, then collapse and reopen preserve its actual draft and controller',async()=>harness(async h=>{
 await h.mount(h.A);assert.equal(sourceReads(h.calls,family).length,0);const other=family==='audio'?'learned':'audio';
 const node=await h.toggle(h.A,sourceSection[family]);
 const input=()=>[...node.querySelectorAll('label')].find(label=>label.firstChild?.textContent==='结果来源名称')?.querySelector('input');
 for(let index=0;index<30&&(!input()||input().disabled);index++)await h.settle();assert.ok(input(),'actual selected source form mounts after native summary click');assert.equal(input().disabled,false);
 assert.ok(sourceReads(h.calls,family).some(row=>row.path.endsWith('/options')));assert.ok(sourceReads(h.calls,family).some(row=>row.path===sourceBase(family)));assert.equal(sourceReads(h.calls,other).length,0);assert.equal(deviceReads(h.calls).length,0);
 h.activate(h.A);const originalInput=input();await act(async()=>{Object.getOwnPropertyDescriptor(h.A.dom.window.HTMLInputElement.prototype,'value').set.call(originalInput,'关闭后仍属于原窗口的人稿');originalInput.dispatchEvent(new h.A.dom.window.Event('input',{bubbles:true}));});await h.settle();
 const prefix=(family==='audio'?'ga-native-graph-sources-v136:':'ga-native-learned-graph-sources-v141:')+account.workspace_id+':'+account.user_id+':'+instanceId+':';
 const originals=Object.keys(h.storage).filter(key=>key.startsWith(prefix)).sort().map(key=>[key,h.storage.getItem(key)]),reads=sourceReads(h.calls,family).length;
 await h.toggle(h.A,sourceSection[family],false);assert.ok(input()===originalInput,'collapse must not unmount the initialized form');assert.equal(input().value,'关闭后仍属于原窗口的人稿');
 await h.toggle(h.A,sourceSection[family],true);assert.ok(input()===originalInput,'reopen retains the same initialized input node');assert.equal(input().value,'关闭后仍属于原窗口的人稿');assert.equal(sourceReads(h.calls,family).length,reads,'reopen must not create another source controller or refresh implicitly');assert.deepEqual(Object.keys(h.storage).filter(key=>key.startsWith(prefix)).sort().map(key=>[key,h.storage.getItem(key)]),originals);assert.equal(h.calls.some(row=>row.method==='POST'),false);
}));

test('V142 actual receipt button programmatically opens and first-mounts the original Audio source section',async()=>harness(async h=>{
 await h.mount(h.A);assert.equal(sourceReads(h.calls,'audio').length,0);await h.toggle(h.A,'设备数据与每日整理');
 assert.ok(deviceReads(h.calls).length>0,'the actual device children now query their explicitly unavailable synthetic transport');assert.equal(sourceReads(h.calls,'audio').length,0);assert.equal(h.section(h.A,sourceSection.audio).open,false);
 await h.click(h.A,'登记本实例的本地语音结果来源');for(let index=0;index<30&&!h.A.container.querySelector('.native-graph-sources-v136');index++)await h.settle();
 assert.equal(h.section(h.A,sourceSection.audio).open,true);assert.ok(h.A.container.querySelector('.native-graph-sources-v136'),'actual parent ref.open path activates Audio');assert.ok(sourceReads(h.calls,'audio').length>0);assert.equal(sourceReads(h.calls,'learned').length,0);assert.equal(h.calls.some(row=>row.method==='POST'),false);
}));

test('V142 direct original graph Task and human draft stay in one DOM position while ancillary sections first-open and collapse',async()=>harness(async h=>{
 await h.mount(h.A);const panel=h.A.container.querySelector('[aria-label="完整流程任务"]');assert.ok(panel,'actual original Task URL mounts GraphTaskPanel');const input=panel.querySelector('textarea');assert.ok(input);assert.equal(input.disabled,false);
 h.activate(h.A);await act(async()=>{Object.getOwnPropertyDescriptor(h.A.dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'原任务不能随折叠区卸载的人稿');input.dispatchEvent(new h.A.dom.window.Event('input',{bubbles:true}));});await h.settle();
 for(const label of [...Object.values(sourceSection),'设备数据与每日整理']){await h.toggle(h.A,label);await h.toggle(h.A,label,false);assert.ok(h.A.container.querySelector('[aria-label="完整流程任务"]')===panel,'ancillary toggles retain original graph panel node');assert.ok(panel.querySelector('textarea')===input,'ancillary toggles retain original human input node');assert.equal(input.value,'原任务不能随折叠区卸载的人稿');}
 assert.ok(Object.keys(h.storage).some(key=>(h.storage.getItem(key)||'').includes('原任务不能随折叠区卸载的人稿')));assert.equal(h.calls.some(row=>row.method==='POST'),false);
},{humanTask:true}));

for(const {laterRelease,failReceiptList}of [{laterRelease:false,failReceiptList:false},{laterRelease:true,failReceiptList:false},{laterRelease:true,failReceiptList:true}])test('V142 unknown original data receipt retains its mounted recovery and leave protection across unavailable current release, then reconciles only by GET'+(laterRelease?' after a different release':'')+(failReceiptList?' with receipt directory unavailable':''),async()=>harness(async h=>{
 await h.mount(h.A);await h.toggle(h.A,'设备数据与每日整理');await h.click(h.A,'核对变化候选接收与接纳');await h.click(h.A,'按原结果重试数据接收');
 assert.equal(h.state.receiptPosts,1);assert.deepEqual(JSON.parse(h.state.receiptOriginalBody),{expected_version:7,reason:'恢复原件和权限后，按原结果重试数据接收'});assert.equal(await h.guard(h.A),false);assert.equal(h.beforeUnload(h.A).defaultPrevented,true);
 const receipt=()=>[...h.A.container.querySelectorAll('h3')].find(node=>node.textContent==='归集已完成的影像、音频和视频处理')?.closest('section'),original=receipt();assert.ok(original,'actual NativeReceipt controller is mounted');assert.ok(h.button(h.A,'只核对这份结果的数据接收'));
 h.state.failRelease=true;h.state.failReceiptList=failReceiptList;await h.click(h.A,'刷新业务收件');
 assert.ok(h.A.container.textContent.includes('当前启用版本尚未读到'),'failed current bucket does not claim an old release is executable');assert.ok(receipt()===original,'current release failure must retain the initialized NativeReceipt instance');assert.equal(await h.guard(h.A),false,'read failure must not clear original unknown-operation protection');assert.equal(h.beforeUnload(h.A).defaultPrevented,true);
 assert.equal(h.button(h.A,'归集已完成处理').disabled,true,'new collection is blocked without a verified current release');assert.ok(h.button(h.A,'只核对这份结果的数据接收'),'original recovery stays accessible independently of the current data listing');assert.equal([...h.A.container.querySelectorAll('button')].some(node=>node.textContent.trim()==='按原结果重试数据接收'),false,'unknown result must not regain a fresh mutation action');
 await h.toggle(h.A,'设备数据与每日整理',false);assert.ok(receipt()===original);assert.equal(await h.guard(h.A),false);await h.toggle(h.A,'设备数据与每日整理',true);
 h.state.failRelease=false;h.state.replacementActive=laterRelease;await act(async()=>{await new Promise(resolve=>setTimeout(resolve,3100));});await h.settle();
 assert.ok(receipt()===original,'configuration recovery must not replace the original receiver');assert.equal(await h.guard(h.A),false,'a new current configuration read does not settle the original operation');const mutations=h.calls.filter(row=>row.method==='POST').length;
 await h.click(h.A,'只核对这份结果的数据接收');assert.equal(h.calls.filter(row=>row.method==='POST').length,mutations);assert.equal(h.state.receiptPosts,1);assert.equal(await h.guard(h.A),true);assert.equal(h.beforeUnload(h.A).defaultPrevented,false);assert.ok(h.A.container.textContent.includes('同一原结果的实际接收回执可核对'));
 const posts=h.calls.filter(row=>row.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].body,h.state.receiptOriginalBody);assert.ok(h.calls.filter(row=>row.method==='GET'&&row.path.endsWith('/observations/'+h.state.receiptRow.id+'/stages')).length>=2);
},{learnedReceipt:true}));

test('V143 ordinary human Task retains its original draft, cancellation and unknown completion when current release cannot be read',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'打开任务：原普通人工核查任务');
 const panel=()=>[...h.A.container.querySelectorAll('h2')].find(node=>node.textContent==='原普通人工核查任务')?.closest('section'),original=panel();assert.ok(original,'actual ordinary workflow Task is selected from its saved Task list');
 await h.edit(h.A,'业务步骤处理结果','配置慢读不能丢失的原说明');await h.edit(h.A,'业务流程分支选择','yes');
 await h.click(h.A,'取消任务');await h.edit(h.A,'业务任务取消原因','原任务的取消草稿也须保留');
 const note=h.field(h.A,'业务步骤处理结果'),branch=h.field(h.A,'业务流程分支选择'),cancelReason=h.field(h.A,'业务任务取消原因');
 assert.equal(h.button(h.A,'保存结果并继续流程').disabled,false,'the original human step is eligible before current configuration becomes unavailable');
 await h.toggle(h.A,'设备数据与每日整理');h.state.failRelease=true;await h.click(h.A,'刷新业务收件');
 assert.equal(h.A.container.textContent.includes('当前启用版本尚未读到'),true);assert.ok(panel()===original,'current release failure must retain the initialized ordinary Task section');
 assert.ok(h.field(h.A,'业务步骤处理结果')===note,'the original human note input keeps its DOM identity');assert.equal(note.value,'配置慢读不能丢失的原说明');assert.ok(h.field(h.A,'业务流程分支选择')===branch);assert.equal(branch.value,'yes');
 assert.ok(h.field(h.A,'业务任务取消原因')===cancelReason);assert.equal(cancelReason.value,'原任务的取消草稿也须保留');assert.equal(h.button(h.A,'保存结果并继续流程').disabled,true,'unavailable current release must block fresh workflow progression');assert.equal(h.button(h.A,'保存处理说明').disabled,false,'saving the original note remains governed by the actual Task permission');assert.equal(h.button(h.A,'确认取消此任务').disabled,false,'the original cancellation entry remains available without pretending the current release is valid');
 h.state.failRelease=false;await h.click(h.A,'刷新业务收件');assert.ok(panel()===original);assert.equal(h.button(h.A,'保存结果并继续流程').disabled,false);
 await h.click(h.A,'保存结果并继续流程');assert.equal(h.state.ordinaryNotePuts,1);assert.equal(h.state.ordinaryCompletionPosts,1);
 const key='ga-workflow-note:'+account.workspace_id+':'+account.username+':'+taskId,raw=h.storage.getItem(key),saved=JSON.parse(raw);assert.equal(saved.pending,true);assert.deepEqual(saved.action,JSON.parse(h.state.ordinaryCompletionBody));assert.equal(saved.action.note,note.value);assert.equal(saved.action.branch,true);assert.equal(await h.guard(h.A),false,'the pending original completion cannot be bypassed by leaving the parent');
 h.state.failRelease=true;await h.click(h.A,'刷新业务收件');
 assert.ok(panel()===original,'the same ordinary Task and its child recovery controllers survive a second failed current read');assert.ok(h.field(h.A,'业务步骤处理结果')===note);assert.equal(note.value,'配置慢读不能丢失的原说明');assert.equal(branch.value,'yes');assert.equal(cancelReason.value,'原任务的取消草稿也须保留');assert.equal(h.storage.getItem(key),raw,'the complete original request and nonce are not reconstructed or cleared');
 assert.equal(h.button(h.A,'核对并重试已提交结果').disabled,false,'the original completion lookup/replay entry is retained separately from new progression');assert.equal(h.button(h.A,'保存结果并继续流程').disabled,true);assert.equal(h.button(h.A,'确认取消此任务').disabled,false);assert.equal(await h.guard(h.A),false);
 assert.equal(h.state.ordinaryCompletionPosts,1,'configuration reads must not automatically replay the original completion');assert.equal(h.calls.filter(row=>row.method==='POST').length,1);assert.equal(h.calls.filter(row=>row.method==='PUT').length,1);assert.ok(h.A.probe.setup>=2&&h.A.probe.cleanup>=1,'real StrictMode remains enabled');
},{ordinaryTask:true}));

test('V143 fresh human completion rechecks current eligibility after original note PUT finishes',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'打开任务：原普通人工核查任务');await h.edit(h.A,'业务步骤处理结果','配置慢读不能丢失的原说明');await h.edit(h.A,'业务流程分支选择','yes');
 let release;h.state.ordinaryNoteGate={promise:new Promise(resolve=>{release=resolve;}),release:()=>release()};
 await h.click(h.A,'保存结果并继续流程');assert.equal(h.state.ordinaryNotePuts,1,'the original note PUT has actually reached the synthetic transport');assert.equal(h.state.ordinaryCompletionPosts,0);
 h.state.failRelease=true;await act(async()=>{await new Promise(resolve=>setTimeout(resolve,3100));});await h.settle();
 assert.equal(h.A.container.textContent.includes('当前启用版本尚未读到'),true,'the actual parent interval has completed a failing current-release read while note PUT is held');
 await act(async()=>h.state.ordinaryNoteGate.release());await h.settle();
 assert.equal(h.state.ordinaryCompletionPosts,0,'the late note save must not dispatch a new completion after current eligibility became unavailable');
 assert.equal(h.calls.filter(row=>row.method==='POST'&&row.path.endsWith('/complete-step')).length,0);assert.equal(h.state.ordinaryNotePuts,1);assert.equal(h.state.task.workflow_runtime.drafts['human-source'].note,'配置慢读不能丢失的原说明','the original human note still saves independently');assert.equal(h.state.task.workflow_runtime.drafts['human-source'].branch,true);
 assert.equal(h.field(h.A,'业务步骤处理结果').value,'配置慢读不能丢失的原说明');assert.equal(h.field(h.A,'业务流程分支选择').value,'yes');assert.equal(h.button(h.A,'保存结果并继续流程').disabled,true);assert.equal(h.button(h.A,'取消任务').disabled,false);
},{ordinaryTask:true}));

test('V142 actual parent coalesces a delayed StrictMode mount and real interval, then gives manual refresh one fresh successor',async()=>harness(async h=>{
 const initial=h.holdCore({name:'首次尚未确认的旧轮实例'});await h.mount(h.A);
 assert.ok(h.A.probe.setup>=2&&h.A.probe.cleanup>=1,'the real StrictMode effects replay');
 assert.deepEqual([...initial.paths].sort(),[...h.corePaths].sort(),'StrictMode must issue one set of six bucket GETs, not one per setup');
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,3100));});await h.settle();
 assert.deepEqual([...initial.paths].sort(),[...h.corePaths].sort(),'the actual three-second interval joins the outstanding first round');
 await h.toggle(h.A,'设备数据与每日整理');const successor=h.holdCore({name:'明确刷新后独立读取的实例'});await h.click(h.A,'刷新业务收件');
 assert.equal(successor.paths.length,0,'manual refresh must wait for the in-flight round before starting its one successor');
 await act(async()=>initial.release());await h.settle();
 assert.deepEqual([...successor.paths].sort(),[...h.corePaths].sort(),'manual refresh starts exactly one new set of actual bucket GETs');
 assert.equal(h.A.container.textContent.includes('首次尚未确认的旧轮实例'),false,'the superseded pre-refresh round cannot be displayed as the explicit fresh read');
 assert.equal(h.calls.filter(row=>row.method==='GET'&&h.corePaths.includes(row.path)).length,12,'only first round plus its single fresh successor reached HTTP');
 await act(async()=>successor.release());await h.settle();
 assert.equal(h.A.container.querySelector('h1')?.textContent,'明确刷新后独立读取的实例');assert.equal(h.calls.some(row=>row.method!=='GET'),false,'joining or requesting fresh reads never sends a mutation');
}));

test('V142 actual parent does not apply an old failing poll over a queued manual read and still reports a current failure',async()=>harness(async h=>{
 await h.mount(h.A);await h.toggle(h.A,'设备数据与每日整理');const old=h.holdCore({name:'不应回显的旧轮实例',failRelease:true});
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,3100));});await h.settle();
 assert.deepEqual([...old.paths].sort(),[...h.corePaths].sort(),'one actual interval round is held at its six HTTP requests');
 const fresh=h.holdCore({name:'后继核对成功的当前实例'});await h.click(h.A,'刷新业务收件');assert.equal(fresh.paths.length,0);
 await act(async()=>old.release());await h.settle();
 assert.deepEqual([...fresh.paths].sort(),[...h.corePaths].sort(),'manual read uses its fresh successor after the old failure');
 assert.equal(h.A.container.querySelector('h1')?.textContent,'合成业务工作区','the old failed round cannot replace the last actual current read');
 assert.equal(h.A.container.textContent.includes('不应回显的旧轮实例'),false);assert.equal(h.A.container.textContent.includes('此轮发布版本暂不可读'),false,'a superseded error cannot become the current workspace error');
 await act(async()=>fresh.release());await h.settle();assert.equal(h.A.container.querySelector('h1')?.textContent,'后继核对成功的当前实例');
 const current=h.holdCore({failRelease:true});await h.click(h.A,'刷新业务收件');assert.deepEqual([...current.paths].sort(),[...h.corePaths].sort());
 await act(async()=>current.release());await h.settle();
 assert.equal(h.A.container.textContent.includes('此轮发布版本暂不可读'),true,'a genuine current release failure remains visible rather than swallowed as stale');assert.equal(h.A.container.textContent.includes('当前启用版本尚未读到'),true,'current failed read removes execution eligibility instead of using a cached release');
 assert.equal(h.calls.filter(row=>row.method==='GET'&&h.corePaths.includes(row.path)).length,24);assert.equal(h.calls.some(row=>row.method!=='GET'),false);
}));

for(const blockedFamily of ['audio','learned'])test('actual workspace leave guard preserves '+blockedFamily+' unsaved source when the other real source panel saves successfully',async()=>harness(async h=>{
 await h.mount(h.A);
 await h.toggle(h.A,sourceSection.audio);await h.toggle(h.A,sourceSection.learned);
 const entry=family=>h.A.container.querySelector(family==='audio'?'.native-graph-sources-v136':'.native-learned-graph-sources-v141');
 for(let index=0;index<30&&(!entry('audio')||!entry('learned'));index++)await h.settle();
 assert.ok(entry('audio'),'actual Audio source panel mounted');assert.ok(entry('learned'),'actual learned source panel mounted');assert.equal(await h.guard(h.A),true,'initial actual parent guard is clear');
 const input=family=>[...entry(family).querySelectorAll('label')].find(node=>node.firstChild?.textContent==='结果来源名称')?.querySelector('input');
 const edit=async(family,value)=>{h.activate(h.A);const node=input(family);assert.ok(node,'actual '+family+' draft field');assert.equal(node.disabled,false);await act(async()=>{Object.getOwnPropertyDescriptor(h.A.dom.window.HTMLInputElement.prototype,'value').set.call(node,value);node.dispatchEvent(new h.A.dom.window.Event('input',{bubbles:true}));});await h.settle();assert.equal(input(family).value,value);};
 const other=blockedFamily==='audio'?'learned':'audio';
 h.state.failSourcePrefix=(blockedFamily==='audio'?'ga-native-graph-sources-v136:':'ga-native-learned-graph-sources-v141:')+account.workspace_id+':'+account.user_id+':'+instanceId+':';
 await edit(blockedFamily,'该入口尚未持久保存的人稿');assert.equal(await h.guard(h.A),false);assert.equal(h.beforeUnload(h.A).defaultPrevented,true);
 const originalInput=input(blockedFamily);await h.toggle(h.A,sourceSection[blockedFamily],false);assert.ok(input(blockedFamily)===originalInput,'folded source retains original quota-blocked input node');assert.equal(await h.guard(h.A),false,'collapse must retain the actual parent protection ref');assert.equal(h.beforeUnload(h.A).defaultPrevented,true);
 await edit(other,'另一真实入口已完整保存的人稿');assert.equal(await h.guard(h.A),false,'other successful writer cannot clear the first source protection ref');assert.equal(input(blockedFamily).value,'该入口尚未持久保存的人稿');assert.equal(h.beforeUnload(h.A).defaultPrevented,true);
 await h.toggle(h.A,sourceSection[blockedFamily],true);assert.ok(input(blockedFamily)===originalInput,'folded source retains original quota-blocked input node');assert.equal(input(blockedFamily).value,'该入口尚未持久保存的人稿');
 h.state.failSourcePrefix=null;const retry=[...entry(blockedFamily).querySelectorAll('button')].find(node=>node.textContent.trim()==='重试保存本页输入');assert.ok(retry);assert.equal(retry.disabled,false);await act(async()=>retry.click());await h.settle();assert.equal(await h.guard(h.A),true);assert.equal(h.beforeUnload(h.A).defaultPrevented,false);
 assert.equal(h.calls.some(row=>row.method==='POST'),false,'parent protection check neither registers a source nor starts a model');
}));

for(const staleEdit of ['title','graph-input'])test('two mounted workspace windows preserve unknown original Task when stale tab edits '+staleEdit,async()=>harness(async h=>{
 await h.mount(h.A);assert.ok(h.A.probe.setup>=2&&h.A.probe.cleanup>=1,'actual StrictMode replay');
 await h.edit(h.A,'业务任务名称','原申请：核对二号样地');await h.edit(h.A,'检查数量','7');await h.edit(h.A,'等待结果对应对象',objectB);
 await h.mount(h.B);assert.notEqual(h.A.dom.window,h.B.dom.window);assert.equal(h.B.dom.window.localStorage,h.A.dom.window.localStorage);assert.notEqual(h.A.dom.window.sessionStorage,h.B.dom.window.sessionStorage);
 assert.equal(h.field(h.B,'业务任务名称').value,'原申请：核对二号样地');
 await h.click(h.A,'保存并打开任务');assert.equal(h.calls.filter(v=>v.path==='/api/workspace/instances/'+instanceId+'/tasks'&&v.method==='POST').length,1);
 const pointer=formKey+':task-create-pending',original=h.storage.getItem(pointer),record=JSON.parse(original),immutable=formKey+':task-create-outbox:'+record.payload.request_id;
 assert.equal(record.payload.graph_inputs.watched,objectB);assert.equal(h.storage.getItem(immutable),original);
 if(staleEdit==='title'){await h.edit(h.B,'业务任务名称','另一窗口尚未提交的名称');assert.equal(h.field(h.B,'业务任务名称').value,'另一窗口尚未提交的名称');}
 else{await h.edit(h.B,'检查数量','99');assert.equal(h.field(h.B,'检查数量').value,'99');}
 const journals=()=>Array.from({length:h.storage.length},(_,index)=>h.storage.key(index)).filter(key=>key.startsWith(formKey+':task-form-journal:')).map(key=>JSON.parse(h.storage.getItem(key)));
 const retained=()=>journals().some(row=>staleEdit==='title'?row.form.title==='另一窗口尚未提交的名称':row.form.graphInputDraft?.values.count.raw==='99');
 assert.ok(retained(),'stale tab human input is independently durable even when the shared-form write is blocked');
 assert.equal(h.storage.getItem(pointer),original,'stale form edits cannot erase the original independent pending');assert.equal(h.storage.getItem(immutable),original,'immutable original body and source inputs cannot be replaced');
 await h.unmount(h.A);await h.mount(h.A);assert.equal(h.field(h.A,'业务任务名称').value,record.payload.title);assert.equal(h.field(h.A,'检查数量').value,'7');assert.equal(h.field(h.A,'等待结果对应对象').value,objectB);
 assert.equal(h.storage.getItem(pointer),original);await h.click(h.A,'核对原发起请求');
 assert.equal(h.calls.filter(v=>v.path==='/api/workspace/instances/'+instanceId+'/tasks'&&v.method==='POST').length,1,'lookup and cold Task read never issue a second creation');
 const lookups=h.calls.filter(v=>v.path.endsWith('/task-requests/lookup'));assert.ok(lookups.length>=1);assert.ok(lookups.every(v=>v.body===h.state.postBody));
 assert.equal(h.storage.getItem(pointer),null);assert.equal(h.storage.getItem(immutable),original);assert.deepEqual(JSON.parse(h.storage.getItem(formKey+':creation-history:'+record.payload.request_id)).payload,record.payload);
 assert.ok(h.A.container.textContent.includes('原申请：核对二号样地'));assert.ok(h.A.container.textContent.includes('任务流程已保存'));
 assert.ok(retained(),'settling the original request keeps the other window human journal');
 await h.click(h.B,'查看本机保留的任务草稿');assert.ok(h.B.container.textContent.includes(staleEdit==='title'?'另一窗口尚未提交的名称':'本机任务草稿'));
}));

test('quota failure during graph input edit blocks actual leave guard and beforeunload until storage recovers',async()=>harness(async h=>{
 await h.mount(h.A);await h.edit(h.A,'检查数量','7');await h.edit(h.A,'等待结果对应对象',objectB);
 h.state.failWrites=true;await h.edit(h.A,'检查数量','41');assert.equal(h.field(h.A,'检查数量').value,'41');
 assert.ok([...h.A.container.querySelectorAll('[role=alert]')].some(n=>/未|保留|保存/.test(n.textContent)));
 assert.equal(await h.guard(h.A),false);assert.equal(h.beforeUnload(h.A).defaultPrevented,true);
 h.state.failWrites=false;assert.equal(await h.guard(h.A),true);assert.equal(h.beforeUnload(h.A).defaultPrevented,false);
 const saved=JSON.parse(h.storage.getItem(formKey));assert.equal(saved.graphInputDraft.values.count.raw,'41');assert.equal(saved.graphInputDraft.values.watched.raw,objectB);
 const histories=Object.keys(h.storage).filter(k=>k.startsWith(formKey+':task-input-history:')).map(k=>JSON.parse(h.storage.getItem(k)));assert.ok(histories.some(row=>row.draft.values.count.raw==='41'),'latest human input survives in independent history');
 assert.equal(h.calls.filter(v=>v.path==='/api/workspace/instances/'+instanceId+'/tasks'&&v.method==='POST').length,0);
 await h.unmount(h.A);await h.mount(h.A);assert.equal(h.field(h.A,'检查数量').value,'41');assert.equal(h.field(h.A,'等待结果对应对象').value,objectB);
}));

test('an unknown original Task remains recoverable after remount when current releases are unavailable',async()=>harness(async h=>{
 await h.mount(h.A);await h.edit(h.A,'业务任务名称','版本暂不可读时仍可找回的任务');await h.edit(h.A,'检查数量','7');await h.edit(h.A,'等待结果对应对象',objectB);
 await h.click(h.A,'保存并打开任务');const pointer=formKey+':task-create-pending',original=h.storage.getItem(pointer),record=JSON.parse(original),before=h.calls.length;
 h.state.failRelease=true;await h.unmount(h.A);await h.mount(h.A);
 assert.ok(h.calls.slice(before).some(v=>v.path==='/api/instances/'+instanceId+'/releases'));
 assert.ok(h.A.container.textContent.includes('核对已保留的发起请求'));
 assert.ok(h.A.container.textContent.includes('合成发布版本列表暂不可读'));
 assert.equal(h.storage.getItem(pointer),original);await h.click(h.A,'核对原发起请求');
 assert.equal(h.calls.filter(v=>v.path==='/api/workspace/instances/'+instanceId+'/tasks'&&v.method==='POST').length,1);
 assert.ok(h.calls.some(v=>v.path.endsWith('/task-requests/lookup')&&v.body===h.state.postBody));
 assert.ok(h.calls.some(v=>v.path==='/api/workspace/tasks/'+taskId));
 assert.equal(h.storage.getItem(pointer),null);assert.ok(h.storage.getItem(formKey+':task-create-outbox:'+record.payload.request_id));
 assert.ok(h.A.container.querySelector('[aria-label="完整流程任务"]'),'original Task is actually mounted, not merely acknowledged');
 assert.ok(h.A.container.textContent.includes('版本暂不可读时仍可找回的任务'));assert.ok(h.A.container.textContent.includes('任务流程已保存'));
}));

test('a mounted stale original requester cannot settle over a newer human draft after another tab completed recovery',async()=>harness(async h=>{
 await h.mount(h.A);await h.edit(h.A,'业务任务名称','原请求已由另窗结清');await h.edit(h.A,'检查数量','7');await h.edit(h.A,'等待结果对应对象',objectB);await h.click(h.A,'保存并打开任务');
 const original=JSON.parse(h.storage.getItem(formKey+':task-create-pending'));await h.mount(h.B);await h.click(h.B,'核对原发起请求');
 assert.equal(h.storage.getItem(formKey+':task-create-pending'),null);
 const completed=formKey+':task-create-completed:'+original.payload.request_id,marker=h.storage.getItem(completed);assert.ok(marker,'original settlement marker is durable');
 await h.click(h.B,'发起业务任务');await h.edit(h.B,'业务任务名称','另一窗口正在填写的新任务');
 h.activate(h.B);await act(async()=>h.field(h.B,'任务对象 一号样地').click());await h.settle();
 await h.edit(h.B,'检查数量','33');await h.edit(h.B,'等待结果对应对象',objectA);
 const newer=h.storage.getItem(formKey),draft=JSON.parse(newer);assert.equal(draft.title,'另一窗口正在填写的新任务');assert.equal(draft.graphInputDraft.values.count.raw,'33');
 // The original page has not received the queued cross-window notification.
 // Its real old recovery action must fail closed or read history, never clear
 // the new page's unsubmitted work. Web Locks remain genuinely shared.
 await h.click(h.A,'核对原发起请求');assert.equal(h.storage.getItem(formKey),newer);assert.equal(h.storage.getItem(completed),marker);
 assert.equal(h.storage.getItem(formKey+':task-create-pending'),null);assert.equal(h.field(h.B,'业务任务名称').value,'另一窗口正在填写的新任务');assert.equal(h.field(h.B,'检查数量').value,'33');
 assert.equal(h.calls.filter(v=>v.path==='/api/workspace/instances/'+instanceId+'/tasks'&&v.method==='POST').length,1);
 assert.ok(h.storage.getItem(formKey+':task-create-outbox:'+original.payload.request_id));
}));

for(const raw of ['-0.0','1.0','1e21'])test('V132 mounted Task form preserves original numeric request and settles after remount '+raw,async()=>harness(async h=>{
 await h.mount(h.A);await h.edit(h.A,'检查数量',raw);await h.edit(h.A,'等待结果对应对象',objectB);await h.click(h.A,'保存并打开任务');
 const original=h.storage.getItem(formKey+':task-create-pending'),pending=JSON.parse(original);assert(pending.exact_body.includes('"count":'+raw));
 await h.unmount(h.A);await h.mount(h.A);assert.equal(h.field(h.A,'检查数量').value,raw);await h.click(h.A,'核对原发起请求');
 assert.equal(h.storage.getItem(formKey+':task-create-pending'),null);const history=JSON.parse(h.storage.getItem(formKey+':creation-history:'+pending.payload.request_id));assert.equal(history.exact_body,pending.exact_body);assert(h.A.container.textContent.includes('任务流程已保存'));
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path.endsWith('/tasks')).length,1);
},{numericRaw:raw}));
for(const raw of ['-0.0','1.0','1e21'])test('V132 mounted object fields retain raw input and cold original registration '+raw,async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'登记对象');await h.edit(h.A,'对象字段 对象名称','公开合成登记');await h.edit(h.A,'对象字段 观测数值',raw);await h.edit(h.A,'对象字段 登记数量','1.0');
 assert.equal(h.field(h.A,'对象字段 观测数值').type,'text');await h.click(h.A,'保存对象');const original=h.storage.getItem(formKey+':object-create-v132'),pending=JSON.parse(original);assert(pending.exact_body.includes('"reading":'+raw));assert(pending.exact_body.includes('"quantity":1'));
 await h.unmount(h.A);await h.mount(h.A);assert.equal(h.field(h.A,'对象字段 观测数值').value,raw);await h.click(h.A,'核对原登记');assert.equal(h.storage.getItem(formKey+':object-create-v132'),null);
 const history=JSON.parse(h.storage.getItem(formKey+':object-request-receipt:'+pending.body.request_id));assert.equal(history.action.exact_body,pending.exact_body);assert(h.A.container.textContent.includes('原对象登记已从服务器读回'));
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/api/instances/'+instanceId+'/objects').length,1);
},{objectRaw:raw}));
test('V132 actual object incomplete/unsafe inputs and failed save keep the draft and never POST',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'登记对象');await h.edit(h.A,'对象字段 对象名称','公开合成登记');
 for(const raw of ['-','9007199254740993']){await h.edit(h.A,'对象字段 观测数值',raw);await h.click(h.A,'保存对象');assert.equal(h.field(h.A,'对象字段 观测数值').value,raw);}
 h.state.failWrites=true;await h.edit(h.A,'对象字段 观测数值','-0.0');await h.click(h.A,'保存对象');assert.equal(h.field(h.A,'对象字段 观测数值').value,'-0.0');assert.equal(await h.guard(h.A),false);
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/api/instances/'+instanceId+'/objects').length,0);h.state.failWrites=false;assert.equal(await h.guard(h.A),true);
}));

test('V132 two actual windows can recover an earlier object draft without replacing the other window or sending',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'登记对象');await h.edit(h.A,'对象字段 对象名称','第一窗口未完对象');await h.edit(h.A,'对象字段 观测数值','-');
 await h.mount(h.B);await h.edit(h.B,'对象字段 对象名称','第二窗口继续输入');await h.edit(h.B,'对象字段 观测数值','1e21');
 const bRecords=Array.from({length:h.storage.length},(_,i)=>h.storage.key(i)).filter(key=>key.startsWith(formKey+':task-form-journal:')).map(key=>[key,h.storage.getItem(key)]).filter(([,raw])=>JSON.parse(raw).form.attributes?.name==='第二窗口继续输入');assert(bRecords.length);
 await h.unmount(h.A);await h.mount(h.A);assert.equal(h.field(h.A,'对象字段 对象名称').value,'第二窗口继续输入');await h.click(h.A,'查看本机对象草稿');
 const row=[...h.A.container.querySelectorAll('article')].find(item=>item.textContent.includes('第一窗口未完对象'));assert(row);const restore=[...row.querySelectorAll('button')].find(item=>item.textContent.trim()==='恢复这份对象草稿');assert(restore&&!restore.disabled);h.activate(h.A);await act(async()=>restore.click());await h.settle();
 assert.equal(h.field(h.A,'对象字段 对象名称').value,'第一窗口未完对象');assert.equal(h.field(h.A,'对象字段 观测数值').value,'-');for(const [key,raw]of bRecords)assert.equal(h.storage.getItem(key),raw);assert.equal(h.field(h.B,'对象字段 对象名称').value,'第二窗口继续输入');
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/api/instances/'+instanceId+'/objects').length,0);await h.unmount(h.A);await h.mount(h.A);assert.equal(h.field(h.A,'对象字段 观测数值').value,'-');
}));
test('V132 unknown original object prevents restoring a different object draft',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'登记对象');await h.edit(h.A,'对象字段 对象名称','公开合成登记');await h.edit(h.A,'对象字段 观测数值','-0.0');await h.click(h.A,'保存对象');const original=h.storage.getItem(formKey+':object-create-v132');assert(original);
 await h.click(h.A,'查看本机对象草稿');const buttons=[...h.A.container.querySelectorAll('button')].filter(item=>item.textContent.trim()==='恢复这份对象草稿');assert(buttons.length);assert(buttons.every(item=>item.disabled));assert.equal(h.storage.getItem(formKey+':object-create-v132'),original);
}));
test('V132 object late POST lookup after cookie identity change cannot settle or clear its original',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'登记对象');await h.edit(h.A,'对象字段 对象名称','公开合成登记');await h.edit(h.A,'对象字段 观测数值','-0.0');await h.click(h.A,'保存对象');const original=h.storage.getItem(formKey+':object-create-v132');assert(original);
 h.state.switchAfterObjectLookup=true;await h.click(h.A,'核对原登记');assert.equal(h.storage.getItem(formKey+':object-create-v132'),original);assert.equal(h.storage.getItem(formKey+':object-request-receipt:'+JSON.parse(original).body.request_id),null);
 h.state.switchAfterObjectLookup=false;h.state.me=null;await h.click(h.A,'核对原登记');assert.equal(h.storage.getItem(formKey+':object-create-v132'),null);assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/api/instances/'+instanceId+'/objects').length,1);
}));

test('V347 video candidate preserves existing task draft and restores its exact source selection after reload',async()=>harness(async h=>{
 await h.mount(h.A);assert.match(h.A.container.textContent,/这是已保存的视频候选线索/);
 assert.match(h.A.container.textContent,/拍摄时间尚未校准/);assert.match(h.A.container.textContent,/原视频暂不可读/);
 await h.click(h.A,'核对线索并发起业务');assert.match(h.A.container.textContent,/本次任务关联已保存的视频线索/);
 const saved=JSON.parse(h.storage.getItem(formKey));assert.deepEqual(saved.videoHandoff,{source_task_id:taskId,source_task_version:4,basis_hash:H,release_id:releaseId,business_id:businessId,entity_id:objectA});
 assert.deepEqual(saved.selectedObjects,[objectA]);
 const journals=Array.from({length:h.storage.length},(_,i)=>h.storage.key(i)).filter(key=>key.startsWith(formKey+':task-form-journal:')).map(key=>JSON.parse(h.storage.getItem(key)));
 assert.ok(journals.some(row=>row.form.title==='初始任务草稿'&&row.form.selectedObjects.length===2));
 await h.unmount(h.A);await h.mount(h.A);assert.match(h.A.container.textContent,/本次任务关联已保存的视频线索/);
 assert.deepEqual(JSON.parse(h.storage.getItem(formKey)).videoHandoff,saved.videoHandoff);assert.equal(h.calls.some(call=>call.method==='POST'),false);
} ,{videoCandidate:true}));

test('V347 video handoff survives a lost task response and recovers only its exact original request',async()=>harness(async h=>{
 await h.mount(h.A);await h.click(h.A,'核对线索并发起业务');await h.edit(h.A,'检查数量','7');await h.edit(h.A,'等待结果对应对象',objectA);
 await h.click(h.A,'保存并打开任务');const pointer=formKey+':task-create-pending',raw=h.storage.getItem(pointer),record=JSON.parse(raw);
 assert.deepEqual(record.payload.video_handoff,{source_task_id:taskId,source_task_version:4,basis_hash:H});assert.equal(record.exact_body,h.state.postBody);
 await h.unmount(h.A);await h.mount(h.A);assert.equal(h.storage.getItem(pointer),raw);
 await h.click(h.A,'核对原发起请求');assert.equal(h.storage.getItem(pointer),null);
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path==='/api/workspace/instances/'+instanceId+'/tasks').length,1);
 assert.ok(h.calls.filter(call=>call.path.endsWith('/task-requests/lookup')).every(call=>call.body===record.exact_body));
 assert.deepEqual(JSON.parse(h.storage.getItem(formKey+':creation-history:'+record.payload.request_id)).payload.video_handoff,record.payload.video_handoff);
},{videoCandidate:true}));

test('V347 a dismissed candidate remains readable with an explicit warning',async()=>harness(async h=>{
 await h.mount(h.A);assert.match(h.A.container.textContent,/此视频线索已被核查排除/);
 assert.match(h.A.container.textContent,/原任务和画面保留/);assert.equal(h.calls.some(call=>call.method==='POST'),false);
},{videoCandidate:true,videoDismissed:true}));

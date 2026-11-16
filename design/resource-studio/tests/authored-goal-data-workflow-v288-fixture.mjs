// Synthetic transport/storage only. This fixture proves no PG, published model,
// worker, independent ACK, Goal evaluation or cloud execution behavior.
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {Memory} from './authored-decision-workflow-v286-fixture.mjs';
import {GOAL_DATA_BASE,OPERATIONS,OPERATION_LABELS,ACTIONS,SCHEMAS,canonical,goalDataScopeKey,goalDataReadbackFromSaved} from '../src/authored-goal-data-workflow-v288-protocol.mjs';
export const id=n=>'28800000-0000-4000-8000-'+String(n).padStart(12,'0');
export const copy=v=>structuredClone(v),sha=v=>createHash('sha256').update(v).digest('hex');
let Controller;
export async function initializeGoalDataController(){
 assert.ok(globalThis.sessionStorage&&globalThis.window?.document);
 ({AuthoredGoalDataWorkflowControllerV288:Controller}=await import('../src/authored-goal-data-workflow-v288-controller.mjs'));
}
export function goalDataFixture(){
 const account={user_id:id(1),workspace_id:id(2),username:'编制人',role:'admin'};
 const scope={actor_id:account.user_id,workspace_id:account.workspace_id,business_name:'目标资料准备与业务建议'};
 const storage=new Memory(),session=new Memory(),key=goalDataScopeKey(scope),csrf='synthetic-goal-data-csrf';
 for(const[k,v]of Object.entries({'ga-setup-csrf':csrf,'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(k,v);
 const locks={request:async(_key,_options,fn)=>fn()};
 const people=[{user_id:account.user_id,username:account.username,membership_role:'admin'},{user_id:id(3),username:'原人工负责人',membership_role:'operator'},{user_id:id(4),username:'资料与建议核对人',membership_role:'operator'}];
 const role='原业务核对岗',toolRoles=Object.fromEntries(OPERATIONS.map(op=>[op,OPERATION_LABELS[op]+'执行人（'+scope.business_name+'·V288）'])),reviewRole='资料与业务建议核对人（'+scope.business_name+'·V288）';
 const flow={trigger:'人工发起',output:'保存原业务核对意见',steps:[{id:'original_step',name:'办理原业务核对',role,output:'原业务意见',agent:'人工确认',nextId:'end',elseId:'end',condition:''}]};
 const empty={type:'object',properties:{},required:[],additionalProperties:false};
 const adapters={context_reader:'builtin',local_prepare:'workflow_service',resense:'workflow_service',evaluation_packet:'workflow_service',packet:'runtime_document_packet',reasoning:'business_goal_ready_v288'};
 const caps=OPERATIONS.map((operation,index)=>({operation,id:id(10+index),name:OPERATION_LABELS[operation]+'登记能力',hash:String(index+1).repeat(64),release_version:'1.0.0',adapter:adapters[operation],input_schema:copy(empty),output_schema:copy(empty)}));
 const fields=[{object_key:'object_state',object_name:'监测对象',field:'field_temperature',label:'温度',value_type:'number',unit:'Cel'}];
 const candidateCaps=[{id:caps[2].id,name:caps[2].name,hash:caps[2].hash,release_version:'1.0.0',requested_observation:true,output_schema:copy(empty)}];
 const state={revision:7,origin:null,caps:copy(caps),candidateCaps:copy(candidateCaps),identity:copy(account),commands:new Map(),adoptions:[],posts:[],gets:[],previews:[],lose:false,unknown:false,reject:false,readbackError:false,next:100};
 const context=()=>({schema:'authored-goal-data-workflow-context-v288',configuration_revision:state.revision,configuration_hash:sha('configuration-'+state.revision),business_name:scope.business_name,flow:copy(flow),human_roles:[role],tool_roles:copy(toolRoles),review_role:reviewRole,prior_origin:copy(state.origin),members:copy(people),capabilities:copy(state.caps),candidate_capabilities:copy(state.candidateCaps),target_fields:copy(fields),target_fields_issue:null,target_fields_source:'saved_configuration',observation_extractors:[],adoptions:copy(state.adoptions),preparation_policy_schema:{type:'object',properties:{schema:{const:'goal-data-preparation-policy-v288'}}},node_actions:copy(ACTIONS),external_node_ids:['reasoning'],simulation_only:true,decision_intent:'information_acquisition',can_preview:true,reason:null,can_execute:false,cloud_authorized:false});
 const preview=body=>{
  const bindings=Object.fromEntries(Object.entries(body.role_bindings).map(([name,actor])=>[name,copy(people.find(p=>p.user_id===actor))]));
  for(const op of OPERATIONS)bindings[toolRoles[op]]=copy(people.find(p=>p.user_id===body.tool_bindings[op].actor_id));
  bindings[reviewRole]=copy(people.find(p=>p.user_id===body.reviewer_id));
  const steps=[...OPERATIONS.map(op=>({node_id:op,kind:'ability',name:OPERATION_LABELS[op],actor_id:body.tool_bindings[op].actor_id})),...['review_data','review_decision'].map(node_id=>({node_id,kind:'human',name:node_id==='review_data'?'核对资料准备':'核对业务建议',actor_id:body.reviewer_id})),{node_id:'human_original',kind:'human',name:flow.steps[0].name,actor_id:body.role_bindings[role]}];
  const graph={schema:'full-semantic-graph-v1',entry:'context_reader',nodes:steps.map(s=>({id:s.node_id,kind:s.kind,actor_id:s.actor_id,source_step_id:s.node_id==='human_original'?'original_step':null,role:s.node_id==='human_original'?role:OPERATIONS.includes(s.node_id)?toolRoles[s.node_id]:reviewRole}))};
  const projection={authorship:'human_authored',model_generated:false,semantic_quality_verified:false,simulation_only:true,business_name:body.business_name,trigger:flow.trigger,output:flow.output,preparation_policy:copy(body.preparation_policy),source_explanation:'资料准备和建议核对为平台新增，不替代原业务人工步骤。',steps,observation_extractors:[],conditional_resense:{data_ready:'evaluation_packet',acquire:'resense',review_required:'review_data'},uses_model:true,can_execute:false,cloud_authorized:false,physical_state_verified:false,limitations:['本测试没有证明设备、独立收件或目标完成。']};
  const binding={schema:'authored-goal-data-workflow-binding-v288',template:'authored-goal-data-before-decision-template-v288',workspace_id:scope.workspace_id,authorship:'human_authored',model_generated:false,semantic_quality_verified:false,simulation_only:true,business_name:body.business_name,configuration_revision:body.configuration_revision,configuration_hash:body.configuration_hash,tool_bindings:copy(body.tool_bindings),preparation_policy:copy(body.preparation_policy),candidate_specs:copy(body.candidate_specs),prior_origin:copy(body.prior_origin),observation_extractors:copy(body.observation_extractors),node_actions:copy(body.node_actions),external_node_ids:copy(body.external_node_ids),decision_intent:body.decision_intent,binding_hash:sha(canonical(body)),role_bindings:bindings,graph,step_mapping:[{node_id:'human_original',source_step_id:'original_step'}],capability_pins:copy(body.tool_bindings)};
  return {schema:'authored-goal-data-workflow-preview-v288',workspace_id:scope.workspace_id,actor_id:scope.actor_id,command:copy(body),binding,projection,can_execute:false,cloud_authorized:false,preview_hash:sha('preview-'+canonical(body))};
 };
 const api=async(path,options={})=>{
  assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers['X-CSRF-Token'],csrf);
  if(path==='/me')return copy(state.identity);
  const url=new URL('https://synthetic.invalid'+path),p=url.pathname;
  assert.ok(p.startsWith(GOAL_DATA_BASE+'/'),'No V286 endpoint may accept a V288 command');
  if(p===GOAL_DATA_BASE+'/context')return context();
  if(p===GOAL_DATA_BASE+'/preview'){const body=JSON.parse(options.body);state.previews.push(body);return preview(body);}
  if(p.startsWith(GOAL_DATA_BASE+'/adoptions/')){const value=state.adoptions.find(row=>row.id===p.split('/').at(-1));assert.ok(value);return copy(value);}
  if(p.startsWith(GOAL_DATA_BASE+'/requests/')){
   state.gets.push(path);const row=state.commands.get(p.split('/').at(-1));if(row)assert.equal(row.sha256,url.searchParams.get('expected_hash'));
   return {schema:'authored-goal-data-workflow-recovery-v288',found:!!row,body:row?copy(row.body):null,result:row?copy(row.result):null};
  }
  assert.equal(p,GOAL_DATA_BASE+'/adopt');
  const pending=JSON.parse(storage.getItem(key+':pending'));
  assert.equal(pending.raw,options.body);assert.equal(pending.sha256,sha(options.body));
  assert.equal(storage.getItem(key+':original:'+pending.body.request_id),JSON.stringify({...pending,rejected:null}));
  state.posts.push(options.body);
  if(state.unknown)throw Error('原请求回复未知');
  if(state.reject)throw Object.assign(Error('当前配置已变化'),{status:409});
  const body=JSON.parse(options.body),existing=state.commands.get(body.request_id);
  if(existing){assert.equal(existing.sha256,sha(options.body));return copy(existing.result);}
  const {request_id,preview_hash,consent,...base}=body;base.schema=SCHEMAS.previewCommand;
  const rendered=preview(base);assert.equal(preview_hash,rendered.preview_hash);assert.equal(consent,true);
  const prior=copy(state.origin),origin={schema:'authored-workflow-origin-v208',adoption_id:id(state.next++),business_name:scope.business_name,binding_hash:rendered.binding.binding_hash,command_hash:sha(options.body)};
  state.origin=origin;state.revision++;
  const result={schema:SCHEMAS.result,id:origin.adoption_id,request_id,business_name:scope.business_name,origin,accepted_revision:state.revision,configuration_hash:sha('configuration-'+state.revision),binding_hash:origin.binding_hash,can_execute:false,cloud_authorized:false};
  state.commands.set(request_id,{body:copy(body),result:copy(result),sha256:sha(options.body)});
  state.adoptions.push({schema:'authored-goal-data-workflow-adoption-view-v288',id:result.id,business_name:scope.business_name,accepted_revision:state.revision,ordinal:state.adoptions.length+1,prior_origin:prior,origin:copy(origin),binding_hash:origin.binding_hash,projection:rendered.projection,graph:rendered.binding.graph,role_bindings:rendered.binding.role_bindings,can_execute:false,cloud_authorized:false});
  if(state.lose)throw Error('服务器保存完成但回复丢失');return copy(result);
 };
 const readback=result=>{
  if(state.readbackError)throw Error('页面配置暂未读回');
  return goalDataReadbackFromSaved({revision:state.revision,configuration:{businesses:[scope.business_name],authoredWorkflowGraphs:{[scope.business_name]:copy(state.origin)}}},result,scope);
 },adopted=[];
 const make=extra=>new Controller({businessName:scope.business_name,account,request:api,storage,session,locks,crypto:webcrypto,sha:async bytes=>sha(bytes),onAdopted:async result=>{const proof=readback(result);adopted.push(result);return proof;},...extra});
 async function choose(c){
  for(const cap of caps){c.selectTool(cap.operation,cap.id);c.bindTool(cap.operation,account.user_id);}
  c.bindRole(role,id(3));c.bindReviewer(id(4));c.addCandidate();const ref=c.draft.candidates[0].candidate_ref;
  c.editCandidate(ref,{proposal:'使用已登记补采能力申请目标资料，比较后由原业务负责人办理。'});
  c.selectCandidateCapability(ref,caps[2].id);c.selectCandidateDelivery(ref,'requested_observation');
  c.toggleCandidatePort(ref,{field:fields[0].field,pointer:'/requested_observation/'+fields[0].field},true);
  c.update({actions_confirmed:true});
  await c.setPreparationPolicy({time_basis:'rolling_latest',allow_acquisition:true,max_age_seconds:3600});
 }
 async function ready(c=make()){await c.start();await choose(c);await c.buildPreview();return c;}
 return {account,scope,key,storage,session,locks,state,people,role,toolRoles,reviewRole,caps,fields,candidateCaps,flow,context,preview,api,make,choose,ready,readback,adopted};
}

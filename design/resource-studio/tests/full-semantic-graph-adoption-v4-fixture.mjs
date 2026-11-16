// Synthetic transport, not a DB/authority substitute. Root owns actual journeys.
import {graphFixture} from './full-semantic-graph-adoption-fixture.mjs';
import {FullSemanticGraphAdoptionController,graphAdoptionCommandHash} from '../src/full-semantic-graph-adoption-controller.mjs';
import {graphWireCanonical,graphWireDigest,verifyGraphWire} from '../src/full-semantic-graph-wire.mjs';
import {addControl,connectNodes,updateGraphDraft,selectGraphEventSource,addWaitEntityInput} from '../src/full-semantic-graph-editor-model.mjs';

const wireSchema='report-full-semantic-graph-canonical-wire-v1';
const copy=value=>JSON.parse(JSON.stringify(value));
const typed=canonical=>verifyGraphWire({schema:wireSchema,canonical,value:JSON.parse(canonical)});
const wrap=value=>{const canonical=graphWireCanonical(value);return {schema:wireSchema,canonical,value:JSON.parse(canonical)};};
const seal=async(value,key)=>typed(graphWireCanonical({...value,[key]:await graphWireDigest(value)}));
export const waitPolicy={schema:'graph-wait-source-policy-v1',arrival_basis:'durable_admission',sequence:{open:'exclusive',closed_cut:'inclusive',order:'ascending'},deadline:{clock:'database_clock_timestamp',boundary:'exclusive',basis:'admission_decision_time'},transition:'buffer_complete_batch_before_advance',clock_regression:'block',result_scope:'candidate_only',source_time_verified:false,physical_state_verified:false,revocation:{new_admission:'block',new_execution:'block',history:'preserve',draft:'preserve',cancel:'preserve'}};

export async function sourceAdoptionFixture(){
 const old=await graphFixture(),baseline=await old.open(),scope=old.scope,H='b'.repeat(64),producerId=crypto.randomUUID();
 const initial=baseline.context;baseline.dispose();old.saved.configuration.fullSemanticDesigns=[initial.design];
 const result=typed('{"additionalProperties":false,"properties":{"reading":{"minimum":1.0,"multipleOf":1e-05,"title":"来源读数","type":"number"}},"required":["reading"],"type":"object"}');
 const identity={type:'string',format:'uuid'},required=['schema','adapter_id','observation_id','entity_id','source_id','extractor_id','invocation_id','observed_at','candidate_only','result','source_time_verified','physical_state_verified'].sort();
 const output={type:'object',additionalProperties:false,required,$defs:{extractor_result:result},properties:{schema:{const:'graph-event-observation-output-v1'},adapter_id:{const:'processed-observation-v1'},...Object.fromEntries(['observation_id','entity_id','source_id','extractor_id','invocation_id'].map(k=>[k,identity])),observed_at:{type:['string','null'],format:'date-time'},candidate_only:{type:'boolean'},result:{$ref:'#/$defs/extractor_result'},source_time_verified:{const:false},physical_state_verified:{const:false}}};
 const contract=await seal({schema:'graph-event-observation-source-contract-v1',adapter_id:'processed-observation-v1',event_kind:'observation.data',correlation_kind:'entity_id',source_id:crypto.randomUUID(),source_hash:H,source_release_version:'1.0.0',extractor_id:crypto.randomUUID(),extractor_hash:H,extractor_release_version:'2.0.0',result_schema:result,output_schema:output,output_schema_hash:await graphWireDigest(output)},'contract_hash');
 const source={producer_id:producerId,label:'已登记业务观测结果',status:'active',contract_hash:await graphWireDigest(contract),contract,available:true,reason:null};
 const context={...initial,schema:'report-full-semantic-graph-editor-context-v2',event_wait:{available:true,reason:null,policy:waitPolicy,sources:[source]},adoptions:[]};
 const mode={},calls=[],requests=new Map(),base='/setup/full-semantic-graph-wire/'+scope.handoff_id+'/v4';
 async function preview(body){
  const v3=await seal({schema:'report-full-semantic-graph-binding-v3',handoff_id:scope.handoff_id,design_hash:context.design.design_hash,business_name:body.business_name,candidate_index:0,business_hash:await graphWireDigest(context.design.result.businesses[0]),source_design:context.design,source_business:context.design.result.businesses[0],source_manifest:context.manifest,source_proof:old.proof,proof_id:old.proofId,manifest_hash:context.manifest.manifest_hash,proof_hash:old.proof.proof_hash,graph:body.graph,owner_bindings:body.owner_bindings,capability_pins:{},role_bindings:Object.fromEntries(Object.entries(body.role_bindings).map(([role,id])=>[role,{user_id:id,username:old.member.name,membership_role:old.member.role}])),blocked_businesses:[],semantic_mode:'workspace_graph_design',source_semantics_status:'not_certified',coverage_hash:await graphWireDigest(body.owner_bindings)},'binding_hash');
  const {binding_hash,...common}=v3,binding=await seal({...common,schema:'report-full-semantic-graph-binding-v4',base_binding_hash:binding_hash,event_source_bindings:body.event_source_bindings,event_admission_policy:body.event_admission_policy},'binding_hash');
  const head=context.adoptions.find(a=>a.is_current),revision=!!body.parent;
  return seal({schema:revision?'report-full-semantic-graph-revision-preview-v4':'report-full-semantic-graph-preview-v4',...scope,command:body,binding,external_node_ids:body.external_node_ids,semantic_mode:'workspace_workflow_design',source_semantics_status:'not_certified',consent_scope:'明确采用来源及等待范围，不认证现场事实',...(revision?{parent:body.parent,root_id:head.root_id,previous_binding_hash:head.binding.binding_hash,effect_scope:'future_releases_only'}:{})},'preview_hash');
 }
 async function api(path,options={}){
  calls.push({path,method:options.method||'GET',body:options.body});
  if(!path.startsWith(base))return old.options.api(path,options);
  const body=options.body?typed(options.body):null,revision=path.includes('/revisions/'),kind=revision?'revision':'root';
  if(path===base+'/editor-context'){if(mode.contextFailure)throw Object.assign(Error('合成当前来源读取不可用'),{status:409});return wrap(context);}
  if(path===base+'/configuration')return wrap(old.saved);
  if(path.endsWith('/command-hash'))return wrap({schema:'report-full-semantic-graph-command-hash-v1',kind:body.kind,...scope,command:body.command,command_hash:await graphAdoptionCommandHash(scope,body.kind,body.command)});
  if(path.endsWith('/preview')){if(mode.previewFailure)throw Object.assign(Error('合成来源已撤销'),{status:409});const value=await preview(body);return wrap(mode.mutatePreview?await mode.mutatePreview(value):value);}
  if(path.includes('/requests/')){const value=requests.get(path.split('/').at(-1));return wrap(value?{found:true,...value}:{schema:revision?'report-full-semantic-graph-revision-recovery-v4':'report-full-semantic-graph-recovery-v4',found:false});}
  if(path.endsWith('/adopt')){
   if(mode.before)throw Error('合成发送结果未知');const prior=requests.get(body.request_id);if(prior)return wrap(prior);
   const {request_id,preview_hash,consent,semantic_mode,...p}=body;
   const command={...p,schema:revision?'report-full-semantic-graph-revision-preview-command-v4':'report-full-semantic-graph-preview-command-v4'},value=await preview(command),id=crypto.randomUUID(),command_hash=await graphAdoptionCommandHash(scope,kind,body);
   const origin={schema:'report-full-semantic-graph-origin-v4',adoption_id:id,handoff_id:scope.handoff_id,business_name:body.business_name,binding_hash:value.binding.binding_hash,command_hash};
   old.saved.revision=body.configuration_revision+1;old.saved.configuration.fullSemanticGraphBindings={[body.business_name]:origin};
   const receipt=await seal({id,...scope,request_id,command_hash,preview_hash,accepted_revision:old.saved.revision,configuration_hash:await graphWireDigest(old.saved.configuration),record_hash:H,...(revision?{schema:'report-full-semantic-graph-revision-receipt-v4',parent_id:body.parent.id,root_id:value.root_id,parent_receipt_hash:body.parent.receipt_hash,previous_binding_hash:value.previous_binding_hash}:{})},'receipt_hash');
   const reply={schema:revision?'report-full-semantic-graph-revision-result-v4':'report-full-semantic-graph-adoption-result-v4',receipt,origin,configuration:copy(old.saved),...(revision?{current_matches:true,source_semantics_status:'not_certified',effect_scope:'future_releases_only'}:{})};requests.set(request_id,reply);
   context.configuration={revision:old.saved.revision,hash:receipt.configuration_hash};context.adoptions.forEach(a=>a.is_current=false);context.adoptions.push({business_name:body.business_name,origin,receipt,binding:value.binding,root_id:value.root_id||id,parent_id:body.parent?.id||null,is_current:true,execution_scope:{node_actions:body.node_actions,external_node_ids:body.external_node_ids}});
   if(mode.after)throw Error('合成保存后响应丢失');return wrap(reply);
  }
  throw Error('Unknown v4 synthetic route '+path);
 }
 const open=async(extra={})=>{const c=new FullSemanticGraphAdoptionController({...old.options,api,contextFamily:'v4',...extra});await c.load();return c;};
 function ready(c){
  let value=addControl(old.ready(),'wait');value=updateGraphDraft(value,d=>{d.graph.edges[0].to='wait_1';d.graph.nodes.find(n=>n.id==='wait_1').timeout_seconds=60.5;});
  value=connectNodes(value,'wait_1','end_1','success');value=connectNodes(value,'wait_1','end_1','timeout');
  value=addWaitEntityInput(value,'subscription_1','本次等待的业务对象');value=selectGraphEventSource(value,c.context,'subscription_1',producerId);
  return updateGraphDraft(value,d=>{
   // This is an explicit workspace arrangement for the existing domain clause,
   // not a claim that its prose already specifies a producer or entity ID.
   const domain=d.owner_bindings.find(row=>row.owner_path==='/domain');
   domain.resolution='typed';domain.reason='本工作区明确将领域条款落实为等待已登记观测结果；由发起人从本次选定对象中明确选择关联对象。来源、期限及对象范围是本次执行安排，不认证原文或现场事实。';
   domain.clauses.find(clause=>clause.source_field==='value').targets=[{kind:'wait',id:'wait_1'},{kind:'value_ref',id:d.graph.subscriptions.find(s=>s.id==='subscription_1').correlation_ref}];
  });
 }
 return {...old,context,source,contract,mode,calls,requests,api,open,ready,base,typed,seal,preview};
}

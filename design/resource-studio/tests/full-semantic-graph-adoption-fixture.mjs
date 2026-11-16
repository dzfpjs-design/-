// Synthetic transport only. Actual server, SQL and task acceptance are separate.
import {fixture as fullFixture,memoryStorage} from './full-semantic-handoff-fixture.mjs';
import {generationHash as digest} from '../src/instance-generation.mjs';
import {copy as integerCopy} from '../src/report-evidence-reading-contract.mjs';
import {graphWireClone,graphWireCanonical,graphWireDigest,graphWireParseCanonical,graphWireTypedCopy,graphWireRequestJSON} from '../src/full-semantic-graph-wire.mjs';
import {FullSemanticGraphAdoptionController,graphAdoptionCommandHash} from '../src/full-semantic-graph-adoption-controller.mjs';
import {createGraphEditorDraft,setRoleMember,addOutputField,addControl,connectNodes,addGoal,updateGraphDraft} from '../src/full-semantic-graph-editor-model.mjs';
export async function seal(value,key){const body={...value};delete body[key];return {...body,[key]:await digest(body)};}
const integerSeal=seal;
async function typedSeal(value,key){const body={...value};delete body[key];const frozen=graphWireParseCanonical(graphWireRequestJSON(graphWireTypedCopy(body)));return graphWireParseCanonical(graphWireCanonical({...frozen,[key]:await graphWireDigest(frozen)}));}
export async function graphFixture({typedNumbers=false}={}){
 const copy=typedNumbers?graphWireClone:integerCopy,seal=typedNumbers?typedSeal:integerSeal;
 const f=await fullFixture(),scope={actor_id:f.account.user_id,workspace_id:f.account.workspace_id,handoff_id:f.preview.design.handoff_id},H='a'.repeat(64);
 let design=copy(f.preview.design);design.result.global_claims=[];design.result.questions=[];design.result.objects=[];design.result.relationships=[];
 const business=design.result.businesses[0];business.name='通用资料确认';business.steps=[{...business.steps[0],name:'核对资料',role:'核对负责人',next_index:null}];business.participants=[];business.interactions=[];business.conditions=[];business.coordination=[];
 design.business_bindings=[{candidate_index:0,name:business.name,step_ids:[crypto.randomUUID()],participant_ids:[],interaction_ids:[],condition_ids:[],coordination_ids:[]}];design=await seal(design,'design_hash');
 const source=[['/domain','domain',design.result.domain,['value']],['/businesses/0','business',business,['trigger','output']],['/businesses/0/steps/0','step',business.steps[0],['name','agent','role','purpose','output','next_index','branch_condition','else_index']]];
 const owners=await Promise.all(source.map(async([path,kind,value,fields])=>({path,kind,label:kind==='domain'?'领域':value.name,value_hash:await digest(value),ref_hashes:[],evidence_status:kind==='domain'?'context':'not_declared',pending_reasons:kind==='domain'?['context_not_source_evidence']:['no_citations','inference_reason_requires_review']})));
 const manifest=await seal({schema:'report-full-semantic-source-manifest-v1',handoff_id:scope.handoff_id,design_hash:design.design_hash,result_hash:await digest(design.result),source_hash:design.source_hash,catalog_hash:design.catalog_hash,privacy:{schema:'evidence-privacy-v1',projection_hash:H},owners,refs:[]},'manifest_hash');
 const proof=await seal({schema:'synthetic-position-proof-not-provider-evidence',handoff_id:scope.handoff_id,manifest_hash:manifest.manifest_hash},'proof_hash'),proofId=crypto.randomUUID();
 const member={id:scope.actor_id,name:'合成负责人',role:'operator'},members=[member],abilities=[],storage=memoryStorage(),tab=f.tab,calls=[],requests=new Map();
 const saved={revision:8,configuration:{version:1,fullSemanticDesigns:[design]}},context={schema:'report-full-semantic-graph-editor-context-v1',handoff_id:scope.handoff_id,active:true,design,manifest,configuration:{revision:8,hash:await digest(saved.configuration)},owners:owners.map((o,i)=>({owner_path:o.path,kind:o.kind,value_hash:o.value_hash,source_fields:source[i][3]})),proofs:[{id:proofId,proof_hash:proof.proof_hash,current_source_verified:true,reason:null}],schema_origins:{objects:[],relationships:[]},adoptions:[],event_wait:{available:false,reason:'真实事件接收来源尚未接通'}};
 const mode={before:false,after:false,afterReject:false,reject:false,mutateReply:null,previewPause:null,identity:null},base='/setup/full-semantic-graph-wire/'+scope.handoff_id;
 async function preview(body){
  const revision=!!body.parent,b=await seal({schema:'report-full-semantic-graph-binding-v3',handoff_id:scope.handoff_id,design_hash:design.design_hash,business_name:business.name,candidate_index:0,business_hash:await digest(business),source_design:design,source_business:business,source_manifest:manifest,source_proof:proof,proof_id:proofId,manifest_hash:manifest.manifest_hash,proof_hash:proof.proof_hash,graph:copy(body.graph),owner_bindings:copy(body.owner_bindings),capability_pins:{},role_bindings:Object.fromEntries(Object.entries(body.role_bindings).map(([role,id])=>[role,{user_id:id,username:member.name,membership_role:member.role}])),blocked_businesses:[],semantic_mode:'workspace_graph_design',source_semantics_status:'not_certified',coverage_hash:await digest(body.owner_bindings)},'binding_hash');
  const head=context.adoptions.find(a=>a.is_current);
  return seal({schema:revision?'report-full-semantic-graph-revision-preview-v1':'report-full-semantic-graph-preview-v3',...scope,command:copy(body),binding:b,external_node_ids:copy(body.external_node_ids),semantic_mode:'workspace_workflow_design',source_semantics_status:'not_certified',consent_scope:'本次完整安排仍须独立确认',...(revision?{parent:copy(body.parent),root_id:head.root_id,previous_binding_hash:head.binding.binding_hash,effect_scope:'future_releases_only'}:{})},'preview_hash');
 }
 async function api(path,options={}){
  calls.push({path,method:options.method||'GET',body:options.body});const body=options.body?(typedNumbers?graphWireParseCanonical(options.body):JSON.parse(options.body)):null;
  if(path==='/me')return mode.identity||{user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path==='/instances/members')return copy(members);if(path==='/workspace/ability-options')return copy(abilities);
  // Root controller unwraps a canonical envelope only on this new adapter.
  // A fixture copy has editable token metadata, not server/hash authority.
  // Rebuild its synthetic response from those exact tokens before wrapping.
  const wrap=value=>{const text=typedNumbers?graphWireRequestJSON(graphWireTypedCopy(value)):canonical(value);return {schema:'report-full-semantic-graph-canonical-wire-v1',value:typedNumbers?JSON.parse(text):copy(value),canonical:text};};
  if(path===base+'/ability-options')return wrap(abilities);
  if(path===base+'/editor-context')return wrap(context);
  if(path===base+'/configuration')return wrap(saved);
  const revision=path.startsWith(base+'/revisions/'),kind=revision?'revision':'root';
  if(path.endsWith('/command-hash'))return wrap({schema:'report-full-semantic-graph-command-hash-v1',kind:body.kind,...scope,command:copy(body.command),command_hash:await graphAdoptionCommandHash(scope,body.kind,body.command)});
  if(path.endsWith('/preview')){if(mode.previewPause)await mode.previewPause();return wrap(await preview(body));}
  if(path.includes('/requests/')){const result=requests.get(path.split('/').at(-1));return wrap(result?{found:true,...result}:{schema:revision?'report-full-semantic-graph-revision-recovery-v1':'report-full-semantic-graph-recovery-v3',found:false});}
  if(path.endsWith('/adopt')){
   if(mode.before)throw Error('合成发送前断线');if(mode.reject)throw Object.assign(Error('合成过期拒绝'),{status:409});
   const command={...copy(body),schema:revision?'report-full-semantic-graph-revision-preview-command-v1':'report-full-semantic-graph-preview-command-v3'};for(const key of ['request_id','preview_hash','consent','semantic_mode'])delete command[key];
   const p=await preview(command),id=crypto.randomUUID(),commandHash=await graphAdoptionCommandHash(scope,kind,body),origin={schema:'report-full-semantic-graph-origin-v3',adoption_id:id,handoff_id:scope.handoff_id,business_name:body.business_name,binding_hash:p.binding.binding_hash,command_hash:commandHash};
   saved.revision=body.configuration_revision+1;saved.configuration.fullSemanticGraphBindings={[body.business_name]:copy(origin)};
   const receipt=await seal({id,...scope,request_id:body.request_id,command_hash:commandHash,preview_hash:body.preview_hash,accepted_revision:saved.revision,configuration_hash:await digest(saved.configuration),record_hash:H,...(revision?{schema:'report-full-semantic-graph-revision-receipt-v1',parent_id:body.parent.id,root_id:p.root_id,parent_receipt_hash:body.parent.receipt_hash,previous_binding_hash:p.previous_binding_hash}:{})},'receipt_hash');
   const result={schema:revision?'report-full-semantic-graph-revision-result-v1':'report-full-semantic-graph-adoption-result-v3',receipt,origin,configuration:copy(saved),...(revision?{current_matches:true,source_semantics_status:'not_certified',effect_scope:'future_releases_only'}:{})};requests.set(body.request_id,copy(result));
   context.configuration={revision:saved.revision,hash:receipt.configuration_hash};context.adoptions.forEach(a=>a.is_current=false);context.adoptions.push({business_name:body.business_name,origin,receipt,binding:p.binding,root_id:p.root_id||id,parent_id:body.parent?.id||null,is_current:true,execution_scope:{node_actions:copy(body.node_actions),external_node_ids:copy(body.external_node_ids)}});
   if(mode.afterReject)throw Object.assign(Error('合成提交已保存但回执读取返回409'),{status:409});if(mode.after)throw Error('合成保存后响应丢失');return wrap(mode.mutateReply?await mode.mutateReply(copy(result)):result);
  }
  throw Error('Unknown synthetic route '+path);
 }
 const options={api,design,actorId:scope.actor_id,workspaceId:scope.workspace_id,storage,tabStorage:tab,locks:{request:async(_key,_options,fn)=>fn()},prepareMutation:async received=>{if(JSON.stringify(received)!==JSON.stringify(scope))throw Error('scope contract mismatch');return saved;}};
 const open=async extra=>{const c=new FullSemanticGraphAdoptionController({...options,...extra});await c.load();return c;};
 function ready(){let draft=createGraphEditorDraft(context,business.name);draft.proof_id=proofId;draft=setRoleMember(draft,'核对负责人',member.id);draft=addOutputField(draft,'step_1','是否核对完成','boolean');draft=addControl(draft,'end');draft=connectNodes(draft,'step_1','end_1');draft.graph.entry='step_1';draft=addGoal(draft,'本次核对完成');draft.graph.goals[0].required_nodes=['step_1'];return updateGraphDraft(draft,d=>{for(const row of d.owner_bindings){row.reason='合成工作区逐项安排，原文事实仍待核';if(row.owner_path==='/domain'){row.resolution='informational';continue;}row.resolution='typed';for(const clause of row.clauses)clause.targets=row.owner_path==='/businesses/0'?[{kind:clause.source_field==='trigger'?'node':'goal',id:clause.source_field==='trigger'?'step_1':'goal_1'}]:clause.source_field==='next_index'?[{kind:'edge',id:'edge_1'}]:['branch_condition','else_index'].includes(clause.source_field)?[]:[{kind:'node',id:'step_1'}];}});}
 return {scope,design,context,proof,proofId,members,abilities,member,storage,tab,calls,requests,mode,saved,options,open,ready,preview};
}
// Integer-only fixture canonical: this fixture does not certify Python float parity.
function canonical(v){if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';return JSON.stringify(v);}

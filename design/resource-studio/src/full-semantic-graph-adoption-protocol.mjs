// Pure transport checks. Live source permission remains a server decision.
import {fullKeys,fullUUID,fullSha} from './report-evidence-reading-contract.mjs';
import {graphWireCanonical,graphWireDigest,graphWireTypedCopy,verifyGraphWire} from './full-semantic-graph-wire.mjs';
import {typedEqual} from './typed-numbers-v132.mjs';
import {NATIVE_AUDIO_CONTRACT_V136,verifyNativeGraphSourceContractV136} from './native-graph-sources-v136.mjs';
import {NATIVE_LEARNED_GEO_CONTRACT_V141,verifyLearnedGraphSourceContractV141} from './native-learned-graph-sources-v141.mjs';

const need=v=>{if(!v)throw Error('来源合同、执行版本或原请求无法核对，原输入与记录保留。');};
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const uuid=v=>typeof v==='string'&&fullUUID.test(v),sha=v=>typeof v==='string'&&fullSha.test(v);
export const GRAPH_BINDING_KEYS='schema,handoff_id,design_hash,business_name,candidate_index,business_hash,source_design,source_business,source_manifest,source_proof,proof_id,manifest_hash,proof_hash,graph,owner_bindings,capability_pins,role_bindings,blocked_businesses,semantic_mode,source_semantics_status,coverage_hash,binding_hash';
const policy={schema:'graph-wait-source-policy-v1',arrival_basis:'durable_admission',sequence:{open:'exclusive',closed_cut:'inclusive',order:'ascending'},deadline:{clock:'database_clock_timestamp',boundary:'exclusive',basis:'admission_decision_time'},transition:'buffer_complete_batch_before_advance',clock_regression:'block',result_scope:'candidate_only',source_time_verified:false,physical_state_verified:false,revocation:{new_admission:'block',new_execution:'block',history:'preserve',draft:'preserve',cancel:'preserve'}};
const versions={
 v3:{commands:{root:'report-full-semantic-graph-command-v3',revision:'report-full-semantic-graph-revision-command-v1'},previews:{root:'report-full-semantic-graph-preview-command-v3',revision:'report-full-semantic-graph-revision-preview-command-v1'},preview:{root:'report-full-semantic-graph-preview-v3',revision:'report-full-semantic-graph-revision-preview-v1'},result:{root:'report-full-semantic-graph-adoption-result-v3',revision:'report-full-semantic-graph-revision-result-v1'},recovery:{root:'report-full-semantic-graph-recovery-v3',revision:'report-full-semantic-graph-revision-recovery-v1'},receipt:'report-full-semantic-graph-revision-receipt-v1'},
 v4:{commands:{root:'report-full-semantic-graph-command-v4',revision:'report-full-semantic-graph-revision-command-v4'},previews:{root:'report-full-semantic-graph-preview-command-v4',revision:'report-full-semantic-graph-revision-preview-command-v4'},preview:{root:'report-full-semantic-graph-preview-v4',revision:'report-full-semantic-graph-revision-preview-v4'},result:{root:'report-full-semantic-graph-adoption-result-v4',revision:'report-full-semantic-graph-revision-result-v4'},recovery:{root:'report-full-semantic-graph-recovery-v4',revision:'report-full-semantic-graph-revision-recovery-v4'},receipt:'report-full-semantic-graph-revision-receipt-v4'},
 'objects-v171':{commands:{root:'report-object-graph-command-v171',revision:'report-object-graph-revision-command-v171'},previews:{root:'report-object-graph-preview-command-v171',revision:'report-object-graph-revision-preview-command-v171'},preview:{root:'report-object-graph-preview-v171',revision:'report-object-graph-revision-preview-v171'},result:{root:'report-object-graph-adoption-result-v171',revision:'report-object-graph-revision-result-v171'},recovery:{root:'report-object-graph-recovery-v171',revision:'report-object-graph-revision-recovery-v171'},receipt:'report-object-graph-revision-receipt-v171'},
};
export function graphProtocol(family){need(Object.hasOwn(versions,family));return versions[family];}
export const objectGraphFamily='objects-v171';
export function graphBindingFamily(binding){const known={'report-full-semantic-graph-binding-v3':'v3','report-full-semantic-graph-binding-v4':'v4','report-object-graph-binding-v171':objectGraphFamily};need(Object.hasOwn(known,binding?.schema));return known[binding.schema];}
export function graphOriginFamily(origin){const known={'report-full-semantic-graph-origin-v3':'v3','report-full-semantic-graph-origin-v4':'v4','report-object-graph-origin-v171':objectGraphFamily};need(Object.hasOwn(known,origin?.schema));return known[origin.schema];}
export const graphOriginSchema=family=>{graphProtocol(family);return family===objectGraphFamily?'report-object-graph-origin-v171':'report-full-semantic-graph-origin-'+family;};
export const graphContextSchema=family=>family===objectGraphFamily?'report-object-graph-editor-context-v171':family==='v4'?'report-full-semantic-graph-editor-context-v2':family==='v3'?'report-full-semantic-graph-editor-context-v1':(need(false));
export const graphHasEvents=family=>{graphProtocol(family);return family!=='v3';};
export function graphFamily(body){for(const [family,p] of Object.entries(versions))if([...Object.values(p.commands),...Object.values(p.previews)].includes(body?.schema))return family;need(false);}
export function sameGraphWire(left,right){return graphWireCanonical(left)===graphWireCanonical(right);}
// Preview inputs can contain new numeric atoms. Semantic equality preserves
// int/float and zero sign without pretending to know a server hash spelling.
export function sameGraphTyped(left,right){try{return typedEqual(graphWireTypedCopy(left),graphWireTypedCopy(right));}catch{return false;}}
export function reviveGraphCommand(canonical,value){return verifyGraphWire({schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value});}
export function checkWaitPolicy(value){need(sameGraphWire(value,policy));return value;}
export async function verifyWaitContract(value){
 if(value?.schema===NATIVE_AUDIO_CONTRACT_V136)return verifyNativeGraphSourceContractV136(value);
 if(value?.schema===NATIVE_LEARNED_GEO_CONTRACT_V141)return verifyLearnedGraphSourceContractV141(value);
 need(fullKeys(value,'schema,adapter_id,event_kind,correlation_kind,source_id,source_hash,source_release_version,extractor_id,extractor_hash,extractor_release_version,result_schema,output_schema,output_schema_hash,contract_hash')
  &&value.schema==='graph-event-observation-source-contract-v1'&&value.correlation_kind==='entity_id'
  &&({'processed-observation-v1':'observation.data','anomaly-candidate-v1':'perception.anomaly'})[value.adapter_id]===value.event_kind
  &&uuid(value.source_id)&&uuid(value.extractor_id)&&value.source_id!==value.extractor_id&&sha(value.source_hash)&&sha(value.extractor_hash)
  &&/^\d+\.\d+\.\d+$/.test(value.source_release_version)&&/^\d+\.\d+\.\d+$/.test(value.extractor_release_version)
  &&object(value.result_schema)&&object(value.output_schema)&&sha(value.output_schema_hash)&&sha(value.contract_hash));
 need(await graphWireDigest(value.output_schema)===value.output_schema_hash&&await graphWireDigest(value,{omit:'contract_hash'})===value.contract_hash);
 return value;
}
export async function verifyWaitOptions(value){
 need(fullKeys(value,'available,reason,policy,sources')&&typeof value.available==='boolean'&&(value.reason===null||typeof value.reason==='string')&&Array.isArray(value.sources));
 checkWaitPolicy(value.policy);const seen=new Set();
 for(const source of value.sources){
  need(fullKeys(source,'producer_id,label,status,contract_hash,contract,available,reason')&&uuid(source.producer_id)&&!seen.has(source.producer_id)
   &&typeof source.label==='string'&&source.label.trim()&&['active','revoked'].includes(source.status)&&typeof source.available==='boolean'
   &&(source.available?source.status==='active'&&source.reason===null:typeof source.reason==='string'&&source.reason.trim()));
  seen.add(source.producer_id);await verifyWaitContract(source.contract);need(await graphWireDigest(source.contract)===source.contract_hash);
 }
 need(!value.available||value.sources.some(s=>s.available));return value;
}
export async function verifyWaitBindings(graph,bindings,admissionPolicy){
 checkWaitPolicy(admissionPolicy);need(object(bindings)&&Array.isArray(graph?.subscriptions));
 need(Object.keys(bindings).length===graph.subscriptions.length&&new Set(graph.subscriptions.map(s=>s.id)).size===graph.subscriptions.length);
 for(const sub of graph.subscriptions){const value=bindings[sub.id],ref=graph.references?.[sub.correlation_ref];
  need(fullKeys(value,'producer_id,contract_hash,contract,scope')&&uuid(value.producer_id)&&sha(value.contract_hash)
   &&fullKeys(value.scope,'entity_policy,correlation_ref,source_policy')&&value.scope.entity_policy==='frozen_task_entities'
   &&value.scope.correlation_ref===sub.correlation_ref&&value.scope.source_policy==='exact_registered_source'&&ref?.kind==='string');
  await verifyWaitContract(value.contract);need(await graphWireDigest(value.contract)===value.contract_hash
   &&sub.event_type===value.contract.event_kind&&sameGraphWire(sub.output_schema,value.contract.output_schema));
 }
 return bindings;
}
export async function verifyBindingFamily(binding){
 const family=graphBindingFamily(binding),events=graphHasEvents(family);
 need(fullKeys(binding,GRAPH_BINDING_KEYS+(events?',base_binding_hash,event_source_bindings,event_admission_policy':''))&&sha(binding.binding_hash));
 need(await graphWireDigest(binding,{omit:'binding_hash'})===binding.binding_hash);
 if(family===objectGraphFamily){need(binding.source_design?.schema==='report-object-semantic-design-v169'&&binding.source_manifest?.schema==='report-object-source-manifest-v170'&&binding.source_proof?.schema==='report-object-region-proof-v170'&&binding.source_proof.handoff_id===binding.handoff_id&&binding.source_proof.manifest_hash===binding.manifest_hash&&binding.source_proof.proof_hash===binding.proof_hash);need(await graphWireDigest(binding.source_proof,{omit:'proof_hash'})===binding.proof_hash);}
 else need(binding.source_design?.schema==='report-full-semantic-design-v1'&&binding.source_manifest?.schema==='report-full-semantic-source-manifest-v1');
 if(events){
  need(sha(binding.base_binding_hash));await verifyWaitBindings(binding.graph,binding.event_source_bindings,binding.event_admission_policy);
  const {binding_hash,base_binding_hash,event_source_bindings,event_admission_policy,...base}=binding;
  need(await graphWireDigest({...base,schema:family===objectGraphFamily?'report-object-graph-base-binding-v171':'report-full-semantic-graph-binding-v3'})===base_binding_hash);
 }
 return family;
}

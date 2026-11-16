/** Durable source fragments. These preserve wire spelling, never grant authority. */
import {graphWireCanonical,graphWireClone,graphWireTypedCopy,graphWireParseCanonical,graphWireNumericTokens,graphWireRestoreNumericTokens} from './full-semantic-graph-wire.mjs';
import {typedEqual,isTypedNumber} from './typed-numbers-v132.mjs';
import {NATIVE_AUDIO_CONTRACT_V136,nativeGraphSourceContractV136} from './native-graph-sources-v136.mjs';
import {NATIVE_LEARNED_GEO_CONTRACT_V141,learnedGraphSourceContractV141} from './native-learned-graph-sources-v141.mjs';

export const SOURCE_DRAFT_SCHEMA='full-semantic-graph-editor-draft-v2';
export const SOURCE_CONTEXT_SCHEMA='report-full-semantic-graph-editor-context-v2';
export const OBJECT_SOURCE_DRAFT_SCHEMA='object-graph-editor-draft-v171';
export const OBJECT_SOURCE_CONTEXT_SCHEMA='report-object-graph-editor-context-v171';
const eventFamily=context=>context?.schema===OBJECT_SOURCE_CONTEXT_SCHEMA?{draft:OBJECT_SOURCE_DRAFT_SCHEMA,binding:'report-object-graph-binding-v171'}:context?.schema===SOURCE_CONTEXT_SCHEMA?{draft:SOURCE_DRAFT_SCHEMA,binding:'report-full-semantic-graph-binding-v4'}:null;
const FRAGMENT_SCHEMA='full-semantic-graph-event-source-fragment-v1';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,HASH=/^[a-f0-9]{64}$/;
const own=(value,key)=>Object.prototype.hasOwnProperty.call(value||{},key);
const plain=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>plain(value)&&Object.keys(value).length===keys.length&&keys.every(key=>own(value,key));
const need=(ok,message='原消息来源记录不完整；已保存的安排保留，请重新核对。')=>{if(!ok)throw Error(message);};
const copy=value=>{const next=graphWireClone(value);if(next?.graph&&next.ui?.number_tokens_v132)graphWireRestoreNumericTokens(next.graph,next.ui.number_tokens_v132);return next;};
const stamp=value=>{if(value?.graph&&value.ui){const current=graphWireNumericTokens(value.graph),prior=Object.fromEntries(Object.entries(value.ui.number_tokens_v132||{}).filter(([path])=>!own(current,path)));graphWireRestoreNumericTokens(value.graph,prior,{matchingOnly:true});value.ui.number_tokens_v132=graphWireNumericTokens(value.graph);}return value;};
const same=(a,b)=>typedEqual(graphWireTypedCopy(a),graphWireTypedCopy(b));

export function restoreGraphCanonicalFragment(canonical){
 need(typeof canonical==='string');
 return graphWireParseCanonical(canonical);
}
function contract(value){
 if(value?.schema===NATIVE_AUDIO_CONTRACT_V136)return nativeGraphSourceContractV136(value);
 if(value?.schema===NATIVE_LEARNED_GEO_CONTRACT_V141)return learnedGraphSourceContractV141(value);
 need(plain(value)&&value.schema==='graph-event-observation-source-contract-v1'&&
  ['processed-observation-v1','anomaly-candidate-v1'].includes(value.adapter_id)&&
  value.event_kind===(value.adapter_id==='processed-observation-v1'?'observation.data':'perception.anomaly')&&
  value.correlation_kind==='entity_id'&&plain(value.output_schema)&&plain(value.result_schema)&&
  HASH.test(value.contract_hash||'')&&HASH.test(value.output_schema_hash||'')&&
  UUID.test(value.source_id||'')&&UUID.test(value.extractor_id||'')&&
  HASH.test(value.source_hash||'')&&HASH.test(value.extractor_hash||'')&&
  typeof value.source_release_version==='string'&&typeof value.extractor_release_version==='string');
 return value;
}
export function freezeGraphEventSource(source){
 need(plain(source)&&UUID.test(source.producer_id||'')&&HASH.test(source.contract_hash||'')&&typeof source.label==='string');
 contract(source.contract);
 // Only the immutable server contract is captured, not its mutable availability.
 return graphWireCanonical({schema:FRAGMENT_SCHEMA,source:{producer_id:source.producer_id,label:source.label,
  contract_hash:source.contract_hash,contract:source.contract}});
}
export function readGraphEventSource(value,subscriptionId){
 const fragment=restoreGraphCanonicalFragment(value?.event_source_fragments?.[subscriptionId]);
 need(exact(fragment,['schema','source'])&&fragment.schema===FRAGMENT_SCHEMA&&
  exact(fragment.source,['producer_id','label','contract_hash','contract']));
 const source=fragment.source;need(UUID.test(source.producer_id||'')&&HASH.test(source.contract_hash||'')&&typeof source.label==='string');
 contract(source.contract);return source;
}
function policy(value){
 need(plain(value)&&value.schema==='graph-wait-source-policy-v1','等待接收规则尚未读取；原稿保留。');
 return value;
}
export function withGraphEventDraft(value,context,binding=null){
 const family=eventFamily(context);need(family,'请先读取包含真实消息来源的当前设计；原稿保留。');
 need(family.draft===OBJECT_SOURCE_DRAFT_SCHEMA?value.schema===OBJECT_SOURCE_DRAFT_SCHEMA:value.schema!==OBJECT_SOURCE_DRAFT_SCHEMA,'原稿属于另一执行版本，不能重标后采用。');
 const next=copy(value);next.schema=family.draft;
 if(value.schema===family.draft&&!binding&&plain(value.event_source_fragments)&&typeof value.event_admission_policy_canonical==='string')return stamp(next);
 next.event_source_fragments={};
 next.event_admission_policy_canonical=graphWireCanonical(policy(binding?binding.event_admission_policy:context.event_wait?.policy));
 if(binding){
  need(binding.schema===family.binding&&plain(binding.event_source_bindings));
  next.ui={...next.ui,adopted_graph_canonical:graphWireCanonical(binding.graph)};
  for(const [id,pin] of Object.entries(binding.event_source_bindings)){
   const row=context.event_wait?.sources?.find(source=>source.producer_id===pin.producer_id&&source.contract_hash===pin.contract_hash);
   Object.defineProperty(next.event_source_fragments,id,{value:freezeGraphEventSource({producer_id:pin.producer_id,
    label:row?.label||'此前确认的消息来源',contract_hash:pin.contract_hash,contract:pin.contract}),enumerable:true,writable:true,configurable:true});
  }
 }
 return stamp(next);
}
export function selectGraphEventSource(value,context,subscriptionId,producerId){
 need(context?.event_wait?.available===true,'当前没有可确认的真实消息来源；原选择和填写内容保留。');
 const source=context.event_wait.sources?.find(row=>row.producer_id===producerId);
 need(source?.available===true&&source.status==='active','此来源当前不可采用；原选择和填写内容保留。');
 const canonical=freezeGraphEventSource(source),next=withGraphEventDraft(value,context),subscription=next.graph.subscriptions.find(row=>row.id===subscriptionId);
 need(subscription,'原等待安排已变化；请保留原稿后核对。');
 Object.defineProperty(next.event_source_fragments,subscriptionId,{value:canonical,enumerable:true,writable:true,configurable:true});
 subscription.event_type=source.contract.event_kind;subscription.output_schema=copy(source.contract.output_schema);
 return stamp(next);
}
export function graphEventDraftIssues(value,context){
 if(![SOURCE_DRAFT_SCHEMA,OBJECT_SOURCE_DRAFT_SCHEMA].includes(value?.schema))return [];
 const issues=[],report=(path,message)=>issues.push({path,message});
 try{
  value=copy(value);
  need(eventFamily(context)?.draft===value.schema);
  const frozenPolicy=policy(restoreGraphCanonicalFragment(value.event_admission_policy_canonical));
  need(graphWireCanonical(frozenPolicy)===graphWireCanonical(policy(context.event_wait?.policy)),
   '等待接收规则已变化；原规则保留，请明确重新核对。');
  need(plain(value.event_source_fragments));
  const subscriptions=value.graph.subscriptions;
  for(const key of Object.keys(value.event_source_fragments))if(!subscriptions.some(row=>row.id===key))report('event_source_fragments','原消息来源仍对应已移除的等待；请明确核对，不能隐藏后提交。');
  for(const subscription of subscriptions){
   const path='graph.subscriptions.'+subscription.id;
   try{
    const source=readGraphEventSource(value,subscription.id),row=context.event_wait?.sources?.find(item=>item.producer_id===source.producer_id);
    need(context.event_wait?.available===true&&row?.available===true&&row.status==='active',
     '已选消息来源当前不可用；原选择、字段和填写内容保留，请核对后明确重选。');
    need(row.contract_hash===source.contract_hash&&graphWireCanonical(row.contract)===graphWireCanonical(source.contract),
     '已选消息来源的版本或字段规则已变化；原稿保留，不会自动替换。');
    need(subscription.event_type===source.contract.event_kind&&same(subscription.output_schema,source.contract.output_schema),
     '等待接收的字段与原来源不一致；不能删减或改写来源结果。');
    const ref=value.graph.references[subscription.correlation_ref];
    need(ref?.kind==='string'&&ref.nullable===false&&ref.source==='input',
     '请选择必填文字业务输入作为关联对象；发起任务时须从本次所选对象中明确选择。');
    need((value.graph.input_schema.required||[]).includes(ref.field),
     '关联对象的业务输入必须必填，不能用空值或其他任务对象代替。');
   }catch(error){report(path,error.message);}
  }
 }catch(error){report('event_source_fragments',error.message);}
 return issues;
}
export function graphEventPreviewFields(value,context,{graph:providedGraph,editedNumericPaths=[]}={}){
 const issues=graphEventDraftIssues(value,context);need(!issues.length,issues[0]?.message);
 const original=value.ui?.adopted_graph_canonical?restoreGraphCanonicalFragment(value.ui.adopted_graph_canonical):null;
 // An unchanged published schema or expression must not become a new integer
 // merely because the editable local tree was serialized. Changed UI values
 // remain new values; this does not reuse any previous execution consent.
 const escape=key=>String(key).replaceAll('~','~0').replaceAll('/','~1');
 const retain=(current,prior,path='')=>{
  if(isTypedNumber(current))return current;
  if(prior&&typeof current==='object'&&!editedNumericPaths.some(edit=>edit===path||edit.startsWith(path+'/'))&&same(current,prior))return prior;
  if(Array.isArray(current))return current.map((child,index)=>retain(child,Array.isArray(prior)?plain(child)&&typeof child.id==='string'?prior.find(item=>item?.id===child.id):prior[index]:undefined,path+'/'+index));
  if(plain(current))return Object.fromEntries(Object.entries(current).map(([key,child])=>[key,retain(child,plain(prior)?prior[key]:undefined,path+'/'+escape(key))]));
  return current;
 };
 const graph={...retain(copy(providedGraph||value.graph),original)},bindings={};
 graph.subscriptions=graph.subscriptions.map(item=>{
  const subscription={...item};
  const source=readGraphEventSource(value,subscription.id);
  // Reinsert immutable parsed containers, retaining 1.0/-0.0 and nested schema metadata.
  subscription.output_schema=source.contract.output_schema;
  Object.defineProperty(bindings,subscription.id,{value:{producer_id:source.producer_id,contract_hash:source.contract_hash,
   contract:source.contract,scope:{entity_policy:'frozen_task_entities',correlation_ref:subscription.correlation_ref,
    source_policy:'exact_registered_source'}},enumerable:true,writable:true,configurable:true});
  return subscription;
 });
 return {graph,event_source_bindings:bindings,event_admission_policy:restoreGraphCanonicalFragment(value.event_admission_policy_canonical)};
}

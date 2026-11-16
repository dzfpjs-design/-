// Human graph adoption: durable input, exact original requests, server authority.
import {copy,same,fullKeys,fullUUID,fullSha,fullInt} from './report-evidence-reading-contract.mjs';
import {graphWireDigest as digest,graphWireCanonical,graphWireRequestJSON,graphWireFamilyPath,verifyGraphWire} from './full-semantic-graph-wire.mjs';
import {graphFamily,graphProtocol,graphOriginFamily,graphOriginSchema,graphContextSchema,graphHasEvents,objectGraphFamily,reviveGraphCommand,sameGraphWire,sameGraphTyped,verifyWaitOptions,verifyWaitBindings,verifyBindingFamily} from './full-semantic-graph-adoption-protocol.mjs';
import {scopedPreparationRequester,exactPreparationWrite} from './understanding-preparation-contract.mjs';
import {verifyFullSemanticDesign} from './full-semantic-handoff-contract.mjs';
import {verifySourceManifest} from './full-semantic-source-review-controller.mjs';
import {createGraphEditorDraft,buildGraphPreviewCommand,collectGraphEditorIssues,restoreGraphEditorDraftNumbers} from './full-semantic-graph-editor-model.mjs';
import {checkedObjectTypeV170} from './setup-object-proposals.mjs';

const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const uuid=v=>typeof v==='string'&&fullUUID.test(v),sha=v=>typeof v==='string'&&fullSha.test(v);
const need=(v,m='流程选择或保存回执无法核对，原输入与请求保留。')=>{if(!v)throw Error(m);};
const keys=fullKeys,limit=4*1024*1024;
const prefix='ga-full-semantic-graph-adoption-v115:';
const objectPrefix='ga-object-graph-adoption-v171:';
function pendingChanged(){globalThis.dispatchEvent?.(new Event('ga-full-semantic-graph-pending-change'));}
const originKeys='schema,adoption_id,handoff_id,business_name,binding_hash,command_hash';
const ownerFields={domain:['value'],question:['value'],global_claim:['kind','statement'],business:['trigger','output'],step:['name','agent','role','purpose','output','next_index','branch_condition','else_index'],participant:['agent','responsibility'],interaction:['from_participant_index','to_participant_index','purpose','condition','step_indices'],condition:['kind','statement','step_indices'],coordination:['kind','step_indices','start_rule','wait_rule','join_rule'],object:['name'],field:['label','kind','required','unit'],relationship:['name','description','source_type','target_type','cardinality']};
const objectOwnerFields={...ownerFields,business_identity:['status','field_labels','explanation'],platform_identity_confirmation:['object_name','acknowledge_platform_identity']};
function objectOwnerValues(design){
 const result=design.result,items=[['/domain','domain',result.domain]];result.global_claims.forEach((value,index)=>items.push(['/global_claims/'+index,'global_claim',value]));
 result.businesses.forEach((business,index)=>{const path='/businesses/'+index;items.push([path,'business',business]);for(const [collection,kind]of [['steps','step'],['participants','participant'],['interactions','interaction'],['conditions','condition'],['coordination','coordination']])business[collection].forEach((value,child)=>items.push([path+'/'+collection+'/'+child,kind,value]));});
 result.objects.forEach((value,index)=>{const path='/objects/'+index;items.push([path,'object',value],[path+'/business_identity','business_identity',value.business_identity]);value.fields.forEach((field,child)=>items.push([path+'/fields/'+child,'field',field]));});
 result.relationships.forEach((value,index)=>items.push(['/relationships/'+index,'relationship',value]));result.questions.forEach((value,index)=>items.push(['/questions/'+index,'question',value]));design.object_identity_confirmations.forEach((value,index)=>items.push(['/object_identity_confirmations/'+index,'platform_identity_confirmation',value]));return items;
}
async function sealed(value,key){need(object(value)&&sha(value[key]));need(value[key]===await digest(value,{omit:key}));}
function safeJSON(value){need(new TextEncoder().encode(JSON.stringify(value)).length<=limit,'本页内容超过本机单份保存范围，原输入保留；请保留页面并缩小本次采用范围。');}
function graphCommand(body,kind){
 const family=graphFamily(body),protocol=graphProtocol(family);
 const common='schema,configuration_revision,configuration_hash,proof_id,business_name,graph,owner_bindings,role_bindings,external_node_ids,node_actions';
 need(keys(body,common+(graphHasEvents(family)?',event_source_bindings,event_admission_policy':'')+(kind==='revision'?',parent':'')+',request_id,preview_hash,consent,semantic_mode')&&body.schema===protocol.commands[kind]
  &&fullInt(body.configuration_revision,1)&&sha(body.configuration_hash)&&uuid(body.proof_id)&&typeof body.business_name==='string'&&body.business_name.trim()
  &&object(body.graph)&&Array.isArray(body.owner_bindings)&&object(body.role_bindings)&&Array.isArray(body.external_node_ids)&&object(body.node_actions)
  &&uuid(body.request_id)&&sha(body.preview_hash)&&body.consent===true&&body.semantic_mode==='workspace_workflow_design');
 if(graphHasEvents(family))need(object(body.event_source_bindings)&&object(body.event_admission_policy));
 if(kind==='revision')need(keys(body.parent,'id,receipt_hash')&&uuid(body.parent.id)&&sha(body.parent.receipt_hash));
}
export async function graphAdoptionCommandHash(scope,kind,body){
 need(['root','revision'].includes(kind));graphCommand(body,kind);
 return digest({...(kind==='revision'?{schema:graphFamily(body)===objectGraphFamily?'report-object-graph-revision-command-hash-v171':graphFamily(body)==='v4'?'report-full-semantic-graph-revision-command-hash-v4':'report-full-semantic-graph-revision-command-hash-v1'}:{}),workspace_id:scope.workspace_id,actor_id:scope.actor_id,handoff_id:scope.handoff_id,command:body});
}
export async function verifyGraphEditorContext(value,scope){
 const objects=value?.schema===graphContextSchema(objectGraphFamily),events=objects||value?.schema===graphContextSchema('v4');
 need(keys(value,'schema,handoff_id,active,design,manifest,configuration,owners,proofs,schema_origins,adoptions,event_wait')&&[graphContextSchema('v3'),graphContextSchema('v4'),graphContextSchema(objectGraphFamily)].includes(value.schema)
  &&value.handoff_id===scope.handoff_id&&typeof value.active==='boolean');
 await verifyFullSemanticDesign(value.design);await verifySourceManifest(value.manifest,scope.handoff_id,{designSchema:value.design.schema});
 need(value.design.schema===(objects?'report-object-semantic-design-v169':'report-full-semantic-design-v1')&&value.manifest.schema===(objects?'report-object-source-manifest-v170':'report-full-semantic-source-manifest-v1'));
 need(value.design.handoff_id===scope.handoff_id&&value.manifest.design_hash===value.design.design_hash
  &&keys(value.configuration,'revision,hash')&&fullInt(value.configuration.revision,1)&&sha(value.configuration.hash)
  &&Array.isArray(value.owners)&&Array.isArray(value.proofs)&&Array.isArray(value.adoptions)&&keys(value.schema_origins,'objects,relationships'));
 if(events){await verifyWaitOptions(value.event_wait);need(!value.event_wait.available||value.active);}
 else need(keys(value.event_wait,'available,reason')&&value.event_wait.available===false&&typeof value.event_wait.reason==='string');
 need(value.owners.length===value.manifest.owners.length&&new Set(value.owners.map(o=>o.owner_path)).size===value.owners.length);
 if(objects){const expected=objectOwnerValues(value.design);need(expected.length===value.owners.length&&value.manifest.result_hash===await digest(value.design.result)&&value.manifest.source_hash===value.design.source_hash&&value.manifest.catalog_hash===value.design.catalog_hash);for(const [index,[path,kind,raw]]of expected.entries()){const owner=value.owners[index],manifest=value.manifest.owners[index];need(owner.owner_path===path&&owner.kind===kind&&owner.value_hash===await digest(raw)&&manifest.path===path&&manifest.kind===kind&&manifest.value_hash===owner.value_hash);}}
 for(const owner of value.owners){const source=value.manifest.owners.find(o=>o.path===owner.owner_path);need(source&&keys(owner,'owner_path,kind,value_hash,source_fields')&&owner.kind===source.kind&&owner.value_hash===source.value_hash&&same(owner.source_fields,(objects?objectOwnerFields:ownerFields)[owner.kind]));}
 for(const proof of value.proofs)need(keys(proof,'id,proof_hash,current_source_verified,reason')&&uuid(proof.id)&&sha(proof.proof_hash)&&typeof proof.current_source_verified==='boolean'&&(proof.reason===null||typeof proof.reason==='string')&&(!proof.current_source_verified||value.active));
 need(new Set(value.proofs.map(p=>p.id)).size===value.proofs.length);
 for(const collection of ['objects','relationships']){
  need(Array.isArray(value.schema_origins[collection]));
  for(const item of value.schema_origins[collection]){need(keys(item,'name,origin,definition')&&typeof item.name==='string'&&item.name===item.definition?.name&&object(item.origin)&&item.origin.name===item.name&&uuid(item.origin.adoption_id));if(objects){need(item.origin.schema==='report-object-schema-origin-v170'&&item.origin.kind===(collection==='objects'?'object':'relationship'));if(collection==='objects')checkedObjectTypeV170(item.definition);}}
 }
 const ids=new Set(),heads=new Set();
 for(const adopted of value.adoptions){
  const family=await verifyBindingFamily(adopted.binding);
  need(keys(adopted,'business_name,origin,receipt,binding,root_id,parent_id,is_current,execution_scope')&&keys(adopted.origin,originKeys)
   &&adopted.origin.schema===graphOriginSchema(family)&&uuid(adopted.origin.adoption_id)&&uuid(adopted.root_id)&&(adopted.parent_id===null||uuid(adopted.parent_id))
   &&typeof adopted.is_current==='boolean'&&!ids.has(adopted.origin.adoption_id)&&adopted.business_name===adopted.origin.business_name&&adopted.binding.business_name===adopted.business_name
   &&adopted.origin.binding_hash===adopted.binding.binding_hash&&adopted.receipt.id===adopted.origin.adoption_id&&keys(adopted.execution_scope,'node_actions,external_node_ids'));
  await sealed(adopted.binding,'binding_hash');await sealed(adopted.receipt,'receipt_hash');
  const r=adopted.receipt,b=adopted.binding;
  need(keys(r,'id,workspace_id,actor_id,handoff_id,request_id,command_hash,preview_hash,accepted_revision,configuration_hash,record_hash,receipt_hash'+(adopted.parent_id!==null?',schema,parent_id,root_id,parent_receipt_hash,previous_binding_hash':''))
   &&uuid(r.actor_id)&&uuid(r.request_id)&&fullInt(r.accepted_revision,1)&&r.accepted_revision<=value.configuration.revision
   &&sha(r.configuration_hash)&&sha(r.record_hash)&&sha(r.preview_hash)&&sha(r.command_hash)
   &&r.workspace_id===scope.workspace_id&&r.handoff_id===adopted.origin.handoff_id&&r.command_hash===adopted.origin.command_hash
   &&b.handoff_id===adopted.origin.handoff_id&&b.source_design.handoff_id===b.handoff_id
   &&b.source_manifest.handoff_id===b.handoff_id&&b.source_proof.handoff_id===b.handoff_id);
  if(adopted.is_current){need(!heads.has(adopted.business_name));heads.add(adopted.business_name);}ids.add(adopted.origin.adoption_id);
 }
 for(const adopted of value.adoptions){
  let current=adopted;const seen=new Set();
  while(current.parent_id!==null){need(!seen.has(current.origin.adoption_id)&&seen.size<257);seen.add(current.origin.adoption_id);
   const parent=value.adoptions.find(a=>a.origin.adoption_id===current.parent_id);
   need(parent&&parent.root_id===adopted.root_id&&parent.business_name===adopted.business_name
    &&current.receipt.parent_id===parent.origin.adoption_id&&current.receipt.parent_receipt_hash===parent.receipt.receipt_hash
    &&current.receipt.previous_binding_hash===parent.binding.binding_hash&&current.receipt.root_id===adopted.root_id
    &&current.receipt.schema===graphProtocol(graphOriginFamily(current.origin)).receipt
    &&current.receipt.accepted_revision>parent.receipt.accepted_revision
    &&!(graphOriginFamily(parent.origin)==='v4'&&graphOriginFamily(current.origin)==='v3')
    &&((graphOriginFamily(parent.origin)===objectGraphFamily)===(graphOriginFamily(current.origin)===objectGraphFamily)));
   current=parent;
  }
  need(current.origin.adoption_id===adopted.root_id);
 }
 return value;
}
export async function verifyGraphAdoptionPreview(value,command,kind,{scope,context,members}){
 const family=graphFamily(command),protocol=graphProtocol(family);
 const common='schema,workspace_id,actor_id,handoff_id,command,binding,external_node_ids,semantic_mode,source_semantics_status,consent_scope,preview_hash';
 need(keys(value,common+(kind==='revision'?',parent,root_id,previous_binding_hash,effect_scope':''))&&value.schema===protocol.preview[kind]
  &&value.workspace_id===scope.workspace_id&&value.actor_id===scope.actor_id&&value.handoff_id===scope.handoff_id&&sameGraphTyped(value.command,command)
  &&value.semantic_mode==='workspace_workflow_design'&&value.source_semantics_status==='not_certified'&&same(value.external_node_ids,command.external_node_ids));
 const binding=value.binding;
 need(await verifyBindingFamily(binding)===family&&binding.handoff_id===scope.handoff_id&&binding.business_name===command.business_name&&binding.proof_id===command.proof_id
  &&sameGraphWire(binding.source_design,context.design)&&fullInt(binding.candidate_index)&&sameGraphWire(binding.source_business,context.design.result.businesses[binding.candidate_index])&&binding.business_name===binding.source_business.name&&binding.business_hash===await digest(binding.source_business)&&sameGraphWire(binding.source_manifest,context.manifest)&&sameGraphTyped(binding.graph,command.graph)&&sameGraphTyped(binding.owner_bindings,command.owner_bindings)
  &&binding.manifest_hash===context.manifest.manifest_hash&&binding.design_hash===context.design.design_hash
  &&binding.proof_hash===context.proofs.find(p=>p.id===command.proof_id)?.proof_hash&&!binding.blocked_businesses.includes(command.business_name)
  &&binding.semantic_mode==='workspace_graph_design'&&binding.source_semantics_status==='not_certified'
  &&sameGraphTyped(binding.capability_pins,Object.fromEntries(command.graph.nodes.filter(n=>n.kind==='ability').map(n=>[n.id,n.execution])))
  &&binding.coverage_hash===await digest(binding.owner_bindings));
 need(sameGraphWire(binding.graph,value.command.graph));
 if(graphHasEvents(family)){
  // New local fractions have no server canonical spelling yet. Their complete
  // JSON values were compared above; every frozen source subtree is also
  // compared byte-for-byte, including the copy embedded in subscriptions.
  need(sameGraphWire(value.command.event_source_bindings,command.event_source_bindings)
   &&sameGraphWire(value.command.graph.subscriptions,command.graph.subscriptions)
   &&sameGraphWire(binding.graph,value.command.graph)
   &&sameGraphWire(binding.event_source_bindings,command.event_source_bindings)&&sameGraphWire(binding.event_admission_policy,command.event_admission_policy));
  await verifyWaitBindings(command.graph,command.event_source_bindings,command.event_admission_policy);
  for(const pin of Object.values(command.event_source_bindings)){const source=context.event_wait.sources.find(s=>s.producer_id===pin.producer_id);
   need(source?.available===true&&source.contract_hash===pin.contract_hash&&sameGraphWire(source.contract,pin.contract));}
 }
 need(binding.source_proof.handoff_id===scope.handoff_id&&binding.source_proof.manifest_hash===context.manifest.manifest_hash);await sealed(binding,'binding_hash');await sealed(binding.source_proof,'proof_hash');await sealed(value,'preview_hash');
 need(same(Object.keys(binding.role_bindings).sort(),Object.keys(command.role_bindings).sort()));
 for(const [role,id] of Object.entries(command.role_bindings)){const member=members.find(m=>m.id===id),saved=binding.role_bindings[role];need(member&&saved.user_id===id&&saved.username===member.name&&saved.membership_role===member.role);}
 if(kind==='revision'){
  const head=context.adoptions.find(a=>a.is_current&&a.business_name===command.business_name);
  need(head&&same(value.parent,command.parent)&&head.origin.adoption_id===command.parent.id&&head.receipt.receipt_hash===command.parent.receipt_hash
   &&value.root_id===head.root_id&&value.previous_binding_hash===head.binding.binding_hash&&value.effect_scope==='future_releases_only');
 }
 return {preview_hash:value.preview_hash,binding_hash:binding.binding_hash,design_hash:binding.design_hash,manifest_hash:binding.manifest_hash,proof_hash:binding.proof_hash,
  ...(kind==='revision'?{root_id:value.root_id,previous_binding_hash:value.previous_binding_hash}:{})};
}
export async function verifyGraphSavedReceipt(saved,pending,scope){
 const revision=pending.kind==='revision',family=graphFamily(pending.body),r=saved?.receipt,o=saved?.origin;
 need(keys(saved,'receipt,origin')&&keys(o,originKeys)&&o.schema===graphOriginSchema(family)&&o.handoff_id===scope.handoff_id&&o.business_name===pending.body.business_name
  &&o.binding_hash===pending.pin.binding_hash&&o.command_hash===pending.command_hash&&uuid(o.adoption_id)&&r.id===o.adoption_id
  &&r.workspace_id===scope.workspace_id&&r.actor_id===scope.actor_id&&r.handoff_id===scope.handoff_id&&r.request_id===pending.body.request_id
  &&r.command_hash===pending.command_hash&&r.preview_hash===pending.body.preview_hash&&r.accepted_revision===pending.body.configuration_revision+1&&sha(r.configuration_hash)&&sha(r.record_hash));
 const receiptFields='id,workspace_id,actor_id,handoff_id,request_id,command_hash,preview_hash,accepted_revision,configuration_hash,record_hash,receipt_hash';
 need(keys(r,receiptFields+(revision?',schema,parent_id,root_id,parent_receipt_hash,previous_binding_hash':'')));
 if(revision)need(r.schema===graphProtocol(family).receipt&&r.parent_id===pending.body.parent.id&&r.parent_receipt_hash===pending.body.parent.receipt_hash
  &&r.root_id===pending.pin.root_id&&r.previous_binding_hash===pending.pin.previous_binding_hash);
 await sealed(r,'receipt_hash');return saved;
}
export async function verifyGraphAdoptionReply(value,pending,scope){
 const revision=pending.kind==='revision',family=graphFamily(pending.body);
 need(keys(value,'schema,receipt,origin,configuration'+(revision?',current_matches,source_semantics_status,effect_scope':'')+(value?.found!==undefined?',found':''))
  &&value.schema===graphProtocol(family).result[pending.kind]&&(value.found===undefined||value.found===true));
 const saved={receipt:copy(value.receipt),origin:copy(value.origin)};await verifyGraphSavedReceipt(saved,pending,scope);
 if(revision)need(typeof value.current_matches==='boolean'&&value.source_semantics_status==='not_certified'&&value.effect_scope==='future_releases_only');
 const config=value.configuration,r=saved.receipt;need(object(config)&&fullInt(config.revision,1)&&config.revision>=r.accepted_revision&&object(config.configuration));
 if(config.revision===r.accepted_revision)need(await digest(config.configuration)===r.configuration_hash&&same(config.configuration.fullSemanticGraphBindings?.[pending.body.business_name],saved.origin));
 return saved;
}

function pendingFamily(p){return p.schema==='full-semantic-graph-adoption-pending-v3'?p.family:p.schema==='full-semantic-graph-adoption-pending-v2'?'v4':'v3';}
function storageMirrorMatches(left,right){
 let count=0;
 function equal(a,b,depth=0){
  need(++count<=1000000&&depth<=128);
  if(typeof a==='number'||typeof b==='number')return typeof a==='number'&&typeof b==='number'&&Number.isFinite(a)&&Number.isFinite(b)&&Object.is(a,b);
  if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return a===b;
  if(Array.isArray(a)!==Array.isArray(b))return false;
  const ak=Object.keys(a),bk=Object.keys(b);return ak.length===bk.length&&ak.every(k=>Object.hasOwn(b,k)&&equal(a[k],b[k],depth+1));
 }
 return equal(left,right);
}
export function checkedGraphAdoptionPending(value,scope){
 const typed=value?.schema==='full-semantic-graph-adoption-pending-v3',newer=typed||value?.schema==='full-semantic-graph-adoption-pending-v2';
 need(keys(value,'schema,scope,kind,body,command_hash,pin,generation,attempted,rejection,receipt'+(newer?',family,exact_body':''))
  &&value.schema===(typed?'full-semantic-graph-adoption-pending-v3':newer?'full-semantic-graph-adoption-pending-v2':'full-semantic-graph-adoption-pending-v1')
  &&same(value.scope,scope)&&['root','revision'].includes(value.kind)&&fullInt(value.generation)&&typeof value.attempted==='boolean'&&sha(value.command_hash)
  &&keys(value.pin,'preview_hash,binding_hash,design_hash,manifest_hash,proof_hash'+(value.kind==='revision'?',root_id,previous_binding_hash':''))
  &&Object.entries(value.pin).every(([k,v])=>k==='root_id'?uuid(v):sha(v)));
 let p=value;
 if(newer){
  need((typed?['v3','v4',objectGraphFamily].includes(value.family):value.family==='v4')&&typeof value.exact_body==='string');
  const parsed=JSON.parse(value.exact_body),body=reviveGraphCommand(value.exact_body,parsed);
  // body is a JSON storage mirror, not the request authority. JSON storage
  // cannot retain -0; the signed original bytes remain solely in exact_body.
  need(storageMirrorMatches(value.body,JSON.parse(JSON.stringify(body))));p={...value,body};
 }
 graphCommand(p.body,p.kind);need(graphFamily(p.body)===pendingFamily(p));
 need(p.pin.preview_hash===p.body.preview_hash&&(p.rejection===null||keys(p.rejection,'status,message')&&[400,404,409,413,422,428].includes(p.rejection.status)&&typeof p.rejection.message==='string'&&p.rejection.message.length<=1000)
  &&(p.receipt===null||keys(p.receipt,'receipt,origin'))&&!(p.receipt&&p.rejection));
 return p;
}

export class FullSemanticGraphAdoptionController{
 constructor({api,design=null,handoffId=design?.handoff_id,actorId,workspaceId,contextFamily='v3',storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks,prepareMutation,onChange=()=>{}}){
  need(uuid(actorId)&&uuid(workspaceId)&&uuid(handoffId));this.scope={actor_id:actorId,workspace_id:workspaceId,handoff_id:handoffId};this.design=design?copy(design):null;
  graphProtocol(contextFamily);if(design)need((design.schema==='report-object-semantic-design-v169')===(contextFamily===objectGraphFamily),'新对象设计须使用独立完整图入口；原版本和请求保留。');this.contextFamily=contextFamily;
  this.storage=storage;this.tabStorage=tabStorage;this.locks=locks;this.prepareMutation=prepareMutation;this.onChange=onChange;this.active=true;this.denied=false;this.busy=false;
  this.token=tabStorage?.getItem('ga-setup-csrf')||'';this.prefix=(contextFamily===objectGraphFamily?objectPrefix:prefix)+workspaceId+':'+actorId+':'+handoffId+':';this.owner=crypto.randomUUID();this.key=this.prefix+'working:'+this.owner;this.outboxKey=this.prefix+'outbox';this.tabKey=this.prefix+'tab';
  this.context=null;this.members=[];this.abilities=[];this.role='';this.preview=null;this.error='';this.missing=false;this.otherDrafts=[];
  const send=scopedPreparationRequester({user_id:actorId,workspace_id:workspaceId},api,tabStorage);
  this.api=async(...args)=>{try{const result=await send(...args);return args[0].startsWith('/setup/full-semantic-graph-wire/')?verifyGraphWire(result):result;}catch(e){if([401,403].includes(e.status)||/页面会话已变化/.test(e.message)){this.denied=true;this.context=null;this.members=[];this.abilities=[];this.notify();}throw e;}};
  this.check();const saved=tabStorage?.getItem(this.tabKey);const old=saved?this.readWorking(saved):null;
  this.state=old?{...copy(old),owner:this.owner}:{schema:'full-semantic-graph-adoption-working-v1',scope:copy(this.scope),owner:this.owner,generation:0,design_hash:design?.design_hash||'',value:null};
  this.localSaved=false;this.localError='';this.expectedWorking=null;this.writeWorking();this.pending=this.readPending();this.refreshDrafts();
 }
 notify(){this.onChange();}
 check(){need(this.active&&!this.suspended&&!this.denied,'账号或页面已变化，原输入与请求仍保留。');if(!(this.token&&this.tabStorage?.getItem('ga-setup-csrf')===this.token&&this.tabStorage?.getItem('ga-setup-csrf-user')===this.scope.actor_id&&this.tabStorage?.getItem('ga-setup-csrf-workspace')===this.scope.workspace_id)){this.denied=true;this.context=null;this.members=[];this.abilities=[];throw Error('页面会话已变化，旧账号输入已隐藏，原记录保留。');}}
 base(kind='root',family='v3'){graphProtocol(family);return graphWireFamilyPath(this.scope.handoff_id,family,kind);}
 async identity(){this.check();const me=await this.api('/me');this.check();if(me.user_id!==this.scope.actor_id||me.workspace_id!==this.scope.workspace_id){this.denied=true;this.context=null;throw Error('当前账号或工作区已变化，原选择仍保留。');}this.role=me.role;return me;}
 readWorking(key){need(typeof key==='string'&&key.startsWith(this.prefix+'working:')&&uuid(key.slice((this.prefix+'working:').length)));const raw=this.storage.getItem(key);need(raw!==null);const value=JSON.parse(raw);safeJSON(value);need(keys(value,'schema,scope,owner,generation,design_hash,value')&&value.schema==='full-semantic-graph-adoption-working-v1'&&same(value.scope,this.scope)&&uuid(value.owner)&&key===this.prefix+'working:'+value.owner&&fullInt(value.generation)&&(value.design_hash===''||sha(value.design_hash))&&(value.value===null||object(value.value)));return value;}
 writeWorking(){try{safeJSON(this.state);need(this.expectedWorking===null?this.storage.getItem(this.key)===null:this.storage.getItem(this.key)===this.expectedWorking,'另一页面已保存本机稿。本页输入仍保留，请另存本页草稿。');exactPreparationWrite(this.storage,this.key,this.state);this.expectedWorking=JSON.stringify(this.state);need(this.tabStorage);this.tabStorage.setItem(this.tabKey,this.key);need(this.tabStorage.getItem(this.tabKey)===this.key);this.localSaved=true;this.localError='';return true;}catch(e){this.localSaved=false;this.localError=e.message||'本页输入未可靠保存，请保留页面并重试。';return false;}}
 persist(){this.check();const ok=this.writeWorking();this.notify();return ok;}
 forkWorking(){this.check();this.owner=crypto.randomUUID();this.key=this.prefix+'working:'+this.owner;this.expectedWorking=null;this.state={...copy(this.state),owner:this.owner};this.persist();}
 refreshDrafts(){this.otherDrafts=[];this.draftError='';for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key===this.key||!key?.startsWith(this.prefix+'working:'))continue;try{const state=this.readWorking(key);if(state.value&&!same(state.value,this.state.value))this.otherDrafts.push({key,state});}catch{this.draftError='另有本机流程稿无法读取，原记录保留。';}}return this.otherDrafts;}
 restoreDraft(key){this.check();need(!this.pending&&!this.busy);need(this.persist(),this.localError);const old=this.readWorking(key);this.owner=crypto.randomUUID();this.key=this.prefix+'working:'+this.owner;this.expectedWorking=null;this.state={...copy(old),owner:this.owner,generation:Math.max(old.generation,this.state.generation)+1};this.preview=null;this.persist();this.refreshDrafts();}
 edit(value){this.check();need(!this.busy&&!this.pending&&['admin','operator'].includes(this.role)&&object(value));this.state={...this.state,design_hash:this.state.value?this.state.design_hash:this.context.design.design_hash,generation:this.state.generation+1,value:restoreGraphEditorDraftNumbers(value)};this.preview=null;this.persist();}
 chooseBusiness(name){this.check();need(!this.pending&&!this.busy&&this.context&&this.context.design.result.businesses.some(b=>b.name===name));need(this.persist(),this.localError);this.owner=crypto.randomUUID();this.key=this.prefix+'working:'+this.owner;this.expectedWorking=null;this.state={...copy(this.state),owner:this.owner,design_hash:this.context.design.design_hash};this.edit(createGraphEditorDraft(this.context,name));this.refreshDrafts();}
 issues(){if(!this.context||!this.state.value)return [{path:'business_name',message:'请先选择要确认的业务。'}];const result=collectGraphEditorIssues(this.state.value,this.context,this.abilities,this.members);if(this.state.design_hash!==this.context.design.design_hash)result.unshift({path:'source',message:'来源设计已变化，原稿保留；请先对照新设计。'});return result;}
 async load(){await this.identity();const context=await this.api(this.base('root',this.contextFamily)+'/editor-context');this.check();await verifyGraphEditorContext(context,this.scope);need(context.schema===graphContextSchema(this.contextFamily));if(this.design)need(same(this.design,context.design),'服务器设计与当前显示的资料设计不一致，原选择保留。');this.context=context;this.design=copy(context.design);
  if(['admin','operator'].includes(this.role)){const [members,abilities]=await Promise.all([this.api('/instances/members'),this.api(this.base('root',this.contextFamily===objectGraphFamily?objectGraphFamily:'v3')+'/ability-options')]);this.check();need(Array.isArray(members)&&members.every(m=>uuid(m.id)&&typeof m.name==='string'&&['admin','operator','reviewer','viewer'].includes(m.role)));need(Array.isArray(abilities)&&abilities.every(a=>uuid(a.id)&&sha(a.content_hash)&&typeof a.release_version==='string'&&a.release_version.length>0&&fullInt(a.max_attempts,1)&&object(a.input_schema)&&object(a.output_schema)));this.members=members;this.abilities=abilities;}
  this.refreshDrafts();this.notify();return context;
 }
 async makePreview(){this.check();need(!this.busy&&!this.pending&&!this.readPending()&&this.localSaved&&this.locks?.request,'请先保全本机输入并核对原请求；多窗口提交保护也必须可用。');this.busy=true;try{await this.identity();need(['admin','operator'].includes(this.role));if(this.prepareMutation)await this.prepareMutation(copy(this.scope));this.check();need(this.context?.active&&this.state.design_hash===this.context.design.design_hash);const problems=this.issues();need(!problems.length,problems.map(x=>x.message).slice(0,5).join('；'));const generation=this.state.generation;const command=buildGraphPreviewCommand(this.state.value,this.context,this.abilities,{typedNumbers:true});const kind=command.parent?'revision':'root',family=graphFamily(command);const value=await this.api(this.base(kind,family)+'/preview',{method:'POST',body:graphWireRequestJSON(command)});this.check();const pin=await verifyGraphAdoptionPreview(value,command,kind,{scope:this.scope,context:this.context,members:this.members});need(this.state.generation===generation,'本页输入在预览期间已变化，原输入保留，请重新预览。');this.preview={kind,family,generation,command:value.command,value,pin};this.notify();return value;}finally{this.busy=false;this.notify();}}
 readPending(){const raw=this.storage.getItem(this.outboxKey);if(raw===null)return null;const p=JSON.parse(raw);safeJSON(p);const checked=checkedGraphAdoptionPending(p,this.scope);need((pendingFamily(checked)===objectGraphFamily)===(this.contextFamily===objectGraphFamily),'原请求不属于此执行版本，原字节与记录保留。');return checked;}
 exact(p){this.check();need(same(this.readPending(),p),'原请求已在另一页面变化，未重发或清除。');}
 async commandHash(kind,body,{legacyV1=false}={}){
  this.check();graphCommand(body,kind);
  const family=graphFamily(body),request={kind,command:body};
  const result=await this.api(this.base('root',family)+'/command-hash',{method:'POST',body:legacyV1?JSON.stringify(request):graphWireRequestJSON(request)});
  this.check();need(keys(result,'schema,kind,actor_id,workspace_id,handoff_id,command,command_hash')&&result.schema==='report-full-semantic-graph-command-hash-v1'
   &&result.kind===kind&&Object.entries(this.scope).every(([k,v])=>result[k]===v)&&sha(result.command_hash));
  need(legacyV1?same(result.command,body):sameGraphWire(result.command,body));
  return result.command_hash;
 }
 async validPending(p){
  need(await this.commandHash(p.kind,p.body,{legacyV1:p.schema==='full-semantic-graph-adoption-pending-v1'})===p.command_hash);
  if(p.receipt){
   await verifyGraphSavedReceipt(p.receipt,p,this.scope);
   const response=await this.api(this.base(p.kind,pendingFamily(p))+'/requests/'+p.body.request_id);this.check();this.exact(p);
   need(response?.found===true,'服务器尚未核实本机原回执，原请求与记录保留。');
   const actual=await verifyGraphAdoptionReply(response,p,this.scope);this.check();this.exact(p);
   need(same(actual,p.receipt),'本机原回执与服务器真实记录不一致，原内容保留，未结清。');
  }
 }
 async submit(consent){this.check();need(consent===true,'请核对服务器预览，并独立同意采用本次完整安排。');need(!this.busy&&!this.pending&&!this.readPending()&&this.localSaved&&this.preview&&this.preview.generation===this.state.generation&&this.locks?.request);const preview=this.preview;await this.identity();need(['admin','operator'].includes(this.role)&&this.preview===preview&&this.state.generation===preview.generation&&this.localSaved,'预览期间输入或权限已变化，请保留并重新核对。');const family=graphFamily(preview.command),body={...preview.command,schema:graphProtocol(family).commands[preview.kind],request_id:crypto.randomUUID(),preview_hash:preview.pin.preview_hash,consent:true,semantic_mode:'workspace_workflow_design'};const p={schema:'full-semantic-graph-adoption-pending-v3',family,exact_body:graphWireCanonical(body),scope:copy(this.scope),kind:preview.kind,body,command_hash:await this.commandHash(preview.kind,body),pin:copy(preview.pin),generation:preview.generation,attempted:false,rejection:null,receipt:null};this.check();need(this.preview===preview&&this.state.generation===preview.generation&&this.localSaved,'输入在提交前已变化，尚未发送采用请求。');return this.recover(true,p);}
 async recover(retry=false,newPending=null){this.check();need(!this.busy);this.busy=true;try{const run=async()=>{this.check();if(newPending){need(this.readPending()===null);safeJSON(newPending);exactPreparationWrite(this.storage,this.outboxKey,newPending);pendingChanged();}let p=this.readPending();need(p,'本页尚无待核对的原提交。');this.pending=p;this.exact(p);await this.validPending(p);await this.identity();this.exact(p);
   if(!p.receipt){let response=await this.api(this.base(p.kind,pendingFamily(p))+'/requests/'+p.body.request_id);this.check();this.exact(p);const missing=response?.found===false;need(missing?keys(response,'schema,found')&&response.schema===graphProtocol(pendingFamily(p)).recovery[p.kind]:response?.found===true);this.missing=missing;
    if(missing&&retry){need(this.locks?.request&&!p.rejection&&['admin','operator'].includes(this.role),'请先保留并核对已拒请求，不能自动建立另一份采用。');p={...p,attempted:true};exactPreparationWrite(this.storage,this.outboxKey,p);this.pending=p;
     try{response=await this.api(this.base(p.kind,pendingFamily(p))+'/adopt',{method:'POST',body:p.schema==='full-semantic-graph-adoption-pending-v1'?JSON.stringify(p.body):p.exact_body});}catch(e){this.check();this.exact(p);if([400,404,409,413,422,428].includes(e.status)){p={...p,rejection:{status:e.status,message:String(e.message).slice(0,1000)}};exactPreparationWrite(this.storage,this.outboxKey,p);this.pending=p;}throw e;}
    }else if(missing){this.notify();return false;}
    this.check();this.exact(p);const receipt=await verifyGraphAdoptionReply(response,p,this.scope);this.exact(p);p={...p,receipt,rejection:null};exactPreparationWrite(this.storage,this.outboxKey,p);this.pending=p;this.missing=false;
   }
   this.notify();return true;
  };if(this.locks?.request)return await this.locks.request(this.prefix+'lock',{mode:'exclusive'},run);need(!retry&&!newPending,'当前浏览器不能保护提交，只能核对原请求。');return await run();
 }finally{this.busy=false;this.notify();}}
 async acceptCurrent(readCurrent){
  this.check();need(this.pending?.receipt&&this.locks?.request&&typeof readCurrent==='function');
  const p=this.readPending();need(p);await this.validPending(p);await this.identity();this.exact(p);let completed=false;
  await readCurrent(copy(this.scope),current=>this.locks.request(this.prefix+'lock',{mode:'exclusive'},async()=>{
   this.check();this.exact(p);
   need(object(current)&&fullInt(current.revision,1)&&object(current.configuration)&&current.revision>=p.receipt.receipt.accepted_revision,'服务器当前配置尚未核对，原提交继续保留。');
   const canonicalCurrent=await this.api(this.base('root',pendingFamily(p))+'/configuration');this.check();this.exact(p);need(canonicalCurrent.revision===current.revision&&same(canonicalCurrent.configuration,current.configuration),'读取期间服务器配置已变化，原回执保留，请重新读取。');
   if(current.revision===p.receipt.receipt.accepted_revision)need(await digest(canonicalCurrent.configuration)===p.receipt.receipt.configuration_hash&&same(current.configuration.fullSemanticGraphBindings?.[p.body.business_name],p.receipt.origin));
   this.check();this.exact(p);need(this.persist(),this.localError);
   exactPreparationWrite(this.storage,this.prefix+'accepted:'+p.body.request_id,{...p,current_configuration:{revision:current.revision,hash:await digest(canonicalCurrent.configuration)}});
   this.exact(p);this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);
   this.pending=null;this.preview=null;this.missing=false;completed=true;this.notify();pendingChanged();
  }));
  this.check();need(completed,'服务器当前配置尚未读入，原采用回执仍保留。');return true;
 }
 async archiveRejected(){
  this.check();need(this.locks?.request&&!this.busy);this.busy=true;
  try{return await this.locks.request(this.prefix+'lock',{mode:'exclusive'},async()=>{
   let p=this.readPending();need(p?.rejection&&!p.receipt,'结果未知或已有回执的原请求不能丢弃。');this.exact(p);await this.validPending(p);await this.identity();this.exact(p);
   const response=await this.api(this.base(p.kind,pendingFamily(p))+'/requests/'+p.body.request_id);this.check();this.exact(p);
   if(response?.found===true){
    const receipt=await verifyGraphAdoptionReply(response,p,this.scope);this.exact(p);p={...p,receipt,rejection:null};
    exactPreparationWrite(this.storage,this.outboxKey,p);this.pending=p;this.missing=false;this.notify();
    throw Error('服务器已保存原请求，原回执已保留，请读取当前配置继续。');
   }
   need(keys(response,'schema,found')&&response.found===false&&response.schema===graphProtocol(pendingFamily(p)).recovery[p.kind]);
   exactPreparationWrite(this.storage,this.prefix+'rejected:'+p.body.request_id,p);this.exact(p);
   this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);
   this.pending=null;this.preview=null;this.notify();pendingChanged();
  });}finally{this.busy=false;this.notify();}
 }

 dispose(){this.active=false;}
}

export function graphAdoptionPendingScopes(storage,{actorId,workspaceId}){
 const starts=[prefix,objectPrefix].map(value=>value+workspaceId+':'+actorId+':'),result=[];
 for(let i=0;i<storage.length;i++){const key=storage.key(i),start=starts.find(value=>key?.startsWith(value));if(!start||!key.endsWith(':outbox'))continue;try{const raw=JSON.parse(storage.getItem(key)),p=checkedGraphAdoptionPending(raw,{actor_id:actorId,workspace_id:workspaceId,handoff_id:raw.scope?.handoff_id});need(uuid(p.scope.handoff_id)&&key===start+p.scope.handoff_id+':outbox'&&(start.startsWith(objectPrefix)===(pendingFamily(p)===objectGraphFamily)));result.push({handoff_id:p.scope.handoff_id,business_name:p.body.business_name,request_id:p.body.request_id,kind:p.kind,family:pendingFamily(p)});}catch{result.push({unreadable:true,key});}}
 return result;
}

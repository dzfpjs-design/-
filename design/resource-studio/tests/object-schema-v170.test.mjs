// Public synthetic protocol fixtures only. These checks do not establish real
// report truth, source authority, provider quality, HTTP or PostgreSQL success.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
 FullSemanticAdoptionController,adoptionCommandHash,verifyAdoptionPreview,
 verifyAdoptionReply,verifyCurrentAdoptionDraft,objectSchemaDesign,copy,
} from '../src/full-semantic-adoption-controller.mjs';
import {generationHash as digest} from '../src/instance-generation.mjs';
import {OBJECT_DESIGN_V169,objectAdoptionUUID5} from '../src/object-adoption-v169.mjs';
import {fixture as fullFixture,memoryStorage} from './full-semantic-handoff-fixture.mjs';

const H='a'.repeat(64),PRIVATE='绝不共享的私人说明';
async function seal(value,key){const body=copy(value);delete body[key];return {...body,[key]:await digest(body)};}
function memory(){const rows=memoryStorage();return {fail:false,get length(){return rows.length;},key:i=>rows.key(i),getItem:k=>rows.getItem(k),setItem(k,v){if(this.fail)throw Error('synthetic quota');rows.setItem(k,v);},removeItem:k=>rows.removeItem(k)};}
const posts=f=>f.calls.filter(c=>c.method==='POST'&&c.url.endsWith('/adoptions'));

async function fixture(){
 const base=await fullFixture(),design=copy(base.preview.design),scope={actor_id:base.account.user_id,workspace_id:base.account.workspace_id,handoff_id:design.handoff_id};
 const names=['\ue000资料','😀任务'],relationName='资料关联任务',proofId=crypto.randomUUID(),manifestHash='d'.repeat(64),proofHash='e'.repeat(64),materialIds=[crypto.randomUUID(),crypto.randomUUID()];
 design.schema=OBJECT_DESIGN_V169;design.result.schema='report-object-semantic-candidate-wire-v166';
 design.result.objects=[
  {name:names[0],fields:[{label:'名称🙂',kind:'文本',required:false,unit:null,citations:[{span_ref:base.ref}],inference_reason:''},{label:'长度\ue000',kind:'数值',required:false,unit:'米',citations:[],inference_reason:'合成字段单位仍须核对。'}],citations:[{span_ref:base.ref}],inference_reason:'',business_identity:{status:'referenced',field_labels:['名称🙂'],citations:[{span_ref:base.ref}],explanation:'引用字段只是待核业务标识说明，不代表唯一或必填。'}},
  {name:names[1],fields:[{label:'数量',kind:'整数',required:false,unit:null,citations:[],inference_reason:'人工合成字段。'}],citations:[],inference_reason:'人工补充对象。',business_identity:{status:'not_established',field_labels:[],citations:[],explanation:'业务标识尚未建立，继续保留未知。'}},
 ];
 design.result.relationships=[{name:relationName,description:'只用于合成合同核对，不证明实际对象之间存在关联。',source_type:names[0],target_type:names[1],cardinality:'unspecified',citations:[{span_ref:base.ref}],inference_reason:''}];
 // Deliberately retain all original rules/questions/conditions and reverse the
 // confirmation array: an acknowledgement path is never inferred by object index.
 design.object_identity_confirmations=names.slice().reverse().map(object_name=>({object_name,acknowledge_platform_identity:true}));
 const privateValue={schema:'report-object-semantic-review-v166',result:copy(design.result),object_identity_confirmations:copy(design.object_identity_confirmations),note:PRIVATE};
 design.review_origin={schema:'report-object-confirmed-origin-v169',report_id:design.report_id,actor_id:scope.actor_id,workspace_id:scope.workspace_id,revision_id:crypto.randomUUID(),revision_version:3,revision_record_hash:H,basis_hash:H,confirmed_command_id:crypto.randomUUID(),confirmed_command_hash:H,confirmed_wire_sha256:H,confirmed_value_hash:await digest(privateValue),confirmed_audit_id:crypto.randomUUID(),confirmed_audit_hash:H};
 design.review_receipt_hash=design.review_origin.confirmed_command_hash;design.manual_value_hash=design.review_origin.confirmed_value_hash;
 design.business_bindings=[];
 for(const[i,b]of design.result.businesses.entries()){
  const binding={candidate_index:i,name:b.name};
  for(const[kind,collection]of [['step','steps'],['participant','participants'],['interaction','interactions'],['condition','conditions'],['coordination','coordination']])binding[kind+'_ids']=await Promise.all(b[collection].map((_,j)=>objectAdoptionUUID5(design.handoff_id,OBJECT_DESIGN_V169+'/'+kind+'/'+i+'/'+j)));
  design.business_bindings.push(binding);
 }
 const complete=await seal(design,'design_hash'),storage=memory(),tab=base.tab,calls=[],requests=new Map(),previews=new Map(),mode={before:false,after:false,reject:false,identity:null,mutateReply:null};
 const saved={revision:7,configuration:{version:1,fullSemanticDesigns:[copy(complete)],retained_number:1.25}};
 const state={schema:'report-object-schema-state-v170',handoff_id:scope.handoff_id,active:true,design_hash:complete.design_hash,manifest_hash:manifestHash,configuration:{revision:7,hash:H},objects:copy(complete.result.objects),relationships:copy(complete.result.relationships),proofs:[{id:proofId,proof_hash:proofHash,current_source_verified:true}],adoptions:[],semantic_mode:'workspace_schema_design',execution_supported:false};
 async function descriptor(raw,path,kind='base'){
  const refs=await Promise.all((kind==='ack'?[]:raw.citations||[]).map((c,i)=>digest({path,i,c}))),reasons=[];
  if(kind==='ack')reasons.push('human_confirmation_not_source_evidence');
  else{if(!refs.length)reasons.push('no_citations');if(raw.inference_reason?.trim())reasons.push('inference_reason_requires_review');if(refs.length)reasons.push('source_mapping_required');if(kind==='identity'){reasons.push('identity_claim_requires_review');if(raw.status==='not_established')reasons.push('unknown_evidence');}}
  return {owner_path:path,owner_value_hash:await digest(raw),ref_hashes:refs,declared_evidence_status:kind==='ack'?'context':kind==='identity'&&raw.status==='not_established'?'unknown':'not_declared',pending_reasons:reasons};
 }
 async function schemaPreview(body){
  const objects=[],relationships=[],object_sources={},relationship_sources={},owner_paths=[],pending_evidence_owner_paths=[];
  const add=(path,s,always=false)=>{owner_paths.push(path);if(always||!s.ref_hashes.length||s.pending_reasons.includes('inference_reason_requires_review'))pending_evidence_owner_paths.push(path);};
  for(const[i,o]of complete.result.objects.entries()){
   if(!body.selection.object_names.includes(o.name))continue;
   const path='/objects/'+i,s=await descriptor(o,path);add(path,s);s.fields=[];
   for(const[j,f]of o.fields.entries()){const child=path+'/fields/'+j,source=await descriptor(f,child);s.fields.push(source);add(child,source);}
   s.business_identity=await descriptor(o.business_identity,path+'/business_identity','identity');add(s.business_identity.owner_path,s.business_identity,true);
   const ai=complete.object_identity_confirmations.findIndex(a=>a.object_name===o.name),ack=complete.object_identity_confirmations[ai];
   s.platform_identity_confirmation=await descriptor(ack,'/object_identity_confirmations/'+ai,'ack');add(s.platform_identity_confirmation.owner_path,s.platform_identity_confirmation,true);object_sources[o.name]=s;
   objects.push({schema:'report-object-type-v170',name:o.name,fields:o.fields.map(f=>({label:f.label,kind:f.kind,required:f.required,unit:f.unit})),business_identity:copy(o.business_identity),platform_identity_confirmation:copy(ack)});
  }
  for(const[i,r]of complete.result.relationships.entries()){
   if(!body.selection.relationship_names.includes(r.name))continue;
   const path='/relationships/'+i,s=await descriptor(r,path);add(path,s);relationship_sources[r.name]={...s,declared_cardinality:r.cardinality,cardinality_origin:'workspace_policy',allow_self_origin:'workspace_policy'};relationships.push({name:r.name,description:r.description,source_type:r.source_type,target_type:r.target_type,...copy(body.relationship_policies[r.name])});
  }
  const plan=await seal({schema:'report-object-schema-adoption-v170',design_hash:complete.design_hash,manifest_hash:manifestHash,proof_hash:proofHash,selection:copy(body.selection),relationship_policies:copy(body.relationship_policies),semantic_mode:'workspace_schema_design',owner_paths,pending_evidence_owner_paths,objects,relationships,object_sources,relationship_sources},'plan_hash');
  return seal({schema:'report-object-schema-preview-v170',...scope,proof_id:proofId,plan,configuration:{revision:body.configuration_revision,hash:body.configuration_hash},conflicts:[]},'preview_hash');
 }
 let controller;
 const root='/setup/full-semantic-schemas/'+scope.handoff_id;
 async function api(url,options={}){
  calls.push({url,method:options.method||'GET',body:options.body,headers:options.headers});
  if(url==='/me')return mode.identity||{user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(url==='/setup/draft')return copy(saved);
  if(url===root)return copy(state);
  if(url.startsWith(root+'/requests/'))return copy(requests.get(url.split('/').at(-1))||{schema:'report-full-semantic-schema-recovery-v1',found:false});
  if(url===root+'/preview'){
   const body=JSON.parse(options.body);assert.equal(body.schema,'report-object-schema-preview-command-v170');const preview=await schemaPreview(body);previews.set(preview.preview_hash,preview);return copy(preview);
  }
  if(url===root+'/adoptions'){
   const body=JSON.parse(options.body),original=JSON.parse(storage.getItem(controller.outboxKey));assert.deepEqual(original.body,body);assert.equal(body.schema,'report-object-schema-command-v170');assert.ok(calls.some(c=>c.url===root+'/requests/'+body.request_id));
   if(mode.before)throw Error('synthetic connection lost before save');
   if(mode.reject)throw Object.assign(Error('synthetic stale configuration'),{status:409,detail:{code:'full_schema_stale'}});
   const preview=previews.get(body.preview_hash),id=crypto.randomUUID(),receipt=await seal({schema:'report-object-schema-receipt-v170',...scope,proof_id:proofId,id,configuration_id:crypto.randomUUID(),request_id:body.request_id,command_hash:await adoptionCommandHash(scope,'schema',body),preview_hash:preview.preview_hash,plan_hash:preview.plan.plan_hash,accepted_revision:body.configuration_revision+1,configuration_hash:H},'receipt_hash');
   const result={schema:'report-full-semantic-schema-reply-v1',id,handoff_id:scope.handoff_id,plan:copy(preview.plan),receipt,configuration:{revision:receipt.accepted_revision,hash:H},current_matches:true};requests.set(body.request_id,{...copy(result),schema:'report-full-semantic-schema-recovery-v1',found:true});
   saved.revision=receipt.accepted_revision;saved.configuration.fullSemanticSchemaAdoptions=[{id,plan_hash:preview.plan.plan_hash,material_ids:materialIds.slice()}];saved.configuration.instanceObjects=copy(preview.plan.objects);saved.configuration.instanceRelationTypes=copy(preview.plan.relationships);
   for(const[kind,rows,key,sources]of [['object',preview.plan.objects,'instanceObjectOrigins',preview.plan.object_sources],['relationship',preview.plan.relationships,'instanceRelationOrigins',preview.plan.relationship_sources]])saved.configuration[key]=Object.fromEntries(rows.map(row=>[row.name,{schema:'report-object-schema-origin-v170',adoption_id:id,plan_hash:preview.plan.plan_hash,kind,name:row.name,owner_path:sources[row.name].owner_path,owner_value_hash:sources[row.name].owner_value_hash}]));
   state.configuration.revision=saved.revision;state.adoptions.push({id,receipt:copy(receipt)});
   if(mode.after)throw Error('synthetic successful reply lost');
   return copy(mode.mutateReply?await mode.mutateReply(copy(result)):result);
  }
  throw Error('Unexpected synthetic route '+url);
 }
 const options={api,design:complete,actorId:scope.actor_id,workspaceId:scope.workspace_id,storage,tabStorage:tab,locks:{request:async(_name,_options,fn)=>fn()},prepareMutation:async()=>copy(saved)};
 const open=async changes=>{controller=new FullSemanticAdoptionController({...options,...changes});await controller.load();return controller;};
 return {scope,design:complete,names,relationName,proofId,state,storage,tab,calls,requests,mode,saved,options,open,schemaPreview};
}
function choose(c,f){c.edit({proof_id:f.proofId,object_names:f.names.slice(),relationship_names:[f.relationName],relationship_policies:{[f.relationName]:{cardinality:'many',allow_self:false}}});}
async function previewed(){const f=await fixture(),c=await f.open();choose(c,f);const value=await c.preview('schema');return {f,c,value};}
async function changedPreview(value,mutate){const bad=copy(value);await mutate(bad);bad.plan=await seal(bad.plan,'plan_hash');return seal(bad,'preview_hash');}
function verify(f,c,v){return verifyAdoptionPreview(v,'schema',{scope:f.scope,design:f.design,body:c.state.previews.schema.command,view:f.state,members:[]});}

test('new family preserves complete design, optional units, identities and reversed acknowledgement paths',async()=>{
 const {f,c,value}=await previewed();assert.equal(objectSchemaDesign(f.design),true);assert.ok(f.design.result.global_claims.length&&f.design.result.questions.length&&f.design.result.businesses[0].conditions.length);
 assert.equal(f.calls.some(x=>x.url==='/instances/members'),false);assert.deepEqual(c.members,[]);
 const [first,second]=value.plan.objects;assert.deepEqual(Object.keys(first).sort(),['schema','name','fields','business_identity','platform_identity_confirmation'].sort());assert.ok(value.plan.objects.every(o=>o.fields.every(field=>field.required===false)));
 assert.equal(first.fields[1].unit,'米');assert.equal(second.fields[0].unit,null);assert.deepEqual(first.business_identity,f.design.result.objects[0].business_identity);assert.deepEqual(first.platform_identity_confirmation,f.design.object_identity_confirmations[1]);
 const s=value.plan.object_sources[first.name],unknown=value.plan.object_sources[second.name];assert.equal(s.platform_identity_confirmation.owner_path,'/object_identity_confirmations/1');assert.equal(unknown.platform_identity_confirmation.owner_path,'/object_identity_confirmations/0');assert.equal(unknown.business_identity.declared_evidence_status,'unknown');assert.equal(s.platform_identity_confirmation.declared_evidence_status,'context');assert.deepEqual(s.platform_identity_confirmation.ref_hashes,[]);
 assert.ok(value.plan.pending_evidence_owner_paths.includes(s.business_identity.owner_path)&&value.plan.pending_evidence_owner_paths.includes(s.platform_identity_confirmation.owner_path));assert.equal(JSON.stringify(value).includes(PRIVATE),false);
});

test('schema only adopts with separate consent and new command/hash/receipt/origin while wrappers stay generic',async()=>{
 const {f,c,value}=await previewed();await assert.rejects(c.submit('schema',false));assert.equal(posts(f).length,0);const reply=await c.submit('schema',true),p=c.pending;
 assert.equal(reply.schema,'report-full-semantic-schema-reply-v1');assert.equal(p.receipt.schema,'report-object-schema-receipt-v170');assert.equal(p.body.schema,'report-object-schema-command-v170');assert.equal(p.command_hash,await digest({schema:'report-object-schema-command-hash-v170',...f.scope,body:p.body}));
 assert.equal(f.saved.configuration.instanceObjectOrigins[f.names[0]].schema,'report-object-schema-origin-v170');assert.deepEqual(f.saved.configuration.instanceObjects,value.plan.objects);assert.equal(f.saved.configuration.retained_number,1.25);assert.equal(JSON.stringify(f.saved).includes(PRIVATE),false);
 const original=copy(p);await c.acceptCurrent(async(scope,consume)=>{assert.deepEqual(scope,f.scope);await consume(copy(f.saved));});assert.equal(c.pending,null);assert.equal(f.storage.getItem(c.outboxKey),null);assert.deepEqual(JSON.parse(f.storage.getItem(c.prefix+'resolved:'+p.body.request_id)),original);
});

test('lost successful response remount recovers by GET without a second adoption POST',async()=>{
 const {f,c}=await previewed();f.mode.after=true;await assert.rejects(c.submit('schema',true),/reply lost/);const original=f.storage.getItem(c.outboxKey),request=JSON.parse(original);c.dispose();f.mode.after=false;
 const next=await f.open();assert.equal(next.pending.body.request_id,request.body.request_id);await next.recover();assert.equal(posts(f).length,1);assert.deepEqual(next.pending.body,request.body);assert.ok(next.pending.receipt);await next.acceptCurrent(async(_scope,consume)=>consume(copy(f.saved)));assert.equal(posts(f).length,1);
});

test('missing original result retains exact body and needs explicit retry',async()=>{
 const {f,c}=await previewed();f.mode.before=true;await assert.rejects(c.submit('schema',true));const body=copy(c.pending.body);c.dispose();f.mode.before=false;const next=await f.open();const answer=await next.recover();assert.equal(answer.found,false);assert.equal(posts(f).length,1);assert.deepEqual(next.pending.body,body);await next.recover(true);assert.equal(posts(f).length,2);assert.deepEqual(JSON.parse(posts(f)[1].body),body);
});

test('new schema never calls old runtime endpoints or permits old runtime preview validation',async()=>{
 const {f,c}=await previewed();await assert.rejects(c.preview('runtime'),/完整运行合同/);await assert.rejects(c.submit('runtime',true),/完整运行合同/);await assert.rejects(verifyAdoptionPreview({},'runtime',{scope:f.scope,design:f.design,body:{},view:f.state,members:[]}));assert.equal(f.calls.some(x=>x.url.includes('full-semantic-runtimes')),false);assert.equal(posts(f).length,0);
});

for(const [name,mutate] of [
 ['new preview marker',v=>{v.schema='report-full-semantic-schema-preview-v1';}],
 ['new plan marker',v=>{v.plan.schema='report-full-semantic-schema-adoption-v1';}],
 ['five-key object marker',v=>{delete v.plan.objects[0].schema;}],
 ['optional required flag',v=>{v.plan.objects[0].fields[0].required=true;}],
 ['numeric unit',v=>{v.plan.objects[0].fields[1].unit=null;}],
 ['field subset',v=>{v.plan.objects[0].fields.pop();}],
 ['identity values',v=>{v.plan.objects[0].business_identity.field_labels=[];}],
 ['technical confirmation',v=>{v.plan.objects[0].platform_identity_confirmation.acknowledge_platform_identity=false;}],
 ['reversed confirmation path',v=>{v.plan.object_sources[v.plan.objects[0].name].platform_identity_confirmation.owner_path='/object_identity_confirmations/0';}],
 ['human confirmation source status',v=>{v.plan.object_sources[v.plan.objects[0].name].platform_identity_confirmation.declared_evidence_status='not_declared';}],
 ['human confirmation fake citation',v=>{v.plan.object_sources[v.plan.objects[0].name].platform_identity_confirmation.ref_hashes=[H];}],
 ['business identity pending reason',v=>{v.plan.object_sources[v.plan.objects[0].name].business_identity.pending_reasons=['source_mapping_required'];}],
 ['unknown identity laundering',v=>{v.plan.object_sources[v.plan.objects[1].name].business_identity.declared_evidence_status='not_declared';}],
 ['owner inventory removal',v=>{v.plan.owner_paths.pop();}],
 ['pending confirmation removal',v=>{v.plan.pending_evidence_owner_paths=v.plan.pending_evidence_owner_paths.filter(p=>!p.startsWith('/object_identity_confirmations/'));}],
 ['private field insertion',v=>{v.plan.objects[0].note=PRIVATE;}],
 ['source metadata private field',v=>{v.plan.object_sources[v.plan.objects[0].name].business_identity.note=PRIVATE;}],
])test('resealing cannot launder '+name,async()=>{const {f,c,value}=await previewed();const bad=await changedPreview(value,mutate);await assert.rejects(verify(f,c,bad));assert.equal(posts(f).length,0);});

test('relationship selection requires both objects and explicit cardinality and self policy',async()=>{
 const f=await fixture(),c=await f.open();choose(c,f);c.edit({object_names:[f.names[0]]});await assert.rejects(c.preview('schema'));assert.equal(f.calls.some(x=>x.url.endsWith('/preview')),false);choose(c,f);c.edit({relationship_policies:{[f.relationName]:{cardinality:'',allow_self:null}}});await assert.rejects(c.preview('schema'));assert.equal(posts(f).length,0);
});

test('old state marker is rejected and original local selection stays saved',async()=>{
 const f=await fixture(),c=await f.open();choose(c,f);const raw=f.storage.getItem(c.key);f.state.schema='report-full-semantic-schema-state-v1';await assert.rejects(c.load());assert.equal(f.storage.getItem(c.key),raw);
});

test('old receipt marker and invented replacement wrapper cannot replace actual new-family reply',async()=>{
 const {f,c}=await previewed(),reply=await c.submit('schema',true),p=copy(c.pending);let bad=copy(reply);bad.receipt.schema='report-full-semantic-schema-receipt-v1';bad.receipt=await seal(bad.receipt,'receipt_hash');await assert.rejects(verifyAdoptionReply(bad,p,f.scope));bad=copy(reply);bad.schema='report-object-schema-reply-v170';await assert.rejects(verifyAdoptionReply(bad,p,f.scope));assert.ok(c.pending.receipt);
});

for(const [name,mutate]of [
 ['old origin',s=>{s.configuration.instanceObjectOrigins[s.configuration.instanceObjects[0].name].schema='report-full-semantic-schema-origin-v1';}],
 ['identity changed',s=>{s.configuration.instanceObjects[0].business_identity.explanation='changed';}],
 ['required changed',s=>{s.configuration.instanceObjects[0].fields[0].required=true;}],
 ['unit changed',s=>{s.configuration.instanceObjects[0].fields[1].unit='株';}],
 ['acknowledgement omitted',s=>{delete s.configuration.instanceObjects[0].platform_identity_confirmation;}],
])test('current configuration cannot settle with '+name,async()=>{
 const {f,c}=await previewed();await c.submit('schema',true);const before=f.storage.getItem(c.outboxKey),bad=copy(f.saved);mutate(bad);assert.throws(()=>verifyCurrentAdoptionDraft(bad,c.pending,f.scope));await assert.rejects(c.acceptCurrent(async(_scope,consume)=>consume(bad)));assert.equal(f.storage.getItem(c.outboxKey),before);assert.ok(c.pending.receipt);
});

test('state adoption receipt cannot be retagged and resealed into old family',async()=>{
 const {f,c}=await previewed();await c.submit('schema',true);f.state.adoptions[0].receipt.schema='report-full-semantic-schema-receipt-v1';f.state.adoptions[0].receipt=await seal(f.state.adoptions[0].receipt,'receipt_hash');await assert.rejects(c.load());assert.ok(c.pending.receipt);
});

test('mixed-family retained outbox is not converted, overwritten or resent',async()=>{
 const {f,c}=await previewed();f.mode.before=true;await assert.rejects(c.submit('schema',true));const p=copy(c.pending);p.body.schema='report-full-semantic-schema-command-v1';p.command_hash=await adoptionCommandHash(f.scope,'schema',p.body);f.storage.setItem(c.outboxKey,JSON.stringify(p));const raw=f.storage.getItem(c.outboxKey);c.dispose();assert.throws(()=>new FullSemanticAdoptionController(f.options),/合同与当前设计不同/);assert.equal(f.storage.getItem(c.outboxKey),raw);assert.equal(posts(f).length,1);
});

test('storage failure blocks sending; changed session hides data and retains original request',async()=>{
 const {f,c}=await previewed();f.storage.fail=true;await assert.rejects(c.submit('schema',true));assert.equal(posts(f).length,0);f.storage.fail=false;c.persist();f.mode.before=true;await assert.rejects(c.submit('schema',true));const raw=f.storage.getItem(c.outboxKey);f.tab.setItem('ga-setup-csrf','changed-session');await assert.rejects(c.recover());assert.equal(c.denied,true);assert.equal(c.view,null);assert.equal(f.storage.getItem(c.outboxKey),raw);assert.equal(posts(f).length,1);
});

test('ordinary remount preserves unsent choices but stale preview cannot be submitted',async()=>{
 const {f,c}=await previewed();const old=copy(c.state.previews.schema);c.edit({relationship_names:[]});c.dispose();const next=await f.open();assert.deepEqual(next.state.object_names,f.names);assert.deepEqual(next.state.relationship_names,[]);assert.deepEqual(next.state.previews.schema,old);await assert.rejects(next.submit('schema',true));assert.equal(posts(f).length,0);
});

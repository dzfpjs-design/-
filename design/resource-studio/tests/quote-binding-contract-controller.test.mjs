import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture,identity,memory,emptyForm,privatePending,formalPending,reseal} from './quote-binding-fixture.mjs';
import * as contract from '../src/quote-binding-contract.mjs';
import {QuoteBindingController} from '../src/quote-binding-controller.mjs';
const {copy,same,digest}=contract;
const missing=()=>Object.assign(Error('尚未找到原回执'),{status:404});

test('production empty-PG raw private save, confirmed formal reply and public evidence keep their independent signed contracts',async()=>{
 const f=await fixture();assert.equal(f.synthetic_empty_database,true);assert.equal(f.real_ocr_or_runtime_proof,false);
 const raw=identity(f.raw_reply.draft),pending=await privatePending(f.raw_reply,raw.scope);
 await contract.verifyRegionOptions(f.raw_options,raw.scope);await contract.verifyBindingDraft(f.raw_reply.draft,raw.scope);await contract.verifyBindingReply(f.raw_reply,raw.scope,pending);
 assert.equal(f.raw_options.copies[0].whole_document_confirmed,false);
 const formal=identity(f.formal_private_reply.draft),original=await formalPending(f,formal.scope);
 await contract.verifyRegionOptions(f.formal_options,formal.scope);await contract.verifyBindingReply(f.formal_private_reply,formal.scope,await privatePending(f.formal_private_reply,formal.scope));await contract.verifyRegionSourceReply(f.formal_reply,formal.scope,original);
 const v=f.evidence;await contract.verifyRegionEvidence(v,formal.account,{handoff_id:v.handoff_id,source_review_id:v.source_review_id},{material_id:v.binding.copy_material_id,block_id:v.first_block_id,quote:v.binding.reference.quote,span_binding_id:v.binding_id});
});
test('Unicode codepoint offsets, full interior regions and original decimal boxes cannot be rounded, skipped or silently trimmed',async()=>{
 const f=await fixture(),c=f.raw_options.quotes[0].candidates[0],spans=c.binding.spans,first=c.regions.findIndex(r=>r.id===spans[0].region_id),last=c.regions.findIndex(r=>r.id===spans.at(-1).region_id);
 const actual=await contract.bindingFromRange(c,first,last,spans[0].start,spans.at(-1).end);assert.deepEqual(actual,c.binding);assert.match(actual.reference.quote,/🙂/);
 for(const change of [b=>{const original=b.spans[0].box[0],normalized=String(Number(original));b.spans[0].box[0]=original===normalized?original+'.0':normalized;assert.notEqual(b.spans[0].box[0],original);},b=>{b.spans[0].end++;},b=>{b.spans.reverse();}]){const bad=copy(actual);change(bad);await assert.rejects(contract.verifyBindingText(bad,c.regions));}
 const source=identity(f.raw_reply.draft),form=emptyForm();form.ranges[f.raw_options.quotes[0].reference_hash]={candidate:c,first:last,last:first,start:'尚待填写',end:''};contract.checkLocalBindingForm(form,source.scope); // unfinished human adjustments remain journalable
});
test('an accepted old receipt is still readable when its source is stale; those two currentness flags are independent',async()=>{
 const f=await fixture(),{scope}=identity(f.raw_reply.draft),reply=copy(f.raw_reply),pending=await privatePending(reply,scope);reply.draft.current={matches:false,reason_code:'binding_revision_changed',reason:'页文字已变化，旧稿保留'};await reseal(reply.draft);await contract.verifyBindingReply(reply,scope,pending);assert.equal(reply.current_matches,true);assert.equal(reply.draft.current.matches,false);
 const bad=copy(reply);bad.receipt.request_id=crypto.randomUUID();await reseal(bad.receipt,'receipt_hash');await assert.rejects(contract.verifyBindingReply(bad,scope,pending));
});
test('lost private PUT is exact-journaled before sending; remount probes first and explicit retry retains draft ID, nonce and body',async()=>{
 const f=await fixture(),{account,tab,scope}=identity(f.raw_reply.draft),cache=memory(),original=await privatePending(f.raw_reply,scope),calls=[];let lost=true,stored=false;
 const request=async(path,o={})=>{calls.push([path,o]);if(path==='/me')return account;if(path.endsWith('/receipts/'+original.body.request_id)){if(!stored)throw missing();return copy(f.raw_reply);}if(o.method==='PUT'){const journal=JSON.parse(cache.getItem(contract.bindingPrefix(scope)+'request:'+original.body.request_id));assert.equal(journal.exact_body,o.body);assert.deepEqual(JSON.parse(o.body),original.body);if(lost)throw Error('response unknown');stored=true;return copy(f.raw_reply);}throw Error('unexpected '+path);};
 const a=new QuoteBindingController({account,handoffId:scope.handoff_id,candidateIndex:0,request,tabStorage:tab,storage:cache});await assert.rejects(a.run(original,{fresh:true,retry:true}),/unknown/);const exact=a.commands()[0];a.dispose();
 const b=new QuoteBindingController({account,handoffId:scope.handoff_id,candidateIndex:0,request,tabStorage:tab,storage:cache}),before=calls.filter(([,o])=>o.method==='PUT').length;await assert.rejects(b.run(exact),/尚未查到/);assert.equal(calls.filter(([,o])=>o.method==='PUT').length,before);const other=copy(exact);other.draft_id=crypto.randomUUID();other.body.request_id=crypto.randomUUID();other.exact_body=JSON.stringify(other.body);other.command_hash=await contract.bindingCommandHash(scope,other.draft_id,other.body);await assert.rejects(b.run(other,{fresh:true,retry:true}),/本业务范围/);assert.equal(b.commands().length,1);assert.equal(calls.filter(([,o])=>o.method==='PUT').length,before);lost=false;await b.run(exact,{retry:true});assert.equal(calls.filter(([,o])=>o.method==='PUT').length,before+1);assert.deepEqual(b.commands()[0].body,original.body);assert.equal(b.commands()[0].status,'saved');
});
test('corrupt original body or changed actor/session cannot be sent or re-signed under the current selection',async()=>{
 const f=await fixture(),{account,tab,scope}=identity(f.raw_reply.draft),cache=memory(),row=await privatePending(f.raw_reply,scope),calls=[],controller=new QuoteBindingController({account,handoffId:scope.handoff_id,candidateIndex:0,storage:cache,tabStorage:tab,request:async p=>{calls.push(p);return account;}});controller.writeCommand(row);const bad=copy(row);bad.body.value.bindings=[];bad.exact_body=JSON.stringify(bad.body);await assert.rejects(controller.run(bad,{retry:true}),/摘要已变化/);assert.deepEqual(calls,[]);tab.setItem('ga-setup-csrf','synthetic-other-session');await assert.rejects(controller.run(row,{retry:true}),/会话已变化/);assert.deepEqual(calls,[]);assert.deepEqual(controller.command(controller.commandKey(row)).body,row.body);
});
test('known CAS rejection preserves original outbox and later owner form, without blocking a separately reviewed new nonce',async()=>{
 const f=await fixture(),{account,tab,scope}=identity(f.raw_reply.draft),cache=memory(),row=await privatePending(f.raw_reply,scope),controller=new QuoteBindingController({account,handoffId:scope.handoff_id,candidateIndex:0,storage:cache,tabStorage:tab,request:async(p,o={})=>{if(p==='/me')return account;if(o.method==='PUT')throw Object.assign(Error('版本冲突，原输入保留'),{status:409});throw missing();}}),form=emptyForm();form.copy_ids=[f.raw_options.copies[0].copy_ref.id];controller.journal(form);await assert.rejects(controller.run(row,{fresh:true,retry:true}),/版本冲突/);assert.equal(controller.commands()[0].status,'rejected');assert.deepEqual(controller.owners()[0].form,form);assert.deepEqual(controller.commands()[0].body,row.body);
});
test('formal original GET recovery survives unavailable fresh sources; current_matches false is acknowledged without adopting current configuration',async()=>{
 const f=await fixture(),{account,tab,scope}=identity(f.formal_private_reply.draft),cache=memory(),row=await formalPending(f,scope),reply=copy(f.formal_reply),calls=[];reply.configuration.revision++;reply.current_matches=false;
 const controller=new QuoteBindingController({account,handoffId:scope.handoff_id,candidateIndex:0,storage:cache,tabStorage:tab,request:async(p,o={})=>{calls.push([p,o]);if(p==='/me')return account;if(p.endsWith('/source-review-receipts/'+row.body.request_id))return reply;throw Error('fresh sources unavailable');}});cache.setItem(controller.formalKey(row),JSON.stringify(row));const result=await controller.formalRun({original:row,probeOnly:true});assert.equal(result.reply.current_matches,false);assert.deepEqual(calls.map(([p])=>p),['/me',contract.bindingBase(scope)+'/source-review-receipts/'+row.body.request_id]);controller.settleFormal(result.pending,result.reply);assert.equal(controller.formalCommands()[0].status,'resolved');assert.deepEqual(controller.formalCommands()[0].body,row.body);
});
test('rehashed formal or evidence snapshots cannot swap a source, span, quote or receipt binding',async()=>{
 const f=await fixture(),{account,scope}=identity(f.formal_private_reply.draft),row=await formalPending(f,scope);
 const bad=copy(f.formal_reply);bad.source_review.projection.region_bindings[0].spans[0].region_id='another-region';await reseal(bad.source_review,'record_hash');bad.receipt.record_hash=bad.source_review.record_hash;await reseal(bad.receipt,'receipt_hash');await assert.rejects(contract.verifyRegionSourceReply(bad,scope,row));
 const v=copy(f.evidence);v.binding.reference.quote+='伪';v.binding_id=await digest(v.binding);await reseal(v);await assert.rejects(contract.verifyRegionEvidence(v,account,{handoff_id:v.handoff_id,source_review_id:v.source_review_id},{material_id:f.evidence.binding.copy_material_id,block_id:f.evidence.first_block_id,quote:f.evidence.binding.reference.quote,span_binding_id:f.evidence.binding_id}));
});

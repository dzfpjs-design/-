import test from 'node:test';
import assert from 'node:assert/strict';
import {newUnderstandingRequest,verifyUnderstandingPreview,sourcePosition,understandingRecovery,localUnderstandingProgress} from '../src/setup-understanding-contract.mjs';
import {startPending,writeStartPending,readStartPending,sameStartPending,objectProposals,selectionBody} from '../src/setup-object-proposals.mjs';
const uuid='00000000-0000-4000-8000-000000000001',hash='a'.repeat(64);
const body=()=>({...newUnderstandingRequest({revision:4,model_id:'model',material_ids:['material'],instruction:'保持来源明确范围'},uuid),contract_version:3});
const fixture=()=>{
 const blocks=Array.from({length:31},(_,i)=>({material_id:'material',block_id:'block-'+i,text:'文字第'+i+'段完整保留',location:i===0?{}:{page:i%2+1}}));
 return {contract_version:3,request_id:uuid,revision:4,model_id:'model',materials:[{id:'material',name:'完整资料'}],chunks:[blocks],source_snapshot:{version:'setup-source-snapshot-v1',materials:[{id:'material',blocks:blocks.map(block=>({id:block.block_id,text:block.text,location:{ocr:{image_sha256:'server-only'}}}))}]},projection_contract:{version:'setup-cloud-source-v3',block_fields:['material_id','block_id','text','location'],location_fields:['page','body_index'],single_batch:true},generation_contract:{version:'setup-grounded-v3',task:'遵循全部所选资料',projection_contract_hash:hash,input_policy:{single_batch:true,max_input_bytes:50000,transport_timeout_seconds:60}},input_summary:{material_count:1,block_count:31,batch_count:1,source_text_bytes:1200,source_snapshot_bytes:23000,projection_bytes:6000,prepared_messages_bytes:21000,max_input_bytes:50000,schema_bytes:13000,max_tokens:6000,token_bound:22000,full_text_single_batch:true},...Object.fromEntries(['source_snapshot_hash','projection_contract_hash','projection_hash','generation_contract_hash','output_schema_hash','first_dispatch_messages_hash','preview_hash'].map(key=>[key,hash]))};
};
const v4Fixture=()=>{
 const p=fixture();p.contract_version=4;p.provider='qwen';p.model='qwen-plus';
 p.generation_contract={...p.generation_contract,version:'setup-grounded-v4',dispatch_options:{model:p.model,max_tokens:6000,enable_thinking:false},input_policy:{single_batch:true,version:'cloud-full-report-2026-10-14',max_input_bytes:900000,transport_timeout_seconds:180,max_output_tokens:8192,max_images:0,context_token_bound:1000000,full_report:true,long_context_verified:true}};
 p.input_summary={...p.input_summary,prepared_messages_bytes:180000,max_input_bytes:900000,token_bound:187024};
 p.source_snapshot.materials[0].blocks=p.chunks[0].map(b=>({id:b.block_id,text:b.text,location:{...b.location,ocr:{image_sha256:'server-only'}}}));
 return p;
};
test('v4 accepts the complete long preview, while server limits and explicit Qwen thinking setting stay checked',()=>{
 const request={...body(),contract_version:4},p=v4Fixture();assert.equal(verifyUnderstandingPreview(p,request),p);
 for(const change of [p=>p.generation_contract.input_policy.max_input_bytes=999999999,p=>p.generation_contract.input_policy.transport_timeout_seconds=600,p=>p.generation_contract.dispatch_options.enable_thinking=true,p=>p.generation_contract.dispatch_options.model='different',p=>p.input_summary.token_bound--,p=>p.chunks[0].pop(),p=>p.generation_contract.input_policy.fake_limit=999999]){
  const p=v4Fixture();change(p);assert.throws(()=>verifyUnderstandingPreview(p,request),/完整资料发送范围/);
 }
});
test('v4 rejects reordered, empty or misplaced projections without requiring raw/private text to equal scrubbed text',()=>{
 const request={...body(),contract_version:4};
 for(const change of [p=>p.chunks[0].reverse(),p=>p.chunks[0][30].text='',p=>p.chunks[0][1].location.page=999]){
  const p=v4Fixture();change(p);assert.throws(()=>verifyUnderstandingPreview(p,request),/完整资料发送范围/);
 }
 const scrubbed=v4Fixture();scrubbed.source_snapshot.materials[0].blocks[0].text='联系人 test@example.com';scrubbed.chunks[0][0].text='联系人 [邮箱已移除]';scrubbed.instruction='核对 [个人号码已移除]';
 assert.equal(verifyUnderstandingPreview(scrubbed,request),scrubbed);
});
test('v4 journal, field review and paid local recovery preserve the original nonce without a new cloud request',()=>{
 const p={...v4Fixture(),body:{...body(),contract_version:4}},entries=new Map(),storage={getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,v)};
 const pending=startPending(p);writeStartPending(storage,'v4',pending);assert.deepEqual(readStartPending(storage,'v4'),pending);
 assert.equal(pending.body.request_id,uuid);assert.doesNotMatch(entries.get('v4'),/source_snapshot|chunks|server-only/);
 const object={name:'对象',fields:[]};assert.deepEqual(objectProposals({contract_version:4,result:{objects:[object]}}),[object]);
 assert.equal(understandingRecovery({contract_version:4,status:'failed',recovery:{mode:'local_only'}}).canRetry,true);
 assert.equal(understandingRecovery({contract_version:4,status:'failed',recovery:{mode:'new_preview_required'}}).canRetry,false);
 assert.equal(localUnderstandingProgress({contract_version:4,status:'running',recovery:{mode:'local_only'}}),true);
});
test('new preview request freezes v5 nonce before sending, without source metadata or preview result in the body',()=>{
 const ids=['material'];const request=newUnderstandingRequest({revision:4,model_id:'model',material_ids:ids,instruction:'说明'},uuid);ids.push('later');
 assert.equal(request.contract_version,5);assert.equal(request.request_id,uuid);assert.deepEqual(request.material_ids,['material']);assert.deepEqual(Object.keys(request).sort(),['contract_version','instruction','material_ids','model_id','request_id','revision']);
});
test('31 full text blocks remain in a single compact preview with unchanged source members and order',()=>{
 const preview=fixture(),before=JSON.stringify(preview);assert.equal(verifyUnderstandingPreview(preview,body()),preview);assert.equal(JSON.stringify(preview),before);assert.equal(preview.chunks[0].length,31);assert.equal(preview.chunks[0][30].text,'文字第30段完整保留');
});
test('a partial, duplicated, cross-source, or hidden second batch cannot be shown as a complete v3 preview',()=>{
 for(const change of [
  p=>p.chunks.push([]),p=>p.chunks[0].pop(),p=>{p.chunks[0][30]={...p.chunks[0][0]};},p=>{p.chunks[0][0].material_id='other';},
  p=>{p.source_snapshot.materials[0].blocks.pop();},p=>{p.chunks[0][0]=null;},p=>{p.input_summary.full_text_single_batch=false;},p=>{p.request_id='different';},p=>{p.contract_version=2;}
 ]){const preview=fixture();change(preview);assert.throws(()=>verifyUnderstandingPreview(preview,body()),/完整资料发送范围/);}
});
test('OCR chains, pixels, fragments, or false positions are rejected instead of silently stripped from the authorized projection',()=>{
 for(const change of [p=>{p.chunks[0][0].location.ocr={run:'private'};},p=>{p.chunks[0][0].fragment=2;},p=>{p.chunks[0][0].location.page='1';},p=>{p.chunks[0][0].location.body_index=-1;}]){const preview=fixture();change(preview);assert.throws(()=>verifyUnderstandingPreview(preview,body()),/完整资料发送范围/);}
});
test('real prepared-message capacity, fixed transport policy, and matching projection contract are required',()=>{
 for(const change of [p=>{p.input_summary.prepared_messages_bytes=50001;},p=>{p.input_summary.max_input_bytes=51000;},p=>{p.input_summary.schema_bytes=null;},p=>{p.generation_contract.input_policy.transport_timeout_seconds=120;},p=>{p.generation_contract.projection_contract_hash='b'.repeat(64);},p=>{p.projection_hash=null;}]){const preview=fixture();change(preview);assert.throws(()=>verifyUnderstandingPreview(preview,body()),/完整资料发送范围/);}
 const max=fixture();max.input_summary.prepared_messages_bytes=50000;assert.equal(verifyUnderstandingPreview(max,body()),max);
});
test('v2 preview and pending startup retain their exact original contract, body, nonce, and hash',()=>{
 const legacyBody={...body(),contract_version:2},legacy={body:legacyBody,preview_hash:hash,chunks:[[{location:{ocr:{run:'original-chain'}}}],[{fragment:2}]]};assert.equal(verifyUnderstandingPreview(legacy,legacyBody),legacy);
 const entries=new Map(),storage={getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,v)};const old=startPending(legacy);writeStartPending(storage,'old',old);assert.deepEqual(readStartPending(storage,'old'),old);assert.equal(readStartPending(storage,'old').body.contract_version,2);
 assert.equal(sameStartPending(old,{...old,body:{...old.body,contract_version:3}}),false);assert.throws(()=>writeStartPending(storage,'old',startPending({body:body(),preview_hash:hash})),/另一个页面/);
});
test('v3 journal restores only original request metadata, never the full source snapshot or private OCR chain',()=>{
 const preview={...fixture(),body:body()},entries=new Map(),storage={getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,v)};const pending=startPending(preview);writeStartPending(storage,'new',pending);assert.deepEqual(readStartPending(storage,'new'),pending);assert.equal(readStartPending(storage,'new').body.request_id,uuid);assert.doesNotMatch(entries.get('new'),/server-only|source_snapshot|chunks/);
});
test('v3 structured object review uses v2 selection semantics without upgrading paid v1 results',()=>{
 const object={name:'检测对象',fields:[{label:'名称',kind:'文本',required:true,unit:null}],citations:[],inference_reason:''},record={contract_version:3,result:{objects:[object]}};
 assert.deepEqual(objectProposals(record),[object]);assert.deepEqual(selectionBody(record,{object_names:['检测对象']}),{names:[],object_names:['检测对象'],accept_domain:false});assert.deepEqual(objectProposals({contract_version:1,result:{objects:['检测对象']}}),[]);
});
test('only declared page or body position is readable, and missing positions are never manufactured',()=>{
 assert.equal(sourcePosition({page:2}),'第2页');assert.equal(sourcePosition({body_index:0}),'正文位置 0');assert.equal(sourcePosition({page:2,body_index:5}),'第2页 · 正文位置 5');assert.equal(sourcePosition({ocr:{run:'private'}}),'未记录页码或正文位置');
});
test('paid v3 retry is local-only and failed missing response requires a fresh authorization, while legacy retry stays unchanged',()=>{
 assert.equal(understandingRecovery({contract_version:3,status:'failed',recovery:{mode:'local_only',reason:'仅本地'}}).canRetry,true);
 const missing=understandingRecovery({contract_version:3,status:'failed',recovery:{mode:'new_preview_required',reason:'可能已计费'}});assert.equal(missing.canRetry,false);assert.equal(missing.requiresNewPreview,true);
 assert.equal(understandingRecovery({contract_version:3,status:'running',recovery:{mode:'local_only'}}).canRetry,false);assert.equal(understandingRecovery({contract_version:3,status:'failed'}).canRetry,false);
 for(const contract_version of [1,2])assert.deepEqual(understandingRecovery({contract_version,status:'failed'}),{legacy:true,canRetry:true,requiresNewPreview:false,reason:''});
});

test('local progress requires v3, existing saved response mode and a running lifecycle, never legacy or new cloud requests',()=>{
 for(const status of ['queued','running'])assert.equal(localUnderstandingProgress({contract_version:3,status,recovery:{mode:'local_only'}}),true);
 for(const record of [null,{contract_version:3,status:'running'},{contract_version:3,status:'review',recovery:{mode:'local_only'}},{contract_version:3,status:'running',recovery:{mode:'new_preview_required'}},{contract_version:2,status:'running',recovery:{mode:'local_only'}}])assert.equal(localUnderstandingProgress(record),false);
});

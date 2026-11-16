import test from 'node:test';
import assert from 'node:assert/strict';
import {newUnderstandingRequest,understandingRecovery,verifyUnderstandingPreview} from '../src/setup-understanding-contract.mjs';
import {startPending,writeStartPending,readStartPending,sameStartPending,understandingVersion,legacyExistingUpsert,canApplySelection} from '../src/setup-object-proposals.mjs';
import {editReview,selectedEditOperations,editSelectionProblems,makeEditPending,editJournalKey,readEditPending,writeEditPending,clearEditPending,editReceipt,editRequestState,fencedEditConflict,archiveFencedEditPending} from '../src/setup-edit-contract.mjs';

const hash='a'.repeat(64),uuid='10000000-0000-4000-8000-000000000001',other='10000000-0000-4000-8000-000000000002';
const item=(id,changes={})=>({id,type:'step.update',target_label:'业务 / 核查步骤',field_label:'负责人',before:'旧负责人',after:'新负责人',citations:[],inference_reason:'仅修改负责人',effects:['未改步骤与能力绑定保留'],dependencies:[],blocked_reasons:[],...changes});
const record=(changes={})=>({id:'analysis-a',contract_version:6,edit_mode:true,status:'review',version:3,result_hash:hash,result:{operations:[],questions:[]},edit_review:[item('op-a'),item('op-b',{dependencies:['op-a']})],review_selection:{operation_ids:[],result_hash:hash},edit_receipts:[],...changes});
const memory=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};

test('explicit edit makes v6 while initial understanding and old histories keep their contract',()=>{
 const input={revision:2,model_id:'model',material_ids:['a'],instruction:'只改负责人'};
 assert.equal(newUnderstandingRequest(input,uuid).contract_version,5);
 const body=newUnderstandingRequest({...input,mode:'edit'},uuid);assert.equal(body.contract_version,6);assert.equal(Object.hasOwn(body,'mode'),false);
 assert.equal(startPending({body,preview_hash:hash}).body.contract_version,6);
 const store=memory(),original=startPending({body,preview_hash:hash});writeStartPending(store,'v6',original);assert.deepEqual(readStartPending(store,'v6'),original);assert.equal(sameStartPending(original,{...original,body:{...body,contract_version:5}}),false);
 assert.equal(understandingVersion(record()),6);assert.equal(understandingVersion({contract_version:99}),null);
 assert.throws(()=>newUnderstandingRequest({...input,mode:'other'},uuid));
 assert.throws(()=>verifyUnderstandingPreview({},{...body,contract_version:99}));
 assert.equal(understandingRecovery({contract_version:6,edit_mode:true,status:'failed',recovery:{mode:'local_only'}}).canRetry,true);
 assert.equal(understandingRecovery({contract_version:99,status:'failed'}).canRetry,false);
});

test('v6 preview is fully checked with its own generation and scope hash, never a version-only bypass',()=>{
 const body=newUnderstandingRequest({revision:2,model_id:'model',material_ids:[],instruction:'只改负责人',mode:'edit'},uuid);
 const policy={single_batch:true,version:'cloud-full-report-2026-10-14',max_input_bytes:900000,transport_timeout_seconds:180,max_output_tokens:8192,max_images:0,context_token_bound:1000000,full_report:true,long_context_verified:true};
 const preview={...body,edit_mode:true,provider:'qwen',model:'qwen-plus',edit_scope_hash:hash,materials:[],chunks:[[]],source_snapshot:{version:'setup-source-snapshot-v1',materials:[]},projection_contract:{version:'setup-cloud-source-v3',single_batch:true,block_fields:['material_id','block_id','text','location'],location_fields:['page','body_index']},generation_contract:{version:'setup-edits-v6',task:'只提出指定字段修改',projection_contract_hash:hash,input_policy:policy,dispatch_options:{model:'qwen-plus',max_tokens:2048,enable_thinking:false}},input_summary:{material_count:0,block_count:0,batch_count:1,full_text_single_batch:true,source_text_bytes:0,source_snapshot_bytes:10,projection_bytes:2,prepared_messages_bytes:100,max_input_bytes:900000,schema_bytes:10,max_tokens:2048,token_bound:3172}};
 for(const key of ['source_snapshot_hash','projection_contract_hash','projection_hash','generation_contract_hash','output_schema_hash','first_dispatch_messages_hash','preview_hash'])preview[key]=hash;
 assert.equal(verifyUnderstandingPreview(preview,body),preview);
 for(const change of [copy=>{copy.generation_contract.version='setup-grounded-v5';},copy=>{delete copy.edit_scope_hash;},copy=>{copy.edit_mode=false;},copy=>{copy.edit_handles={private:'mapping'};},copy=>{copy.input_summary.token_bound++;}]){const copy=structuredClone(preview);change(copy);assert.throws(()=>verifyUnderstandingPreview(copy,body));}
});

test('review may persist a partial dependency set; apply never adds hidden choices',()=>{
 const r=record(),ids=['op-b'];assert.deepEqual(editReview(r).map(x=>x.id),['op-a','op-b']);
 assert.deepEqual(makeEditPending('review',r,ids,{request_id:uuid}).body.operation_ids,ids);
 assert.ok(editSelectionProblems(r,ids).some(text=>text.includes('依赖')));assert.deepEqual(ids,['op-b']);
 assert.deepEqual(selectedEditOperations(r),[]);
 assert.throws(()=>editReview(record({edit_review:[item('op-a'),item('op-a')]})));
 assert.throws(()=>makeEditPending('review',r,['missing'],{request_id:uuid}));
 assert.throws(()=>makeEditPending('review',r,['op-a','op-a'],{request_id:uuid}));
 assert.ok(editSelectionProblems(record({edit_review:[item('op-a',{blocked_reasons:['当前来源失效']})]}),['op-a']).some(text=>text.includes('来源失效')));
});

test('apply requires the exact saved review and a distinct nonce',()=>{
 const r=record({review_selection:{operation_ids:['op-a','op-b'],result_hash:hash}});
 const p=makeEditPending('apply',r,['op-a','op-b'],{revision:8,request_id:other});
 assert.deepEqual(p.body,{version:3,revision:8,result_hash:hash,operation_ids:['op-a','op-b'],request_id:other});
 assert.throws(()=>makeEditPending('apply',r,['op-b','op-a'],{revision:8,request_id:other}));
 assert.throws(()=>makeEditPending('apply',record(),['op-a'],{revision:8,request_id:other}));
 assert.throws(()=>selectedEditOperations(record({review_selection:{operation_ids:['op-a'],result_hash:'b'.repeat(64)}})));
});

test('request is durably journaled without replacing a different in-flight body',()=>{
 const storage=memory(),key=editJournalKey({workspace_id:'w',username:'u'},'analysis-a');
 const p=makeEditPending('review',record(),['op-a'],{request_id:uuid});writeEditPending(storage,key,p);assert.deepEqual(readEditPending(storage,key),p);
 assert.throws(()=>writeEditPending(storage,key,makeEditPending('review',record(),[],{request_id:other})));
 assert.equal(clearEditPending(storage,key,{...p,body:{...p.body,request_id:other}}),false);assert.deepEqual(readEditPending(storage,key),p);
 assert.equal(clearEditPending(storage,key,p),true);assert.equal(readEditPending(storage,key),null);
 assert.notEqual(key,editJournalKey({workspace_id:'another',username:'u'},'analysis-a'));
 assert.throws(()=>writeEditPending({getItem:()=>null,setItem(){}},key,p));
 assert.throws(()=>readEditPending({getItem:()=>'{broken'},key));
});

test('receipt recovery binds action/hash/selection and accepts an original receipt with a newer view',()=>{
 const r=record(),p=makeEditPending('review',r,['op-a'],{request_id:uuid});
 const receipt={request_id:uuid,action:'review',status:'review',revision:8,result_hash:hash,operation_ids:['op-a']};
 const latest=record({version:7,status:'applied',edit_receipts:[receipt]});assert.deepEqual(editReceipt(latest,p),receipt);assert.equal(editRequestState(latest,p),'confirmed');
 for(const change of [{action:'apply'},{result_hash:'b'.repeat(64)},{operation_ids:['op-b']},{revision:0}])assert.throws(()=>editReceipt(record({edit_receipts:[{...receipt,...change}]}),p));
 assert.throws(()=>editReceipt(record({edit_receipts:[receipt,receipt]}),p));
 assert.equal(editRequestState(r,p),'retryable');assert.equal(editRequestState(record({version:4}),p),'conflict');
 assert.equal(editReceipt(record({edit_receipts:[{...receipt,request_id:other}]}),p),null);
});

test('undo has a new exact request and never carries arbitrary replacement data',()=>{
 const p=makeEditPending('undo',record({status:'applied',applied_operation_ids:['op-a']}),[],{revision:9,request_id:other});
 assert.deepEqual(p.body,{version:3,revision:9,result_hash:hash,request_id:other});
 assert.deepEqual(p.expected_operation_ids,['op-a']);
 const receipt={request_id:other,action:'undo',status:'undone',revision:10,result_hash:hash,operation_ids:['op-a']};assert.deepEqual(editReceipt(record({status:'undone',edit_receipts:[receipt]}),p),receipt);
 assert.throws(()=>makeEditPending('undo',record(),[],{revision:9,request_id:other}));
 const storage=memory();storage.setItem('pending',JSON.stringify({...p,body:{...p.body,configuration:{}}}));assert.throws(()=>readEditPending(storage,'pending'));
});

test('only undefined legacy versions are compatible; explicit null and mixed edit modes fail closed',()=>{
 assert.equal(understandingVersion({}),1);
 for(const value of [{contract_version:null},{contract_version:6},{contract_version:6,edit_mode:false},{contract_version:5,edit_mode:true},{edit_mode:true}]){
  assert.equal(understandingVersion(value),null);assert.equal(understandingRecovery({...value,status:'failed'}).canRetry,false);
 }
});

test('fenced original request is durably retained with its account and observation before active clear',()=>{
 const account={workspace_id:'w',username:'u'},store=memory(),r=record(),key=editJournalKey(account,r.id),p=makeEditPending('review',r,['op-a'],{request_id:uuid});
 writeEditPending(store,key,p);const latest=record({version:4,review_selection:{operation_ids:['op-b'],result_hash:hash}});
 assert.ok(fencedEditConflict(latest,p));assert.equal(fencedEditConflict(record(),p),null);assert.equal(fencedEditConflict(record({version:4,status:'running'}),p),null);
 const result=archiveFencedEditPending(store,key,p,latest,account);assert.deepEqual(result.operation_ids,['op-b']);assert.equal(readEditPending(store,key),null);
 const retained=JSON.parse(store.getItem(key+':retained:'+uuid));assert.deepEqual(retained.original,p);assert.deepEqual(retained.account,account);assert.equal(retained.observation.old_version,3);assert.equal(retained.observation.current_version,4);assert.equal(retained.observation.original_receipt_absent,true);
});

test('archive failure, changed active request, foreign account and unresolved versions never delete active progress',()=>{
 const account={workspace_id:'w',username:'u'},r=record(),key=editJournalKey(account,r.id),p=makeEditPending('review',r,['op-a'],{request_id:uuid}),latest=record({version:4});
 const store=memory();writeEditPending(store,key,p);
 const broken={...store,setItem(name,value){if(name.includes(':retained:'))throw Error('quota');store.setItem(name,value);}};
 assert.throws(()=>archiveFencedEditPending(broken,key,p,latest,account));assert.deepEqual(readEditPending(store,key),p);
 assert.throws(()=>archiveFencedEditPending(store,key,p,latest,{workspace_id:'foreign',username:'u'}));assert.deepEqual(readEditPending(store,key),p);
 for(const current of [record(),record({version:4,status:'queued'}),record({version:4,status:'running'})])assert.throws(()=>archiveFencedEditPending(store,key,p,current,account));
 const newer=makeEditPending('review',latest,[],{request_id:other});
 const racing={...store,setItem(name,value){store.setItem(name,value);if(name.includes(':retained:'))store.setItem(key,JSON.stringify(newer));}};
 assert.throws(()=>archiveFencedEditPending(racing,key,p,latest,account));assert.deepEqual(readEditPending(store,key),newer);assert.ok(store.getItem(key+':retained:'+uuid));
});

test('old whole-business suggestions cannot update an existing target but new/remove and historical display remain',()=>{
 const business={operation:'upsert',original_name:'现有业务',name:'改名后'},workspace={businesses:['现有业务'],instanceObjects:[]};
 for(const version of [1,2,3,4,5]){
  const r={contract_version:version,status:'review',result:{businesses:[business],objects:[]}};
  assert.equal(legacyExistingUpsert(r,workspace,business),true);assert.equal(canApplySelection(r,workspace,{names:['改名后']}),false);
  assert.equal(legacyExistingUpsert({...r,status:'applied'},workspace,business),false);
  const added={...business,original_name:'',name:'新业务'},removed={...business,operation:'remove'};
  assert.equal(canApplySelection({...r,result:{businesses:[added],objects:[]}},workspace,{names:['新业务']}),true);
  assert.equal(canApplySelection({...r,result:{businesses:[removed],objects:[]}},workspace,{names:['改名后']}),true);
 }
});

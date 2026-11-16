// Synthetic closed DTOs only: no audio decoding, model execution, database ACK or domain-quality proof.
import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {
 checkedSoundClassification,checkedSoundRun,checkedTaskSounds,checkedSoundReception,
 checkedSoundDataReceipt,checkedNativeSoundEntry,checkedNativeSoundRecord,
 checkedSoundReviewReceipt,makeSoundReviewOriginal,soundRunScope
} from '../src/task-sound-result-protocol.mjs';

const priorCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto');
before(()=>{if(!globalThis.crypto?.subtle)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});});
after(()=>{if(priorCrypto)Object.defineProperty(globalThis,'crypto',priorCrypto);else delete globalThis.crypto;});
const id=n=>'25430000-0000-4000-8000-'+String(n).padStart(12,'0');
const hash=c=>String(c).repeat(64),sha=v=>createHash('sha256').update(v).digest('hex');
const clone=v=>structuredClone(v);
const canonical=v=>JSON.stringify(order(v));
function order(v){if(Array.isArray(v))return v.map(order);if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,order(v[k])]));return v;}
const stamp='2026-11-01T04:00:00+00:00';
const labels=Array.from({length:521},(_,i)=>'synthetic_class_'+String(i).padStart(3,'0'));
const scope={actor_id:id(1),workspace_id:id(2),instance_id:id(3),task_id:id(7),release_id:id(8),business_id:'business_sound_dynamic'};
const installation={installation_id:id(10),pack_id:'yamnet-mediapipe-v1',generation:1,recipe_hash:hash('a'),backend:'cpu',model_sha256:hash('b'),labels_sha256:sha(labels.join('\n')+'\n')};

function input(){
 const snapshot={schema:'sound-input-snapshot-v1',id:id(5),workspace_id:scope.workspace_id,instance_id:scope.instance_id,
  owner_id:id(4),evidence_id:id(6),name:'独立保存的原声.wav',media_type:'audio/wav',size_bytes:44,input_sha256:hash('c'),
  configuration:{channel:'mono',threshold:.5},metadata:{status:'not_decoded',original_channels:null,original_sample_rate:null,duration_seconds:null},created_at:stamp};
 return {schema:'sound-input-view-v1',id:snapshot.id,version:1,status:'stored',workspace_id:scope.workspace_id,
  instance_id:scope.instance_id,request_id:id(9),owner_id:snapshot.owner_id,snapshot,snapshot_hash:hash('d'),
  upload:{body_hash:hash('e'),raw_sha256:hash('f')},content_path:'/api/sounds/inputs/'+snapshot.id+'/content',created_at:stamp};
}
function derive(c){
 const selected=new Map();
 for(const w of c.windows){
  const best=Array.from({length:521},(_,i)=>i).sort((a,b)=>w.scores[b]-w.scores[a]||a-b).slice(0,5);
  w.top_classes=best.map(i=>({class_index:i,label:labels[i],score:w.scores[i]}));
  w.scores.forEach((value,i)=>{if(value>=c.threshold){const found=selected.get(i)||{class_index:i,label:labels[i],max_score:value,window_indices:[]};found.max_score=Math.max(found.max_score,value);found.window_indices.push(w.index);selected.set(i,found);}});
 }
 c.candidates=[...selected.values()].sort((a,b)=>a.class_index-b.class_index);
}
function classification(samples=16001){
 const c={sample_rate:16000,window_samples:15600,hop_samples:7680,threshold:.5,top_k:5,
  score_semantics:'uncalibrated_classification_score',timing:'analysis_windows_not_event_boundaries',
  inference_performed:true,all_samples_covered:true,vad_used:false,asr_used:false,domain_quality:'not_verified',windows:[],candidates:[]};
 for(let start=0,index=0;;start+=7680,index++){
  const end=Math.min(samples,start+15600),scores=Array(521).fill(0);scores[index%2?494:76]=index%2?.875:.75;
  c.windows.push({index,start_sample:start,end_sample:end,padded_samples:15600-(end-start),start_seconds:start/16000,end_seconds:end/16000,scores,top_classes:[]});
  if(start+15600>=samples)break;
 }
 derive(c);
 return {schema:'local-environment-sound-v1',deployment:'local',backend:'cpu',cloud_calls:0,input_sha256:hash('c'),recipe_hash:installation.recipe_hash,
  model:{pack_id:installation.pack_id,model_sha256:installation.model_sha256,labels_sha256:installation.labels_sha256,labels_count:521,labels:clone(labels),
   native_input:{name:'waveform_binary',shape:[15600],dtype:'float32'},native_output:{name:'tower0/network/layer32/final_output',shape:[1,521],dtype:'float32'},
   metadata:{name:'yamnet/classification',author:'MediaPipe',version:'1',license:'Apache License. Version 2.0 http://www.apache.org/licenses/LICENSE-2.0.'},
   quantized_tensors:89,weight_precision:'internally_quantized_not_all_fp32',runtime_versions:{'ai-edge-litert':'2.3.0',numpy:'2.5.3',av:'16.1.0',flatbuffers:'synthetic-metadata-only'}},
  audio:{sample_rate:16000,channels:1,samples,duration_seconds:samples/16000,selected_channel:'mono',rms:.1,clipped_fraction:0,peak:.75,
   silence_detected:false,channel_policy:'explicit',noise_quality:'not_measured',source_time:'unknown',analysis_sample_rate:16000,analysis_samples:samples,resampling:'original_16000_no_resample'},
  classification:c,elapsed_seconds:.01};
}
function run(){const saved=input();return {schema:'environment-sound-run-view-v1',id:id(11),version:1,status:'succeeded',task_id:scope.task_id,
 job_id:id(12),invocation_id:id(13),owner_id:id(14),sound_input_id:saved.id,sound_snapshot:saved,installation:clone(installation),
 result:classification(),result_hash:hash('1'),error:null,reviews:[],content_path:'/api/sounds/runs/'+id(11)+'/content',candidate_only:true,physical_state_verified:false};}
function declaredBinding(){
 const output_schema={type:'object',additionalProperties:false,required:['attributes','anomalies','sound_classification','sound_run_id'],
  properties:{attributes:{type:'object',maxProperties:0},anomalies:{type:'array',maxItems:0},sound_classification:{type:'object'},sound_run_id:{type:'string'}}};
 return {business_id:scope.business_id,business_name:'动态声音候选核查',step_id:'classify_sound_dynamic',node_id:null,
  capability_id:id(25),capability_hash:hash('6'),release_version:'1.0.31415926',agent_role:'perception',output_schema,output_schema_hash:sha(canonical(output_schema)),
  source_family:'workflow_environment_sound_candidate_v1',storage:{machine_result:'environment_sound_runs',original:'immutable_evidence',handoff:'observation_and_outbox',
   reception:'independent_candidate_memory',human_work:'versioned_sound_review_and_original_task_receipts'},
  candidate_only:true,score_semantics:'uncalibrated_multilabel',temporal_bounds:'model_windows_not_exact_event_boundaries',source_time_verified:false,object_state_updated:false};
}
function receipt(r){return {schema:'instance-native-sound-data-reception-v1',workspace_id:scope.workspace_id,source_id:id(20),source_hash:hash('2'),
 observation_id:id(21),event_id:id(22),instance_id:scope.instance_id,release_id:scope.release_id,package_id:id(23),configuration_hash:hash('3'),data_plan_hash:hash('4'),
 business_id:scope.business_id,task_id:r.task_id,run_id:r.id,entity_id:id(24),sound_input_id:r.sound_input_id,sound_snapshot_hash:hash('5'),job_id:r.job_id,
 invocation_id:r.invocation_id,capability_id:id(25),capability_hash:hash('6'),input_audit_id:id(26),result_audit_id:id(27),completion_audit_id:id(28),issued_audit_id:id(29),
 packet_hash:hash('7'),event_base_hash:hash('8'),request_hash:hash('9'),result_hash:r.result_hash,output_hash:hash('a'),installation_proof_hash:hash('b'),
 evidence_ids:[r.sound_snapshot.snapshot.evidence_id],registered_at:stamp,registration_day:'2026-11-01',declaration_status:'declared_native_sound_result',
 declared_outputs:[declaredBinding()],producer_status:'succeeded',candidate_only:true,not_authoritative:true,
 source_time_verified:false,physical_state_verified:false,entity_state_updated:false,can_execute:false,can_delete:false,receipt_hash:hash('c')};}
function reception(r,state='waiting_data'){
 const got=state==='received';return {schema:'native-sound-reception-status-v1',run_id:r.id,observation_id:got?id(21):null,event_id:got?id(22):null,
  memory_id:got?id(30):null,state,receipt:got?receipt(r):null,integrity_issue:['blocked_integrity','producer_failed'].includes(state)?'原结果需核对':null};
}
function entry(state='waiting_data'){
 const r=run(),available=state==='received';if(!available)r.result=null;
 return {run:r,entity_id:id(24),reception:reception(r,state),data_values_available:available,candidate_only:true,not_authoritative:true,physical_state_verified:false};
}
function task(access={allowed:false,reason:'当前步骤由另一个指定岗位办理',current_step_name:'原音核查'}){
 const r=run();return {schema:'task-sound-results-v1',task_id:r.task_id,instance_id:scope.instance_id,release_id:scope.release_id,
  business_id:scope.business_id,sound_snapshots:[clone(r.sound_snapshot)],records:[{run:r,reception:reception(r)}],review_access:access};
}

for(const samples of [4000,15600,15601,16001,960000])test('peer sound accepts all original support including the bounded tail '+samples,async()=>{
 const value=classification(samples),before=canonical(value),checked=await checkedSoundClassification(value,input(),installation);
 assert.equal(canonical(value),before);assert.deepEqual(checked,value);assert.notEqual(checked,value);
 const windows=checked.classification.windows;assert.equal(windows.at(-1).end_sample,samples);
 assert.ok(windows.every(row=>row.scores.length===521));assert.equal(windows.at(-1).padded_samples,15600-(windows.at(-1).end_sample-windows.at(-1).start_sample));
});
for(const [name,damage]of [
 ['520 labels',v=>v.model.labels.pop()],['duplicate labels',v=>v.model.labels[520]=v.model.labels[0]],
 ['reordered original labels',v=>v.model.labels.reverse()],['wrong raw score hash declaration',v=>v.model.labels_sha256=hash('f')],
 ['native rank invented',v=>v.model.native_input.shape=[1,15600]],['all FP32 falsely claimed',v=>v.model.weight_precision='all_fp32'],
 ['one raw class missing',v=>v.classification.windows[0].scores.pop()],['NaN score',v=>v.classification.windows[0].scores[0]=NaN],
 ['boolean score',v=>v.classification.windows[0].scores[0]=true],['not original float32',v=>v.classification.windows[0].scores[0]=.1],
 ['missing tail window',v=>v.classification.windows.pop()],['extra padded window',v=>v.classification.windows.push(clone(v.classification.windows.at(-1)))],
 ['tail support gap',v=>v.classification.windows[1].start_sample+=1],['wrong final padding',v=>v.classification.windows[1].padded_samples-=1],
 ['changed score but stale top classes',v=>v.classification.windows[0].scores[100]=.875],
 ['changed aggregate but unchanged raw scores',v=>v.classification.candidates[0].max_score=.875],
 ['calibrated risk probability claim',v=>v.classification.score_semantics='calibrated_risk_probability'],
 ['exact event boundary claim',v=>v.classification.timing='exact_event_boundaries'],['VAD filtering',v=>v.classification.vad_used=true],
 ['ASR filtering',v=>v.classification.asr_used=true],['silent skip',v=>v.classification.inference_performed=false],
 ['field quality falsely verified',v=>v.classification.domain_quality='verified'],['recording clock invented',v=>v.audio.source_time=stamp],
 ['different original audio',v=>v.input_sha256=hash('f')],['different recipe',v=>v.recipe_hash=hash('f')]
])test('peer sound rejects '+name,async()=>{const value=classification();damage(value);await assert.rejects(checkedSoundClassification(value,input(),installation));});

test('peer sound keeps raw float32 scores while applying a stricter neighboring user threshold',async()=>{
 const value=classification(4000),saved=input(),score=Math.fround(.1),bits=new DataView(new ArrayBuffer(8));bits.setFloat64(0,score);bits.setBigUint64(0,bits.getBigUint64(0)+1n);const threshold=bits.getFloat64(0);
 saved.snapshot.configuration.threshold=threshold;value.classification.threshold=threshold;value.classification.windows[0].scores.fill(0);value.classification.windows[0].scores[76]=score;derive(value.classification);
 const checked=await checkedSoundClassification(value,saved,installation);assert.deepEqual(checked.classification.candidates,[]);assert.equal(checked.classification.windows[0].scores[76],score);
});

test('peer sound permits distinct uploader, processing executor and current review actor',async()=>{
 const value=task();assert.notEqual(value.records[0].run.owner_id,value.sound_snapshots[0].owner_id);assert.notEqual(scope.actor_id,value.records[0].run.owner_id);
 const checked=await checkedTaskSounds(value,scope);assert.equal(checked.review_access.allowed,false);
});
for(const [name,damage]of [
 ['wrong Task',v=>v.task_id=id(80)],['wrong processing Task',v=>v.records[0].run.task_id=id(80)],
 ['wrong input ID',v=>v.records[0].run.sound_input_id=id(80)],['wrong original workspace',v=>v.records[0].run.sound_snapshot.workspace_id=id(80)],
 ['wrong original instance',v=>v.records[0].run.sound_snapshot.instance_id=id(80)],
 ['different frozen input body',v=>v.records[0].run.sound_snapshot.snapshot.name='替换后的原音.wav'],
 ['same Run repeated',v=>v.records.push(clone(v.records[0]))],['result bytes absent for normal Task result',v=>v.records[0].run.result=null],
 ['field truth claim',v=>v.records[0].run.physical_state_verified=true]
])test('peer Task sound rejects '+name,async()=>{const value=task();damage(value);await assert.rejects(checkedTaskSounds(value,scope));});

test('peer review access uses the actual server decision, not an admin-role shortcut',async()=>{
 const denied=await checkedTaskSounds(task(),{...scope,role:'admin'});assert.equal(denied.review_access.allowed,false);assert.match(denied.review_access.reason,/指定岗位/);
 const allowed=await checkedTaskSounds(task({allowed:true,reason:null,current_step_name:'指定人播放核查'}),{...scope,role:'operator'});assert.equal(allowed.review_access.allowed,true);
 const absent=task();delete absent.review_access;await assert.rejects(checkedTaskSounds(absent,{...scope,role:'admin'}));
});

test('peer data view keeps all pending scores redacted while Task view can show candidate-only model output',async()=>{
 const pending=entry();const checked=await checkedNativeSoundEntry(pending,scope);assert.equal(checked.data_values_available,false);assert.equal(checked.run.result,null);assert.equal(checked.reception.receipt,null);
 const modelView=await checkedTaskSounds(task(),scope);assert.equal(modelView.records[0].reception.state,'waiting_data');assert.equal(modelView.records[0].run.candidate_only,true);
 assert.equal(modelView.records[0].run.result.classification.score_semantics,'uncalibrated_classification_score');
});
for(const state of ['waiting_data','blocked_integrity','producer_failed'])test('peer '+state+' cannot claim received data values or expose a numeric payload',async()=>{
 const value=entry(state);assert.equal((await checkedNativeSoundEntry(value,scope)).data_values_available,false);
 const numeric=clone(value);numeric.run.result=classification();await assert.rejects(checkedNativeSoundEntry(numeric,scope));
 const claimsReceived=clone(value);claimsReceived.data_values_available=true;claimsReceived.run.result=classification();await assert.rejects(checkedNativeSoundEntry(claimsReceived,scope));
});

test('peer received data requires the full linked receipt, observation Event and Memory identities',async()=>{
 const checked=await checkedNativeSoundEntry(entry('received'),scope);assert.equal(checked.data_values_available,true);assert.equal(checked.reception.state,'received');
 assert.equal(checked.reception.receipt.observation_id,checked.reception.observation_id);assert.equal(checked.reception.receipt.event_id,checked.reception.event_id);
 assert.ok(checked.reception.memory_id);assert.equal(checked.reception.receipt.can_execute,false);assert.equal(checked.reception.receipt.can_delete,false);
});
for(const [name,damage]of [
 ['empty declared binding',v=>v.reception.receipt.declared_outputs=[{}]],
 ['declaration for another business',v=>v.reception.receipt.declared_outputs[0].business_id='another_business'],
 ['declaration for another capability',v=>v.reception.receipt.declared_outputs[0].capability_id=id(84)],
 ['declaration for another capability hash',v=>v.reception.receipt.declared_outputs[0].capability_hash=hash('f')],
 ['declaration borrows ASR source',v=>v.reception.receipt.declared_outputs[0].source_family='audio_transcription'],
 ['declaration stores machine data as ASR',v=>v.reception.receipt.declared_outputs[0].storage.machine_result='audio_runs'],
 ['declaration omits human-work storage',v=>delete v.reception.receipt.declared_outputs[0].storage.human_work],
 ['declaration expands object-state permission',v=>v.reception.receipt.declared_outputs[0].object_state_updated=true],
 ['declaration pretends calibrated risk',v=>v.reception.receipt.declared_outputs[0].score_semantics='calibrated_risk_probability'],
 ['declaration pretends exact events',v=>v.reception.receipt.declared_outputs[0].temporal_bounds='exact_event_boundaries'],
 ['duplicate declared business-step-node',v=>v.reception.receipt.declared_outputs.push(clone(v.reception.receipt.declared_outputs[0]))],
 ['undeclared additional binding field',v=>v.reception.receipt.declared_outputs[0].can_execute=true],
 ['missing independent receipt',v=>v.reception.receipt=null],['missing Memory',v=>v.reception.memory_id=null],
 ['invalid Memory',v=>v.reception.memory_id='not-a-resource'],['missing Event',v=>v.reception.event_id=null],
 ['wrong linked Event',v=>v.reception.event_id=id(81)],['wrong linked Observation',v=>v.reception.observation_id=id(81)],
 ['wrong receipt Run',v=>v.reception.receipt.run_id=id(81)],['wrong receipt input',v=>v.reception.receipt.sound_input_id=id(81)],
 ['wrong receipt workspace',v=>v.reception.receipt.workspace_id=id(81)],['wrong receipt original Evidence',v=>v.reception.receipt.evidence_ids=[id(81)]],
 ['wrong receipt result hash',v=>v.reception.receipt.result_hash=hash('f')],['wrong receipt entity',v=>v.reception.receipt.entity_id=id(81)],
 ['missing independent producer audit',v=>v.reception.receipt.completion_audit_id=null],['missing receipt hash',v=>v.reception.receipt.receipt_hash=null],
 ['claimed field fact',v=>v.reception.receipt.entity_state_updated=true],['claimed recording time',v=>v.reception.receipt.source_time_verified=true],
 ['permission smuggling',v=>v.reception.receipt.can_execute=true],['received values hidden without explanation',v=>{v.data_values_available=false;v.run.result=null;}]
])test('peer received entry rejects '+name,async()=>{const value=entry('received');damage(value);await assert.rejects(checkedNativeSoundEntry(value,scope));});

test('peer receipt verifies explicit authorization release and business without borrowing new scope',()=>{
 const r=run(),saved=receipt(r);assert.deepEqual(checkedSoundDataReceipt(saved,r,scope),saved);
 assert.throws(()=>checkedSoundDataReceipt(saved,r,{...scope,release_id:id(82)}));
 assert.throws(()=>checkedSoundDataReceipt(saved,r,{...scope,business_id:'other_business'}));
 assert.throws(()=>checkedSoundReception(reception(r,'received'),r,{...scope,instance_id:id(82)}));
});

test('peer ordinary data row cannot turn pending sound into an effective received value',async()=>{
 const value=entry();const outer={id:id(21),created_at:stamp,received_day:'2026-11-01',entity_id:value.entity_id,integrity_error:null,data_received:false,
  record:{schema:'native-sound-receipt-record-v1',source_key:'EnvironmentSoundRun:'+value.run.id,name:'候选环境声音',review_state:'unreviewed',date_basis:'machine_result_registered',recording_time_verified:false,candidate_only:true,entity_state_updated:false},native_sound_result_v1:value};
 assert.equal((await checkedNativeSoundRecord(outer,scope)).data_received,false);
 outer.data_received=true;await assert.rejects(checkedNativeSoundRecord(outer,scope));
});

test('peer saved human receipt binds exact original note, actor, Run and machine result',async()=>{
 const r=run(),account={user_id:scope.actor_id,workspace_id:scope.workspace_id},reviewScope=soundRunScope(account,scope.instance_id,scope.task_id,r.id);
 const body={schema:'sound-review-v1',request_id:'review_sound_original_01',version:r.version,verdict:'uncertain',note:'听到相关声音，但当前样本不足以认定现场异常。'};
 const original=await makeSoundReviewOriginal(reviewScope,body,r);
 const value={schema:'sound-review-receipt-v1',run_id:r.id,task_id:r.task_id,request_id:body.request_id,actor_id:scope.actor_id,expected_version:1,version:2,
  verdict:body.verdict,note:body.note,result_hash:r.result_hash,input_snapshot_hash:r.sound_snapshot.snapshot_hash,saved_at:stamp};value.receipt_hash=sha(canonical(value));
 assert.deepEqual(await checkedSoundReviewReceipt(value,r,original),value);
 for(const [field,replacement]of [['actor_id',id(83)],['note','擅自替换的结论'],['result_hash',hash('f')],['input_snapshot_hash',hash('f')]]){
  const bad={...value,[field]:replacement};const{receipt_hash,...unsigned}=bad;bad.receipt_hash=sha(canonical(unsigned));await assert.rejects(checkedSoundReviewReceipt(bad,r,original));
 }
});

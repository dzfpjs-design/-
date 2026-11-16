import test from 'node:test';
import assert from 'node:assert/strict';
import {appendEvidenceStructure,structureAvailability} from '../src/report-evidence-structure.mjs';
import {evidenceManualValidationView} from '../src/report-evidence-manual-v5.mjs';

// Inline synthetic manual values only. No model, source authority or save receipt.
const clone=structuredClone,reason=' 人工补充理由末字🙂 \n';
const evidence=()=>({status:'unknown',citations:[],explanation:'原含义待核'});
const step=(name,next_index=null)=>({name,purpose:' 原目的 \n',agent:'人工确认',role:'核对职责',output:' 原产出 ',branch_condition:'',citations:[{span_ref:'w:p1/s1'}],inference_reason:'原步骤待核',next_index,else_index:null});
function manual(){
 const business={operation:'upsert',original_name:'',name:'合成业务',trigger:'原触发',output:'原产出',steps:[step('原步骤甲',1),step('原步骤乙',0)],citations:[{span_ref:'w:p1/s1'}],inference_reason:'原依据',
  participants:[{agent:'参与甲',responsibility:'原职责甲',evidence:evidence()},{agent:'参与乙',responsibility:'原职责乙',evidence:evidence()}],
  interactions:[{from_participant_index:0,to_participant_index:1,purpose:'原交接',condition:' 原条件 ',step_indices:[0,1],evidence:evidence()}],
  conditions:[{kind:'exception',statement:'原异常',step_indices:[1],evidence:evidence()}],
  coordination:[{kind:'parallel',step_indices:[0,1],start_rule:'原开始',wait_rule:'原等待',join_rule:'原汇合',evidence:evidence()}]};
 return {schema:'report-evidence-manual-review-v5',note:' 原人工说明末字🙂 \n',result:{schema:'report-evidence-candidate-wire-v5',source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),domain:' 原领域 ',
  global_claims:[{kind:'治理',statement:' 原独立限制 ',span_refs:['w:p1/s1'],status:'explicit',explanation:''}],objects:[],relationships:[],businesses:[business],questions:[' 原待核问题 ']}};
}
const payloads={business:()=>({name:'人工补充业务',reason}),step:()=>({name:'人工补充步骤',purpose:'人工目的',output:'人工产出',agent:'人工确认',role:'人工职责',next_index:0,reason}),
 participant:()=>({agent:'人工补充参与方',responsibility:'人工职责',reason}),interaction:()=>({from_participant_index:1,to_participant_index:0,purpose:'人工交接目的',condition:'',step_indices:[1,0],reason}),
 condition:()=>({kind:'stop',statement:'人工补充停止条件',step_indices:[1],reason}),coordination:()=>({kind:'branch',start_rule:'人工开始规则',wait_rule:'人工等待规则',join_rule:'人工汇合规则',step_indices:[1,0],reason})};
const collection={business:'businesses',step:'steps',participant:'participants',interaction:'interactions',condition:'conditions',coordination:'coordination'};
function command(kind,payload=payloads[kind]()){return {kind,...(kind==='business'?{}:{businessIndex:0}),payload};}
function freeze(value){if(value&&typeof value==='object'){Object.freeze(value);Object.values(value).forEach(freeze);}return value;}
function rejected(value,cmd,pattern){const before=JSON.stringify(value),input=JSON.stringify(cmd);assert.throws(()=>appendEvidenceStructure(value,cmd),pattern);assert.equal(JSON.stringify(value),before);assert.equal(JSON.stringify(cmd),input);}

test('all six append kinds retain every earlier semantic byte value, local index, question and note',()=>{
 for(const kind of Object.keys(payloads)){
  const value=freeze(manual()),before=JSON.stringify(value),cmd=freeze(command(kind)),next=appendEvidenceStructure(value,cmd);
  const expected=clone(value),items=kind==='business'?expected.result.businesses:expected.result.businesses[0][collection[kind]],actual=kind==='business'?next.result.businesses:next.result.businesses[0][collection[kind]];
  items.push(clone(actual.at(-1)));expected.result.questions.push(reason);
  assert.deepEqual(next,expected);assert.equal(JSON.stringify(value),before);assert.notEqual(next,value);assert.notEqual(next.result,value.result);assert.equal(next.note,value.note);
  evidenceManualValidationView(next);assert.equal(next.result.questions.at(-1),reason);
  if(!['business','step'].includes(kind))assert.deepEqual(actual.at(-1).evidence,{status:'unknown',citations:[],explanation:reason});
 }
});
test('new business is an honest partial definition and no source fields, nodes or roles are invented',()=>{
 const value=manual(),next=appendEvidenceStructure(value,command('business')),b=next.result.businesses.at(-1);
 assert.deepEqual(b,{operation:'upsert',original_name:'',name:'人工补充业务',trigger:'',output:'',steps:[],citations:[],inference_reason:reason,participants:[],interactions:[],conditions:[],coordination:[]});
 assert.deepEqual(value.result.businesses[0],next.result.businesses[0]);
 rejected(value,command('business',{name:'合成业务',reason}),/同名/);
 rejected(value,command('business',{name:' 新业务 ',reason}),/首尾空白/);
 const partial=clone(next);rejected(partial,{...command('step'),businessIndex:1},/启动条件与产出/);
 assert.equal(structureAvailability(partial,'step',1).allowed,false);
 partial.result.businesses[1].trigger='人工核对的启动条件';partial.result.businesses[1].output='人工核对的业务产出';
 const payload=payloads.step();payload.next_index=null;assert.equal(appendEvidenceStructure(partial,{...command('step',payload),businessIndex:1}).result.businesses[1].steps.length,1);
});
test('step target is explicitly supplied, keeps cycles and never rewires a previous step',()=>{
 const value=manual(),next=appendEvidenceStructure(value,command('step'));
 assert.deepEqual(next.result.businesses[0].steps.slice(0,2),value.result.businesses[0].steps);assert.equal(next.result.businesses[0].steps[2].next_index,0);assert.equal(next.result.businesses[0].steps[2].else_index,null);
 const end=payloads.step();end.next_index=null;assert.equal(appendEvidenceStructure(value,command('step',end)).result.businesses[0].steps[2].next_index,null);
 const missing=payloads.step();delete missing.next_index;rejected(value,command('step',missing));
 for(const target of [undefined,false,true,'0',-1,-0,0.5,2,NaN,Infinity]){const p=payloads.step();p.next_index=target;rejected(value,command('step',p));}
});
test('human role and human-entered content are required without trimming or placeholder repair',()=>{
 const value=manual();for(const [kind,key] of [['business','name'],['step','name'],['step','purpose'],['step','output'],['step','role'],['participant','agent'],['participant','responsibility'],['interaction','purpose'],['condition','statement']]){
  const p=payloads[kind]();p[key]=' \n';rejected(value,command(kind,p));
 }
 for(const kind of Object.keys(payloads)){const p=payloads[kind]();p.reason=' \t';rejected(value,command(kind,p));}
 const p=payloads.step();p.agent='数据管理智能体';p.role='';const next=appendEvidenceStructure(value,command('step',p));assert.equal(next.result.businesses[0].steps.at(-1).role,'');
 p.agent='看似合理的新角色';rejected(value,command('step',p));
});
test('interaction and step selections are strict, unique and confined to existing local arrays',()=>{
 const value=manual();value.result.businesses.push({...clone(value.result.businesses[0]),name:'另一合成业务'});
 for(const bad of [false,'0',-0,-1,0.5,2,null]){const p=payloads.interaction();p.from_participant_index=bad;rejected(value,command('interaction',p));}
 const same=payloads.interaction();same.to_participant_index=same.from_participant_index;rejected(value,command('interaction',same));
 for(const kind of ['interaction','condition','coordination'])for(const indices of [[0,0],[2],[false],['0'],[-0],null]){const p=payloads[kind]();p.step_indices=indices;rejected(value,command(kind,p));}
 for(const index of [undefined,null,false,'0',-0,-1,0.5,2])rejected(value,{...command('condition'),businessIndex:index});
});
test('known coordination needs two declared steps but unknown coordination may retain no inferred edges',()=>{
 const value=manual();for(const kind of ['sequence','branch','parallel']){const p=payloads.coordination();p.kind=kind;p.step_indices=[0];rejected(value,command('coordination',p));}
 const p=payloads.coordination();p.kind='unspecified';p.step_indices=[];p.start_rule='';p.wait_rule='';p.join_rule='';
 const next=appendEvidenceStructure(value,command('coordination',p));assert.deepEqual(next.result.businesses[0].coordination.at(-1),{kind:'unspecified',step_indices:[],start_rule:'',wait_rule:'',join_rule:'',evidence:{status:'unknown',citations:[],explanation:reason}});
});
test('closed commands and payloads reject raw model schemas, proof fields and invented identifiers',()=>{
 const value=manual();for(const kind of Object.keys(payloads)){for(const field of ['key','citations','status','evidence','extra']){const p=payloads[kind]();p[field]='not accepted';rejected(value,command(kind,p));}}
 rejected(value,{...command('step'),projection_hash:'c'.repeat(64)});rejected(value,{kind:'remove',payload:{reason}});rejected(value,{kind:'business',businessIndex:0,payload:payloads.business()});
 for(const schema of ['report-evidence-candidate-projection-v6','report-evidence-candidate-wire-v6','report-evidence-reading-working-v1']){const bad=manual();bad.schema=schema;rejected(bad,command('business'));}
 const bad=manual();bad.result.businesses[0].steps[0].next_index=99;rejected(bad,command('participant'));
});
test('every collection cap and the shared question cap reject the whole append without clipping',()=>{
 for(const [kind,limit] of [['business',20],['step',16],['participant',40],['interaction',40],['condition',40],['coordination',40]]){
  const value=manual(),items=kind==='business'?value.result.businesses:value.result.businesses[0][collection[kind]],template=clone(items[0]);
  while(items.length<limit){const item=clone(template);if(kind==='business')item.name='合成业务'+items.length;items.push(item);}
  const availability=structureAvailability(value,kind,kind==='business'?undefined:0);assert.equal(availability.allowed,false);assert.equal(availability.count,limit);assert.equal(availability.limit,limit);rejected(value,command(kind));
 }
 const value=manual();value.result.questions=Array.from({length:30},(_,i)=>'原问题'+i);rejected(value,command('participant'),/30/);assert.equal(value.result.questions.length,30);
 const valid=manual();valid.result.questions=Array.from({length:29},(_,i)=>'原问题'+i);assert.equal(appendEvidenceStructure(valid,command('participant')).result.questions.length,30);
});
test('reason and scalar limits are exact, preserve supplementary characters and reject invalid Unicode',()=>{
 const value=manual();for(const [kind,max] of [['business',500],['step',500],['participant',1000],['interaction',1000],['condition',1000],['coordination',1000]]){
  const p=payloads[kind]();p.reason='🙂'.repeat(max);assert.equal(appendEvidenceStructure(value,command(kind,p)).result.questions.at(-1),p.reason);p.reason+='x';rejected(value,command(kind,p));
 }
 for(const reasonValue of [null,1,false,'坏\u0000字','坏\ud800']){const p=payloads.participant();p.reason=reasonValue;rejected(value,command('participant',p));}
});
test('whole-manual byte capacity is enforced after append without losing the pending reason',()=>{
 const value=manual(),bytes=v=>new TextEncoder().encode(JSON.stringify(v)).length;
 value.result.businesses=Array.from({length:20},(_,bi)=>({...clone(value.result.businesses[0]),name:'合成业务'+bi,
  steps:Array.from({length:16},(_,si)=>step('合成步骤'+si,si%2?si-1:si+1))}));
 const target=512*1024-100;
 for(const b of value.result.businesses)for(const s of b.steps)for(const key of ['purpose','output']){
  const remaining=target-bytes(value);if(remaining>0)s[key]+='x'.repeat(Math.min(1000-Array.from(s[key]).length,remaining));
 }
 assert.equal(bytes(value),target);evidenceManualValidationView(value);
 const p=payloads.participant();p.reason='人'.repeat(1000);rejected(value,command('participant',p),/512 KiB/);
});
test('availability is read-only and a failed append cannot poison later human edits',()=>{
 const value=freeze(manual()),before=JSON.stringify(value);assert.deepEqual(structureAvailability(value,'participant',0),{allowed:true,reason:'',count:2,limit:40});
 assert.equal(structureAvailability(value,'unknown',0).allowed,false);assert.equal(structureAvailability(value,'step',99).allowed,false);assert.equal(structureAvailability(null,'business').allowed,false);
 const bad=payloads.step();bad.role='';rejected(value,command('step',bad));const next=appendEvidenceStructure(value,command('participant'));next.result.businesses[0].participants[0].responsibility='后续人工修改';assert.equal(JSON.stringify(value),before);assert.equal(value.result.businesses[0].participants[0].responsibility,'原职责甲');
});

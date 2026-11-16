import test from 'node:test';
import assert from 'node:assert/strict';
import {evidenceManualFromProjectionV6} from '../src/report-evidence-manual-v6-adapter.mjs';
import {evidenceManualFromProjection,evidenceManualValidationView} from '../src/report-evidence-manual-v5.mjs';

// Public structural fixture, NOT a provider answer, verified journal, source
// proof, API/permission test or semantic/model-quality result. The pure Python
// companion additionally obtains its fixture from the real V6 projection gate.
// The synthetic record's required text field explicitly satisfies FullObject's
// existing structural gate; it is not extracted from source or a model answer.
// Its original optional numeric field stays at index 0 with its original unit.
const clone=structuredClone,id=(prefix,n)=>prefix+'_'+n.toString(16).padStart(32,'0');
const cites=()=>[{span_ref:'w:p1/s1'},{span_ref:'o:p192/s2'}];
const evidence=()=>({status:'explicit',citations:cites(),explanation:' 原逐字依据仍需核对🙂\n'});
function projection(){
 const businesses=[0,1].map(bi=>{
  const offset=bi*20,steps=[id('st',offset+1),id('st',offset+2)],people=[id('pa',offset+3),id('pa',offset+4)];
  return {operation:'upsert',original_name:'',name:bi?'合成核对乙':'合成核对甲',trigger:' 收到资料\n',output:' 留存原记录 ',
   steps:steps.map((key,i)=>({key,name:i?'核对':'登记',purpose:' 原职责🙂\n 保留字序与空白 ',agent:'人工确认',role:i?'核对员':'登记员',output:'原处理说明',next_key:i?(bi?steps[0]:'end'):steps[1],branch_condition:'',else_key:'end',citations:cites(),inference_reason:' 尚待核对 '})),
   citations:cites(),inference_reason:' 原推断说明 ',participants:people.map((key,i)=>({key,agent:i?'核对员':'登记员',responsibility:' 跨业务原职责，不授予账号权限 ',evidence:evidence()})),
   interactions:[{key:id('in',offset+5),from_participant:people[0],to_participant:people[1],purpose:' 原资料交接 ',condition:' 原交接条件\n',step_keys:steps.slice(),evidence:evidence()}],
   conditions:[{key:id('co',offset+6),kind:'precondition',statement:' 原开始条件 ',step_keys:[steps[0]],evidence:evidence()}],
   coordination:[{key:id('cd',offset+7),kind:'parallel',step_keys:steps.slice(),start_rule:' 原并行说明，未证明可执行 ',wait_rule:' 原等待说明 ',join_rule:' 原汇合说明 ',evidence:evidence()}]};
 });
 return {schema:'report-evidence-candidate-projection-v6',source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),domain:' 合成领域🙂\n保留全角＋与+ ',
  global_claims:[{key:id('gc',1),kind:'职责',statement:' 跨业务全局原职责 ',span_refs:['w:p1/s1','o:p192/s2'],status:'explicit',explanation:' '},{key:id('gc',2),kind:'治理',statement:' 全局含义未知 ',span_refs:[],status:'unknown',explanation:' 原文仍需核对 '}],
  objects:[{name:'材料',fields:[{label:'名称',kind:'文本',required:true,unit:null,citations:cites(),inference_reason:''}],citations:cites(),inference_reason:''},{name:'记录',fields:[{label:'数量',kind:'整数',required:false,unit:'株',citations:cites(),inference_reason:' 原单位说明 '},{label:'合成记录识别名',kind:'文本',required:true,unit:null,citations:cites(),inference_reason:'仅为公开结构测试构造，不证明原文定义'}],citations:cites(),inference_reason:''}],
  relationships:[{name:'材料关联记录',description:' 原关联含义待核 ',source_type:'材料',target_type:'记录',cardinality:'unspecified',citations:cites(),inference_reason:' 不推定唯一性 '}],businesses,questions:[' 未知含义待核，未自动批准。 ']};
}
function freeze(value){if(value&&typeof value==='object'){Object.freeze(value);Object.values(value).forEach(freeze);}return value;}
function fieldNames(value,result=[]){if(value&&typeof value==='object')for(const [name,item] of Object.entries(value)){result.push(name);fieldNames(item,result);}return result;}

test('V6 adapter retains the complete semantic value and Unicode without relabelling or mutating its original model',()=>{
 const original=freeze(projection()),before=JSON.stringify(original),note=' 私人末字🙂\n ',value=evidenceManualFromProjectionV6(original,note);
 assert.deepEqual(Object.keys(value).sort(),['note','result','schema']);assert.equal(value.schema,'report-evidence-manual-review-v5');assert.equal(value.result.schema,'report-evidence-candidate-wire-v5');assert.equal(value.note,note);
 // Independent equivalence oracle: the old function is used only on this
 // explicitly labelled internal structural view, never on original wire.
 const internal=clone(original);internal.schema='report-evidence-candidate-projection-v5';assert.deepEqual(value,evidenceManualFromProjection(internal,note));
 for(const name of ['source_hash','catalog_hash','domain','objects','relationships','questions'])assert.deepEqual(value.result[name],original[name]);
 for(let bi=0;bi<2;bi++){
  const b=value.result.businesses[bi],o=original.businesses[bi];
  for(const name of ['operation','original_name','name','trigger','output','citations','inference_reason'])assert.deepEqual(b[name],o[name]);
  for(let si=0;si<2;si++)for(const name of ['name','purpose','agent','role','output','branch_condition','citations','inference_reason'])assert.deepEqual(b.steps[si][name],o.steps[si][name]);
  for(let pi=0;pi<2;pi++)for(const name of ['agent','responsibility','evidence'])assert.deepEqual(b.participants[pi][name],o.participants[pi][name]);
  for(const name of ['purpose','condition','evidence'])assert.deepEqual(b.interactions[0][name],o.interactions[0][name]);
  for(const name of ['kind','statement','evidence'])assert.deepEqual(b.conditions[0][name],o.conditions[0][name]);
  for(const name of ['kind','start_rule','wait_rule','join_rule','evidence'])assert.deepEqual(b.coordination[0][name],o.coordination[0][name]);
 }
 assert.deepEqual(value.result.global_claims,original.global_claims.map(({key,...claim})=>claim));
 assert.equal(fieldNames(value).some(name=>name==='key'||name.endsWith('_key')||name.endsWith('_keys')),false);
 assert.equal(JSON.stringify(original),before);assert.equal(evidenceManualFromProjectionV6(original).note,'');
 value.result.businesses[0].steps[0].purpose='后来人稿';assert.equal(JSON.stringify(original),before);
});

test('two business-local zero values, nullable ends, units and explicit multi-step cycles remain exact',()=>{
 const value=evidenceManualFromProjectionV6(projection()),[a,b]=value.result.businesses,view=evidenceManualValidationView(value);
 assert.deepEqual(a.steps.map(s=>s.next_index),[1,null]);assert.deepEqual(b.steps.map(s=>s.next_index),[1,0]);
 for(const item of [a,b]){assert.equal(item.interactions[0].from_participant_index,0);assert.equal(item.interactions[0].to_participant_index,1);assert.deepEqual(item.interactions[0].step_indices,[0,1]);assert.deepEqual(item.conditions[0].step_indices,[0]);assert.deepEqual(item.coordination[0].step_indices,[0,1]);}
 assert.equal(value.result.objects[0].fields[0].unit,null);assert.equal(value.result.objects[1].fields[0].unit,'株');
 assert.equal(value.result.objects[1].fields[0].required,false);assert.equal(value.result.objects[1].fields[0].kind,'整数');
 assert.equal(value.result.objects[1].fields[1].required,true);assert.equal(value.result.objects[1].fields[1].kind,'文本');
 assert.notEqual(view.result.businesses[0].steps[0].key,view.result.businesses[1].steps[0].key);
 assert.equal(view.result.businesses[1].steps[1].next_key,view.result.businesses[1].steps[0].key);
});

test('an object with every field optional is rejected without inventing an identifier or changing the original projection',()=>{
 const p=projection();for(const field of p.objects[1].fields)field.required=false;const before=JSON.stringify(p);
 assert.throws(()=>evidenceManualFromProjectionV6(p),/对象尚缺明确的必填文本识别字段/);
 assert.equal(JSON.stringify(p),before);assert.equal(p.objects[1].fields[0].required,false);assert.equal(p.objects[1].fields[0].unit,'株');assert.equal(p.objects[1].fields[1].required,false);
});

test('missing, duplicate, foreign-business and ambiguous references never fall back or reorder',()=>{
 const mutations=[p=>{p.businesses[0].steps[0].next_key='missing';},p=>{p.businesses[0].steps[0].next_key=p.businesses[1].steps[0].key;},p=>{p.businesses[0].steps[0].next_key=null;},p=>{p.businesses[0].steps[0].next_key='auto';},p=>{p.businesses[0].steps[0].next_key=p.businesses[0].steps[0].key;},p=>{p.businesses[0].steps[1].key=p.businesses[0].steps[0].key;},p=>{p.businesses[0].participants[1].key=p.businesses[0].participants[0].key;},p=>{p.businesses[0].interactions[0].from_participant=p.businesses[1].participants[0].key;},p=>{p.businesses[0].interactions[0].to_participant=p.businesses[0].interactions[0].from_participant;},p=>{p.businesses[0].conditions[0].step_keys=['missing'];},p=>{p.businesses[0].conditions[0].step_keys=[p.businesses[0].steps[0].key,p.businesses[0].steps[0].key];},p=>{p.global_claims[1].key=p.global_claims[0].key;}];
 for(const mutate of mutations){const p=projection();mutate(p);const before=JSON.stringify(p);assert.throws(()=>evidenceManualFromProjectionV6(p));assert.equal(JSON.stringify(p),before);}
});

test('typed closure rejects old versions, proof metadata, extra quotes and coerced scalars instead of dropping them',()=>{
 const mutations=[p=>{p.schema='report-evidence-candidate-projection-v5';},p=>{p.schema='report-evidence-candidate-projection-v99';},p=>{p.wire_bytes_sha256='c'.repeat(64);},p=>{p.businesses[0].steps[0].next_index=1;},p=>{delete p.businesses[0].coordination;},p=>{p.objects[0].fields[0].required=1;},p=>{p.objects[0].fields[0].unit=false;},p=>{p.businesses[0].steps[0].citations[0].quote='not a span citation';},p=>{p.businesses[0].steps[0].key='invented';}];
 for(const mutate of mutations){const p=projection();mutate(p);const before=JSON.stringify(p);assert.throws(()=>evidenceManualFromProjectionV6(p));assert.equal(JSON.stringify(p),before);}
 assert.throws(()=>evidenceManualFromProjection(projection()));
});

test('the existing 512 KiB human limit blocks the entire value without truncation',()=>{
 const p=projection(),claim=p.global_claims[0];p.global_claims=Array.from({length:160},(_,i)=>({...clone(claim),key:id('gc',i),statement:'字'.repeat(2000),explanation:'核'.repeat(1000)}));
 const before=JSON.stringify(p);assert.throws(()=>evidenceManualFromProjectionV6(p),/512 KiB/);assert.equal(JSON.stringify(p),before);assert.equal(p.global_claims.length,160);
});

test('note, JSON and explicit target types stay strict and unnormalized',()=>{
 const p=projection(),note='🙂'.repeat(12000);assert.equal(evidenceManualFromProjectionV6(p,note).note,note);
 for(const note of [null,false,1,'x'.repeat(12001),'不可保存\u0000','\ud800'])assert.throws(()=>evidenceManualFromProjectionV6(p,note));
 for(const target of [false,true,0,0.5,-1,NaN,Infinity]){const bad=projection();bad.businesses[0].steps[0].next_key=target;assert.throws(()=>evidenceManualFromProjectionV6(bad));}
 const bad=projection();bad.domain='坏\ud800';assert.throws(()=>evidenceManualFromProjectionV6(bad));
});

test('a new independent human value cannot rewrite a frozen original nonce, pending body or later input',()=>{
 const p=freeze(projection()),value=evidenceManualFromProjectionV6(p,'原末字A'),pending=freeze({body:{request_id:'274854b9-5b86-423e-a4f3-fb6dd304b504',revision:7,value:clone(value)},command_hash:'e'.repeat(64)}),before=JSON.stringify(pending);
 const later=evidenceManualFromProjectionV6(p,'后来末字B');later.result.businesses[0].steps[0].purpose='后来人稿';
 assert.equal(JSON.stringify(pending),before);assert.equal(pending.body.value.note,'原末字A');assert.equal(later.note,'后来末字B');assert.equal(pending.body.value.result.businesses[0].steps[0].purpose,p.businesses[0].steps[0].purpose);
 assert.equal(Object.hasOwn(later,'model_result_hash'),false);assert.equal(Object.hasOwn(later,'projection_hash'),false);
});

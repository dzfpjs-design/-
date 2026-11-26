import test from 'node:test';
import assert from 'node:assert/strict';
import {addReportCandidate, currentCandidateMaterial} from '../src/report-candidate-draft.mjs';
import {flowProblems, flowSignature, saveFlow} from '../src/setup-schema.js';

const material={id:'material-a',hash:'a'.repeat(64),version:2,engine:'native-docx-v1',status:'needs_input'};
const missing={material_id:'material-a',source_text:'现有原文',quote:'现有原文',reason:'not_found'};
const citation={material_id:'material-a',block_id:'b8',quote:'原文'};
const original={key:'forest',name:'森林巡护',trigger:'发现异常',output:'核查记录',inference_reason:'结合巡护需求',sources:[{source_text:'原文',quote:'原文'}],steps:[{key:'verify',name:'核查',purpose:'核实线索',agent:'人工确认',role:'巡护员',output:'核查结果',condition:'',next_key:'end',else_key:'end',inference_reason:'建议现场核查',sources:[{source_text:'现有原文',quote:'现有原文'}]}]};
const bundle={schema:'setup-business-candidates-v1',origin:'local_summary',material,candidates:[original]};
const row={...original,citations:[citation],unresolved_sources:[],steps:[{...original.steps[0],citations:[],unresolved_sources:[missing]}]};
const preview={material,candidates:[row]};
const base=()=>({version:1,instanceProfile:{domain:'林业',goal:''},businesses:[],flows:{},materials:[{...material,name:'报告'}],resources:[],connections:[]});
const add=(ws=base())=>addReportCandidate(ws,bundle,preview,'forest',[material],'ready',()=> 'step-a');

test('adds the chosen candidate as an unconfirmed editable flow and retains unresolved sources',()=>{
 const before=base(),after=add(before),flow=after.flows['森林巡护'];
 assert.deepEqual(before.businesses,[]);assert.deepEqual(after.businesses,['森林巡护']);
 assert.equal(flow.confirmedAt,null);assert.equal(flow.confirmedSignature,null);assert.equal(flow.candidateKey,'forest');
 assert.equal(flow.steps[0].candidateKey,'verify');assert.equal(flow.steps[0].sourceId,'material-a');
 assert.deepEqual(flow.steps[0].candidateUnresolvedSources,[missing]);
 assert.ok(flowProblems(after,flow,[material],'ready').some(value=>value.includes('依据待核')));
 assert.throws(()=>saveFlow(after,'森林巡护',flow,true,undefined,undefined,[material],'ready'),/依据待核/);
 const edited=saveFlow(after,'森林巡护',{...flow,steps:flow.steps.map(s=>({...s,name:'现场核查'}))});
 assert.deepEqual(edited.flows['森林巡护'].steps[0].candidateUnresolvedSources,[missing]);
});

test('existing name and stale material stop the merge without modifying the draft',()=>{
 const old=base();old.businesses=['森林巡护'];old.flows['森林巡护']={trigger:'旧',output:'旧',steps:[]};
 assert.throws(()=>add(old),/已存在/);assert.equal(old.flows['森林巡护'].trigger,'旧');
 assert.equal(currentCandidateMaterial(bundle,[{...material,version:3}],'ready'),false);
 assert.throws(()=>addReportCandidate(base(),bundle,preview,'forest',[{...material,version:3}],'ready',()=> 'x'),/重新核对/);
 assert.throws(()=>addReportCandidate(base(),bundle,preview,'forest',[material],'failed',()=> 'x'),/重新核对/);
});

test('unresolved business evidence participates in the confirmation signature',()=>{
 const ws=add(),flow=ws.flows['森林巡护'];const changed={...flow,candidateUnresolvedSources:[missing]};
 assert.notEqual(flowSignature(ws,flow),flowSignature(ws,changed));
 assert.ok(flowProblems(ws,changed,[material],'ready').some(value=>value.includes('业务依据待核')));
});

test('malformed unresolved evidence fails closed instead of becoming a confirmed flow',()=>{
 const ws=add(),flow=ws.flows['森林巡护'];
 assert.ok(flowProblems(ws,{...flow,candidateUnresolvedSources:'invalid'},[material],'ready').some(value=>value.includes('依据待核')));
});

test('business-level citations also respect current material status',()=>{
 const ws=add(),flow={...ws.flows['森林巡护'],steps:[{...ws.flows['森林巡护'].steps[0],candidateUnresolvedSources:[],sourceId:''}]};
 assert.ok(flowProblems(ws,flow,[material],'ready').some(value=>value.startsWith('业务依据：')));
});

test('a preview cannot omit a source or change the proposed business content',()=>{
 const missingPreview={...preview,candidates:[{...row,citations:[]}]};
 assert.throws(()=>addReportCandidate(base(),bundle,missingPreview,'forest',[material],'ready',()=> 'x'),/重新核对/);
 const modifiedPreview={...preview,candidates:[{...row,trigger:'虚构的触发'}]};
 assert.throws(()=>addReportCandidate(base(),bundle,modifiedPreview,'forest',[material],'ready',()=> 'x'),/重新核对/);
});

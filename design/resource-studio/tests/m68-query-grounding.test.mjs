import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
import {queryGroundingState} from '../src/native-query-grounding-contract.mjs';
import {queryEffectState,effectReviewKey} from '../src/native-query-effect-contract.mjs';

const H='a'.repeat(64),J='b'.repeat(64),K='c'.repeat(64);
function row(){
 const question='🔎 请按词项LAMP、PAPER检索，任意一个出现即可。',quote='词项LAMP、PAPER',field={key:'text-field',label:'工作说明',type:'string',unit:null};
 const start=Array.from(question.slice(0,question.indexOf(quote))).length,end=start+Array.from(quote).length;
 const parameters={field:field.key,text:'LAMP PAPER',match_mode:'any_terms',top_k:4,min_score:0};
 const source={id:'evidence',shape:'records',record_unit:'evidence_records',cardinality:'zero_or_more',fields:[field],ranked_subset:false,subset_limits:[]};
 const limit={step_id:'search',method:'fulltext',parameters:{field:field.key,match_mode:'any_terms',top_k:4,min_score:0}};
 const output={...structuredClone(source),id:'search',ranked_subset:true,subset_limits:[limit]};
 return {id:'proposal-x',wire_contract_version:6,question_hash:H,candidate_hash:J,original_question:question,
  catalog:{fields:[field],query_tools:[{id:'fulltext-cap',method:'fulltext'}]},
  candidate:{status:'candidate',fields:[field.key],relation_query:null,tool_steps:[{id:'search',capability_id:'fulltext-cap',input_from:'evidence',parameters}]},
  claim_spans:[{kind:'filter',key:'search',quote,start,end}],
  source_grounding:{schema:'native-query-grounding-v1',tokenizer_version:'zh_character_bigram_en_word_v1',semantic_equivalence_verified:false,raw_hash:K,question_hash:H,blocker_count:0,diagnostics:[],steps:[{id:'search',method:'fulltext',text:parameters.text,quote,start,end,status:'supported',query_tokens:['lamp','paper'],tokens:[{token:'lamp',required_count:1,source_count:1},{token:'paper',required_count:1,source_count:1}],diagnostic_codes:[]}]},
  query_effect:{schema:'native-query-effect-v1',status:'derived',semantic_equivalence_verified:false,selected_fields:[field],source_scope:{kind:'selected_task_entities',relation_query:null,resolved_entities:false},provenance:{evidence_metadata:['entity_id','version','observation_id','source'],tool_record_metadata:['entity_ids','evidence_refs'],requires_business_name_field:false},steps:[{id:'search',input_from:'evidence',capability_id:'fulltext-cap',method:'fulltext',operation:null,field:field.key,input:source,output,denominator:null}],final:structuredClone(output),unknown_reasons:[],limitations:[]}};
}

test('v6 binds original text and code-point source span while leaving punctuation and actual parameters unchanged',()=>{
 const value=row(),original=structuredClone(value);
 assert.equal(queryGroundingState(value).reviewAllowed,true);assert.equal(queryEffectState(value).reviewAllowed,true);
 assert.deepEqual(value,original);assert.equal(value.candidate.tool_steps[0].parameters.text,'LAMP PAPER');
 assert.equal(value.source_grounding.steps[0].quote,'词项LAMP、PAPER');
});
test('old versions do not require new grounding and an unknown version cannot confirm',()=>{
 for(const version of [1,2,3,4,5]){const value=row();value.wire_contract_version=version;delete value.source_grounding;assert.equal(queryGroundingState(value).applicable,false);if(version===5)assert.equal(queryEffectState(value).reviewAllowed,true);}
 const value=row();value.wire_contract_version=7;assert.equal(queryEffectState(value).reviewAllowed,false);
});
test('partial or contradictory token proof always blocks confirmation',()=>{
 const changes=[v=>delete v.source_grounding,v=>v.source_grounding.question_hash=K,v=>v.source_grounding.raw_hash='bad',v=>v.source_grounding.semantic_equivalence_verified=true,v=>v.source_grounding.tokenizer_version='unregistered',v=>v.source_grounding.steps.pop(),v=>v.source_grounding.steps.push(structuredClone(v.source_grounding.steps[0])),v=>v.source_grounding.steps[0].text='LAMP',v=>v.source_grounding.steps[0].query_tokens.push('paper'),v=>v.source_grounding.steps[0].tokens[1].source_count=0,v=>v.source_grounding.steps[0].tokens.push(structuredClone(v.source_grounding.steps[0].tokens[0])),v=>v.source_grounding.steps[0].tokens[0].required_count=0,v=>v.source_grounding.steps[0].tokens[0].source_count=Infinity];
 for(const change of changes){const value=row();change(value);assert.equal(queryGroundingState(value).valid,false);assert.equal(queryEffectState(value).reviewAllowed,false);}
});
test('malformed catalog or saved steps fail closed without throwing',()=>{
 for(const change of [v=>v.candidate.tool_steps=[null],v=>v.catalog.query_tools=[null],v=>v.candidate.tool_steps=[{}],v=>v.catalog.query_tools=[{}],v=>v.candidate.tool_steps.push(structuredClone(v.candidate.tool_steps[0])),v=>v.candidate.tool_steps[0].parameters.top_k={},v=>v.candidate.tool_steps[0].parameters.match_mode='unknown']){
  const value=row();change(value);assert.equal(queryGroundingState(value).valid,false);assert.equal(queryEffectState(value).reviewAllowed,false);
 }
});
test('changed quote, UTF-16 offsets, repeated source or lost claim cannot masquerade as supported',()=>{
 for(const change of [v=>v.source_grounding.steps[0].start++,v=>v.source_grounding.steps[0].end++,v=>v.source_grounding.steps[0].quote='LAMP PAPER',v=>v.original_question+=' '+v.source_grounding.steps[0].quote,v=>v.claim_spans=[],v=>v.claim_spans.push(structuredClone(v.claim_spans[0])),v=>v.claim_spans[0].end++]){const value=row();change(value);assert.equal(queryGroundingState(value).valid,false);}
});
test('server blockers remain blockers even if the visible plan looks executable',()=>{
 const value=row();value.source_grounding.blocker_count=1;value.source_grounding.diagnostics=[{code:'field_unit_unknown',message:'请明确字段单位。',step_id:null}];
 assert.equal(queryGroundingState(value).valid,true);assert.equal(queryGroundingState(value).reviewAllowed,false);assert.equal(queryEffectState(value).reviewAllowed,false);
 value.source_grounding.blocker_count=0;assert.equal(queryGroundingState(value).valid,false);
});
test('blocked fulltext must retain a complete saved diagnostic and cannot confirm',()=>{
 const value=row(),proof=value.source_grounding.steps[0];proof.status='blocked';proof.diagnostic_codes=['token_source_missing'];proof.tokens[1].source_count=0;
 value.source_grounding.blocker_count=1;value.source_grounding.diagnostics=[{code:'token_source_missing',message:'请核对检索词的原文依据。',step_id:'search'}];
 assert.equal(queryGroundingState(value).valid,true);assert.equal(queryGroundingState(value).reviewAllowed,false);
 value.source_grounding.diagnostics[0].step_id='not-a-step';assert.equal(queryGroundingState(value).valid,false);
});
test('new candidate, protocol or question binding requires renewed review',()=>{
 const value=row(),key=effectReviewKey(value);
 for(const patch of [{candidate_hash:K},{question_hash:K},{wire_contract_version:5}])assert.notEqual(effectReviewKey({...value,...patch}),key);
});

test('render puts exact source beside actual terms without treating provenance as correct meaning',async()=>{
 const server=await createServer({configFile:false,cacheDir:fileURLToPath(new URL('../.m68-validation/vite-cache',import.meta.url)),server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const {default:Component}=await server.ssrLoadModule('/src/NativeQueryGrounding.jsx');
  const html=renderToStaticMarkup(React.createElement(Component,{row:row()}));
  for(const text of ['LAMP PAPER','词项LAMP、PAPER','逐词核对','仍需核对','全部词','任意词'])assert.ok(html.includes(text));
  assert.equal(html.includes('candidate_hash'),false);assert.equal(html.includes('text-field'),false);
  const bad=row();bad.source_grounding.steps[0].tokens=[{token:{secret:'not-rendered'},required_count:1,source_count:1}];
  const invalid=renderToStaticMarkup(React.createElement(Component,{row:bad}));assert.match(invalid,/暂时不能确认/);assert.equal(invalid.includes('not-rendered'),false);
  for(const change of [v=>v.candidate.tool_steps=[null],v=>v.catalog.query_tools=[null],v=>v.candidate.tool_steps[0].parameters.top_k={}]){const corrupt=row();change(corrupt);assert.match(renderToStaticMarkup(React.createElement(Component,{row:corrupt})),/暂时不能确认/);}
  const many=row();many.source_grounding.diagnostics=Array.from({length:12},(_,i)=>({code:'needs_check',message:'核对项'+i,step_id:null}));many.source_grounding.blocker_count=12;
  const blockers=renderToStaticMarkup(React.createElement(Component,{row:many}));assert.ok(blockers.includes('其余 2 项已保存'));assert.equal(queryEffectState(many).reviewAllowed,false);
 }finally{await server.close();}
});

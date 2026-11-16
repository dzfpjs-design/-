import test from 'node:test';
import assert from 'node:assert/strict';
import {BUSINESS_REASONING_RESULT,isBusinessReasoningResult,businessReasoningResultView,businessEvidencePath,readBusinessEvidencePreview,MAX_TEXT_PREVIEW_BYTES,evidenceExtension} from '../src/business-reasoning-result.mjs';

const id=n=>'19700000-0000-4000-8000-'+String(n).padStart(12,'0');
const copy=value=>structuredClone(value);
function scene(){
 const inputs={question:'怎样核对异常并安排后续工作？',evidence_ids:[id(2),id(1)],candidates:[{candidate_ref:'proposal_first',summary:'先核对受损范围',context:null},{candidate_ref:'proposal_second',summary:'扩大范围建议',context:null},{candidate_ref:'proposal_third',summary:'资料尚不足的建议',context:null}]};
 const value={schema:BUSINESS_REASONING_RESULT,analysis:{summary:'已有一份资料支持第一方案，但第三方案仍缺现场信息。',candidate_assessments:[{candidate_ref:'proposal_first',assessment:'supported',reason:'本次资料说明需先核对范围。',evidence_refs:[id(2)],missing_information:[]},{candidate_ref:'proposal_second',assessment:'conflicting',reason:'另份资料与该范围不一致。',evidence_refs:[id(1)],missing_information:[]},{candidate_ref:'proposal_third',assessment:'insufficient',reason:'缺少当前设备观测。',evidence_refs:[],missing_information:['需提供当前设备的有效观测。']}],recommendation:{candidate_ref:'proposal_first',reason:'先核对范围，再由流程决定下一步。',evidence_refs:[id(2)]}},review_state:'needs_information',recommended_candidate_ref:'proposal_first',recommended_context:null,permissions_verified:false,can_dispatch:false};
 return {inputs,value};
}

test('197 recognition is closed and rejects invalid states, grants and incomplete analysis',()=>{
 const {value}=scene();assert.equal(isBusinessReasoningResult(value),true);
 for(const mutate of [v=>v.schema='different',v=>v.can_dispatch=true,v=>v.extra=true,v=>v.review_state='ready_for_human_review',v=>v.analysis.candidate_assessments[0].assessment='unknown',v=>v.analysis.candidate_assessments[2].missing_information=[],v=>v.analysis.candidate_assessments.push(copy(v.analysis.candidate_assessments[0])),v=>v.recommended_candidate_ref='other',v=>v.analysis.recommendation.evidence_refs=[]]){const changed=copy(value);mutate(changed);assert.equal(isBusinessReasoningResult(changed),false);}
});

test('candidate explanation requires exact unique reference and evidence numbering uses actual input order',()=>{
 const {inputs,value}=scene(),before=copy({inputs,value}),view=businessReasoningResultView(value,inputs);
 assert.equal(view.question,inputs.question);assert.equal(view.state,'资料不足，需补充信息');
 assert.deepEqual(view.rows.map(row=>row.assessment),['有资料支持','资料存在冲突','资料不足']);
 assert.equal(view.rows[0].summary,'先核对受损范围');assert.equal(view.rows[0].citations.items[0].label,'资料1');assert.equal(view.rows[1].citations.items[0].label,'资料2');
 assert.equal(view.recommendation.label,'方案1');view.rows[2].missing.length=0;
 assert.deepEqual({inputs,value},before,'display is detached and never edits model or source');
 inputs.candidates[0].candidate_ref='near_match';assert.equal(businessReasoningResultView(value,inputs).rows[0].summary,null);
 inputs.candidates.push({candidate_ref:'proposal_second',summary:'不能悄悄取第一条同名记录'});assert.equal(businessReasoningResultView(value,inputs).rows[1].summary,null);
});

test('unknown source references stay visibly unavailable and cannot become arbitrary fetch targets',()=>{
 const {inputs,value}=scene();value.analysis.candidate_assessments[0].evidence_refs=[id(999)];
 const view=businessReasoningResultView(value,inputs);assert.equal(view.rows[0].citations.unavailable,true);assert.deepEqual(view.rows[0].citations.items,[]);
 assert.equal(businessEvidencePath(id(1)),'/evidence/'+id(1)+'/content');
 for(const invalid of ['https://remote.invalid/a','../secret','constructor','',null])assert.throws(()=>businessEvidencePath(invalid));
 assert.equal(evidenceExtension('text/html'),'.bin');assert.equal(evidenceExtension('application/pdf'),'.pdf');
});

test('three global review states are derived consistently without turning advice into approval',()=>{
 const {inputs,value}=scene();value.analysis.candidate_assessments.pop();
 value.review_state='conflicting_evidence';assert.equal(businessReasoningResultView(value,inputs).state,'资料存在冲突，需核对');
 value.analysis.candidate_assessments.pop();value.review_state='ready_for_human_review';assert.equal(businessReasoningResultView(value,inputs).state,'可供人工核对');
 value.analysis.recommendation.candidate_ref=null;value.recommended_candidate_ref=null;value.analysis.recommendation.evidence_refs=[];
 assert.equal(businessReasoningResultView(value,inputs).recommendation.label,'当前没有推荐方案');
});

test('preview bounds the actual stream, preserves plain text and never renders active file types',async()=>{
 const plain='<script>unsafe()</script>\n实际原文';
 assert.deepEqual(await readBusinessEvidencePreview(new Response(plain,{headers:{'Content-Type':'text/plain; charset=utf-8'}})),{kind:'text',text:plain});
 assert.equal((await readBusinessEvidencePreview(new Response('<script>unsafe()</script>',{headers:{'Content-Type':'text/html'}}))).kind,'download');
 assert.equal((await readBusinessEvidencePreview(new Response('<svg/>',{headers:{'Content-Type':'image/svg+xml'}}))).kind,'download');
 let cancelled=0;const bytes=new Uint8Array(MAX_TEXT_PREVIEW_BYTES+1),stream=new ReadableStream({start(controller){controller.enqueue(bytes);},cancel(){cancelled++;}});
 assert.equal((await readBusinessEvidencePreview(new Response(stream,{headers:{'Content-Type':'text/plain'}}))).kind,'download');assert.equal(cancelled,1,'overflow stops reading, rather than showing truncated content');
 assert.equal((await readBusinessEvidencePreview(new Response('short',{headers:{'Content-Type':'text/plain','Content-Length':String(MAX_TEXT_PREVIEW_BYTES+1)}}))).kind,'download');
 const invalid=new Uint8Array([0xc3,0x28]);assert.equal((await readBusinessEvidencePreview(new Response(invalid,{headers:{'Content-Type':'text/plain'}}))).kind,'download');
 const image=await readBusinessEvidencePreview(new Response(new Uint8Array([1,2,3]),{headers:{'Content-Type':'image/png'}}));assert.equal(image.kind,'image');assert.equal(image.blob.type,'image/png');assert.equal(image.blob.size,3);
});

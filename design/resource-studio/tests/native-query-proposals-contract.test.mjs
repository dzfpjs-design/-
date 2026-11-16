import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyProposalForm,proposalEvidence,proposalFormFromDraft,proposalDraftBody,checkProposalView,checkProposalPreview,checkProposalJournal,proposalScope,proposalDraftEqual,reconcileProposalSave,goalFormFromProposal,preserveQuerySeed,proposalSeedKey} from '../src/native-query-proposal-contract.mjs';
import {buildNativeGoal} from '../src/native-goal-contract.mjs';
import {queryMetadata,queryTools} from './native-query-fixtures.mjs';

const listing=(extra={})=>({task_id:'task-a',task_version:3,mode:'goal_draft',draft:null,current:null,history:[],models:[],can_manage:true,...extra});
test('unconfirmed task accepts natural language with zero manually selected fields; evidence confirmation is independent',()=>{
 const form={...emptyProposalForm(),question:'从场景甲包含的档案中筛标题含热，算数量均值并核对20'};
 assert.equal(proposalDraftBody(form).evidence,null);
 assert.equal(checkProposalView(listing(),'task-a').draft.revision,0);
 const evidence=proposalEvidence({...form,evidenceConfirmed:true});assert.equal(evidence.source,'archive');assert.equal(evidence.time.mode,'archive');
 assert.throws(()=>proposalEvidence({...form,evidenceConfirmed:true,source:'observations',hours:'0'}),/小时/);
 const restored=proposalFormFromDraft({question:form.question,evidence:{...evidence,time:{...evidence.time,start:null,end:null}}});
 assert.equal(restored.question,form.question);assert.equal(restored.evidenceConfirmed,true);
 assert.throws(()=>proposalFormFromDraft({question:form.question,evidence:{source:'archive',time:{mode:'range'}}}),/不能编辑/);
});
test('task identity, preview versions, corrupt local journals and unknown operations fail closed',()=>{
 const view=checkProposalView(listing(),'task-a');
 assert.throws(()=>checkProposalView(listing(),'task-b'),/原任务/);
 const preview={preview_hash:'hash',task_id:'task-a',task_version:3,draft_revision:0,messages:[{role:'user',content:'全部内容'}],input_bytes:12};
 assert.equal(checkProposalPreview(preview,view),preview);assert.throws(()=>checkProposalPreview({...preview,task_version:2},view),/版本/);
 assert.throws(()=>checkProposalJournal({scope:'other',form:emptyProposalForm(),dirty:true},'actual'),/恢复记录/);
 assert.throws(()=>checkProposalJournal({scope:'actual',form:emptyProposalForm(),dirty:true,pending:{operation:'erase',body:{request_id:'uuid'}}},'actual'),/恢复记录/);
});
test('late save preserves later typing and another server revision; normalized optional fields do not create false dirty state',()=>{
 const original={...emptyProposalForm(),question:'原说明',evidenceConfirmed:true};
 const body=proposalDraftBody(original),sent={operation:'draft',body};
 const server={question:'原说明',evidence:{...body.evidence,time:{...body.evidence.time,start:null,end:null,at:null}}};
 assert.equal(reconcileProposalSave(sent,{...original,reason:'保留理由'},true,server).dirty,false);
 const later={...original,question:'稍后新增的人类说明'};
 assert.deepEqual(reconcileProposalSave(sent,later,true,server),{form:later,dirty:true});
 assert.deepEqual(reconcileProposalSave(sent,original,true,{...server,question:'其他处理人的说明'}),{form:original,dirty:true});
 assert.equal(proposalDraftEqual({a:1,b:null},{a:1}),true);
});
test('adopted GoalSpec maps into existing strict manual editors without defining or accepting any goal',()=>{
 const spec={purpose:'核对热记录均值20',required_fields:['name','count'],entity_ids:['object-a'],evidence:{source:'archive',time:{mode:'archive'},minimum_verification:'recorded'},criteria:[{kind:'evidence_sufficient'},{kind:'human_confirmation'},{kind:'query_value',field:'value',operator:'eq',expected:20}],data_query:{tool_steps:[{id:'hot',capability_id:'keyword-fixed',input_from:'evidence',parameters:{field:'name',text:'热'}},{id:'mean',capability_id:'aggregate-fixed',input_from:'hot',parameters:{field:'count',operation:'mean'}}]},relation_query:null};
 const form=goalFormFromProposal({goal_spec:spec},queryMetadata);assert.equal(form.fields.length,2);assert.equal(form.querySteps[1].inputFrom,'hot');assert.equal(form.queryCriterion.expected,20);assert.equal(form.reason,'');
 const compiled=buildNativeGoal(form,queryMetadata);assert.deepEqual(compiled.data_query.tool_steps,spec.data_query.tool_steps);
 assert.throws(()=>goalFormFromProposal({...spec,criteria:[{kind:'tool_value'}]},queryMetadata),/完整表达/);
 assert.throws(()=>goalFormFromProposal({...spec,required_fields:['invented']},queryMetadata),/已不属于/);
 assert.equal(queryTools.length,3);
});
test('new task query seed remains separate from original creation payload and never overwrites another human seed',()=>{
 const cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value)},account={workspace_id:'w',user_id:'u',username:'operator',role:'operator'};
 const form={purpose:'先前手写目标',queryQuestion:'本次自然语言说明',source:'archive',hours:24,confirmation:'confirmed'};
 preserveQuerySeed(storage,account,'task-a',form);
 const saved=JSON.parse(storage.getItem(proposalSeedKey(account,'task-a')));assert.equal(saved.question,'本次自然语言说明');assert.equal(saved.scope,proposalScope(account,'task-a'));
 assert.throws(()=>preserveQuerySeed(storage,account,'task-a',{...form,queryQuestion:'其他稿'}),/阻止覆盖/);
 assert.equal(form.purpose,'先前手写目标');
});

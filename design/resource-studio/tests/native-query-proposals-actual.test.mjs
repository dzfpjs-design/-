import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {readFile,writeFile,mkdir,mkdtemp} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {evidencePath} from './evidence-path.mjs';
import {createHash} from 'node:crypto';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {checkProposalView,checkProposalPreview,proposalDraftEqual} from '../src/native-query-proposal-contract.mjs';

const evidenceDir=new URL('.',pathToFileURL(evidencePath('m58-query-contract/service-restart-api-views.json')));
const sha=value=>createHash('sha256').update(value).digest('hex');
const bySuffix=(views,suffix)=>Object.entries(views).find(([path])=>path.endsWith(suffix))?.[1];
test('actual stopped/restarted API fixtures pass UI contracts, readable SSR and real React read/refresh/detail events offline',async()=>{
 await mkdir(new URL('../.m62-validation/',import.meta.url),{recursive:true});const outputDir=pathToFileURL(await mkdtemp(new URL('../.m62-validation/m58-actual-fixture-',import.meta.url))+'/');
 const fixtureBytes=await readFile(new URL('service-restart-api-views.json',evidenceDir)),fixtures=JSON.parse(fixtureBytes),report=JSON.parse(await readFile(new URL('service-restart-result.json',evidenceDir),'utf8'));
 assert.equal(report.passed,true);assert.equal(report.browser_used,false);assert.equal(report.normal_database_used,false);
 const stages=['proposal_and_support','formally_accepted'];
 for(const stage of stages)for(const phase of ['before','after']){
  const views=fixtures[stage][phase],listing=bySuffix(views,'/query-proposals'),support=bySuffix(views,'/supporting-queries'),goal=bySuffix(views,'/goal');
  checkProposalView(listing,goal.task_id);assert.equal(support.task_id,goal.task_id);assert.equal(listing.mode,'reference_only');
  const detail=bySuffix(views,'/query-proposals/'+listing.current.id);checkProposalPreview(detail.preview,listing);assert.equal(detail.preview.schema.oneOf.length,2);assert.ok(detail.preview.schema.$defs.Candidate.properties.operations);assert.equal(detail.preview.schema.$defs.Candidate.properties.questions,undefined);assert.equal(detail.preview.schema.$defs.Candidate.properties.claims,undefined);assert.equal(detail.preview.schema.$defs.Clarification.properties.operations,undefined);assert.ok(detail.preview.schema.$defs.Operation.oneOf.every(item=>item.properties.input_from.type==='integer'&&item.required.includes('quote')&&item.properties.id===undefined));const wire=JSON.parse(detail.raw_response.content);assert.equal(wire.status,'candidate');assert.equal(wire.claims,undefined);assert.ok(wire.fields.every(choice=>typeof choice.key==='string'&&typeof choice.quote==='string'));assert.ok(wire.operations.every(operation=>typeof operation.quote==='string'&&operation.id===undefined));
  assert.equal(listing.current.id,detail.id);assert.equal(support.items.every(item=>item.reference_only),true);
 }
 const before=fixtures.proposal_and_support.after,accepted=fixtures.formally_accepted.after,listing=bySuffix(before,'/query-proposals'),goal=bySuffix(before,'/goal'),support=bySuffix(before,'/supporting-queries'),detail=bySuffix(before,'/query-proposals/'+listing.current.id),task=before['/api/workspace/tasks/'+goal.task_id];
 const prior={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage,window:globalThis.window},cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,window:{addEventListener(){},removeEventListener(){}}});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let component;
 try{
  const {default:Assistant,ProposalMeaning,SupportingQueryResults}=await server.ssrLoadModule('/src/NativeQueryProposals.jsx');
  const meaning=renderToStaticMarkup(React.createElement(ProposalMeaning,{row:listing.current,catalog:listing.current.catalog})),result=renderToStaticMarkup(React.createElement(SupportingQueryResults,{view:support,catalog:listing.catalog}));
  assert.ok(meaning.includes(listing.current.candidate.summary));assert.ok(meaning.includes('原话引用只证明出处'));assert.ok(meaning.includes('证据记录数'));
  for(const text of ['本机查询结果已保存','只读参考结果','正式验收','核对本次完整原来源','条证据记录'])assert.ok(result.includes(text),text);
  for(const field of listing.catalog.fields)assert.equal(result.includes(field.key),false,'business field key must not appear as human result');
  const html='<!doctype html><meta charset="utf-8"><title>M56 actual saved API fixtures · offline SSR</title><main>'+meaning+result+'</main>';
  await writeFile(new URL('actual-service-frontend-render.html',outputDir),html);
  let views=before;const requests=[];globalThis.fetch=async(path,options={})=>{requests.push({path,method:options.method||'GET'});assert.equal(options.method,undefined,'actual fixture proof is read-only');const url=new URL(path,'http://offline.invalid'),row=views[url.pathname];assert.ok(row,'saved actual API view: '+url.pathname);return new Response(JSON.stringify(row),{headers:{'Content-Type':'application/json'}});};
  const props={task:{...task},account:{workspace_id:'offline-fixture-render',user_id:'offline-fixture-reader',username:'offline-reader',role:'admin'},Btn:'button',goal};
  component=mount(Assistant,props);await component.flush();assert.equal(component.byAria('自然语言查询需求').props.value,listing.draft.question);
  await component.click('核对该建议原发送与原回复');await component.input('自然语言查询需求','保存于本机、必须保留的后续人类输入');
  const originalGoal=JSON.stringify(props.goal.goal);views=accepted;props.task.version=accepted['/api/workspace/tasks/'+goal.task_id].version;component.render();await component.flush();await component.click('更新查询建议与结果');
  assert.equal(component.byAria('自然语言查询需求').props.value,'保存于本机、必须保留的后续人类输入');assert.equal(JSON.stringify(props.goal.goal),originalGoal);assert.equal(component.button('执行本任务只读补充查询').props.disabled,true);assert.ok(requests.every(request=>request.method==='GET'));
  const acceptance=bySuffix(accepted,'/goal').acceptance;assert.ok(acceptance);assert.ok(proposalDraftEqual(bySuffix(before,'/goal').goal,bySuffix(accepted,'/goal').goal));
  await writeFile(new URL('actual-service-frontend-contract-proof.json',outputDir),JSON.stringify({schema:'m58-actual-service-frontend-proof-v1',passed:true,source_fixture_sha256:sha(fixtureBytes),render_sha256:sha(html),checks:{actual_api_checkpoints:4,m58_v3_wire_preview_bound_to_unchanged_ui:true,task_bound_contracts:true,actual_saved_preview:true,human_fields_units_operations_and_evidence:true,reference_only_result:true,original_goal_unchanged:true,actual_formal_acceptance_preserved:true,real_react_read_detail_refresh_events:true,later_human_typing_preserved:true,no_mock_write_responses:true},requests,limitations:['Offline replay of saved real API responses; no browser or narrow-screen verification','No HTTP calls, cloud calls, normal database access or product writes in this proof','Synthetic generation and actual process-restart provenance remain described by the source backend report']},null,2)+'\n');
 }finally{component?.unmount();await server.close();Object.assign(globalThis,prior);}
});

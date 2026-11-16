import test from 'node:test';
import assert from 'node:assert/strict';
import {adoptExecutionConfiguration} from '../src/execution-configuration-adoption.mjs';
function scene(){
 const origin={schema:'business-flow-execution-origin-v1',adoption_id:'actual_id',business_name:'业务甲',binding_hash:'actual_pin',command_hash:'original_command'};
 const calls=[];return {calls,options:{saved:{revision:4,configuration:{businessFlowExecutions:{'业务甲':origin}}},
  scope:{actor_id:'actor',workspace_id:'workspace'},account:{user_id:'actor',workspace_id:'workspace'},
  receipt:{schema:'business-flow-execution-adoption-result-v1',id:'actual_id',origin,can_execute:false,accepted_revision:3},
  wire:{requires_lossless_sync:false},checkEntry:()=>calls.push('entry'),adoptServerDraft:async()=>{calls.push('saved-current-configuration');return {preservedPending:true};},onStatus:value=>calls.push(value)}};
}
test('single flow projection keeps other work open and reports actual preserved configuration labor',async()=>{
 const {calls,options}=scene();const result=await adoptExecutionConfiguration(options);
 assert.deepEqual(result,{accepted:true,preserved_numeric_tokens:false,preservedPending:true});assert.equal(calls.filter(x=>x==='saved-current-configuration').length,1);
 assert.match(calls.at(-1).text,/其他流程可继续核对/);assert.match(calls.at(-1).text,/尚未生成或启用/);
});
test('configuration requiring numeric fidelity remains saved remotely without destructive legacy projection',async()=>{
 const {calls,options}=scene();options.wire.requires_lossless_sync=true;const result=await adoptExecutionConfiguration(options);
 assert.equal(result.accepted,false);assert.match(result.reason,/已加入服务器配置/);assert.equal(calls.includes('saved-current-configuration'),false);
});
test('changed account, origin or version cannot project an old receipt',async()=>{
 for(const fault of ['actor','origin','version']){
  const {calls,options}=scene();if(fault==='actor')options.scope.actor_id='different';
  if(fault==='origin')options.saved.configuration.businessFlowExecutions['业务甲']={...options.receipt.origin,adoption_id:'another'};
  if(fault==='version')options.saved.revision=2;
  await assert.rejects(adoptExecutionConfiguration(options));assert.equal(calls.includes('saved-current-configuration'),false);
 }
});

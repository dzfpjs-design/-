// Public synthetic records only. Real wire codec and SHA-256; no backend,
// current registration, route grant, device connection or field truth claimed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {HTTP_SOURCE_CONTRACT_KEY,checkHTTPGraphSourceContract} from '../src/graph-http-source-contract-v177.mjs';
import {graphWireCanonical,graphWireDigest,graphWireParseCanonical} from '../src/full-semantic-graph-wire.mjs';

if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const id=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
const hash=letter=>letter.repeat(64),FIELD='public_temperature';
async function fixture(fieldSchema={type:'number',unit:'°C',minimum:0}){
 const scope={actor_id:id(1),workspace_id:id(2),instance_id:id(3)};
 const command={release_id:id(4),source_id:id(5),extractor_id:id(6)};
 const actualScope={workspace_id:scope.workspace_id,environment_id:id(7),instance_id:scope.instance_id,
  release_id:command.release_id,package_id:id(8),package_hash:hash('a'),entity_type:'public_weather'};
 const mapping={schema:'http-observation-mapping-v177',scope:actualScope,
  links:{temperature_c:{field_key:FIELD,unit:'°C'}},
  supplier_contract:'sim-acquisition-receipt-v176',time_semantics:'simulator_sample_time'};
 const frozen={schema:'http-observation-mapping-freeze-v177',mapping,mapping_hash:await graphWireDigest(mapping),
  source_id:command.source_id,source_manifest_hash:hash('b'),entity_schemas_hash:hash('c'),
  field_schemas:{[FIELD]:fieldSchema},allowed_actions:['observe']};
 frozen.freeze_hash=await graphWireDigest(frozen);
 const contract={schema:'signal-graph-http-source-contract-v177',family:'http-mapped-acquisition-v177',scope:actualScope,
  source_id:command.source_id,source_hash:hash('b'),extractor_id:command.extractor_id,extractor_hash:hash('d'),
  environment_hash:hash('e'),configuration_hash:hash('f'),mapping:frozen,field_units:{[FIELD]:'°C'},
  simulation:true,test_only:true,field_verified:false};
 const binding={release_id:command.release_id,configuration_hash:contract.configuration_hash,
  source:{id:command.source_id,content_hash:contract.source_hash},
  extractor:{id:command.extractor_id,content_hash:contract.extractor_hash}};
 return {contract,scope,command,binding};
}
async function reseal(contract){
 contract.mapping.mapping_hash=await graphWireDigest(contract.mapping.mapping);
 contract.mapping.freeze_hash=await graphWireDigest(contract.mapping,{omit:'freeze_hash'});
 return contract;
}
const check=({contract,...context})=>checkHTTPGraphSourceContract(contract,context);

test('actual closed SIM contract returns an immutable detached typed snapshot, without adding authority',async()=>{
 const input=await fixture(),before=graphWireCanonical(input.contract),result=await check(input);
 assert.equal(HTTP_SOURCE_CONTRACT_KEY,'http_mapped_source_contract_v177');
 assert.notEqual(result,input.contract);assert.equal(graphWireCanonical(result),before);
 assert.equal(graphWireCanonical(input.contract),before);
 assert.ok(Object.isFrozen(result)&&Object.isFrozen(result.mapping)&&Object.isFrozen(result.mapping.field_schemas));
 assert.equal(Object.keys(result).length,14);
 for(const key of ['can_execute','source_authorized','route_id','task_id','invocation_id'])assert.equal(Object.hasOwn(result,key),false);
 input.contract.mapping.field_schemas[FIELD].minimum=3;
 assert.equal(result.mapping.field_schemas[FIELD].minimum,0);
});

test('all outer keys, family markers and strict booleans are required without old-family fallback',async()=>{
 for(const damage of [
  v=>delete v.environment_hash,v=>v.extra_permission=true,
  v=>v.schema='signal-graph-http-source-contract-unknown',v=>v.family='simulation-sample-v176',
  v=>v.simulation=1,v=>v.test_only=1,v=>v.field_verified=0,v=>v.field_verified=true,
  v=>v.source_id='ABCDEF00-0000-4000-8000-000000000005',v=>v.extractor_hash='A'.repeat(64),
 ]){const input=await fixture();damage(input.contract);await assert.rejects(check(input));}
 await assert.rejects(checkHTTPGraphSourceContract(undefined,{scope:{},command:{},binding:{}}));
});

test('scope is exactly seven actual pins, not a guessed entity or relabelled instance',async()=>{
 for(const damage of [
  v=>delete v.scope.environment_id,v=>v.scope.source_id=v.source_id,
  v=>v.scope.package_id='not-a-uuid',v=>v.scope.package_hash='x'.repeat(64),
  v=>v.scope.entity_type='',v=>v.scope.entity_type=' public_weather',v=>v.scope.entity_type='a'.repeat(101),
 ]){const input=await fixture();damage(input.contract);await reseal(input.contract);await assert.rejects(check(input));}
});

test('current command, instance and actual binding hashes are independently matched',async()=>{
 const cases=[
  input=>input.scope.workspace_id=id(91),input=>input.scope.instance_id=id(92),
  input=>input.command.release_id=id(93),input=>input.command.source_id=id(94),
  input=>input.command.extractor_id=id(95),input=>input.binding.release_id=id(96),
  input=>input.binding.source.id=id(97),input=>input.binding.source.content_hash=hash('a'),
  input=>input.binding.extractor.id=id(98),input=>input.binding.extractor.content_hash=hash('a'),
  input=>input.binding.configuration_hash=hash('a'),
 ];
 for(const damage of cases){const input=await fixture();damage(input);await assert.rejects(check(input));}
});

test('current pins require own data fields without inherited identities or getter execution',async()=>{
 for(const key of ['scope','command','binding']){
  const input=await fixture();input[key]=Object.create(input[key]);await assert.rejects(check(input));
 }
 const inherited=await fixture();inherited.binding.source=Object.create(inherited.binding.source);
 await assert.rejects(check(inherited));
 const input=await fixture();let reads=0;
 Object.defineProperty(input.binding.extractor,'content_hash',{get(){reads++;return hash('d');},enumerable:true});
 await assert.rejects(check(input));assert.equal(reads,0);
 const root=await fixture(),options={command:root.command,binding:root.binding};
 Object.defineProperty(options,'scope',{get(){reads++;return root.scope;},enumerable:true});
 await assert.rejects(checkHTTPGraphSourceContract(root.contract,options));assert.equal(reads,0);
});

test('frozen mapping and config have separate closed families and independently recomputed hashes',async()=>{
 for(const damage of [
  v=>delete v.mapping.entity_schemas_hash,v=>v.mapping.extra=true,
  v=>v.mapping.schema='http-observation-mapping-freeze-legacy',v=>v.mapping.mapping.schema='legacy',
  v=>v.mapping.mapping.supplier_contract='other-supplier',v=>v.mapping.mapping.time_semantics='wall_clock',
  v=>v.mapping.mapping_hash=hash('a'),v=>v.mapping.freeze_hash=hash('a'),
  v=>v.mapping.mapping.extra=true,v=>v.mapping.mapping.links.temperature_c.unit='C',
 ]){const input=await fixture();damage(input.contract);await assert.rejects(check(input));}
});

test('resealing cannot substitute another source, scope or Source manifest',async()=>{
 for(const damage of [
  v=>v.mapping.source_id=id(99),v=>v.mapping.source_manifest_hash=hash('a'),
  v=>v.mapping.mapping.scope={...v.scope,environment_id:id(90)},
  v=>v.mapping.entity_schemas_hash='not-a-hash',
 ]){const input=await fixture();damage(input.contract);await reseal(input.contract);await assert.rejects(check(input));}
});

test('links keep the supplier enum, exact units and one domain field per measurement',async()=>{
 for(const damage of [
  v=>v.mapping.mapping.links={},
  v=>v.mapping.mapping.links={unknown_measurement:{field_key:FIELD,unit:'°C'}},
  v=>v.mapping.mapping.links.temperature_c={field_key:FIELD,unit:'K'},
  v=>v.mapping.mapping.links.temperature_c={field_key:FIELD,unit:'°C',scale:1},
  v=>v.mapping.mapping.links.temperature_c.field_key=' '+FIELD,
  v=>v.mapping.mapping.links.humidity_percent={field_key:FIELD,unit:'%'},
 ]){const input=await fixture();damage(input.contract);await reseal(input.contract);await assert.rejects(check(input));}
});

test('field schema keys, numeric kind and unit stay aligned with every original link',async()=>{
 for(const damage of [
  v=>v.mapping.field_schemas={},v=>v.mapping.field_schemas.extra={type:'number',unit:'°C'},
  v=>v.mapping.field_schemas[FIELD].type='string',v=>v.mapping.field_schemas[FIELD].type=['number','null'],
  v=>v.mapping.field_schemas[FIELD].unit='K',v=>v.mapping.field_schemas[FIELD]=true,
  v=>v.mapping.field_schemas[FIELD].$ref='#/elsewhere',
  v=>v.mapping.field_schemas[FIELD].allOf=[{$dynamicRef:'https://invalid.example/schema'}],
  v=>v.mapping.field_schemas[FIELD].allOf=[{$recursiveRef:'#'}],
 ]){const input=await fixture();damage(input.contract);await reseal(input.contract);await assert.rejects(check(input));}
});

test('NUL is rejected throughout the frozen JSON even when hashes are correctly resealed',async()=>{
 for(const damage of [
  v=>v.scope.entity_type='public_weather\u0000',
  v=>v.mapping.field_schemas[FIELD].description='原字段\u0000说明',
  v=>v.mapping.field_schemas[FIELD]['description\u0000']='原字段说明',
 ]){const input=await fixture();damage(input.contract);await reseal(input.contract);await assert.rejects(check(input));}
});

test('field units are the entire original link projection with neither omitted nor added fields',async()=>{
 for(const units of [{},{[FIELD]:'C'},{[FIELD]:'°C',extra:'%'},null]){
  const input=await fixture();input.contract.field_units=units;await assert.rejects(check(input));
 }
});

test('actions stay finite unique original read or observation declarations',async()=>{
 for(const actions of [[],['observe','observe'],['fly'],[true],'observe']){
  const input=await fixture();input.contract.mapping.allowed_actions=actions;
  await reseal(input.contract);await assert.rejects(check(input));
 }
 const input=await fixture();input.contract.mapping.allowed_actions=['read_existing'];
 await reseal(input.contract);assert.equal((await check(input)).mapping.allowed_actions[0],'read_existing');
});

test('real typed SHA keeps negative zero and floating integer spelling through detached checking',async()=>{
 const fieldSchema=graphWireParseCanonical('{"maximum":1.0,"minimum":-0.0,"type":"number","unit":"°C"}');
 const input=await fixture(fieldSchema),before=graphWireCanonical(input.contract);
 const result=await check(input),raw=graphWireCanonical(result);
 assert.equal(raw,before);assert.ok(raw.includes('"minimum":-0.0')&&raw.includes('"maximum":1.0'));
 input.contract.mapping.field_schemas[FIELD]={type:'number',unit:'°C',minimum:0,maximum:1};
 // This visibly similar integer projection has a DIFFERENT typed freeze hash.
 assert.notEqual(await graphWireDigest(input.contract.mapping,{omit:'freeze_hash'}),input.contract.mapping.freeze_hash);
 await assert.rejects(check(input));
});

test('nonfinite, unsafe integers, non-JSON values and getters are rejected before being displayed',async()=>{
 for(const invalid of [NaN,Infinity,Number.MAX_SAFE_INTEGER+1,1n]){
  const input=await fixture();input.contract.mapping.field_schemas[FIELD].minimum=invalid;
  await assert.rejects(check(input));
 }
 const input=await fixture();let reads=0;
 Object.defineProperty(input.contract,'schema',{get(){reads++;return 'signal-graph-http-source-contract-v177';},enumerable:true});
 await assert.rejects(check(input));assert.equal(reads,0);
});

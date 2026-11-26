// Actual preview checker and ReactDOM. Only public synthetic transport records;
// no browser, database, provider receipt, device connection or grant is proved.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {checkGraphRoutePreview,buildGraphRoutePreview,emptyGraphRouteForm,routeInputDraft} from '../src/graph-signal-routing-v134.mjs';
import {HTTP_SOURCE_CONTRACT_KEY} from '../src/graph-http-source-contract-v177.mjs';
import {graphWireDigest,graphWireCanonical} from '../src/full-semantic-graph-wire.mjs';
import {fixture,previewFor,scope,sourceID,extractorID,entityID,id,wire,seal,Storage,session} from './graph-signal-routing-fixture-v134.mjs';

if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),temps=[];
let componentPromise;
after(async()=>{await Promise.all(temps.map(path=>rm(path,{recursive:true,force:true})));});

async function scene(){
 const original=await fixture(),entities=original.entities.map(entity=>({...entity,
  fields:entity.fields.map(field=>({...field,unit:'°C'}))}));
 const f={...original,entities,binding:{...original.binding,entities,
  source:{...original.source,name:'隔离气象模拟来源'}}};
 const form={...emptyGraphRouteForm(),source_id:sourceID,extractor_id:extractorID,entity_ids:[entityID],
  business_id:f.business.id,entity_input_fields:['entity'],purpose:'核对实际触发对象并保存原依据',
  required_fields:['temperature'],reason:'明确仅授权已核对的完整流程范围'};
 form.input_draft=routeInputDraft(scope,f.business,['entity']);
 form.input_draft.values.threshold={mode:'value',raw:'35.0'};
 const command=buildGraphRoutePreview(form,f.options,scope),preview=await previewFor(f,command);
 const actualScope={workspace_id:scope.workspace_id,environment_id:id(71),instance_id:scope.instance_id,
  release_id:command.release_id,package_id:id(72),package_hash:'b'.repeat(64),entity_type:'public_weather'};
 const mapping={schema:'http-observation-mapping-v177',scope:actualScope,
  links:{temperature_c:{field_key:'temperature',unit:'°C'}},
  supplier_contract:'sim-acquisition-receipt-v176',time_semantics:'simulator_sample_time'};
 const frozen={schema:'http-observation-mapping-freeze-v177',mapping,mapping_hash:await graphWireDigest(mapping),
  source_id:sourceID,source_manifest_hash:preview.binding.source.content_hash,entity_schemas_hash:'c'.repeat(64),
  field_schemas:{temperature:{type:'number',unit:'°C',title:'温度'}},allowed_actions:['observe']};
 frozen.freeze_hash=await graphWireDigest(frozen);
 const contract={schema:'signal-graph-http-source-contract-v177',family:'http-mapped-acquisition-v177',scope:actualScope,
  source_id:sourceID,source_hash:preview.binding.source.content_hash,extractor_id:extractorID,
  extractor_hash:preview.binding.extractor.content_hash,environment_hash:'d'.repeat(64),
  configuration_hash:preview.binding.configuration_hash,mapping:frozen,field_units:{temperature:'°C'},
  simulation:true,test_only:true,field_verified:false};
 return {f,command,preview,contract};
}
async function withContract(s,contract=s.contract,extra={}){
 return seal({...s.preview,binding:{...s.preview.binding,[HTTP_SOURCE_CONTRACT_KEY]:contract,...extra}},'preview_hash');
}
async function resealContract(contract){
 contract.mapping.mapping_hash=await graphWireDigest(contract.mapping.mapping);
 contract.mapping.freeze_hash=await graphWireDigest(contract.mapping,{omit:'freeze_hash'});
}

test('real checker permits only the one optional HTTP key while preserving the old seventeen-key preview',async()=>{
 const s=await scene(),legacy=await checkGraphRoutePreview(wire(s.preview),s.command,scope);
 assert.equal(Object.keys(legacy.binding).length,17);
 assert.equal(Object.hasOwn(legacy.binding,HTTP_SOURCE_CONTRACT_KEY),false);
 const extended=await withContract(s),before=graphWireCanonical(extended);
 const actual=await checkGraphRoutePreview(wire(extended),s.command,scope);
 assert.equal(Object.keys(actual.binding).length,18);
 assert.equal(actual.binding[HTTP_SOURCE_CONTRACT_KEY].simulation,true);
 assert.equal(actual.binding[HTTP_SOURCE_CONTRACT_KEY].field_verified,false);
 assert.equal(graphWireCanonical(extended),before);
 assert.deepEqual(actual.expanded,legacy.expanded);
});

test('real checker rejects any other binding extension and a null or mixed-family contract',async()=>{
 const s=await scene();
 const unknown=await seal({...s.preview,binding:{...s.preview.binding,
  http_mapped_source_contract_v178:s.contract}},'preview_hash');
 await assert.rejects(checkGraphRoutePreview(wire(unknown),s.command,scope));
 for(const contract of [null,{...s.contract,family:'simulation-sample-v176'}]){
  await assert.rejects(checkGraphRoutePreview(wire(await withContract(s,contract)),s.command,scope));
 }
 await assert.rejects(checkGraphRoutePreview(wire(await withContract(s,s.contract,{source_authorized:true})),s.command,scope));
});

test('outer preview resealing cannot hide mismatched HTTP scope, pins, units or typed freeze',async()=>{
 for(const change of [
  s=>s.contract.configuration_hash='e'.repeat(64),
  s=>s.contract.scope.workspace_id=id(99),
  s=>s.contract.extractor_hash='e'.repeat(64),
  s=>s.contract.test_only=1,
  s=>s.contract.mapping.field_schemas.temperature.minimum=1,
 ]){const s=await scene();change(s);await assert.rejects(checkGraphRoutePreview(wire(await withContract(s)),s.command,scope));}
 const s=await scene();
 s.contract.mapping.mapping.links.temperature_c.unit='K';
 s.contract.mapping.field_schemas.temperature.unit='K';s.contract.field_units.temperature='K';
 await resealContract(s.contract);
 await assert.rejects(checkGraphRoutePreview(wire(await withContract(s)),s.command,scope));
});

test('HTTP extension retains the original graph ability and outer preview integrity gates',async()=>{
 const s=await scene(),extended=await withContract(s);
 await assert.rejects(checkGraphRoutePreview(wire({...extended,preview_hash:'e'.repeat(64)}),s.command,scope));
 const bad=await withContract(s,s.contract,{abilities:s.preview.binding.abilities.map(ability=>({...ability,adapter:'http'}))});
 await assert.rejects(checkGraphRoutePreview(wire(bad),s.command,scope));
});

async function component(){
 if(!componentPromise)componentPromise=(async()=>{
  const temp=await mkdtemp(join(tmpdir(),'graph-http-preview-'));temps.push(temp);
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',
   build:{ssr:join(project,'src/SignalGraphRoutingStudioV134.jsx'),write:false,minify:false,
    rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:key=>key==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:key}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);
  const path=join(temp,'SignalGraphRoutingStudioV134.mjs');await writeFile(path,chunk.code);
  return import(pathToFileURL(path).href);
 })();return componentPromise;
}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources permitted');}}
async function mounted(preview,inspect){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
  {url:'https://synthetic-http-graph.invalid/',resources:new NoResources(),virtualConsole:console});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,
  Event:dom.window.Event,localStorage:new Storage(),sessionStorage:session(),crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,
  fetch:()=>{throw Error('No network is permitted');}};
 const descriptors=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 let root;
 try{
  const {createRoot}=await import('react-dom/client'),{GraphRoutingPreviewV134}=await component();
  const container=dom.window.document.getElementById('root');
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(GraphRoutingPreviewV134,{preview}))));
  await inspect(container);
  await act(async()=>root.unmount());root=null;assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();
  for(const [key,descriptor]of Object.entries(descriptors))if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
 }
}

test('actual ReactDOM preview explains mapped SIM units, future receipts and separate device authority without IDs or raw JSON',async()=>{
 const s=await scene(),preview=await checkGraphRoutePreview(wire(await withContract(s)),s.command,scope);
 await mounted(preview,container=>{
  const section=container.querySelector('[aria-label="HTTP模拟来源与字段映射"]');assert.ok(section);
  const text=section.textContent;
  for(const message of ['模拟设备','模拟数据（SIM）','仅供测试','非真实设备观测或现场事实',
   '模拟温度 → 温度','原单位：°C','预览不会发起采集','采样时间由模拟器提供',
   '仅另经授权的后续采集','旧数据不会重复派单','审批、设备操作和补采须另行授权'])assert.ok(text.includes(message),message);
  const mapping=section.querySelector('details');assert.ok(mapping);assert.equal(mapping.open,false);
  assert.equal(mapping.querySelector('summary').textContent,'查看字段映射');
  const workflow=[...container.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent==='查看流程步骤和负责人');
  assert.ok(workflow);assert.equal(workflow.open,false,'detailed steps stay available without filling the initial view');
  const limits=[...container.querySelectorAll('details')].find(node=>node.querySelector('summary')?.textContent==='查看执行限制');
  assert.ok(limits);assert.equal(limits.open,false);assert.match(limits.textContent,/最多 .* 步、.* 次能力调用/);
  assert.equal((container.textContent.match(/非真实设备观测或现场事实/g)||[]).length,1);
  assert.ok(container.textContent.includes('仅使用本机只读功能，不操作设备'));
  assert.equal(/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/.test(container.textContent),false);
  assert.equal(container.textContent.includes('{"'),false);
  assert.equal(section.querySelector('button'),null,'preview has no acquisition or device action');
 });
});

test('actual legacy ReactDOM preview retains its prior explanation without inventing an HTTP source',async()=>{
 const s=await scene(),preview=await checkGraphRoutePreview(wire(s.preview),s.command,scope);
 await mounted(preview,container=>{
  assert.equal(container.querySelector('[aria-label="HTTP模拟来源与字段映射"]'),null);
  assert.ok(container.textContent.includes('仅使用本机只读功能，不操作设备。'));
  assert.ok(container.textContent.includes('只处理启用后的新异常'));
 });
});

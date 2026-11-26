// Real product builder -> strict options/preview -> ReactDOM; public synthetic
// transport only. No received data, current authorization or device is proved.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';
import {checkGraphRouteOptions,checkGraphRoutePreview,buildGraphRoutePreview,emptyGraphRouteForm,routeInputDraft} from '../src/graph-signal-routing-v134.mjs';
import {HTTP_SOURCE_CONTRACT_KEY} from '../src/graph-http-source-contract-v177.mjs';
import {graphWireDigest,graphWireRequestJSON,graphWireCanonical} from '../src/full-semantic-graph-wire.mjs';
import {fixture,previewFor,scope,sourceID,extractorID,entityID,id,wire,seal,Storage,session} from './graph-signal-routing-fixture-v134.mjs';

if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const IDS='http_mapped_source_ids_v177',manualID=id(81);
const expectedHTTP={source:'observations',minimum_verification:'unverified_extraction',minimum_records_per_entity:1,
 time:{mode:'latest',timezone:'Asia/Shanghai',start:null,end:null,at:null,relative:null,days:null,max_age_seconds:3600}};
const expectedArchive={source:'archive',minimum_verification:'recorded',minimum_records_per_entity:1,
 time:{mode:'archive',timezone:'Asia/Shanghai',start:null,end:null,at:null,relative:null,days:null,max_age_seconds:null}};

async function scene({http=true,selected=sourceID}={}){
 const f=await fixture(),manual={...f.source,id:manualID,name:'另一人工来源'};
 const rawOptions=await seal({...f.options,sources:[{...f.source,name:'HTTP模拟字样不是授权'},manual],
  ...(http?{[IDS]:[sourceID]}:{})},'options_hash');
 const options=await checkGraphRouteOptions(wire(rawOptions),scope);
 const form={...emptyGraphRouteForm(),source_id:selected,extractor_id:extractorID,entity_ids:[entityID],
  business_id:f.business.id,entity_input_fields:['entity'],purpose:'核对实际对象的限定资料并明确验收',
  required_fields:['temperature'],reason:'逐项核对来源与目标，不新增采集权限'};
 form.input_draft=routeInputDraft(scope,f.business,['entity']);
 form.input_draft.values.threshold={mode:'value',raw:'30.0'};
 const command=buildGraphRoutePreview(form,options,scope);
 return {f,form,rawOptions,options,command};
}
async function preview(s,command=s.command,{http=true}={}){
 const actualSource=s.options.sources.find(source=>source.id===command.source_id);
 const f={...s.f,binding:{...s.f.binding,source:actualSource}};
 const base=await previewFor(f,command);if(!http)return base;
 const sourceScope={workspace_id:scope.workspace_id,environment_id:id(71),instance_id:scope.instance_id,
  release_id:command.release_id,package_id:id(72),package_hash:'b'.repeat(64),entity_type:'public_weather'};
 const mapping={schema:'http-observation-mapping-v177',scope:sourceScope,
  links:{temperature_c:{field_key:'temperature',unit:'°C'}},supplier_contract:'sim-acquisition-receipt-v176',time_semantics:'simulator_sample_time'};
 const frozen={schema:'http-observation-mapping-freeze-v177',mapping,mapping_hash:await graphWireDigest(mapping),
  source_id:actualSource.id,source_manifest_hash:actualSource.content_hash,entity_schemas_hash:'c'.repeat(64),
  field_schemas:{temperature:{type:'number',unit:'°C',title:'温度'}},allowed_actions:['observe']};
 frozen.freeze_hash=await graphWireDigest(frozen);
 const contract={schema:'signal-graph-http-source-contract-v177',family:'http-mapped-acquisition-v177',scope:sourceScope,
  source_id:actualSource.id,source_hash:actualSource.content_hash,extractor_id:extractorID,
  extractor_hash:base.binding.extractor.content_hash,environment_hash:'d'.repeat(64),configuration_hash:base.binding.configuration_hash,
  mapping:frozen,field_units:{temperature:'°C'},simulation:true,test_only:true,field_verified:false};
 return seal({...base,binding:{...base.binding,[HTTP_SOURCE_CONTRACT_KEY]:contract}},'preview_hash');
}

test('checked nonempty subset marker is included in the real options hash',async()=>{
 const s=await scene();assert.deepEqual(s.options[IDS],[sourceID]);
 const old=await scene({http:false});assert.equal(Object.hasOwn(old.options,IDS),false);
 await assert.rejects(checkGraphRouteOptions(wire({...old.rawOptions,[IDS]:[sourceID]}),scope));
});

test('options reject empty, duplicated, out-of-source or non-UUID source markers even after resealing',async()=>{
 const s=await scene({http:false});
 for(const ids of [[],[sourceID,sourceID],[id(99)],['not-an-id'],[true],null,sourceID]){
  const bad=await seal({...s.rawOptions,[IDS]:ids},'options_hash');
  await assert.rejects(checkGraphRouteOptions(wire(bad),scope));
 }
 const unknown=await seal({...s.rawOptions,http_mapped_source_ids_v178:[sourceID]},'options_hash');
 await assert.rejects(checkGraphRouteOptions(wire(unknown),scope));
 assert.throws(()=>buildGraphRoutePreview(s.form,{...s.options,[IDS]:[id(99)]},scope));
});

test('actual HTTP builder emits every Goal/default explicitly and strict preview preserves it per object',async()=>{
 const s=await scene(),before=graphWireRequestJSON(s.command),goal=s.command.goal_spec;
 assert.deepEqual(goal.evidence,expectedHTTP);
 assert.deepEqual(Object.keys(goal).sort(),['contract_version','purpose','entity_ids','required_fields','criteria','subgoals',
  'evidence','observation_mode','deadline','max_steps','max_tool_calls','stop_on_conflict','relation_query','data_query'].sort());
 assert.equal(goal.observation_mode,'existing_only');assert.equal(goal.deadline,null);
 assert.equal(goal.stop_on_conflict,true);assert.equal(goal.relation_query,null);assert.equal(goal.data_query,null);
 for(const criterion of goal.criteria)for(const key of ['capability_id','field','operator','expected'])assert.equal(criterion[key],null);
 const checked=await checkGraphRoutePreview(wire(await preview(s)),s.command,scope);
 assert.deepEqual(checked.expanded[0].goal_spec.evidence,expectedHTTP);
 assert.deepEqual(checked.expanded[0].goal_spec.entity_ids,[entityID]);
 assert.equal(graphWireRequestJSON(s.command),before);
});

test('unlisted selection and old options retain archive bytes even if the name mentions HTTP',async()=>{
 const unmarked=await scene({http:false});assert.deepEqual(unmarked.command.goal_spec.evidence,expectedArchive);
 const listed=await scene({selected:manualID});assert.deepEqual(listed.command.goal_spec.evidence,expectedArchive);
 const withoutMarker=Object.fromEntries(Object.entries(listed.options).filter(([key])=>key!==IDS));
 assert.equal(graphWireRequestJSON(buildGraphRoutePreview(listed.form,withoutMarker,scope)),graphWireRequestJSON(listed.command));
 const checked=await checkGraphRoutePreview(wire(await preview(listed,listed.command,{http:false})),listed.command,scope);
 assert.deepEqual(checked.expanded[0].goal_spec.evidence,expectedArchive);
});

test('strict preview rejects changed or unsupported verification without rewriting the original HTTP or manual goal',async()=>{
 for(const http of [true,false]){
  const s=await scene({http}),before=graphWireRequestJSON(s.command),base=await preview(s,s.command,{http});
  for(const verification of http?['recorded','confirmed','unsupported']:['unverified_extraction','unsupported']){
   const changed=await seal({...base,expanded:base.expanded.map(row=>({...row,goal_spec:{...row.goal_spec,
    evidence:{...row.goal_spec.evidence,minimum_verification:verification}}}))},'preview_hash');
   await assert.rejects(checkGraphRoutePreview(wire(changed),s.command,scope));
  }
  assert.equal(graphWireRequestJSON(s.command),before);
 }
});

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),temps=[];let componentPromise;
after(async()=>{await Promise.all(temps.map(path=>rm(path,{recursive:true,force:true})));});
async function component(){
 if(!componentPromise)componentPromise=(async()=>{
  const temp=await mkdtemp(join(tmpdir(),'graph-http-goal-'));temps.push(temp);
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',
   build:{ssr:join(project,'src/SignalGraphRoutingStudioV134.jsx'),write:false,minify:false,
    rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:key=>key==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:key}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);
  const path=join(temp,'SignalGraphRoutingStudioV134.mjs');await writeFile(path,chunk.code);return import(pathToFileURL(path).href);
 })();return componentPromise;
}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources permitted');}}
async function rendered(preview){
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
  {url:'https://synthetic-http-goal.invalid/',resources:new NoResources()});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,
  localStorage:new Storage(),sessionStorage:session(),crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,fetch:()=>{throw Error('No network permitted');}};
 const originals=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 let root;const errors=[];
 try{
  const {createRoot}=await import('react-dom/client'),{GraphRoutingPreviewV134}=await component(),container=dom.window.document.getElementById('root');
  root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(GraphRoutingPreviewV134,{preview}))));
  const text=container.textContent;await act(async()=>root.unmount());root=null;assert.deepEqual(errors,[]);return text;
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();
  for(const [key,descriptor]of Object.entries(originals))if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
 }
}

test('ReactDOM target text describes the actual expanded SIM observation goal and preserves old archive meaning',async()=>{
 const s=await scene(),checked=await checkGraphRoutePreview(wire(await preview(s)),s.command,scope);
 const text=await rendered(checked);
 assert.ok(text.includes('近 1 小时的模拟观测（SIM，北京时间）'));
 assert.ok(text.includes('至少 1 条'));
 assert.ok(text.includes('非真实设备观测或现场事实'));assert.ok(text.includes('不会自动补采或更新对象档案'));
 const old=await scene({http:false}),oldChecked=await checkGraphRoutePreview(wire(await preview(old)),old.command,scope);
 const oldText=await rendered(oldChecked);
 assert.ok(oldText.includes('核对已登记的对象档案'));
 assert.equal(oldText.includes('近 1 小时的模拟观测（SIM，北京时间）'),false);
 assert.deepEqual(oldChecked.command.goal_spec.evidence,expectedArchive);
 assert.equal(graphWireCanonical(checked.expanded[0].goal_spec.evidence),graphWireCanonical(expectedHTTP));
});

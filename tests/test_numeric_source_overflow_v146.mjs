// UI-contract boundary only: actual codec, query editor reader and DOM. No
// builtin/HTTP success, real network, database, browser or durable save is claimed.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const requireFrontend=createRequire(new URL('../design/resource-studio/package.json',import.meta.url));
const {JSDOM}=requireFrontend('jsdom');
const html=readFileSync(new URL('../src/generic_agents/web/index.html',import.meta.url),'utf8');
const inline=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];assert.ok(inline);
const assets=[...html.matchAll(/<script src="\/assets\/([^"?]+)(?:\?[^\"]*)?"><\/script>/g)]
  .map(match=>({name:match[1],source:readFileSync(new URL('../src/generic_agents/web/'+match[1],import.meta.url),'utf8')}));
const HUGE='1'+'0'.repeat(400),NEXT='1'+'0'.repeat(399)+'1';
const ids={tool:'11111111-1111-4111-8111-111111111111',package:'22222222-2222-4222-8222-222222222222',entity:'33333333-3333-4333-8333-333333333333'};
function fixture(enumField){
  // Generic read-only query schemas can declare an integer without fulltext's
  // 20/50 bounds. This is a source-parameter UI fixture, not an executed tool.
  const property='{"type":"integer","title":"原大整数"'+(enumField?',"enum":['+HUGE+','+NEXT+']':'')+'}';
  return '{"caps":[{"id":"'+ids.package+'","kind":"domain_package","status":"published","data":{"execution":{},"specification":{"data_tool_ids":["'+ids.tool+'"],"entity_schemas":{"plot":{"properties":{"large":'+property+'}}}}}},'
    +'{"id":"'+ids.tool+'","kind":"tool","status":"published","data":{"description":"UI 合同的已存只读参数","execution":{"query_tool":true,"adapter":"http","endpoint":"https://transport.invalid/query","side_effect":false,"input_bindings":{"records":"data_records"}},'
    +'"input_schema":{"type":"object","required":["records","large"],"properties":{"records":{"type":"array"},"large":'+property+'}},"output_schema":{"type":"object","properties":{}},"data_policy":{"external_allowed":true}}}],'
    +'"entities":[{"id":"'+ids.entity+'","schema_capability_id":"'+ids.package+'","entity_type":"plot","data":{"name":"原对象"}}],'
    +'"run":{"data":{"request":{"entity_ids":["'+ids.entity+'"]},"result":null}},"draft":{"tool_steps":[{"id":"original","capability_id":"'+ids.tool+'","input_from":"evidence","parameters":{"large":'+HUGE+'}}]}}';
}
function environment(enumField){
  const dom=new JSDOM(html,{url:'https://source-overflow.invalid/',runScripts:'outside-only'}),window=dom.window,context=dom.getInternalVMContext();
  window.TextEncoder=globalThis.TextEncoder;window.structuredClone=globalThis.structuredClone;
  const state={serialized:[],errors:[],network:[]};window.fetch=async path=>{state.network.push(path);throw Error('No transport is permitted in this UI contract');};
  window.addEventListener('error',event=>{state.errors.push(String(event.error?.message||event.message));event.preventDefault();});
  for(const asset of assets)vm.runInContext(asset.source,context,{filename:asset.name});vm.runInContext(inline,context,{filename:'actual-index-inline.js'});
  const host=window.document.createElement('dialog');host.innerHTML='<div id="overflowTools"></div><p id="needError" role="alert"></p><button id="serializeDraft">核对待保存正文</button>';
  window.document.body.append(host);window.fixtureWire=fixture(enumField);
  vm.runInContext('globalThis.sourceFixture=ordinaryNumericParse(fixtureWire);caps=sourceFixture.caps;entities=sourceFixture.entities;globalThis.overflowRead=mountQueryTools(document.querySelector("#overflowTools"),sourceFixture.run,sourceFixture.draft);',context);
  const input=()=>host.querySelector('[data-tool-param="large"]'),error=()=>host.querySelector('#needError');
  host.querySelector('#serializeDraft').onclick=()=>{error().textContent='';try{
    state.serialized.push(vm.runInContext('ordinaryNumericStringify({draft:{tool_steps:overflowRead()}})',context));
  }catch(cause){error().textContent=cause.message;}};
  const save=()=>host.querySelector('#serializeDraft').click();
  const change=(control,value)=>{control.value=value;for(const type of ['input','change'])control.dispatchEvent(new window.Event(type,{bubbles:true}));};
  return {window,state,host,input,error,save,change,close:()=>window.close()};
}
function clean(ui){assert.deepEqual(ui.state.errors,[]);assert.deepEqual(ui.state.network,[]);}
function exactOriginal(wire){assert.ok(wire.includes('"large":'+HUGE+'}'),'original 401-digit integer is serialized without an Infinity/rounded substitute');assert.ok(!wire.includes('null')&&!wire.includes('Infinity'),'serialized source is a valid original integer');}

test('V146 UI contract non-enum trusted integer with an Infinity projection stays visible and serializes unchanged',()=>{
  const ui=environment(false);try{
    assert.equal(ui.input().value,HUGE,'original integer lexeme is visible');ui.save();
    assert.equal(ui.state.serialized.length,1,'actual mounted reader permits an untouched source integer');exactOriginal(ui.state.serialized[0]);clean(ui);
  }finally{ui.close();}
});
test('V146 UI contract enum trusted overflowing integers keep exact distinct identities and new unsafe selection is blocked',()=>{
  const ui=environment(true);try{
    const select=ui.input(),label=()=>select.selectedOptions[0]?.textContent||'';
    assert.ok(label().includes(HUGE)&&/原.*保存/.test(label()),'original 401-digit integer remains visibly original');
    const options=[...select.options].filter(option=>option.textContent.trim()===HUGE||option.textContent.trim()===NEXT);
    assert.equal(options.length,2);assert.ok(options[0].value!==options[1].value,'Infinity aliases have separate option identities');
    ui.save();assert.equal(ui.state.serialized.length,1);exactOriginal(ui.state.serialized[0]);
    const next=options.find(option=>option.textContent.trim()===NEXT);ui.change(select,next.value);ui.save();
    assert.equal(ui.state.serialized.length,1,'new unsafe integer selection never becomes a saved-body candidate');assert.ok(label().includes(NEXT),'rejected selection is retained');
    assert.match(ui.error().textContent,/精度|整数|范围|数值/);clean(ui);
  }finally{ui.close();}
});
test('V146 UI contract fresh overflowing integer text is rejected without rounding or clearing the human input',()=>{
  const ui=environment(false);try{
    ui.change(ui.input(),NEXT);ui.save();assert.equal(ui.state.serialized.length,0);assert.equal(ui.input().value,NEXT,'human input remains available for correction');
    assert.match(ui.error().textContent,/精度|整数|范围|数值/);clean(ui);
  }finally{ui.close();}
});

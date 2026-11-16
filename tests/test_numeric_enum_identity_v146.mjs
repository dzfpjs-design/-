// Actual classic DOM, shipped scripts and raw HTTP strings. Transport is wholly
// synthetic; no real API, registered execution, model, PG or browser is claimed.
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
const manifest=JSON.parse(readFileSync(new URL('../examples/data_fulltext.json',import.meta.url),'utf8'));
const ids={actor:'11111111-1111-4111-8111-111111111111',workspace:'22222222-2222-4222-8222-222222222222',
  run:'33333333-3333-4333-8333-333333333333',entity:'44444444-4444-4444-8444-444444444444',
  package:'55555555-5555-4555-8555-555555555555',tool:'66666666-6666-4666-8666-666666666666',
  otherTool:'77777777-7777-4777-8777-777777777777'};
const literal=JSON.stringify,clone=value=>JSON.parse(literal(value));
const response=(wire,status=200)=>({ok:status>=200&&status<300,status,headers:{get:()=> 'application/json'},
  text:async()=>wire,json:async()=>JSON.parse(wire)});
const json=value=>response(literal(value));
const tick=()=>new Promise(resolve=>setTimeout(resolve,2));
async function until(predicate,message){for(let i=0;i<150;i++){if(predicate())return;await tick();}assert.ok(Boolean(predicate()),message);}
function memberRaw(wire,key){
  let index=wire.indexOf(literal(key)+':');assert.ok(index>=0,'actual body contains '+key);index+=literal(key).length+1;
  while(/\s/.test(wire[index]))index++;const start=index;let depth=0,string=false,escape=false;
  for(;index<wire.length;index++){const c=wire[index];if(string){if(escape)escape=false;else if(c==='\\')escape=true;else if(c==='"')string=false;continue;}
    if(c==='"'){string=true;continue;}if(c==='{'||c==='[')depth++;else if(c==='}'||c===']'){if(!depth)return wire.slice(start,index);if(!--depth)return wire.slice(start,index+1);}
    if(!depth&&c===',')return wire.slice(start,index);
  }throw Error('Synthetic server cannot locate submitted subtree');
}
function parameters({score='-0.0',unsafe=false}={}){
  return '{"field":"numeric_text","text":"1.0","top_k":1.0,"min_score":'+score+',"match_mode":"all_terms"'
    +(unsafe?',"unsafe_choice":9007199254740992,"flag":true':'')+'}';
}
function environment({twoSteps=false,uiBoundary=false}={}){
  // The additional unsafe/boolean fields are UI-only, never represented as a
  // valid fulltext execution. Ordinary cases only add legitimate enum limits.
  const first=clone(manifest),second=clone(manifest);
  for(const entry of [first,second]){
    entry.input_schema.properties.min_score.enum=entry===first?[0,1]:[2,3];
    entry.input_schema.properties.top_k.enum=[1,2];entry.input_schema.properties.text.enum=['1.0','2.0'];
  }
  if(uiBoundary){first.input_schema.properties.unsafe_choice={type:'integer',title:'UI 边界大整数选项',enum:['__unsafeA__','__unsafeB__']};
    first.input_schema.properties.flag={type:'boolean',title:'UI 边界布尔选项',enum:[true,false]};}
  const step=(id,params)=>'{"id":'+literal(id)+',"capability_id":'+literal(ids.tool)+',"input_from":"evidence","parameters":'+params+'}';
  const originalSteps='['+step('first',parameters({unsafe:uiBoundary}))+(twoSteps?','+step('second',parameters({score:'1.0'})):'')+']';
  const draft='{"question":"核对原枚举条件","fields":["numeric_text"],"business_timezone":null,"tool_steps":'+originalSteps+',"max_tool_calls":6,"max_tool_seconds":60,"allow_external_tools":false,"form_reason":"原保存依据"}';
  const state={calls:[],unexpected:[],errors:[],draft,version:1};
  const runWire=()=>'{"id":'+literal(ids.run)+',"version":'+state.version+',"status":"needs_confirmation","question":"原枚举查询","created_at":"2026-10-21T12:00:00+00:00","data":{'
    +'"request":{"question":"原枚举查询","entity_ids":['+literal(ids.entity)+'],"fields":["numeric_text"],"mode":"structured","tool_steps":'+originalSteps+'},"resolution_draft":'+state.draft+',"result":null}}';
  const capabilityWire=literal([
    {id:ids.package,kind:'domain_package',status:'published',data:{description:'合成业务包',execution:{},specification:{data_tool_ids:[ids.tool,ids.otherTool],entity_schemas:{plot:{properties:{numeric_text:{type:'string',title:'原文字段'}}}}}}},
    {id:ids.tool,kind:'tool',status:'published',release_version:'1.0.0',data:first},
    {id:ids.otherTool,kind:'tool',status:'published',release_version:'1.0.0',data:second}
  ]).replace('"__unsafeA__"','9007199254740992').replace('"__unsafeB__"','9007199254740993');
  const dom=new JSDOM(html,{url:'https://enum-query.invalid/',runScripts:'outside-only'}),window=dom.window,context=dom.getInternalVMContext();
  window.TextEncoder=globalThis.TextEncoder;window.structuredClone=globalThis.structuredClone;
  window.HTMLElement.prototype.scrollIntoView=function(){};
  window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event('close'));};
  window.addEventListener('error',event=>{state.errors.push(String(event.error?.message||event.message));event.preventDefault();});
  window.sessionStorage.setItem('ga_csrf','synthetic-csrf');
  window.fetch=async(path,options={})=>{
    const method=options.method||'GET';state.calls.push({method,path,body:options.body});
    if(method==='GET'&&path==='/api/me')return json({user_id:ids.actor,workspace_id:ids.workspace,role:'operator',username:'合成操作者',workspaces:[{id:ids.workspace,name:'枚举条件工作区'}]});
    if(method==='GET'&&path==='/api/capabilities')return response(capabilityWire);
    if(method==='GET'&&path==='/api/entities')return json([{id:ids.entity,version:1,schema_capability_id:ids.package,entity_type:'plot',external_id:'原对象',data:{name:'原对象'}}]);
    if(method==='GET'&&path==='/api/overview')return json({tasks:[],plans:[],issues:[],observations:[],events:[],memories:[],governance:[],acquisitions:[]});
    if(method==='GET'&&path==='/api/data/queries')return response('['+runWire()+']');
    if(method==='GET'&&path==='/api/learning/overview')return json({candidates:[],sets:[],runs:[],deployments:[],trials:[]});
    if(method==='GET'&&path==='/api/relations')return json({items:[],truncated:false});
    if(method==='GET'&&path==='/api/quality')return json({policies:[]});
    if(method==='GET'&&path==='/api/data/gaps')return json({items:[],truncated:false});
    if(method==='GET'&&path==='/api/sensing?offset=0')return json({assets:[],plans:[]});
    if(method==='GET'&&path==='/api/sensing/devices?offset=0')return json({devices:[]});
    if(method==='PUT'&&path==='/api/data/queries/'+ids.run+'/draft'){
      assert.equal(JSON.parse(options.body).expected_version,state.version);state.draft=memberRaw(options.body,'draft');state.version++;
      return response(runWire());
    }
    state.unexpected.push(method+' '+path);throw Error('Unexpected synthetic HTTP: '+method+' '+path);
  };
  for(const asset of assets)vm.runInContext(asset.source,context,{filename:asset.name});vm.runInContext(inline,context,{filename:'actual-index-inline.js'});
  const query=selector=>window.document.querySelector(selector),writes=()=>state.calls.filter(call=>call.method!=='GET');
  const ready=async()=>{await until(()=>!query('#app').classList.contains('hidden'),'actual workbench is authenticated');query('nav [data-page="data"]').click();await tick();assert.ok(query('#queryForm')!==null,'actual Data page renders');};
  const open=async()=>{const button=[...query('[data-query="'+ids.run+'"]').querySelectorAll('button')].find(b=>b.textContent==='澄清并确认查询方案');assert.ok(button!==undefined,'actual query editor exists');button.click();await tick();assert.ok(query('#needSave')!==null,'actual query editor opens');};
  const select=(key,index=0)=>query('[data-tool-row="'+index+'"] [data-tool-param="'+key+'"]');
  const save=async()=>{const before=writes().length;query('#needReason').value='明确核对本次原枚举条件';query('#needSave').click();await until(()=>writes().length>before||Boolean(query('#needError')?.textContent),'save succeeds or explicitly rejects');await until(()=>!query('#needSave').disabled,'save controls settle');};
  const reopen=async()=>{query('#needClose').click();await open();};
  const run=source=>vm.runInContext(source,context);
  return {dom,window,state,query,writes,ready,open,select,save,reopen,run,close:()=>window.close()};
}
function currentLabel(select){return select.selectedOptions[0]?.textContent.trim()||'';}
function originalOption(select,token){return [...select.options].find(option=>/原.*保存/.test(option.textContent)&&option.textContent.includes(token));}
function registeredOption(select,token){return [...select.options].find(option=>option.value!==''&&!/原.*保存|原值|未核实/.test(option.textContent)&&option.textContent.trim()===token);}
function change(ui,select,option){assert.ok(option!==undefined,'visible registered/original option exists');select.value=option.value;for(const type of ['input','change'])select.dispatchEvent(new ui.window.Event(type,{bubbles:true}));}
function assertOriginal(select,token){assert.ok(select.value!=='','source value cannot become the empty/delete option');assert.ok(currentLabel(select).includes(token),'selected option visibly preserves original '+token);assert.match(currentLabel(select),/原.*保存/);}
function clean(ui){assert.deepEqual(ui.state.unexpected,[]);assert.deepEqual(ui.state.errors,[]);}

test('V146 real fulltext enum shows original negative zero, preserves untouched bytes and records an explicit registered zero choice',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();assertOriginal(ui.select('min_score'),'-0.0');
    await ui.save();assert.match(ui.writes()[0].body,/"min_score":-0\.0(?:[,}])/);await ui.reopen();assertOriginal(ui.select('min_score'),'-0.0');
    const select=ui.select('min_score'),zero=registeredOption(select,'0');assert.ok(zero?.value!==select.value,'original and registered zero have distinct local identities');
    change(ui,select,zero);assert.equal(currentLabel(select),'0');await ui.save();assert.match(ui.writes()[1].body,/"min_score":0(?:[,}])/);
    await ui.reopen();assert.ok(!currentLabel(ui.select('min_score')).includes('-0.0'),'server-saved new zero is not relabelled with old sign');clean(ui);
  }finally{ui.close();}
});
test('V146 real fulltext integer enum preserves source float one until an explicit integer choice',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();assertOriginal(ui.select('top_k'),'1.0');await ui.save();assert.match(ui.writes()[0].body,/"top_k":1\.0(?:[,}])/);
    await ui.reopen();change(ui,ui.select('top_k'),registeredOption(ui.select('top_k'),'1'));await ui.save();
    assert.match(ui.writes()[1].body,/"top_k":1(?:[,}])/);assert.ok(!/"top_k":1\.0(?:[,}])/.test(ui.writes()[1].body),'new integer does not borrow source float binding');clean(ui);
  }finally{ui.close();}
});
test('V146 restoring the immutable original enum restores only that field and keeps another explicit edit',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();const score=ui.select('min_score'),original=originalOption(score,'-0.0');
    assert.ok(original!==undefined,'original source snapshot is an explicit choice');change(ui,score,registeredOption(score,'1'));
    change(ui,ui.select('text'),registeredOption(ui.select('text'),'2.0'));change(ui,score,original);assertOriginal(score,'-0.0');await ui.save();
    assert.match(ui.writes()[0].body,/"min_score":-0\.0(?:[,}])/);const params=JSON.parse(ui.writes()[0].body).draft.tool_steps[0].parameters;
    assert.equal(params.text,'2.0','single-field original restoration cannot roll back the other human edit');clean(ui);
  }finally{ui.close();}
});
test('V146 numeric-shaped string enum and UI-only boolean enum retain their actual types',async()=>{
  const ui=environment({uiBoundary:true});try{await ui.ready();await ui.open();assert.equal(currentLabel(ui.select('text')),'1.0');
    change(ui,ui.select('text'),registeredOption(ui.select('text'),'2.0'));change(ui,ui.select('flag'),registeredOption(ui.select('flag'),'否'));await ui.save();
    const saved=JSON.parse(ui.writes()[0].body).draft.tool_steps[0].parameters;assert.equal(saved.text,'2.0');assert.equal(saved.flag,false);
    assert.match(ui.writes()[0].body,/"text":"2\.0"/);assert.match(ui.writes()[0].body,/"flag":false/);clean(ui);
  }finally{ui.close();}
});
test('V146 local enum choices cannot overwrite the corresponding source in another step',async()=>{
  const ui=environment({twoSteps:true});try{await ui.ready();await ui.open();assertOriginal(ui.select('min_score',0),'-0.0');assertOriginal(ui.select('min_score',1),'1.0');
    change(ui,ui.select('min_score',1),registeredOption(ui.select('min_score',1),'0'));await ui.save();
    const source=memberRaw(ui.writes()[0].body,'draft');assert.match(source,/"min_score":-0\.0(?:[,}])/);
    const saved=JSON.parse(source).tool_steps;assert.ok(Object.is(saved[0].parameters.min_score,-0));assert.equal(saved[1].parameters.min_score,0);
    assert.ok(!Object.is(saved[1].parameters.min_score,-0));clean(ui);
  }finally{ui.close();}
});
test('V146 changing the actual registered capability discards the old enum identity and old parameter binding',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();const cap=ui.query('[data-tool-cap]');cap.value=ids.otherTool;cap.dispatchEvent(new ui.window.Event('change',{bubbles:true}));await tick();
    const score=ui.select('min_score');assert.equal(score.value,'','new capability starts with no saved score');
    assert.ok(![...score.options].some(option=>/原.*保存/.test(option.textContent)),'old capability original option cannot survive the change');
    change(ui,score,registeredOption(score,'2'));await ui.save();const saved=JSON.parse(ui.writes()[0].body).draft.tool_steps[0];
    assert.equal(saved.capability_id,ids.otherTool);assert.equal(saved.parameters.min_score,2);assert.ok(!Object.hasOwn(saved.parameters,'top_k'),'old capability parameters are not silently copied');clean(ui);
  }finally{ui.close();}
});
test('V146 UI-only unsafe integer enum keeps exact source identity and blocks a new unsafe choice without losing the selection',async()=>{
  const ui=environment({uiBoundary:true});try{await ui.ready();await ui.open();const select=ui.select('unsafe_choice');assertOriginal(select,'9007199254740992');
    const first=registeredOption(select,'9007199254740992'),second=registeredOption(select,'9007199254740993');
    assert.ok(first!==undefined&&second!==undefined&&first.value!==second.value,'Number-alias integer options retain separate identities');
    await ui.save();assert.match(ui.writes()[0].body,/"unsafe_choice":9007199254740992(?:[,}])/);await ui.reopen();
    const current=ui.select('unsafe_choice');change(ui,current,registeredOption(current,'9007199254740993'));await ui.save();
    assert.equal(ui.writes().length,1,'fresh unsafe integer never reaches POST/PUT');assert.ok(currentLabel(current).includes('9007199254740993'),'rejected human selection remains visible');
    assert.match(ui.query('#needError').textContent,/精度|整数|范围|数值/);clean(ui);
  }finally{ui.close();}
});
test('V146 UI-only lost metadata cannot manufacture an original float type or silently send a rounded value',async()=>{
  const ui=environment();try{await ui.ready();
    // Exercise the real editor with a deliberately legacy JSON-cloned record;
    // no codec/WeakMap or authority gate is mocked or replaced.
    ui.run('openNeedEditor(JSON.parse(JSON.stringify(queries[0])))');await tick();
    const select=ui.select('min_score');assert.match(currentLabel(select),/未核实|重新填写/);assert.ok(!originalOption(select,'-0.0'),'lost sign/type is never fabricated');
    await ui.save();assert.equal(ui.writes().length,0);assert.match(ui.query('#needError').textContent,/未核实|数值/);clean(ui);
  }finally{ui.close();}
});
test('V146 clear and remove keep the original enum value from reappearing in a saved incomplete draft',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();const select=ui.select('min_score');
    const blank=[...select.options].find(option=>option.value==='');change(ui,select,blank);await ui.save();
    assert.ok(!Object.hasOwn(JSON.parse(ui.writes()[0].body).draft.tool_steps[0].parameters,'min_score'),'explicit clear deletes the source binding');
    await ui.reopen();assert.equal(ui.select('min_score').value,'');ui.query('[data-tool-remove]').click();await tick();await ui.save();
    assert.deepEqual(JSON.parse(ui.writes()[1].body).draft.tool_steps,[]);clean(ui);
  }finally{ui.close();}
});

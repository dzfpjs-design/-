// Real shipped classic DOM and original response bytes; synthetic HTTP only.
// Root runs this source. It is not real-browser, API, PG or model evidence.
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
const ids={actor:'11111111-1111-4111-8111-111111111111',workspace:'22222222-2222-4222-8222-222222222222',
  run:'33333333-3333-4333-8333-333333333333',entity:'44444444-4444-4444-8444-444444444444',
  package:'55555555-5555-4555-8555-555555555555',tool:'66666666-6666-4666-8666-666666666666',
  next:'77777777-7777-4777-8777-777777777777'};
const literal=JSON.stringify;
const manifest=JSON.parse(readFileSync(new URL('../examples/data_fulltext.json',import.meta.url),'utf8'));
// These numeric spellings are authored HTTP bytes, never stringify(Number).
const numeric='{"negative":-0.0,"unit":1.0,"scale":1e21,"huge":9007199254740993,"numeric_text":"1.0","nested":[-0.0,1.0,1e21,9007199254740993,"1.0"]}';
const parameters='{"field":"numeric_text","text":"1.0","top_k":1.0,"min_score":-0.0,"match_mode":"all_terms"}';
const steps='[{"id":"calculate","capability_id":'+literal(ids.tool)+',"input_from":"evidence","parameters":'+parameters+'}]';
const fields='["negative","unit","scale","huge","numeric_text","nested"]';
const initialDraft='{"question":"原查询条件","fields":'+fields+',"business_timezone":null,"tool_steps":'+steps+',"max_tool_calls":6,"max_tool_seconds":60,"allow_external_tools":false,"form_reason":"原保存依据"}';
function runWire({version=1,draft=initialDraft,id=ids.run}={}) {
  return '{"id":'+literal(id)+',"version":'+version+',"status":"sufficient","question":"原数字证据查询","created_at":"2026-10-21T12:00:00+00:00","data":{'
    +'"request":{"question":"原数字证据查询","entity_ids":['+literal(ids.entity)+'],"fields":'+fields+',"mode":"structured","tool_steps":'+steps+'},'
    +'"resolution_draft":'+draft+',"result":{"reasoning_mode":"structured","evidence":[{"entity_id":'+literal(ids.entity)+',"version":1,"observed_at":null,"attributes":'+numeric+'}],'
    +'"observation_evidence":[],"trusted_cases":[],"gaps":[],"conflicts":[],"derivation_verification":"deterministic_registered_computation",'
    +'"tool_trace":[{"capability_id":'+literal(ids.tool)+',"title":"本地登记计算","release_version":"1.0.0","status":"succeeded","step":{"parameters":'+parameters+'},"inputs":{"records":[]},"output":{}}],'
    +'"derived_records":[{"values":'+numeric+',"entity_ids":['+literal(ids.entity)+'],"evidence_refs":["entity:original-version"]}]}}}';
}
function rawResponse(wire,status=200){return {ok:status>=200&&status<300,status,headers:{get:()=> 'application/json'},
  text:async()=>wire,json:async()=>JSON.parse(wire)};}
const jsonResponse=(value,status=200)=>rawResponse(literal(value),status);
const tick=()=>new Promise(resolve=>setTimeout(resolve,2));
async function until(predicate,message){for(let i=0;i<150;i++){if(predicate())return;await tick();}assert.ok(Boolean(predicate()),message);}
// Test server copies the exact submitted subtree, not a numeric re-encoder.
function rawMember(wire,key){
  let index=wire.indexOf(literal(key)+':');assert.ok(index>=0,'submitted body contains '+key);index+=literal(key).length+1;
  while(/\s/.test(wire[index]))index++;const start=index;let depth=0,inString=false,escape=false;
  for(;index<wire.length;index++){
    const ch=wire[index];if(inString){if(escape)escape=false;else if(ch==='\\')escape=true;else if(ch==='"')inString=false;continue;}
    if(ch==='"'){inString=true;continue;}if(ch==='{'||ch==='[')depth++;else if(ch==='}'||ch===']'){if(depth===0)return wire.slice(start,index);depth--;if(depth===0)return wire.slice(start,index+1);}
    if(depth===0&&(ch===','||ch==='}'||ch===']'))return wire.slice(start,index);
  }throw Error('Test server could not isolate exact submitted '+key);
}
function environment(){
  const dom=new JSDOM(html,{url:'https://ordinary-query.invalid/',runScripts:'outside-only'}),window=dom.window,context=dom.getInternalVMContext();
  window.TextEncoder=globalThis.TextEncoder;
  const state={calls:[],unexpected:[],errors:[],version:1,draft:initialDraft,listOverride:null};
  const labels={negative:'负零',unit:'浮点一',scale:'指数',huge:'大整数',numeric_text:'数字形字符串',nested:'嵌套数值'};
  const schema=Object.fromEntries(Object.entries(labels).map(([key,title])=>[key,{title,type:key==='numeric_text'?'string':key==='nested'?'array':key==='huge'?'integer':'number'}]));
  window.structuredClone=globalThis.structuredClone;
  window.HTMLElement.prototype.scrollIntoView=function(){};
  window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event('close'));};
  window.addEventListener('error',event=>{state.errors.push(String(event.error?.message||event.message));event.preventDefault();});
  window.sessionStorage.setItem('ga_csrf','synthetic-csrf');
  window.fetch=async(path,options={})=>{
    const method=options.method||'GET';state.calls.push({path,method,body:options.body});
    if(method==='GET'&&path==='/api/me')return jsonResponse({user_id:ids.actor,workspace_id:ids.workspace,role:'operator',username:'合成操作者',workspaces:[{id:ids.workspace,name:'原数字工作区'}]});
    if(method==='GET'&&path==='/api/capabilities')return jsonResponse([
      {id:ids.package,kind:'domain_package',status:'published',data:{description:'合成业务包',execution:{},specification:{data_tool_ids:[ids.tool],entity_schemas:{plot:{properties:schema}}}}},
      {id:ids.tool,kind:'tool',status:'published',release_version:'1.0.0',data:manifest}
    ]);
    if(method==='GET'&&path==='/api/entities')return jsonResponse([{id:ids.entity,version:1,schema_capability_id:ids.package,entity_type:'plot',external_id:'原对象',data:{name:'原对象'}}]);
    if(method==='GET'&&path==='/api/overview')return jsonResponse({tasks:[],plans:[],issues:[],observations:[],events:[],memories:[],governance:[],acquisitions:[]});
    if(method==='GET'&&path==='/api/data/queries')return rawResponse(state.listOverride??'['+runWire({version:state.version,draft:state.draft})+']');
    if(method==='GET'&&path==='/api/learning/overview')return jsonResponse({candidates:[],sets:[],runs:[],deployments:[],trials:[]});
    if(method==='GET'&&path==='/api/relations')return jsonResponse({items:[],truncated:false});
    if(method==='GET'&&path==='/api/quality')return jsonResponse({policies:[]});
    if(method==='GET'&&path==='/api/data/gaps')return jsonResponse({items:[],truncated:false});
    if(method==='GET'&&path==='/api/sensing?offset=0')return jsonResponse({assets:[],plans:[]});
    if(method==='GET'&&path==='/api/sensing/devices?offset=0')return jsonResponse({devices:[]});
    if(method==='PUT'&&path==='/api/data/queries/'+ids.run+'/draft'){
      const mirror=JSON.parse(options.body);assert.equal(mirror.expected_version,state.version);state.draft=rawMember(options.body,'draft');state.version++;
      return rawResponse(runWire({version:state.version,draft:state.draft}));
    }
    if(method==='POST'&&path==='/api/data/queries/'+ids.run+'/confirm')return jsonResponse({query:{id:ids.next}});
    state.unexpected.push(method+' '+path);throw Error('Unexpected synthetic HTTP: '+method+' '+path);
  };
  for(const asset of assets)vm.runInContext(asset.source,context,{filename:asset.name});vm.runInContext(inline,context,{filename:'actual-index-inline.js'});
  const query=selector=>window.document.querySelector(selector),mutations=()=>state.calls.filter(call=>call.method!=='GET');
  const ready=async()=>{await until(()=>!query('#app').classList.contains('hidden'),'actual workbench authenticates');query('nav [data-page="data"]').click();await tick();assert.ok(query('#queryForm')!==null,'actual Data page renders');};
  const open=async()=>{const button=[...query('[data-query="'+ids.run+'"]').querySelectorAll('button')].find(b=>b.textContent==='核查条件并新建查询');assert.ok(button!==undefined,'actual query condition editor exists');button.click();await tick();assert.ok(query('#needSave')!==null,'actual condition dialog opens');};
  const edit=(selector,raw)=>{const input=query(selector);input.value=raw;input.dispatchEvent(new window.Event('input',{bubbles:true}));};
  const save=async()=>{const count=mutations().length;query('#needSave').click();await until(()=>mutations().length>count||Boolean(query('#needError')?.textContent),'save completes or clearly rejects');await until(()=>!query('#needSave').disabled,'save controls recover');};
  return {dom,window,state,query,mutations,ready,open,edit,save,close:()=>window.close()};
}
function valueFor(root,label){const span=[...root.querySelectorAll('.kv > span')].find(node=>node.textContent===label);assert.ok(span!==undefined,'value is human-labelled '+label);return span.nextElementSibling.textContent;}
function assertTokens(wire){
  assert.match(wire,/"min_score"\s*:\s*-0\.0(?:[,}])/);assert.match(wire,/"top_k"\s*:\s*1\.0(?:[,}])/);
  assert.match(wire,/"text"\s*:\s*"1\.0"/);
}
function clean(ui){assert.deepEqual(ui.state.unexpected,[]);assert.deepEqual(ui.state.errors,[]);}

test('V145 actual ordinary evidence displays original signed float, exponent and unsafe integer bytes without casting strings',async()=>{
  const ui=environment();try{await ui.ready();const card=ui.query('[data-query="'+ids.run+'"]');
    for(const [label,text] of [['负零','-0.0'],['浮点一','1.0'],['指数','1e21'],['大整数','9007199254740993'],['数字形字符串','1.0']])assert.equal(valueFor(card,label),text,label+' original HTTP token');
    assert.equal(valueFor(card,'嵌套数值').replace(/\s/g,''),'[-0.0,1.0,1e21,9007199254740993,"1.0"]');
    assert.equal(ui.mutations().length,0);clean(ui);
  }finally{ui.close();}
});
test('V145 actual tool receipt and derived result preserve original numeric tokens beside their evidence',async()=>{
  const ui=environment();try{await ui.ready();const card=ui.query('[data-query="'+ids.run+'"]'),trace=[...card.querySelectorAll('section')].find(node=>node.querySelector('h3')?.textContent==='查询执行回执');
    assert.ok(trace!==undefined,'real tool receipt section is present');assert.match(trace.textContent,/最低全文匹配分：-0\.0/);assert.match(trace.textContent,/最多返回几条证据：1\.0/);
    const derived=card.querySelector('[data-derived-query]');assert.ok(derived!==null,'real derived result section is present');
    assert.equal(valueFor(derived,'负零'),'-0.0');assert.equal(valueFor(derived,'浮点一'),'1.0');assert.equal(valueFor(derived,'大整数'),'9007199254740993');
    assert.equal(ui.mutations().length,0);clean(ui);
  }finally{ui.close();}
});
test('V145 actual unedited fulltext tool draft saves, reopens and confirms original float tokens even on an integer schema',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();
    assert.equal(ui.query('[data-tool-param="min_score"]').value,'-0.0');assert.equal(ui.query('[data-tool-param="top_k"]').value,'1.0');assert.equal(ui.query('[data-tool-param="text"]').value,'1.0');
    ui.edit('#needReason','核对原条件，保留原数字');await ui.save();assert.equal(ui.mutations().length,1);assertTokens(ui.mutations()[0].body);
    ui.query('#needClose').click();await ui.open();assert.equal(ui.query('[data-tool-param="top_k"]').value,'1.0');
    ui.query('#needConfirm').click();await until(()=>ui.mutations().some(call=>call.path.endsWith('/confirm')),'confirmation uses the actual saved draft endpoint');
    const writes=ui.mutations();assert.equal(writes.length,3);assertTokens(writes[1].body);assert.equal(JSON.parse(writes[2].body).expected_version,3);
    assert.equal(writes.filter(call=>call.method==='POST').length,1,'no new query/model fallback is sent');clean(ui);
  }finally{ui.close();}
});
test('V145 actual equal-number edits and clear-then-retype establish fresh raw tokens instead of retaining the old binding',async()=>{
  const ui=environment();try{await ui.ready();await ui.open();ui.edit('[data-tool-param="min_score"]','-0.00');ui.edit('#needReason','明确输入新数值形式');await ui.save();
    assert.match(ui.mutations()[0].body,/"min_score"\s*:\s*-0\.00(?:[,}])/,'new input cannot silently inherit old -0.0 token');
    ui.query('#needClose').click();await ui.open();assert.equal(ui.query('[data-tool-param="min_score"]').value,'-0.00');
    ui.edit('[data-tool-param="min_score"]','');ui.edit('[data-tool-param="min_score"]','1e0');ui.edit('[data-tool-param="top_k"]','1.0');ui.edit('#needReason','清空后明确输入新数值');await ui.save();
    assert.match(ui.mutations()[1].body,/"min_score"\s*:\s*1e0(?:[,}])/,'new exponent token replaces the cleared binding');
    assert.match(ui.mutations()[1].body,/"top_k"\s*:\s*1(?:[,}])/,'explicit fresh integer edit follows integer policy rather than inheriting the old float token');
    assert.equal(ui.query('[data-tool-param="min_score"]').value,'1e0');clean(ui);
  }finally{ui.close();}
});
test('V145 actual fresh unsafe integer or incomplete numeric input is blocked while the human raw draft stays visible',async()=>{
  for(const [key,raw] of [['top_k','9007199254740995'],['top_k','1.5'],['min_score','1.'],['min_score','-'],['min_score','1e999'],['min_score','51']]){
    const ui=environment();try{await ui.ready();await ui.open();ui.edit('[data-tool-param="'+key+'"]',raw);ui.edit('#needReason','保留输入待核对');await ui.save();
      assert.equal(ui.mutations().length,0,'invalid fresh number cannot be rounded and saved');assert.equal(ui.query('[data-tool-param="'+key+'"]').value,raw,'human input is retained');
      assert.match(ui.query('#needError').textContent,/数值|整数|数字|精度|范围/);clean(ui);
    }finally{ui.close();}
  }
});
test('V145 actual API response parser rejects duplicate or nonfinite numeric wire instead of adopting a new query result',async()=>{
  for(const broken of [runWire().replace('"negative":-0.0','"negative":-0.0,"negative":1.0'),runWire().replace('"negative":-0.0','"negative":1e999')]){
    const ui=environment();try{await ui.ready();ui.state.listOverride='['+broken+']';ui.query('#refresh').click();
      await until(()=>!ui.query('#refresh').disabled,'real response refresh finishes');assert.ok(ui.query('#notice').classList.contains('error'),'strict numeric parse error is visible');
      assert.equal(ui.mutations().length,0);clean(ui);
    }finally{ui.close();}
  }
});

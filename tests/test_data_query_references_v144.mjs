// Actual shipped classic-DOM application, with synthetic HTTP only. These tests
// do not establish browser, real API, database, model or durability acceptance.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const requireFrontend=createRequire(new URL('../design/resource-studio/package.json',import.meta.url));
const {JSDOM}=requireFrontend('jsdom');
const html=readFileSync(new URL('../src/generic_agents/web/index.html',import.meta.url),'utf8');
const inline=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(inline,'actual workbench inline application is present');
const assets=[...html.matchAll(/<script src="\/assets\/([^"?]+)(?:\?[^\"]*)?"><\/script>/g)]
  .map(match=>({name:match[1],source:readFileSync(new URL('../src/generic_agents/web/'+match[1],import.meta.url),'utf8')}));
const ids={actor:'11111111-1111-4111-8111-111111111111',other:'22222222-2222-4222-8222-222222222222',
  workspace:'33333333-3333-4333-8333-333333333333',instance:'44444444-4444-4444-8444-444444444444',
  entity:'55555555-5555-4555-8555-555555555555',task:'66666666-6666-4666-8666-666666666666',
  candidate:'77777777-7777-4777-8777-777777777777',nativeTask:'88888888-8888-4888-8888-888888888888',
  generic:'99999999-9999-4999-8999-999999999999',observation:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  nonce:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',release:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  job:'dddddddd-dddd-4ddd-8ddd-dddddddddddd',package:'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'};
const clone=value=>JSON.parse(JSON.stringify(value));
const actor=(role='operator')=>({user_id:ids.actor,workspace_id:ids.workspace,role,username:'合成操作者',
  workspaces:[{id:ids.workspace,name:'合成工作区'}]});
const response=(value,status=200)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(value),json:async()=>clone(value)});
function reference(family='native_candidate',changes={}) {
  const id=family==='native_task'?ids.nativeTask:ids.candidate;
  return {schema:'data-query-reference-v144',id,version:1,status:'sufficient',
    question:family==='native_task'?'原业务的受控查询':'已保存候选资料检索',created_at:'2026-10-21T10:00:00+00:00',
    reference_only:true,family,context:{instance_id:ids.instance,task_id:family==='native_task'?ids.task:null,
      detail_path:'/api/data/queries/'+id},notice:'请在原业务范围内核对；此处不重新执行查询。',...changes};
}
function candidate(changes={}) {
  return {schema:'native-candidate-query-view-v139',id:ids.candidate,version:1,status:'sufficient',request_id:ids.nonce,
    request:{request_id:ids.nonce,release_id:ids.release,entity_id:ids.entity,field:'search_text',text:'差异候选'},
    job_id:ids.job,integrity_error:null,source_changed:false,not_authoritative:true,candidate_only:true,
    records:[{observation_id:ids.observation,entity_id:ids.entity,task_id:ids.task,name:'北区样地的候选资料',
      attributes:{'差异候选像素数':3,'未知像素数':1},review_state:'confirmed',
      source_time_verified:false,physical_state_verified:false}],truncated:false,...changes};
}
function generic() {
  return {id:ids.generic,version:1,status:'sufficient',question:'普通档案温度查询',created_at:'2026-10-21T09:00:00+00:00',
    data:{request:{question:'普通档案温度查询',entity_ids:[ids.entity],fields:['temperature'],mode:'structured',
      tool_steps:[],business_timezone:null},result:{reasoning_mode:'structured',evidence:[{entity_id:ids.entity,
      version:1,attributes:{temperature:18},observed_at:null}],observation_evidence:[],trusted_cases:[],gaps:[],conflicts:[]}}};
}
function deferred(){let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};}
const tick=()=>new Promise(resolve=>setTimeout(resolve,2));
async function until(predicate,message){for(let i=0;i<100;i++){if(predicate())return;await tick();}assert.ok(Boolean(predicate()),message);}

function environment({rows=[generic(),reference(),reference('native_task')],role='operator',detail=candidate()}={}) {
  const dom=new JSDOM(html,{url:'https://workbench.invalid/',runScripts:'outside-only'}),window=dom.window;
  const context=dom.getInternalVMContext(),state={cookie:actor(role),calls:[],unexpected:[],errors:[],detail,
    detailRead:null,rows:clone(rows),tasks:[]};
  window.TextEncoder=globalThis.TextEncoder;
  window.addEventListener('error',event=>{state.errors.push(String(event.error?.message||event.message));event.preventDefault();});
  window.sessionStorage.setItem('ga_csrf','synthetic-csrf');
  window.structuredClone=clone;
  window.fetch=async(path,options={})=>{
    const method=options.method||'GET';state.calls.push({method,path,headers:clone(options.headers||{}),body:options.body});
    if(method==='GET'&&path==='/api/me')return response(state.cookie);
    if(method==='GET'&&path==='/api/capabilities')return response([{id:ids.package,kind:'domain_package',status:'published',
      data:{description:'合成业务包',execution:{},specification:{entity_schemas:{plot:{properties:{temperature:{type:'number',title:'温度'}}}}}}}]);
    if(method==='GET'&&path==='/api/entities')return response([{id:ids.entity,version:1,schema_capability_id:ids.package,
      entity_type:'plot',external_id:'北区样地',data:{name:'北区样地'}}]);
    if(method==='GET'&&path==='/api/overview')return response({tasks:state.tasks,plans:[],issues:[],observations:[],events:[],
      memories:[],governance:[],acquisitions:[]});
    if(method==='GET'&&path==='/api/data/queries')return response(state.rows);
    if(method==='GET'&&path==='/api/learning/overview')return response({candidates:[],sets:[],runs:[],deployments:[],trials:[]});
    if(method==='GET'&&path==='/api/relations')return response({items:[],truncated:false});
    if(method==='GET'&&path==='/api/quality')return response({policies:[]});
    if(method==='GET'&&path==='/api/data/gaps')return response({items:[],truncated:false});
    if(method==='GET'&&path==='/api/sensing?offset=0')return response({assets:[],plans:[]});
    if(method==='GET'&&path==='/api/sensing/devices?offset=0')return response({devices:[]});
    if(method==='GET'&&path==='/api/data/queries/'+ids.candidate)
      return state.detailRead?state.detailRead(options):response(state.detail);
    if(method==='POST'&&path==='/api/data/queries')return response(generic(),201);
    state.unexpected.push(method+' '+path);throw Error('Unexpected synthetic HTTP: '+method+' '+path);
  };
  for(const asset of assets)vm.runInContext(asset.source,context,{filename:asset.name});
  vm.runInContext(inline,context,{filename:'actual-index-inline.js'});
  const query=selector=>window.document.querySelector(selector);
  const ready=async()=>until(()=>!query('#app').classList.contains('hidden'),'actual authenticated workbench is ready');
  const openData=async()=>{await ready();query('nav [data-page="data"]').click();await tick();
    assert.ok(query('#queryForm')!==null,'actual Data form is rendered');};
  const card=id=>query('[data-query-reference="'+id+'"]');
  const mutations=()=>state.calls.filter(call=>!['GET','HEAD'].includes(call.method));
  const close=()=>{window.close();};
  return {dom,window,state,query,ready,openData,card,mutations,close};
}
function assertClean(ui){assert.deepEqual(ui.state.unexpected,[]);assert.deepEqual(ui.state.errors,[]);}

test('V144 actual Data page discovers safe native references and reads original candidate detail without generic write controls',async()=>{
  const ui=environment();try{
    await ui.openData();const card=ui.card(ids.candidate);
    assert.ok(card!==null,'native owner query remains discoverable as a safe reference');
    assert.ok(ui.card(ids.nativeTask)!==null,'original Task reference remains discoverable');
    assert.ok(ui.query('[data-query="'+ids.generic+'"]')!==null,'ordinary query still has its original evidence card');
    assert.ok(!card.textContent.includes('差异候选像素数'),'list descriptor has no raw result');
    const read=card.querySelector('[data-query-reference-read]');assert.ok(read!==null,'reference has an explicit read-only check');
    read.click();await until(()=>card.textContent.includes('差异候选像素数'),'checked original candidate contents are visible');
    assert.match(card.textContent,/候选/);assert.match(card.textContent,/已核查/);assert.match(card.textContent,/现场|采集时间/);
    const link=card.querySelector('a[data-query-task-context]');assert.ok(link!==null,'record exposes its original Task context');
    assert.equal(link.getAttribute('href'),'/setup/?view=workspace&instance='+ids.instance+'&task='+ids.task);
    assert.ok(!card.textContent.includes(ids.observation),'internal record identifiers stay out of human content');
    assert.ok(!card.textContent.includes(ids.job),'internal Job identifiers stay out of human content');
    assert.ok(!/澄清并确认查询方案|按此范围重新核查|核查并确认推导|取消此查询/.test(card.textContent),'four legacy augmenters do not add generic mutations');
    assert.equal(ui.state.calls.filter(call=>call.path==='/api/data/queries/'+ids.candidate).length,1);
    assert.equal(ui.state.calls.find(call=>call.path==='/api/data/queries/'+ids.candidate).headers['X-Workspace-ID'],ids.workspace);
    assert.equal(ui.mutations().length,0);assertClean(ui);
  }finally{ui.close();}
});

test('V144 actual ordinary structured query and Relations navigation preserve their original flow beside references',async()=>{
  const ui=environment();try{
    await ui.openData();ui.query('#question').value='查询这份原档案';ui.query('[name="entity"]').checked=true;
    ui.query('#fields').value='temperature';ui.query('#mode').value='structured';
    const form=ui.query('#queryForm');form.requestSubmit(form.querySelector('button'));
    await until(()=>ui.mutations().length===1,'ordinary structured query sends its original POST');
    const sent=JSON.parse(ui.mutations()[0].body);assert.equal(sent.question,'查询这份原档案');
    assert.deepEqual(sent.entity_ids,[ids.entity]);assert.deepEqual(sent.fields,['temperature']);assert.equal(sent.mode,'structured');
    await until(()=>!ui.query('#queryForm button').disabled,'ordinary saved query refresh settles');
    ui.query('nav [data-page="relations"]').click();await tick();
    assert.ok(ui.query('#relationQueryForm')!==null,'actual relation form renders without reading reference.data');
    assert.equal(ui.mutations().length,1);assertClean(ui);
  }finally{ui.close();}
});

test('V144 actual viewer can follow the native Task reference without gaining generic mutation controls',async()=>{
  const ui=environment({role:'viewer'});try{
    await ui.openData();const card=ui.card(ids.nativeTask);assert.ok(card!==null,'readable original Task is discoverable');
    const link=card.querySelector('a[data-query-task-context]');assert.ok(link!==null,'Task reference links to its original context');
    assert.equal(link.getAttribute('href'),'/setup/?view=workspace&instance='+ids.instance+'&task='+ids.task);
    const read=ui.card(ids.candidate).querySelector('[data-query-reference-read]');
    assert.ok(read!==null&&!read.disabled,'read-only candidate check remains usable for its owner viewer');
    read.click();await until(()=>ui.card(ids.candidate).textContent.includes('差异候选像素数'),'viewer can cold-read checked candidate detail');
    assert.equal(ui.mutations().length,0);assertClean(ui);
  }finally{ui.close();}
});

test('V144 actual reference cards reject foreign, expanded and mismatched detail paths without any transport or raw fallback',async()=>{
  for(const path of ['https://other.invalid/api/data/queries/'+ids.candidate,'//other.invalid/api/data/queries/'+ids.candidate,
    '/api/data/queries/'+ids.nativeTask,'/api/data/queries/'+ids.candidate+'?raw=1']){
    const row=reference();row.context.detail_path=path;const ui=environment({rows:[generic(),row]});try{
      await ui.openData();const card=ui.card(ids.candidate);assert.ok(card!==null,'invalid reference remains explicitly unavailable');
      assert.match(card.textContent,/未核|不可|异常/);assert.ok(card.querySelector('[data-query-reference-read]')===null,'invalid path grants no read action');
      assert.ok(card.querySelector('a')===null,'invalid path never becomes a navigation target');
      assert.equal(ui.state.calls.filter(call=>call.path.includes(ids.candidate)).length,0);
      assert.ok(ui.query('[data-query="'+ids.generic+'"]')!==null,'invalid reference does not erase ordinary evidence');
      assert.equal(ui.mutations().length,0);assertClean(ui);
    }finally{ui.close();}
  }
});

test('V144 actual failed original detail read preserves the reference and offers no query retry or raw fallback',async()=>{
  const ui=environment();try{
    ui.state.detailRead=async()=>response({detail:'原业务查询当前不可核实'},404);await ui.openData();
    const card=ui.card(ids.candidate);card.querySelector('[data-query-reference-read]').click();
    await until(()=>card.textContent.includes('原业务查询当前不可核实'),'original detail failure is visible');
    assert.ok(!card.textContent.includes('差异候选像素数'),'failed read has no invented candidate result');
    assert.equal(ui.state.calls.filter(call=>call.path==='/api/data/queries').length,1,'failed detail does not reload the raw list');
    assert.equal(ui.mutations().length,0);assertClean(ui);
  }finally{ui.close();}
});

test('V144 actual integrity or changed-source detail displays unconfirmed state without candidate contents',async()=>{
  const unverified=reference();
  unverified.context.instance_id=null;unverified.notice='原候选查询当前未核实，原记录保留';
  for(const detail of [candidate({integrity_error:'原记录暂不可核实',records:[]}),candidate({source_changed:true,records:[]}),unverified]){
    const ui=environment({detail});try{
      await ui.openData();const card=ui.card(ids.candidate);card.querySelector('[data-query-reference-read]').click();
      await until(()=>Boolean(card.querySelector('[data-query-reference-result]')?.textContent.trim()),'checked detail state is rendered');
      assert.match(card.textContent,/未核|变化|不可核/);assert.ok(!card.textContent.includes('差异候选像素数'),'unverified current detail does not expose old records');
      assert.equal(ui.mutations().length,0);assertClean(ui);
    }finally{ui.close();}
  }
});

test('V144 actual late detail cannot display old candidate content after the cookie identity changes',async()=>{
  const ui=environment(),held=deferred();try{
    ui.state.detailRead=()=>held.promise;await ui.openData();ui.card(ids.candidate).querySelector('[data-query-reference-read]').click();
    await until(()=>ui.state.calls.some(call=>call.path==='/api/data/queries/'+ids.candidate),'original scoped read begins');
    ui.state.cookie={...actor(),user_id:ids.other};held.resolve(response(candidate()));
    await until(()=>ui.query('#app').classList.contains('hidden'),'changed identity masks the original page');
    assert.ok(!ui.query('#content').textContent.includes('差异候选像素数'),'late original result never enters the old DOM');
    assert.equal(ui.mutations().length,0);assertClean(ui);
  }finally{held.resolve(response(candidate()));ui.close();}
});

test('V144 actual Tasks navigation preserves current and historical native query references beside the original Task',async()=>{
  const historyID='ffffffff-ffff-4fff-8fff-ffffffffffff',historical=reference('native_task',{
    id:historyID,question:'先前目标的受控查询',context:{instance_id:ids.instance,task_id:ids.task,
      detail_path:'/api/data/queries/'+historyID}}),ui=environment({rows:[generic(),reference('native_task'),historical]});
  try{
    await ui.openData();ui.state.tasks=[{id:ids.task,version:3,status:'completed',title:'原业务人工核查',
      package_id:ids.package,created_at:'2026-10-21T09:00:00+00:00',data:{entity_ids:[ids.entity],fields:['temperature'],
        current_plan_id:null,step_receipts:{},goal_version:2,goal_spec:{purpose:'核对原资料并人工确认',observation_mode:'existing_only',
          evidence:{source:'archive',minimum_verification:'recorded',minimum_records_per_entity:1},
          criteria:[{id:'human',kind:'human_confirmation',description:'人工核对原资料'}],data_query:{}},
        data_query_run_id:ids.nativeTask,data_query_history:[{query_id:historyID,goal_version:1}]}}];
    ui.query('#refresh').click();await until(()=>ui.state.calls.filter(call=>call.path==='/api/overview').length===2
      &&!ui.query('#refresh').disabled,'actual refresh adopts the original saved Task');
    ui.query('nav [data-page="tasks"]').click();await tick();
    const task=ui.query('[data-task="'+ids.task+'"]'),panel=task?.querySelector('[data-goal-query]');
    assert.ok(task!==null,'original Task card is rendered');assert.ok(panel!==null&&panel!==undefined,'delegated query section completes without raw data access');
    const current=panel.querySelector('[data-query-reference="'+ids.nativeTask+'"]');
    const old=panel.querySelector('[data-query-reference="'+historyID+'"]');
    assert.ok(current!==null,'current native query remains a safe reference');assert.ok(old!==null,'historical native query remains a safe reference');
    assert.match(old.closest('details').textContent,/目标第1版/);
    for(const entry of [current,old]){
      const link=entry.querySelector('a[data-query-task-context]');assert.ok(link!==null,'original context remains reachable');
      assert.equal(link.getAttribute('href'),'/setup/?view=workspace&instance='+ids.instance+'&task='+ids.task);
      assert.ok(!/核查并确认推导|按此范围重新核查|取消此查询/.test(entry.textContent),'Task reference cannot enter generic query mutations');
    }
    assert.equal(ui.mutations().length,0);assertClean(ui);
  }finally{ui.close();}
});

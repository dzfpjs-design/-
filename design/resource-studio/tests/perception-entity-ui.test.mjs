import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount,installEventBrowser} from '../scripts/react-event-harness.mjs';
import {scene as stateScene,object as stateObject} from './object-state-background-v267.fixture.mjs';

const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild'),modules=new Map();
const account={workspace_id:'workspace-a',user_id:'user-a',username:'核查员',role:'operator'},instanceId='instance-a',releaseID='release-new';
const object=(patch={})=>({id:'original-entity',version:7,package_id:'package-original',entity_type:'object-a',name:'原对象',attributes:{field_title:'原对象',field_value:20},object_compatibility:{usable:true,status:'compatible',reason:'',target_release_id:releaseID,source_release_number:1,source_label:'原对象',source_type:'测量对象'},...patch});
const blocked=()=>object({object_compatibility:{...object().object_compatibility,usable:false,status:'incompatible',reason:'字段单位已改变，暂不可用于当前版本'}});
const finish=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const memory=()=>{const rows=new Map();return {get length(){return rows.size;},key:index=>[...rows.keys()][index]??null,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key),keys:()=>[...rows.keys()]};};
function nodes(value,out=[]){if(Array.isArray(value))value.forEach(item=>nodes(item,out));else if(value&&typeof value==='object'&&value.props){out.push(value);nodes(value.props.children,out);}return out;}
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):typeof value==='string'?value:'';
async function component(name){if(!modules.has(name)){const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/'+name+'.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',logLevel:'silent',loader:{'.css':'empty'},plugins:[{name:'reuse-react',setup(b){b.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});modules.set(name,import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64')));}return (await modules.get(name)).default;}
async function environment(run,{browser=false}={}){const keys=['fetch','localStorage','sessionStorage','window','location','history','setInterval','clearInterval'],old=Object.fromEntries(keys.map(key=>[key,globalThis[key]])),storage=memory(),components=[];Object.assign(globalThis,{localStorage:storage,sessionStorage:memory(),window:{addEventListener(){},removeEventListener(){}},location:{search:''},history:{replaceState(){}},setInterval:()=>1,clearInterval(){}});const eventBrowser=browser?installEventBrowser({account,storage}):null;try{await run({storage,eventBrowser,mount:(C,p)=>{const ui=mount(C,p);components.push(ui);return ui;}});}finally{components.forEach(ui=>ui.unmount());eventBrowser?.restore();Object.assign(globalThis,old);}}
const props=(extra={})=>({account,instanceId,objects:[object()],releaseID,Btn:'button',Field:'label',...extra});

test('manual event harness callback cache follows Object.is, dependency length and absent-dependency semantics',()=>{
 const properties={deps:[NaN,-0],value:'first'};
 function Probe(props){const value=props.value,callback=React.useCallback(()=>value,props.deps);return React.createElement('button',{onClick:callback},'callback probe');}
 const ui=mount(Probe,properties);try{const first=ui.button('callback probe').props.onClick;properties.value='second';ui.render();assert.equal(ui.button('callback probe').props.onClick,first);assert.equal(first(),'first');properties.deps=[NaN,0];ui.render();const second=ui.button('callback probe').props.onClick;assert.notEqual(second,first);assert.equal(second(),'second');properties.deps=[NaN,0,'extra'];ui.render();const longer=ui.button('callback probe').props.onClick;assert.notEqual(longer,second);properties.deps=undefined;ui.render();const without=ui.button('callback probe').props.onClick;ui.render();assert.notEqual(ui.button('callback probe').props.onClick,without);}finally{ui.unmount();}
});

test('real workspace passes original entity and exact current release to perception entrances without expanding device props',async()=>environment(async env=>{
 const Workspace=await component('BusinessWorkspace'),release={id:releaseID,package_id:'package-new',snapshot:{object_types:[],businesses:[]}};
 globalThis.fetch=async path=>path==='/api/instances'?finish([{id:instanceId,status:'partially_active',active_release_id:releaseID,enabled_business_ids:[]}]):path.endsWith('/releases')?finish([release]):path.endsWith('/objects')?finish([object()]):finish([]);
 const ui=env.mount(Workspace,{instanceId,account,Btn:'button',Field:'label'});await ui.flush();const tree=nodes(ui.render());
 for(const name of ['ObjectStateStudio','NativeReceiptStudio','LocalObservationStudio','SignalStudio','EvidenceAssessmentStudio']){const node=tree.find(n=>n.type?.name===name);assert.ok(node,name);assert.deepEqual(node.props.objects.map(o=>o.id),['original-entity'],name);assert.equal(node.props.releaseID,releaseID,name);assert.equal(node.props.objects[0].package_id,'package-original');assert.equal(node.props.objects[0].object_compatibility.usable,true);}
 assert.equal(tree.find(n=>n.type?.name==='MqttDailyStudio').props.objects,undefined);
}));

test('state keeps a saved unavailable selection visible and blocks a new refresh even through its handler',async()=>environment(async env=>{
 const State=await component('ObjectStateStudio'),f=stateScene(),key='state-form:state-refresh';env.storage.setItem(key,JSON.stringify({selected:f.entityId,pending:null}));
 const incompatible=stateObject();incompatible.object_compatibility={...incompatible.object_compatibility,usable:false,status:'incompatible',reason:'字段单位已改变，暂不可用于当前版本'};
 const ui=env.mount(State,{account:f.account,instanceId:f.instanceId,releaseID:f.releaseID,Btn:'button',formKey:'state-form',objects:[incompatible],canWrite:true,request:f.api,storage:env.storage,tabStorage:f.session,locks:f.locks});await ui.flush();
 assert.equal(ui.byAria('状态核查对象').props.value,f.entityId);assert.equal(ui.button('请求后台本地重新核对').props.disabled,true);await ui.click('请求后台本地重新核对');assert.equal(f.calls.filter(r=>r.method==='POST').length,0);assert.match(text(ui.render()),/字段单位已改变/);
 ui.byAria('状态核查对象').props.onChange({target:{value:''}});ui.render();assert.equal(JSON.parse(env.storage.getItem(key)).selected,'');
}));

test('state compatible original entity refreshes by original ID without package rewrite',async()=>environment(async env=>{
 const State=await component('ObjectStateStudio'),f=stateScene();const ui=env.mount(State,{account:f.account,instanceId:f.instanceId,releaseID:f.releaseID,objects:[stateObject()],Btn:'button',formKey:'state-compatible',canWrite:true,request:f.api,storage:env.storage,tabStorage:f.session,locks:f.locks});await ui.flush();await ui.input('状态核查对象',f.entityId);await ui.flush();await ui.click('请求后台本地重新核对');const posts=f.calls.filter(r=>r.method==='POST');assert.equal(posts.length,1);assert.ok(posts[0].path.includes('/objects/'+f.entityId+'/refresh-state'));assert.equal(Object.hasOwn(JSON.parse(posts[0].raw),'package_id'),false);assert.equal(f.calls.some(r=>r.path.endsWith('/state')),false);
}));

test('native receipts retain history reads while invalid object scope cannot start a sync',async()=>environment(async env=>{
 const Receipt=await component('NativeReceiptStudio'),requests=[],base='/api/workspace/instances/'+instanceId+'/native-receipts';globalThis.fetch=async(path,options={})=>{
  requests.push({path,method:options.method});assert.equal(options.method===undefined||options.method==='GET',true,'history fixture authorizes only reads');
  if(path==='/api/me')return finish(account);
  if(path===base||path===base+'?entity_ids=original-entity')return finish({available:1,truncated:false,preview_hash:'saved-preview',not_ready:0,candidates:[],records:[],daily:[],history_count:1,has_more:false});
  throw Error('Unexpected native receipt history fixture route '+path);
 };
 const ui=env.mount(Receipt,props({objects:[blocked()],onOpenTask(){}}));await ui.flush();await ui.input('多模归集对象范围','original-entity');await ui.flush();assert.equal(ui.button('归集已完成处理').props.disabled,true);await ui.click('归集已完成处理');assert.equal(requests.filter(r=>r.method==='POST').length,0);assert.ok(requests.some(r=>r.path.includes('entity_ids=original-entity')));assert.match(text(ui.render()),/字段单位已改变/);
 const identityReads=requests.filter(row=>row.path==='/api/me').length,historyReads=requests.filter(row=>row.path===base+'?entity_ids=original-entity').length;
 env.eventBrowser.window.dispatchEvent(new env.eventBrowser.window.Event('focus'));await ui.flush();
 assert.ok(requests.filter(row=>row.path==='/api/me').length>identityReads,'real focus listener freshly verifies identity');assert.ok(requests.filter(row=>row.path===base+'?entity_ids=original-entity').length>historyReads,'focus reload retains the original selected object');assert.equal(requests.filter(row=>row.method==='POST').length,0);assert.equal(ui.button('归集已完成处理').props.disabled,true);assert.match(text(ui.render()),/字段单位已改变/);
},{browser:true}));

test('local observation keeps old draft and model choice, blocking new upload for incompatible source',async()=>environment(async env=>{
 const Local=await component('LocalObservationStudio'),key='ga-local-observation:'+account.workspace_id+':'+account.username+':'+instanceId,form={entity_id:'original-entity',extractor_id:'local-model',selected:'',drafts:{review:'尚未提交的人稿'}};env.storage.setItem(key,JSON.stringify({form,action:null}));const posts=[];
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts.push(path);throw Error('must not upload');}if(path.endsWith('/local-observation-models'))return finish([{id:'local-model',name:'合成本地模型目录',version:'1',deployment:'local',candidate_only:true}]);if(path.endsWith('/local-observations'))return finish({records:[],has_more:false});return finish([]);};
 const ui=env.mount(Local,props({objects:[blocked()]}));await ui.flush();assert.equal(ui.byAria('本地观测对象').props.value,'original-entity');assert.equal(ui.button('上传PNG或JPEG并在本机处理').props.disabled,true);
 const upload=nodes(ui.render()).find(n=>n.type==='input'&&n.props.type==='file');await upload.props.onChange({target:{files:[new File(['synthetic-only'],'fixture.png',{type:'image/png'})],value:'fixture.png'}});await ui.flush();assert.deepEqual(posts,[]);assert.deepEqual(JSON.parse(env.storage.getItem(key)).form.drafts,form.drafts);assert.equal(JSON.parse(env.storage.getItem(key)).action,null);
}));

test('assessment preserves invalid cached selection and purpose while rejecting a forced new request',async()=>environment(async env=>{
 const Assessment=await component('EvidenceAssessmentStudio'),key='ga-evidence-assessment:'+account.workspace_id+':'+account.username+':'+instanceId,form={purpose:'保留核查用途人稿',goal:'available_evidence',required_modalities:['archive'],lookback_hours:24,entity_ids:['original-entity']};env.storage.setItem(key,JSON.stringify({form,action:null,handoff:null}));const posts=[];globalThis.fetch=async(path,options={})=>{if(options.method==='POST')posts.push(path);return finish([]);};
 const ui=env.mount(Assessment,props({objects:[blocked()],release:{id:releaseID,snapshot:{businesses:[]}},enabledBusinesses:[],onViewTask(){}}));await ui.flush();assert.equal(ui.button('保存条件并核查资料').props.disabled,true);await ui.click('保存条件并核查资料');assert.deepEqual(posts,[]);assert.deepEqual(JSON.parse(env.storage.getItem(key)).form,form);
}));

test('candidate rule draft is retained and cannot infer eligibility from matching object name or package',async()=>environment(async env=>{
 const Watch=await component('CandidateWatchStudio'),base='/workspace/instances/'+instanceId,key='ga-candidate-watch:'+account.workspace_id+':'+account.username+':'+base,form={object:'original-entity',profile:'native:VideoRun',operator:'gte',threshold:'1',needle:'',label:'',minScore:'0',name:'保留的线索规则',confirmed:true};env.storage.setItem(key,JSON.stringify({form,pending:null,saved:null}));let posts=0;
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts++;return finish({id:'should-not-be-created'});}return finish(path.endsWith('/candidate-watch-options')?[{key:'native:VideoRun',name:'原生视频结果',family:'native',value_kind:'number'}]:{records:[],has_more:false});};
 const ui=env.mount(Watch,props({objects:[blocked()],base,writable:true}));await ui.flush();assert.equal(ui.button('保存并启用线索规则').props.disabled,true);await ui.click('保存并启用线索规则');assert.equal(posts,0);assert.deepEqual(JSON.parse(env.storage.getItem(key)).form,form);
}));

test('manual registration cannot start against an invalid old object and never erases typed input',async()=>environment(async env=>{
 const Manual=await component('ManualObservationForm'),key='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId,values={note:'保留的人工正文'},cache={object:'original-entity',source:'source-a',rule:'rule-a',values,when:'2026-10-16T09:00',pending:null,saved:null,attempts:[]};env.storage.setItem(key,JSON.stringify(cache));const writes=[];
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){writes.push(path);throw Error('must not register');}return finish(path==='/api/me'?account:{sources:[{id:'source-a',name:'人工来源',timezone:'Asia/Shanghai'}],rules:[{id:'rule-a',name:'原样登记',registration_mode:'passthrough',registration_fields:[{key:'note',label:'说明',type:'string',required:true}]}]});};
 const ui=env.mount(Manual,props({objects:[blocked()],enabled:true,onRefresh:async()=>{}}));await ui.flush();assert.equal(ui.button('保存登记并本地处理').props.disabled,true);await ui.click('保存登记并本地处理');assert.deepEqual(writes,[]);assert.deepEqual(JSON.parse(env.storage.getItem(key)).values,values);assert.equal(JSON.parse(env.storage.getItem(key)).pending,null);
}));

test('manual registration remains blocked without server qualification or with proof for another release',async()=>environment(async env=>{
 const Manual=await component('ManualObservationForm'),key='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId,values={note:'无资格旧fixture正文保留'},cache={object:'original-entity',source:'source-a',rule:'rule-a',values,when:'2026-10-16T09:00',pending:null,saved:null,attempts:[]},writes=[];globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){writes.push(path);throw Error('must not register');}return finish(path==='/api/me'?account:{sources:[{id:'source-a',name:'人工来源',timezone:'Asia/Shanghai'}],rules:[{id:'rule-a',name:'原样登记',registration_mode:'passthrough',registration_fields:[{key:'note',label:'说明',type:'string',required:true}]}]});};
 for(const candidate of [object({object_compatibility:undefined}),object({object_compatibility:{...object().object_compatibility,target_release_id:'another-release'}})]){env.storage.setItem(key,JSON.stringify(cache));const ui=env.mount(Manual,props({objects:[candidate],enabled:true,onRefresh:async()=>{}}));await ui.flush();assert.equal(ui.button('保存登记并本地处理').props.disabled,true);await ui.click('保存登记并本地处理');assert.deepEqual(writes,[]);assert.deepEqual(JSON.parse(env.storage.getItem(key)).values,values);assert.equal(JSON.parse(env.storage.getItem(key)).pending,null);ui.unmount();}
}));

test('a new explicitly confirmed rule posts the original entity ID with the current release fence',async()=>environment(async env=>{
 const Watch=await component('CandidateWatchStudio'),base='/workspace/instances/'+instanceId,key='ga-candidate-watch:'+account.workspace_id+':'+account.username+':'+base,form={object:'original-entity',profile:'native:VideoRun',operator:'gte',threshold:'1',needle:'',label:'',minScore:'0',name:'R2明确确认的新规则',confirmed:true};env.storage.setItem(key,JSON.stringify({form,pending:null,saved:null}));const posts=[];
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts.push(JSON.parse(options.body));return finish({id:'saved-rule'});}return finish(path.endsWith('/candidate-watch-options')?[{key:'native:VideoRun',name:'原生结果',family:'native',value_kind:'number'}]:{records:[],has_more:false});};
 const ui=env.mount(Watch,props({base,writable:true}));await ui.flush();await ui.click('保存并启用线索规则');assert.equal(posts.length,1);assert.equal(posts[0].entity_id,'original-entity');assert.equal(posts[0].release_id,releaseID);assert.equal(posts[0].confirm_candidate_only,true);assert.equal(Object.hasOwn(posts[0],'package_id'),false);
}));

test('new manual registration keeps typed text and pins current release in a lost-response journal',async()=>environment(async env=>{
 const Manual=await component('ManualObservationForm'),key='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId,values={note:'  原文逐字保留  '};env.storage.setItem(key,JSON.stringify({object:'original-entity',source:'source-a',rule:'rule-a',values,when:'2026-10-16T09:00',pending:null,saved:null,attempts:[]}));const posts=[];
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts.push({path,body:JSON.parse(options.body)});throw Error('response lost');}return finish(path==='/api/me'?account:{sources:[{id:'source-a',name:'人工来源',timezone:'Asia/Shanghai'}],rules:[{id:'rule-a',name:'原样登记',registration_mode:'passthrough',registration_fields:[{key:'note',label:'说明',type:'string',required:true}]}]});};
 const ui=env.mount(Manual,props({enabled:true,onRefresh:async()=>{}}));await ui.flush();await ui.click('保存登记并本地处理');assert.equal(posts.length,1);assert.equal(posts[0].body.release_id,releaseID);assert.equal(posts[0].body.entity_id,'original-entity');assert.equal(posts[0].body.attributes.note,values.note);assert.deepEqual(JSON.parse(env.storage.getItem(key)).pending,posts[0].body);
}));

test('assessment retry cannot retag an old pending request and local abandonment preserves its complete original',async()=>environment(async env=>{
 const Assessment=await component('EvidenceAssessmentStudio'),key='ga-evidence-assessment:'+account.workspace_id+':'+account.username+':'+instanceId,form={purpose:'原核查用途',goal:'available_evidence',required_modalities:['archive'],lookback_hours:24,entity_ids:['original-entity']};env.storage.setItem(key,JSON.stringify({form,action:null,handoff:null}));const posts=[];globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts.push(JSON.parse(options.body));throw Error('response lost');}return finish([]);};
 const properties=props({release:{id:releaseID,snapshot:{businesses:[]}},enabledBusinesses:[],onViewTask(){}}),ui=env.mount(Assessment,properties);await ui.flush();await ui.click('保存条件并核查资料');assert.equal(posts.length,1);assert.equal(posts[0].release_id,releaseID);const pending=structuredClone(JSON.parse(env.storage.getItem(key)).action);
 properties.releaseID='release-next';properties.release={...properties.release,id:'release-next'};ui.render();await ui.click('核对并重试原请求');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(env.storage.getItem(key)).action,pending);
 await ui.click('保留原核查请求，取消本机等待');const archive=JSON.parse(env.storage.getItem(env.storage.keys().find(k=>k.startsWith(key+':retained:'))));assert.deepEqual(archive.request,pending);assert.equal(JSON.parse(archive.original_raw).form.purpose,form.purpose);assert.equal(JSON.parse(env.storage.getItem(key)).action,null);assert.deepEqual(JSON.parse(env.storage.getItem(key)).form,form);
}));

test('old untagged local image pending can probe history but never creates an observation under the new release',async()=>environment(async env=>{
 const Local=await component('LocalObservationStudio'),key='ga-local-observation:'+account.workspace_id+':'+account.username+':'+instanceId,action={request_id:'original-image-request',entity_id:'original-entity',extractor_id:'local-model',sha256:'a'.repeat(64),bytes:4,name:'原图.png',evidence_id:'saved-evidence'},form={entity_id:action.entity_id,extractor_id:action.extractor_id,selected:'',drafts:{old:'人稿保留'}};env.storage.setItem(key,JSON.stringify({form,action}));let posts=0,probes=0;
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts++;throw Error('must not create');}if(path.includes('/local-observation-requests/')){probes++;return finish({detail:'not found'},404);}if(path.endsWith('/local-observation-models'))return finish([]);if(path.endsWith('/local-observations'))return finish({records:[],has_more:false});return finish([]);};
 const ui=env.mount(Local,props());await ui.flush();await ui.click('核对并重试原提交');assert.equal(posts,0);assert.ok(probes>=2);assert.deepEqual(JSON.parse(env.storage.getItem(key)).action,action);assert.equal(Object.hasOwn(JSON.parse(env.storage.getItem(key)).action,'release_id'),false);
 await ui.click('保留原请求，取消本机等待');assert.equal(JSON.parse(env.storage.getItem(key)).action,null);assert.deepEqual(JSON.parse(env.storage.getItem(key)).form.drafts,form.drafts);const archive=JSON.parse(env.storage.getItem(env.storage.keys().find(k=>k.startsWith(key+':retained:'))));assert.deepEqual(archive.request,action);
}));

test('a disappeared state choice stays visible after refresh until the person cancels it',async()=>environment(async env=>{
 const State=await component('ObjectStateStudio'),f=stateScene(),key='retained-state:state-refresh';env.storage.setItem(key,JSON.stringify({selected:f.entityId,pending:null}));const properties={account:f.account,instanceId:f.instanceId,releaseID:f.releaseID,objects:[stateObject()],Btn:'button',formKey:'retained-state',canWrite:true,request:f.api,storage:env.storage,tabStorage:f.session,locks:f.locks},ui=env.mount(State,properties);await ui.flush();properties.objects=[];ui.render();await ui.flush();assert.equal(ui.byAria('状态核查对象').props.value,f.entityId);assert.match(text(ui.render()),/原选择对象（暂未载入，选择保留）/);assert.equal(JSON.parse(env.storage.getItem(key)).selected,f.entityId);assert.equal(f.calls.some(r=>r.path.endsWith('/state')||r.method==='POST'),false);
}));

async function signalFixture(env,basisRelease){
 const Signal=await component('SignalStudio'),base='/workspace/instances/'+instanceId,formKey='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId,cardKey=formKey+':signal:signal-a',business={id:'human-business',name:'人工核查',available:true,flow:{steps:[{agent:'人工确认'}]}};let signal={id:'signal-a',version:1,status:'unassigned',entity_id:'original-entity',instance_id:instanceId,note:'原说明',basis_hash:'original-basis-hash',basis:{release_id:basisRelease,object_name:'原对象',candidate_only:false},tasks:[],related_native_receipts:[]};const posts=[],notes=[];env.storage.setItem(cardKey,JSON.stringify({note:signal.note,dirty:false,business:business.id,version:1,pending:null,handoff:null,nativeLink:null}));
 globalThis.fetch=async(path,options={})=>{if(options.method==='PUT'){notes.push(JSON.parse(options.body));signal={...signal,note:notes.at(-1).note,version:signal.version+1};return finish(signal);}if(options.method==='POST'){posts.push(JSON.parse(options.body));return finish({id:'new-task'});}return finish(path.endsWith('/signals')?{records:[signal],has_more:false}:{records:[],has_more:false});};
 const ui=env.mount(Signal,props({enabled:true,release:{id:releaseID,snapshot:{businesses:[business]}},enabledBusinesses:[business.id],onViewTask(){},onViewAssessment(){}}));await ui.flush();const node=nodes(ui.render()).find(n=>n.type?.name==='SignalCard');assert.ok(node);const card=env.mount(node.type,node.props);await card.flush();return {card,posts,notes,signal};
}

test('R1 signal keeps human notes but cannot inherit current entity eligibility as R2 handoff authority',async()=>environment(async env=>{
 const {card,posts,notes,signal}=await signalFixture(env,'release-original'),before=structuredClone(signal.basis);await card.input('异常核查说明','先保存R1通知的人工说明');await card.click('保存核查说明');assert.equal(notes.length,1);assert.equal(card.button('交给所选业务处理').props.disabled,true);await card.click('交给所选业务处理');assert.equal(posts.length,0);assert.deepEqual(signal.basis,before);assert.match(text(card.render()),/不能自动交给新版本业务/);assert.equal(nodes(card.render()).find(n=>n.type?.name==='SignalAdviceStudio').props.canWrite,false);
}));

test('new R2 signal can explicitly hand off the original entity ID without changing its source package',async()=>environment(async env=>{
 const {card,posts}=await signalFixture(env,releaseID);assert.equal(card.button('交给所选业务处理').props.disabled,false);await card.click('交给所选业务处理');assert.equal(posts.length,1);assert.deepEqual(posts[0].entity_ids,['original-entity']);assert.equal(posts[0].release_id,releaseID);assert.equal(posts[0].signal_id,'signal-a');assert.equal(Object.hasOwn(posts[0],'package_id'),false);
}));

test('failed archive readback leaves the original pending request and draft untouched',async()=>{
 const {retainPerceptionPending}=await import('../src/perception-entity-contract.mjs'),storage=memory(),key='pending-archive',request={request_id:'same-request',release_id:'release-original'},raw=JSON.stringify({form:{purpose:'保留完整人稿'},action:request});storage.setItem(key,raw);const write=storage.setItem;storage.setItem=(name,value)=>{if(!name.includes(':retained:'))write(name,value);};assert.throws(()=>retainPerceptionPending(storage,key,request),/归档/);assert.equal(storage.getItem(key),raw);
});

test('a new image observation carries release_id while retaining the original Entity ID and evidence receipt',async()=>environment(async env=>{
 const Local=await component('LocalObservationStudio'),key='ga-local-observation:'+account.workspace_id+':'+account.username+':'+instanceId,form={entity_id:'original-entity',extractor_id:'local-model',selected:'',drafts:{}},file=new File(['synthetic-request-shape-only'],'fixture.png',{type:'image/png'}),sha=Buffer.from(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())).toString('hex'),posts=[];env.storage.setItem(key,JSON.stringify({form,action:null}));
 globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){posts.push({path,...(typeof options.body==='string'?{body:JSON.parse(options.body)}:{})});if(path==='/api/evidence')return finish({id:'saved-evidence',sha256:sha});throw Error('response lost');}if(path.includes('/local-observation-requests/'))return finish({detail:'not found'},404);if(path.endsWith('/local-observation-models'))return finish([{id:'local-model',name:'已登记本地目录',version:'1',deployment:'local',candidate_only:true}]);if(path.endsWith('/local-observations'))return finish({records:[],has_more:false});return finish([]);};
 const ui=env.mount(Local,props());await ui.flush();const input=nodes(ui.render()).find(n=>n.type==='input'&&n.props.type==='file');await input.props.onChange({target:{files:[file],value:'fixture.png'}});await ui.flush();assert.equal(posts.length,2);const body=posts[1].body;assert.equal(body.release_id,releaseID);assert.equal(body.entity_id,'original-entity');assert.equal(body.evidence_id,'saved-evidence');const saved=JSON.parse(env.storage.getItem(key));assert.equal(saved.action.release_id,releaseID);assert.equal(saved.action.sha256,sha);
}));

test('old rule history remains readable but current object usability never restores old rule authority',async()=>environment(async env=>{
 const Watch=await component('CandidateWatchStudio'),base='/workspace/instances/'+instanceId,old={id:'old-rule',version:2,status:'paused',definition:{release_id:'release-original',entity_id:'original-entity',name:'旧规则',profile:{name:'旧原生结果',family:'native'},operator:'gte',threshold:1}},writes=[];globalThis.fetch=async(path,options={})=>{if(options.method){writes.push(path);throw Error('must not activate old rule');}return finish(path.endsWith('/candidate-watch-options')?[]:{records:[old],has_more:false});};
 const ui=env.mount(Watch,props({base,writable:true}));await ui.flush();const node=nodes(ui.render()).find(n=>n.type?.name==='WatchCard'),card=env.mount(node.type,node.props);await card.flush();assert.equal(card.button('恢复此线索规则').props.disabled,true);await card.click('恢复此线索规则');await card.click('按此规则核查已有结果');assert.deepEqual(writes,[]);assert.match(text(card.render()),/原规则与核查记录保留/);
}));

test('invalid assessment scope cannot prepare new cloud text while saved question reads and human text remain',async()=>environment(async env=>{
 const Question=await component('EvidenceQuestionStudio'),key='ga-evidence-question:'+account.workspace_id+':'+account.username+':'+instanceId,form={question:'原问题草稿逐字保留',model_profile_id:'model-a'};env.storage.setItem(key,JSON.stringify({form,action:null,confirmation:null}));const writes=[];globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){writes.push(path);throw Error('must not preview or dispatch');}return finish(path.endsWith('/models')?[{id:'model-a',name:'已批准目录',provider:'qwen',model:'text'}]:[]);};
 const ui=env.mount(Question,props({objects:[blocked()],entityIds:['original-entity'],onAssessment(){},onBusy(){}}));await ui.flush();assert.equal(ui.button('查看将发送的文字').props.disabled,true);await ui.click('查看将发送的文字');assert.deepEqual(writes,[]);assert.equal(ui.byAria('自然语言资料问题').props.value,form.question);assert.deepEqual(JSON.parse(env.storage.getItem(key)).form,form);
}));

test('an old question keeps editable human criteria but visibly blocks confirmation into the current release',async()=>environment(async env=>{
 const Question=await component('EvidenceQuestionStudio'),key='ga-evidence-question:'+account.workspace_id+':'+account.username+':'+instanceId,criteria={purpose:'保留的人工核查条件',goal:'available_evidence',required_modalities:['video'],lookback_hours:24},row={id:'old-question',version:2,status:'needs_confirmation',created_at:'2026-10-16T09:00:00Z',request:{question:'旧问题原文',entity_ids:['original-entity'],release_id:'release-original'},draft:criteria,proposal:{...criteria,clarifications:[],unsupported_parts:[]}},writes=[];env.storage.setItem(key,JSON.stringify({selected:row.id,criteria,dirty:false}));globalThis.fetch=async(path,options={})=>{if(options.method){writes.push({path,method:options.method});return finish({...row,version:3,draft:JSON.parse(options.body).criteria});}return finish(path.endsWith('/models')?[]:[row]);};
 const ui=env.mount(Question,props({entityIds:['original-entity'],onAssessment(){},onBusy(){}}));await ui.flush();assert.equal(ui.button('确认条件，开始本地核查').props.disabled,true);assert.match(text(ui.render()),/原问题固定于原实例版本/);await ui.click('确认条件，开始本地核查');assert.equal(writes.length,0);await ui.input('自然语言核查确认用途','继续保留并保存旧问题人工条件');await ui.click('保存核对后的条件');assert.deepEqual(writes.map(w=>w.method),['PUT']);assert.equal(JSON.parse(env.storage.getItem(key)).criteria.purpose,'继续保留并保存旧问题人工条件');
}));

test('a saved question in the current release confirms its original eligible entity scope',async()=>environment(async env=>{
 const Question=await component('EvidenceQuestionStudio'),key='ga-evidence-question:'+account.workspace_id+':'+account.username+':'+instanceId,criteria={purpose:'当前版本明确核查',goal:'available_evidence',required_modalities:['video'],lookback_hours:24},row={id:'current-question',version:2,status:'needs_confirmation',created_at:'2026-10-16T09:00:00Z',request:{question:'当前问题原文',entity_ids:['original-entity'],release_id:releaseID},draft:criteria,proposal:{...criteria,clarifications:[],unsupported_parts:[]}},writes=[],opened=[];env.storage.setItem(key,JSON.stringify({selected:row.id,criteria,dirty:false}));globalThis.fetch=async(path,options={})=>{if(options.method==='POST'){writes.push(path);return finish({question:{...row,status:'applied',assessment_id:'saved-assessment'},assessment_id:'saved-assessment'});}return finish(path.endsWith('/models')?[]:[row]);};
 const ui=env.mount(Question,props({entityIds:['original-entity'],onAssessment:id=>opened.push(id),onBusy(){}}));await ui.flush();assert.equal(ui.button('确认条件，开始本地核查').props.disabled,false);await ui.click('确认条件，开始本地核查');assert.deepEqual(writes,['/api/evidence-questions/current-question/confirm']);assert.deepEqual(opened,['saved-assessment']);assert.equal(JSON.parse(env.storage.getItem(key)).confirmation,null);
}));

const gateway={id:'mqtt-source',version:4,config_hash:'source-hash',configuration:{topics:{'site/data':'site-device'},mapping:{t:{attribute:'温度',unit:'C'}}}};
const mqttObject=(patch={})=>({id:'original-entity',package_id:'original-package',entity_type:'station',label:'原对象',fields:[{key:'temperature',label:'温度',unit:'C'}],instance:{instance_id:instanceId,instance_name:'林业实例',instance_release_id:releaseID,instance_release_number:2,current:true,reason:null,businesses:[]},...patch});
const mqttDraft=(patch={})=>({requestId:'fixed-association-request',baseVersion:null,baseHash:gateway.config_hash,topic:'site/data',entity_id:'original-entity',extractor_id:'local-rule',field_links:{温度:'temperature'},include_existing:false,...patch});
const mqttRule={id:'local-rule',kind:'tool',status:'published',name:'已发布本地规则',data:{execution:{adapter:'builtin',handler:'numeric_rules'},allowed_agent_roles:['perception'],data_policy:{},specification:{}}};

test('MQTT association pins only the explicitly confirmed server destination and rejects stale instance drafts',async()=>{
 const {associationRequest}=await import('../src/mqtt-association-policy.mjs');
 assert.throws(()=>associationRequest(mqttDraft(),gateway,null,[mqttObject()],[mqttRule]),/目的地/);
 assert.throws(()=>associationRequest(mqttDraft({instance_release_id:'release-original'}),gateway,null,[mqttObject()],[mqttRule]),/目的地/);
 const request=associationRequest(mqttDraft({instance_release_id:releaseID}),gateway,null,[mqttObject()],[mqttRule]);assert.equal(request.instance_release_id,releaseID);assert.equal(request.entity_id,'original-entity');assert.equal(Object.hasOwn(request,'package_id'),false);
 const generic=associationRequest(mqttDraft(),gateway,null,[mqttObject({instance:null})],[mqttRule]);assert.equal(Object.hasOwn(generic,'instance_release_id'),false);
});

test('MQTT current false or missing server release metadata never grants a new destination',async()=>{
 const {associationRequest}=await import('../src/mqtt-association-policy.mjs');
 for(const instance of [{...mqttObject().instance,current:false,reason:'当前目的地需重新核对'}, {...mqttObject().instance,instance_release_id:null}])assert.throws(()=>associationRequest(mqttDraft({instance_release_id:releaseID}),gateway,null,[mqttObject({instance})],[mqttRule]),/目的地/);
});

test('MQTT original pending content and saved route destination are never retagged by fresh options',async()=>{
 const {associationRequest,savedAssociationDraft,sameAssociation}=await import('../src/mqtt-association-policy.mjs'),pending={request_id:'original-untagged-request',expected_version:4,topic:'site/data',entity_id:'original-entity',extractor_id:'local-rule',field_links:{温度:'temperature'},include_existing:false},row={...pending,id:'saved-route',version:2,config_hash:gateway.config_hash,instance:{id:instanceId,release_id:'release-original',release_number:1}};
 assert.deepEqual(associationRequest(mqttDraft({pending}),gateway,null,[mqttObject()],[mqttRule]),pending);assert.equal(Object.hasOwn(pending,'instance_release_id'),false);const draft=savedAssociationDraft(gateway,row);assert.equal(draft.instance_release_id,'release-original');assert.equal(sameAssociation({...draft,instance_release_id:releaseID},row),false);
});

async function mqttFixture(env,{draft=mqttDraft(),option=mqttObject(),fail=true}={}){
 const Panel=await component('MqttAssociationsPanel'),posts=[],key=gateway.id+':route:new',properties={gateway,account:{...account,role:'admin'},workspace:{mqttBindingDrafts:{[key]:draft}},onCommit(update){properties.workspace=update(properties.workspace);return true;},onSave:async()=>{},Btn:'button',Field:'label'},row={id:'saved-route',version:1,revision:1,config_hash:gateway.config_hash,topic:draft.topic,entity_id:draft.entity_id,extractor_id:draft.extractor_id,field_links:draft.field_links,status:'active',instance:{id:instanceId,release_id:releaseID,release_number:2},entity_label:'原对象',instance_name:'林业实例'};
 globalThis.fetch=async(path,options={})=>{if(options.method){posts.push({path,body:JSON.parse(options.body)});if(fail)throw Error('response lost');return finish(row);}if(path.endsWith('/routes'))return finish([]);if(path.includes('/domain-options/'))return finish(option);if(path.includes('/domain-options?'))return finish([option]);if(path.includes('/rule-options/'))return finish(mqttRule);if(path.includes('/rule-options?'))return finish([mqttRule]);throw Error('unexpected '+path);};
 const ui=env.mount(Panel,properties);await ui.flush();return {ui,posts,properties,key};
}

test('MQTT old editable draft requires an explicit current destination confirmation before a new request',async()=>environment(async env=>{
 const {ui,posts,properties,key}=await mqttFixture(env);assert.equal(ui.button('确认新增关联').props.disabled,true);assert.match(text(ui.render()),/目的地/);await ui.click('确认新增关联');assert.equal(posts.length,0);await ui.click('核对并采用当前业务目的地');assert.equal(properties.workspace.mqttBindingDrafts[key].instance_release_id,releaseID);const confirmedRequest=properties.workspace.mqttBindingDrafts[key].requestId;assert.notEqual(confirmedRequest,'fixed-association-request');await ui.click('确认新增关联');assert.equal(posts.length,1);assert.equal(posts[0].body.instance_release_id,releaseID);assert.equal(posts[0].body.request_id,confirmedRequest);assert.equal(properties.workspace.mqttBindingDrafts[key].pending.instance_release_id,releaseID);
}));

test('MQTT untagged pending request remains exact and visible while retrying its original receipt',async()=>environment(async env=>{
 const pending={request_id:'original-untagged-request',expected_version:4,topic:'site/data',entity_id:'original-entity',extractor_id:'local-rule',field_links:{温度:'temperature'},include_existing:false},draft=mqttDraft({pending,pendingPath:'/mqtt/gateways/'+gateway.id+'/routes',pendingMethod:'POST'}),{ui,posts,properties,key}=await mqttFixture(env,{draft});assert.match(text(ui.render()),/不能自动改签/);await ui.click('重试保存原请求');assert.equal(posts.length,1);assert.deepEqual(posts[0].body,pending);assert.deepEqual(properties.workspace.mqttBindingDrafts[key].pending,pending);assert.equal(properties.workspace.mqttBindingDrafts[key].requestId,draft.requestId);
}));

test('MQTT explicit destination reconfirmation creates a fresh nonce and requires fresh historical replay consent',async()=>environment(async env=>{
 const original=mqttDraft({instance_release_id:'release-original',include_existing:true}),{ui,posts,properties,key}=await mqttFixture(env,{draft:original});await ui.click('核对并采用当前业务目的地');const updated=properties.workspace.mqttBindingDrafts[key];assert.equal(posts.length,0);assert.notEqual(updated.requestId,original.requestId);assert.equal(updated.include_existing,false);assert.equal(updated.instance_release_id,releaseID);for(const field of ['topic','entity_id','extractor_id','field_links'])assert.deepEqual(updated[field],original[field]);assert.equal(Object.hasOwn(updated,'pending'),false);assert.match(text(ui.render()),/历史处理范围已重置为只处理以后数据/);
}));

test('evidence question preview describes usage recording and possible cost without implying every provider has a budget',async()=>{
 for(const provider of ['deepseek','qwen']) await environment(async env=>{
  const Question=await component('EvidenceQuestionStudio'),key='ga-evidence-question:'+account.workspace_id+':'+account.username+':'+instanceId;
  env.storage.setItem(key,JSON.stringify({form:{question:'核查最近观测',model_profile_id:'model-a'}}));
  const posts=[];globalThis.fetch=async(path,options={})=>{
   if(options.method==='POST'){posts.push(path);return finish({provider,model:'text-model',max_output_tokens:512,preview_hash:'preview',cloud_input:{question:'核查最近观测',objects:[{name:'原对象',type:'测量对象'}]},notice:'请核对内容'});}
   return finish(path.endsWith('/models')?[{id:'model-a',name:'模型',provider,model:'text-model'}]:[]);
  };
  const ui=env.mount(Question,props({entityIds:['original-entity'],onAssessment(){},onBusy(){}}));await ui.flush();await ui.click('查看将发送的文字');await ui.flush();
  const visible=text(ui.render());assert.match(visible,/调用用量会记录；可能产生云服务费用/);assert.doesNotMatch(visible,/调用计入模型网关预算/);assert.match(visible,provider==='deepseek'?/DeepSeek · text-model/:/Qwen · text-model/);assert.deepEqual(posts,['/api/evidence-questions/preview']);assert.equal(ui.button('保存问题并理解').props.disabled,true);
 });
});

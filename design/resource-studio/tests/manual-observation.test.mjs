import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {manualCanonical,manualHash,manualFields,registrationDefinition,registrationAttributes,reviewScope,reviewKey,readManualReview,writeManualReview,checkedManualProbe,checkedManualPost,checkedRegistrationResolution,reviewableRecord} from '../src/manual-registration-contract.mjs';

const H='a'.repeat(64),id='12345678-1234-4234-9234-123456789abc';
const account={workspace_id:'workspace-a',user_id:'actor-a',username:'登记员',role:'viewer'},instanceId='instance-a',releaseID='release-current';
const fields=[{key:'note',label:'交接标记',type:'string',required:true,minLength:1},{key:'quantity',label:'数量',type:'integer',required:false,minimum:0},{key:'checked',label:'已复核',type:'boolean',required:false},{key:'day',label:'业务日期',type:'string',format:'date',required:false},{key:'kind',label:'类别',type:'string',enum:['甲','乙'],required:false}];
const cap={id:'tool-a',name:'人工原样登记',version:1,content_hash:H,rules:[],registration_mode:'passthrough',registration_fields:fields};
const options={sources:[{id:'source-a',name:'人工已有资料',timezone:'Asia/Shanghai'}],rules:[cap]};
const objects=[{id:'object-a',version:3,package_id:'package-original',entity_type:'object-kind',name:'对象甲',object_compatibility:{usable:true,status:'compatible',reason:'',target_release_id:releaseID,source_release_number:1,source_label:'对象甲',source_type:'登记对象'}}],key='ga-reported:'+account.workspace_id+':'+account.username+':'+instanceId;
const memory=()=>{const map=new Map();return {getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)};};
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const scope=reviewScope(account,instanceId,'observation-a');
const body={request_id:id,expected_version:3,expected_input_hash:H,reason:'已核对登记原文',decision:'confirmed'};
const row=(patch={})=>({id:scope.observation_id,version:3,entity_id:'object-a',source_id:'source-a',extractor_id:'tool-a',request_id:'reported-original',processing_version:'1.0.0',status:'processed',data_received:true,can_verify:true,block_reason:null,review_basis:{fields,original_attributes:{note:'原样文本',checked:false},extracted_attributes:{note:'原样文本',checked:false},verification_input_hash:H,verification:null,source_name:'人工已有资料',extractor_name:'原样登记',observed_at:'2026-10-15T00:00:00Z',evidence_ids:[],integrity_issue:null},...patch});
const savedReview=async(value=body,patch={})=>({request_id:value.request_id,body_hash:await manualHash(value),reviewed_by:account.user_id,input_hash:value.expected_input_hash,decision:value.decision,reason:value.reason,...patch});
const postReceipt=async(value=body,patch={})=>({id:scope.observation_id,workspace_id:account.workspace_id,version:4,data:{verification_history:[await savedReview(value)]},...patch});
const probe=async(value=body,patch={})=>({observation_id:scope.observation_id,request_id:value.request_id,version:4,found:true,actor_matches:true,current:true,review:await savedReview(value),...patch});
function registrationRow(value){return row({request_id:value.source_record_id,entity_id:value.entity_id,source_id:value.source_id,extractor_id:value.extractor_id,processing_version:value.processing_version||'1.0.0',review_basis:{...row().review_basis,original_attributes:value.attributes,extracted_attributes:value.attributes}});}
function putReview(storage,value=body){const journal=readManualReview(storage,scope);return writeManualReview(storage,scope,{...journal,reason:value.reason,pending:{body:value}},journal);}
async function environment(run){
 const old=Object.fromEntries(['fetch','localStorage','sessionStorage'].map(key=>[key,globalThis[key]])),storage=memory(),components=[];
 globalThis.localStorage=storage;globalThis.sessionStorage=memory();
 const server=await createServer({configFile:false,cacheDir:'.m70-validation/vite-cache',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const {default:Form}=await server.ssrLoadModule('/src/ManualObservationForm.jsx'),{default:Review}=await server.ssrLoadModule('/src/ReportedObservationReview.jsx');
  const defaults={instanceId,account,releaseID,Btn:'button',onRefresh:async()=>{},objects,enabled:true};
  const make=(Component,props={})=>{for(const [key,value] of Object.entries(defaults))if(!(key in props))props[key]=value;if(Component===Review&&!props.record)props.record=row();const ui=mount(Component,props);components.push(ui);return ui;};
  await run({storage,form:props=>make(Form,props),review:props=>make(Review,props),loadSignal:()=>server.ssrLoadModule('/src/SignalStudio.jsx')});
 }finally{components.forEach(ui=>ui.unmount());await server.close();Object.assign(globalThis,old);}
}
function route(handler,{role='operator',catalog=options}={}){globalThis.fetch=async(path,request={})=>{if(path==='/api/me')return response({...account,role});if(path.endsWith('/reported-observation-options'))return response(catalog);return handler(path,request);};}
async function fill(ui,text='  原样文本  '){await ui.flush();await ui.input('规则登记对象','object-a');await ui.input('人工数据来源','source-a');await ui.input('本地处理方式','tool-a');await ui.input('登记字段 交接标记',text);}

test('typed registration preserves text/date/false, omits optional empty fields, and retains zero',()=>{
 const result=registrationAttributes(cap,{note:'  原样文本  ',quantity:'0',checked:false,day:'2024-02-29',kind:''});
 assert.deepEqual({...result},{note:'  原样文本  ',quantity:0,checked:false,day:'2024-02-29'});
 assert.equal(registrationDefinition(cap).mode,'passthrough');
 assert.deepEqual(registrationDefinition({rules:[{field:'temperature'}]}).fields,[{key:'temperature',label:'temperature',type:'number',required:true}]);
});
test('required/enum/date/numeric restrictions fail without coercing original text',()=>{
 for(const values of [{note:''},{note:3},{note:'字',quantity:' '},{note:'字',quantity:'1.2'},{note:'字',quantity:'Infinity'},{note:'字',quantity:'9007199254740993'},{note:'字',quantity:'-1'},{note:'字',checked:'false'},{note:'字',day:'2025-02-29'},{note:'字',kind:'丙'},{note:'字',extra:0}])assert.throws(()=>registrationAttributes(cap,values));
 const restricted={...cap,registration_fields:[{...fields[0],minLength:2,maxLength:3}]};assert.throws(()=>registrationAttributes(restricted,{note:'字'}));assert.equal(registrationAttributes(restricted,{note:'甲乙'}).note,'甲乙');
});
test('unsupported or malformed fields cannot fall back to permissive numeric rules',()=>{
 for(const candidate of [{...cap,registration_mode:'unsupported'},{...cap,registration_fields:[]},{...cap,registration_fields:[fields[0],fields[0]]},{...cap,registration_mode:null},{...cap,registration_fields:[{...fields[0],type:'object'}]}])assert.throws(()=>registrationDefinition(candidate));
 assert.throws(()=>manualFields([{...fields[0],enum:[1]}]));
 const pattern={...cap,registration_fields:[{...fields[0],pattern:'(a+)+$'}]};assert.equal(registrationAttributes(pattern,{note:'a'.repeat(100)+'!'}).note.length,101,'arbitrary server regex is never executed in browser');
});
test('review full-body digest/scope/CAS/readback protect immutable original request',async()=>{
 assert.equal(await manualHash(body),createHash('sha256').update(manualCanonical(body)).digest('hex'));
 const storage=memory(),original=putReview(storage);assert.notEqual(reviewKey(scope),reviewKey({...scope,user_id:'another'}));
 assert.throws(()=>writeManualReview(storage,scope,{...original,pending:{body:{...body,reason:'换了内容'}}},original));
 assert.throws(()=>writeManualReview(storage,scope,{...original,pending:null},original));
 const stale=readManualReview(storage,scope);storage.setItem(reviewKey(scope),JSON.stringify({...stale,reason:'另一页说明'}));assert.throws(()=>writeManualReview(storage,scope,stale,stale));
 const broken={getItem:()=>null,setItem(){}};assert.throws(()=>putReview(broken),/未完整保存/);
});
test('review receipt/probe bind observation, actor, request hash and exact original decision',async()=>{
 await checkedManualPost(await postReceipt(),{body},scope);await checkedManualProbe(await probe(body,{current:false}),{body},scope);
 for(const patch of [{observation_id:'other'},{request_id:'other'},{actor_matches:false},{review:await savedReview(body,{body_hash:'b'.repeat(64)})},{review:await savedReview(body,{reviewed_by:'other'})}])await assert.rejects(checkedManualProbe(await probe(body,patch),{body},scope));
 await checkedManualProbe(await probe(body,{found:false,review:null,actor_matches:false}),{body},scope);
 await assert.rejects(checkedManualProbe(await probe(body,{found:false}),{body},scope));
 assert.equal(reviewableRecord(row({data_received:false})),false);assert.equal(reviewableRecord(row({can_verify:false})),false);assert.equal(reviewableRecord(row()),true);
});
test('registration resolve distinguishes exact saved request, unknown and same-key conflict',()=>{
 const value={source_id:'source-a',source_record_id:'reported-a',entity_id:'object-a',extractor_id:'tool-a',attributes:{note:'原文'}};
 assert.equal(checkedRegistrationResolution({found:false,request_matches:null,record:null},value).found,false);
 assert.equal(checkedRegistrationResolution({found:true,request_matches:true,record:registrationRow(value)},value).request_matches,true);
 assert.equal(checkedRegistrationResolution({found:true,request_matches:false,record:registrationRow({...value,attributes:{note:'旧文'}})},value).request_matches,false);
 assert.throws(()=>checkedRegistrationResolution({found:true,request_matches:true,record:registrationRow({...value,attributes:{note:'旧文'}})},value));
});

test('real form handlers submit original strings and false through the shipping API body, then start next entry explicitly',()=>environment(async({storage,form})=>{
 let sent,posts=0,refreshes=0;route(async(path,request)=>{const value=JSON.parse(request.body);if(path.endsWith('/resolve'))return response({found:true,request_matches:true,record:registrationRow(value)});posts++;sent=value;return response({id:scope.observation_id,source_id:value.source_id,source_record_id:value.source_record_id,entity_id:value.entity_id,data:{extractor_id:value.extractor_id}},201);});
 const ui=form({onRefresh:async()=>refreshes++});await fill(ui);await ui.input('登记字段 已复核','false');await ui.input('登记字段 数量','0');await ui.click('保存登记并本地处理');
 assert.equal(posts,1);assert.equal(sent.release_id,releaseID);assert.equal(sent.attributes.note,'  原样文本  ');assert.equal(sent.attributes.checked,false);assert.equal(sent.attributes.quantity,0);assert.equal(sent.attributes.day,undefined);assert.equal(refreshes,1);
 assert.equal(ui.byAria('登记字段 交接标记').props.disabled,true);assert.equal(JSON.parse(storage.getItem(key)).saved,scope.observation_id);
 await ui.click('登记下一条');assert.equal(posts,1);assert.equal(ui.byAria('登记字段 交接标记').props.value,'');assert.deepEqual(JSON.parse(storage.getItem(key)).attempts[0].body,sent);
}));
test('lost registration response survives remount; unknown resolve does not claim success or change body',()=>environment(async({storage,form})=>{
 let sent,posts=0,found=false;route(async(path,request)=>{const value=JSON.parse(request.body);if(path.endsWith('/resolve'))return response(found?{found:true,request_matches:true,record:registrationRow(value)}:{found:false,request_matches:null,record:null});posts++;sent=value;throw Error('simulated lost response');});
 let ui=form();await fill(ui,'原稿');await ui.click('保存登记并本地处理');const original=JSON.parse(storage.getItem(key)).pending;ui.unmount();ui=form();await ui.flush();await ui.click('核对原登记请求');assert.equal(JSON.parse(storage.getItem(key)).saved,null);assert.deepEqual(JSON.parse(storage.getItem(key)).pending,original);assert.equal(posts,1);
 await ui.click('按原编号重试登记');assert.deepEqual(sent,original);assert.equal(posts,2);found=true;await ui.click('核对原登记请求');assert.equal(JSON.parse(storage.getItem(key)).saved,scope.observation_id);assert.equal(posts,2);
}));
test('explicit 422 correction archives original wire and reuses source identity; conflict is never reported as saved draft',()=>environment(async({storage,form})=>{
 const sent=[];let conflict=false;route(async(path,request)=>{const value=JSON.parse(request.body);if(path.endsWith('/resolve'))return response(conflict?{found:true,request_matches:false,record:registrationRow({...value,attributes:{note:'先前已保存正文'}})}:{found:false,request_matches:null,record:null});sent.push(value);return response({detail:sent.length===1?'服务器格式不符合':'同编号已有其他正文'},sent.length===1?422:409);});
 const ui=form();await fill(ui,'原始输入');await ui.click('保存登记并本地处理');const original=JSON.parse(storage.getItem(key)).pending;
 await ui.click('保留原请求并修正输入');assert.deepEqual(JSON.parse(storage.getItem(key)).attempts[0].body,original);assert.equal(ui.byAria('人工数据来源').props.disabled,true);
 await ui.input('登记字段 交接标记','修正输入');await ui.click('保存登记并本地处理');assert.equal(sent[1].source_record_id,original.source_record_id);assert.equal(sent[1].source_id,original.source_id);assert.notDeepEqual(sent[1].attributes,original.attributes);
 conflict=true;await ui.click('核对原登记请求');assert.equal(JSON.parse(storage.getItem(key)).saved,null);await ui.click('保留修正稿并结束冲突核对');assert.equal(JSON.parse(storage.getItem(key)).saved_matches,false);
 await ui.click('登记下一条');assert.equal(JSON.parse(storage.getItem(key)).attempts.at(-1).status,'conflict_not_saved');assert.deepEqual(JSON.parse(storage.getItem(key)).attempts.at(-1).body,sent[1]);
}));
test('storage failure, changed actor and unsupported form block registration before POST',()=>environment(async({storage,form})=>{
 let posts=0;route(async()=>{posts++;return response({});});let ui=form();await fill(ui);const set=storage.setItem;storage.setItem=()=>{throw Error('quota');};await ui.click('保存登记并本地处理');assert.equal(posts,0);ui.unmount();storage.setItem=set;
 route(async()=>{posts++;return response({});},{catalog:{...options,rules:[{...cap,registration_mode:'unsupported',registration_issue:'需专用表单'}]}});ui=form();await ui.flush();await ui.input('本地处理方式','tool-a');assert.equal(ui.button('保存登记并本地处理').props.disabled,true);ui.unmount();
 globalThis.fetch=async()=>response({...account,user_id:'other',role:'operator'});ui=form();await ui.flush();assert.equal(ui.button('保存登记并本地处理').props.disabled,true);assert.equal(posts,0);
}));
test('late registration response cannot clear another instance or replace a newer cached request',()=>environment(async({storage,form})=>{
 let release,sent;const gate=new Promise(resolve=>release=resolve);route(async(path,request)=>{const value=JSON.parse(request.body);if(path.endsWith('/resolve'))return response({found:true,request_matches:true,record:registrationRow(value)});sent=value;await gate;return response({source_id:value.source_id,source_record_id:value.source_record_id,entity_id:value.entity_id,data:{extractor_id:value.extractor_id}});});
 const props={},ui=form(props);await fill(ui);const posting=ui.click('保存登记并本地处理');await new Promise(resolve=>setImmediate(resolve));props.instanceId='instance-b';ui.render();await ui.flush();release();await posting;
 assert.deepEqual(JSON.parse(storage.getItem(key)).pending,sent);assert.equal(JSON.parse(storage.getItem(key)).saved,null);assert.equal(ui.byAria('规则登记对象').props.value,'');
}));
test('review waits for actual received and server permission, regardless of stale account role',()=>environment(async({review})=>{
 route(async()=>response({}));for(const patch of [{data_received:false},{can_verify:false},{status:'received'}]){const ui=review({record:row(patch)});await ui.flush();await ui.input('核查说明 '+scope.observation_id,'核对原文');assert.equal(ui.button('保存人工核查').props.disabled,true);ui.unmount();}
 const ui=review();await ui.flush();await ui.input('核查说明 '+scope.observation_id,'核对原文');assert.equal(ui.button('保存人工核查').props.disabled,false);
}));
test('real review event freezes exact hash/version/actor and acknowledges only matching saved receipt',()=>environment(async({storage,review})=>{
 let sent,posts=0,refreshes=0;route(async(path,request)=>{posts++;sent=JSON.parse(request.body);return response(await postReceipt(sent));});const ui=review({onRefresh:async()=>refreshes++});await ui.flush();await ui.input('核查判断 '+scope.observation_id,'rejected');await ui.input('核查说明 '+scope.observation_id,'原文与提取需要纠正');await ui.click('保存人工核查');
 assert.equal(sent.expected_version,3);assert.equal(sent.expected_input_hash,H);assert.equal(sent.decision,'rejected');assert.equal(posts,1);assert.equal(refreshes,1);const saved=readManualReview(storage,scope);assert.equal(saved.pending,null);assert.deepEqual(saved.history[0].body,sent);
}));
test('lost review response recovers exact historical request after remount, never treating found:false as success',()=>environment(async({storage,review})=>{
 let sent,posts=0,found=false;route(async(path,request)=>{if(path.endsWith('/verify')){posts++;sent=JSON.parse(request.body);throw Error('simulated lost response');}return response(await probe(sent,found?{current:false}:{found:false,review:null,actor_matches:false}));});
 let ui=review();await ui.flush();await ui.input('核查说明 '+scope.observation_id,'核对原文');await ui.click('保存人工核查');ui.unmount();ui=review();await ui.flush();await ui.click('核对原核查请求');assert.ok(readManualReview(storage,scope).pending);assert.equal(posts,1);
 found=true;await ui.click('核对原核查请求');const saved=readManualReview(storage,scope);assert.equal(saved.pending,null);assert.equal(saved.history[0].current,false);assert.deepEqual(saved.history[0].body,sent);assert.equal(posts,1);
}));
test('concurrent replacement of the cached review request preserves the newer pending body',()=>environment(async({storage,review})=>{
 let release,sent;const gate=new Promise(resolve=>release=resolve);route(async(path,request)=>{sent=JSON.parse(request.body);await gate;return response(await postReceipt(sent));});const ui=review();await ui.flush();await ui.input('核查说明 '+scope.observation_id,'原输入说明');const posting=ui.click('保存人工核查');await new Promise(resolve=>setImmediate(resolve));const old=readManualReview(storage,scope),newer={...old.pending.body,request_id:'12345678-1234-4234-9234-123456789abd'};storage.setItem(reviewKey(scope),JSON.stringify({...old,reason:'另一页面新说明',pending:{body:newer}}));release();await posting;assert.deepEqual(readManualReview(storage,scope).pending.body,newer);assert.equal(readManualReview(storage,scope).reason,'另一页面新说明');
}));
test('403 review preserves original request and only successful identity/permission refresh unlocks it',()=>environment(async({storage,review})=>{
 putReview(storage);let allowed=false,posts=0;route(async(path,request)=>{if(!allowed)return response({detail:'权限已撤销'},403);return response(await probe());});const ui=review();await ui.flush();await ui.click('核对原核查请求');assert.equal(ui.button('按原编号重试核查').props.disabled,true);assert.deepEqual(readManualReview(storage,scope).pending.body,body);allowed=true;await ui.click('刷新登记和当前权限');assert.equal(ui.button('核对原核查请求').props.disabled,false);assert.equal(posts,0);
}));
test('unknown review needs two strictly newer-version probes and a separate explicit save with new UUID',()=>environment(async({storage,review})=>{
 putReview(storage);let probes=0,posts=0,sent;const props={record:row(),onRefresh:async()=>{props.record=row({version:7});}};
 route(async(path,request)=>{if(path.endsWith('/verify')){posts++;sent=JSON.parse(request.body);return response(await postReceipt(sent));}probes++;return response(await probe(body,{found:false,review:null,actor_matches:false,version:7,current:false}));});
 const ui=review(props);await ui.flush();await ui.click('核对原核查请求');assert.equal(posts,0);await ui.click('保留原请求，重新核对当前依据');
 let saved=readManualReview(storage,scope);assert.equal(probes,2);assert.equal(posts,0);assert.deepEqual(saved.history[0].body,body);assert.equal(saved.history[0].probes.length,2);assert.equal(saved.history[0].status,'version_fenced');
 await ui.click('保存人工核查');assert.equal(posts,1);assert.equal(sent.expected_version,7);assert.notEqual(sent.request_id,body.request_id);
}));
test('same-version unknown remains frozen; a viewer may read their exact historical receipt without sending verification',()=>environment(async({storage,review})=>{
 putReview(storage);let posts=0,found=false;route(async(path,request)=>{if(request.method==='POST')posts++;return response(await probe(body,found?{current:false}:{found:false,review:null,actor_matches:false,version:3}));},{role:'viewer'});
 const ui=review({record:row({can_verify:false})});await ui.flush();await ui.click('核对原核查请求');assert.throws(()=>ui.button('保留原请求，重新核对当前依据'));assert.ok(readManualReview(storage,scope).pending);assert.equal(ui.button('按原编号重试核查').props.disabled,true);
 found=true;await ui.click('核对原核查请求');assert.equal(readManualReview(storage,scope).pending,null);assert.equal(readManualReview(storage,scope).history[0].current,false);assert.equal(posts,0);
}));
test('late review response cannot acknowledge a different actor journal',()=>environment(async({storage,review})=>{
 let release,sent;const gate=new Promise(resolve=>release=resolve);route(async(path,request)=>{sent=JSON.parse(request.body);await gate;return response(await postReceipt(sent));});
 const props={},ui=review(props);await ui.flush();await ui.input('核查说明 '+scope.observation_id,'原账号核查说明');const posting=ui.click('保存人工核查');await new Promise(resolve=>setImmediate(resolve));props.account={...account,user_id:'actor-b',username:'另一人'};ui.render();await ui.flush();release();await posting;
 assert.deepEqual(readManualReview(storage,scope).pending.body,sent);assert.equal(readManualReview(storage,reviewScope(props.account,instanceId,scope.observation_id)).pending,null);
}));
test('integrated SignalStudio imports the actual manual form and review implementation',()=>environment(async({loadSignal})=>{assert.equal(typeof (await loadSignal()).default,'function');}));

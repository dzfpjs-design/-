// Public synthetic protocol/component seams. These tests do not claim real
// browser, adopted-source authority, database, device or model acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';
import {objectTypeSchemaV170,checkedObjectTypeV170,configuredObjects,schemaObjects,protectedObjectDefinition,detachObjectOrigin,renameObjectDefinition,verifyObjectTypeSnapshotV170} from '../src/setup-object-proposals.mjs';
import {generationBody,generationHash,generationScope,generationKey,stageGeneration,readGeneration,listGenerations,verifyGenerationReceipt,confirmGeneration,settleGeneration} from '../src/instance-generation.mjs';

const clone=value=>structuredClone(value),hash='a'.repeat(64),nonce='00000000-0000-4000-8000-000000000170';
const account={workspace_id:'public-workspace',user_id:'public-admin',username:'管理员',role:'admin'},scope=generationScope(account);
const object=()=>({schema:objectTypeSchemaV170,name:'监测对象',fields:[{label:'高度',kind:'数值',required:false,unit:'米'},{label:'数量',kind:'整数',required:false,unit:null}],business_identity:{status:'not_established',field_labels:[],citations:[],explanation:'人工尚未确认业务标识；原资料仍待核对。'},platform_identity_confirmation:{object_name:'监测对象',acknowledge_platform_identity:true}});
const source=()=>({schema:'report-object-schema-origin-v170',adoption_id:'public-adoption',plan_hash:hash,kind:'object',name:'监测对象',owner_path:'/objects/0',owner_value_hash:hash});
const workspace=()=>({businesses:[],instanceProfile:{name:'通用实例',domain:'观测管理',goal:'保留用户目标'},instanceObjects:[object()],instanceObjectOrigins:{监测对象:source()},instanceRoleBindings:{}});
const id=(prefix,label)=>prefix+'_'+createHash('sha256').update(label).digest('hex').slice(0,16);
function snapshot(objects=[object()]){
 const catalog=[],schemas={};for(const value of objects){const key=id('object',value.name),properties={},required=[],fields=value.fields.map(field=>({key:id('field',field.label),...clone(field)}));
  for(const field of fields){const kinds={'数值':{type:'number'},'整数':{type:'integer'},'文本':{type:'string',maxLength:2000},'是否':{type:'boolean'},'日期':{type:'string',format:'date'}},property={...kinds[field.kind],title:field.label};if(field.unit!==null)property.unit=field.unit;if(field.required){required.push(field.key);if(field.kind==='文本')Object.assign(property,{minLength:1,pattern:'\\S'});}properties[field.key]=property;}
  catalog.push({key,name:value.name,fields,contract_schema:objectTypeSchemaV170,business_identity:clone(value.business_identity),platform_identity_confirmation:clone(value.platform_identity_confirmation)});schemas[key]={type:'object',title:value.name,properties,required,additionalProperties:false};
 }
 return {businesses:[],object_types:catalog,entity_schemas:schemas,relation_definitions:[],sensor_sources:[],general_issues:[],data_plan:[]};
}
const body=(objects=[object()],patch={})=>generationBody({revision:7,objects,confirmed_configuration_hash:hash,request_id:nonce,...patch});
const memory=()=>{const rows=new Map();return {get length(){return rows.size;},key:index=>[...rows.keys()][index]??null,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key)};};
async function accepted(value){const release={id:'public-release',instance_id:value.instance_id||'public-instance',release_number:1,status:'draft',configuration_hash:hash,snapshot:snapshot(value.objects),available_business_ids:[]};return {saved:true,request_id:value.request_id,request_hash:await generationHash(value),actor_id:scope.actor_id,workspace_id:scope.workspace_id,generation_intent:value.generation_intent,instance_id:release.instance_id,release_id:release.id,release_number:1,configuration_hash:hash,release};}

test('configured five-key objects remain exact, detached and all optional without a fabricated name field',()=>{
 const original=workspace(),before=clone(original),rows=configuredObjects(original);assert.deepEqual(rows,[object()]);assert.deepEqual(schemaObjects(rows),rows);assert.deepEqual(original,before);assert.equal(rows[0].fields.some(field=>field.kind==='文本'),false);assert.equal(rows[0].fields.every(field=>field.required===false),true);rows[0].business_identity.explanation='changed';rows[0].platform_identity_confirmation.object_name='changed';assert.deepEqual(original,before);
 const unicode=object();unicode.fields[0].unit='\ufeff米';unicode.business_identity.explanation='  人工保留的完整说明  ';assert.deepEqual(checkedObjectTypeV170(unicode),unicode);
});

test('new fields and identities are strictly checked rather than trimmed, coerced or silently dropped',()=>{
 const changes=[value=>{value.schema='report-object-type-v171';},value=>{value.name=' 监测对象';},value=>{value.fields[0].label='高度 ';},value=>{value.fields[0].required=0;},value=>{value.fields[0].required='false';},value=>{delete value.fields[0].required;},value=>{value.fields[0].unit=' 米 ';},value=>{value.fields[0].unit='';},value=>{delete value.fields[0].unit;},value=>{value.fields[0].kind='文本';},value=>{value.fields[0].extra='discard';},value=>{value.business_identity.field_labels=['高度'];},value=>{value.business_identity.explanation=' ';},value=>{value.business_identity.extra=true;},value=>{value.platform_identity_confirmation.acknowledge_platform_identity='true';},value=>{value.platform_identity_confirmation.acknowledge_platform_identity=false;},value=>{value.platform_identity_confirmation.object_name='另一对象';},value=>{value.fields.push(clone(value.fields[0]));},value=>{value.fields=[];},value=>{value.extra='discard';}];
 for(const change of changes){const value=object();change(value);const before=clone(value);assert.throws(()=>checkedObjectTypeV170(value),/原配置保留/);assert.throws(()=>schemaObjects([value]),/原配置保留/);assert.throws(()=>body([value]),/原配置保留/);assert.deepEqual(value,before);}
});

test('referenced business identity remains optional and separate from confirmed platform record identity',()=>{
 const value=object();value.business_identity={status:'referenced',field_labels:['数量'],citations:[{span_ref:'w:p2/s3'}],explanation:'此字段原文有关联，唯一性尚未核查。'};assert.deepEqual(checkedObjectTypeV170(value),value);assert.deepEqual(body([value]).objects,[value]);assert.equal(body([value]).objects[0].fields[1].required,false);
 for(const patch of [{citations:[]},{field_labels:[]},{field_labels:['不存在']},{citations:[{span_ref:'invalid'}]},{citations:[{span_ref:'w:p2/s3',quote:'不得丢弃'}]}])assert.throws(()=>checkedObjectTypeV170({...value,business_identity:{...value.business_identity,...patch}}));
});

test('legacy request normalization stays independent while the new request keeps all five keys',()=>{
 const legacy={name:' 样地 ',fields:[{label:' 名称 ',kind:'文本',required:1,unit:' '}],citations:[{source_ref:'old'}]},value=body([legacy,object()]);assert.deepEqual(value.objects[0],{name:'样地',fields:[{label:'名称',kind:'文本',required:true,unit:null}]});assert.deepEqual(value.objects[1],object());assert.equal(value.objects[1].schema,objectTypeSchemaV170);assert.equal(legacy.name,' 样地 ');
});

test('issued and marker-only new objects cannot detach or rename into a legacy manual definition',()=>{
 for(const config of [workspace(),{...workspace(),instanceObjectOrigins:{}}]){const before=clone(config);assert.equal(protectedObjectDefinition(config.instanceObjects[0],config.instanceObjectOrigins.监测对象),true);assert.throws(()=>detachObjectOrigin(config,'监测对象'),/不能解除来源/);assert.throws(()=>renameObjectDefinition(config,0,'新名称'),/不能在生成页改写/);assert.deepEqual(config,before);}
 const legacy={instanceObjects:[{name:'手工对象',fields:[{label:'名称',kind:'文本',required:true,unit:null}]}],instanceObjectOrigins:{手工对象:{schema:'legacy-suggestion',object:{name:'手工对象'}}}};assert.deepEqual(detachObjectOrigin(legacy,'手工对象').instanceObjectOrigins,{});assert.equal(renameObjectDefinition(legacy,0,'新手工对象').instanceObjects[0].name,'新手工对象');
});

test('snapshot must preserve the complete type, exact units, all-optional schema and independent identities',()=>{
 const expected=object(),valid=snapshot();assert.equal(verifyObjectTypeSnapshotV170([expected],valid),valid);assert.deepEqual(valid.entity_schemas[valid.object_types[0].key].required,[]);
 const changes=[value=>{delete value.object_types[0].business_identity;},value=>{delete value.object_types[0].platform_identity_confirmation;},value=>{value.object_types[0].fields[0].unit=null;},value=>{value.object_types[0].fields[0].required=true;},value=>{value.object_types[0].business_identity.explanation='不同说明';},value=>{value.object_types[0].platform_identity_confirmation.object_name='别的对象';},value=>{value.object_types.push(clone(value.object_types[0]));},value=>{value.entity_schemas[value.object_types[0].key].required=[value.object_types[0].fields[0].key];},value=>{value.entity_schemas[value.object_types[0].key].properties[value.object_types[0].fields[0].key].type=['number','null'];},value=>{value.object_types[0].fields[0].extra=true;}];
 for(const change of changes){const value=snapshot();change(value);assert.throws(()=>verifyObjectTypeSnapshotV170([expected],value),/原配置保留/);}
});

test('original new-family journal survives reload, independent draft changes and exact release readback',async()=>{
 const storage=memory(),request=body(),journal=await stageGeneration(storage,scope,request),raw=journal.raw;request.objects[0].business_identity.explanation='草稿后来修改';assert.deepEqual(readGeneration(storage,scope,nonce).value.body.objects,[object()]);assert.equal(readGeneration(storage,scope,nonce).raw,raw);const verified=await verifyGenerationReceipt(journal,await accepted(journal.value.body)),confirmed=confirmGeneration(storage,journal,verified),saved=settleGeneration(storage,confirmed,clone(verified.release));assert.equal(saved.value.status,'saved');assert.equal(saved.value.exact_body,journal.value.exact_body);assert.deepEqual(saved.value.release.snapshot.object_types[0].business_identity,object().business_identity);assert.deepEqual(readGeneration(storage,scope,nonce),saved);
});

test('lost or changed identity in a success response keeps the original pending request intact',async()=>{
 const storage=memory(),journal=await stageGeneration(storage,scope,body()),raw=journal.raw;
 for(const change of [reply=>{delete reply.release.snapshot.object_types[0].business_identity;},reply=>{reply.release.snapshot.object_types[0].fields[0].unit='厘米';},reply=>{reply.release.snapshot.entity_schemas[reply.release.snapshot.object_types[0].key].required=['invented'];}]){const reply=await accepted(journal.value.body);change(reply);await assert.rejects(verifyGenerationReceipt(journal,reply),/原配置保留/);assert.equal(storage.getItem(generationKey(scope,nonce)),raw);assert.equal(readGeneration(storage,scope,nonce).value.status,'pending');}
});

test('corrupt strict field values in the journal are retained and block recovery',async()=>{
 const storage=memory(),journal=await stageGeneration(storage,scope,body()),value=clone(journal.value);value.body.objects[0].fields[0].required='false';value.exact_body=JSON.stringify(value.body);const raw=JSON.stringify(value);storage.setItem(generationKey(scope,nonce),raw);assert.throws(()=>readGeneration(storage,scope,nonce),/原生成请求或回执无法核对/);assert.equal(storage.getItem(generationKey(scope,nonce)),raw);
});

const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild');let loaded;
async function component(){if(!loaded){const built=await build({entryPoints:[fileURLToPath(new URL('../src/RuntimeReviewStudio.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',loader:{'.css':'empty'},logLevel:'silent',plugins:[{name:'react',setup(builder){builder.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});loaded=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));}return loaded;}
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):['string','number'].includes(typeof value)?String(value):'';
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
async function environment(run){const keys=['fetch','localStorage','sessionStorage'],old=Object.fromEntries(keys.map(key=>[key,globalThis[key]])),storage=memory(),mounted=[];Object.assign(globalThis,{localStorage:storage,sessionStorage:memory()});try{await run({storage,mount:(Component,props)=>{const ui=mount(Component,props);mounted.push(ui);return ui;}});}finally{mounted.forEach(ui=>ui.unmount());Object.assign(globalThis,old);}}
async function fixture(env,options={}){
 const state={posts:[],receipts:new Map(),commits:[],edits:[],saves:0,...options.state},props={account,workspace:workspace(),initialInstanceId:'',Btn:'button',Field:'label',onSave:async()=>{state.saves++;return 7;},onCommit(value){state.commits.push(clone(value));props.workspace=value;return true;},onEdit(value){state.edits.push(value);},onWorkspace(){},...options.props};
 globalThis.fetch=async(path,request={})=>{if(request.method==='POST'){const value=JSON.parse(request.body);state.posts.push({path,body:value,exact:request.body});if(path==='/api/instances/check'){const value={...snapshot(props.workspace.instanceObjects),configuration_hash:hash};if(options.preview)options.preview(value);return response(value);}assert.equal(path,'/api/instances');assert.ok(env.storage.getItem(generationKey(scope,value.request_id)),'exact new-family body saved before POST');if(options.post)return options.post(state,value);const reply=await accepted(value);state.receipts.set(value.request_id,reply);return response({...reply.release,generation_receipt:Object.fromEntries(Object.entries(reply).filter(([key])=>key!=='release'))});}
  if(path==='/api/me')return response(account);if(path==='/api/instances/members'||path==='/api/instances')return response([]);if(path.startsWith('/api/instances/generation-requests/')){const requestID=path.split('/').at(-1);return response(state.receipts.get(requestID)||{saved:false,request_id:requestID});}if(path.endsWith('/releases'))return response([...state.receipts.values()].map(value=>value.release));throw Error('unexpected public seam '+path);
 };
 const ui=env.mount((await component()).default,props);await ui.flush();return {ui,state,props};
}
async function generate(ui){await ui.click('检查当前配置');await ui.input('确认当前业务配置生成版本',true);await ui.click('保存生成版本');}

test('actual generation component checks and submits the exact adopted five-key object',()=>environment(async env=>{
 const {ui,state,props}=await fixture(env);await generate(ui);const checks=state.posts.filter(value=>value.path==='/api/instances/check'),posts=state.posts.filter(value=>value.path==='/api/instances');assert.equal(checks.length,1);assert.equal(posts.length,1);assert.deepEqual(checks[0].body.objects,[object()]);assert.deepEqual(posts[0].body.objects,[object()]);const journal=listGenerations(env.storage,scope)[0];assert.equal(journal.value.status,'saved');assert.equal(journal.value.exact_body,posts[0].exact);assert.deepEqual(props.workspace,workspace());assert.equal(state.commits.length,0);
}));

test('protected object controls cannot rename, change fields, detach or remove adopted values',()=>environment(async env=>{
 const {ui,state,props}=await fixture(env);assert.equal(ui.byAria('对象类型 1').props.disabled,true);assert.equal(ui.byAria('字段名称 1-1').props.disabled,true);assert.equal(ui.byAria('单位 1-1').props.disabled,true);assert.throws(()=>ui.button('按人工定义保存，解除建议关联'),/button:/);await ui.input('字段名称 1-1','错误字段');await ui.click('移除此对象类型');assert.equal(state.commits.length,0);assert.deepEqual(props.workspace,workspace());await ui.click('返回核对对象结构');assert.deepEqual(state.edits,[1]);
}));

test('server preview that drops identity cannot produce consent or a generation request',()=>environment(async env=>{
 const {ui,state}=await fixture(env,{preview(value){delete value.object_types[0].business_identity;}});await ui.click('检查当前配置');assert.match(text(ui.render()),/原配置保留/);assert.throws(()=>ui.byAria('确认当前业务配置生成版本'),/control:/);assert.equal(state.posts.filter(value=>value.path==='/api/instances').length,0);assert.equal(listGenerations(env.storage,scope).length,0);
}));

test('malformed saved configuration is explained without replacing it or sending a check',()=>environment(async env=>{
 const config=workspace();config.instanceObjects[0].fields[0].required='false';const before=clone(config),{ui,state,props}=await fixture(env,{props:{workspace:config}});assert.match(text(ui.render()),/原配置保留/);await ui.click('检查当前配置');assert.equal(state.posts.length,0);assert.equal(state.saves,0);assert.equal(state.commits.length,0);assert.deepEqual(props.workspace,before);
}));

test('unknown POST remount recovers only the exact original object contract despite changed current draft',()=>environment(async env=>{
 let lost=true;const {ui,state,props}=await fixture(env,{post:async(state,value)=>{if(lost)throw Error('public response lost before confirmation');const reply=await accepted(value);state.receipts.set(value.request_id,reply);return response({...reply.release,generation_receipt:Object.fromEntries(Object.entries(reply).filter(([key])=>key!=='release'))});}});await generate(ui);const journal=listGenerations(env.storage,scope)[0];assert.equal(journal.value.status,'pending');ui.unmount();props.workspace=workspace();props.workspace.instanceProfile.goal='刷新后修改目标';const next=env.mount((await component()).default,props);await next.flush();lost=false;await next.click('核对并恢复原生成请求');const posts=state.posts.filter(value=>value.path==='/api/instances');assert.equal(posts.length,2);assert.equal(posts[1].exact,journal.value.exact_body);assert.deepEqual(posts[1].body.objects,[object()]);assert.equal(listGenerations(env.storage,scope)[0].value.status,'saved');assert.equal(props.workspace.instanceProfile.goal,'刷新后修改目标');
}));

test('identity summary explains optional attributes and keeps technical record identity separate',async()=>{
 const {ObjectIdentitySummary}=await component(),value=text(ObjectIdentitySummary({object:object()}));assert.match(value,/目前尚未明确/);assert.match(value,/不代表业务编号或业务唯一性/);assert.match(value,/业务字段均为可选/);assert.match(value,/未填写不等于允许填入空值/);assert.doesNotMatch(value,/not_established|report-object-type|field_[a-f0-9]/);
});

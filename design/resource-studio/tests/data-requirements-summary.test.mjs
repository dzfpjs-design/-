import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';

// Inline synthetic display contracts only; these hashes do not prove derivation,
// source facts, backend persistence, capability availability or execution rights.
const SHA='a'.repeat(64),dependencies=['businesses','entity_schemas','object_types','resources','sensor_sources','materials','automation','equipment','general_issues'];
function output(patch={}){
 const schema={type:'object',properties:{amount:{title:'核对数量',type:'number',unit:'份'},unresolved:{$ref:'#/$defs/Unresolved'}},required:['amount'],$defs:{Unresolved:{type:'string'}}};
 return {step_id:'internal-step-identifier',step_name:'整理核对结果',agent_role:'data',capability_id:'internal-capability-identifier',capability_hash:SHA,release_version:'1.2.3',output_schema:schema,output_schema_hash:SHA,input_bindings:{records:{source:'object_records'}},fields:Object.entries(schema.properties).map(([name,schema])=>({name,schema})),storage:'existing_evidence_and_receipts',status:'candidate',...patch};
}
function requirements(patch={}){
 return {schema:'instance-data-requirements-v1',origin:'system_design',dependency_hash:SHA,dependency_hashes:Object.fromEntries(dependencies.map(key=>[key,SHA])),confirmed_object_catalog:[{name:'人工确认对象',fields:[{label:'原对象面积',unit:'公顷'}]}],businesses:[{id:'internal-business-identifier',name:'合成资料核对',status:'derived',issues:[],outputs:[output()],summary:'从冻结能力契约派生1项候选输出；1项人工步骤仍保存非结构化结果回执，不为其编造输出字段。'}],evidence_policy:{status:'candidate',source_timestamp:'unknown_until_actual_output',unit:'unknown_until_actual_output',retention_authorized:false,deletion_authorized:false},summary:'根据确认配置与固定能力版本自动形成数据保存设计。',requirements_hash:SHA,...patch};
}
const snapshot=value=>({data_requirements:value});
const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild'),components=new Map();
async function component(name='DataRequirementsSummary'){
 if(!components.has(name)){
  const built=await build({entryPoints:[fileURLToPath(new URL('../src/'+name+'.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',loader:{'.css':'empty'},logLevel:'silent',plugins:[{name:'react',setup(b){b.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});
  components.set(name,(await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'))).default);
 }
 return components.get(name);
}
async function html(value,props={}){return renderToStaticMarkup(React.createElement(await component(),{snapshot:value,...props}));}

test('legacy snapshot has no added section; an explicitly present null or unsupported contract is visible as an error',async()=>{
 assert.equal(await html({data_plan:[]}), '');
 for(const value of [null,{},requirements({schema:'future-version'}),requirements({origin:'document_fact'})]){
  const rendered=await html(snapshot(value));assert.match(rendered,/role="alert"/);assert.match(rendered,/数据保存需求格式无法核对/);assert.match(rendered,/原业务配置与生成记录保留/);assert.doesNotMatch(rendered,/已形成候选保存需求/);
 }
});

test('business and pinned step show readable inputs and exact field declarations without internal identities or authority claims',async()=>{
 const value=requirements(),before=structuredClone(value),rendered=await html(snapshot(value));
 for(const wording of ['合成资料核对','整理核对结果','数据管理智能体','能力版本 1.2.3','业务对象记录：本任务选择的对象记录','核对数量','必填（能力契约声明）','契约声明单位：份','unresolved','非必填（能力契约声明）','字段细节引用另一定义','系统根据已确认业务、接入资源和能力版本安排数据保存，无需单独配置数据库。此处展示保存方案；实际结果仍按来源核实，执行、外发和删除继续遵守原授权。','采集时间与实际单位待输出后核对','1项人工步骤仍保存非结构化结果回执'])assert.ok(rendered.includes(wording),wording);
 assert.match(rendered,/<details><summary>查看生成说明<\/summary><p>从冻结能力契约派生/);assert.doesNotMatch(rendered,/<details[^>]*\bopen\b|单位未明确/);assert.ok(!rendered.includes(value.summary),'the longer contract summary stays in the saved data rather than repeating on screen');
 assert.doesNotMatch(rendered,/internal-business-identifier|internal-step-identifier|internal-capability-identifier|aaaaaaaa|#\/\$defs|公顷/);assert.doesNotMatch(rendered,/<input|<select|<button|<pre/);assert.deepEqual(value,before);
});

test('all known input sources use Chinese labels and previous output resolves only within the same saved business',async()=>{
 const value=requirements(),b=value.businesses[0],prior=output({step_id:'saved-prior-id',step_name:'先前核查步骤'});
 const bindings=Object.fromEntries(['task_audio_ref','task_identifier','constant','object_records','object_attributes','original_evidence_ids','task_image_ids','task_scene_refs','perception_frame_ids','observation_records','task_context','object_ids','task_comparison_refs','request_key'].map((source,index)=>['arg'+index,{source}]));
 bindings.result={source:'previous_output',step_id:prior.step_id,field:'amount'};bindings.unknown={source:'unknown-private-source'};
 b.outputs=[prior,output({step_id:'next-step-id',input_bindings:bindings})];
 let rendered=await html(snapshot(value));
 for(const wording of ['原音频与固定设置','当前任务','固定参数','选择的对象记录','唯一对象的字段','引用的原件','明确选中的图片','影像与分析范围','视频线索的保存画面','已保存的观测记录','当前任务保存的上下文','本任务选择的业务对象','对比资料与固定设置','本次请求的固定编号','先前核查步骤 · 核对数量','尚不能识别的输入来源'])assert.ok(rendered.includes(wording),wording);
 assert.doesNotMatch(rendered,/unknown-private-source|saved-prior-id|next-step-id/);
 b.outputs[1].input_bindings.result={source:'previous_output',step_id:'another-business-step',field:'secret-field-id'};
 rendered=await html(snapshot(value));assert.match(rendered,/前序步骤待核对 · 输出字段待核对/);assert.doesNotMatch(rendered,/another-business-step|secret-field-id/);
});

test('unspecified requiredness, boolean schemas and reference-only output remain unknown rather than inferred from object fields',async()=>{
 const value=requirements(),schema={type:'object',properties:{plain:{type:'number'},whole:{type:'integer'},nullable:{type:['number','null']},label:{type:'string'},container:{type:'object'},list:{type:'array'},textUnit:{type:'string',unit:'条'},permitted:true,impossible:false}};
 value.businesses[0].outputs=[output({output_schema:schema,fields:Object.entries(schema.properties).map(([name,schema])=>({name,schema}))})];
 const rendered=await html(snapshot(value));assert.equal((rendered.match(/必填性未明确/g)||[]).length,9);assert.equal((rendered.match(/单位未明确/g)||[]).length,3);assert.match(rendered,/textUnit<\/strong>：必填性未明确；契约声明单位：条/);for(const name of ['label','container','list','permitted','impossible'])assert.ok(rendered.includes('<strong>'+name+'</strong>：必填性未明确</li>'),name+' has no irrelevant unit warning');assert.doesNotMatch(rendered,/公顷|非必填（/);
 value.businesses[0].outputs=[output({output_schema:{$ref:'#/$defs/Output',$defs:{Output:{type:'object'}}},fields:[]})];
 assert.match(await html(snapshot(value)),/没有直接列出字段.*不能据此认定没有输出/);
});

test('blocked and human-only businesses retain their explanations without inventing automatic output contracts',async()=>{
 const value=requirements({businesses:[{id:'blocked',name:'阻塞业务',status:'blocked',issues:['执行能力版本待补齐','负责账号待关联'],outputs:[],summary:'业务存在阻塞，原配置与问题保留。'},{id:'human',name:'人工复核业务',status:'derived',issues:[],outputs:[],summary:'0项候选输出；2项人工步骤仍保存非结构化结果回执，不为其编造输出字段。'}]});
 const rendered=await html(snapshot(value));assert.match(rendered,/阻塞业务 · 需先补齐执行条件/);assert.match(rendered,/执行能力版本待补齐/);assert.match(rendered,/负责账号待关联/);assert.match(rendered,/<details><summary>查看生成说明<\/summary><p>0项候选输出；2项人工步骤仍保存非结构化结果回执/);assert.doesNotMatch(rendered,/<details[^>]*\bopen\b|拟保存字段|能力版本 1\./);
});

test('malformed required keys and nested field or policy shapes cannot silently look like a valid empty design',async()=>{
 const mutations=[v=>delete v.dependency_hashes.resources,v=>delete v.summary,v=>v.extra='not-supported',v=>v.evidence_policy.deletion_authorized=true,v=>v.businesses[0].issues=null,v=>v.businesses[0].outputs[0].release_version=null,v=>v.businesses[0].outputs[0].fields[0].schema={type:'string'},v=>v.businesses[0].outputs[0].input_bindings.records=null,v=>v.businesses[0].outputs[0].output_schema.required=false,v=>v.businesses[0].outputs[0].output_schema_hash='not-a-hash'];
 for(const change of mutations){const value=requirements();change(value);const before=structuredClone(value),rendered=await html(snapshot(value));assert.match(rendered,/role="alert"/);assert.doesNotMatch(rendered,/已形成候选保存需求|拟保存字段/);assert.deepEqual(value,before);}
});

test('saved release is explicitly historical and human content is escaped and left unchanged',async()=>{
 const value=requirements();value.businesses[0].name='<img src=x onerror=alert(1)>';const before=structuredClone(value),rendered=await html(snapshot(value),{historical:true});
 assert.match(rendered,/此版本的数据保存需求/);assert.match(rendered,/固定于此版本生成时.*不代表当前能力状态/);assert.match(rendered,/&lt;img/);assert.doesNotMatch(rendered,/<img /);assert.deepEqual(value,before);
});

const memory=()=>{const values=new Map();return {get length(){return values.size;},key:index=>[...values.keys()][index]??null,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
test('real ReviewStudio hooks render current check and frozen release separately across remount, without generation or configuration writes',async()=>{
 const old={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage},mounted=[];
 const account={workspace_id:'synthetic-workspace',user_id:'synthetic-admin',username:'合成审核员',role:'admin'},workspace={businesses:[],instanceProfile:{name:'合成实例',domain:'公开合成',goal:'保留人工目标'},instanceObjects:[],instanceRoleBindings:{}},original=structuredClone(workspace),posts=[];
 const current=requirements(),saved=requirements();current.businesses[0].outputs[0].step_name='当前检查步骤';saved.businesses[0].outputs[0].step_name='历史固定步骤';saved.businesses[0].outputs[0].release_version='1.0.0';
 const view=req=>({businesses:[],relation_definitions:[],sensor_sources:[],data_plan:[{name:'原数据方案',details:'原方案说明继续显示'}],data_requirements:req});
 const checked={...view(current),configuration_hash:SHA,general_issues:[]},release={id:'synthetic-release',instance_id:'synthetic-instance',release_number:2,configuration_hash:SHA,created_at:'2026-10-18T10:00:00Z',status:'draft',snapshot:view(saved),available_business_ids:[]};
 const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
 globalThis.localStorage=memory();globalThis.sessionStorage=memory();globalThis.localStorage.setItem('human-draft',JSON.stringify(workspace));
 globalThis.fetch=async(path,request={})=>{
  if(request.method==='POST'){assert.equal(path,'/api/instances/check');posts.push(JSON.parse(request.body));return response(checked);}
  if(path==='/api/me')return response(account);if(path==='/api/instances/members')return response([]);if(path==='/api/instances')return response([{id:'synthetic-instance',name:'合成实例',domain:'公开合成',version:1,status:'draft',created_at:'2026-10-18T09:00:00Z'}]);if(path==='/api/instances/synthetic-instance/releases')return response([release]);throw Error('unexpected route '+path);
 };
 try{
  const Form=await component('RuntimeReviewStudio'),props={account,workspace,initialInstanceId:'synthetic-instance',Btn:'button',Field:'label',onSave:async()=>7,onCommit(){assert.fail('read-only summary must not change configuration');},onEdit(){},onWorkspace(){}};
  const ui=mount(Form,props);mounted.push(ui);await ui.flush();let rendered=renderToStaticMarkup(ui.render());assert.match(rendered,/历史固定步骤/);assert.match(rendered,/能力版本 1\.0\.0/);assert.doesNotMatch(rendered,/当前检查步骤/);assert.equal(posts.length,0);
  await ui.click('检查当前配置');rendered=renderToStaticMarkup(ui.render());assert.match(rendered,/当前检查步骤/);assert.match(rendered,/历史固定步骤/);assert.match(rendered,/原数据方案/);assert.match(rendered,/原方案说明继续显示/);assert.deepEqual(posts,[{revision:7,objects:[],role_bindings:{},instance_id:'synthetic-instance'}]);
  ui.unmount();const refreshed=mount(Form,props);mounted.push(refreshed);await refreshed.flush();rendered=renderToStaticMarkup(refreshed.render());assert.match(rendered,/历史固定步骤/);assert.doesNotMatch(rendered,/当前检查步骤/);assert.equal(posts.length,1);assert.equal(globalThis.localStorage.getItem('human-draft'),JSON.stringify(original));assert.deepEqual(workspace,original);
 }finally{mounted.forEach(ui=>ui.unmount());for(const [key,value] of Object.entries(old)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
});

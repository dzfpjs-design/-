// Real React/DOM and real File/SHA; explicit API seam does not test the parser,
// target server, receiver or browser. Root owns those separate acceptance layers.
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {File} from 'node:buffer';
import {createRequire} from 'node:module';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {canonical} from '../src/business-data-protocol-v204.mjs';
export const id=n=>'20400000-0000-4000-8000-'+String(n).padStart(12,'0');
export const account={user_id:id(1),workspace_id:id(2),role:'admin'};
export const ids={source:id(10),read:id(11),binding:id(12),import:id(13),row:id(14),instance:id(15),release:id(16),otherRead:id(17),day:id(30)};
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),requireProject=createRequire(join(project,'package.json'));
const transportName='__businessDataReactTransportV204',clone=x=>structuredClone(x);
let bundle,temp;
export async function cleanup(){if(temp)await rm(temp,{recursive:true,force:true});}
async function component(){if(!bundle)bundle=(async()=>{temp=await mkdtemp(join(tmpdir(),'business-data-react-v204-'));const viteRequire=createRequire(requireProject.resolve('vite/package.json')),{build}=await import(pathToFileURL(viteRequire.resolve('esbuild')).href),out=join(temp,'studio.mjs');await build({entryPoints:[join(project,'src/BusinessDataStudio.jsx')],outfile:out,write:true,bundle:true,format:'esm',platform:'node',jsx:'automatic',logLevel:'silent',plugins:[{name:'explicit-data-transport',setup(b){b.onResolve({filter:/^\.\/runtime-api$/},()=>({path:'runtime-api',namespace:'data-seam'}));b.onLoad({filter:/.*/,namespace:'data-seam'},()=>({loader:'js',contents:`export const json=(...a)=>globalThis.${transportName}(...a);export const api=()=>{throw Error('Download is not exercised by the synthetic API seam');};export const hashBytes=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(n=>n.toString(16).padStart(2,'0')).join('');`}));b.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(requireProject.resolve(args.path)).href,external:true}));}}]});return (await import(pathToFileURL(out).href)).default;})();return bundle;}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden');}}
const sha=async bytes=>Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256',bytes))).map(n=>n.toString(16).padStart(2,'0')).join('');
function readView(){return {id:ids.read,version:1,source_id:ids.source,name:'业务登记.xlsx',status:'ready',error:null,warnings:['Excel公式保留为表达式，未执行计算。'],raw_sha256:'d'.repeat(64),can_retry:false,record_sets:[{key:'sheet:台账',label:'台账',count:3,fields:[{path:'/编号',label:'编号',types:['string']},{path:'/名称',label:'名称',types:['string']}],samples:[{index:1,location:{sheet:'台账',row:2},cells:[{path:'/编号',label:'编号',value_text:'S-001',type:'string'},{path:'/名称',label:'名称',value_text:'作业站甲',type:'string'}]}]}]};}
export async function runStudio({instance=false,preloaded=false}={},run){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://synthetic-business-data.invalid/',resources:new NoResources(),virtualConsole:console});
 const session=dom.window.sessionStorage,storage=dom.window.localStorage;session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);session.setItem('ga-setup-csrf','synthetic-not-a-real-secret');
 const source={id:ids.source,version:1,name:'业务登记表',kind:'xlsx',status:'active',endpoint:null,secret_ref:null,credential_configured:false,last_read_id:preloaded?ids.read:null};
 const state={sources:preloaded?[source]:[],read:readView(),posts:[],calls:[],requests:new Map(),readCalls:0,failMe:0,loseUploadResponse:false,loseDayResponse:false,import:null,rows:[],uploads:[],schema:'e'.repeat(64),days:[],historyCalls:[],historyFailure:0,holdHistory:null};
 let tail=Promise.resolve();const locks={request:(_key,options,fn)=>{assert.equal(options.mode,'exclusive');const result=tail.then(fn);tail=result.catch(()=>{});return result;}};Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});
 const scope={actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instance?ids.instance:null},key='ga-business-data-v204:'+canonical(scope),pendingKey=key+':pending';
 const transport=async(path,options={})=>{const method=options.method||'GET';state.calls.push({path,method,body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],account.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-not-a-real-secret');
  if(path==='/me'){if(state.failMe&&storage.getItem(pendingKey)){state.failMe--;const e=Error('本次身份读取断网，操作尚未发出');e.status=503;throw e;}return clone(account);}
  assert.equal(session.getItem('ga-setup-csrf-user'),account.user_id);assert.equal(session.getItem('ga-setup-csrf-workspace'),account.workspace_id);
  if(method==='GET'){
   if(path==='/business-data/sources')return clone(state.sources);
   if(path==='/business-data/instances/'+ids.instance+'/options')return {instance_id:ids.instance,release_id:ids.release,object_types:[{key:'custom_station',label:'自定义作业站',schema_hash:state.schema,fields:[{key:'display_name',label:'站点名称',type:'string',required:true}]}]};
   if(path.startsWith('/business-data/requests/')){const nonce=path.split('/')[3].split('?')[0],saved=state.requests.get(nonce);if(saved)assert.ok(path.endsWith('expected_hash='+saved.hash));return {schema:'business-data-command-v204',found:!!saved,body:saved?clone(saved.body):null,result:saved?clone(saved.result):null};}
   if(path==='/business-data/reads/'+ids.read)return clone(state.read);
   if(path==='/business-data/reads/'+ids.otherRead)return {...clone(state.read),id:ids.otherRead,name:'后续原资料.xlsx'};
   if(path==='/business-data/reads?source_id='+ids.source)return state.sources[0]?.last_read_id?[clone(state.read),...(state.extraReads||[]).map(clone)]:[];
   if(path==='/business-data/sources/'+ids.source+'/days')return clone(state.days);
   const history=path.match(/^\/business-data\/(reads|imports)\/([a-f0-9-]+)\/history(?:\?cursor=(.*))?$/);
   if(history){state.historyCalls.push(path);if(state.historyFailure>0){state.historyFailure--;const error=Error('处理记录暂时无法读取，原资料未重读');error.status=503;throw error;}if(state.holdHistory?.id===history[2])await state.holdHistory.promise;
    const type=history[1]==='reads'?'read':'import',older=history[3]!==undefined;
    if(older)assert.equal(decodeURIComponent(history[3]),'synthetic+opaque/old==');
    return {schema:'business-data-history-v204',subject_type:type,subject_id:history[2],entries:type==='import'?[]:older?[{id:id(41),at:'2026-10-24T01:00:00+00:00',actor:'已停用的原操作人',label:'原文件已保存',detail:'原资料继续保留，仅查看当时处理记录。',status:'succeeded'}]:[{id:id(40),at:'2026-10-25T01:02:03+00:00',actor:'陈管理员',label:'读取未完成',detail:'原来源连接暂不可达，原资料记录保留。',status:'failed'}],next_cursor:type==='read'&&!older?'synthetic+opaque/old==':null,continuation:{count:type==='read'?2:null,notice:type==='import'?'当前续接记录尚不能完整核对。':null}};
   }
   if(path.startsWith('/business-data/imports?'))return state.import?[clone(state.import),...(state.extraImports||[]).map(clone)]:[];
   if(path==='/business-data/imports/'+ids.import)return clone(state.import);
   const extraImport=(state.extraImports||[]).find(batch=>path==='/business-data/imports/'+batch.id);if(extraImport)return clone(extraImport);
   if(path.startsWith('/business-data/imports/'+ids.import+'/rows?')||(state.extraImports||[]).some(batch=>path.startsWith('/business-data/imports/'+batch.id+'/rows?')))return clone(state.rows);
   throw Error('Unexpected synthetic GET '+path);
  }
  const pending=JSON.parse(storage.getItem(pendingKey));assert.ok(pending,'original is durable before mutating transport');assert.equal(pending.sha256,await sha(new TextEncoder().encode(pending.raw)));assert.equal(JSON.parse(storage.getItem(key+':original:'+pending.body.request_id)).raw,pending.raw);
  let body;if(options.body instanceof FormData){body={request_id:options.body.get('request_id'),expected_version:Number(options.body.get('expected_version')),file_name:options.body.get('file_name'),file_size:Number(options.body.get('file_size')),file_sha256:options.body.get('file_sha256')};const file=options.body.get('file');assert.equal(file.name,body.file_name);assert.equal(file.size,body.file_size);assert.equal(await sha(await file.arrayBuffer()),body.file_sha256);state.uploads.push({name:file.name,bytes:new Uint8Array(await file.arrayBuffer())});}
  else{assert.equal(options.body,pending.raw,'exact original JSON bytes sent');body=JSON.parse(options.body);}
  assert.equal(canonical(body),pending.raw);state.posts.push({path,body:clone(body),raw:pending.raw,hash:pending.sha256});let result;
  if(path==='/business-data/sources'){assert.equal(method,'POST');Object.assign(source,{name:body.name,kind:body.kind,endpoint:body.endpoint,secret_ref:body.secret_ref});state.sources=[source];result=clone(source);}
  else if(path==='/business-data/sources/'+ids.source+'/uploads'||path==='/business-data/sources/'+ids.source+'/read'){state.readCalls++;source.last_read_id=ids.read;state.read.raw_sha256=body.file_sha256||'d'.repeat(64);result=clone(state.read);}
  else if(path==='/business-data/bindings'){assert.equal(body.object_type,'custom_station');assert.equal(body.schema_hash,state.schema);assert.equal(body.record_set,state.read.record_sets[0].key);assert.equal(body.external_id_path,'/编号');assert.deepEqual(body.fields,[{target:'display_name',source:'/名称',conversion:'identity'}]);result={...clone(body),id:ids.binding};}
  else if(path==='/business-data/bindings/'+ids.binding+'/previews'){state.import={id:ids.import,version:1,source_id:ids.source,read_id:ids.read,binding_id:ids.binding,instance_id:ids.instance,release_id:ids.release,status:'previewed',error:null,confirm_hash:'f'.repeat(64),counts:{new:1,updated:0,unchanged:0,conflict:1,invalid:1,imported:0,data_received:0,failed:0,cancelled:0},can_confirm:true,can_resume:false};state.rows=[{id:ids.row,external_key:'S-001',name:'作业站甲',status:'new',differences:[{label:'站点名称',before_text:null,after_text:'作业站甲'}],reasons:[],location:{sheet:'台账',row:2},data_received:false},{id:id(18),external_key:'S-002',name:'已有人工维护对象',status:'conflict',differences:[],reasons:['此编号已由人工登记，未自动接管。'],location:{sheet:'台账',row:3},data_received:false},{id:id(19),external_key:null,name:'原资料第4行',status:'invalid',differences:[],reasons:['缺少稳定业务编号。'],location:{sheet:'台账',row:4},data_received:false}];result=clone(state.import);}
  else if(path==='/business-data/imports/'+ids.import+'/confirm'){assert.equal(body.accept_partial,true);assert.equal(body.confirm_hash,'f'.repeat(64));state.import={...state.import,status:'partial',version:2,can_confirm:false,counts:{...state.import.counts,imported:1}};state.rows[0]={...state.rows[0],status:'imported',data_delivery:{event_id:id(20),status:'blocked_delivery',version:1,can_retry:true,error:'原资料需重新核对接收'}};result=clone(state.import);}
  else if(path==='/business-data/imports/'+ids.import+'/rows/'+ids.row+'/retry-data-delivery'){assert.equal(body.expected_version,1);assert.match(body.reason,/恢复/);state.rows[0].data_delivery={...state.rows[0].data_delivery,status:'pending_delivery',version:2,can_retry:false,error:null};result=clone(state.rows[0]);}
  else if(path==='/business-data/sources/'+ids.source+'/days/'+ids.day+'/retry-data-delivery'){assert.equal(body.expected_version,state.days[0].version);assert.match(body.reason,/恢复/);state.days[0]={...state.days[0],version:state.days[0].version+1,status:'pending_delivery',data_delivery:{event_id:ids.day,version:state.days[0].version+1,status:'pending_delivery',can_retry:false,error:null}};result=clone(state.days[0]);}
  else throw Error('Unexpected synthetic mutation '+path);
  state.requests.set(body.request_id,{body:clone(body),result:clone(result),hash:pending.sha256});if(state.loseUploadResponse&&path.endsWith('/uploads')){state.loseUploadResponse=false;const e=Error('上传响应丢失，服务器原件已保存');e.status=503;throw e;}if(state.loseDayResponse&&path.includes('/days/')){state.loseDayResponse=false;const error=Error('每日接收恢复响应丢失，原请求已保存');error.status=503;throw error;}return clone(result);
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:storage,sessionStorage:session,HTMLElement:dom.window.HTMLElement,HTMLInputElement:dom.window.HTMLInputElement,HTMLSelectElement:dom.window.HTMLSelectElement,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,crypto:webcrypto,File,IS_REACT_ACT_ENVIRONMENT:true,fetch:()=>{throw Error('Real network forbidden');},[transportName]:transport};const prior=new Map(Object.keys(globals).map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const[k,value]of Object.entries(globals))Object.defineProperty(globalThis,k,{configurable:true,writable:true,value});
 let root;const container=dom.window.document.getElementById('root'),settle=async()=>{for(let n=0;n<12;n++)await act(async()=>new Promise(r=>setImmediate(r)));},until=async(predicate,label)=>{for(let n=0;n<150;n++){if(predicate())return;await act(async()=>new Promise(r=>setTimeout(r,5)));}assert.ok(predicate(),label+'; '+container.textContent.slice(0,1800));};
 const button=label=>{const nodes=[...container.querySelectorAll('button')],node=nodes.find(n=>n.textContent===label)||nodes.find(n=>n.textContent.startsWith(label));assert.ok(node,'actual button '+label);return node;};
 const click=async label=>{const node=button(label),details=node.closest('details');if(details&&!details.open)await act(async()=>details.querySelector('summary').click());assert.equal(node.disabled,false,label+' is enabled');await act(async()=>node.click());await settle();};
 const field=label=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,'actual field '+label);return node;};
 const edit=async(label,value)=>{const node=field(label),proto=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
 const chooseFile=async(file,label='选择业务数据原文件')=>{const node=field(label);Object.defineProperty(node,'files',{configurable:true,value:[file]});await act(async()=>node.dispatchEvent(new dom.window.Event('change',{bubbles:true})));await settle();};
 const clickWithin=async(label,buttonLabel)=>{const section=container.querySelector('section[aria-label="'+label+'"]');assert.ok(section,'actual section '+label);const node=[...section.querySelectorAll('button')].find(n=>n.textContent===buttonLabel);assert.ok(node,'actual section button '+buttonLabel);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
 const openDetails=async label=>{const summary=[...container.querySelectorAll('summary')].find(node=>node.textContent===label);assert.ok(summary,'actual details '+label);if(!summary.parentElement.open)await act(async()=>summary.click());await settle();};
 try{const Component=await component(),{createRoot}=await import('react-dom/client'),Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
  const render=async(extra={})=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,{account,instanceId:instance?ids.instance:'',Btn,...extra}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});await render();await until(()=>!container.textContent.includes('正在读取已保存的数据连接'),'initial normal GET finished');await settle();};
  const unmount=async()=>{if(root){const previous=root;root=null;await act(async()=>previous.unmount());await settle();}};
  await mount();await run({state,dom,storage,session,container,key,pendingKey,scope,File,field,button,click,clickWithin,openDetails,edit,chooseFile,settle,until,mount,unmount,render,text:()=>container.textContent});await unmount();assert.deepEqual(errors,[],'actual component has no React/DOM errors');
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[k,d]of prior){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
}

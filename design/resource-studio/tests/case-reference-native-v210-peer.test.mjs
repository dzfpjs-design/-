// Independent storage/fixed-version tests. Root executes these; the transport
// below is synthetic and proves no backend, native parsing or browser layout.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createRequire} from 'node:module';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {canonical} from '../src/runtime-document-v205-protocol.mjs';

class Memory {
 constructor(){this.map=new Map();this.noRemove=new Set();}
 get length(){return this.map.size;}
 key(index){return [...this.map.keys()][index]??null;}
 getItem(key){return this.map.get(key)??null;}
 setItem(key,value){this.map.set(key,String(value));}
 removeItem(key){if(!this.noRemove.has(key))this.map.delete(key);}
}
const id=n=>'21090000-0000-4000-8000-'+String(n).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2)},use=()=>({id:id(3),target:{task_id:id(4),instance_id:id(5)},
 document:{document_id:id(6),version_id:id(7)}});
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),requireProject=createRequire(join(project,'package.json'));
let folder,bundle,serial=0;
const oldSession=globalThis.sessionStorage,oldCrypto=globalThis.crypto;
globalThis.sessionStorage=new Memory();
Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
async function classes(){
 if(!bundle){
  folder=await mkdtemp(join(tmpdir(),'case-reference-native-peer-v210-'));
  const vite=createRequire(requireProject.resolve('vite/package.json'));
  const {build}=await import(pathToFileURL(vite.resolve('esbuild')).href);
  bundle=join(folder,'helper.mjs');
  await build({entryPoints:[join(project,'src/CaseReferenceNativeRecoveryV210.jsx')],outfile:bundle,
   bundle:true,format:'esm',platform:'node',logLevel:'silent',jsx:'automatic'});
 }
 return import(pathToFileURL(bundle).href+'?peer='+serial++);
}
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});globalThis.sessionStorage=oldSession;
 Object.defineProperty(globalThis,'crypto',{configurable:true,value:oldCrypto});});
const sha=async raw=>Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(raw))).toString('hex');
function draft(store,reason='独立填写的原处理理由'){
 return {schema:'runtime-document-draft-v205',scope:store.scope,draft:{document_id:store.pin.document_id,
  version_id:store.pin.version_id,search_id:'',title:'',keyword:'',reason,editor:'closed',
  upload_basis:null,text_basis:null,citation_basis:null,upload_forms:{}}};
}
async function pending(store,{kind='retry',reason='沿原文件恢复',request=id(8),context={}}={}){
 const body={request_id:request,expected_version:1,reason},raw=canonical(body);
 return {schema:'runtime-document-outbox-v205',scope:store.scope,kind,
  context:{document_id:store.pin.document_id,version_id:store.pin.version_id,...context},
  body,raw,sha256:await sha(raw),rejected:null};
}
function journal(store,p){const raw=JSON.stringify(p);store.setItem(store.nativeKey+':original:'+p.body.request_id,raw);
 store.setItem(store.nativeKey+':pending',raw);return raw;}

test('inline native storage rejects unknown cached source fields at every draft and receipt boundary',async()=>{
 const {CaseReferenceNativeStorageV210:Store}=await classes(),storage=new Memory(),store=new Store({storage,account,use:use()});
 const saved=JSON.stringify(draft(store));store.setItem(store.nativeKey+':draft',saved);
 for(const value of [{...draft(store),source_case:{value:{experience:'不得缓存的源案例全文'}}},
  {...draft(store),draft:{...draft(store).draft,source_cache:'不得缓存的参考正文'}},
  {...draft(store),draft:{...draft(store).draft,text_basis:{text:'不得缓存的原文'}}}]){
  assert.throws(()=>store.setItem(store.nativeKey+':draft',JSON.stringify(value)));
  assert.equal(store.getItem(store.nativeKey+':draft'),saved);
 }
 const p=await pending(store);journal(store,p);
 const receipt={original:p,result:{id:store.pin.version_id,instance_id:store.scope.instance_id,version:2}};
 assert.throws(()=>store.setItem(store.nativeKey+':received:'+p.body.request_id,JSON.stringify({...receipt,source_cache:'不可保存'})));
 store.setItem(store.nativeKey+':received:'+p.body.request_id,JSON.stringify(receipt));
 for(const [key,raw]of storage.map)if(key.startsWith(store.prefix))assert.equal(/不得缓存|不可保存/u.test(raw),false);
});

test('native pending remains bound to its immutable original journal and key nonce',async()=>{
 const {CaseReferenceNativeStorageV210:Store}=await classes(),storage=new Memory(),store=new Store({storage,account,use:use()});
 const p=await pending(store),raw=journal(store,p);
 const changed=await pending(store,{reason:'另一个窗口替换的理由'});
 assert.throws(()=>store.setItem(store.nativeKey+':pending',JSON.stringify(changed)));
 assert.equal(store.getItem(store.nativeKey+':pending'),raw);
 assert.throws(()=>store.setItem(store.nativeKey+':original:'+id(9),JSON.stringify(p)));
 const rejected={...p,rejected:{status:409,message:'原版本有更新，保留原请求'}};
 store.setItem(store.nativeKey+':pending',JSON.stringify(rejected));
 assert.equal(JSON.parse(store.getItem(store.nativeKey+':pending')).raw,p.raw);
 // Even an out-of-band local overwrite cannot pass the read/guard boundary.
 storage.setItem(store.keyFor(store.nativeKey+':pending'),JSON.stringify(changed));
 assert.throws(()=>store.getItem(store.nativeKey+':pending'));
 assert.equal(store.healthy(),false);
 assert.equal(storage.getItem(store.keyFor(store.nativeKey+':original:'+p.body.request_id)),raw);
});

test('pending cannot migrate to another document and silent removal failure retains the original',async()=>{
 const {CaseReferenceNativeStorageV210:Store}=await classes(),storage=new Memory(),store=new Store({storage,account,use:use()});
 const foreign=await pending(store,{context:{version_id:id(90)}});
 assert.throws(()=>store.setItem(store.nativeKey+':original:'+foreign.body.request_id,JSON.stringify(foreign)));
 const p=await pending(store),raw=journal(store,p),mapped=store.keyFor(store.nativeKey+':pending');
 storage.noRemove.add(mapped);
 assert.throws(()=>store.removeItem(store.nativeKey+':pending'));
 assert.equal(store.getItem(store.nativeKey+':pending'),raw);
 assert.equal(store.pendingExists(),true);
});

test('retained restore derives its content from the saved record and backs up current human work',async()=>{
 const {CaseReferenceNativeStorageV210:StoreA}=await classes(),{CaseReferenceNativeStorageV210:StoreB}=await classes();
 const storage=new Memory(),a=new StoreA({storage,account,use:use()}),b=new StoreB({storage,account,use:use()});
 a.setItem(a.nativeKey+':draft',JSON.stringify(draft(a,'窗口甲的原理由')));
 const current=JSON.stringify(draft(b,'窗口乙当前尚需保留的理由'));b.setItem(b.nativeKey+':draft',current);
 const old=b.retained(0).records.find(row=>row.reason==='窗口甲的原理由');assert.ok(old);
 assert.throws(()=>b.restore({...old,draftRaw:JSON.stringify(draft(b,'被拼接进来的其他理由'))},current));
 assert.throws(()=>b.restore(old,JSON.stringify({...draft(b),source_cache:'不得作为备份缓存'})));
 assert.equal(b.getItem(b.nativeKey+':draft'),current);
 assert.equal(b.restore(old,current).reason,'窗口甲的原理由');
 assert.ok([...storage.map.entries()].some(([key,raw])=>key.startsWith(b.prefix+'retained:')&&raw.includes('窗口乙当前尚需保留的理由')));
});

test('reference namespaces isolate task/use/version and leave main native human drafts untouched',async()=>{
 const {CaseReferenceNativeStorageV210:Store}=await classes(),storage=new Memory(),a=new Store({storage,account,use:use()});
 const main=a.nativeKey+':draft',mainRaw='主资料管理页面的原人稿';storage.setItem(main,mainRaw);
 a.setItem(a.nativeKey+':draft',JSON.stringify(draft(a,'案例参考甲理由')));
 const alternate=use();alternate.target.task_id=id(41);alternate.document.version_id=id(71);
 const b=new Store({storage,account,use:alternate});assert.notEqual(a.prefix,b.prefix);
 assert.equal(b.getItem(b.nativeKey+':draft'),null);
 assert.equal(storage.getItem(main),mainRaw);
 assert.throws(()=>b.setItem(b.nativeKey+':draft',JSON.stringify(draft(a))));
});

test('fixed reference controller never selects a newer head or reads instance search/text',async()=>{
 const {CaseReferenceNativeStorageV210:Store,CaseReferenceNativeControllerV210:Controller}=await classes();
 const storage=new Memory(),selected=use(),store=new Store({storage,account,use:selected}),session=new Memory();
 for(const [key,value]of Object.entries({'ga-setup-csrf-user':account.user_id,
  'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf':'synthetic-inline-peer'}))session.setItem(key,value);
 const original={id:selected.document.version_id,version:3,document_id:selected.document.document_id,
  instance_id:selected.target.instance_id,sequence:1,name:'固定案例参考.txt',status:'failed',raw_saved:true,
  raw_sha256:'a'.repeat(64),parse_hash:null,parse_engine:null,blocks_count:0,warnings:[],error:'原解析中断',
  can_retry:true,can_cancel:false,latest_attempt_id:null,delivery:null};
 const newer={...original,id:id(70),sequence:2,name:'后来新版本.txt',status:'parsed',can_retry:false};
 const document={id:selected.document.document_id,version:2,instance_id:selected.target.instance_id,
  title:'固定参考的文档记录',latest_version_id:newer.id,latest:newer};
 const calls=[],request=async path=>{calls.push(path);
  if(path==='/me')return {...account};
  if(path==='/runtime-documents/catalog')return {schema:'runtime-documents-catalog-v205',allowed_extensions:['.txt']};
  if(path==='/runtime-documents/instances/'+selected.target.instance_id+'/context')return {
   instance_id:selected.target.instance_id,release_id:id(50),can_upload:true};
  if(path==='/runtime-documents/documents/'+document.id)return structuredClone(document);
  if(path==='/runtime-documents/documents/'+document.id+'/versions?offset=0')return {
   records:[structuredClone(newer),structuredClone(original)],offset:0,has_more:false};
  if(path==='/runtime-documents/versions/'+original.id)return structuredClone(original);
  throw Error('Unexpected nonfixed read: '+path);
 };
 const c=new Controller({account,use:selected,instanceId:selected.target.instance_id,request,storage:store,session,
  hash:async bytes=>Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex'),
  locks:{request:(_key,_options,action)=>action()}});
 try{
  await c.start();await c.selectDocument(document.id);await c.selectVersion(original.id);await c.refresh();
  assert.equal(c.error,'');assert.equal(c.version.id,original.id);assert.equal(c.draft.version_id,original.id);
  assert.equal(calls.some(path=>path.includes('/searches')||path.includes('/blocks')||path.endsWith('/versions/'+newer.id)),false);
  assert.throws(()=>c.selectVersion(newer.id));
 }finally{c.dispose();}
});

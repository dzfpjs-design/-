import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {preserveObjectInputDraft,enumerateObjectInputDrafts,readObjectInputDraftCandidate,objectInputFormJSON,prepareObjectRequest,readObjectRequest} from '../src/object-request-v132.mjs';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const scope={actor_id:'actor',workspace_id:'workspace',instance_id:'instance'},base='ga-workflow-forms:workspace:person:instance';
class Store{constructor(){this.map=new Map();this.reads=[];this.fail=false;}get length(){return this.map.size;}key(i){return [...this.map.keys()][i]??null;}getItem(k){this.reads.push(k);return this.map.get(k)??null;}setItem(k,v){if(this.fail)throw Error('quota');this.map.set(k,String(v));}removeItem(k){this.map.delete(k);}}
const form=(raw='-0.0',patch={})=>({type:'site',attributes:{name:'公开合成人稿',reading:raw,confirmed:false},reference:'原业务编号',showObject:true,objectAction:null,...patch});
const journal=(value,owner=scope)=>JSON.stringify({schema:'workflow-task-form-journal-v1',at:'2026-10-20T01:02:03Z',scope:{...owner,release_id:'release'},form:value});

test('separate window object fields remain discoverable after another shared head wins',()=>{
 const storage=new Store(),a=form('-0.0'),b=form('1e21');storage.setItem(base+':task-form-journal:window-a',journal(a));storage.setItem(base+':task-form-journal:window-b',journal(b));storage.setItem(base,JSON.stringify(b));
 const page=enumerateObjectInputDrafts(storage,base,scope);assert.equal(page.items.length,2);
 const candidate=page.items.find(item=>item.window_id==='window-a'),before=[...storage.map];
 assert.deepEqual(readObjectInputDraftCandidate(storage,base,scope,candidate).form,a);assert.deepEqual([...storage.map],before);assert.equal(candidate.form.attributes.reading,'-0.0');
 assert.equal(readObjectRequest(storage,base,scope),null);
});
test('an explicit restore can archive current fields with exact numeric text and without overwriting any window',()=>{
 const storage=new Store(),current=form('1.0'),old=form('1e21');const original=journal(old);storage.setItem(base+':task-form-journal:other',original);
 const saved=preserveObjectInputDraft(storage,base,scope,'window-current','release',current),candidate=enumerateObjectInputDrafts(storage,base,scope).items.find(row=>row.key===saved.key);
 assert.deepEqual(readObjectInputDraftCandidate(storage,base,scope,candidate).form,current);assert.equal(storage.getItem(base+':task-form-journal:other'),original);
 assert.equal(storage.getItem(saved.key),saved.raw);assert.equal(storage.getItem(base),null);
 assert.ok(objectInputFormJSON(current,scope).includes('"reading":"1.0"'));
});
for(const suffix of [':object-form-history:old',':object-form-history:old:original',':object-form-completed:old',':object-original-form:old'])test('legacy object backup is visible without rewriting original bytes '+suffix,()=>{
 const storage=new Store(),raw=JSON.stringify({...form('1e21'),title:'原任务人稿，不应被对象恢复改写'});storage.setItem(base+suffix,raw);
 const candidate=enumerateObjectInputDrafts(storage,base,scope).items[0];assert.equal(candidate.kind,'legacy');assert.equal(candidate.release_id,'');
 assert.deepEqual(readObjectInputDraftCandidate(storage,base,scope,candidate).form,form('1e21'));assert.equal(storage.getItem(base+suffix),raw);
});
test('candidate changes, foreign keys and actor scope are rejected before restoration',()=>{
 const storage=new Store();storage.setItem(base+':task-form-journal:old',journal(form()));const candidate=enumerateObjectInputDrafts(storage,base,scope).items[0];
 storage.setItem(candidate.key,journal(form('1.0')));assert.throws(()=>readObjectInputDraftCandidate(storage,base,scope,candidate),/变化/);
 storage.reads=[];assert.throws(()=>readObjectInputDraftCandidate(storage,base,scope,{...candidate,key:'foreign-private-key'}));assert.deepEqual(storage.reads,[]);
 assert.throws(()=>readObjectInputDraftCandidate(storage,base,{...scope,actor_id:'other'},candidate));assert.deepEqual(storage.reads,[]);
});
test('discovery reads no other account keys and returns a cursor for bounded empty pages',()=>{
 const storage=new Store();for(let n=0;n<5;n++)storage.setItem('other-person:'+n,'private-unparsed');storage.setItem(base+':task-form-journal:mine',journal(form()));
 const first=enumerateObjectInputDrafts(storage,base,scope,{maxScan:3});assert.deepEqual(first.items,[]);assert.equal(first.next_offset,3);assert.deepEqual(storage.reads,[]);
 const second=enumerateObjectInputDrafts(storage,base,scope,{offset:first.next_offset,maxScan:3});assert.equal(second.items.length,1);assert.equal(second.next_offset,null);assert.deepEqual(storage.reads,[base+':task-form-journal:mine']);
});
test('broken and oversized records remain in place and do not hide a later valid draft',()=>{
 const storage=new Store();storage.setItem(base+':object-form-history:broken','{broken');storage.setItem(base+':object-form-history:large',' '.repeat(4*1024*1024+1));storage.setItem(base+':task-form-journal:ok',journal(form()));
 const first=enumerateObjectInputDrafts(storage,base,scope,{maxScan:2});assert.equal(first.problem_count,2);assert.equal(first.next_offset,2);
 assert.equal(enumerateObjectInputDrafts(storage,base,scope,{offset:first.next_offset}).items.length,1);assert.equal(storage.getItem(base+':object-form-history:broken'),'{broken');
});
test('storage failures do not return a saved draft or change input memory',()=>{
 const storage=new Store(),input=form(' 1.0 '),before=JSON.stringify(input);storage.fail=true;
 assert.throws(()=>preserveObjectInputDraft(storage,base,scope,'window','release',input),/quota/);assert.equal(storage.length,0);assert.equal(JSON.stringify(input),before);
 storage.fail=false;storage.setItem(base+':task-form-journal:owned',journal(input));storage.getItem=()=>{throw Error('storage unavailable');};assert.throws(()=>enumerateObjectInputDrafts(storage,base,scope),/storage unavailable/);
});
test('original typed or legacy object requests remain attached to a restored draft without a new nonce',()=>{
 const storage=new Store(),body={release_id:'old-release',object_type:'site',attributes:{name:'合成原对象'},reference:'original',request_id:'original-request-132'},typed=prepareObjectRequest(body,scope);
 for(const [index,action]of [typed,body].entries())storage.setItem(base+':object-form-completed:'+index,JSON.stringify(form('1.0',{objectAction:action})));
 for(const candidate of enumerateObjectInputDrafts(storage,base,scope).items){const restored=readObjectInputDraftCandidate(storage,base,scope,candidate);assert.equal(restored.release_id,'old-release');assert.equal((restored.form.objectAction.body||restored.form.objectAction).request_id,body.request_id);}
 assert.equal(storage.getItem(base+':object-create-v132'),null);assert.equal(storage.length,2);
});

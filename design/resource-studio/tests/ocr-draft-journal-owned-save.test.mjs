import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {OCRDraftJournal} from '../src/ocr-draft-journal.mjs';
import {newOCRCache,readOCRCache,withoutPendingOCR} from '../src/material-ocr-contract.mjs';

if(!globalThis.crypto)globalThis.crypto=webcrypto;
const copy=value=>structuredClone(value);
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const account={user_id:id(1),workspace_id:id(2)},materialID=id(3),unitID=id(4),otherID=id(5),runID=id(6);
const key='synthetic-ocr-owner-save';
function storage(){const values=new Map();return {get length(){return values.size;},key:n=>[...values.keys()][n]??null,getItem:name=>values.get(name)??null,setItem:(name,value)=>values.set(name,String(value))};}
function draft(text){return {run_id:runID,parent_hash:'a'.repeat(64),unit_version:4,regions:[{id:'r0012',text,box:[10,20,100,30],source_region_id:'r0012'}]};}
function setup(initial=newOCRCache()){
 const local=storage();local.setItem(key,JSON.stringify(initial));
 return {local,journal:()=>new OCRDraftJournal({storage:local,key,account,materialID}),shared:()=>readOCRCache(local,key)};
}

test('one editor can revise its own text without inventing another-page conflict',()=>{
 const {journal,shared}=setup(),editor=journal(),initial=newOCRCache();
 const first=editor.save(initial,{...initial,selected_unit:unitID,drafts:{[unitID]:draft('核对末字甲')}});
 assert.deepEqual(editor.conflicts,[]);
 const next={...first,drafts:{...first.drafts,[unitID]:draft('核对末字乙')}};
 const saved=editor.save(first,next);
 assert.deepEqual(editor.conflicts,[]);
 assert.deepEqual(saved.drafts,next.drafts);
 assert.deepEqual(shared().drafts,next.drafts);
 assert.deepEqual(editor.read(editor.location).cache.drafts,next.drafts);
});

test('accepted save removes the submitted draft and pending without ghost conflict or losing another page',()=>{
 const initial={...newOCRCache(),drafts:{[otherID]:draft('另一页未发送末字')}};
 const {journal,shared}=setup(initial),editor=journal();
 const edited=editor.save(initial,{...initial,selected_unit:unitID,drafts:{...initial.drafts,[unitID]:draft('已保存的人工末字')}});
 const pending={action:'save',unit_id:unitID,body:{request_id:id(7),expected_material_version:5,run_id:runID,expected_unit_version:4,parent_hash:'a'.repeat(64),reason:'对照原图纠错',regions:copy(edited.drafts[unitID].regions)}};
 const waiting=editor.save(edited,{...edited,pending});
 const acknowledged=withoutPendingOCR(waiting,pending),drafts={...acknowledged.drafts};delete drafts[unitID];
 const saved=editor.save(waiting,{...acknowledged,drafts});
 assert.deepEqual(editor.conflicts,[]);
 assert.equal(saved.pending,null);assert.equal(saved.drafts[unitID],undefined);
 assert.equal(shared().pending,null);assert.equal(shared().drafts[unitID],undefined);
 assert.deepEqual(shared().drafts[otherID],initial.drafts[otherID]);
 assert.equal(editor.read(editor.location).cache.drafts[unitID],undefined);
 assert.equal(editor.touched.has(unitID),true,'the owner tombstone remains tracked for real concurrent writes');
 const later=editor.save(saved,{...saved,reason:'说明已保存版本'});
 assert.deepEqual(editor.conflicts,[]);assert.equal(later.drafts[unitID],undefined);
});

test('real same-page conflict preserves both owners and the local text during reason and selection changes',()=>{
 const {journal,shared}=setup(),editorA=journal(),editorB=journal(),initial=newOCRCache();
 const a=editorA.save(initial,{...initial,selected_unit:unitID,drafts:{[unitID]:draft('另一页面甲稿')}});
 const b=editorB.save(initial,{...initial,selected_unit:unitID,drafts:{[unitID]:draft('本页乙稿末字')}});
 assert.deepEqual(editorA.conflicts,[]);assert.deepEqual(editorB.conflicts,[unitID]);
 assert.deepEqual(shared().drafts,a.drafts);assert.equal(b.drafts[unitID].regions[0].text,'本页乙稿末字');
 const explained=editorB.save(b,{...b,reason:'乙稿仍需对照',selected_unit:otherID});
 assert.deepEqual(editorB.conflicts,[unitID]);
 assert.equal(explained.drafts[unitID].regions[0].text,'本页乙稿末字');
 assert.equal(shared().drafts[unitID].regions[0].text,'另一页面甲稿');
 assert.deepEqual(editorA.read(editorA.location).cache.drafts,a.drafts);
 assert.deepEqual(editorB.read(editorB.location).cache.drafts,b.drafts);
 assert.equal(editorA.list().some(item=>item.row.owner===editorB.owner),true);
 assert.equal(editorB.list().some(item=>item.row.owner===editorA.owner),true);
});

test('stale removal cannot silently delete a genuinely newer other-owner draft',()=>{
 const initial={...newOCRCache(),drafts:{[unitID]:draft('最初的待核稿')}};
 const {journal,shared}=setup(initial),editorA=journal(),editorB=journal();editorB.preserve(initial);
 const a=editorA.save(initial,{...initial,drafts:{[unitID]:draft('另一页面后来稿')}});
 const removed=editorB.save(initial,{...initial,drafts:{}});
 assert.deepEqual(editorB.conflicts,[unitID]);assert.equal(removed.drafts[unitID],undefined);
 assert.deepEqual(shared().drafts,a.drafts);
 assert.equal(editorB.list().some(item=>item.row.cache.drafts[unitID]?.regions[0].text==='最初的待核稿'),true);
 assert.deepEqual(editorA.read(editorA.location).cache.drafts,a.drafts);
});

test('different-page concurrent edits merge and remain readable after a metadata-only save',()=>{
 const {journal,shared}=setup(),editorA=journal(),editorB=journal(),initial=newOCRCache();
 const a=editorA.save(initial,{...initial,drafts:{[unitID]:draft('甲页末字')}});
 editorB.save(initial,{...initial,drafts:{[otherID]:draft('乙页末字')}});
 assert.deepEqual(editorA.conflicts,[]);assert.deepEqual(editorB.conflicts,[]);
 const latest=editorA.save(a,{...a,reason:'甲页说明'});
 assert.deepEqual(editorA.conflicts,[]);
 assert.equal(latest.drafts[unitID].regions[0].text,'甲页末字');
 assert.equal(latest.drafts[otherID].regions[0].text,'乙页末字');
 assert.deepEqual(shared().drafts,latest.drafts);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {NativeVideoReviewJournalV165 as Journal} from '../src/native-video-review-contract-v165.mjs';

const id=n=>'16616600-0000-4000-8000-'+String(n).padStart(12,'0');
function store(){const values=new Map();return {fail:false,getItem:key=>values.get(key)??null,setItem(key,value){if(this.fail)throw Error('quota');values.set(key,String(value));},removeItem:key=>values.delete(key)};}
function scene(n){const storage=store(),session=store(),scope={actor_id:id(n),workspace_id:id(2),username:'核查人',instance_id:id(3),observation_id:id(4),role:'operator',current_release_id:id(5),release_id:id(5),receipt_hash:'a'.repeat(64)};return {storage,session,scope,newJournal:()=>new Journal(scope,{storage,session,locks:{request:async(name,options,fn)=>fn()}})};}
const form=note=>({note,frame_ids:[],decision:null,confirm_candidate_only:false});
test('inactive quota rescue across three owners cannot resurrect an older saved note',()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 const x=scene(1);let a,b,c,d;
 try{
  x.storage.fail=true;a=x.newJournal();a.open();assert.throws(()=>a.save(form('第一代旧稿')),/quota/);a.dispose();
  b=x.newJournal();assert.equal(b.open().note,'第一代旧稿');assert.throws(()=>b.save(form('第二代应保留的最新稿')),/quota/);b.dispose();
  c=x.newJournal();assert.equal(c.open().note,'第二代应保留的最新稿');x.storage.fail=false;c.save(form('第二代应保留的最新稿'));assert.equal(c.unsaved,false);c.dispose();
  d=x.newJournal();assert.equal(d.open().note,'第二代应保留的最新稿');assert.equal(d.unsaved,false);
 }finally{for(const j of [a,b,c,d])j?.dispose();if(previous)Object.defineProperty(globalThis,'crypto',previous);else delete globalThis.crypto;}
});
test('an active owner private memory is not consumed or cleared by another mount',()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 const x=scene(9);let a,b,c;
 try{
  a=x.newJournal();a.open();x.storage.fail=true;assert.throws(()=>a.save(form('仍在工作的独立窗口原稿')),/quota/);
  b=x.newJournal();assert.equal(b.open().note,'');x.storage.fail=false;b.save(form('另一挂载已保存'));b.dispose();
  a.dispose();c=x.newJournal();assert.equal(c.open().note,'仍在工作的独立窗口原稿');assert.equal(c.unsaved,true);c.save(c.form);
 }finally{for(const j of [a,b,c])j?.dispose();if(previous)Object.defineProperty(globalThis,'crypto',previous);else delete globalThis.crypto;}
});
test('saving a chosen inactive branch preserves a different independent inactive draft',()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 const x=scene(19);let a,b,c,d;
 try{
  x.storage.fail=true;a=x.newJournal();a.open();assert.throws(()=>a.save(form('独立分支甲')),/quota/);
  b=x.newJournal();assert.equal(b.open().note,'');assert.throws(()=>b.save(form('独立分支乙')),/quota/);a.dispose();b.dispose();
  c=x.newJournal();assert.equal(c.open().note,'独立分支乙');x.storage.fail=false;c.save(c.form);c.dispose();
  d=x.newJournal();assert.equal(d.open().note,'独立分支甲');assert.equal(d.unsaved,true);d.save(d.form);
 }finally{for(const j of [a,b,c,d])j?.dispose();if(previous)Object.defineProperty(globalThis,'crypto',previous);else delete globalThis.crypto;}
});

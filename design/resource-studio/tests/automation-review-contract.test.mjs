import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewJournalKey,legacyReviewJournalKey,readScopedReviewJournal,readReviewJournal,saveReviewRequest,clearReviewRequest,rejectReviewRequest,reviewBusiness,checkReviewReceipt,definiteReviewConflict,newerReviewView} from '../src/automation-review-contract.mjs';
const UUID='11111111-2222-4333-8444-555555555555';
const account={workspace_id:'w',username:'human',user_id:'user-human'};
const pending={body:{revision:7,business_id:'business_0123456789abcdef',request_id:UUID},business_name:'库位资料核查',created_at:'2026-10-11T10:00:00Z'};
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values};};
const view=(revision=8,status='confirmed')=>({revision,configuration:{flows:{}},automation_review:{version:1,businesses:[{business_id:'business_0123456789abcdef',business_name:'库位资料核查',status,reason:'实际服务器说明',flow_hash:'a'.repeat(64),policy_hash:'b'.repeat(64)}]}});
test('review journal is principal scoped and preserves exact original CAS and UUID',()=>{
 const store=storage(),key=reviewJournalKey(account);saveReviewRequest(store,key,pending);assert.deepEqual(readReviewJournal(store,key),pending);
 assert.notEqual(key,reviewJournalKey({...account,username:'other'}));assert.notEqual(key,reviewJournalKey({...account,workspace_id:'other'}));
 assert.equal(readReviewJournal(store,reviewJournalKey({...account,username:'other'})),null);
 assert.throws(()=>saveReviewRequest(store,key,{...pending,body:{...pending.body,request_id:'aaaaaaaa-2222-4333-8444-555555555555'}}),/原确认请求/);
 assert.equal(clearReviewRequest(store,key,{...pending,body:{...pending.body,revision:9}}),false);assert.deepEqual(readReviewJournal(store,key),pending);
 assert.equal(clearReviewRequest(store,key,pending),true);assert.equal(readReviewJournal(store,key),null);
});
test('cache corruption and quota errors cannot become an authorized POST',()=>{
 const store=storage(),key=reviewJournalKey(account);store.setItem(key,'{bad');assert.throws(()=>readReviewJournal(store,key),/无法核对/);
 const full={getItem:()=>null,setItem(){throw Error('quota');}};assert.throws(()=>saveReviewRequest(full,key,pending),/quota/);
 assert.throws(()=>saveReviewRequest(storage(),key,{...pending,body:{...pending.body,extra:'unsafe'}}),/请求/);
});
test('only precise no-submit CAS rejection unlocks, preserving rejected original body',()=>{
 const error={status:409,message:'配置版本已更新，本次自动化确认未提交。请读取最新配置后重新核对。'};
 assert.equal(definiteReviewConflict(error),true);for(const e of [{...error,status:500},{status:409,message:'请求内容不一致'},{message:'网络断开'}])assert.equal(definiteReviewConflict(e),false);
 const store=storage(),key=reviewJournalKey(account);saveReviewRequest(store,key,pending);assert.equal(rejectReviewRequest(store,key,pending,error.message),true);
 assert.equal(readReviewJournal(store,key),null);const rejected=JSON.parse(store.getItem(key+':rejected'));assert.deepEqual(rejected.original,pending);assert.equal(rejected.reason,error.message);
});
test('server receipt identifies saved operation but current status stays authoritative',()=>{
 const latest=view(10,'needs_review');latest.review_receipt={request_id:UUID,business_id:'business_0123456789abcdef',saved_revision:8,replayed:true};
 assert.equal(checkReviewReceipt(latest,pending).revision,10);assert.equal(reviewBusiness(latest,'库位资料核查').status,'needs_review');
 assert.throws(()=>checkReviewReceipt({...latest,review_receipt:{...latest.review_receipt,request_id:'other'}},pending),/回执/);
 assert.throws(()=>checkReviewReceipt({...latest,review_receipt:{...latest.review_receipt,business_id:'other'}},pending),/回执/);
 assert.throws(()=>reviewBusiness({...latest,automation_review:undefined},'库位资料核查'),/服务器/);
});
test('late draft reads and malformed metadata never downgrade server state',()=>{
 const current=view(10),incoming=view(8,'needs_review');assert.equal(newerReviewView(current,incoming),current);assert.equal(newerReviewView(current,view(11,'needs_review')).revision,11);
 assert.throws(()=>newerReviewView(current,{...view(12),automation_review:{version:1,businesses:[{...view().automation_review.businesses[0],status:'pretend'}]}}),/服务器/);
 assert.throws(()=>reviewBusiness(view(),'wrong'),/业务/);
});

test('scoped journal locks serialize creation and never erase another unresolved request',async()=>{
 const {withReviewJournalLock}=await import('../src/automation-review-contract.mjs');const store=storage(),key=reviewJournalKey(account);
 let tail=Promise.resolve();const manager={request(_key,_options,operation){const result=tail.then(operation);tail=result.catch(()=>{});return result;}};
 const other={...pending,body:{...pending.body,request_id:'aaaaaaaa-2222-4333-8444-555555555555'}};
 const outcomes=await Promise.allSettled([pending,other].map(item=>withReviewJournalLock(key,async()=>{await Promise.resolve();return saveReviewRequest(store,key,item);},manager)));
 assert.equal(outcomes.filter(item=>item.status==='fulfilled').length,1);assert.deepEqual(readReviewJournal(store,key),pending);
 assert.equal(await withReviewJournalLock(key,()=>rejectReviewRequest(store,key,other,'CAS'),manager),false);assert.deepEqual(readReviewJournal(store,key),pending);
 await assert.rejects(withReviewJournalLock(key,()=>{throw Error('must not run');},{}),/浏览器/);
});
test('server business identities and hash states fail closed',()=>{
 assert.throws(()=>saveReviewRequest(storage(),reviewJournalKey(account),{...pending,body:{...pending.body,business_id:'unsafe'}}),/请求/);
 const absent=view(8,'missing');absent.automation_review.businesses[0].policy_hash=null;assert.equal(reviewBusiness(absent,'库位资料核查').status,'missing');
 const invalid=view();invalid.automation_review.businesses[0].policy_hash=null;assert.throws(()=>reviewBusiness(invalid,'库位资料核查'),/服务器/);
});

test('legacy pending review stays untouched and same-name user IDs have separate journals',()=>{const store=storage(),old=legacyReviewJournalKey(account),bytes=JSON.stringify(pending);store.setItem(old,bytes);assert.notEqual(reviewJournalKey(account),reviewJournalKey({...account,user_id:'replacement'}));assert.throws(()=>readScopedReviewJournal(store,account),/旧版未决.*原记录保留/);assert.equal(store.getItem(old),bytes);assert.equal(store.getItem(reviewJournalKey(account)),null);});

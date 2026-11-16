import test from 'node:test';
import assert from 'node:assert/strict';
import {copy,digest} from '../src/report-configuration-contract.mjs';
import {SourceRequirementSession,verifyRequirementSaved} from '../src/source-requirement-session.mjs';

class Storage{
 constructor(){this.map=new Map();this.fail=false;}
 get length(){return this.map.size;}
 key(i){return [...this.map.keys()][i]??null;}
 getItem(key){return this.map.get(key)??null;}
 setItem(key,value){if(this.fail)throw Error('quota');this.map.set(key,value);}
}
const account=()=>({user_id:crypto.randomUUID(),workspace_id:crypto.randomUUID(),role:'admin'});
const view=()=>({selected_unit:{source_unit_id:'w:p1'},binding:{draft_revision:3,
 source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),candidate_kind:'not_generated',candidate_hash:null,effective_value_hash:null}});
function session(options={}){
 const state=new SourceRequirementSession({account:account(),draftID:crypto.randomUUID(),storage:new Storage(),
  identity:async()=>{},request:async()=>{throw Error('not supplied');},...options});
 state.begin(view());state.edit({requirement:'合成要求',reason:'合成人工判断'});return state;
}
async function reply(subject,pending,{history=false,changeValue=false}={}){
 const record=copy(pending.body.value);if(changeValue)record.reason+=' mismatch';
 let checked={schema:'report-source-requirement-checked-v1',record,record_hash:await digest(record),
  source_unit_bindings:[{source_unit_id:'w:p1',unit_hash:'c'.repeat(64)}],target_bindings:[]};
 checked={...checked,check_hash:await digest(checked)};
 const commandHash=await digest({kind:'source_requirement_check',workspace_id:subject.account.workspace_id,
  actor_id:subject.account.user_id,draft_id:subject.draftID,body:pending.body});
 let receipt={schema:'report-source-requirement-receipt-v1',workspace_id:subject.account.workspace_id,
  actor_id:subject.account.user_id,draft_id:subject.draftID,check_id:pending.body.check_id,
  item_version:pending.body.expected_version+1,request_id:pending.body.request_id,command_hash:commandHash,
  checked_hash:await digest(checked),binding_hash:await digest(pending.binding),saved_at:'2026-10-18T12:00:00+00:00'};
 receipt={...receipt,receipt_hash:await digest(receipt)};
 return {replayed:history,saved:{check_id:pending.body.check_id,version:receipt.item_version,checked,binding:copy(pending.binding),
  receipt,review_status:{status:history?'history':'pending_candidate',reasons:[]},
  can_apply:false,can_publish:false,semantic_completeness_verified:false}};
}

test('uncertain save preserves exact nonce; receipt recovery does not issue another PUT',async()=>{
 const subject=session();let calls=[];
 subject.request=async(path,options)=>{calls.push({path,options});throw Error('connection lost after commit');};
 await assert.rejects(subject.save());const pending=copy(subject.state.pending);
 assert.equal(subject.state.dirty,true);assert.match(calls[0].path,/requirement-checks/);
 subject.request=async(path,options)=>{calls.push({path,options});return reply(subject,pending,{history:true});};
 await subject.recover(true);
 assert.equal(calls.length,2);assert.match(calls[1].path,new RegExp('requirement-receipts/'+pending.body.request_id));
 assert.equal(subject.state.pending,null);assert.equal(subject.state.dirty,false);assert.equal(subject.state.version,1);
});

test('inputs typed while save is pending survive its later successful receipt',async()=>{
 const subject=session();let complete;
 subject.request=()=>new Promise(resolve=>{complete=resolve;});
 const saving=subject.save();await new Promise(resolve=>setImmediate(resolve));
 const pending=copy(subject.state.pending);subject.edit({requirement:'后来输入不可覆盖'});
 complete(await reply(subject,pending));await saving;
 assert.equal(subject.state.form.requirement,'后来输入不可覆盖');assert.equal(subject.state.dirty,true);
 assert.equal(subject.state.pending,null);assert.equal(subject.state.version,1);
});

test('local write failure keeps RAM input, blocks leave and dispatch including retries',async()=>{
 const subject=session();let calls=0;subject.request=async()=>{calls++;throw Error();};
 subject.storage.fail=true;subject.edit({reason:'即使本机空间不足也保留输入'});
 await assert.rejects(subject.save(),/本机/);await assert.rejects(subject.recover(false),/本机/);
 assert.equal(calls,0);assert.equal(subject.canLeave(),false);assert.equal(subject.state.form.reason,'即使本机空间不足也保留输入');
});

test('an inactive page does not adopt late saved content; a fresh owner recovers the original request',async()=>{
 const subject=session();let complete;
 subject.request=()=>new Promise(resolve=>{complete=resolve;});const saving=subject.save();
 await new Promise(resolve=>setImmediate(resolve));const pending=copy(subject.state.pending);subject.deactivate();
 complete(await reply(subject,pending));await assert.rejects(saving,/页面或账号/);
 assert.notEqual(subject.state.pending,null);
 const fresh=new SourceRequirementSession({account:subject.account,draftID:subject.draftID,storage:subject.storage,
  identity:async()=>{},request:async()=>reply(subject,pending,{history:true})});
 assert.notEqual(fresh.owner,subject.owner);fresh.restore(fresh.restoreCandidates()[0]);await fresh.recover(true);
 assert.equal(fresh.state.pending,null);assert.equal(fresh.state.dirty,false);
});

test('self-consistent but different content or scope cannot acknowledge a pending human save',async()=>{
 const subject=session();let pending;
 subject.request=async()=>{pending=copy(subject.state.pending);return reply(subject,pending,{changeValue:true});};
 await assert.rejects(subject.save());assert.notEqual(subject.state.pending,null);
 const valid=(await reply(subject,pending)).saved;
 await assert.rejects(verifyRequirementSaved(valid,{...subject.account,workspace_id:crypto.randomUUID()},subject.draftID,pending));
});

test('GET receipt not found keeps unknown pending; original serialized PUT rejection permits correction',async()=>{
 const subject=session();subject.request=async()=>{throw Error('unknown');};await assert.rejects(subject.save());
 const original=copy(subject.state.pending);
 subject.request=async()=>{throw Object.assign(Error('not found'),{status:404,detail:{code:'receipt_not_found'}});};
 await assert.rejects(subject.recover(true));assert.deepEqual(subject.state.pending,original);
 subject.request=async(_path,options)=>{assert.deepEqual(JSON.parse(options.body),original.body);
  throw Object.assign(Error('new revision'),{status:409,detail:{code:'draft_revision_conflict'}});};
 await assert.rejects(subject.recover(false));assert.equal(subject.state.pending,null);assert.equal(subject.state.dirty,true);
});

test('separate tabs keep independent local journals, and other accounts cannot discover them',()=>{
 const shared=new Storage(),identity=account(),id=crypto.randomUUID();
 const first=session({storage:shared,account:identity,draftID:id});
 const second=new SourceRequirementSession({storage:shared,account:identity,draftID:id});
 assert.equal(second.restoreCandidates().length,1);second.restore(second.restoreCandidates()[0]);second.edit({reason:'第二个页面的输入'});
 assert.equal(JSON.parse(shared.getItem(first.key)).form.reason,'合成人工判断');
 assert.equal(new SourceRequirementSession({storage:shared,account:account(),draftID:id}).restoreCandidates().length,0);
});

test('restoring a historical version appends after the latest version and preserves the original receipt',async()=>{
 const subject=session();let original;
 subject.request=async()=>{original=copy(subject.state.pending);return reply(subject,original);};
 const saved=(await subject.save()).saved,before=copy(saved);
 subject.restoreHistorical(saved,3);
 assert.equal(subject.state.version,3);assert.equal(subject.state.dirty,true);assert.equal(subject.state.lastReceipt,null);
 subject.edit({reason:'重新对照后的说明'});
 let command;
 subject.request=async()=>{command=copy(subject.state.pending);return reply(subject,command);};
 await subject.save();
 assert.equal(command.body.check_id,original.body.check_id);assert.equal(command.body.expected_version,3);
 assert.notEqual(command.body.request_id,original.body.request_id);assert.equal(subject.state.version,4);
 assert.deepEqual(saved,before);assert.equal(subject.state.form.reason,'重新对照后的说明');
});

test('a definite competing-version rejection retains input until the user chooses the newer base',async()=>{
 const subject=session();let original;
 subject.request=async()=>{original=copy(subject.state.pending);return reply(subject,original);};
 const saved=(await subject.save()).saved;
 subject.edit({requirement:'我在另一页面保存前输入的要求'});
 subject.request=async()=>{throw Object.assign(Error('另一个页面已修改'),{status:409,detail:{code:'requirement_version_conflict'}});};
 await assert.rejects(subject.save());assert.equal(subject.state.pending,null);
 assert.equal(subject.state.version,1);assert.equal(subject.state.form.requirement,'我在另一页面保存前输入的要求');
 const remote={...copy(saved),version:2};subject.keepOwnWithLatest(remote);
 assert.equal(subject.state.version,2);assert.equal(subject.state.form.requirement,'我在另一页面保存前输入的要求');
 let pending;
 subject.request=async()=>{pending=copy(subject.state.pending);return reply(subject,pending);};await subject.save();
 assert.equal(pending.body.expected_version,2);assert.equal(subject.state.version,3);
});

test('stashing an uncertain save preserves its exact journal while allowing a separate new requirement',async()=>{
 const subject=session();subject.request=async()=>{throw Error('保存结果未知');};
 await assert.rejects(subject.save());const oldKey=subject.key,oldState=copy(subject.state);
 subject.stash();assert.notEqual(subject.key,oldKey);assert.equal(subject.state.form,null);
 assert.deepEqual(JSON.parse(subject.storage.getItem(oldKey)),oldState);
 subject.begin(view());subject.edit({requirement:'另一项要求',reason:'新的说明'});
 assert.notEqual(subject.state.checkID,oldState.checkID);
 assert.deepEqual(JSON.parse(subject.storage.getItem(oldKey)),oldState);
 subject.stash();const entry=subject.restoreCandidates().find(item=>item.key===oldKey);
 subject.restore(entry);assert.deepEqual(subject.state.pending,oldState.pending);
 subject.request=async()=>reply(subject,oldState.pending,{history:true});await subject.recover(true);
 assert.equal(subject.state.pending,null);assert.equal(subject.state.form.requirement,oldState.form.requirement);
});

test('failed local persistence cannot stash or erase the existing input',()=>{
 const subject=session(),key=subject.key,form=copy(subject.state.form);subject.storage.fail=true;
 assert.throws(()=>subject.stash(),/本机/);assert.equal(subject.key,key);
 assert.deepEqual(subject.state.form,form);assert.equal(subject.canLeave(),false);
});

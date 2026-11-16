import test from 'node:test';
import assert from 'node:assert/strict';
import {enumerateGraphSourceRecovery,readGraphSourceRecoveryCandidate,GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH} from '../src/graph-sources-recovery-v123.mjs';

// These are structural local-cache fixtures, never server-issued authority.
// The production controller independently hashes commands and reads real APIs.
const ID=n=>'83000000-0000-4000-8000-'+String(n).padStart(12,'0');
const scope={actor_id:ID(1),workspace_id:ID(2)},windowID=ID(5),owner=ID(6),H='a'.repeat(64),J='b'.repeat(64);
const copy=value=>JSON.parse(JSON.stringify(value));
const prefixFor=value=>'ga-graph-sources-v122:'+value.workspace_id+':'+value.actor_id+':';
const prefix=prefixFor(scope),raw=value=>JSON.stringify(value);
class Storage{
 constructor(entries=[]){this.rows=new Map(entries);this.reads=[];this.keys=[];this.failLength=false;this.failKey=false;this.failGet=false;this.forbidden=new Set();}
 get length(){if(this.failLength)throw Error('private length failure');return this.rows.size;}
 key(index){if(this.failKey)throw Error('private directory failure');this.keys.push(index);return [...this.rows.keys()][index]??null;}
 getItem(key){this.reads.push(key);if(this.failGet||this.forbidden.has(key))throw Error('private value failure');return this.rows.get(key)??null;}
 setItem(){throw Error('Recovery discovery must never write');}
 removeItem(){throw Error('Recovery discovery must never remove');}
}
const capability=(kind,id,name)=>({id,name,kind,content_hash:H,release_version:'1.2.3',description:'已发布的合成能力说明'});
function form(){return {label:'流域核对的观测结果',adapter_id:'processed-observation-v1',
 source:capability('source',ID(3),'流域观测来源'),extractor:capability('tool',ID(4),'温度核对'),selected_registration:null,reasons:{[ID(9)]:'保留原停止说明'}};}
function register(){return {schema:'graph-event-producer-command-v1',request_id:ID(21),label:'供巡查业务选择的温度结果',adapter_id:'processed-observation-v1',source_id:ID(3),source_hash:H,extractor_id:ID(4),extractor_hash:H};}
function revoke(){return {schema:'graph-event-producer-revocation-v2',request_id:ID(22),producer_id:ID(9),producer_version:3,contract_hash:H,reason:'核对后停止供应，保留历史记录'};}
function requestRecord(kind='register'){
 const body=kind==='register'?register():revoke();
 return {schema:'graph-source-outbox-v122',scope:copy(scope),window_id:windowID,kind,body,exact_body:raw(body),command_hash:H,preview_contract_hash:kind==='register'?H:null};
}
function revokeReceipt(body=revoke()){return {id:ID(23),request_id:body.request_id,actor_id:scope.actor_id,producer_id:body.producer_id,command_hash:H,command:copy(body),result:{status:'revoked',version:body.producer_version+1,contract_hash:body.contract_hash}};}
function sourceCompleted(kind='register',withReceipt=false){
 const value=requestRecord(kind),record={schema:'graph-source-completed-v122',scope:copy(scope),window_id:windowID,kind,command_hash:H,exact_body:value.exact_body};
 if(withReceipt)record.receipt=revokeReceipt(value.body);return record;
}
function retirement(kind='register'){
 const original=kind==='register'?register():revoke();
 return {schema:'graph-event-source-retirement-v1',request_id:ID(24),kind,original,reason:'不再发送这项原请求，仍保留原内容'};
}
function retirementReceipt(body=retirement()){return {id:ID(25),request_id:body.request_id,actor_id:scope.actor_id,kind:body.kind,
 original_request_id:body.original.request_id,original_command_hash:H,command_hash:J,command:copy(body),
 result:{outcome:'retired',kind:body.kind,original_request_id:body.original.request_id,original_command_hash:H}};}
function retireOutbox(kind='register'){
 const body=retirement(kind);return {schema:'graph-source-retirement-outbox-v123',scope:copy(scope),window_id:ID(26),body,exact_body:raw(body),command_hash:J,original_window_id:windowID};
}
function retireCompleted(){const body=retirement();return {schema:'graph-source-retirement-completed-v123',scope:copy(scope),window_id:ID(26),command_hash:J,exact_body:raw(body),receipt:retirementReceipt(body)};}
function retiredSource(){return {schema:'graph-source-retired-v123',scope:copy(scope),window_id:windowID,kind:'register',command_hash:H,exact_body:raw(register()),retirement:retirementReceipt()};}
function draftRecord(type='slot',generation=3){return {schema:'graph-source-form-'+type+'-v122',scope:copy(scope),window_id:windowID,owner,...(type==='slot'?{generation}:{}),form:form()};}
function enumerate(storage,options={}){return enumerateGraphSourceRecovery({storage,scope,...options});}
function read(storage,candidate){return readGraphSourceRecoveryCandidate({storage,scope,candidate});}

test('empty session discovery finds current-scope legacy drafts, owner entries and both slots without touching any stored byte',()=>{
 const session=new Storage(),old={schema:'graph-source-form-v122',scope:copy(scope),window_id:windowID,form:form()};
 const entries=[[prefix+'form:'+ID(30),raw(old)],[prefix+'form:entry:'+owner,raw(draftRecord('entry'))],
  [prefix+'form:slot:'+owner+':1',raw(draftRecord('slot',3))],[prefix+'form:slot:'+owner+':0',raw(draftRecord('slot',4))]];
 const storage=new Storage(entries),before=[...storage.rows],page=enumerate(storage);
 assert.equal(session.length,0);assert.equal(page.items.length,4);assert.equal(page.next_offset,null);assert.equal(page.problem_count,0);
 for(const candidate of page.items){assert.equal(candidate.kind,'draft');assert.equal(candidate.local_completion,false);assert.ok(candidate.label.includes('流域'));
  assert.ok(candidate.summary.includes('温度核对'));assert.ok(!candidate.label.includes(owner));assert.ok(!candidate.summary.includes(H));assert.equal(read(storage,candidate).record.form.label,form().label);}
 assert.equal(page.items[0].owner,null);assert.equal(page.items[0].generation,null);assert.equal(page.items[1].owner,owner);assert.equal(page.items[1].generation,null);
 assert.equal(page.items[2].generation,3);assert.deepEqual([...storage.rows],before);assert.equal(session.length,0);
});

test('discovery never reads or parses another actor or workspace record, including malformed private values',()=>{
 const otherActor={...scope,actor_id:ID(31)},otherWorkspace={...scope,workspace_id:ID(32)};
 const keys=[prefixFor(otherActor)+'outbox:'+ID(21),prefixFor(otherWorkspace)+'outbox:'+ID(21),prefix.slice(0,-1)+'-suffix:outbox:'+ID(21)];
 const storage=new Storage(keys.map(key=>[key,'private malformed payload']));for(const key of keys)storage.forbidden.add(key);
 const page=enumerate(storage);assert.deepEqual(page,{items:[],next_offset:null,problem_count:0});assert.deepEqual(storage.reads,[]);
});

test('request discovery retains register and revoke exact bodies and does not require a session pointer',()=>{
 const values=[requestRecord(),requestRecord('revoke')],storage=new Storage(values.map(value=>[prefix+'outbox:'+value.body.request_id,raw(value)]));
 const page=enumerate(storage);assert.equal(page.items.length,2);assert.equal(page.problem_count,0);
 for(const candidate of page.items){const result=read(storage,candidate);assert.equal(result.kind,'request');assert.equal(result.raw,storage.rows.get(result.key));assert.equal(result.record.exact_body,raw(result.record.body));assert.equal(candidate.local_completion,false);}
});

test('canonical lowercase version-seven and zero UUID records remain recoverable without adding a version or variant gate',()=>{
 const versionSeven='018f099f-7639-7c65-b456-321821dcba73',zero='00000000-0000-0000-0000-000000000000';
 const selectedScope={actor_id:versionSeven,workspace_id:zero},selectedPrefix=prefixFor(selectedScope);
 const draft=draftRecord();draft.scope=copy(selectedScope);draft.window_id=zero;draft.owner=versionSeven;draft.form.selected_registration=zero;
 draft.form.reasons={[versionSeven]:'保留早先已接受的原稿'};
 const request=requestRecord();request.scope=copy(selectedScope);request.window_id=zero;request.body.request_id=zero;request.body.source_id=versionSeven;request.exact_body=raw(request.body);
 const storage=new Storage([[selectedPrefix+'form:slot:'+versionSeven+':1',raw(draft)],[selectedPrefix+'outbox:'+zero,raw(request)]]);
 const page=enumerateGraphSourceRecovery({storage,scope:selectedScope});assert.equal(page.problem_count,0);assert.equal(page.items.length,2);
 for(const candidate of page.items)assert.equal(readGraphSourceRecoveryCandidate({storage,scope:selectedScope,candidate}).raw,candidate.raw);
 const reads=storage.reads.length;
 assert.throws(()=>enumerateGraphSourceRecovery({storage,scope:{...selectedScope,actor_id:versionSeven.toUpperCase()}}),{code:'GRAPH_SOURCE_RECOVERY_RECORD'});
 assert.equal(storage.reads.length,reads);
 request.body.request_id=versionSeven.toUpperCase();request.exact_body=raw(request.body);
 const upper=new Storage([[selectedPrefix+'outbox:'+request.body.request_id,raw(request)]]);
 assert.deepEqual(enumerateGraphSourceRecovery({storage:upper,scope:selectedScope}),{items:[],next_offset:null,problem_count:1});
});

test('local completion remains a discoverable request hint and never means a server result has been verified',()=>{
 const values=[sourceCompleted(),sourceCompleted('revoke'),sourceCompleted('revoke',true)];
 for(const value of values){const body=JSON.parse(value.exact_body),storage=new Storage([[prefix+'completed:'+body.request_id,raw(value)]]),page=enumerate(storage);
  assert.equal(page.items.length,1);const candidate=page.items[0];assert.equal(candidate.kind,'request');assert.equal(candidate.local_completion,true);
  assert.ok(candidate.summary.includes('不是服务器回执'));assert.ok(candidate.summary.includes('仍需'));assert.equal(read(storage,candidate).record.exact_body,value.exact_body);
  assert.equal(Object.hasOwn(candidate,'saved'),false);assert.equal(Object.hasOwn(candidate,'server_verified'),false);}
});

test('retirement requests and completion hints keep the immutable original command and both window identities',()=>{
 const pending=retireOutbox('revoke'),complete=retireCompleted(),marker=retiredSource();
 const entries=[[prefix+'retire-outbox:'+pending.body.request_id,raw(pending)],
  [prefix+'retire-completed:'+JSON.parse(complete.exact_body).request_id,raw(complete)],[prefix+'retired:'+register().request_id,raw(marker)]];
 const storage=new Storage(entries),before=[...storage.rows],page=enumerate(storage);
 assert.deepEqual(page.items.map(value=>value.kind),['retirement','retirement','request']);assert.deepEqual(page.items.map(value=>value.local_completion),[false,true,true]);
 const selected=read(storage,page.items[0]);assert.equal(selected.record.original_window_id,windowID);assert.equal(selected.record.window_id,ID(26));
 assert.deepEqual(selected.record.body.original,revoke());assert.equal(selected.record.exact_body,raw(pending.body));
 selected.record.body.original.reason='independent detached read';assert.deepEqual([...storage.rows],before);assert.deepEqual(read(storage,page.items[0]).record.body.original,revoke());
});

test('selected slots are reread exactly and a concurrently reused slot is refused rather than replaced with its newer draft',()=>{
 const key=prefix+'form:slot:'+owner+':1',storage=new Storage([[key,raw(draftRecord())]]),candidate=enumerate(storage).items[0];
 const changed=draftRecord('slot',5);changed.form.label='另一窗口后来的输入';storage.rows.set(key,raw(changed));
 assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_RECOVERY_CHANGED'});assert.equal(JSON.parse(storage.rows.get(key)).form.label,changed.form.label);
 assert.equal(enumerate(storage).items[0].generation,5);storage.rows.delete(key);assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_RECOVERY_CHANGED'});
});

test('even whitespace-only raw changes require a fresh explicit selection',()=>{
 const value=requestRecord(),key=prefix+'outbox:'+value.body.request_id,storage=new Storage([[key,raw(value)]]),candidate=enumerate(storage).items[0];
 storage.rows.set(key,JSON.stringify(value,null,2));assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_RECOVERY_CHANGED'});
 const next=enumerate(storage).items[0];assert.equal(read(storage,next).raw,storage.rows.get(key));
});

test('candidate scope and presentation bindings cannot be swapped before rereading',()=>{
 const value=requestRecord(),key=prefix+'outbox:'+value.body.request_id,storage=new Storage([[key,raw(value)]]),candidate=enumerate(storage).items[0];
 const reads=storage.reads.length;
 assert.throws(()=>read(storage,{...candidate,scope:{...scope,actor_id:ID(40)}}),{code:'GRAPH_SOURCE_RECOVERY_RECORD'});
 assert.throws(()=>read(storage,{...candidate,key:prefixFor({...scope,workspace_id:ID(41)})+'outbox:'+value.body.request_id}),{code:'GRAPH_SOURCE_RECOVERY_RECORD'});
 assert.equal(storage.reads.length,reads);
 for(const patch of [{label:'伪造的另一项人稿'},{window_id:ID(42)},{owner:owner},{generation:1},{kind:'draft'},{local_completion:true},{extra:true}])
  assert.throws(()=>read(storage,{...candidate,...patch}),{code:'GRAPH_SOURCE_RECOVERY_RECORD'});
});

test('malformed and oversized matching records are counted generically, retained and never replaced',()=>{
 const tooLong='x'.repeat(GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH+1),badScope=requestRecord();badScope.scope.actor_id=ID(45);
 const entries=[[prefix+'bad-json','{"private":"unfinished"'],[prefix+'huge',tooLong],[prefix+'outbox:'+badScope.body.request_id,raw(badScope)]];
 const storage=new Storage(entries),before=[...storage.rows],page=enumerate(storage);
 assert.deepEqual(page,{items:[],next_offset:null,problem_count:3});assert.deepEqual([...storage.rows],before);assert.equal(Object.hasOwn(page,'errors'),false);
});

test('bounded scanning can produce an empty page and resumes after exactly the scanned physical keys',()=>{
 const foreign={...scope,actor_id:ID(50)},value=requestRecord(),entries=[];
 for(let i=0;i<7;i++)entries.push([prefixFor(foreign)+'private:'+i,'not readable here']);entries.push([prefix+'bad','broken'],[prefix+'outbox:'+value.body.request_id,raw(value)]);
 const storage=new Storage(entries);for(const [key] of entries.slice(0,7))storage.forbidden.add(key);
 assert.deepEqual(enumerate(storage,{maxScan:3}),{items:[],next_offset:3,problem_count:0});assert.equal(storage.keys.length,3);assert.equal(storage.reads.length,0);
 assert.deepEqual(enumerate(storage,{offset:3,maxScan:3}),{items:[],next_offset:6,problem_count:0});
 const page=enumerate(storage,{offset:6,maxScan:2});assert.deepEqual(page,{items:[],next_offset:8,problem_count:1});
 const final=enumerate(storage,{offset:8,maxScan:2});assert.equal(final.items.length,1);assert.equal(final.next_offset,null);
});

test('item limits do not silently scan remaining matches, and multiple explicit pages retain every old entry',()=>{
 const entries=[];for(let index=0;index<5;index++){const value=draftRecord('entry');value.owner=ID(60+index);entries.push([prefix+'form:entry:'+value.owner,raw(value)]);}
 const storage=new Storage(entries),before=[...storage.rows],seen=[];let offset=0;
 do{const page=enumerate(storage,{offset,limit:2,maxScan:200});seen.push(...page.items.map(value=>value.key));offset=page.next_offset;}while(offset!==null);
 assert.deepEqual(seen,entries.map(([key])=>key));assert.equal(storage.keys.length,5);assert.deepEqual([...storage.rows],before);
 assert.deepEqual(enumerate(storage,{offset:50}),{items:[],next_offset:null,problem_count:0});
});

test('copied window IDs and independent draft owners are listed without changing or claiming either tab',()=>{
 const first=draftRecord(),second=draftRecord();second.owner=ID(70);second.form.label='另一页保留的人稿';
 const storage=new Storage([[prefix+'form:slot:'+owner+':1',raw(first)],[prefix+'form:slot:'+second.owner+':1',raw(second)]]),before=[...storage.rows];
 const page=enumerate(storage);assert.equal(page.items.length,2);assert.equal(page.items[0].window_id,page.items[1].window_id);
 assert.notEqual(page.items[0].owner,page.items[1].owner);for(const candidate of page.items)read(storage,candidate);assert.deepEqual([...storage.rows],before);
});

test('storage exceptions are explicit generic failures rather than empty or incomplete recovery success',()=>{
 for(const failure of ['failLength','failKey','failGet']){const value=requestRecord(),storage=new Storage([[prefix+'outbox:'+value.body.request_id,raw(value)]]);storage[failure]=true;
  assert.throws(()=>enumerate(storage),error=>error.code==='GRAPH_SOURCE_RECOVERY_STORAGE'&&!error.message.includes('private'));}
 const value=requestRecord(),storage=new Storage([[prefix+'outbox:'+value.body.request_id,raw(value)]]),candidate=enumerate(storage).items[0];storage.failGet=true;
 assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_RECOVERY_STORAGE'});
});

test('offset, scan and item counts are strict bounded integers',()=>{
 const storage=new Storage();for(const options of [{offset:-1},{offset:0.5},{offset:true},{limit:0},{limit:51},{limit:'2'},{maxScan:0},{maxScan:201},{maxScan:Infinity}])
  assert.throws(()=>enumerate(storage,options),{code:'GRAPH_SOURCE_RECOVERY_RECORD'});
 assert.deepEqual(storage.reads,[]);assert.deepEqual(storage.keys,[]);
});

test('strict schemas reject malformed fields, mismatched keys, body substitutions and fake retirement linkage',async t=>{
 const cases=[
  ['unexpected draft field',draftRecord(),value=>{value.extra=true;},value=>prefix+'form:slot:'+value.owner+':1'],
  ['boolean generation',draftRecord(),value=>{value.generation=true;},value=>prefix+'form:slot:'+value.owner+':1'],
  ['wrong slot parity',draftRecord(),()=>{},value=>prefix+'form:slot:'+value.owner+':0'],
  ['unknown capability kind',draftRecord(),value=>{value.form.extractor.kind='data';},value=>prefix+'form:slot:'+value.owner+':1'],
  ['invalid Unicode human draft',draftRecord(),value=>{value.form.label='\ud800';},value=>prefix+'form:slot:'+value.owner+':1'],
  ['raw reason has NUL',requestRecord('revoke'),value=>{value.body.reason+='\u0000';value.exact_body=raw(value.body);},value=>prefix+'outbox:'+value.body.request_id],
  ['missing original command field',requestRecord(),value=>{delete value.body.source_hash;value.exact_body=raw(value.body);},value=>prefix+'outbox:'+value.body.request_id],
  ['body and exact body diverge',requestRecord(),value=>{value.body.label='替换正文';},value=>prefix+'outbox:'+value.body.request_id],
  ['source outbox wrong nonce key',requestRecord(),()=>{},()=>prefix+'outbox:'+ID(99)],
  ['source outbox bad hash',requestRecord(),value=>{value.command_hash='bad';},value=>prefix+'outbox:'+value.body.request_id],
  ['source completion extra receipt',sourceCompleted(),value=>{value.receipt=revokeReceipt();},value=>prefix+'completed:'+JSON.parse(value.exact_body).request_id],
  ['stop completion different actor',sourceCompleted('revoke',true),value=>{value.receipt.actor_id=ID(98);},value=>prefix+'completed:'+JSON.parse(value.exact_body).request_id],
  ['retirement missing origin window',retireOutbox(),value=>{delete value.original_window_id;},value=>prefix+'retire-outbox:'+value.body.request_id],
  ['retirement original unknown kind',retireOutbox(),value=>{value.body.kind='delete';value.exact_body=raw(value.body);},value=>prefix+'retire-outbox:'+value.body.request_id],
  ['retirement result mismatches original',retireCompleted(),value=>{value.receipt.result.original_request_id=ID(97);},value=>prefix+'retire-completed:'+JSON.parse(value.exact_body).request_id],
  ['retired marker does not own original body',retiredSource(),value=>{value.retirement.command.original.label='另一项原命令';},value=>prefix+'retired:'+JSON.parse(value.exact_body).request_id],
  ['retired marker different original hash',retiredSource(),value=>{value.retirement.original_command_hash=J;value.retirement.result.original_command_hash=J;},value=>prefix+'retired:'+JSON.parse(value.exact_body).request_id],
 ];
 for(const [name,initial,mutate,keyFor] of cases)await t.test(name,()=>{const value=copy(initial);mutate(value);const key=keyFor(value),stored=raw(value),storage=new Storage([[key,stored]]);
  assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});assert.equal(storage.rows.get(key),stored);});
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {enumerateGraphSourceDeliveryRecovery,readGraphSourceDeliveryRecoveryCandidate,GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH} from '../src/graph-source-delivery-recovery-v124.mjs';

// These fixtures certify only structural local recovery. Command hashes and
// server-issued retry/admission receipts are verified by the real controller.
const ID=n=>'84000000-0000-4000-8000-'+String(n).padStart(12,'0');
const scope={actor_id:ID(1),workspace_id:ID(2)},H='d'.repeat(64),copy=value=>JSON.parse(JSON.stringify(value));
const prefixFor=value=>'ga-graph-delivery-v124:'+value.workspace_id+':'+value.actor_id+':',prefix=prefixFor(scope),raw=value=>JSON.stringify(value);
class Storage{
 constructor(entries=[]){this.rows=new Map(entries);this.reads=[];this.keys=[];this.forbidden=new Set();this.failLength=false;this.failKey=false;this.failGet=false;}
 get length(){if(this.failLength)throw Error('private length failure');return this.rows.size;}
 key(index){if(this.failKey)throw Error('private directory failure');this.keys.push(index);return [...this.rows.keys()][index]??null;}
 getItem(key){this.reads.push(key);if(this.failGet||this.forbidden.has(key))throw Error('private value failure');return this.rows.get(key)??null;}
 setItem(){throw Error('The pure recovery model must not write');}
 removeItem(){throw Error('The pure recovery model must not delete');}
 clear(){throw Error('The pure recovery model must not clear storage');}
}
function record(completed=false,nonce=ID(6)){
 const body={request_id:nonce,job_version:3,reason:'  按原处理结果核对投递，保留原输入。  '};
 return {schema:completed?'graph-source-delivery-completed-v124':'graph-source-delivery-outbox-v124',scope:copy(scope),window_id:ID(3),
  intent_id:ID(4),producer_id:ID(5),body,exact_body:raw(body),command_hash:H};
}
const keyFor=value=>prefixFor(value.scope)+(value.schema==='graph-source-delivery-completed-v124'?'completed:':'outbox:')+value.body.request_id;
const storageFor=(...records)=>new Storage(records.map(value=>[keyFor(value),raw(value)]));
const enumerate=(storage,options={})=>enumerateGraphSourceDeliveryRecovery({storage,scope,...options});
const read=(storage,candidate)=>readGraphSourceDeliveryRecoveryCandidate({storage,scope,candidate});

test('an empty new session can discover and reread the exact original delivery request using only retained local storage',()=>{
 const session=new Storage(),value=record(),storage=storageFor(value),before=[...storage.rows],page=enumerate(storage);
 assert.equal(session.length,0);assert.equal(page.items.length,1);assert.equal(page.next_offset,null);assert.equal(page.problem_count,0);
 const candidate=page.items[0];assert.deepEqual(Object.keys(candidate).sort(),['key','raw','scope','window_id','intent_id','producer_id','kind','label','summary','local_completion'].sort());
 assert.equal(candidate.kind,'request');assert.equal(candidate.intent_id,value.intent_id);assert.equal(candidate.producer_id,value.producer_id);assert.equal(candidate.window_id,value.window_id);
 assert.equal(candidate.local_completion,false);assert.ok(candidate.summary.includes('向服务器核对'));assert.ok(candidate.summary.includes('按原处理结果'));
 assert.ok(!candidate.label.includes(value.intent_id));assert.ok(!candidate.summary.includes(value.producer_id));assert.ok(!candidate.summary.includes(H));
 const selected=read(storage,candidate);assert.deepEqual(selected,{record:value,raw:raw(value),key:keyFor(value),kind:'request'});
 assert.deepEqual([...storage.rows],before);assert.equal(session.length,0);
});

test('another actor, workspace and adjacent prefix cause zero value reads even when their contents are corrupt',()=>{
 const scopes=[{...scope,actor_id:ID(11)},{...scope,workspace_id:ID(12)}],keys=scopes.map(value=>prefixFor(value)+'outbox:'+ID(6));
 keys.push(prefix.slice(0,-1)+'-other:outbox:'+ID(6),'ga-graph-sources-v122:'+scope.workspace_id+':'+scope.actor_id+':outbox:'+ID(6));
 const storage=new Storage(keys.map(key=>[key,'private broken payload']));keys.forEach(key=>storage.forbidden.add(key));
 assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:0});assert.deepEqual(storage.reads,[]);
});

test('completed hints remain discoverable requests and carry neither mutable delivery state nor claimed server authority',()=>{
 const original=record(),completed=record(true),storage=storageFor(original,completed),before=[...storage.rows],page=enumerate(storage);
 assert.equal(page.items.length,2);assert.deepEqual(page.items.map(value=>value.local_completion),[false,true]);
 assert.ok(page.items[1].summary.includes('本机曾记录核对完成'));assert.ok(page.items[1].summary.includes('仍需向服务器核对'));
 for(const candidate of page.items){assert.equal(candidate.kind,'request');const selected=read(storage,candidate);
  assert.deepEqual(Object.keys(selected.record).sort(),['schema','scope','window_id','intent_id','producer_id','body','exact_body','command_hash'].sort());
  for(const field of ['saved','server_verified','receipt','delivery','admission','accepted_job_version'])assert.equal(Object.hasOwn(selected.record,field),false);}
 assert.deepEqual([...storage.rows],before);
});

test('the original whitespace reason and Unicode bytes remain untouched while display text may be trimmed and clipped',()=>{
 for(const reason of ['  原投递恢复说明\n  ',' \t\n ','温'.repeat(1000),'🌲'.repeat(1000),'e\u0301 与 é 保留各自原字符']){
  const value=record();value.body.reason=reason;value.exact_body=raw(value.body);const storage=storageFor(value),page=enumerate(storage);
  assert.equal(page.problem_count,0);assert.equal(page.items.length,1);const selected=read(storage,page.items[0]);
  assert.equal(selected.record.body.reason,reason);assert.equal(selected.record.exact_body,value.exact_body);assert.equal(storage.rows.get(keyFor(value)),raw(value));
  assert.ok(page.items[0].summary.length<350);}
});

test('canonical lowercase zero and version-seven UUIDs are retained without version or variant restrictions',()=>{
 const zero='00000000-0000-0000-0000-000000000000',v7='018f099f-7639-7c65-b456-321821dcba73',selectedScope={actor_id:v7,workspace_id:zero};
 const value=record();Object.assign(value,{scope:copy(selectedScope),window_id:zero,intent_id:v7,producer_id:zero});value.body.request_id=v7;value.exact_body=raw(value.body);
 const storage=storageFor(value),page=enumerateGraphSourceDeliveryRecovery({storage,scope:selectedScope});assert.equal(page.items.length,1);assert.equal(page.problem_count,0);
 assert.equal(readGraphSourceDeliveryRecoveryCandidate({storage,scope:selectedScope,candidate:page.items[0]}).record.body.request_id,v7);
 const reads=storage.reads.length;assert.throws(()=>enumerateGraphSourceDeliveryRecovery({storage,scope:{...selectedScope,actor_id:v7.toUpperCase()}}),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'});assert.equal(storage.reads.length,reads);
});

test('positive safe integer job versions survive exactly, with no new current-job or producer lookup',()=>{
 for(const version of [1,2147483647,Number.MAX_SAFE_INTEGER]){const value=record();value.body.job_version=version;value.exact_body=raw(value.body);const storage=storageFor(value),page=enumerate(storage);
  assert.equal(page.items.length,1);assert.equal(read(storage,page.items[0]).record.body.job_version,version);}
});

test('changed raw text, changed intent binding or removed records refuse the selected candidate without substituting another request',()=>{
 const value=record(),storage=storageFor(value),candidate=enumerate(storage).items[0],key=keyFor(value);
 storage.rows.set(key,JSON.stringify(value,null,2));assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_CHANGED'});
 const next=enumerate(storage).items[0];assert.equal(read(storage,next).raw,storage.rows.get(key));
 const changed=copy(value);changed.intent_id=ID(21);storage.rows.set(key,raw(changed));assert.throws(()=>read(storage,next),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_CHANGED'});
 storage.rows.delete(key);assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_CHANGED'});
});

test('candidate metadata cannot redirect an original request to the currently selected producer, intent or account',()=>{
 const value=record(),storage=storageFor(value),candidate=enumerate(storage).items[0],reads=storage.reads.length;
 for(const patch of [{scope:{...scope,actor_id:ID(30)}},{key:prefixFor({...scope,workspace_id:ID(31)})+'outbox:'+value.body.request_id}])
  assert.throws(()=>read(storage,{...candidate,...patch}),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'});
 assert.equal(storage.reads.length,reads);
 for(const patch of [{intent_id:ID(32)},{producer_id:ID(33)},{window_id:ID(34)},{kind:'draft'},{local_completion:true},{label:'另一项操作'},{summary:'伪造成功'},{extra:true}])
  assert.throws(()=>read(storage,{...candidate,...patch}),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'});
});

test('empty pages still advance across bounded physical keys without parsing foreign values',()=>{
 const other={...scope,actor_id:ID(40)},entries=[];
 for(let index=0;index<7;index++)entries.push([prefixFor(other)+'outbox:'+ID(100+index),'unreadable foreign text']);
 const value=record();entries.push([prefix+'bad','broken'],[keyFor(value),raw(value)]);const storage=new Storage(entries);entries.slice(0,7).forEach(([key])=>storage.forbidden.add(key));
 assert.deepEqual(enumerate(storage,{maxScan:3}),{items:[],next_offset:3,problem_count:0});assert.equal(storage.keys.length,3);assert.equal(storage.reads.length,0);
 assert.deepEqual(enumerate(storage,{offset:3,maxScan:3}),{items:[],next_offset:6,problem_count:0});
 assert.deepEqual(enumerate(storage,{offset:6,maxScan:2}),{items:[],next_offset:8,problem_count:1});
 const page=enumerate(storage,{offset:8,maxScan:2});assert.equal(page.items.length,1);assert.equal(page.next_offset,null);
});

test('item limits and scan limits stop independently and explicit pagination preserves all requests',()=>{
 const records=Array.from({length:5},(_,index)=>record(false,ID(50+index))),storage=storageFor(...records),before=[...storage.rows],found=[];let offset=0;
 do{const page=enumerate(storage,{offset,limit:2,maxScan:200});found.push(...page.items.map(value=>value.key));offset=page.next_offset;}while(offset!==null);
 assert.deepEqual(found,records.map(keyFor));assert.equal(storage.keys.length,5);assert.deepEqual([...storage.rows],before);
 const bounded=enumerate(storage,{limit:20,maxScan:1});assert.equal(bounded.items.length,1);assert.equal(bounded.next_offset,1);
 assert.deepEqual(enumerate(storage,{offset:99}),{items:[],next_offset:null,problem_count:0});
});

test('duplicated browser windows and completed hints are read without claiming, deleting or rewriting any original record',()=>{
 const a=record(),b=record(false,ID(61)),done=record(true);b.window_id=ID(62);b.intent_id=ID(63);const storage=storageFor(a,b,done),before=[...storage.rows];
 const page=enumerate(storage);assert.equal(page.items.length,3);for(const candidate of page.items)read(storage,candidate);assert.deepEqual([...storage.rows],before);
 const selected=read(storage,page.items[0]);selected.record.body.reason='detached returned value';selected.record.scope.actor_id=ID(64);
 assert.deepEqual([...storage.rows],before);assert.equal(read(storage,page.items[0]).record.exact_body,a.exact_body);
});

test('the shared 256K cap accepts an exact-boundary record and retains oversized or malformed records as counted problems',()=>{
 const value=record(),encoded=raw(value),atLimit=encoded+' '.repeat(GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH-encoded.length),key=keyFor(value);
 const storage=new Storage([[key,atLimit]]),page=enumerate(storage);assert.equal(page.items.length,1);assert.equal(read(storage,page.items[0]).raw.length,GRAPH_SOURCE_DELIVERY_RECOVERY_MAX_RECORD_LENGTH);
 const oversized=atLimit+' ';storage.rows.set(key,oversized);assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});assert.equal(storage.rows.get(key),oversized);
 storage.rows.set(key,'{"private":"unfinished"');assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});
 assert.throws(()=>read(storage,{...page.items[0],raw:oversized}),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'});
});

test('storage failures propagate as generic errors rather than an empty successful recovery list',()=>{
 for(const failure of ['failLength','failKey','failGet']){const value=record(),storage=storageFor(value);storage[failure]=true;
  assert.throws(()=>enumerate(storage),error=>error.code==='GRAPH_SOURCE_DELIVERY_RECOVERY_STORAGE'&&!error.message.includes('private'));}
 const storage=storageFor(record()),candidate=enumerate(storage).items[0];storage.failGet=true;assert.throws(()=>read(storage,candidate),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_STORAGE'});
});

test('pagination arguments and scopes are strict before any storage is read',()=>{
 const storage=new Storage();for(const options of [{offset:-1},{offset:0.5},{offset:false},{offset:Number.MAX_SAFE_INTEGER+1},{limit:0},{limit:51},{limit:'1'},{maxScan:0},{maxScan:201},{maxScan:Infinity}])
  assert.throws(()=>enumerate(storage,options),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'});
 assert.throws(()=>enumerateGraphSourceDeliveryRecovery({storage,scope:{...scope,extra:true}}),{code:'GRAPH_SOURCE_DELIVERY_RECOVERY_RECORD'});
 assert.deepEqual(storage.reads,[]);assert.deepEqual(storage.keys,[]);
});

test('strict records reject extra fields, unsafe command values, mismatched keys and invalid UTF without modifying the originals',async t=>{
 const cases=[
  ['extra record field',value=>{value.delivery={status:'succeeded'};}],
  ['extra scope field',value=>{value.scope.extra=true;}],
  ['wrong workspace in body',value=>{value.scope.workspace_id=ID(71);}],
  ['wrong schema',value=>{value.schema='graph-source-outbox-v122';}],
  ['extra command field',value=>{value.body.schema='new-command';value.exact_body=raw(value.body);}],
  ['missing reason',value=>{delete value.body.reason;value.exact_body=raw(value.body);}],
  ['empty reason',value=>{value.body.reason='';value.exact_body=raw(value.body);}],
  ['overlong Unicode reason',value=>{value.body.reason='🌲'.repeat(1001);value.exact_body=raw(value.body);}],
  ['reason not string',value=>{value.body.reason=5;value.exact_body=raw(value.body);}],
  ['NUL reason',value=>{value.body.reason='原\u0000因';value.exact_body=raw(value.body);}],
  ['unpaired high surrogate',value=>{value.body.reason='\ud800';value.exact_body=raw(value.body);}],
  ['unpaired low surrogate',value=>{value.body.reason='\udc00';value.exact_body=raw(value.body);}],
  ['fraction version',value=>{value.body.job_version=1.5;value.exact_body=raw(value.body);}],
  ['boolean version',value=>{value.body.job_version=true;value.exact_body=raw(value.body);}],
  ['string version',value=>{value.body.job_version='1';value.exact_body=raw(value.body);}],
  ['zero version',value=>{value.body.job_version=0;value.exact_body=raw(value.body);}],
  ['unsafe integer version',value=>{value.body.job_version=Number.MAX_SAFE_INTEGER+1;value.exact_body=raw(value.body);}],
  ['null version',value=>{value.body.job_version=null;value.exact_body=raw(value.body);}],
  ['uppercase request UUID',value=>{value.body.request_id='018F099F-7639-7C65-B456-321821DCBA73';value.exact_body=raw(value.body);}],
  ['invalid window UUID',value=>{value.window_id='old-window';}],
  ['invalid intent UUID',value=>{value.intent_id=4;}],
  ['invalid producer UUID',value=>{value.producer_id=true;}],
  ['body changed after freezing',value=>{value.body.reason='后来修改的说明';}],
  ['noncanonical exact body',value=>{value.exact_body=JSON.stringify(value.body,null,2);}],
  ['bad hash',value=>{value.command_hash='saved';}],
 ];
 for(const [name,mutate] of cases)await t.test(name,()=>{const value=record();mutate(value);const key=prefix+'outbox:'+ID(6),stored=raw(value),storage=new Storage([[key,stored]]);
  assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});assert.equal(storage.rows.get(key),stored);});
 await t.test('different request nonce key',()=>{const value=record(),stored=raw(value),storage=new Storage([[prefix+'outbox:'+ID(99),stored]]);assert.equal(enumerate(storage).problem_count,1);assert.equal(storage.rows.size,1);});
 await t.test('completion schema with an outbox key',()=>{const value=record(true),storage=new Storage([[prefix+'outbox:'+value.body.request_id,raw(value)]]);assert.equal(enumerate(storage).problem_count,1);});
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {enumerateGraphSourceDeliveryRetirementRecovery,readGraphSourceDeliveryRetirementRecoveryCandidate,GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_MAX_RECORD_LENGTH as MAX} from '../src/graph-source-delivery-retirement-recovery-v125.mjs';

// Only structural local recovery is exercised here. The real controller and
// backend must verify command hashes, original outboxes and server receipts.
const ID=n=>'85000000-0000-4000-8000-'+String(n).padStart(12,'0');
const scope={actor_id:ID(1),workspace_id:ID(2)},H='e'.repeat(64),copy=value=>JSON.parse(JSON.stringify(value)),raw=value=>JSON.stringify(value);
const prefixFor=value=>'ga-graph-delivery-retirement-v125:'+value.workspace_id+':'+value.actor_id+':',prefix=prefixFor(scope);
const ERR='GRAPH_SOURCE_DELIVERY_RETIREMENT_RECOVERY_';
class Storage{
 constructor(entries=[]){this.rows=new Map(entries);this.reads=[];this.keys=[];this.lengthReads=0;this.forbidden=new Set();this.failLength=false;this.failKey=false;this.failGet=false;}
 get length(){this.lengthReads++;if(this.failLength)throw Error('private length failure');return this.rows.size;}
 key(index){if(this.failKey)throw Error('private directory failure');this.keys.push(index);return [...this.rows.keys()][index]??null;}
 getItem(key){this.reads.push(key);if(this.failGet||this.forbidden.has(key))throw Error('private value failure');return this.rows.get(key)??null;}
 setItem(){throw Error('The pure recovery model must not write');}
 removeItem(){throw Error('The pure recovery model must not delete');}
 clear(){throw Error('The pure recovery model must not clear storage');}
}
function record(completed=false,nonce=ID(7)){
 const body={schema:'graph-event-intent-retry-retirement-v1',request_id:nonce,
  original:{request_id:ID(6),job_version:3,reason:'  原恢复说明保留全部字节。  '},reason:'  保留原稿，结束这次未保存的恢复操作。  '};
 return {schema:completed?'graph-source-delivery-retirement-completed-v125':'graph-source-delivery-retirement-outbox-v125',scope:copy(scope),
  window_id:ID(3),intent_id:ID(4),producer_id:ID(5),original_window_id:ID(8),body,exact_body:raw(body),command_hash:H};
}
const keyFor=value=>prefixFor(value.scope)+(value.schema==='graph-source-delivery-retirement-completed-v125'?'completed:':'outbox:')+value.body.request_id;
const storageFor=(...values)=>new Storage(values.map(value=>[keyFor(value),raw(value)]));
const enumerate=(storage,options={})=>enumerateGraphSourceDeliveryRetirementRecovery({storage,scope,...options});
const read=(storage,candidate)=>readGraphSourceDeliveryRetirementRecoveryCandidate({storage,scope,candidate});
const freeze=value=>{value.exact_body=raw(value.body);};

test('a wholly empty session discovers the retained original ending operation without changing either window or either nonce',()=>{
 const session=new Storage(),value=record(),storage=storageFor(value),before=[...storage.rows],page=enumerate(storage);
 assert.equal(session.rows.size,0);assert.equal(page.items.length,1);assert.equal(page.next_offset,null);assert.equal(page.problem_count,0);
 const candidate=page.items[0];assert.deepEqual(Object.keys(candidate).sort(),['key','raw','scope','window_id','intent_id','producer_id','kind','label','summary','local_completion'].sort());
 assert.equal(candidate.kind,'retirement');assert.equal(candidate.local_completion,false);assert.equal(candidate.window_id,value.window_id);assert.equal(candidate.intent_id,value.intent_id);assert.equal(candidate.producer_id,value.producer_id);
 assert.ok(candidate.summary.includes('向服务器核对'));assert.ok(!candidate.summary.includes(H));assert.ok(!candidate.label.includes(value.intent_id));
 const selected=read(storage,candidate);assert.deepEqual(selected,{record:value,raw:raw(value),key:keyFor(value),kind:'retirement'});
 assert.equal(selected.record.original_window_id,ID(8));assert.equal(selected.record.body.original.request_id,ID(6));assert.notEqual(selected.record.body.request_id,selected.record.body.original.request_id);
 assert.deepEqual([...storage.rows],before);assert.equal(session.rows.size,0);
});

test('other accounts, workspaces, adjacent prefixes and every old V124 key cause zero value reads',()=>{
 const others=[{...scope,actor_id:ID(20)},{...scope,workspace_id:ID(21)}],keys=others.map(value=>prefixFor(value)+'outbox:'+ID(7));
 keys.push(prefix.slice(0,-1)+'-other:outbox:'+ID(7),'ga-graph-delivery-v124:'+scope.workspace_id+':'+scope.actor_id+':outbox:'+ID(6));
 const storage=new Storage(keys.map(key=>[key,'private invalid data']));keys.forEach(key=>storage.forbidden.add(key));
 assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:0});assert.deepEqual(storage.reads,[]);
});

test('completed hints stay discoverable retirement requests and assert no server outcome or mutable Job state',()=>{
 const pending=record(),done=record(true),storage=storageFor(pending,done),before=[...storage.rows],page=enumerate(storage);
 assert.equal(page.items.length,2);assert.deepEqual(page.items.map(value=>value.local_completion),[false,true]);assert.ok(page.items[1].summary.includes('本机曾记录核对完成'));assert.ok(page.items[1].summary.includes('仍需向服务器核对'));
 for(const candidate of page.items){assert.equal(candidate.kind,'retirement');const selected=read(storage,candidate);
  assert.deepEqual(Object.keys(selected.record).sort(),['schema','scope','window_id','intent_id','producer_id','original_window_id','body','exact_body','command_hash'].sort());
  for(const field of ['receipt','retirement','retired','saved','server_verified','job','admission','sequence'])assert.equal(Object.hasOwn(selected.record,field),false);}
 assert.deepEqual([...storage.rows],before);
});

test('meaningful ending reasons retain whitespace and Unicode; the original retry may still contain only whitespace',()=>{
 for(const reason of ['  核对后结束\n  ','温'.repeat(1000),'🌲'.repeat(1000),'e\u0301 与 é 的原字节']){
  const value=record();value.body.reason=reason;value.body.original.reason=' \t\n\u0085 ';freeze(value);const storage=storageFor(value),page=enumerate(storage);
  assert.equal(page.problem_count,0);assert.equal(page.items.length,1);const selected=read(storage,page.items[0]);assert.equal(selected.record.body.reason,reason);assert.equal(selected.record.body.original.reason,value.body.original.reason);assert.equal(selected.record.exact_body,value.exact_body);assert.ok(page.items[0].summary.length<350);}
});

test('the new meaningful-text check agrees with Python whitespace while never trimming the stored body',()=>{
 for(const reason of [' \t\n ','\u001c\u001d\u001e\u001f','\u0085','\u00a0\u1680\u2000\u2028\u202f\u205f\u3000']){
  const value=record();value.body.reason=reason;freeze(value);const storage=storageFor(value),before=[...storage.rows];assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});assert.deepEqual([...storage.rows],before);}
 // Python str.strip() does not remove U+FEFF; JS trim() would. The backend
 // contract, rather than display trimming, determines whether it is retained.
 const value=record();value.body.reason='\ufeff';freeze(value);const storage=storageFor(value),candidate=enumerate(storage).items[0];assert.ok(candidate);assert.equal(read(storage,candidate).record.body.reason,'\ufeff');
});

test('canonical lowercase zero and version-seven UUIDs survive with distinct original and ending nonces',()=>{
 const zero='00000000-0000-0000-0000-000000000000',v7='018f099f-7639-7c65-b456-321821dcba73',selectedScope={actor_id:v7,workspace_id:zero};
 const value=record();Object.assign(value,{scope:copy(selectedScope),window_id:zero,original_window_id:v7,intent_id:v7,producer_id:zero});value.body.request_id=v7;value.body.original.request_id=zero;freeze(value);
 const storage=storageFor(value),page=enumerateGraphSourceDeliveryRetirementRecovery({storage,scope:selectedScope});assert.equal(page.items.length,1);
 assert.equal(readGraphSourceDeliveryRetirementRecoveryCandidate({storage,scope:selectedScope,candidate:page.items[0]}).record.original_window_id,v7);
 const accesses=storage.lengthReads;assert.throws(()=>enumerateGraphSourceDeliveryRetirementRecovery({storage,scope:{...selectedScope,actor_id:v7.toUpperCase()}}),{code:ERR+'RECORD'});assert.equal(storage.lengthReads,accesses);
});

test('the original version is a positive safe integer and is never replaced by a current version',()=>{
 for(const version of [1,2147483647,Number.MAX_SAFE_INTEGER]){const value=record();value.body.original.job_version=version;freeze(value);const storage=storageFor(value),candidate=enumerate(storage).items[0];assert.ok(candidate);assert.equal(read(storage,candidate).record.body.original.job_version,version);}
});

test('the same original nonce may be retained for distinct intents and independent ending requests',()=>{
 const a=record(),b=record(false,ID(30));b.intent_id=ID(31);b.original_window_id=ID(32);const storage=storageFor(a,b),page=enumerate(storage);
 assert.equal(page.items.length,2);assert.equal(read(storage,page.items[0]).record.body.original.request_id,read(storage,page.items[1]).record.body.original.request_id);assert.notEqual(page.items[0].intent_id,page.items[1].intent_id);
});

test('changed raw, original window, original body or deletion refuses selection rather than substituting another operation',()=>{
 const value=record(),storage=storageFor(value),key=keyFor(value),candidate=enumerate(storage).items[0];
 storage.rows.set(key,JSON.stringify(value,null,2));assert.throws(()=>read(storage,candidate),{code:ERR+'CHANGED'});const next=enumerate(storage).items[0];assert.equal(read(storage,next).raw,storage.rows.get(key));
 const changed=copy(value);changed.original_window_id=ID(40);storage.rows.set(key,raw(changed));assert.throws(()=>read(storage,next),{code:ERR+'CHANGED'});
 const another=enumerate(storage).items[0];changed.body.original.reason='另一份原说明';freeze(changed);storage.rows.set(key,raw(changed));assert.throws(()=>read(storage,another),{code:ERR+'CHANGED'});
 storage.rows.delete(key);assert.throws(()=>read(storage,candidate),{code:ERR+'CHANGED'});
});

test('candidate metadata cannot redirect the operation to another account, intent, producer or window',()=>{
 const value=record(),storage=storageFor(value),candidate=enumerate(storage).items[0],beforeReads=storage.reads.length;
 for(const patch of [{scope:{...scope,actor_id:ID(41)}},{key:prefixFor({...scope,workspace_id:ID(42)})+'outbox:'+value.body.request_id}])assert.throws(()=>read(storage,{...candidate,...patch}),{code:ERR+'RECORD'});
 assert.equal(storage.reads.length,beforeReads);
 for(const patch of [{intent_id:ID(43)},{producer_id:ID(44)},{window_id:ID(45)},{kind:'request'},{local_completion:true},{label:'另一操作'},{summary:'伪造服务器已结束'},{extra:true}])assert.throws(()=>read(storage,{...candidate,...patch}),{code:ERR+'RECORD'});
});

test('bounded scanning can return empty pages and still continue through foreign and damaged keys',()=>{
 const foreign={...scope,actor_id:ID(50)},entries=[];for(let index=0;index<7;index++)entries.push([prefixFor(foreign)+'outbox:'+ID(100+index),'foreign private text']);
 const value=record();entries.push([prefix+'bad','broken'],[keyFor(value),raw(value)]);const storage=new Storage(entries);entries.slice(0,7).forEach(([key])=>storage.forbidden.add(key));
 assert.deepEqual(enumerate(storage,{maxScan:3}),{items:[],next_offset:3,problem_count:0});assert.equal(storage.keys.length,3);assert.equal(storage.reads.length,0);
 assert.deepEqual(enumerate(storage,{offset:3,maxScan:3}),{items:[],next_offset:6,problem_count:0});assert.deepEqual(enumerate(storage,{offset:6,maxScan:2}),{items:[],next_offset:8,problem_count:1});
 const page=enumerate(storage,{offset:8,maxScan:2});assert.equal(page.items.length,1);assert.equal(page.next_offset,null);
});

test('item limits and physical scan limits remain independent without mutating copied-window records',()=>{
 const values=Array.from({length:5},(_,index)=>record(false,ID(60+index)));values[1].window_id=ID(70);values[1].original_window_id=ID(71);
 const storage=storageFor(...values),before=[...storage.rows],found=[];let offset=0;
 do{const page=enumerate(storage,{offset,limit:2});found.push(...page.items.map(value=>value.key));offset=page.next_offset;}while(offset!==null);
 assert.deepEqual(found,values.map(keyFor));assert.equal(storage.keys.length,5);assert.deepEqual([...storage.rows],before);
 assert.equal(enumerate(storage,{maxScan:1}).next_offset,1);assert.deepEqual(enumerate(storage,{offset:99}),{items:[],next_offset:null,problem_count:0});
 const candidate=enumerate(storage).items[0],selected=read(storage,candidate);selected.record.body.original.reason='只修改返回副本';selected.record.scope.actor_id=ID(72);assert.deepEqual([...storage.rows],before);assert.equal(read(storage,candidate).record.exact_body,values[0].exact_body);
});

test('the shared 256K cap accepts an exact-boundary record and preserves oversized or malformed records',()=>{
 const value=record(),encoded=raw(value),atLimit=encoded+' '.repeat(MAX-encoded.length),key=keyFor(value),storage=new Storage([[key,atLimit]]),page=enumerate(storage);
 assert.equal(page.items.length,1);assert.equal(read(storage,page.items[0]).raw.length,MAX);
 const oversized=atLimit+' ';storage.rows.set(key,oversized);assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});assert.equal(storage.rows.get(key),oversized);assert.throws(()=>read(storage,{...page.items[0],raw:oversized}),{code:ERR+'RECORD'});
 const malformed='{"private":"unfinished';storage.rows.set(key,malformed);assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});
 assert.throws(()=>read(storage,{...page.items[0],raw:malformed}),error=>error.code===ERR+'RECORD'&&!error.message.includes('private'));
});

test('storage exceptions propagate generically rather than becoming an empty successful discovery',()=>{
 for(const field of ['failLength','failKey','failGet']){const storage=storageFor(record());storage[field]=true;assert.throws(()=>enumerate(storage),error=>error.code===ERR+'STORAGE'&&!error.message.includes('private'));}
 const storage=storageFor(record()),candidate=enumerate(storage).items[0];storage.failGet=true;assert.throws(()=>read(storage,candidate),{code:ERR+'STORAGE'});
 const directory=storageFor(record());directory.key=()=>3;assert.throws(()=>enumerate(directory),{code:ERR+'STORAGE'});assert.equal(directory.reads.length,0);
});

test('invalid scopes and pagination are rejected before reading any storage',()=>{
 const storage=new Storage();for(const options of [{offset:-1},{offset:0.5},{offset:false},{offset:Number.MAX_SAFE_INTEGER+1},{limit:0},{limit:51},{limit:'1'},{maxScan:0},{maxScan:201},{maxScan:Infinity}])assert.throws(()=>enumerate(storage,options),{code:ERR+'RECORD'});
 for(const selectedScope of [{...scope,extra:true},{...scope,actor_id:5},{...scope,workspace_id:'unknown'},null])assert.throws(()=>enumerateGraphSourceDeliveryRetirementRecovery({storage,scope:selectedScope}),{code:ERR+'RECORD'});
 assert.equal(storage.lengthReads,0);assert.deepEqual(storage.keys,[]);assert.deepEqual(storage.reads,[]);
});

test('closed shape, nonce independence, exact body and unsafe original command values fail without rewriting any bytes',async t=>{
 const cases=[
  ['extra record field',value=>{value.retirement={outcome:'retired'};}],
  ['missing original window',value=>{delete value.original_window_id;}],
  ['invalid original window',value=>{value.original_window_id='old-window';}],
  ['extra scope field',value=>{value.scope.extra=true;}],
  ['different workspace',value=>{value.scope.workspace_id=ID(80);}],
  ['old record schema',value=>{value.schema='graph-source-delivery-outbox-v124';}],
  ['extra ending field',value=>{value.body.producer_id=ID(5);freeze(value);}],
  ['wrong ending schema',value=>{value.body.schema='other';freeze(value);}],
  ['same original and ending nonce',value=>{value.body.original.request_id=value.body.request_id;freeze(value);}],
  ['uppercase ending nonce',value=>{value.body.request_id='018F099F-7639-7C65-B456-321821DCBA73';freeze(value);}],
  ['uppercase original nonce',value=>{value.body.original.request_id='018F099F-7639-7C65-B456-321821DCBA73';freeze(value);}],
  ['extra original field',value=>{value.body.original.schema='new-retry';freeze(value);}],
  ['missing original reason',value=>{delete value.body.original.reason;freeze(value);}],
  ['missing original object',value=>{delete value.body.original;freeze(value);}],
  ['null original object',value=>{value.body.original=null;freeze(value);}],
  ['empty original reason',value=>{value.body.original.reason='';freeze(value);}],
  ['empty ending reason',value=>{value.body.reason='';freeze(value);}],
  ['nonstring ending reason',value=>{value.body.reason=5;freeze(value);}],
  ['nonstring original reason',value=>{value.body.original.reason=true;freeze(value);}],
  ['overlong original Unicode',value=>{value.body.original.reason='🌲'.repeat(1001);freeze(value);}],
  ['overlong ending Unicode',value=>{value.body.reason='🌲'.repeat(1001);freeze(value);}],
  ['original NUL',value=>{value.body.original.reason='原\u0000因';freeze(value);}],
  ['ending NUL',value=>{value.body.reason='原\u0000因';freeze(value);}],
  ['original unpaired high surrogate',value=>{value.body.original.reason='\ud800';freeze(value);}],
  ['ending unpaired low surrogate',value=>{value.body.reason='\udc00';freeze(value);}],
  ['fraction original version',value=>{value.body.original.job_version=1.5;freeze(value);}],
  ['boolean original version',value=>{value.body.original.job_version=true;freeze(value);}],
  ['string original version',value=>{value.body.original.job_version='3';freeze(value);}],
  ['zero original version',value=>{value.body.original.job_version=0;freeze(value);}],
  ['unsafe original version',value=>{value.body.original.job_version=Number.MAX_SAFE_INTEGER+1;freeze(value);}],
  ['null original version',value=>{value.body.original.job_version=null;freeze(value);}],
  ['invalid intent',value=>{value.intent_id=4;}],
  ['invalid producer',value=>{value.producer_id=false;}],
  ['changed ending body',value=>{value.body.reason='事后改写';}],
  ['changed original body',value=>{value.body.original.reason='事后改写';}],
  ['noncanonical exact body',value=>{value.exact_body=JSON.stringify(value.body,null,2);}],
  ['invalid command hash',value=>{value.command_hash='verified';}],
 ];
 for(const [name,mutate] of cases)await t.test(name,()=>{const value=record();mutate(value);const key=prefix+'outbox:'+ID(7),stored=raw(value),storage=new Storage([[key,stored]]);assert.deepEqual(enumerate(storage),{items:[],next_offset:null,problem_count:1});assert.equal(storage.rows.get(key),stored);});
 await t.test('different ending nonce key',()=>{const value=record(),storage=new Storage([[prefix+'outbox:'+ID(99),raw(value)]]);assert.equal(enumerate(storage).problem_count,1);assert.equal(storage.rows.size,1);});
 await t.test('completed schema with original outbox key',()=>{const value=record(true),storage=new Storage([[prefix+'outbox:'+value.body.request_id,raw(value)]]);assert.equal(enumerate(storage).problem_count,1);});
});

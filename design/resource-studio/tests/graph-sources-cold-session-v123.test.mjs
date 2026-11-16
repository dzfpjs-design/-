import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {GraphSourcesControllerV122,graphSourceCommandHash,graphSourceRetirementHash} from '../src/graph-sources-controller-v122.mjs';
import {GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH} from '../src/graph-sources-recovery-v123.mjs';

const ID=n=>'83000000-0000-4000-8000-'+String(n).padStart(12,'0'),H='c'.repeat(64),scope={actor_id:ID(1),workspace_id:ID(2)},copy=x=>JSON.parse(JSON.stringify(x));
class Storage{
 constructor(){this.rows=new Map();this.fail=false;}get length(){return this.rows.size;}key(i){return [...this.rows.keys()][i]??null;}getItem(k){return this.rows.get(k)??null;}
 setItem(k,v){if(this.fail)throw Error('synthetic quota');this.rows.set(k,String(v));}removeItem(k){this.rows.delete(k);}
}
function tab(){const s=new Storage();s.setItem('ga-setup-csrf-user',scope.actor_id);s.setItem('ga-setup-csrf-workspace',scope.workspace_id);s.setItem('ga-setup-csrf','synthetic-cold-token');return s;}
function fixture(){
 const storage=new Storage(),calls=[],saved=new Map(),stops=new Map(),retired=new Map(),retireReceipts=new Map();
 const state={loseRegister:false,sourceUndelivered:false,loseRevoke:false,loseRetirement:false,retirementUndelivered:false,raceSaved:false,cookieActor:scope.actor_id,badReceipt:false,reorderReceipt:false};
 const source={id:ID(3),name:'登记来源',kind:'source',content_hash:H,release_version:'1.0.0'},extractor={id:ID(4),name:'处理能力',kind:'tool',content_hash:H,release_version:'1.0.0'};
 const contract=body=>({schema:'graph-event-observation-source-contract-v1',adapter_id:body.adapter_id,event_kind:body.adapter_id==='processed-observation-v1'?'observation.data':'perception.anomaly',correlation_kind:'entity_id',source_id:body.source_id,source_hash:body.source_hash,source_release_version:'1.0.0',extractor_id:body.extractor_id,extractor_hash:body.extractor_hash,extractor_release_version:'1.0.0',result_schema:{type:'object',properties:{value:{type:'number',title:'处理数值'}},required:['value'],additionalProperties:false},output_schema:{type:'object',properties:{result:{type:'object'}}},output_schema_hash:H,contract_hash:H});
 const producer=async body=>({id:ID(5),label:body.label,status:'active',version:1,contract_hash:H,contract:contract(body),registration:{request_id:body.request_id,actor_id:scope.actor_id,command_hash:await graphSourceCommandHash(scope,'register',body,webcrypto),command:copy(body)},execution_granted:false,physical_state_verified:false});
 const catalog=row=>({...copy(row),source_name:source.name,extractor_name:extractor.name,current_availability:{available:row.status==='active',reason_code:row.status==='active'?'available':'registration_stopped',label:'当前发布状态需单独核对'},receipt_verified:false});
 const base='/setup/graph-event-producers';
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);
  if(path==='/me')return {user_id:state.cookieActor,workspace_id:scope.workspace_id,role:'admin'};
  if(path===base+'/catalog/capabilities?kind=source&limit=20')return {items:[source],next_after:null,can_register:true};
  if(path===base+'/catalog/capabilities?kind=extractor&limit=20')return {items:[extractor],next_after:null,can_register:true};
  if(path===base+'/catalog/registrations?limit=20')return {items:[...saved.values()].map(catalog),next_after:null,can_register:true};
  if(path.startsWith(base+'/catalog/registrations/'))return catalog([...saved.values()].find(row=>row.id===path.split('/').at(-1)));
  if(path===base+'/preview'){const body=JSON.parse(options.body);return {contract:contract(body),contract_hash:H,command_hash:await graphSourceCommandHash(scope,'register',body,webcrypto),execution_granted:false,meaning:'只登记结果来源'};}
  if(path===base){const body=JSON.parse(options.body);if(retired.has('register:'+body.request_id))throw Object.assign(Error('retired'),{status:409});if(state.sourceUndelivered)throw Error('synthetic source not delivered');const row=await producer(body);saved.set(body.request_id,row);if(state.loseRegister)throw Error('synthetic lost saved source reply');return {producer:copy(row),created:true};}
  if(path===base+'/revocations'){
   const body=JSON.parse(options.body);if(retired.has('revoke:'+body.request_id))throw Object.assign(Error('retired'),{status:409});const row=[...saved.values()].find(item=>item.id===body.producer_id);row.status='revoked';row.version++;
   const receipt={id:ID(6),request_id:body.request_id,actor_id:scope.actor_id,producer_id:body.producer_id,command_hash:await graphSourceCommandHash(scope,'revoke',body,webcrypto),command:copy(body),result:{status:'revoked',version:body.producer_version+1,contract_hash:H}};stops.set(body.request_id,receipt);if(state.loseRevoke)throw Error('synthetic lost saved stop reply');return {receipt:copy(receipt),replayed:false};
  }
  if(path.startsWith(base+'/command-requests/')){
   const parts=path.split('/'),kind=parts.at(-2),request_id=parts.at(-1),row=copy((kind==='register'?saved:stops).get(request_id)||null),r=copy(retired.get(kind+':'+request_id)||null);
   if(row&&kind==='revoke'&&state.reorderReceipt)row.command=Object.fromEntries(Object.entries(row.command).reverse());
   return {schema:'graph-event-source-request-state-v1',kind,request_id,outcome:row?'saved':r?'retired':'not_found',command_hash:row?(kind==='register'?row.registration.command_hash:row.command_hash):r?.original_command_hash||null,saved:row,retirement:r};
  }
  if(path===base+'/command-retirements'){
   const body=JSON.parse(options.body),nonce=body.original.request_id,key=body.kind+':'+nonce;
   const stored=[...storage.rows.values()].map(raw=>{try{return JSON.parse(raw);}catch{return null;}}).find(row=>row?.schema==='graph-source-retirement-outbox-v123'&&row.body.request_id===body.request_id);assert.ok(stored);assert.equal(stored.exact_body,options.body);
   if(state.raceSaved){saved.set(nonce,await producer(body.original));throw Object.assign(Error('source saved first'),{status:409});}
   if((body.kind==='register'?saved:stops).has(nonce))throw Object.assign(Error('saved'),{status:409});if(state.retirementUndelivered)throw Error('synthetic retirement not delivered');
   const original_command_hash=await graphSourceCommandHash(scope,body.kind,body.original,webcrypto),command_hash=await graphSourceRetirementHash(scope,body,webcrypto);
   const receipt={id:ID(7),request_id:body.request_id,actor_id:scope.actor_id,kind:body.kind,original_request_id:nonce,original_command_hash,command_hash,command:copy(body),result:{outcome:'retired',kind:body.kind,original_request_id:nonce,original_command_hash}};
   retired.set(key,receipt);retireReceipts.set(body.request_id,receipt);if(state.loseRetirement)throw Error('synthetic lost retirement reply');return {retirement:copy(receipt),replayed:false};
  }
  if(path.startsWith(base+'/command-retirements/requests/')){const receipt=copy(retireReceipts.get(path.split('/').at(-1))||null);if(receipt&&state.badReceipt)receipt.actor_id=ID(98);return {found:!!receipt,retirement:receipt};}
  throw Error('Unexpected synthetic path '+path);
 };
 const make=(session=tab(),selectedScope=scope)=>new GraphSourcesControllerV122({api,scope:selectedScope,storage,session,crypto:webcrypto});
 return {storage,calls,state,saved,stops,retired,retireReceipts,source,extractor,api,make,base};
}
async function prepared(h){const c=h.make();await c.open();c.chooseCapability('source',h.source.id);c.chooseCapability('extractor',h.extractor.id);c.edit({label:'可恢复的登记原稿'});await c.previewRegistration();return c;}
function candidate(c,kind,schema){const value=c.recovery.items.find(row=>row.kind===kind&&(!schema||JSON.parse(row.raw).schema===schema));assert.ok(value,'scoped local candidate '+kind);return value;}
const sourcePosts=h=>h.calls.filter(call=>call.path===h.base&&call.method==='POST'),retirePosts=h=>h.calls.filter(call=>call.path===h.base+'/command-retirements'&&call.method==='POST');

test('empty fresh session discovers original source outbox and explicitly recovers actual saved state without a second POST',async()=>{
 const h=fixture(),a=await prepared(h);h.state.loseRegister=true;await assert.rejects(a.register());const original=copy(a.pending),oldSessionPointer=a.session.getItem(a.prefix+'pending');
 const b=h.make();await b.open();assert.equal(b.pending,null);assert.equal(b.form.label,'');assert.notEqual(b.windowID,original.window_id);
 await b.restoreRecovery(candidate(b,'request','graph-source-outbox-v122'));assert.equal(b.pending,null);assert.equal(sourcePosts(h).length,1);assert.equal(sourcePosts(h)[0].body,original.exact_body);
 assert.equal(a.session.getItem(a.prefix+'pending'),oldSessionPointer);assert.equal(a.pending.exact_body,original.exact_body);assert.equal(JSON.parse(h.storage.getItem(a.prefix+'completed:'+original.body.request_id)).window_id,original.window_id);
});
test('explicit draft recovery saves the new window current draft and never overwrites the original live owner slots',async()=>{
 const h=fixture(),a=await prepared(h);a.edit({label:'原窗口的完整人稿'});const old=[...h.storage.rows].filter(([key])=>key.includes(a.formOwner));
 const b=h.make();await b.open();b.edit({label:'新窗口先写的人稿'});const owner=b.formOwner;await b.discoverRecovery();const selected=b.recovery.items.find(row=>row.kind==='draft'&&row.label==='原窗口的完整人稿');assert.ok(selected);
 await b.restoreRecovery(selected);assert.equal(b.form.label,'原窗口的完整人稿');assert.notEqual(b.formOwner,owner);assert.equal(a.form.label,'原窗口的完整人稿');
 for(const [key,value] of old)assert.equal(h.storage.getItem(key),value);assert.ok([...h.storage.rows.values()].some(raw=>{try{return JSON.parse(raw).form?.label==='新窗口先写的人稿';}catch{return false;}}));
});
test('storage failure blocks recovery replacement, preserving original candidate bytes and current human draft',async()=>{
 const h=fixture(),a=await prepared(h);a.edit({label:'保留的旧稿'});const b=h.make();await b.open();b.edit({label:'当前尚需保留的稿'});await b.discoverRecovery();const selected=b.recovery.items.find(row=>row.kind==='draft'&&row.label==='保留的旧稿');assert.ok(selected);
 h.storage.fail=true;await assert.rejects(b.restoreRecovery(selected));assert.equal(b.form.label,'当前尚需保留的稿');assert.equal(h.storage.getItem(selected.key),selected.raw);assert.equal(sourcePosts(h).length,0);
 h.storage.fail=false;assert.equal(b.saveLocal(),true);await b.restoreRecovery(selected);assert.equal(b.form.label,'保留的旧稿');
});
test('changed original bytes, wrong actor and an unrelated existing pending cannot be silently replaced',async()=>{
 const h=fixture(),a=await prepared(h);h.state.sourceUndelivered=true;await assert.rejects(a.register());const b=h.make();await b.open();const selected=candidate(b,'request','graph-source-outbox-v122');
 const raw=h.storage.getItem(selected.key);h.storage.setItem(selected.key,raw+' ');await assert.rejects(b.restoreRecovery(selected));assert.equal(b.pending,null);h.storage.setItem(selected.key,raw);
 h.state.cookieActor=ID(99);await assert.rejects(b.restoreRecovery(selected));assert.equal(b.pending,null);h.state.cookieActor=scope.actor_id;
 const foreign=h.make(tab(),{actor_id:scope.actor_id,workspace_id:ID(97)});await assert.rejects(foreign.restoreRecovery(selected));assert.equal(foreign.pending,null);
 const other=await prepared(h);await assert.rejects(other.register());const before=other.pending.exact_body;await assert.rejects(other.restoreRecovery(selected));assert.equal(other.pending.exact_body,before);
});
test('a new retirement nonce is immutable, its lost reply recovers from another empty session, and late original send stays retired',async()=>{
 const h=fixture(),a=await prepared(h);h.state.sourceUndelivered=true;await assert.rejects(a.register());const original=copy(a.pending);a.dispose();
 const b=h.make();await b.open();await b.restoreRecovery(candidate(b,'request','graph-source-outbox-v122'));assert.equal(b.pendingNotFound,true);b.setRetirementReason('保留原稿，结束这次未完成操作');h.state.loseRetirement=true;await assert.rejects(b.retireOriginal());const stopped=copy(b.retirement);assert.notEqual(stopped.body.request_id,original.body.request_id);assert.deepEqual(stopped.body.original,original.body);
 b.dispose();const c=h.make();await c.open();await c.restoreRecovery(candidate(c,'retirement','graph-source-retirement-outbox-v123'));assert.equal(c.pending,null);assert.equal(c.retirement,null);assert.equal(sourcePosts(h).length,1);assert.equal(retirePosts(h).length,1);
 const marker=JSON.parse(h.storage.getItem(c.prefix+'retired:'+original.body.request_id));assert.equal(marker.window_id,original.window_id);assert.equal(marker.exact_body,original.exact_body);
 h.state.sourceUndelivered=false;await assert.rejects(h.api(h.base,{method:'POST',body:original.exact_body,headers:{'X-Workspace-ID':scope.workspace_id}}),error=>error.status===409);assert.equal(h.saved.size,0);
});
test('retirement not delivered requires negative cancellation and source lookup before retrying the same cancellation body',async()=>{
 const h=fixture(),a=await prepared(h);h.state.sourceUndelivered=true;await assert.rejects(a.register());await a.recover();a.setRetirementReason('结束此次未完成操作');h.state.retirementUndelivered=true;await assert.rejects(a.retireOriginal());const raw=a.retirement.exact_body;
 await assert.rejects(a.resendRetirement());assert.equal(retirePosts(h).length,1);const b=h.make();await b.open();await b.restoreRecovery(candidate(b,'retirement','graph-source-retirement-outbox-v123'));assert.equal(b.retirementNotFound,true);await assert.rejects(b.resend());
 h.state.retirementUndelivered=false;await b.resendRetirement();assert.equal(retirePosts(h).length,2);assert.equal(retirePosts(h)[1].body,raw);assert.equal(b.pending,null);assert.equal(b.retirement,null);
});
test('source saved race is reported as saved and never as successful retirement',async()=>{
 const h=fixture(),a=await prepared(h);h.state.sourceUndelivered=true;await assert.rejects(a.register());await a.recover();a.setRetirementReason('停止尚未保存的操作');h.state.raceSaved=true;
 await a.retireOriginal();assert.equal(a.pending,null);assert.equal(a.retirement,null);assert.equal(h.retired.size,0);assert.equal(h.retireReceipts.size,0);assert.ok(a.notice.includes('原操作已经保存'));assert.ok(!a.notice.includes('未完成的登记操作已停止'));
 assert.equal([...h.storage.rows.keys()].filter(key=>key.includes(':retire-completed:')).length,0);assert.equal(retirePosts(h).length,1);assert.ok(h.calls.some(call=>call.path.includes('/command-requests/register/')));
});
test('retirement quota failure sends nothing and forged receipt actor never settles either immutable pending',async()=>{
 const h=fixture(),a=await prepared(h);h.state.sourceUndelivered=true;await assert.rejects(a.register());await a.recover();a.setRetirementReason('保留原稿');h.storage.fail=true;
 await assert.rejects(a.retireOriginal());assert.equal(retirePosts(h).length,0);assert.ok(a.pending);h.storage.fail=false;assert.equal(a.saveLocal(),true);h.state.loseRetirement=true;await assert.rejects(a.retireOriginal());h.state.badReceipt=true;
 const b=h.make();await b.open();await assert.rejects(b.restoreRecovery(candidate(b,'retirement','graph-source-retirement-outbox-v123')));assert.ok(b.pending);assert.ok(b.retirement);assert.equal(b.retirementNotFound,false);assert.equal(retirePosts(h).length,1);
});
test('cold saved revocation recovers original window receipt after producer state changed without repeating stop',async()=>{
 const h=fixture(),a=await prepared(h);await a.register();a.setReason('停止此结果来源');h.state.loseRevoke=true;await assert.rejects(a.revoke());const raw=a.pending.exact_body;
 const b=h.make();await b.open();const selected=b.recovery.items.find(row=>row.kind==='request'&&JSON.parse(row.raw).body?.schema==='graph-event-producer-revocation-v2');assert.ok(selected);await b.restoreRecovery(selected);
 assert.equal(b.pending,null);assert.equal(h.calls.filter(call=>call.path===h.base+'/revocations'&&call.method==='POST').length,1);assert.equal(JSON.parse(h.storage.getItem(b.prefix+'completed:'+JSON.parse(raw).request_id)).exact_body,raw);
});
test('canonical zero and version-seven UUID pins remain exact rather than being normalized or restricted to legacy variants',async()=>{
 const body={schema:'graph-event-producer-command-v1',request_id:'00000000-0000-0000-0000-000000000000',label:'精确保留标识的原操作',adapter_id:'processed-observation-v1',source_id:'018f0000-0000-7000-0000-000000000003',source_hash:H,extractor_id:ID(4),extractor_hash:H};
 const raw=JSON.stringify(body),hash=await graphSourceCommandHash(scope,'register',body,webcrypto);assert.match(hash,/^[a-f0-9]{64}$/);assert.equal(JSON.stringify(body),raw);
 await assert.rejects(graphSourceCommandHash(scope,'register',{...body,source_id:body.source_id.toUpperCase()},webcrypto));
});
test('cold completed revocation accepts only equivalent receipt object ordering and retains original marker bytes',async()=>{
 const h=fixture(),a=await prepared(h);await a.register();a.setReason('停止此结果来源');await a.revoke();const stop=[...h.stops.values()][0],key=a.prefix+'completed:'+stop.request_id,raw=h.storage.getItem(key);
 h.state.reorderReceipt=true;const b=h.make();await b.open();const selected=b.recovery.items.find(row=>row.kind==='request'&&row.local_completion&&JSON.parse(row.raw).kind==='revoke');assert.ok(selected);await b.restoreRecovery(selected);
 assert.equal(b.pending,null);assert.equal(h.storage.getItem(key),raw);assert.equal(h.calls.filter(call=>call.path===h.base+'/revocations'&&call.method==='POST').length,1);
});
test('form writer accepts the discoverable record-length boundary and blocks an oversized in-memory draft without replacing the last saved head',async()=>{
 const h=fixture(),c=await prepared(h),reasons={},generation=c.formGeneration+1;
 const size=value=>JSON.stringify({schema:'graph-source-form-slot-v122',scope:c.scope,window_id:c.windowID,owner:c.formOwner,generation,form:{...c.form,reasons:value}}).length;
 let n=100;while(GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH-size(reasons)>1100)reasons[ID(n++)]='核'.repeat(1000);
 if(GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH-size({...reasons,[ID(n)]:''})>1000)reasons[ID(n++)]='核'.repeat(500);
 const last=ID(n++),length=GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH-size({...reasons,[last]:''});assert.ok(length>=0&&length<=1000);reasons[last]='尾'.repeat(length);
 c.edit({reasons});assert.equal(c.localSaved,true);const head=c.session.getItem(c.prefix+'form-head'),savedKey=JSON.parse(head).key,saved=h.storage.getItem(savedKey);assert.equal(saved.length,GRAPH_SOURCE_RECOVERY_MAX_RECORD_LENGTH);
 await c.discoverRecovery();assert.ok(c.recovery.items.some(row=>row.key===savedKey));c.edit({reasons:{...reasons,[ID(n)]:''}});assert.equal(c.localSaved,false);assert.ok(c.error.includes('超过单份可恢复范围'));assert.equal(c.session.getItem(c.prefix+'form-head'),head);assert.equal(h.storage.getItem(savedKey),saved);assert.ok(Object.hasOwn(c.form.reasons,ID(n)));
 assert.equal(sourcePosts(h).length,0);c.edit({reasons:{[last]:'缩短后仍保留原稿'}});assert.equal(c.localSaved,true);
});

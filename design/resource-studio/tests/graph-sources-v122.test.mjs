import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {GraphSourcesControllerV122,graphSourceCommandHash,graphSourceResultFields,checkGraphSourceCapability,checkGraphSourceRegistration} from '../src/graph-sources-controller-v122.mjs';

const ID=n=>'81000000-0000-4000-8000-'+String(n).padStart(12,'0');
const scope={actor_id:ID(1),workspace_id:ID(2)},H='a'.repeat(64),copy=x=>JSON.parse(JSON.stringify(x));
class Storage{
 constructor(source){this.rows=new Map(source?.rows||[]);this.fail=false;this.limit=Infinity;}get length(){return this.rows.size;}key(index){return [...this.rows.keys()][index]??null;}
 getItem(key){return this.rows.get(key)??null;}setItem(key,value){const next=new Map(this.rows);next.set(key,String(value));const bytes=[...next].reduce((sum,[k,v])=>sum+2*(k.length+v.length),0);if(this.fail||bytes>this.limit)throw Error('synthetic storage quota');this.rows=next;}removeItem(key){this.rows.delete(key);}
}
function session(){const value=new Storage();value.setItem('ga-setup-csrf-user',scope.actor_id);value.setItem('ga-setup-csrf-workspace',scope.workspace_id);value.setItem('ga-setup-csrf','synthetic-token');return value;}
function contract(body){return {schema:'graph-event-observation-source-contract-v1',adapter_id:body.adapter_id,event_kind:body.adapter_id==='processed-observation-v1'?'observation.data':'perception.anomaly',correlation_kind:'entity_id',source_id:body.source_id,source_hash:body.source_hash,source_release_version:'1.0.0',extractor_id:body.extractor_id,extractor_hash:body.extractor_hash,extractor_release_version:'2.0.0',result_schema:{type:'object',properties:{temperature:{type:'number',title:'温度'}},required:['temperature'],additionalProperties:false},output_schema:{type:'object',properties:{result:{type:'object'},physical_state_verified:{const:false}}},output_schema_hash:H,contract_hash:H};}
function fixture({storage=new Storage(),tab=session()}={}){
 const calls=[],records=new Map(),revocations=new Map(),source={id:ID(3),name:'已发布采集来源',kind:'source',content_hash:H,release_version:'1.0.0'},extractor={id:ID(4),name:'已发布数值检查',kind:'tool',content_hash:H,release_version:'2.0.0'};
 const state={loseRegister:false,loseRevoke:false,noCommit:false,canRegister:true,emptyPage:false,wrongReceipt:false,failDetail:false,cookieActor:scope.actor_id,meError:null};
 const catalog=row=>({...copy(row),source_name:source.name,extractor_name:extractor.name,current_availability:{available:row.status==='active',reason_code:row.status==='active'?'available':'registration_stopped',label:row.status==='active'?'当前可核对':'已停止供应'},receipt_verified:false});
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body,headers:options.headers});
  assert.equal(options.headers['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers['X-CSRF-Token'],'synthetic-token');
  if(path==='/me'){if(state.meError)throw Object.assign(Error('synthetic private diagnosis'),{status:state.meError});return {user_id:state.cookieActor,workspace_id:scope.workspace_id,role:'admin'};}
  const url=new URL(path,'https://synthetic-sources.invalid'),base='/setup/graph-event-producers';
  if(url.pathname===base+'/catalog/capabilities')return {items:state.emptyPage&&!url.searchParams.has('after')?[]:[copy(url.searchParams.get('kind')==='source'?source:extractor)],next_after:state.emptyPage&&!url.searchParams.has('after')?ID(20):null,can_register:state.canRegister};
  if(url.pathname===base+'/catalog/registrations')return {items:[...records.values()].map(catalog),next_after:null,can_register:state.canRegister};
  if(url.pathname.startsWith(base+'/catalog/registrations/')){if(state.failDetail)throw Error('synthetic current-projection unavailable');return catalog([...records.values()].find(row=>row.id===url.pathname.split('/').at(-1)));}
  if(path===base+'/preview'){
   const body=JSON.parse(options.body);return {contract:contract(body),contract_hash:H,command_hash:await graphSourceCommandHash(scope,'register',body,webcrypto),execution_granted:false,meaning:'登记处理结果，不授予业务执行权限'};
  }
  if(path===base){
   const body=JSON.parse(options.body),outbox=[...storage.rows.values()].map(raw=>{try{return JSON.parse(raw);}catch{return null;}}).find(row=>row?.schema==='graph-source-outbox-v122'&&row.body.request_id===body.request_id);
   assert.ok(outbox,'immutable exact body exists before actual transport');assert.equal(outbox.exact_body,options.body);
   const command_hash=await graphSourceCommandHash(scope,'register',body,webcrypto);
   const producer={id:ID(5),label:body.label,status:'active',version:1,contract_hash:H,contract:contract(body),registration:{request_id:body.request_id,actor_id:scope.actor_id,command_hash,command:copy(body)},execution_granted:false,physical_state_verified:false};
   if(!state.noCommit)records.set(body.request_id,producer);if(state.loseRegister)throw Error('synthetic lost response');return {producer:copy(producer),created:true};
  }
  if(path.startsWith(base+'/requests/')){const producer=records.get(path.split('/').at(-1));return {found:!!producer,producer:producer?copy(producer):null};}
  if(path.startsWith(base+'/command-requests/')){
   const parts=path.split('/'),kind=parts.at(-2),request_id=parts.at(-1),saved=copy((kind==='register'?records:revocations).get(request_id)||null);
   if(saved&&state.wrongReceipt&&kind==='revoke')saved.command.reason='替换理由';
   return {schema:'graph-event-source-request-state-v1',kind,request_id,outcome:saved?'saved':'not_found',command_hash:saved?(kind==='register'?saved.registration.command_hash:saved.command_hash):null,saved,retirement:null};
  }
  if(path===base+'/revocations'){
   const body=JSON.parse(options.body),row=[...records.values()].find(row=>row.id===body.producer_id);
   const receipt={id:ID(6),request_id:body.request_id,actor_id:scope.actor_id,producer_id:body.producer_id,command_hash:await graphSourceCommandHash(scope,'revoke',body,webcrypto),command:copy(body),result:{status:'revoked',version:body.producer_version+1,contract_hash:body.contract_hash}};
   row.status='revoked';row.version++;revocations.set(body.request_id,receipt);if(state.loseRevoke)throw Error('synthetic lost response');return {receipt:copy(receipt),replayed:false};
  }
  if(path.startsWith(base+'/revocations/requests/')){const receipt=copy(revocations.get(path.split('/').at(-1))||null);if(receipt&&state.wrongReceipt)receipt.command.reason='替换理由';return {found:!!receipt,receipt};}
  throw Error('Unexpected synthetic transport '+path);
 };
 const make=(selectedTab=tab,selectedScope=scope)=>new GraphSourcesControllerV122({api,scope:selectedScope,storage,session:selectedTab,crypto:webcrypto});
 return {storage,tab,calls,state,records,revocations,source,extractor,api,make};
}
async function prepared(h){const c=h.make();await c.open();c.chooseCapability('source',h.source.id);c.chooseCapability('extractor',h.extractor.id);c.edit({label:'供流程选择的温度结果'});await c.previewRegistration();return c;}
const registrationPosts=h=>h.calls.filter(call=>call.method==='POST'&&call.path==='/setup/graph-event-producers');

test('preview is explicit, exact-body bound and invalidated by editing before registration',async()=>{
 const h=fixture(),c=await prepared(h);assert.ok(c.preview);assert.equal(registrationPosts(h).length,0);const original=c.preview.exact_body;
 c.edit({label:'明确的新名称'});assert.equal(c.preview,null);await assert.rejects(c.register());assert.equal(registrationPosts(h).length,0);
 await c.previewRegistration();await c.register();assert.equal(registrationPosts(h).length,1);assert.notEqual(registrationPosts(h)[0].body,original);assert.equal(c.pending,null);
});
test('lost registration response remount performs lookup only and retains a later human draft after current health changes',async()=>{
 const h=fixture(),c=await prepared(h);h.state.loseRegister=true;await assert.rejects(c.register());const raw=c.pending.exact_body;c.edit({label:'保留的另一项草稿'});c.dispose();
 const saved=[...h.records.values()][0];saved.status='revoked';saved.version=2;h.state.canRegister=false;
 const restored=h.make();await restored.open();assert.equal(restored.pending.exact_body,raw);assert.equal(registrationPosts(h).length,1);
 await restored.recover();assert.equal(restored.pending,null);assert.equal(restored.form.label,'保留的另一项草稿');assert.equal(registrationPosts(h).length,1);
});
test('unknown before commit requires explicit negative lookup, then retries the original body despite draft edits',async()=>{
 const h=fixture(),c=await prepared(h);h.state.noCommit=true;h.state.loseRegister=true;await assert.rejects(c.register());const raw=c.pending.exact_body;
 await assert.rejects(c.resend());assert.equal(registrationPosts(h).length,1);await c.recover();assert.equal(c.pendingNotFound,true);
 c.edit({label:'不得改写原请求的草稿',adapter_id:'anomaly-candidate-v1'});h.state.noCommit=false;h.state.loseRegister=false;await c.resend();
 assert.equal(registrationPosts(h).length,2);assert.equal(registrationPosts(h)[1].body,raw);assert.equal(c.form.adapter_id,'anomaly-candidate-v1');
});
test('storage quota or readback failure blocks transport before registration',async()=>{
 const h=fixture(),c=await prepared(h);h.storage.fail=true;c.edit({label:'存储未恢复前保留的人稿'});assert.equal(c.localSaved,false);await assert.rejects(c.register());assert.equal(registrationPosts(h).length,0);
 h.storage.fail=false;assert.equal(c.saveLocal(),true);await c.previewRegistration();const get=h.storage.getItem.bind(h.storage);h.storage.getItem=key=>key.includes(':outbox:')?null:get(key);
 await assert.rejects(c.register());assert.equal(registrationPosts(h).length,0);
});
test('cloned whole sessionStorage shares immutable history but never overwrites the other tab draft or original pending',async()=>{
 const h=fixture(),a=await prepared(h);h.state.loseRegister=true;await assert.rejects(a.register());const pending=a.pending.exact_body,originalHead=h.tab.getItem(a.prefix+'form-head');
 const entry=a.prefix+'form:entry:'+a.formOwner,originalRecord=h.storage.getItem(entry);
 const cloneTab=new Storage(h.tab),b=h.make(cloneTab);b.edit({label:'复制标签页的独立改稿'});a.edit({label:'原标签页的独立改稿'});
 assert.notEqual(h.tab.getItem(a.prefix+'form-head'),cloneTab.getItem(a.prefix+'form-head'));assert.equal(h.storage.getItem(entry),originalRecord);assert.notEqual(cloneTab.getItem(a.prefix+'form-head'),originalHead);
 assert.equal(a.pending.exact_body,pending);assert.equal(b.pending.exact_body,pending);assert.equal(h.make(cloneTab).form.label,'复制标签页的独立改稿');assert.equal(h.make().form.label,'原标签页的独立改稿');
 await a.recover();const producer=[...h.records.values()][0];producer.status='revoked';producer.version=2;await b.recover();assert.equal(a.pending,null);assert.equal(b.pending,null);assert.equal(registrationPosts(h).length,1);
});
test('one thousand actual reason edits use bounded owner slots and recover from quota without allocating a new history each keypress',async()=>{
 const h=fixture(),c=await prepared(h);await c.register();h.storage.limit=24000;
 const oldestKey=c.prefix+'form:entry:'+c.formOwner,oldest=h.storage.getItem(oldestKey),outboxKey=[...h.storage.rows.keys()].find(key=>key.includes(':outbox:')),outbox=h.storage.getItem(outboxKey);
 for(let i=1;i<=1000;i++){c.setReason('核'.repeat(i));assert.equal(c.localSaved,true,'keystroke '+i+' remains saved');}
 assert.equal([...h.storage.rows.keys()].filter(key=>key.startsWith(c.prefix+'form:slot:'+c.formOwner+':')).length,2);
 assert.equal(h.storage.getItem(oldestKey),oldest);assert.equal(h.storage.getItem(outboxKey),outbox);assert.ok([...h.storage.rows].reduce((n,[key,value])=>n+2*(key.length+value.length),0)<24000);
 const count=h.storage.length;h.storage.fail=true;c.setReason('未'.repeat(1000));assert.equal(c.localSaved,false);assert.equal(c.form.reasons[ID(5)],'未'.repeat(1000));
 h.storage.fail=false;assert.equal(c.saveLocal(),true);assert.equal(h.storage.length,count);assert.equal(h.make().form.reasons[ID(5)],'未'.repeat(1000));
});
test('a not-yet-mounted cloned tab restores its own complete snapshot after the original cycles both local slots repeatedly',async()=>{
 const h=fixture(),a=await prepared(h),cloneTab=new Storage(h.tab),head=cloneTab.getItem(a.prefix+'form-head'),prior=JSON.parse(head);
 for(let i=0;i<20;i++)a.edit({label:'原页后续编辑 '+i});assert.notEqual(h.storage.getItem(prior.key),JSON.stringify({schema:'graph-source-form-slot-v122',scope:prior.scope,window_id:prior.window_id,owner:prior.owner,generation:prior.generation,form:prior.form}));
 const b=h.make(cloneTab);assert.equal(b.form.label,'供流程选择的温度结果');assert.equal(cloneTab.getItem(a.prefix+'form-head'),head);assert.notEqual(b.formOwner,a.formOwner);
 b.dispose();const remounted=h.make(cloneTab);assert.equal(remounted.form.label,'供流程选择的温度结果');remounted.edit({label:'复制页独立的新编辑'});
 assert.equal(a.form.label,'原页后续编辑 19');assert.equal(h.make(cloneTab).form.label,'复制页独立的新编辑');
});
test('identity remains concealed until cold verification and a cookie-only actor change or failed lookup preserves storage without exposing a verified state',async()=>{
 const h=fixture(),c=await prepared(h);assert.equal(c.identityStatus,'verified');const head=h.tab.getItem(c.prefix+'form-head');c.dispose();
 const restored=h.make();assert.equal(restored.identityStatus,'unverified');h.state.cookieActor=ID(99);await assert.rejects(restored.open());assert.equal(restored.identityStatus,'concealed');assert.equal(h.tab.getItem(c.prefix+'form-head'),head);
 h.state.cookieActor=scope.actor_id;h.state.meError=401;await assert.rejects(restored.recheckIdentity());assert.equal(restored.identityStatus,'concealed');h.state.meError=null;
 await restored.recheckIdentity();assert.equal(restored.identityStatus,'verified');assert.equal(restored.form.label,'供流程选择的温度结果');assert.equal(h.tab.getItem(c.prefix+'form-head'),head);
});
test('a new window cannot read another scope draft and a changed identity fences late responses',async()=>{
 const h=fixture(),a=await prepared(h),other=h.make(session());assert.equal(other.form.label,'');
 const before=h.calls.length;h.tab.setItem('ga-setup-csrf-user',ID(90));await assert.rejects(a.register());assert.equal(h.calls.length,before);
 const wrong=h.make(h.tab,{actor_id:ID(90),workspace_id:ID(91)});assert.equal(wrong.corrupt,true);assert.equal(wrong.pending,null);
});
test('disposed controller cannot settle a late response or touch the remounted scope',async()=>{
 const h=fixture(),c=await prepared(h);let release;const original=h.api;
 c.api=async(path,options)=>{const value=await original(path,options);if(path==='/setup/graph-event-producers')await new Promise(resolve=>{release=resolve;});return value;};
 const sending=c.register();for(let i=0;i<60&&!release;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(typeof release,'function');c.dispose();const next=h.make();release();await assert.rejects(sending);
 assert.ok(next.pending);assert.equal(registrationPosts(h).length,1);await next.recover();assert.equal(next.pending,null);
});
test('revocation freezes trimmed reason, recovers immutable receipt and never registers a replacement source',async()=>{
 const h=fixture(),c=await prepared(h);await c.register();await c.selectRegistration(ID(5));c.setReason('  停止此结果来源，保留历史  ');h.state.loseRevoke=true;await assert.rejects(c.revoke());
 const body=copy(c.pending.body);assert.equal(body.reason,'停止此结果来源，保留历史');assert.equal(body.producer_version,1);c.dispose();
 const next=h.make();await next.open();assert.equal(next.pending.kind,'revoke');await next.recover();assert.equal(next.pending,null);assert.equal(registrationPosts(h).length,1);
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path.endsWith('/revocations')).length,1);
});
test('changed saved revocation body cannot falsely settle the original pending request',async()=>{
 const h=fixture(),c=await prepared(h);await c.register();await c.selectRegistration(ID(5));c.setReason('停止新的结果供应');h.state.loseRevoke=true;await assert.rejects(c.revoke());h.state.wrongReceipt=true;
 await assert.rejects(c.recover());assert.ok(c.pending);assert.equal(c.pendingNotFound,false);
});
test('current detail refresh failure cannot undo successful stop or leave active stop controls',async()=>{
 const h=fixture(),c=await prepared(h);await c.register();await c.selectRegistration(ID(5));c.setReason('停止此结果来源');h.state.failDetail=true;
 await c.revoke();assert.equal(c.pending,null);assert.equal(c.detail.status,'revoked');assert.equal(c.pages.registrations.items[0].status,'revoked');assert.ok(c.notice.includes('原保存结果已核对'));
 assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path.endsWith('/revocations')).length,1);
});
test('empty filtered page with a next cursor remains readable and choices preserve real published versions',async()=>{
 const h=fixture();h.state.emptyPage=true;const c=h.make();await c.open();assert.equal(c.pages.source.items.length,0);assert.equal(c.pages.source.next_after,ID(20));
 await c.loadMore('source');assert.equal(c.pages.source.items[0].release_version,'1.0.0');assert.ok(h.calls.some(call=>call.path.includes('after='+ID(20))));
});
test('registration rows cannot become execution grants and field labels avoid raw identity fallback',()=>{
 const body={schema:'graph-event-producer-command-v1',request_id:ID(7),label:'结果',adapter_id:'processed-observation-v1',source_id:ID(3),source_hash:H,extractor_id:ID(4),extractor_hash:H};
 const row={id:ID(5),label:body.label,status:'active',version:1,contract_hash:H,contract:contract(body),registration:{request_id:body.request_id,actor_id:scope.actor_id,command_hash:H,command:body},execution_granted:true,physical_state_verified:false};
 assert.throws(()=>checkGraphSourceRegistration(row));assert.throws(()=>checkGraphSourceCapability({id:ID(3),name:'错误来源',kind:'tool',content_hash:H,release_version:'1.0.0'},'source'));
 assert.deepEqual(graphSourceResultFields({type:'object',properties:{[ID(3)]:{type:'string'},temperature:{type:'number',title:'温度'}},required:['temperature']}),[{label:'结果字段 1',type:'文字',required:false},{label:'温度',type:'数值',required:true}]);
});

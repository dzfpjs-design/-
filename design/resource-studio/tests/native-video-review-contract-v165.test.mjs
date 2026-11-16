// Actual frontend protocol/Journal only. Synthetic DTO/storage/WebLocks do not
// establish a database, native VideoRun, human review authority or real media.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {NativeVideoReviewJournalV165 as Journal,emptyVideoReviewFormV165,checkedVideoReviewDetailV165 as detail,videoReviewScopeV165 as scope,videoReviewCommandV165 as command,checkedVideoReviewOriginalV165 as original,checkedVideoReviewRecoveryV165 as recovery,videoReviewIdentityV165 as identity,videoReviewSHA256V165 as hash,videoReviewRecoveryPathV165 as recoveryPath,videoReviewNoteReadyV165 as explained} from '../src/native-video-review-contract-v165.mjs';

const id=n=>'16500000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),username:'合成核查人',role:'operator'},instance=id(3),observation=id(4),release=id(5),run=id(6),frame=id(7),evidence=id(8),request=id(9),digest='a'.repeat(64);
const form=()=>({note:'核查画面中的候选，仍保留未知条件',frame_ids:[frame],decision:'uncertain',confirm_candidate_only:true});
const submitted=()=>({version:1,actor_id:account.user_id,actor_label:account.username,note:form().note,frame_ids:[frame],decision:'uncertain',saved_at:'2026-10-21T00:30:20.123456+00:00'});
const human=()=>{const {actor_label,...item}=submitted();return {...item,operation:'submit'};};
function view(saved=false){return {schema:'native-video-review-view-v165',instance_id:instance,observation_id:observation,run_id:run,release_id:release,receipt_hash:digest,version:saved?1:0,status:saved?'reviewed':'empty',owner_id:saved?account.user_id:null,draft:saved?{note:form().note,frame_ids:[frame],decision:'uncertain'}:{note:'',frame_ids:[],decision:null},submitted_history:saved?[submitted()]:[],latest_submitted:saved?submitted():null,human_history:saved?[human()]:[],source_available:true,source_issue:null,run:{id:run,name:'合成网关保存视频',status:'succeeded',input_sha256:digest,gateway_capture:true,original_path:'/api/videos/'+run+'/original',candidate_only:true,source_time_verified:false,physical_state_verified:false},frames:[{id:frame,sample_index:0,seconds:0,evidence_id:evidence,sha256:digest,width:640,height:360,tracks:[],utc:null,content_path:'/api/evidence/'+evidence+'/content'}],can_save_draft:true,can_submit:true};}
const full=()=>scope(account,instance,observation,release,view());
function storage(){const values=new Map();return {fail:false,values,getItem:key=>values.has(key)?values.get(key):null,setItem(key,value){if(this.fail)throw Error('synthetic quota');values.set(key,String(value));},removeItem:key=>values.delete(key)};}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).then(callback);tails.set(name,next.catch(()=>{}));return next;}};}
function journal(current=full(),options={}){return new Journal(current,{storage:storage(),session:storage(),locks:locks(),...options});}
const authorize=async()=>{};
async function installed(j=journal(),operation='draft',version=0){j.open();j.save(form());const body=command(j.scope,form(),version,operation,request);return {j,body,sent:await j.install(operation,body,authorize)};}
const state=(sent,outcome='saved')=>({schema:'native-video-review-request-v165',request_id:sent.value.request_id,wire_sha256:sent.value.wire_sha256,outcome,result:outcome==='unknown'?null:{observation_id:observation,version:JSON.parse(sent.value.exact_body).expected_version+1,operation:sent.value.operation,status:sent.value.operation==='submit'?'reviewed':'draft'}});
const previous=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
after(()=>{if(previous)Object.defineProperty(globalThis,'crypto',previous);else delete globalThis.crypto;});

test('V165 detail has exact media paths, immutable receipt scope and explicit unknown conditions',()=>{
 const value=view();assert.equal(detail(value,account,instance,observation),value);assert.deepEqual(full(),{actor_id:account.user_id,workspace_id:account.workspace_id,username:account.username,instance_id:instance,observation_id:observation,role:'operator',current_release_id:release,release_id:release,receipt_hash:digest});
 for(const mutate of [v=>v.extra=true,v=>v.run.original_path='https://elsewhere.invalid/video',v=>v.run.physical_state_verified=true,v=>v.frames[0].content_path='/api/evidence/'+id(20)+'/content',v=>v.frames[0].sample_index=true]){const bad=view();mutate(bad);assert.throws(()=>detail(bad,account,instance,observation));}
});
test('V165 named team submissions are separate from personal contiguous history',()=>{
 const value=view(true);assert.equal(detail(value,account,instance,observation),value);for(const mutate of [v=>delete v.submitted_history[0].actor_label,v=>v.human_history[0].actor_label='unexpected',v=>v.human_history[0].actor_id=id(30),v=>v.human_history[0].version=2,v=>v.owner_id=id(31)]){const bad=copy(value);mutate(bad);assert.throws(()=>detail(bad,account,instance,observation));}
});
test('V165 damaged source keeps original human history and receipt while hiding all media and writes',()=>{
 const value={...view(true),source_available:false,source_issue:'原依据当前不可核',run:null,frames:[],can_save_draft:false,can_submit:false};assert.equal(detail(value,account,instance,observation),value);assert.equal(value.draft.note,form().note);for(const mutate of [v=>v.can_submit=true,v=>v.receipt_hash=null,v=>v.release_id=null,v=>v.run=view().run]){const bad=copy(value);mutate(bad);assert.throws(()=>detail(bad,account,instance,observation));}
});
test('V165 draft is allowed at version zero without confirmation; submit requires explicit candidate-only consent',()=>{
 const blank=emptyVideoReviewFormV165();assert.deepEqual(command(full(),blank,0,'draft',request),{request_id:request,expected_version:0,note:'',frame_ids:[],decision:null});assert.throws(()=>command(full(),blank,0,'submit',request));assert.deepEqual(Object.keys(command(full(),form(),0,'submit',request)),['request_id','expected_version','note','frame_ids','decision','confirm_candidate_only']);
 for(const change of [{confirm_candidate_only:1},{decision:null},{frame_ids:[]},{note:'短'}])assert.throws(()=>command(full(),{...form(),...change},0,'submit',request));for(const version of [true,-1,1.5,'0'])assert.throws(()=>command(full(),form(),version,'draft',request));
});
test('V165 submit explanation length matches Python codepoints and strip, not JavaScript UTF-16 aliases',()=>{
 assert.equal(explained('😀😀😀'),false);assert.equal(explained('😀😀😀😀😀'),true);assert.equal(explained('\u0085\u001cabcd\u0085'),false);assert.equal(explained('\ufeffabcd'),true);assert.throws(()=>command(full(),{...form(),note:'😀😀😀'},0,'submit',request));assert.equal(command(full(),{...form(),note:'😀😀😀😀😀'},0,'submit',request).note,'😀😀😀😀😀');
});
test('V165 exact original UTF-8 wire and hash survive lookup without reconstruction',async()=>{
 const {j,sent}=await installed();assert.equal(sent.value.exact_body,JSON.stringify(command(full(),form(),0,'draft',request)));assert.equal(sent.value.wire_sha256,createHash('sha256').update(sent.value.exact_body).digest('hex'));assert.equal((await original(sent.value,full())),sent.value);assert.ok(recoveryPath(sent.value).endsWith('?expected_sha256='+sent.value.wire_sha256));assert.equal((await j.pending()).raw,sent.raw);
 for(const mutate of [v=>v.exact_body+=' ',v=>v.path=v.path.replace('/draft','/submit'),v=>v.wire_sha256='b'.repeat(64),v=>v.scope.actor_id=id(44)]){const bad=copy(sent.value);mutate(bad);await assert.rejects(()=>original(bad,full()));}
});
test('V165 recovery is exact original version and operation, never an arbitrary current snapshot',async()=>{
 const {sent}=await installed();assert.equal(recovery(state(sent),sent.value).outcome,'saved');assert.equal(recovery(state(sent,'unknown'),sent.value).result,null);for(const mutate of [v=>v.result.version=2,v=>v.result.version=true,v=>v.result.operation='submit',v=>v.result.observation_id=id(50),v=>v.result.extra=true,v=>v.outcome='rejected']){const bad=state(sent);mutate(bad);assert.throws(()=>recovery(bad,sent.value));}
});
test('V165 another release has isolated drafts but retains original pending GET authority',async()=>{
 const store=storage(),session=storage(),coordination=locks(),a=journal(full(),{storage:store,session,locks:coordination});a.open();a.save(form());const {sent}=await installed(a);const next=scope(account,instance,observation,id(51),view()),b=journal(next,{storage:store,session,locks:coordination});assert.deepEqual(b.open(),emptyVideoReviewFormV165());assert.equal((await b.pending()).raw,sent.raw);let sentAgain=false;await assert.rejects(()=>b.withOriginal(sent,authorize,()=>{sentAgain=true;}));assert.equal(sentAgain,false);assert.equal(journal(full(),{storage:store,session,locks:coordination}).open().note,form().note);
});
test('V165 another actor cannot see the private local draft or original request',async()=>{
 const {j}=await installed(),other=scope({...account,user_id:id(52),username:'另一核查人'},instance,observation,release,view());const next=journal(other,{storage:j.storage,session:j.session,locks:j.locks});assert.deepEqual(next.open(),emptyVideoReviewFormV165());assert.equal(await next.pending(),null);assert.throws(()=>identity({...account,username:'变化'},account));assert.throws(()=>identity({...account,role:'viewer'},account));
});
test('V165 quota failure preserves owner draft in memory and forbids immutable installation',async()=>{
 const j=journal();j.open();j.storage.fail=true;assert.throws(()=>j.save(form()),/quota/);assert.equal(j.form.note,form().note);await assert.rejects(()=>j.install('draft',command(full(),form(),0,'draft',request),authorize));assert.equal(await j.pending(),null);j.dispose();const recovered=journal(full(),{storage:j.storage,session:j.session,locks:j.locks});assert.equal(recovered.open().note,form().note);j.storage.fail=false;recovered.save(form());assert.equal(recovered.unsaved,false);recovered.dispose();
});
test('V165 lack of WebLocks cannot create an original request',async()=>{
 const j=journal(full(),{locks:null});j.open();j.save(form());await assert.rejects(()=>j.install('draft',command(full(),form(),0,'draft',request),authorize));assert.equal(await j.pending(),null);
});
test('V165 only one immutable original may be active, and replacement is never automatic',async()=>{
 const {j,sent}=await installed();await assert.rejects(()=>j.install('draft',command(full(),{...form(),note:'另外的完整人稿'},0,'draft',id(60)),authorize));assert.equal((await j.pending()).raw,sent.raw);
});
test('V165 final authorization after hash prevents a stale new request from installing',async()=>{
 const j=journal();j.open();j.save(form());let checks=0;await assert.rejects(()=>j.install('draft',command(full(),form(),0,'draft',request),async()=>{if(++checks>1)throw Error('synthetic late scope loss');}),/late scope loss/);assert.equal(checks,2);assert.equal(await j.pending(),null);
});
test('V165 original retransmission rechecks authority after immutable hash read',async()=>{
 const {j,sent}=await installed();let checks=0,posts=0;await assert.rejects(()=>j.withOriginal(sent,async()=>{if(++checks>1)throw Error('synthetic late role loss');},()=>{posts++;}),/late role loss/);assert.equal(posts,0);assert.equal((await j.pending()).raw,sent.raw);
});
test('V165 saved reply archives exact original and only clears its own active pointer',async()=>{
 const {j,sent}=await installed();await j.finish(sent,state(sent),authorize);assert.equal(await j.pending(),null);assert.equal(j.storage.getItem(j.prefix+'saved:'+request),sent.raw);assert.equal(j.storage.getItem(sent.key),sent.raw);assert.equal(j.form.note,form().note);
});
test('V165 archive quota cannot lose an active saved request',async()=>{
 const {j,sent}=await installed();j.storage.fail=true;await assert.rejects(()=>j.finish(sent,state(sent),authorize),/quota/);assert.equal((await j.pending()).raw,sent.raw);j.storage.fail=false;await j.finish(sent,state(sent),authorize);
});
test('V165 no server version progress means no CAS settlement and no second lookup',async()=>{
 const {j,sent}=await installed();let lookups=0;await assert.rejects(()=>j.resolveConflict(sent,authorize,async()=>view(),async()=>{lookups++;return state(sent,'unknown');}));assert.equal(lookups,0);assert.equal((await j.pending()).raw,sent.raw);
});
test('V165 version progress plus second unknown lookup explicitly archives old CAS while retaining human input',async()=>{
 const {j,sent}=await installed();const calls=[];const result=await j.resolveConflict(sent,authorize,async()=>{calls.push('detail');return view(true);},async value=>{calls.push('original');assert.equal(value.exact_body,sent.value.exact_body);return state(sent,'unknown');});assert.deepEqual(calls,['detail','original']);assert.equal(result.state.outcome,'unknown');assert.equal(await j.pending(),null);assert.equal(j.storage.getItem(j.prefix+'cas-conflict:'+request),sent.raw);assert.equal(j.form.note,form().note);
});
test('V165 second lookup saved settles as saved rather than falsely retiring a real save',async()=>{
 const {j,sent}=await installed();const result=await j.resolveConflict(sent,authorize,async()=>view(true),async()=>state(sent));assert.equal(result.state.outcome,'saved');assert.equal(j.storage.getItem(j.prefix+'saved:'+request),sent.raw);assert.equal(j.storage.getItem(j.prefix+'cas-conflict:'+request),null);assert.equal(await j.pending(),null);
});
test('V165 source identity change cannot be used as version-progress authority',async()=>{
 const {j,sent}=await installed();let reads=0;await assert.rejects(()=>j.resolveConflict(sent,authorize,async()=>({...view(true),receipt_hash:'b'.repeat(64)}),async()=>{reads++;return state(sent,'unknown');}));assert.equal(reads,0);assert.equal((await j.pending()).raw,sent.raw);
});
test('V165 scope loss during the final immutable digest cannot archive or clear the active original',async()=>{
 const {j,sent}=await installed(),before=Object.getOwnPropertyDescriptor(globalThis,'crypto');let count=0,current=true,entered,release;
 const waiting=new Promise(resolve=>{entered=resolve;}),blocked=new Promise(resolve=>{release=resolve;});
 Object.defineProperty(globalThis,'crypto',{configurable:true,value:{randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(algorithm,bytes){count++;if(count===2){entered();return blocked.then(()=>webcrypto.subtle.digest(algorithm,bytes));}return webcrypto.subtle.digest(algorithm,bytes);}}}});
 try{
  const settling=j.resolveConflict(sent,async()=>{if(!current)throw Error('synthetic stale final hash');},async()=>view(true),async()=>state(sent,'unknown'));
  await waiting;current=false;release();await assert.rejects(settling,/stale final hash/);
  assert.equal(j.storage.getItem(j.prefix+'active'),sent.headRaw);assert.equal(j.storage.getItem(sent.key),sent.raw);assert.equal(j.storage.getItem(j.prefix+'cas-conflict:'+request),null);assert.equal(j.storage.getItem(j.prefix+'saved:'+request),null);
 }finally{release();Object.defineProperty(globalThis,'crypto',before);}
});
test('V165 an overlong editable local note is retained exactly but never becomes an outbound command',()=>{
 const j=journal(),long={...form(),note:'字'.repeat(3001)};j.open();j.save(long);const recovered=journal(full(),{storage:j.storage,session:j.session,locks:j.locks});assert.equal(recovered.open().note,long.note);assert.throws(()=>command(full(),long,0,'draft',request),/3000/);
});
test('V165 broken draft head is fail-closed and cannot silently replace the original pointer',()=>{
 const j=journal();j.session.setItem(j.draftPrefix+'head','{"key":"foreign"}');assert.throws(()=>j.open());const originalHead=j.session.getItem(j.draftPrefix+'head');assert.throws(()=>j.save(form()));assert.equal(j.session.getItem(j.draftPrefix+'head'),originalHead);j.blocked=false;j.save(form());
});
test('V165 hash is over original UTF-8 including whitespace, never a normalized body',async()=>{
 assert.equal(await hash('{"说明":"画面"}'),createHash('sha256').update('{"说明":"画面"}').digest('hex'));assert.notEqual(await hash('{"x":1}'),await hash('{ "x":1}'));
});

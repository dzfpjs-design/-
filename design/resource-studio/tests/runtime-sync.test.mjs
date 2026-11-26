import {test} from 'node:test';
import assert from 'node:assert/strict';
global.sessionStorage={getItem(){return null;},setItem(){},removeItem(){}};
const {DraftSync,api}=await import('../src/runtime-api.js');
const {readDraftRecord,draftOutboxKey}=await import('../src/draft-cache-contract.mjs');
const {addReportCandidates}=await import('../src/report-candidate-draft.mjs');
const storage=()=>{const values=new Map();return {setItem(k,v){values.set(k,v);},getItem(k){return values.get(k);}};};
const tails=new Map();const locks={request(key,_options,operation){const result=(tails.get(key)||Promise.resolve()).then(operation);tails.set(key,result.catch(()=>{}));return result;}};
const response=(obj,status=200)=>new Response(JSON.stringify(obj),{status,headers:{'content-type':'application/json'}});

test('serial writes preserve the latest input and use acknowledged revision',async()=>{
 const s=storage(),requests=[],states=[];let release;
 global.fetch=async(url,options)=>{const input=JSON.parse(options.body);requests.push(input);if(requests.length===1)await new Promise(r=>release=r);return response({revision:input.revision+1});};
 const sync=new DraftSync(s,'draft',4,v=>states.push(v),{locks});sync.save({name:'A'});sync.save({name:'B'});await new Promise(r=>setTimeout(r,0));
 const pending=JSON.parse(s.getItem('draft'));assert.equal(pending.revision,4);assert.equal(pending.pending,true);assert.deepEqual(pending.configuration,{name:'B'});
 release();await sync.wait();assert.deepEqual(requests.map(r=>r.revision),[4,5]);
 assert.equal(JSON.parse(s.getItem('draft')).configuration.name,'B');assert.equal(JSON.parse(s.getItem('draft')).pending,false);assert.equal(states.at(-1).state,'saved');
});
test('network failure preserves latest input and retry uses the same revision',async()=>{
 const s=storage(),states=[];let fail=true;
 global.fetch=async()=>{if(fail)throw Error('offline');return response({revision:2});};
 const sync=new DraftSync(s,'draft',1,v=>states.push(v),{locks});sync.save({name:'A'});await assert.rejects(sync.wait());
 sync.save({name:'B'});assert.equal(readDraftRecord(s,draftOutboxKey('draft',sync.owner)).configuration.name,'B');
 fail=false;sync.retry();await sync.wait();assert.equal(JSON.parse(s.getItem('draft')).revision,2);
 assert.equal(states.at(-1).state,'saved');
});
test('CAS conflict never silently replaces server version or loses local work',async()=>{
 const s=storage(),states=[];global.fetch=async()=>response({detail:'版本冲突'},409);
 const sync=new DraftSync(s,'draft',1,v=>states.push(v),{locks});sync.save({name:'人工修改'});await assert.rejects(sync.wait());
 assert.equal(sync.revision,1);assert.equal(JSON.parse(s.getItem('draft')).pending,true);
 assert.equal(states.at(-1).state,'error');assert.match(states.at(-1).text,/冲突/);
});

test('network and cancellation errors are understandable while HTTP status remains authoritative',async()=>{for(const name of ['TypeError','AbortError']){global.fetch=async()=>{throw Object.assign(Error('internal browser message'),{name});};await assert.rejects(api('/local-check'),e=>!e.message.includes('internal')&&(name==='AbortError'?e.name==='AbortError'&&e.message.includes('原请求'):e.status===undefined&&e.message.includes('连接服务器')));}global.fetch=async()=>response({detail:'版本冲突'},409);await assert.rejects(api('/local-check'),e=>e.status===409&&e.message==='版本冲突');});

test('changing login stops future old draft writes while keeping recoverable unsent work',async()=>{
 const s=storage(),requests=[];let release;
 global.fetch=async(url,options)=>{requests.push(JSON.parse(options.body));await new Promise(r=>release=r);return response({revision:5});};
 const sync=new DraftSync(s,'account-one-draft',4,()=>{},{locks});sync.save({name:'already sent'});await new Promise(r=>setTimeout(r,0));sync.save({name:'latest unsent'});sync.suspend();release();await assert.rejects(sync.wait());sync.retry();
 assert.equal(requests.length,1);const pending=JSON.parse(s.getItem('account-one-draft'));assert.equal(pending.revision,5);assert.equal(pending.pending,true);assert.deepEqual(pending.configuration,{name:'latest unsent'});
});

for(const failure of ['conflict','offline'])test('bulk candidate draft survives '+failure+' without overwriting an existing flow',async()=>{
 const material={id:'synthetic-material',hash:'a'.repeat(64),version:1,engine:'native-docx-v1',status:'parsed'};
 const step={key:'observe',name:'采集',purpose:'核查',agent:'多模感知智能体',role:'',output:'记录',condition:'',next_key:'end',else_key:'end',inference_reason:'建议',sources:[]};
 const candidate={key:'new',name:'新业务',trigger:'按计划',output:'记录',inference_reason:'建议',sources:[],steps:[step]};
 const bundle={schema:'setup-business-candidates-v1',origin:'local_summary',material,candidates:[candidate]};
 const preview={material,candidates:[{...candidate,citations:[],unresolved_sources:[],steps:[{...step,citations:[],unresolved_sources:[]}]}]};
 const original={businessCandidates:bundle,businesses:['已有业务'],flows:{'已有业务':{trigger:'原触发',output:'原产出',steps:[]}}};
 const next=addReportCandidates(original,bundle,preview,['new'],[material],'ready',()=> 'step-1').workspace,s=storage(),states=[];
 global.fetch=async()=>{if(failure==='offline')throw Error('offline');return response({detail:'版本冲突'},409);};
 const sync=new DraftSync(s,'candidate-'+failure,4,value=>states.push(value),{locks});sync.save(next);await assert.rejects(sync.wait());
 const pending=readDraftRecord(s,draftOutboxKey('candidate-'+failure,sync.owner));
 assert.equal(pending.pending,true);assert.deepEqual(pending.configuration.businessCandidates,bundle);
 assert.equal(pending.configuration.flows['已有业务'].trigger,'原触发');assert.equal(pending.configuration.flows['新业务'].confirmedSignature,null);
 assert.equal(original.flows['新业务'],undefined);assert.equal(states.at(-1).state,'error');
});

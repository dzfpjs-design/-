import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {DAILY_COLUMNS,dailyScope,dailyRequestRejected,dailyName,chinaTime,dailyListPath,dailyRecordsPath,checkDailyDetail,checkDailyPage,matchingExport,dailyExportConfirmation,validDailyExportConfirmation,dailyExportResetTarget,dailyCSVDownloadPath,dailyOriginalDownloadPath,dailyCSVAttachmentScope,dailyOriginalAttachmentScope,dailyAttachmentScopeHash,checkDailyCSVAttachment,checkDailyOriginalAttachment,dailyCSVRow,csvCell,csvLines,readableDailyState,prepareDailyExportPage,verifyExportChunks} from '../src/mqtt-daily-policy.mjs';

const account={workspace_id:'workspace-a',username:'operator-a'};
const scope=dailyScope(account,'gateway-a');
const selection={ledgerId:'ledger-a',revision:1005,kind:'original',status:'all'};
const detail={ledger_id:'ledger-a',revision:1005,current_revision:1006,revision_hash:'fixed-hash',instance_id:null};
const baseJob=(total=1005)=>({id:'job-a',scope,ledgerId:selection.ledgerId,revision:selection.revision,revisionHash:detail.revision_hash,kind:'original',filter:'all',instanceID:null,total,count:0,pages:0,cursor:'',manifest:[],status:'paused'});
const item=n=>({item_id:'original:'+n,kind:'original',record_label:'设备记录 '+n,receipt_id:'internal-receipt-'+n,topic:'sensor/raw',input_sha256:'original-hash',observed_at:'2026-10-10T23:30:00Z',received_at:'2026-10-11T02:00:00Z',business_day:'2026-10-11',status:'received',data_received:true,needs_review:false,raw_bytes:'SHOULD NEVER BE CACHED'});
const page=(rows,total=1005,next='')=>({schema:'mqtt-daily-records-v2',ledger_id:selection.ledgerId,revision:selection.revision,revision_hash:detail.revision_hash,instance_id:null,kind:'original',status:'all',total,records:rows,next_cursor:next});
async function completed(total=3){let job=baseJob(total),chunks=[],keys=new Set();for(let start=0;start<total;start+=2){const rows=Array.from({length:Math.min(2,total-start)},(_,i)=>item(start+i)),prepared=await prepareDailyExportPage(job,page(rows,total,start+rows.length<total?'cursor-'+(start+rows.length):''),keys);job=prepared.job;chunks.push(prepared.chunk);for(const key of prepared.itemKeys)keys.add(key);}return {job,chunks};}

test('saved scopes separate account, workspace, source and native business instance',()=>{
 assert.notEqual(scope,dailyScope({...account,username:'operator-b'},'gateway-a'));
 assert.notEqual(scope,dailyScope({...account,workspace_id:'workspace-b'},'gateway-a'));
 assert.notEqual(scope,dailyScope(account,'gateway-b'));
 assert.notEqual(scope,dailyScope(account,'gateway-a','instance-a'));
 assert.notEqual(dailyScope(account,null,'instance-a'),dailyScope(account,null,'instance-b'));
});
test('only a precise non-committed recovery rejection can release an inspection or backfill nonce',()=>{
 assert.equal(dailyRequestRejected({status:409,message:'原检查不满足恢复条件，请读取最新进度'},'inspection'),true);
 assert.equal(dailyRequestRejected({status:409,message:'原回填不满足恢复条件，请读取最新进度'},'backfill'),true);
 for(const error of [{status:500,message:'原检查不满足恢复条件，请读取最新进度'},{status:409,message:'其他内容冲突'},{message:'响应丢失'},{name:'AbortError'}])assert.equal(dailyRequestRejected(error,'inspection'),false);
 assert.equal(dailyRequestRejected({status:409,message:'原检查不满足恢复条件，请读取最新进度'},'backfill'),false);
});
test('capture unknown remains separate and displayed timestamps use China time',()=>{
 assert.equal(dailyName(null),'采集时间待核');assert.equal(chinaTime(null),'未知');assert.equal(chinaTime('bad'),'时间格式待核');
 assert.match(chinaTime('2026-10-10T23:30:00Z'),/2026\/10\/11.*07:30:00/);
 assert.match(dailyListPath('source',null,'opaque-after','unknown'),/business_day=unknown&after=opaque-after/);
 assert.match(dailyListPath(null,'instance-a','','2026-10-11'),/^\/mqtt\/instances\/instance-a\/days\?limit=20&business_day=2026-10-11$/);
});
test('fixed revision, scope, kind, filter and snapshot hash cannot mix with a newer live head',()=>{
 assert.equal(checkDailyDetail(detail,selection,null).current_revision,1006);
 assert.equal(checkDailyPage(page([item(0)],1),selection,null,'fixed-hash').revision,1005);
 for(const changed of [{revision:1006},{ledger_id:'other'},{instance_id:'other'},{kind:'processing'},{status:'received'},{revision_hash:'late-new-head'}])assert.throws(()=>checkDailyPage({...page([item(0)],1),...changed},selection,null,'fixed-hash'),/不一致/);
 assert.throws(()=>checkDailyDetail({...detail,instance_id:'other'},selection,null),/不一致/);
 assert.match(dailyRecordsPath(selection,null,'cursor-a'),/revision=1005&kind=original&status=all&limit=50&after=cursor-a$/);
});
test('full export covers more than the previous 1000 prefix in exact fixed batches',async()=>{
 let job=baseJob(),chunks=[],keys=new Set();for(let start=0;start<1005;start+=100){const end=Math.min(1005,start+100),prepared=await prepareDailyExportPage(job,page(Array.from({length:end-start},(_,i)=>item(start+i)),1005,end<1005?'cursor-'+end:''),keys);job=prepared.job;chunks.push(prepared.chunk);for(const key of prepared.itemKeys)keys.add(key);}
 assert.equal(job.count,1005);assert.equal(job.pages,11);assert.equal(job.status,'completed');assert.equal(job.cursor,'');assert.equal(keys.size,1005);
 assert.equal((await verifyExportChunks(job,chunks)).flatMap(c=>c.rows).length,1005);
 const text=csvLines([DAILY_COLUMNS,...chunks.flatMap(c=>c.rows)]);assert.match(text,/设备记录 1004/);assert.doesNotMatch(text,/SHOULD NEVER BE CACHED|internal-receipt|original-hash/);
});
test('pause checkpoint resumes the same exact scope and includes each item once',async()=>{
 const first=await prepareDailyExportPage(baseJob(3),page([item(0),item(1)],3,'cursor-2'));const paused={...first.job,status:'paused'};
 assert.equal(paused.cursor,'cursor-2');assert.equal(paused.count,2);await verifyExportChunks(paused,[first.chunk]);
 assert.equal(matchingExport(paused,scope,selection,null,'fixed-hash'),true);assert.equal(matchingExport(paused,'other-scope',selection,null,'fixed-hash'),false);assert.equal(matchingExport(paused,scope,{...selection,revision:1006},null,'fixed-hash'),false);assert.equal(matchingExport(paused,scope,selection,'other-instance','fixed-hash'),false);assert.equal(matchingExport(paused,scope,selection,null,'other-hash'),false);
 const last=await prepareDailyExportPage(paused,page([item(2)],3),new Set(first.chunk.itemKeys));await verifyExportChunks(last.job,[first.chunk,last.chunk]);assert.equal(last.job.count,3);
});
test('inline export confirmation freezes account, source, revision, kind, filter, instance and original task',()=>{
 const job=baseJob(),confirmation=dailyExportConfirmation(job,scope,selection,null,'fixed-hash',true),original=structuredClone(job);
 assert.equal(validDailyExportConfirmation(confirmation,job,scope,selection,null,'fixed-hash'),true);
 assert.equal(validDailyExportConfirmation(null,job,scope,selection,null,'fixed-hash'),false);
 assert.equal(validDailyExportConfirmation({},job,scope,selection,null,'fixed-hash'),false);
 for(const changed of [{ledgerId:'other'},{revision:1006},{kind:'processing'},{status:'pending'}])assert.equal(validDailyExportConfirmation(confirmation,job,scope,{...selection,...changed},null,'fixed-hash'),false);
 assert.equal(validDailyExportConfirmation(confirmation,job,'other-account-scope',selection,null,'fixed-hash'),false);
 assert.equal(validDailyExportConfirmation(confirmation,job,scope,selection,'other-instance','fixed-hash'),false);
 assert.equal(validDailyExportConfirmation(confirmation,job,scope,selection,null,'changed-hash'),false);
 assert.equal(validDailyExportConfirmation(confirmation,{...job,id:'another-task'},scope,selection,null,'fixed-hash'),false);
 assert.deepEqual(job,original);
});
test('reset can clear only a matching current range cache and never another range',()=>{
 const job=baseJob();assert.equal(dailyExportResetTarget(job,scope,selection,null,'fixed-hash'),job.id);
 for(const changed of [{ledgerId:'other'},{revision:1006},{kind:'processing'},{status:'pending'}])assert.equal(dailyExportResetTarget(job,scope,{...selection,...changed},null,'fixed-hash'),null);
 assert.equal(dailyExportResetTarget(job,'other-account',selection,null,'fixed-hash'),null);
 assert.equal(dailyExportResetTarget(job,scope,selection,'other-instance','fixed-hash'),null);
});
test('M40 restart, range replacement and stopping waiting do not invoke a blocking native confirmation',async()=>{
 const component=await readFile(new URL('../src/MqttDailyStudio.jsx',import.meta.url),'utf8');assert.doesNotMatch(component,/\b(?:window\.)?confirm\s*\(/);
});
test('page duplicate, cross-instance processing and missing final rows stop before checkpoint advance',async()=>{
 const initial=baseJob(3),first=await prepareDailyExportPage(initial,page([item(0)],3,'cursor-1'));
 await assert.rejects(prepareDailyExportPage(first.job,page([item(0)],3,'cursor-2'),new Set(['original:0'])),/重复/);
 await assert.rejects(prepareDailyExportPage(initial,page([item(0),item(0)],3,'cursor-2')),/重复/);
 await assert.rejects(prepareDailyExportPage(initial,page([item(0)],3)),/全部记录/);
 await assert.rejects(prepareDailyExportPage(initial,page([],3,'cursor-1')),/不一致/);
 await assert.rejects(prepareDailyExportPage(initial,page([item(0)],4,'cursor-1')),/不一致/);
 const scoped={...baseJob(1),instanceID:'instance-a',kind:'processing'},processingPage={...page([{...item(0),kind:'processing',instance_id:'instance-b'}],1),instance_id:'instance-a',kind:'processing'};await assert.rejects(prepareDailyExportPage(scoped,processingPage),/范围/);
 assert.equal(initial.count,0);assert.equal(initial.pages,0);assert.equal(first.job.count,1);
});
test('missing chunks, altered rows, altered frozen scope and broken hash chain cannot produce a partial CSV',async()=>{
 const {job,chunks}=await completed();await verifyExportChunks(job,chunks);
 await assert.rejects(verifyExportChunks(job,chunks.slice(0,1)),/不完整/);
 const changed=structuredClone(chunks);changed[0].rows[0][1]='another record of same length';await assert.rejects(verifyExportChunks(job,changed),/摘要/);
 const wrongScope=structuredClone(chunks);wrongScope[0].scope='another-workspace';await assert.rejects(verifyExportChunks(job,wrongScope),/范围/);
 const wrongVersion=structuredClone(chunks);wrongVersion[1].revision=1006;await assert.rejects(verifyExportChunks(job,wrongVersion),/范围/);
 const wrongChain=structuredClone(chunks);wrongChain[1].previousHash='bad';await assert.rejects(verifyExportChunks(job,wrongChain),/顺序/);
 await assert.rejects(verifyExportChunks({...job,count:2},chunks),/行数/);
 await assert.rejects(verifyExportChunks({...job,total:4},chunks),/总量/);
});
test('minimal cached human rows omit internal identifiers, credentials and packet bytes',()=>{
 const row=dailyCSVRow({...item(0),entity_id:'internal-object',source_id:'internal-source',binding_id:'internal-binding',entity_label:'一号监测点',instance_name:'林场业务',rule_name:'温度候选规则',credentials:'SECRET',raw_bytes:'SENSITIVE'});
 assert.equal(row.length,DAILY_COLUMNS.length);assert.equal(row[1],'设备记录 0');assert.equal(row[9],'一号监测点');assert.doesNotMatch(JSON.stringify(row),/internal-|SECRET|SENSITIVE/);
 assert.equal(dailyCSVRow({...item(0),record_label:null})[1],'数据编号未识别');
});
test('CSV spreadsheet formulas and quote/newline text remain inert and properly escaped',()=>{
 assert.equal(csvCell('=2+2'),'"\'=2+2"');assert.equal(csvCell('@SUM(A1)'),'"\'@SUM(A1)"');assert.equal(csvCell('hello,"quoted"\nnext'),'"hello,""quoted""\nnext"');
 assert.equal(csvLines([[null,'a'],['b','c']]),'"","a"\r\n"b","c"\r\n');
});
test('data-manager receipt and independent object processing remain distinct claims',()=>{
 assert.equal(readableDailyState({kind:'original',data_received:true}),'原件已送达数据管理');
 assert.equal(readableDailyState({kind:'processing',data_received:true,processing_status:'pending'}),'等待后台处理');
 assert.equal(readableDailyState({kind:'processing',status:'succeeded'}),'已提取候选信息');
 assert.equal(readableDailyState({kind:'processing',status:'invalidated'}),'旧授权已失效，记录保留');
});
test('processing CSV rows cannot turn an absent original acknowledgement into a false undelivered claim',()=>{
 const processed={kind:'processing',record_label:'设备记录 1',status:'succeeded',revision:1};
 assert.equal(dailyCSVRow(processed)[6],'见原件收讫记录');assert.equal(dailyCSVRow(processed)[7],'已提取候选信息');
 assert.equal(dailyCSVRow({...processed,status:'pending'})[6],'见原件收讫记录');
 assert.equal(dailyCSVRow({...item(1),data_received:true})[6],'已收讫');assert.equal(dailyCSVRow({...item(1),data_received:false})[6],'尚未收讫');
});
test('protected HTTP file paths bind workspace and the whole fixed range without URL credentials',()=>{
 const job={...baseJob(3),instanceID:'instance-a',kind:'processing',filter:'failed'},csv=new URL(dailyCSVDownloadPath(account.workspace_id,job),'http://localhost');
 assert.equal(csv.pathname,'/mqtt/daily/ledger-a/export.csv');assert.deepEqual(Object.fromEntries(csv.searchParams),{workspace_id:'workspace-a',revision:'1005',expected_revision_hash:'fixed-hash',expected_total:'3',kind:'processing',status:'failed',instance_id:'instance-a'});
 const original=new URL(dailyOriginalDownloadPath(account.workspace_id,selection,'instance-a',item(2),'fixed-hash'),'http://localhost');
 assert.equal(original.pathname,'/mqtt/daily/ledger-a/original/internal-receipt-2/download');assert.deepEqual(Object.fromEntries(original.searchParams),{workspace_id:'workspace-a',revision:'1005',expected_revision_hash:'fixed-hash',expected_input_sha256:'original-hash',instance_id:'instance-a'});
 assert.equal(new URL(dailyCSVDownloadPath(account.workspace_id,baseJob(3)),'http://localhost').searchParams.has('instance_id'),false);
 assert.doesNotMatch(csv.href+original.href,/operator-a|token|password|secret|credential|csrf/i);
});
test('attachment scope hashes use the server canonical UTF8 JSON and separate equal-size scopes',async()=>{
 const job={...baseJob(3),instanceID:'实例甲'},canonical='{"instance_id":"实例甲","kind":"original","ledger_id":"ledger-a","revision":1005,"revision_hash":"fixed-hash","status":"all","total":3,"workspace_id":"workspace-a"}',expected=createHash('sha256').update(canonical,'utf8').digest('hex');
 assert.equal(await dailyAttachmentScopeHash(dailyCSVAttachmentScope(account.workspace_id,job)),expected);
 for(const changed of [{instanceID:'实例乙'},{kind:'processing'},{filter:'failed'},{revision:1006},{revisionHash:'changed-hash'}])assert.notEqual(await dailyAttachmentScopeHash(dailyCSVAttachmentScope(account.workspace_id,{...job,...changed})),expected);
 assert.notEqual(await dailyAttachmentScopeHash(dailyCSVAttachmentScope('workspace-b',job)),expected);
 const originalScope=dailyOriginalAttachmentScope(account.workspace_id,selection,'实例甲',item(1),'fixed-hash');assert.deepEqual(originalScope,{workspace_id:'workspace-a',ledger_id:'ledger-a',revision:1005,revision_hash:'fixed-hash',instance_id:'实例甲',receipt_id:'internal-receipt-1',input_sha256:'original-hash'});
 assert.notEqual(await dailyAttachmentScopeHash(originalScope),await dailyAttachmentScopeHash({...originalScope,receipt_id:'other-receipt'}));
});
test('CSV links require attachment metadata and the exact whole scope digest',async()=>{
 const job=baseJob(3),scopeHash=await dailyAttachmentScopeHash(dailyCSVAttachmentScope(account.workspace_id,job)),checksum='a'.repeat(64),headers=new Headers({'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="daily.csv"','X-Mqtt-Revision':'1005','X-Mqtt-Revision-Hash':'fixed-hash','X-Mqtt-Record-Count':'3','X-Mqtt-Scope-Hash':scopeHash,'X-Content-SHA256':checksum});
 assert.equal(checkDailyCSVAttachment(headers,job,scopeHash),checksum);
 for(const [key,value] of [['X-Mqtt-Revision','1006'],['X-Mqtt-Revision-Hash','changed'],['X-Mqtt-Record-Count','2'],['X-Mqtt-Scope-Hash','b'.repeat(64)],['X-Content-SHA256','invalid'],['Content-Type','text/html'],['Content-Disposition','inline']]){const changed=new Headers(headers);changed.set(key,value);assert.throws(()=>checkDailyCSVAttachment(changed,job,scopeHash),/未提供下载链接/);}
 for(const key of ['X-Mqtt-Scope-Hash','X-Content-SHA256','Content-Disposition']){const changed=new Headers(headers);changed.delete(key);assert.throws(()=>checkDailyCSVAttachment(changed,job,scopeHash),/未提供下载链接/);}
 const otherScope=await dailyAttachmentScopeHash(dailyCSVAttachmentScope(account.workspace_id,{...job,instanceID:'different-instance'}));assert.throws(()=>checkDailyCSVAttachment(headers,job,otherScope),/未提供下载链接/);assert.throws(()=>checkDailyCSVAttachment(headers,job),/未提供下载链接/);
});
test('original links require the frozen input digest, revision and complete instance scope',async()=>{
 const original={...item(1),input_sha256:'c'.repeat(64)},scopeHash=await dailyAttachmentScopeHash(dailyOriginalAttachmentScope(account.workspace_id,selection,'instance-a',original,'fixed-hash')),headers=new Headers({'Content-Disposition':'attachment; filename="mqtt-original.bin"','X-Content-SHA256':original.input_sha256,'X-Mqtt-Revision':'1005','X-Mqtt-Revision-Hash':'fixed-hash','X-Mqtt-Scope-Hash':scopeHash});
 assert.equal(checkDailyOriginalAttachment(headers,original,selection,'fixed-hash',scopeHash),original.input_sha256);
 for(const [key,value] of [['X-Content-SHA256','d'.repeat(64)],['X-Mqtt-Revision','1006'],['X-Mqtt-Revision-Hash','changed'],['X-Mqtt-Scope-Hash','b'.repeat(64)],['Content-Disposition','inline']]){const changed=new Headers(headers);changed.set(key,value);assert.throws(()=>checkDailyOriginalAttachment(changed,original,selection,'fixed-hash',scopeHash),/未提供下载链接/);}
 const otherScope=await dailyAttachmentScopeHash(dailyOriginalAttachmentScope(account.workspace_id,selection,'instance-b',original,'fixed-hash'));assert.throws(()=>checkDailyOriginalAttachment(headers,original,selection,'fixed-hash',otherScope),/未提供下载链接/);
});
test('stopping attachment verification preserves the durable full download cache',async()=>{
 const {job,chunks}=await completed(),before=structuredClone({job,chunks}),controller=new AbortController();controller.abort();await assert.rejects(verifyExportChunks(job,chunks,controller.signal),{name:'AbortError'});assert.deepEqual({job,chunks},before);await verifyExportChunks(job,chunks);
});
test('actual HTTP links are visible and let server attachment headers decide file downloads',async()=>{
 const component=await readFile(new URL('../src/MqttDailyStudio.jsx',import.meta.url),'utf8');
 assert.match(component,/<a[^>]*href=\{csvLink\.href\}[^>]*target="_blank"[^>]*>下载完整 CSV 文件<\/a>/);assert.match(component,/<a[^>]*href=\{originalReady\.href\}[^>]*target="_blank"[^>]*>下载这份原报文文件<\/a>/);
 assert.doesNotMatch(component,/\bnew Blob\b|URL\.createObjectURL|document\.createElement|\.click\s*\(|<a[^>]*\bdownload(?:\s|=|>)/);
 assert.match(component,/response\.body\?\.cancel\(\)/);assert.match(component,/文件尚未保存到本机/);assert.match(component,/停止链接准备，保留进度/);
});

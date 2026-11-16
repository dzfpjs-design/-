import test from 'node:test';
import assert from 'node:assert/strict';
import {canonical,checkedBody,checkedPending,checkedRecovery,citationPath,checkedCitation,checkedSearch,commandPath} from '../src/runtime-document-v205-protocol.mjs';
const id=n=>'20500000-0000-4000-8000-'+String(n).padStart(12,'0'),scope={actor_id:id(1),workspace_id:id(2),instance_id:id(3)};
const delivery={event_id:id(9),version:1,status:'received',verified:true,can_retry:false,error:null,previous_error:null};
const version=()=>({id:id(5),version:1,document_id:id(4),instance_id:id(3),sequence:1,name:'原文件.txt',status:'parsed',raw_saved:true,raw_sha256:'a'.repeat(64),parse_hash:'b'.repeat(64),parse_engine:'native-text',blocks_count:1,warnings:[],error:null,can_retry:false,can_cancel:false,latest_attempt_id:id(6),delivery});
const citation=()=>({version_id:id(5),document_id:id(4),instance_id:id(3),attempt_id:id(6),block_id:'b1',parse_hash:'b'.repeat(64),raw_sha256:'a'.repeat(64),block_hash:'d'.repeat(64),text_is_excerpt:false,char_span:{start:0,end:5},title:'运行记录',name:'原文件.txt',sequence:1,text:'保留的原文',location:{line:1},parse_engine:'native-text',delivery,citation_url:'/api/runtime-documents/versions/'+id(5)+'/citations/b1?attempt_id='+id(6)+'&expected_parse_hash='+'b'.repeat(64)});
const body=()=>({request_id:id(7),release_id:id(8),title:'运行记录',file_name:'原文件.txt',file_size:10,file_sha256:'a'.repeat(64)});
const pending=(kind='upload_document',value=body(),context={})=>({schema:'runtime-document-outbox-v205',scope,kind,context,body:value,raw:canonical(value),sha256:'c'.repeat(64),rejected:null});
test('four native file types and strict original metadata exclude XLSX, fake CAS, credentials and unexpected command fields',()=>{
 for(const extension of ['pdf','docx','txt','md'])assert.equal(checkedBody('upload_document',{...body(),file_name:'资料.'+extension}).file_name,'资料.'+extension);
 for(const file_name of ['宏.xlsm','表.xlsx','../原件.txt','a\\b.pdf'])assert.throws(()=>checkedBody('upload_document',{...body(),file_name}));
 assert.throws(()=>checkedBody('upload_document',{...body(),file_size:true}));assert.throws(()=>checkedBody('upload_document',{...body(),file_size:20*1024*1024+1}));
 assert.throws(()=>checkedBody('retry',{request_id:id(7),expected_version:1.1,reason:'恢复原件'}));assert.throws(()=>checkedBody('search',{request_id:id(7),release_id:id(8),keyword:'词',sql:'SELECT outside'}));
 assert.equal(checkedBody('search',{request_id:id(7),release_id:id(8),keyword:'literal.* [文字]'}).keyword,'literal.* [文字]');
});
test('separate request namespace binds complete raw body, scope and immutable document path',()=>{
 const p=pending();assert.deepEqual(checkedPending(p,scope),p);assert.equal(commandPath(p.kind,scope),'/runtime-documents/instances/'+id(3)+'/documents');
 assert.throws(()=>checkedPending({...p,body:{...p.body,title:'改过'}},scope));assert.throws(()=>checkedPending(p,{...scope,actor_id:id(10)}));assert.throws(()=>checkedPending({...p,kind:'business_data_read'},scope));assert.throws(()=>commandPath('retry_receipt',scope,{version_id:id(5),event_id:'missing'}));
});
test('upload recovery restores the exact original file/hash and current same-instance document; new version requires original CAS advancement',()=>{
 const p=pending(),doc={id:id(4),version:1,instance_id:id(3),title:'运行记录',latest_version_id:id(5),latest:version()},reply={schema:'runtime-document-command-v205',found:true,body:p.body,result:doc};assert.equal(checkedRecovery(reply,p).id,id(4));
 for(const bad of [{...doc,instance_id:id(20)},{...doc,latest:{...version(),raw_sha256:'d'.repeat(64)}},{...doc,title:'另一个标题'}])assert.throws(()=>checkedRecovery({...reply,result:bad},p));
 const nextBody={...body(),expected_version:1,prior_version_id:id(15)},next=pending('upload_version',nextBody,{document_id:id(4)});assert.throws(()=>checkedRecovery({...reply,body:nextBody},next));assert.equal(checkedRecovery({...reply,body:nextBody,result:{...doc,version:2}},next).version,2);
 assert.equal(checkedRecovery({schema:'runtime-document-command-v205',found:false,body:null,result:null},p),null);
});
test('saved citations require exact same-origin namespace URL and original attempt/hash; newer b1 cannot replace older b1',()=>{
 const c=citation();assert.ok(citationPath(c,{requireURL:true}).includes('attempt_id='+id(6)));assert.equal(checkedCitation(c,c,scope).text,'保留的原文');
 for(const citation_url of ['https://outside.invalid/api/runtime-documents/versions/x','//outside.invalid/x',c.citation_url+'&outside=1',c.citation_url.replace(id(6),id(12)),c.citation_url+'#new'])assert.throws(()=>citationPath({...c,citation_url},{requireURL:true}));
 assert.throws(()=>checkedCitation({...c,attempt_id:id(12),citation_url:undefined},c,scope));assert.throws(()=>checkedCitation({...c,text:'后来重试的新正文'},c,scope));
});
test('complete search requires coverage and validated same-instance frozen hits; foreign inventory cannot render as current text',()=>{
 const s={id:id(18),version:1,instance_id:id(3),keyword:'原文',status:'completed',error:null,coverage:{selected:2,eligible:1,unavailable:1,limited:false,document_limit:100,hit_limit:100,notice:'一份新版尚未完成'},hits:[citation()]};assert.equal(checkedSearch(s,scope),s);
 assert.throws(()=>checkedSearch({...s,coverage:null},scope));assert.throws(()=>checkedSearch({...s,hits:[{...citation(),instance_id:id(90)}]},scope));assert.throws(()=>checkedSearch({...s,hits:[{...citation(),citation_url:'https://outside.invalid/'}]},scope));
});
test('excerpt verification uses the original block hash and Unicode character span, not UTF16 or a latest block replacement',()=>{
 const full={...citation(),text:'前😀原文后续'},hit={...full,text:'😀原文',text_is_excerpt:true,char_span:{start:1,end:4}};
 assert.equal(checkedCitation(full,hit,scope).text,full.text);assert.throws(()=>checkedCitation({...full,block_hash:'e'.repeat(64)},hit,scope));assert.throws(()=>checkedCitation(full,{...hit,char_span:{start:1,end:5}},scope));assert.throws(()=>checkedCitation(full,{...hit,text:'换过摘录'},scope));
});

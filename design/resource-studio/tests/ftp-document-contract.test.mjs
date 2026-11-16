import test from 'node:test';
import assert from 'node:assert/strict';
import {checkFtpAttempt,verifyFtpReceipt,ftpBody,ftpPath} from '../src/ftp-document-contract.mjs';

const request_id='53f37e1b-07d2-4ea2-9d9c-91d2fc71c634';
const connect={kind:'connect',submission:{request_id,name:'测试资料',grant_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}};
const listing={kind:'listing',submission:{request_id,version:2,connection_id:'11111111-1111-4111-8111-111111111111'}};
const imported={kind:'import',submission:{request_id,version:2,connection_id:'11111111-1111-4111-8111-111111111111',listing_id:'22222222-2222-4222-8222-222222222222',file_id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'}};

test('request journal accepts only the scoped, non-secret operation shape',()=>{
 for(const attempt of [connect,listing,imported])assert.equal(checkFtpAttempt(attempt),attempt);
 assert.throws(()=>checkFtpAttempt({kind:'connect',submission:{...connect.submission,name:'x'.repeat(101)}}));
 assert.throws(()=>checkFtpAttempt({kind:'connect',submission:{...connect.submission,grant_id:'not-a-grant'}}));
 assert.throws(()=>checkFtpAttempt({kind:'import',submission:{...imported.submission,file_id:'not-a-file-id'}}));
 assert.throws(()=>checkFtpAttempt({...connect,password:'do-not-store'}));
 assert.throws(()=>checkFtpAttempt({kind:'connect',submission:{...connect.submission,password:'do-not-store'}}));
 assert.throws(()=>checkFtpAttempt({kind:'import',submission:{...imported.submission,connection_id:''}}));
 assert.equal(ftpPath(imported),'/ftp/connections/11111111-1111-4111-8111-111111111111/imports');
 assert.deepEqual(ftpBody(imported),{request_id,version:2,listing_id:'22222222-2222-4222-8222-222222222222',file_id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'});
});

test('a receipt may complete only the same operation and must contain a valid result',()=>{
 const result={listing_id:'22222222-2222-4222-8222-222222222222',files:[{id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',name:'业务报告.docx',size:42,modified:'20261115093000'}]};
 const reply={request_id,kind:'listing',submission:{...listing.submission},state:'succeeded',result};
 assert.equal(verifyFtpReceipt(reply,listing),reply);
 assert.throws(()=>verifyFtpReceipt({...reply,submission:{...reply.submission,connection_id:'another'}},listing));
 assert.throws(()=>verifyFtpReceipt({...reply,result:{...result,files:[{...result.files[0],size:-1}]}},listing));
 assert.throws(()=>verifyFtpReceipt({...reply,state:'unknown'},listing));
 assert.equal(verifyFtpReceipt({...reply,state:'pending',result:null},listing).state,'pending');
 const source={connection_id:'11111111-1111-4111-8111-111111111111',listing_id:'22222222-2222-4222-8222-222222222222',file_id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',name:'报告.docx',size:42,modified:'20261115123000',sha256:'a'.repeat(64)};
 const done={request_id,kind:'import',submission:imported.submission,state:'succeeded',result:{material_id:'33333333-3333-4333-8333-333333333333',name:'报告.docx',status:'parsed',source}};
 assert.equal(verifyFtpReceipt(done,imported),done);
 for(const bad of [{...source,file_id:'other'},{...source,listing_id:'other'},{...source,sha256:'bad'},{...source,size:0}])assert.throws(()=>verifyFtpReceipt({...done,result:{...done.result,source:bad}},imported));
});

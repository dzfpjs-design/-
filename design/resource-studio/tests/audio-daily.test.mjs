import test from 'node:test';
import assert from 'node:assert/strict';
import {confirmDailyDetail,dailyRetryPath} from '../src/audio-daily-form.mjs';
const record={key:'segments:a',kind:'segments',id:'a',input_sha256:'same-original',task_id:null};
const value={record:{...record},current_version:8,current_status:'failed',integrity_verified:true};
test('daily evidence rejects a different record or original before playback/retry',()=>{
 assert.equal(confirmDailyDetail(record,value),value);
 for(const field of ['id','key','kind','input_sha256'])assert.throws(()=>confirmDailyDetail(record,{...value,record:{...record,[field]:'different'}}));
});
test('a lost retry response is resolved from current status without a replacement request',()=>{
 assert.equal(dailyRetryPath(record,value),'/audio-segments/a/retry');
 for(const current_status of ['queued','running','succeeded'])assert.throws(()=>dailyRetryPath(record,{...value,current_status,current_version:9}));
 assert.equal(dailyRetryPath(record,{...value,current_status:'cancelled'}),'/audio-segments/a/retry');
});
test('business-owned or inconsistent audio cannot bypass task lease via daily retry',()=>{
 const owned={...record,task_id:'task'};assert.throws(()=>dailyRetryPath(owned,{...value,record:owned}));
 assert.throws(()=>dailyRetryPath(record,{...value,integrity_verified:false}));
 assert.throws(()=>dailyRetryPath(record,{...value,current_version:0}));
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {mayReviseCompletion} from '../src/workflow-completion.mjs';
const request={version:7,step_id:'review',request_id:'original-request'};
const task={version:7,workflow_runtime:{current_step_id:'review',receipts:[]}};
test('confirmed validation error permits correction at unchanged durable step',()=>{
 assert.equal(mayReviseCompletion({status:400},task,request),true);
 assert.equal(mayReviseCompletion({status:422},task,request),true);
});
test('uncertain delivery or permission conflict retains original request',()=>{
 for(const status of [undefined,401,403,409,500])assert.equal(mayReviseCompletion({status},task,request),false);
});
test('committed receipt blocks replacement even when a validation response is stale',()=>{
 assert.equal(mayReviseCompletion({status:400},{...task,workflow_runtime:{...task.workflow_runtime,receipts:[{request_id:request.request_id}]}},request),false);
});
test('changed step version or unavailable durable receipt blocks replacement',()=>{
 for(const changed of [{...task,version:8},{...task,workflow_runtime:{current_step_id:'next',receipts:[]}},{...task,workflow_runtime:{current_step_id:'review'}}])assert.equal(mayReviseCompletion({status:400},changed,request),false);
});

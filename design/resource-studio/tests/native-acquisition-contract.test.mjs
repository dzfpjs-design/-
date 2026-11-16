import test from 'node:test';
import assert from 'node:assert/strict';
import {acquisitionLabel,restoreDecision,reviewBody,reviewChoices} from '../src/native-acquisition-contract.mjs';
test('unknown and accepted receipts cannot be labelled collected or physically stopped',()=>{
 assert.equal(acquisitionLabel({status:'unknown',parent_ended:true}),'设备动作结果未知');
 assert.match(acquisitionLabel({status:'awaiting_observation',device_action_terminal:false}),/尚未确认结束/);
 assert.match(acquisitionLabel({status:'awaiting_observation',device_action_terminal:true}),/等待合格资料归集/);
});
test('ended parent prevents a pending action without claiming hardware cancellation',()=>assert.equal(acquisitionLabel({status:'authorized',parent_ended:true}),'原目标已结束，不会继续下发'));
test('pending review restores its original decision instead of edited draft choice',()=>{
 assert.equal(restoreDecision({decision:'confirmed',pending:{decision:'rejected'}},reviewChoices),'rejected');
 assert.equal(restoreDecision({decision:'rejected'},reviewChoices),'rejected');
 assert.throws(()=>restoreDecision({pending:{decision:'unknown'}},reviewChoices));
});
test('legacy reason-only actions retain no decision field',()=>assert.equal(restoreDecision({reason:'旧操作'},undefined),''));
test('review body pins original version and input with explicit confirmation or rejection',()=>{
 assert.deepEqual(reviewBody({version:4,input_hash:'original'},'rejected'),{expected_version:4,expected_input_hash:'original',decision:'rejected'});
 assert.throws(()=>reviewBody({version:4,input_hash:'original'},'whatever'));
});

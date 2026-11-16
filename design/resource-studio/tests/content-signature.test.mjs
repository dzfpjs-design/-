import {test} from 'node:test';
import assert from 'node:assert/strict';
import {stableStringify,sameSignature} from '../src/content-signature.js';
import {saveFlow,flowConfirmed} from '../src/setup-schema.js';
test('flow confirmation survives JSONB key ordering but not semantic edits',()=>{
 const w={version:1,instanceProfile:{domain:'运维',goal:'核查资料'},materials:[],flows:{}};
 const f={trigger:'人工',output:'核查结论',steps:[{id:'s1',name:'检查',purpose:'检查原文',agent:'人工确认',role:'业务负责人',output:'结果',sourceId:'',citation:'人工配置',nextId:'auto',condition:'',elseId:'end'}]};
 const saved=saveFlow(w,'核查',f,true);
 const roundtrip=JSON.parse(stableStringify(saved));
 assert.equal(flowConfirmed(roundtrip,roundtrip.flows['核查']),true);
 roundtrip.flows['核查'].steps[0].role='其他角色';
 assert.equal(flowConfirmed(roundtrip,roundtrip.flows['核查']),false);
});
test('legacy ordered signatures remain compatible and array order remains meaningful',()=>{
 const a=JSON.stringify({b:2,a:{z:1,y:2},steps:[1,2]});
 assert.equal(sameSignature(a,stableStringify(JSON.parse(a))),true);
 assert.equal(sameSignature(a,stableStringify({b:2,a:{z:1,y:2},steps:[2,1]})),false);
});

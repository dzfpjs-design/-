import test from 'node:test';
import assert from 'node:assert/strict';
import {changeBusinesses} from '../src/business-schema.js';

const makeDraft=()=>({businesses:['巡护','灾害'],businessAliases:{},flows:{'巡护':{trigger:'人工发起',steps:[]}},
 resources:[{businesses:['巡护']}],gaps:[{business:'巡护'}],automationInstructions:{'巡护':'原来的自动化说明','灾害':'其他说明'},
 voiceInputs:[{business:'巡护',audio_run_id:'original-audio',request_id:'original-receipt'}]});

test('explicit one-to-one manual rename preserves the note and original voice evidence',()=>{
 const before=makeDraft(),original=structuredClone(before);
 const next=changeBusinesses(before,[{original:'巡护',name:'林区巡护'},{original:'灾害',name:'灾害'}],{allowProtectedManualRename:true});
 assert.deepEqual(before,original);assert.deepEqual(next.businesses,['林区巡护','灾害']);
 assert.equal(next.automationInstructions['林区巡护'],'原来的自动化说明');
 assert.equal(next.automationInstructions['巡护'],'原来的自动化说明');
 assert.deepEqual(next.voiceInputs,original.voiceInputs);assert.deepEqual(next.resources[0].businesses,['林区巡护']);
 assert.equal(next.gaps[0].business,'林区巡护');assert.equal(next.businessAliases['林区巡护'],'巡护');
 assert.deepEqual(next._businessRenameIntent,{from:'巡护',to:'林区巡护'});
});

test('protected deletes, mixed edits and historical name reuse remain blocked',()=>{
 const base=makeDraft();
 assert.throws(()=>changeBusinesses(base,[{original:'灾害',name:'灾害'}],{allowProtectedManualRename:true}));
 assert.throws(()=>changeBusinesses(base,[{original:'巡护',name:'林区巡护'}],{allowProtectedManualRename:true}));
 assert.throws(()=>changeBusinesses({...base,automationInstructions:{...base.automationInstructions,'历史业务':'旧记录'}},
  [{original:'巡护',name:'历史业务'},{original:'灾害',name:'灾害'}],{allowProtectedManualRename:true}));
 assert.throws(()=>changeBusinesses(base,[{original:'巡护',name:'灾害'},{original:'灾害',name:'巡护'}],{allowProtectedManualRename:true}));
});

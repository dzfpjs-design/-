// Explicit Root-provided actual capture only. No fixtures, network, tokens,
// source enumeration or alternative success path. Output is counts by type.
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {pixelNeed as need,pixelUUID} from '../src/pixel-change-v249-protocol.mjs';
import {registrationExact as exact} from '../src/workflow-pixel-registration-v250-protocol.mjs';
import {checkedPixelQuestionOptions,checkedPixelQuestionPreview,checkedPixelQuestionView,checkedPixelQuestionList,checkedPixelQuestionOriginal,checkedPixelQuestionRecovery,checkedPixelQuestionCommandResult,checkedPixelQuestionCommandRecovery} from '../src/pixel-question-v252-protocol.mjs';
if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
need(process.argv.length===3,'Supply one explicit actual HTTP capture path');
const capture=JSON.parse(await readFile(process.argv[2],'utf8'));
need(exact(capture,['schema','required_checks','checks'])&&capture.schema==='pixel-question-real-capture-v252'&&Array.isArray(capture.required_checks)&&capture.required_checks.length>0&&capture.required_checks.every(v=>typeof v==='string'&&v.length>0)&&new Set(capture.required_checks).size===capture.required_checks.length&&Array.isArray(capture.checks)&&capture.checks.length===capture.required_checks.length);
const kinds=new Set(['options','preview','query','list','request_recovery','command_result','command_recovery']),labels=new Set(),types={};
for(const row of capture.checks){
 need(exact(row,['label','kind','scope','parameters','original','value'])&&capture.required_checks.includes(row.label)&&!labels.has(row.label)&&kinds.has(row.kind)&&exact(row.scope,['actor_id','workspace_id','instance_id'])&&Object.values(row.scope).every(pixelUUID));labels.add(row.label);
 const scope=row.scope;
 if(row.kind==='preview'){need(row.original===null&&exact(row.parameters,['input','options']));await checkedPixelQuestionOptions(row.parameters.options,scope);await checkedPixelQuestionPreview(row.value,scope,row.parameters.input,row.parameters.options);}
 else{need(row.parameters===null);if(['request_recovery','command_result','command_recovery'].includes(row.kind)){const p=await checkedPixelQuestionOriginal(row.original,scope);if(row.kind==='request_recovery')await checkedPixelQuestionRecovery(row.value,scope,p);else if(row.kind==='command_result')await checkedPixelQuestionCommandResult(row.value,scope,p);else await checkedPixelQuestionCommandRecovery(row.value,scope,p);}
  else if(row.kind==='query'){let p=null;if(row.original!==null){p=await checkedPixelQuestionOriginal(row.original,scope);need(p.operation==='submit');}await checkedPixelQuestionView(row.value,scope,p);}
  else{need(row.original===null);if(row.kind==='options')await checkedPixelQuestionOptions(row.value,scope);else await checkedPixelQuestionList(row.value,scope);}
 }
 types[row.kind]=(types[row.kind]||0)+1;
}
need(labels.size===capture.required_checks.length);
console.log(JSON.stringify({schema:'pixel-question-real-check-v252',checked:capture.checks.length,types,passed:true}));

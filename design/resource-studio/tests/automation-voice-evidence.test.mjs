import test from 'node:test';
import assert from 'node:assert/strict';
import {voiceEvidenceFor,readVoiceEvidence} from '../src/voice-evidence-contract.mjs';
const ref={audio_run_id:'a',request_id:'r',target:'automation_instruction',business:'巡护',text:'原始文字',saved_at:'2026-01-01',input_sha256:'hash'};
const newer={...ref,request_id:'r2',business:'林区巡护'};
test('explicit alias chain finds original and new receipts without mutation',()=>{
 const cfg={businessAliases:{'森林巡护':'林区巡护','林区巡护':'巡护'},voiceInputs:[ref,newer,{...ref,request_id:'other',business:'灾情'}]};const before=structuredClone(cfg);
 assert.deepEqual(voiceEvidenceFor(cfg,'森林巡护',['森林巡护','灾情']),{refs:[ref,newer],ambiguous:false});assert.deepEqual(cfg,before);
});
test('ambiguous shared names, cycles and malformed state fail closed',()=>{
 for(const cfg of [{businessAliases:{'林区巡护':'巡护'},voiceInputs:[ref]},{businessAliases:{'林区巡护':'巡护','巡护':'林区巡护'},voiceInputs:[ref]},{businessAliases:{'林区巡护':'巡护'},voiceInputs:{}}])
 assert.equal(voiceEvidenceFor(cfg,'林区巡护',['林区巡护','巡护']).ambiguous,true);
 assert.equal(voiceEvidenceFor({businessAliases:{'林区巡护':'巡护','巡护':'林区巡护'},voiceInputs:[ref]},'林区巡护',['林区巡护']).ambiguous,true);
});
test('empty result stays unknown about other historical versions',()=>assert.deepEqual(voiceEvidenceFor({businessAliases:{},voiceInputs:[]},'巡护',['巡护']),{refs:[],ambiguous:false}));
test('read only after authorisation and complete receipt and audio integrity',async()=>{
 let audioCalls=0;const blob=new Blob(['audio']);const impl={readReceipt:async()=>({receipt:{reference:ref}}),readAudio:async()=>{audioCalls++;return blob},hashBytes:async()=> 'hash'};
 assert.equal(await readVoiceEvidence(ref,impl),blob);assert.equal(audioCalls,1);
 await assert.rejects(readVoiceEvidence(ref,{...impl,readReceipt:async()=>{throw Error('403 权限已失效')}}),/403/);assert.equal(audioCalls,1);
 await assert.rejects(readVoiceEvidence(ref,{...impl,readReceipt:async()=>({receipt:{reference:{...ref,text:'被改'}}})}),/不一致/);assert.equal(audioCalls,1);
 await assert.rejects(readVoiceEvidence(ref,{...impl,hashBytes:async()=> 'different'}),/摘要不一致/);
});

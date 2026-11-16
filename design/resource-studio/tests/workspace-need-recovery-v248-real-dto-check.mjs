// Validate Root's explicit actual API capture with production pure decoders.
// No fixture, network, server raw reconstruction or credential output.
// Capture: {schema:'workspace-need-recovery-real-capture-v248',account,
//  instance_id,original,lookup,expected_found,expected_evidence_level?,
//  detail?,required_checks:['lookup',...optional 'detail']}
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {isAbsolute} from 'node:path';
import {webcrypto} from 'node:crypto';
import {needScope,checkedNeedView} from '../src/workspace-need-v246-protocol.mjs';
import {checkedNeedRequestRecovery,needRecoveryMatchesView} from '../src/workspace-need-recovery-v248.mjs';
const path=process.argv[2];assert.ok(path&&isAbsolute(path),'Supply an explicit absolute path to actual captured responses');
const capture=JSON.parse(await readFile(path,'utf8')),allowed=['schema','account','instance_id','original','lookup','expected_found','expected_evidence_level','detail','required_checks'];
assert.ok(capture&&typeof capture==='object'&&Object.keys(capture).every(key=>allowed.includes(key)));
assert.equal(capture.schema,'workspace-need-recovery-real-capture-v248');
assert.equal(typeof capture.expected_found,'boolean');
assert.ok(Array.isArray(capture.required_checks)&&capture.required_checks.length>0&&capture.required_checks.every(key=>['lookup','detail'].includes(key))&&new Set(capture.required_checks).size===capture.required_checks.length);
const scope=needScope(capture.account,capture.instance_id),lookup=await checkedNeedRequestRecovery(capture.lookup,scope,capture.original,webcrypto),checked=['lookup'];
assert.equal(lookup.found,capture.expected_found,'A missing original request remains unknown');
if(capture.expected_evidence_level!==undefined){assert.equal(lookup.found,true);assert.equal(lookup.request.evidence_level,capture.expected_evidence_level);}
if(capture.detail!==undefined){
 assert.equal(lookup.found,true,'Unknown lookup cannot authorize accepting another detail');
 const detail=await checkedNeedView(capture.detail,scope,webcrypto);needRecoveryMatchesView(lookup.request,detail,capture.original);checked.push('detail');
}
for(const key of capture.required_checks)assert.ok(checked.includes(key),'Required actual capture missing: '+key);
console.log(JSON.stringify({checked_count:checked.length,checked_types:checked,found:lookup.found,evidence_level:lookup.request?.evidence_level??null,read_only:true,device_actions:false,cloud_calls:0,status:'verified_captured_contracts_only'}));

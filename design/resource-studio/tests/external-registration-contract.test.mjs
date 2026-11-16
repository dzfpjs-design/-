import test from 'node:test';
import assert from 'node:assert/strict';
import {initialExternalRegistration,buildExternalManifest,sameIdentity,externalLookupPath} from '../src/external-registration-contract.mjs';

const form=()=>({...initialExternalRegistration('tool'),namespace:'external',name:'weather',description:'天气查询',owner:'业务部门',
 endpoint:'https://example.org/api',roles:['business'],read_only:true,external_allowed:true,
 input:[{name:'place',type:'string',required:true}],output:[{name:'value',type:'number',required:true}]});

test('creates a bounded read-only HTTP manifest with explicit agents and data authorization',()=>{
 const m=buildExternalManifest(form());
 assert.equal(m.execution.side_effect,false);assert.equal(m.data_policy.external_allowed,true);
 assert.deepEqual(m.allowed_agent_roles,['business']);assert.deepEqual(m.input_schema.required,['place']);
 assert.equal(m.input_schema.properties.place.type,'string');assert.equal(m.budget.max_attempts,1);
 assert.match(externalLookupPath(m),/namespace=external/);
});
test('rejects credentials, privileged side effects, malformed fields and missing consent',()=>{
 for(const changes of [{endpoint:'https://name:secret@example.org/'},{endpoint:'https://example.org/?token=secret'},
  {secret_ref:'a-secret-value'},{read_only:false},{external_allowed:false},{roles:[]},
  {input:[{name:'__proto__',type:'string',required:true}]}])assert.throws(()=>buildExternalManifest({...form(),...changes}));
 assert.throws(()=>buildExternalManifest({...form(),adapter:'mcp',tool_name:''}),/工具名称/);
});
test('recovery accepts only exact manifest in the authorized workspace',()=>{
 const m=buildExternalManifest(form()),row={workspace_id:'w1',namespace:m.namespace,name:m.name,release_version:m.version,kind:m.kind,data:m};
 assert.ok(sameIdentity(row,m,'w1'));assert.equal(sameIdentity(row,m,'w2'),false);
 assert.equal(sameIdentity({...row,data:{...m,description:'不同内容'}},m,'w1'),false);
});

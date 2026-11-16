import test from 'node:test';
import assert from 'node:assert/strict';
import {newMQTTDraft,mqttBody,gatewayDraft,receiptStatus,numericObjectOptions,localRuleOptions,mqttIssue,referenceInputAllowed,authorizationReviewProblem} from '../src/mqtt-ui-policy.mjs';
import {withoutSecrets} from '../src/catalog.js';
const draft=()=>({...newMQTTDraft(),name:'North sensor',broker:'mqtt://127.0.0.1:1883',topics:[{topic:'north/data',device:'one'}],measurements:[{input:'t',attribute:'air_temperature',unit:'C'}]});
test('credential references reject plaintext before the autosave journal sees it',()=>{
 for(const value of ['','G','GA_M','GA_MQTT_','GA_MQTT_SITE_PASS'])assert.equal(referenceInputAllowed(value),true);
 for(const value of ['actual-password','ordinary-user','sk-abcdef123456','GA_MQTT_a',null])assert.equal(referenceInputAllowed(value),false);
});
test('save retry keeps one request ID and drafts retain references rather than credentials',()=>{
 const d=draft();d.accessLoginRef='GA_MQTT_SITE_USER';d.accessKeyRef='GA_MQTT_SITE_PASS';
 const journal=withoutSecrets({mqttGatewayDrafts:{new:d}}).mqttGatewayDrafts.new;
 assert.deepEqual(mqttBody(journal),mqttBody(d));assert.equal(mqttBody(journal).request_id,d.requestId);
 assert.equal(mqttBody(journal).configuration.password_ref,'GA_MQTT_SITE_PASS');
 journal.accessKeyRef='plaintext';assert.throws(()=>mqttBody(journal));
});
test('inline credentials, broad topics and ambiguous device declarations cannot be saved',()=>{
 for(const patch of [{broker:'mqtt://user:pass@host:1883'},{topics:[{topic:'north/#',device:'one'}]},
  {topics:[{topic:'a',device:'one'},{topic:'b',device:'one'}]},{topics:[{topic:'a',device:'one'},{topic:'a',device:'two'}]}])
  assert.throws(()=>mqttBody({...draft(),...patch}));
});
test('source measurements stay independent of later business schema and never overwrite',()=>{
 const d=draft();assert.deepEqual(mqttBody(d).configuration.mapping,{t:{attribute:'air_temperature',unit:'C'}});
 assert.throws(()=>mqttBody({...d,measurements:[...d.measurements,{input:'other',attribute:'air_temperature',unit:'C'}]}));
});
test('editing server connection carries original CAS version rather than new creation request',()=>{
 const d=draft(),body=mqttBody(d),r={name:d.name,version:7,configuration:body.configuration};
 const edited=mqttBody(gatewayDraft(r));assert.equal(edited.expected_version,7);assert.equal(edited.request_id,undefined);
});
test('authorization requires review of saved source and current version, including normalized legacy selectors',()=>{
 const d=draft(),body=mqttBody(d),r={name:d.name,version:7,configuration:{...body.configuration,fields:{}}},saved=gatewayDraft(r);
 assert.equal(authorizationReviewProblem(saved,r),'');
 assert.match(authorizationReviewProblem({...saved,broker:'mqtt://127.0.0.1:1999'},r),/尚未提交/);
 assert.match(authorizationReviewProblem(saved,{...r,version:8}),/版本已变化/);
 const altered={...r,configuration:{...r.configuration,topics:{'other/data':'two'}}};
 assert.match(authorizationReviewProblem(saved,altered),/尚未提交/);
});
test('quarantined and failed records cannot appear successfully used or business verified',()=>{
 assert.match(receiptStatus({status:'quarantined',processing:{status:'processed'}}),/待核查/);
 assert.match(receiptStatus({status:'submitted',processing:{status:'failed'}}),/原件保留/);
 assert.match(receiptStatus({status:'awaiting_object_binding',data_received:true}),/等待关联/);
 assert.match(mqttIssue('missing_capture_time'),/采集时间/);assert.match(mqttIssue('unit_conflict:air_temperature'),/air_temperature/);
});
test('object selection requires published numeric business schema',()=>{
 const cap={id:'pkg',kind:'domain_package',status:'published',data:{specification:{entity_schemas:{site:{properties:{temperature:{type:'number',title:'Temperature',unit:'C'},name:{type:'string'}}}}}}};
 const entity={id:'obj',schema_capability_id:'pkg',entity_type:'site',external_id:'one',data:{name:'Site one'}};
 assert.deepEqual(numericObjectOptions([entity],[cap]),[{id:'obj',label:'Site one',package_id:'pkg',entity_type:'site',fields:[{key:'temperature',label:'Temperature',unit:'C'}]}]);
 assert.deepEqual(numericObjectOptions([entity],[{...cap,status:'draft'}]),[]);
});
test('rules with cloud, unapproved handlers or unpublished contracts cannot be chosen',()=>{
 const c={kind:'tool',status:'published',data:{execution:{adapter:'builtin',handler:'numeric_rules'},allowed_agent_roles:['perception'],data_policy:{cloud_allowed:false}}};
 assert.deepEqual(localRuleOptions([c]),[c]);
 assert.deepEqual(localRuleOptions([{...c,status:'draft'},{...c,data:{...c.data,data_policy:{cloud_allowed:true}}},{...c,data:{...c.data,execution:{adapter:'http'}}}]),[]);
});

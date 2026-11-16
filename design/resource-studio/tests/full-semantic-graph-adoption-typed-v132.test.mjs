// Synthetic transport contracts; Root verifies actual Python/PG journeys.
import test from 'node:test';
import assert from 'node:assert/strict';
import {graphFixture} from './full-semantic-graph-adoption-fixture.mjs';
import {sourceAdoptionFixture} from './full-semantic-graph-adoption-v4-fixture.mjs';
import {FullSemanticGraphAdoptionController,checkedGraphAdoptionPending,verifyGraphAdoptionPreview} from '../src/full-semantic-graph-adoption-controller.mjs';
import {sameGraphTyped,sameGraphWire} from '../src/full-semantic-graph-adoption-protocol.mjs';
import {applyNumericEdit,updateGraphDraft,buildGraphPreviewCommand,collectGraphEditorIssues,abilityPin} from '../src/full-semantic-graph-editor-model.mjs';
import {graphWireParseCanonical,graphWireRequestJSON,graphWireCanonical,graphWireTypedCopy} from '../src/full-semantic-graph-wire.mjs';
import {freshNumber,isTypedNumber} from '../src/typed-numbers-v132.mjs';

const posts=f=>f.calls.filter(call=>call.path.endsWith('/adopt'));
const mirror=value=>JSON.parse(JSON.stringify(value));
const accept=f=>async(_scope,settle)=>settle(mirror(f.saved));
function numericDraft(f,c,text){
 let value=f.ready(c);value=updateGraphDraft(value,d=>{
  d.graph.predicates.numeric_rule={op:'eq',left:{op:'value',kind:'number',value:Number(text)},right:{op:'value',kind:'number',value:Number(text)}};
  d.graph.nodes.find(node=>node.kind==='human').guards.precondition='numeric_rule';
  const row=d.owner_bindings.find(owner=>owner.owner_path==='/domain');row.resolution='typed';row.reason='明确采用本工作区数值判断；仍不认证原资料事实。';
  row.clauses[0].targets=[...row.clauses[0].targets,{kind:'predicate',id:'numeric_rule'}];
 });
 for(const side of ['left','right'])value=applyNumericEdit(value,{key:'predicate:numeric_rule/'+side,text,kind:'number',label:'数值判断'},d=>d);
 return value;
}

test('V132 both fresh graph families preserve numeric text through preview, hash, unknown response and original GET',async t=>{
 for(const family of ['v3','v4'])for(const raw of ['-0.0','1.0','1e21'])await t.test(family+' '+raw,async()=>{
  const f=family==='v3'?await graphFixture({typedNumbers:true}):await sourceAdoptionFixture(),c=await f.open();c.edit(numericDraft(f,c,raw));
  const draftBytes=f.storage.getItem(c.key);await c.makePreview();
  const sent=f.calls.find(call=>call.path.endsWith('/preview'));assert.ok(sent.body.includes('"value":'+raw));
  assert.equal(c.preview.command,c.preview.value.command);assert.equal(isTypedNumber(c.preview.command.graph.predicates.numeric_rule.left.value),false);
  assert.equal(f.storage.getItem(c.key),draftBytes);f.mode.after=true;await assert.rejects(c.submit(true),/保存后响应丢失/);
  const p=c.readPending(),exact=p.exact_body,local=f.storage.getItem(c.outboxKey);
  assert.equal(p.schema,'full-semantic-graph-adoption-pending-v3');assert.equal(p.family,family);assert.ok(exact.includes('"value":'+raw));
  assert.equal(posts(f).length,1);assert.equal(posts(f)[0].body,exact);
  for(const call of f.calls.filter(call=>call.path.endsWith('/command-hash')))assert.ok(call.body.includes('"value":'+raw));
  c.dispose();f.mode.after=false;
  const next=new FullSemanticGraphAdoptionController({...f.options,api:f.api||f.options.api,contextFamily:family==='v3'?'v4':'v3'});
  await next.recover();assert.ok(next.pending.receipt);assert.equal(next.pending.exact_body,exact);assert.equal(posts(f).length,1);
  assert.equal(JSON.parse(local).receipt,null);await next.acceptCurrent(accept(f));assert.equal(next.pending,null);
  const archived=JSON.parse(f.storage.getItem(next.prefix+'accepted:'+p.body.request_id));assert.equal(archived.exact_body,exact);assert.equal(archived.schema,'full-semantic-graph-adoption-pending-v3');
 });
});

test('V132 a fresh v3 revision retains the parent float and only replaces explicitly edited numeric text',async()=>{
 const f=await graphFixture({typedNumbers:true}),c=await f.open();c.edit(numericDraft(f,c,'-0.0'));await c.makePreview();await c.submit(true);
 const first=c.pending,firstBytes=first.exact_body;await c.acceptCurrent(accept(f));await c.load();c.chooseBusiness('通用资料确认');
 const draft=applyNumericEdit(c.state.value,{key:'predicate:numeric_rule/left',text:'1.0',kind:'number',label:'新判断左值'},d=>updateGraphDraft(d,next=>{next.graph.predicates.numeric_rule.left.value=1;}));
 c.edit(draft);await c.makePreview();assert.equal(c.preview.kind,'revision');assert.equal(c.preview.family,'v3');
 const sent=f.calls.filter(call=>call.path.endsWith('/preview')).at(-1);assert.ok(sent.body.includes('"left":{"kind":"number","op":"value","value":1.0}'));assert.ok(sent.body.includes('"right":{"kind":"number","op":"value","value":-0.0}'));
 await c.submit(true);assert.equal(c.pending.schema,'full-semantic-graph-adoption-pending-v3');assert.equal(c.pending.receipt.receipt.parent_id,first.receipt.origin.adoption_id);
 assert.equal(JSON.parse(f.storage.getItem(c.prefix+'accepted:'+first.body.request_id)).exact_body,firstBytes);
});

test('V132 typed preview comparison rejects normalized int/float and sign even when every synthetic seal is rebuilt',async()=>{
 const f=await graphFixture({typedNumbers:true}),c=await f.open();c.edit(numericDraft(f,c,'1.0'));
 const command=buildGraphPreviewCommand(c.state.value,c.context,c.abilities,{typedNumbers:true}),raw=graphWireRequestJSON(command);
 const changed=graphWireParseCanonical(raw.replaceAll('"value":1.0','"value":1')),preview=await f.preview(changed);
 await assert.rejects(verifyGraphAdoptionPreview(preview,command,'root',{scope:f.scope,context:c.context,members:f.members}));
 assert.equal(sameGraphTyped({value:freshNumber('1.0')},graphWireParseCanonical('{"value":1.0}')),true);
 assert.equal(sameGraphTyped({value:freshNumber('1.0')},graphWireParseCanonical('{"value":1}')),false);
 assert.equal(sameGraphTyped({value:freshNumber('-0.0')},graphWireParseCanonical('{"value":0.0}')),false);
 assert.throws(()=>sameGraphWire({value:freshNumber('1.0')},graphWireParseCanonical('{"value":1.0}')));
 assert.equal(sameGraphWire(graphWireParseCanonical('{"value":1.0}'),graphWireParseCanonical('{"value":1.0}')),true);
 assert.equal(isTypedNumber(graphWireTypedCopy(graphWireParseCanonical('{"value":1.0}')).value),true);
});

test('V132 typed pending keeps exact bytes when a same-valued mirror or schema marker is tampered',async()=>{
 const f=await graphFixture({typedNumbers:true}),c=await f.open();c.edit(numericDraft(f,c,'1.0'));await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true),/发送前断线/);
 const original=f.storage.getItem(c.outboxKey),p=JSON.parse(original);
 for(const change of [value=>value.family='v4',value=>value.body.graph.predicates.numeric_rule.left.value=true,value=>value.schema='full-semantic-graph-adoption-pending-v1']){
  const bad=mirror(p);change(bad);assert.throws(()=>checkedGraphAdoptionPending(bad,f.scope));
 }
 const normalized=mirror(p);normalized.exact_body=normalized.exact_body.replaceAll('"value":1.0','"value":1');f.storage.setItem(c.outboxKey,JSON.stringify(normalized));
 await assert.rejects(c.recover());assert.equal(posts(f).length,1);assert.equal(f.storage.getItem(c.outboxKey),JSON.stringify(normalized));
 f.storage.setItem(c.outboxKey,original);c.dispose();const next=await f.open();assert.equal(await next.recover(),false);assert.equal(f.storage.getItem(next.outboxKey),original);
 f.mode.before=false;await next.recover(true);assert.equal(posts(f)[1].body,p.exact_body);assert.ok(next.pending.receipt);
});

test('V132 ability options require the canonical root adapter and do not fall back to a normal JSON list',async()=>{
 const f=await graphFixture({typedNumbers:true}),base='/setup/full-semantic-graph-wire/'+f.scope.handoff_id;
 const cap={id:crypto.randomUUID(),name:'纯合成合同',content_hash:'a'.repeat(64),release_version:'1.0.0',max_attempts:1,input_schema:graphWireParseCanonical('{"maximum":1e+21,"minimum":-0.0,"type":"number"}'),output_schema:graphWireParseCanonical('{"const":1.0}')};
 f.abilities.push(cap);const c=await f.open();assert.equal(graphWireCanonical(c.abilities[0].input_schema),'{"maximum":1e+21,"minimum":-0.0,"type":"number"}');
 assert.ok(f.calls.some(call=>call.path===base+'/ability-options'));assert.equal(f.calls.some(call=>call.path==='/workspace/ability-options'),false);
 const api=async(path,options)=>path===base+'/ability-options'?f.abilities:f.options.api(path,options);
 await assert.rejects(f.open({api}));assert.equal(posts(f).length,0);
});

test('V132 choosing an adopted v4 business restores its original numeric ability schemas before validation',async()=>{
 const f=await sourceAdoptionFixture(),initial=await f.open(),H='c'.repeat(64);
 const cap={id:crypto.randomUUID(),name:'合成数值合同能力',content_hash:H,release_version:'1.0.0',max_attempts:1,adapter:'builtin',agents:['业务智能体'],external:false,fixed_bindings:{},
  input_schema:graphWireParseCanonical('{"additionalProperties":false,"properties":{"reading":{"maximum":1e+21,"minimum":-0.0,"type":"number"}},"required":[],"type":"object"}'),
  output_schema:graphWireParseCanonical('{"additionalProperties":false,"properties":{"result":{"minimum":1.0,"type":"number"}},"required":["result"],"type":"object"}')};
 const draft=updateGraphDraft(f.ready(initial),d=>{
  d.graph.nodes.push({id:'b',kind:'ability',source_step_id:null,actor_id:f.member.id,guards:{start:null,precondition:null,stop:null,exception:null},execution:abilityPin(cap,'business')});
  d.graph.edges.find(edge=>edge.from==='wait_1'&&edge.on==='success').to='b';
  d.graph.edges.push({id:'after_b',from:'b',to:'end_1',on:'success',branch_id:null,loop_id:null});d.node_actions.b='observe';
  d.owner_bindings.find(row=>row.owner_path==='/domain').clauses.find(clause=>clause.source_field==='value').targets.push({kind:'node',id:'b'});
 });
 // Build a complete synthetic frozen editor DTO, not a DB-authority claim.
 // No selectAbility call is made after chooseBusiness to repair the old pin.
 const command=graphWireParseCanonical(graphWireRequestJSON(buildGraphPreviewCommand(draft,initial.context,[cap],{typedNumbers:true}))),preview=await f.preview(command);
 const {binding_hash:oldHash,base_binding_hash:oldBase,event_source_bindings,event_admission_policy,...base}=preview.binding;
 const v3=await f.seal({...base,schema:'report-full-semantic-graph-binding-v3',capability_pins:{b:command.graph.nodes.find(node=>node.id==='b').execution}},'binding_hash');
 const {binding_hash:baseHash,...common}=v3,binding=await f.seal({...common,schema:'report-full-semantic-graph-binding-v4',base_binding_hash:baseHash,event_source_bindings,event_admission_policy},'binding_hash');
 const id=crypto.randomUUID(),origin={schema:'report-full-semantic-graph-origin-v4',adoption_id:id,handoff_id:f.scope.handoff_id,business_name:command.business_name,binding_hash:binding.binding_hash,command_hash:H};
 const receipt=await f.seal({id,...f.scope,request_id:crypto.randomUUID(),command_hash:H,preview_hash:preview.preview_hash,accepted_revision:f.context.configuration.revision,configuration_hash:f.context.configuration.hash,record_hash:H},'receipt_hash');
 f.context.adoptions.push({business_name:command.business_name,origin,receipt,binding,root_id:id,parent_id:null,is_current:true,execution_scope:{node_actions:command.node_actions,external_node_ids:command.external_node_ids}});
 initial.dispose();const optionPath='/setup/full-semantic-graph-wire/'+f.scope.handoff_id+'/ability-options';
 const api=async(path,options)=>{
  if(path!==optionPath)return f.api(path,options);
  const canonical=graphWireCanonical([cap]);
  // Match Response.json(): parsing the server's original text keeps -0.
  // A JSON stringify round trip would manufacture an inconsistent envelope.
  return {schema:'report-full-semantic-graph-canonical-wire-v1',canonical,value:JSON.parse(canonical)};
 };
 const c=await f.open({api});c.chooseBusiness(command.business_name);
 assert.deepEqual(c.issues(),[]);assert.equal(Object.is(c.state.value.graph.nodes.find(node=>node.id==='b').execution.input_schema.properties.reading.minimum,-0),true);
 const storageMirror=mirror(c.state.value),bytes=JSON.stringify(storageMirror);
 assert.equal(Object.is(storageMirror.graph.nodes.find(node=>node.id==='b').execution.input_schema.properties.reading.minimum,0),true);
 assert.deepEqual(collectGraphEditorIssues(storageMirror,c.context,c.abilities,c.members),[]);assert.equal(JSON.stringify(storageMirror),bytes);
 c.edit(storageMirror);assert.deepEqual(c.issues(),[]);
 const built=buildGraphPreviewCommand(c.state.value,c.context,c.abilities,{typedNumbers:true}),request=graphWireRequestJSON(built);
 assert.ok(request.includes('"maximum":1e+21,"minimum":-0.0'));assert.ok(request.includes('"minimum":1.0'));
 assert.equal(request.includes('"minimum":0.0'),false);assert.equal(posts(f).length,0);
 // A genuinely changed contract remains rejected, including same-valued
 // zero-sign and integer/float differences; the pin is never reselected.
 for(const input_schema of [
  graphWireParseCanonical('{"additionalProperties":false,"properties":{"reading":{"maximum":1e+21,"minimum":0.0,"type":"number"}},"required":[],"type":"object"}'),
  graphWireParseCanonical('{"additionalProperties":false,"properties":{"reading":{"maximum":1e+20,"minimum":-0.0,"type":"number"}},"required":[],"type":"object"}')]){
  assert.ok(collectGraphEditorIssues(storageMirror,c.context,[{...cap,input_schema}],c.members).some(issue=>issue.path==='graph.nodes.b'&&issue.message.includes('原能力版本或合同已变化')));
 }
 const output_schema=graphWireParseCanonical('{"additionalProperties":false,"properties":{"result":{"minimum":1,"type":"number"}},"required":["result"],"type":"object"}');
 assert.ok(collectGraphEditorIssues(storageMirror,c.context,[{...cap,output_schema}],c.members).some(issue=>issue.path==='graph.nodes.b'&&issue.message.includes('原能力版本或合同已变化')));
 const bad=mirror(storageMirror);bad.ui.number_tokens_v132['/nodes/999/execution/input_schema/minimum']='-0.0';
 assert.ok(collectGraphEditorIssues(bad,c.context,c.abilities,c.members).some(issue=>issue.path==='draft'));assert.equal(JSON.stringify(storageMirror),bytes);
});

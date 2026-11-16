import test from 'node:test';
import assert from 'node:assert/strict';
import {runtimeDocumentSourceInputs,taskInputForm,createTaskInputDraft,buildTaskGraphInputs} from '../src/graph-task-inputs.mjs';

const marker='x-runtime-document-source-set-v207';
const source=()=>({type:'object',properties:{source_set_id:{type:'string',pattern:'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},source_set_hash:{type:'string',pattern:'^[0-9a-f]{64}$'}},required:['source_set_id','source_set_hash'],additionalProperties:false,[marker]:true,title:'核查资料'});
const scope={actor_id:'actor',workspace_id:'workspace',instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:'a'.repeat(64)};
function metadata(){const schema={type:'object',properties:{documents:source(),note:{type:'string',title:'核查问题'}},required:['documents','note'],additionalProperties:false};return {schema:'workflow-graph-task-inputs-v1',instance_id:'instance',release_id:'release',business_id:'business',configuration_hash:scope.configuration_hash,mode:'graph',hash:'b'.repeat(64),graph_hash:'c'.repeat(64),binding_hash:'d'.repeat(64),input_schema:structuredClone(schema),user_input_schema:schema,entity_fields:[],system_fields:[],execution_granted:false};}

test('only an exact source port is deferred until the Task exists; other required inputs retain their gate',()=>{
 const meta=metadata(),before=structuredClone(meta),model=taskInputForm(meta,scope);
 assert.equal(model.supported,true);assert.deepEqual(model.sourceInputs,[{key:'documents',label:'核查资料',required:true}]);
 assert.deepEqual(model.fields.map(f=>f.key),['note']);assert.deepEqual(meta,before);
 const draft=createTaskInputDraft(meta,scope);assert.deepEqual(Object.keys(draft.values),['note']);
 assert.throws(()=>buildTaskGraphInputs(meta,scope,draft,[]));draft.values.note={mode:'value',raw:'需要核查什么'};
 assert.deepEqual(buildTaskGraphInputs(meta,scope,draft,[]),{note:'需要核查什么'});
});
test('a source marker cannot hide malformed or permissive object schemas',()=>{
 for(const change of [spec=>{spec[marker]=false;},spec=>{spec.additionalProperties=true;},spec=>{delete spec.properties.source_set_hash;},spec=>{spec.required=['source_set_id'];},spec=>{spec.default={};}]){
  const meta=metadata();change(meta.user_input_schema.properties.documents);assert.throws(()=>taskInputForm(meta,scope));
 }
 const meta=metadata();delete meta.user_input_schema.properties.documents[marker];assert.equal(taskInputForm(meta,scope).supported,false);
});
test('a source port cannot also supply an entity or a server fixed field',()=>{
 const entity=metadata();entity.entity_fields=[{field:'documents',label:'非法关联',required:true,waits:[]}];assert.throws(()=>taskInputForm(entity,scope));
 const system=metadata();system.system_fields=['documents'];assert.throws(()=>taskInputForm(system,scope));
});
test('source input is not an editable manual JSON field and cannot be smuggled into a creation draft',()=>{
 const meta=metadata(),draft=createTaskInputDraft(meta,scope);draft.values.documents={mode:'value',raw:'{}'};
 assert.throws(()=>buildTaskGraphInputs(meta,scope,draft,[]));
 assert.deepEqual(runtimeDocumentSourceInputs({properties:{plain:{type:'object'}}}),[]);
});
test('typed-number creation retains the same precise source deferral without mutating metadata',()=>{
 const meta=metadata(),before=JSON.stringify(meta),draft=createTaskInputDraft(meta,scope,{typedNumbers:true});draft.values.note={mode:'value',raw:'原问题'};
 assert.deepEqual(buildTaskGraphInputs(meta,scope,draft,[],{typedNumbers:true}),{note:'原问题'});assert.equal(JSON.stringify(meta),before);
});

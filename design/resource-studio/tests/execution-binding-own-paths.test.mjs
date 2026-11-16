import test from 'node:test';
import assert from 'node:assert/strict';
import {setAt,bindingRequestJSON,newBindings,ensureNode} from '../src/business-flow-execution-binding-fields.mjs';
test('legitimate technical identifiers use own data properties without traversing object prototypes',()=>{
 const source={nodes:{}};
 for(const name of ['constructor','prototype','__proto__']){
  const result=setAt(source,['nodes',name,'actor_id'],'actual-user');
  assert.equal(Object.hasOwn(result.nodes,name),true);assert.equal(result.nodes[name].actor_id,'actual-user');
  assert.deepEqual(source,{nodes:{}});assert.equal(Object.prototype.actor_id,undefined);
 }
 const nested=setAt({},['constructor','prototype','polluted'],true);
 assert.equal(nested.constructor.prototype.polluted,true);assert.equal(Object.prototype.polluted,undefined);
});
test('numeric binding tokens and source node names remain literal at safe paths',()=>{
 const flow={nodes:[{node_id:'constructor',actor:'human',role:'prototype',inputs:[],outputs:[]}]};
 const original=newBindings(flow),bound=ensureNode(original,flow.nodes[0]);
 const next=setAt(bound,['nodes','constructor','output_schema','properties','prototype'],{type:'number'});
 assert.equal(next.nodes.constructor.role,'prototype');assert.equal(next.nodes.constructor.output_schema.properties.prototype.type,'number');
 assert.match(bindingRequestJSON(next),/"constructor":/);assert.equal(original.nodes.constructor,null);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {buildObjectAttributes,prepareObjectRequest,objectRequestJSON,objectRequestBody,readObjectRequest,persistObjectRequest,lookupObjectRequest,retireObjectRequest,settleObjectRequest} from '../src/object-request-v132.mjs';
const owner={actor_id:'actor',workspace_id:'workspace',instance_id:'instance'};
const kind={fields:[{key:'name',label:'名称',kind:'文本',required:true},{key:'reading',label:'观测值',kind:'数值'},{key:'count',label:'数量',kind:'整数'}]};
const request=(raw='-0.0',id='original-request-132')=>prepareObjectRequest({release_id:'release',object_type:'site',attributes:buildObjectAttributes(kind,{name:'公开合成对象',reading:raw,count:'1.0'}),reference:'',request_id:id},owner);
class Storage{constructor(){this.map=new Map();this.fail=false;}getItem(k){return this.map.get(k)??null;}setItem(k,v){if(this.fail)throw Error('full');this.map.set(k,String(v));}removeItem(k){this.map.delete(k);}}
const receipt=(action,patch={})=>({schema:'object-request-recovery-v132',...owner,request_id:objectRequestBody(action).request_id,request_hash:'a'.repeat(64),found:true,object:{id:'object',entity_type:'site',version:1,attributes:{}},retirement:null,...patch});
for(const raw of ['-0.0','1.0','1e21'])test('object original text survives persistence and exact lookup/retirement '+raw,async()=>{
 const action=request(raw),storage=new Storage(),exact=objectRequestJSON(action,owner);assert(exact.includes('"reading":'+raw));assert(exact.includes('"count":1'));
 persistObjectRequest(storage,'form',owner,action);const cold=readObjectRequest(storage,'form',owner);assert.equal(objectRequestJSON(cold,owner),exact);
 const calls=[],api=async(path,options)=>{calls.push({path,...options});return receipt(cold,{found:false,object:null,retirement:null});};
 await lookupObjectRequest(api,owner,cold);assert.equal(calls[0].body,exact);
 await retireObjectRequest(api,owner,cold,'停止原登记');assert(calls[1].body.includes('"original":'+exact));assert.equal(JSON.parse(calls[1].body).original.request_id,action.body.request_id);
});
for(const raw of ['-','1e','9007199254740993','NaN'])test('invalid/unsafe object input blocks request '+raw,()=>assert.throws(()=>request(raw)));
test('object integer checks original decimal rather than rounded Number',()=>{
 for(const count of ['1.0000000000000001','9007199254740991.1','-0.0'])assert.throws(()=>buildObjectAttributes(kind,{name:'公开对象',count}));
 assert.throws(()=>buildObjectAttributes(kind,{reading:'1'}),/名称/);
});
test('another actor, altered mirror or malformed slot never becomes an original',()=>{
 const action=request(),storage=new Storage();persistObjectRequest(storage,'form',owner,action);
 assert.throws(()=>readObjectRequest(storage,'form',{...owner,actor_id:'other'}));
 const changed=JSON.parse(JSON.stringify(action));changed.body.attributes.reading=1;assert.throws(()=>objectRequestJSON(changed,owner));
 storage.setItem('form:object-create-v132','{broken');assert.throws(()=>readObjectRequest(storage,'form',owner));
});
test('shared pending refuses replacement and failed persistence never confirms saving',()=>{
 const action=request(),storage=new Storage();persistObjectRequest(storage,'form',owner,action);
 assert.throws(()=>persistObjectRequest(storage,'form',owner,request('1.0','another-original-132')));assert.equal(readObjectRequest(storage,'form',owner).exact_body,action.exact_body);
 storage.fail=true;assert.throws(()=>persistObjectRequest(storage,'new-form',owner,action));assert.equal(storage.getItem('new-form:object-create-v132'),null);
});
test('settlement preserves original receipt and tolerates later legitimate object changes',()=>{
 const action=request(),storage=new Storage(),first=receipt(action);persistObjectRequest(storage,'form',owner,action);settleObjectRequest(storage,'form',owner,action,first);
 const history='form:object-request-receipt:'+action.body.request_id,original=storage.getItem(history);assert.equal(readObjectRequest(storage,'form',owner),null);
 settleObjectRequest(storage,'form',owner,action,receipt(action,{object:{id:'object',entity_type:'site',version:2,attributes:{reading:1}}}));assert.equal(storage.getItem(history),original);
 assert.throws(()=>settleObjectRequest(storage,'form',owner,action,receipt(action,{object:{id:'foreign',entity_type:'site'}})));assert.equal(storage.getItem(history),original);
});
test('settlement cannot erase another pending original or erase before receipt is saved',()=>{
 const action=request(),storage=new Storage();persistObjectRequest(storage,'form',owner,action);storage.fail=true;
 assert.throws(()=>settleObjectRequest(storage,'form',owner,action,receipt(action)));assert.equal(readObjectRequest(storage,'form',owner).exact_body,action.exact_body);
 storage.fail=false;const other=request('1.0','another-original-132');storage.setItem('form:object-create-v132',JSON.stringify(other));
 assert.throws(()=>settleObjectRequest(storage,'form',owner,action,receipt(action)));assert.equal(readObjectRequest(storage,'form',owner).exact_body,other.exact_body);
});
test('original legacy DTO retains its prior serialization and never upgrades',async()=>{
 const body={release_id:'old',object_type:'site',attributes:{reading:0},reference:'',request_id:'legacy-original-132'},exact=JSON.stringify(body),calls=[];
 await lookupObjectRequest(async(path,options)=>{calls.push(options.body);return receipt(body,{found:false,object:null});},owner,body);assert.deepEqual(calls,[exact]);assert.equal(body.schema,undefined);
});

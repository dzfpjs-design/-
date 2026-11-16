import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {freshNumber,numericValue,numericToken,numericDisplay,isTypedNumber,
 typedRequestJSON,parseTypedJSON,plainTyped,cloneTyped,typedEqual,embedRequestJSON} from '../src/typed-numbers-v132.mjs';

for(const [raw,token]of [['-0.0','-0.0'],['-0','-0.0'],['1.0','1.0'],['1e21','1e21'],['1E+021','1E+021'],['.5','0.5'],['+.5','0.5'],['1.','1.0'],['001.00','1.00'],['1','1']]){
 test('fresh human number retains explicit numeric kind: '+raw,()=>{
  const value=freshNumber(raw);assert(isTypedNumber(value));assert.equal(numericToken(value),token);
  assert.equal(typedRequestJSON({value}),'{'+'"value":'+token+'}');
  assert.equal(numericDisplay(value),raw);assert.throws(()=>JSON.stringify(value));
 });
}
for(const raw of ['9007199254740992','9007199254740993','-9007199254740993','NaN','Infinity','','-','.','1e','1e+','1e309','1,2','1} ,"bad":true']){
 test('invalid/unsafe fresh number never becomes a rounded request: '+raw,()=>assert.throws(()=>freshNumber(raw)));
}
for(const [raw,token]of [['1.0','1'],['10e2','1000'],['.0','0'],['001','1'],['9007199254740991.0','9007199254740991'],['100e-2','1']]){
 test('integer is checked from exact decimal text: '+raw,()=>assert.equal(numericToken(freshNumber(raw,'integer')),token));
}
for(const raw of ['9007199254740991.1','9007199254740990.9','1.0000000000000001','1e-999','-0','-0.0','9007199254740992','1e21']){
 test('fractional, rounded, signed-zero or unsafe integer is rejected: '+raw,()=>assert.throws(()=>freshNumber(raw,'integer')));
}
test('new nested JSON preserves leaf types through clone and JSON wire',()=>{
 assert.equal(cloneTyped(null),null);assert.equal(plainTyped(null),null);
 const input=' { "items": [ -0.0, 1.0, 1e21, { "n": 3 } ], "text":"中\\u6587" } ';
 const value=parseTypedJSON(input),copy=cloneTyped(value);
 assert(typedEqual(value,copy));assert.equal(typedRequestJSON(copy),'{'+'"items":[-0.0,1.0,1e21,{"n":3}],"text":"中文"}');
 const normal=plainTyped(copy);assert(Object.is(normal.items[0],-0));assert.equal(normal.items[2],1e21);
 assert.equal(numericDisplay(-0),'-0.0');
 copy.items[1]=freshNumber('1');assert(!typedEqual(value,copy));
 assert(typedEqual(freshNumber('1.00'),freshNumber('1e0')));
 assert(!typedEqual(freshNumber('1.0'),1));assert(!typedEqual(-0,0));assert(!typedEqual(true,1));
});
for(const text of ['{"a":1,"a":2}','{"__proto__":{},"__proto__":{}}','[1,]','01','-0','9007199254740993','1e999','1true','NaN','"\\ud800"','"\\u0000"']){
 test('strict JSON refuses duplicate or malformed/unsafe original text: '+text.slice(0,45),()=>assert.throws(()=>parseTypedJSON(text)));
}
test('prototype keys are plain own data and never inject object prototype',()=>{
 const value=parseTypedJSON('{"__proto__":{"polluted":true},"constructor":1.0}');
 assert.equal(Object.getPrototypeOf(value),Object.prototype);assert.equal({}.polluted,undefined);
 assert.equal(typedRequestJSON(value),'{"__proto__":{"polluted":true},"constructor":1.0}');
});
test('encoder cannot be tricked by token-shaped objects, getters, unsafe numbers or cycles',()=>{
 assert.equal(typedRequestJSON({raw:'1} ,"bad":true',token:'1e21'}),'{"raw":"1} ,\\"bad\\":true","token":"1e21"}');
 let called=false;const getter=Object.defineProperty({},'n',{enumerable:true,get(){called=true;return 1;}});
 assert.throws(()=>typedRequestJSON(getter));assert.equal(called,false);
 assert.throws(()=>typedRequestJSON({n:1e21}));assert.throws(()=>freshNumber(1e21));
 const cycle={};cycle.self=cycle;assert.throws(()=>typedRequestJSON(cycle));
 assert.throws(()=>typedRequestJSON([,1]));assert.throws(()=>typedRequestJSON({n:undefined}));
 assert.throws(()=>typedRequestJSON({n:NaN}));assert.throws(()=>typedRequestJSON({n:'private\0value'}));
});
test('recovery embeds the stored original subtree bytes without parsing them back to Number',()=>{
 const exact=' {"reading": -0.0, "limit":1e21,"scale":1.0} ';
 const result=embedRequestJSON({operation:'probe',expected_version:4},'original_request',exact);
 assert(result.includes('"original_request":'+exact));assert.equal(result,'{"expected_version":4,"operation":"probe","original_request":'+exact+'}');
 assert.throws(()=>embedRequestJSON({original_request:{}},'original_request',exact));
 assert.throws(()=>embedRequestJSON({},'original_request','{"a":1,"a":2}'));
});
test('actual Python decoder receives original int/float/sign, not JS inferred spelling',()=>{
 const body={negative:freshNumber('-0.0'),float:freshNumber('1.0'),large:freshNumber('1e21'),integer:freshNumber('1','integer')};
 const python=fileURLToPath(new URL('../../../.venv/bin/python',import.meta.url));
 const result=spawnSync(python,['-c','import sys,json,math;v=json.load(sys.stdin);assert type(v["negative"]) is float and math.copysign(1,v["negative"])<0;assert type(v["float"]) is float;assert type(v["large"]) is float and v["large"]==1e21;assert type(v["integer"]) is int;print("typed-python-ok")'],{input:typedRequestJSON(body),encoding:'utf8',timeout:5000,env:{PATH:'/usr/bin:/bin',PYTHONDONTWRITEBYTECODE:'1'}});
 assert.equal(result.status,0,result.stderr);assert.equal(result.stdout.trim(),'typed-python-ok');
});

// Pure numeric wire tests. No browser, server, cache, DraftSync or source grant.
import test from 'node:test';
import assert from 'node:assert/strict';
import {freshNumber,parseTypedJSON,typedRequestJSON} from '../src/typed-numbers-v132.mjs';
import {decodeConfigurationReply,attachConfigurationWire,savedConfigurationWire,configurationWire} from '../src/configuration-numeric-wire.mjs';

const saved=raw=>decodeConfigurationReply('{"revision":7,"configuration":'+raw+',"extra":{"kept":true}}');
const plain=raw=>JSON.parse(raw);

test('reply keeps complete DTO and a nonenumerable original float/negative-zero wire',()=>{
 const result=saved('{"threshold":1.0,"integer":1,"zero":-0.0,"label":"原稿🙂"}');
 assert.equal(result.revision,7);assert.deepEqual(result.extra,{kept:true});
 assert.equal(result.configuration.threshold,1);assert.ok(Object.is(result.configuration.zero,-0));
 assert.equal(Object.getOwnPropertyDescriptor(result,'configuration_wire').enumerable,false);
 assert.equal(JSON.stringify(result).includes('configuration_wire'),false);
 assert.equal(savedConfigurationWire(result),'{"integer":1,"label":"原稿🙂","threshold":1.0,"zero":-0.0}');
 assert.equal(configurationWire(result.configuration,savedConfigurationWire(result)),savedConfigurationWire(result));
});

test('attaching clones both DTO and configuration without mutating the original',()=>{
 const original={revision:1,configuration:{threshold:1,deep:{count:2}}};
 const result=attachConfigurationWire(original,'{"deep":{"count":2.0},"threshold":1.0}');
 assert.notEqual(result,original);assert.notEqual(result.configuration,original.configuration);
 result.configuration.deep.count=3;assert.equal(original.configuration.deep.count,2);
 assert.equal(savedConfigurationWire(original),null);
 assert.throws(()=>savedConfigurationWire(result));
 assert.throws(()=>attachConfigurationWire(original,'{"threshold":9,"deep":{"count":2}}'));
});

test('ordinary edits retain unchanged leaf tokens and serialize changed/new values',()=>{
 const before='{"changed":1.0,"kept":2.0,"nested":{"zero":-0.0},"remove":3.0}';
 const next={changed:4,kept:2,nested:{zero:-0},fresh:5};
 assert.equal(configurationWire(next,before),'{"changed":4,"fresh":5,"kept":2.0,"nested":{"zero":-0.0}}');
 assert.equal(configurationWire({n:1},'{"n":true}'),'{"n":1}');
 assert.equal(configurationWire({n:false},'{"n":0.0}'),'{"n":false}');
 assert.equal(configurationWire({n:0},'{"n":-0.0}'),'{"n":0}');
 assert.equal(configurationWire({n:freshNumber('1.0')},'{"n":1}'),'{"n":1.0}');
});

test('array reorder carries unique scalar typed values instead of same-position tokens',()=>{
 const baseline='{"items":[1.0,2,3.0]}',next={items:[3,1,2]};
 assert.equal(configurationWire(next,baseline),'{"items":[3.0,1.0,2]}');
 assert.deepEqual(next,{items:[3,1,2]});
});

test('unique own id keeps tokens through object edit, reorder and insertion',()=>{
 const baseline='{"items":[{"id":"first","n":1.0,"name":"旧甲"},{"id":"second","n":2.0,"name":"旧乙"}]}';
 const next={items:[{id:'new',n:9},{id:'second',n:2,name:'改乙'},{id:'first',n:1,name:'旧甲'}]};
 assert.equal(configurationWire(next,baseline),'{"items":[{"id":"new","n":9},{"id":"second","n":2.0,"name":"改乙"},{"id":"first","n":1.0,"name":"旧甲"}]}');
});

test('unique complete mirrors preserve anonymous objects while reordered',()=>{
 assert.equal(configurationWire({items:[{name:'b',n:2},{name:'a',n:1}]},
  '{"items":[{"name":"a","n":1.0},{"name":"b","n":2.0}]}'),
  '{"items":[{"n":2.0,"name":"b"},{"n":1.0,"name":"a"}]}');
});

test('same visible array items with different typed originals are rejected, never guessed',()=>{
 for(const baseline of ['{"items":[1,1.0]}','{"items":[{"n":1},{"n":1.0}]}']){
  const next=plain(baseline),before=JSON.stringify(next);
  assert.throws(()=>configurationWire(next,baseline));assert.equal(JSON.stringify(next),before);
 }
 assert.throws(()=>configurationWire({items:[1,1]},'{"items":[1.0]}'));
 assert.equal(configurationWire({items:[1,1]},'{"items":[1]}'),'{"items":[1,1]}');
 assert.equal(configurationWire({items:[1]},'{"items":[1.0,1.0]}'),'{"items":[1.0]}');
});

test('unknown structured array edits cannot silently lose old numeric types',()=>{
 assert.throws(()=>configurationWire({items:[{name:'edited',n:1}]},'{"items":[{"name":"old","n":1.0}]}'));
 assert.throws(()=>configurationWire({items:[{id:'same',name:'changed',n:1}]},
  '{"items":[{"id":"same","name":"a","n":1.0},{"id":"same","name":"b","n":2.0}]}'));
 assert.equal(configurationWire({items:[2]},'{"items":[1.0]}'),'{"items":[2]}');
 assert.equal(configurationWire({items:[]},'{"items":[1.0]}'),'{"items":[]}');
});

test('nested arrays also reject ambiguity after a unique outer id match',()=>{
 assert.throws(()=>configurationWire({items:[{id:'same',values:[1,1]}]},
  '{"items":[{"id":"same","values":[1,1.0]}]}'));
});

test('own hostile-looking names remain data without inherited lookup or prototype mutation',()=>{
 const before=Object.prototype.polluted;
 const baseline='{"constructor":{"prototype":{"n":1.0}},"__proto__":{"polluted":false},"items":[{"id":"constructor","n":2.0}]}';
 const value=JSON.parse(baseline),wire=configurationWire(value,baseline);
 assert.equal(wire,'{"__proto__":{"polluted":false},"constructor":{"prototype":{"n":1.0}},"items":[{"id":"constructor","n":2.0}]}');
 assert.equal(Object.prototype.polluted,before);assert.ok(Object.hasOwn(JSON.parse(wire),'__proto__'));
 const attached=saved(baseline);assert.equal(savedConfigurationWire(attached),wire);
});

test('untrusted sidecars, numeric mirror mismatch and negative-zero mismatch are rejected',()=>{
 assert.throws(()=>savedConfigurationWire({revision:1,configuration:{n:1},configuration_wire:'{"n":1.0}'}));
 assert.throws(()=>attachConfigurationWire({revision:1,configuration:{n:1},configuration_wire:'fake'},'{"n":1}'));
 assert.throws(()=>attachConfigurationWire({revision:1,configuration:{n:0}},'{"n":-0.0}'));
 assert.throws(()=>decodeConfigurationReply('{"revision":1,"configuration":{},"configuration_wire":"fake"}'));
});

test('unsafe integers, duplicate JSON keys, nonfinite and invalid strings fail before mutation',()=>{
 for(const text of ['{"n":9007199254740993}','{"n":1,"n":2}','{"n":1e400}','{"n":"\\u0000"}'])assert.throws(()=>saved(text));
 for(const n of [NaN,Infinity,-Infinity,9007199254740992])assert.throws(()=>configurationWire({n}));
 assert.throws(()=>configurationWire({n:1},'{"n":9007199254740993}'));
});

test('accessors, cycles, exotic prototypes, sparse arrays and symbols are never inspected as data',()=>{
 let reads=0;const getter={};Object.defineProperty(getter,'n',{enumerable:true,get(){reads++;return 1;}});
 assert.throws(()=>configurationWire(getter));assert.equal(reads,0);
 const cycle={};cycle.self=cycle;assert.throws(()=>configurationWire(cycle));
 assert.throws(()=>configurationWire({n:new Date()}));assert.throws(()=>configurationWire(Object.create({n:1})));
 const array=[];array[1]=1;assert.throws(()=>configurationWire({array}));
 const symbol={};symbol[Symbol('not-data')]=1;assert.throws(()=>configurationWire(symbol));
});

test('plain null-prototype objects are supported and outputs are full canonical JSON',()=>{
 const next=Object.create(null);next.n=1;
 assert.equal(configurationWire(next,'{"n":1.0}'),'{"n":1.0}');
 assert.equal(typedRequestJSON(parseTypedJSON(configurationWire(next,'{"n":1.0}'))),'{"n":1.0}');
 assert.throws(()=>decodeConfigurationReply('{"revision":true,"configuration":{}}'));
 assert.throws(()=>decodeConfigurationReply('{"revision":1.0,"configuration":{}}'));
});

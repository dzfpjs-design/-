// Pure invented wire fixtures. Root runs Node and the independent Python bridge.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {verifyGraphWire,graphWireCanonical,graphWireDigest,graphWireRequestJSON,graphWireParseCanonical,
 graphWireClone,graphWireTypedCopy,graphWireNumericTokens,graphWireRestoreNumericTokens,graphWireSetNumberToken} from '../src/full-semantic-graph-wire.mjs';
import {freshNumber,isTypedNumber,numericToken} from '../src/typed-numbers-v132.mjs';

const schema='report-full-semantic-graph-canonical-wire-v1';
const hash=value=>createHash('sha256').update(value,'utf8').digest('hex');
const envelope=canonical=>({schema,value:JSON.parse(canonical),canonical});
const digest=(value,options={})=>graphWireDigest(value,{...options,crypto:webcrypto});

test('real number predicates, decimal schemas and fractional proof boxes keep Python bytes',async()=>{
 const canonical='{"graph":{"predicate":{"value":0.5},"schema":{"minimum":0.5,"type":"number"}},"proof":{"box":[0.5,1.0,0.0,1.0],"scale":1e-05}}';
 const wire=envelope(canonical),value=verifyGraphWire(wire);
 assert.equal(graphWireCanonical(value),canonical);
 assert.equal(await digest(value),hash(canonical));
 assert.equal(await digest(wire.value),hash(canonical));
 assert.equal(graphWireCanonical(value.proof.box),'[0.5,1.0,0.0,1.0]');
 assert.notEqual(JSON.stringify(value),canonical);
});

test('cold integral floats and negative zero remain distinct from integer canonical JSON',async()=>{
 const canonical='{"configuration":{"integer":1,"integral_float":1.0,"negative_zero":-0.0,"zero":0},"revision":3}';
 const value=verifyGraphWire(envelope(canonical));
 assert.equal(value.configuration.integral_float,value.configuration.integer);
 assert.ok(Object.is(value.configuration.negative_zero,-0));
 assert.equal(await digest(value.configuration),hash('{"integer":1,"integral_float":1.0,"negative_zero":-0.0,"zero":0}'));
 assert.equal(await digest(value),hash(canonical));
 assert.notEqual(hash(JSON.stringify(value)),hash(canonical));
});

test('omit a seal directly from its server container including top-level float lexemes',async()=>{
 const body='{"confidence":1.0,"negative_zero":-0.0,"proof":{"box":[0.5,1.0]},"revision":2}';
 const canonical=body.slice(0,-1)+',"seal_hash":"'+hash(body)+'"}';
 const value=verifyGraphWire(envelope(canonical));
 assert.equal(graphWireCanonical(value,{omit:'seal_hash'}),body);
 assert.equal(await digest(value,{omit:'seal_hash'}),value.seal_hash);
 assert.throws(()=>graphWireCanonical(value,{omit:'missing_hash'}));
 assert.throws(()=>graphWireCanonical(value.proof.box,{omit:'0'}));
});

test('a fresh unsealed object reuses unchanged nested server containers',async()=>{
 const body='{"count":1,"proof":{"box":[0.5,1.0],"scale":1e-05}}';
 const canonical=body.slice(0,-1)+',"seal_hash":"'+hash(body)+'"}';
 const value=verifyGraphWire(envelope(canonical));
 const {seal_hash,...without}=value;
 assert.equal(graphWireCanonical(without),body);
 assert.equal(await digest(without),seal_hash);
 const alias={a:value.proof,b:value.proof};
 assert.equal(graphWireCanonical(alias),'{"a":{"box":[0.5,1.0],"scale":1e-05},"b":{"box":[0.5,1.0],"scale":1e-05}}');
});

test('server provenance never follows a mutable deep copy',async()=>{
 const canonical='{"nested":{"n":1.0}}',value=verifyGraphWire(envelope(canonical));
 const copied=JSON.parse(JSON.stringify(value));
 assert.equal(graphWireCanonical(copied),'{"nested":{"n":1}}');
 assert.notEqual(await digest(copied),await digest(value));
 copied.nested.n=2;
 assert.equal(graphWireCanonical(copied),'{"nested":{"n":2}}');
 assert.equal(graphWireCanonical(value),canonical);
 const fractional=verifyGraphWire(envelope('{"nested":{"n":0.5}}'));
 assert.throws(()=>graphWireCanonical(JSON.parse(JSON.stringify(fractional))),/服务器/);
});

test('metadata-bearing trees are immutable and cannot retain stale canonical bytes after edits',()=>{
 const wire=envelope('{"array":[{"n":1.0}],"object":{"n":0.5}}');
 const value=verifyGraphWire(wire);
 assert.ok(Object.isFrozen(value)&&Object.isFrozen(value.array)&&Object.isFrozen(value.array[0]));
 assert.ok(Object.isFrozen(wire.value)&&Object.isFrozen(wire.value.object));
 assert.throws(()=>{value.object.n=9;},TypeError);
 assert.throws(()=>{value.array.push(2);},TypeError);
 assert.throws(()=>Object.defineProperty(wire.value.object,'n',{value:9}),TypeError);
 assert.throws(()=>Object.setPrototypeOf(value,{}),TypeError);
 assert.equal(graphWireCanonical(value.object),'{"n":0.5}');
});

test('Python Unicode codepoint key order survives both wire and local safe integer hashing',async()=>{
 const canonical='{"a":1,"\uE000":2,"🙂":3}';
 const value=verifyGraphWire(envelope(canonical));
 assert.equal(await digest(value),hash(canonical));
 const local={'🙂':3,'\uE000':2,a:1};
 assert.equal(graphWireCanonical(local),canonical);
 assert.equal(await digest(local),hash(canonical));
});

for(const canonical of ['{"a":1,"a":2}','{"a":1,"\\u0061":2}','{"b":1,"a":2}',
 ' {"a":1}','{"a":1} ','{"a": 1}','{"a":1,}','{"a":[1,]}']){
 test('reject duplicate, unordered or noncanonical container syntax: '+canonical,()=>{
  let value;try{value=JSON.parse(canonical);}catch{value={a:1};}
  assert.throws(()=>verifyGraphWire({schema,value,canonical}));
 });
}

for(const canonical of ['{"key":"\\ud800"}','{"\\udfff":1}','{"key":"\\ud800x"}']){
 test('reject unsafe surrogate strings: '+canonical,()=>assert.throws(()=>verifyGraphWire(envelope(canonical))));
}

test('reject mismatched DTO data, types, sign, hidden properties and unexpected envelope fields',()=>{
 for(const value of [{a:2},{a:'1'},{a:true},{b:1}])assert.throws(()=>verifyGraphWire({schema,value,canonical:'{"a":1}'}));
 assert.throws(()=>verifyGraphWire({schema,value:{a:0},canonical:'{"a":-0.0}'}));
 const extra={a:1};Object.defineProperty(extra,'hidden',{value:1});
 assert.throws(()=>verifyGraphWire({schema,value:extra,canonical:'{"a":1}'}));
 assert.throws(()=>verifyGraphWire({...envelope('{"a":1}'),extra:true}));
 assert.throws(()=>verifyGraphWire({schema,value:1,canonical:'1'}));
});

test('descriptor checks reject getters without invoking them',()=>{
 let called=false;const value={};Object.defineProperty(value,'a',{enumerable:true,get(){called=true;return 1;}});
 assert.throws(()=>verifyGraphWire({schema,value,canonical:'{"a":1}'}));
 assert.equal(called,false);
 const wire={schema,canonical:'{"a":1}'};Object.defineProperty(wire,'value',{enumerable:true,get(){called=true;return {a:1};}});
 assert.throws(()=>verifyGraphWire(wire));assert.equal(called,false);
});

test('prototype-looking names stay own JSON data without prototype mutation',()=>{
 const canonical='{"__proto__":{"safe":1},"constructor":"ordinary data"}';
 const value=verifyGraphWire(envelope(canonical));
 assert.equal(Object.getPrototypeOf(value),Object.prototype);
 assert.ok(Object.hasOwn(value,'__proto__'));
 assert.equal(value.__proto__.safe,1);
 assert.equal({}.safe,undefined);
 assert.equal(graphWireCanonical(value),canonical);
});

test('nonfinite tokens and rounded server integers fail closed',()=>{
 for(const canonical of ['{"n":NaN}','{"n":Infinity}','{"n":1e+999}',
  '{"n":9007199254740993}','{"n":-0}']){
  assert.throws(()=>verifyGraphWire({schema,value:{n:0},canonical}));
 }
 const value=verifyGraphWire(envelope('{"n":1e+20}'));
 assert.equal(graphWireCanonical(value),'{"n":1e+20}');
});

test('local new commands permit safe integer JSON only and require the actual server hash for floats',async()=>{
 assert.equal(await digest({attempts:2,zero:0}),hash('{"attempts":2,"zero":0}'));
 await assert.rejects(()=>digest({attempts:2,zero:-0}),/服务器/);
 assert.equal(graphWireRequestJSON({attempts:2,zero:-0}),'{"attempts":2,"zero":-0.0}');
 for(const value of [0.5,0.00001,1e-7,Number.NaN,Infinity,9007199254740992]){
  assert.throws(()=>graphWireCanonical({value}),/服务器/);
 }
 assert.throws(()=>graphWireCanonical({value:undefined}));
 assert.throws(()=>graphWireCanonical({value:1n}));
 const cyclic={};cyclic.self=cyclic;assert.throws(()=>graphWireCanonical(cyclic));
});

test('request encoding keeps frozen numeric source bytes while accepting new finite local fractions',()=>{
 const source=verifyGraphWire(envelope('{"minimum":1.0,"negative_zero":-0.0,"scale":1e-05}'));
 const request={source,timeout:60.5,threshold:1e-7};
 const encoded=graphWireRequestJSON(request);
 assert.equal(encoded,'{"source":{"minimum":1.0,"negative_zero":-0.0,"scale":1e-05},"threshold":1e-7,"timeout":60.5}');
 assert.equal(graphWireCanonical(source),'{"minimum":1.0,"negative_zero":-0.0,"scale":1e-05}');
 assert.throws(()=>graphWireCanonical(request),/服务器/);
 assert.throws(()=>graphWireCanonical(JSON.parse(encoded)),/服务器/);
});

test('request encoding rejects unsafe locals without falling back over frozen source trees',()=>{
 for(const value of [Infinity,NaN,9007199254740992,undefined,1n,new Date(0)])assert.throws(()=>graphWireRequestJSON({value}));
 const cyclic={};cyclic.self=cyclic;assert.throws(()=>graphWireRequestJSON(cyclic));
 const sparse=[];sparse.length=1;assert.throws(()=>graphWireRequestJSON(sparse));
 let read=false;const getter={};Object.defineProperty(getter,'value',{enumerable:true,get(){read=true;return 1;}});
 assert.throws(()=>graphWireRequestJSON(getter));assert.equal(read,false);
 assert.throws(()=>graphWireRequestJSON({'\ud800':1}));
});

test('V132 request atoms keep new float type, negative zero and large scientific values without hash authority',async()=>{
 for(const token of ['-0.0','1.0','1e21','1e-21']){
  const atom=freshNumber(token),value={number:atom};
  assert.equal(graphWireRequestJSON(value),'{"number":'+numericToken(atom)+'}');
  assert.throws(()=>JSON.stringify(value),/原数值/);
  assert.throws(()=>graphWireCanonical(value),/服务器/);
  await assert.rejects(()=>digest(value),/服务器/);
 }
 assert.equal(graphWireRequestJSON({number:-0}),'{"number":-0.0}');
 assert.throws(()=>graphWireCanonical({number:-0}),/服务器/);
 assert.throws(()=>graphWireRequestJSON({number:1e21}));
 assert.throws(()=>freshNumber('9007199254740992','integer'));
 assert.throws(()=>graphWireRequestJSON({number:9007199254740992}));
});

test('V132 editable copies preserve numeric tokens across a plain JSON cache without carrying server metadata',async()=>{
 const raw='{"a/b":{"~字段":[-0.0,1.0,1e+21]},"integer":1}',source=graphWireParseCanonical(raw),copy=graphWireClone(source);
 const tokens=graphWireNumericTokens(copy);
 assert.deepEqual(tokens,{'/a~1b/~0字段/0':'-0.0','/a~1b/~0字段/1':'1.0','/a~1b/~0字段/2':'1e+21','/integer':'1'});
 const stored=JSON.stringify({copy,tokens}),cold=JSON.parse(stored);assert.equal(Object.is(cold.copy['a/b']['~字段'][0],-0),false);
 graphWireRestoreNumericTokens(cold.copy,cold.tokens);
 assert.ok(Object.is(cold.copy['a/b']['~字段'][0],-0));
 const typed=graphWireTypedCopy(cold.copy);
 assert.ok(isTypedNumber(typed['a/b']['~字段'][0]));
 assert.equal(graphWireRequestJSON(typed),raw);
 assert.equal(graphWireCanonical(source),raw);assert.equal(await digest(source),hash(raw));
 assert.throws(()=>graphWireCanonical(typed),/服务器/);
 assert.equal(JSON.stringify({copy:graphWireClone(cold.copy),tokens}),stored);
});

test('V132 an explicit zero edit drops old sign metadata and restores only its new token',()=>{
 const source=graphWireParseCanonical('{"value":-0.0}'),copy=graphWireClone(source);
 copy.value=0;assert.deepEqual(graphWireNumericTokens(copy),{});
 graphWireSetNumberToken(copy,'value','0');
 assert.equal(graphWireRequestJSON(graphWireTypedCopy(copy)),'{"value":0}');
 graphWireSetNumberToken(copy,'value','0.0');
 assert.equal(graphWireRequestJSON(graphWireTypedCopy(copy)),'{"value":0.0}');
 assert.equal(graphWireCanonical(source),'{"value":-0.0}');
});

test('V132 token restoration rejects wrong paths, mismatched values, unsafe integers and malformed canonical text',()=>{
 for(const tokens of [{'/missing':'1.0'},{'/value':'2.0'},{'/value':'9007199254740992'},{'/value':'1.0junk'},{'/bad~2key':'1.0'},{value:'1.0'}])assert.throws(()=>graphWireRestoreNumericTokens({value:1},tokens));
 assert.throws(()=>graphWireRestoreNumericTokens(Object.freeze({value:1}),{'/value':'1.0'}));
 for(const raw of ['{"a":1,"a":1}','{"b":1,"a":1}','{"a":1} ','{"a":NaN}','{"a":9007199254740992}','{"a":-0}','1.0'])assert.throws(()=>graphWireParseCanonical(raw));
 let read=false;const bad={};Object.defineProperty(bad,'value',{enumerable:true,get(){read=true;return 1;}});assert.throws(()=>graphWireClone(bad));assert.equal(read,false);
 const cycle={};cycle.a=cycle;assert.throws(()=>graphWireClone(cycle));
});

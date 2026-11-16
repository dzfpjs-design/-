// The actual classic asset runs in a VM. These codec cases do not substitute a
// browser, HTTP/PG verification, or the separate actual-workbench DOM journey.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../src/generic_agents/web/operator.js',import.meta.url),'utf8');
function helpers(){
  const context=vm.createContext({TextEncoder});vm.runInContext(source,context,{filename:'actual-operator.js'});
  for(const name of ['Parse','Stringify','Clone','Display','Set','Delete'])
    assert.equal(typeof context['ordinaryNumeric'+name],'function','actual codec export '+name+' must exist before any rejection oracle');
  return context;
}
const inside=(h,text)=>vm.runInContext(text,h);
const parsed=(h,text)=>h.ordinaryNumericParse(text);
const encoded=(h,value)=>h.ordinaryNumericStringify(value);

test('actual operator codec retains source numeric lexemes, strings, arrays and numeric types',()=>{
  const h=helpers(),wire='{"negative":-0.0,"float":1.0,"scientific":1e+21,"large":9007199254740993,"text":"1.0","nested":[1,1.0,-0.0,{"value":1e21}]}';
  const value=parsed(h,wire);
  assert.ok(Object.is(value.negative,-0));assert.equal(typeof value.float,'number');
  assert.equal(value.text,'1.0');assert.equal(typeof value.text,'string');
  assert.equal(h.ordinaryNumericDisplay(value,'negative'),'-0.0');
  assert.equal(h.ordinaryNumericDisplay(value,'float'),'1.0');
  assert.equal(h.ordinaryNumericDisplay(value,'scientific'),'1e+21');
  assert.equal(h.ordinaryNumericDisplay(value,'large'),'9007199254740993');
  assert.equal(h.ordinaryNumericDisplay(value,'text'),'1.0');
  assert.equal(h.ordinaryNumericDisplay(value.nested,0),'1');
  assert.equal(h.ordinaryNumericDisplay(value.nested,1),'1.0');
  assert.equal(encoded(h,value),wire,'untouched source values retain exact numeric tokens');
});

test('metadata clone preserves nested original values without sharing editable containers',()=>{
  const h=helpers(),wire='{"parameters":{"threshold":-0.0,"count":9007199254740993},"values":[1.0,1e21],"text":"-0.0"}';
  const original=parsed(h,wire),copy=h.ordinaryNumericClone(original);
  assert.notEqual(copy,original);assert.notEqual(copy.parameters,original.parameters);
  assert.notEqual(copy.values,original.values);assert.equal(encoded(h,copy),wire);
  h.ordinaryNumericSet(copy.parameters,'threshold','1.0');
  assert.equal(h.ordinaryNumericDisplay(copy.parameters,'threshold'),'1.0');
  assert.equal(encoded(h,original),wire);
});

test('an explicit new edit replaces the original token even if Number stays equal',()=>{
  const h=helpers(),value=parsed(h,'{"value":1.0}');
  h.ordinaryNumericSet(value,'value','1','number');
  assert.equal(encoded(h,value),'{"value":1}');
  h.ordinaryNumericSet(value,'value','1.0','number');
  assert.equal(encoded(h,value),'{"value":1.0}');
  h.ordinaryNumericSet(value,'value','-0.0','number');
  assert.equal(encoded(h,value),'{"value":-0.0}');
  h.ordinaryNumericSet(value,'value','0','number');
  assert.equal(encoded(h,value),'{"value":0}');
});

test('delete removes the field and its token, and missing metadata does not invent a float',()=>{
  const h=helpers(),value=parsed(h,'{"value":1.0,"keep":2.0}');
  h.ordinaryNumericDelete(value,'value');assert.equal(encoded(h,value),'{"keep":2.0}');
  value.value=1;
  assert.match(h.ordinaryNumericDisplay(value,'value'),/未核实/);
  assert.ok(!h.ordinaryNumericDisplay(value,'value').includes('1.0'));
  assert.equal(encoded(h,value),'{"keep":2.0,"value":1}','safe fresh internal integers remain compatible');
  const lost=inside(h,'({value:1})');
  assert.match(h.ordinaryNumericDisplay(lost,'value'),/未核实/);
});

test('changed projections and zero signs cannot reuse stale source tokens',()=>{
  const h=helpers(),value=parsed(h,'{"float":1.0,"negative":-0.0,"large":9007199254740993}');
  value.float=2;value.negative=0;
  assert.match(h.ordinaryNumericDisplay(value,'float'),/未核实/);
  assert.match(h.ordinaryNumericDisplay(value,'negative'),/未核实/);
  assert.equal(encoded(h,value),'{"float":2,"negative":0,"large":9007199254740993}');
  value.float=1;assert.match(h.ordinaryNumericDisplay(value,'float'),/未核实/,'an observed stale binding cannot revive after a later equal Number assignment');
  value.large=9007199254740994;
  assert.throws(()=>encoded(h,value),/数值|整数|准确|原文/);
});

for(const raw of ['-0.0','1.0','1e21','0.1','5e-324','2.50e+2','-2.5e-7'])test('fresh complete float token is preserved: '+raw,()=>{
  const h=helpers(),value=parsed(h,'{}');h.ordinaryNumericSet(value,'value',raw,'number');
  assert.equal(encoded(h,value),'{"value":'+raw+'}');
  assert.equal(h.ordinaryNumericDisplay(value,'value'),raw);
});

for(const raw of ['9007199254740993','9007199254740993.0','1e-400','1.0000000000000001',
  '0.10000000000000000001','NaN','Infinity','0x10','-','1e','1.2.3'])test('fresh lossy or incomplete numeric input retains the previous value: '+raw,()=>{
  const h=helpers(),value=parsed(h,'{"value":-0.0}');
  assert.throws(()=>h.ordinaryNumericSet(value,'value',raw,'number'));
  assert.equal(encoded(h,value),'{"value":-0.0}','failed edits must not overwrite a retained field');
});

test('integer input validates decimal precision before Number rounding and keeps its safe contract',()=>{
  const h=helpers(),value=parsed(h,'{"value":1}');
  for(const raw of ['24.0000000000000001','9007199254740993','-0.0']){
    assert.throws(()=>h.ordinaryNumericSet(value,'value',raw,'integer'));
    assert.equal(encoded(h,value),'{"value":1}');
  }
  h.ordinaryNumericSet(value,'value','24.0','integer');assert.equal(encoded(h,value),'{"value":24}');
  assert.throws(()=>h.ordinaryNumericSet(value,'value',1e21,'number'));
  assert.equal(encoded(h,value),'{"value":24}');
});

for(const wire of ['{"a":1,"a":2}','{"a":1,"\\u0061":2}','[1,]','{"a":.1}',
  '{"a":01}','1 true','{"a":NaN}','{"a":Infinity}','{"a":1e309}'])test('source reader rejects invalid or ambiguous numeric JSON: '+wire,()=>{
  const h=helpers();assert.throws(()=>parsed(h,wire));
});

test('source decimal underflow remains an original read token, not permission for fresh underflow',()=>{
  const h=helpers(),value=parsed(h,'{"original":1e-400}');
  assert.equal(value.original,0);assert.equal(h.ordinaryNumericDisplay(value,'original'),'1e-400');
  assert.equal(encoded(h,h.ordinaryNumericClone(value)),'{"original":1e-400}');
  assert.throws(()=>h.ordinaryNumericSet(value,'edited','1e-400'));
  assert.ok(!Object.hasOwn(value,'edited'));
});

test('source integer tokens beyond Number range remain opaque readable originals',()=>{
  const h=helpers(),token='1'+'0'.repeat(350),value=parsed(h,'{"value":'+token+'}');
  assert.equal(h.ordinaryNumericDisplay(value,'value'),token);
  assert.equal(encoded(h,h.ordinaryNumericClone(value)),'{"value":'+token+'}');
  assert.throws(()=>h.ordinaryNumericSet(value,'value',token,'integer'));
  assert.equal(encoded(h,value),'{"value":'+token+'}');
});

test('record/list root boundary rejects numeric primitives instead of pretending to retain their type',()=>{
  const h=helpers();
  for(const text of ['1','1.0','-0.0'])assert.throws(()=>parsed(h,text));
  assert.throws(()=>encoded(h,1));assert.throws(()=>h.ordinaryNumericClone(1));
  for(const text of ['"1.0"','true','null'])assert.equal(encoded(h,parsed(h,text)),text);
});

test('prototype-looking own JSON keys remain ordinary data without prototype pollution',()=>{
  const h=helpers(),wire='{"__proto__":{"polluted":true,"value":1.0},"constructor":-0.0}';
  const value=parsed(h,wire);
  assert.ok(Object.hasOwn(value,'__proto__'));assert.equal(inside(h,'({}).polluted'),undefined);
  assert.equal(encoded(h,value),wire);assert.equal(encoded(h,h.ordinaryNumericClone(value)),wire);
  h.ordinaryNumericSet(value,'__proto__','1.0');
  assert.equal(h.ordinaryNumericDisplay(value,'__proto__'),'1.0');
  assert.equal(inside(h,'({}).polluted'),undefined);
});

test('unsupported live values, getters, prototypes and cycles are rejected without executing getters',()=>{
  const h=helpers();inside(h,'globalThis.reads=0');
  const bad=[
    '({get value(){globalThis.reads++;return 1;}})',
    'Object.assign(Object.create({inherited:true}),{value:1})',
    '(()=>{const x={};x.self=x;return x;})()',
    '({value:Infinity})','({value:NaN})','({value:9007199254740993})',
    '({value:undefined})','({value:()=>1})','({value:new Date()})',
    '(()=>{const x=[];x.length=2;return x;})()',
    '({[Symbol("hidden")]:1,value:1})',
  ];
  for(const text of bad){const value=inside(h,text);assert.throws(()=>encoded(h,value),text);assert.throws(()=>h.ordinaryNumericClone(value),text);}
  assert.equal(inside(h,'globalThis.reads'),0);
});

test('depth and input-byte limits fail explicitly',()=>{
  const h=helpers();assert.throws(()=>parsed(h,'['.repeat(200)+'0'+']'.repeat(200)));
  assert.throws(()=>parsed(h,'"'+'x'.repeat(25*1024*1024)+'"'));
});

test('legacy observation raw wire remains opaque and byte-identical inside a new codec envelope',()=>{
  const h=helpers(),wire='{"attributes":{"value":1.0},"source_record_id":"legacy-original"}';
  const value=parsed(h,'{"request":{"wire":'+JSON.stringify(wire)+',"status":"unknown"},"version":1}');
  const saved=encoded(h,h.ordinaryNumericClone(value));
  assert.equal(parsed(h,saved).request.wire,wire);
  assert.equal(value.request.status,'unknown');
});

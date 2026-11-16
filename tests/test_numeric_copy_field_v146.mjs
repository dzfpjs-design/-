// Actual classic-asset VM, with no alternate implementation of the seam.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../src/generic_agents/web/operator.js',import.meta.url),'utf8');
function helpers(){
  const h=vm.createContext({TextEncoder});vm.runInContext(source,h,{filename:'actual-operator.js'});
  assert.equal(typeof h.ordinaryNumericCopyField,'function','actual metadata copy seam must exist before rejection assertions');
  return h;
}
const run=(h,text)=>vm.runInContext(text,h);
const parse=(h,text)=>h.ordinaryNumericParse(text);
const wire=(h,value)=>h.ordinaryNumericStringify(value);

test('copy restores original negative zero, float and unsafe integer without changing other parameter tokens',()=>{
  const h=helpers(),snapshot=parse(h,'{"threshold":-0.0,"float":1.0,"large":9007199254740993}');
  const target=parse(h,'{"threshold":0,"keep":2.0}');
  h.ordinaryNumericCopyField(target,'threshold',snapshot);
  assert.equal(wire(h,target),'{"threshold":-0.0,"keep":2.0}');
  h.ordinaryNumericCopyField(target,'selected',snapshot,'float');
  h.ordinaryNumericCopyField(target,'original_large',snapshot,'large');
  assert.equal(wire(h,target),'{"threshold":-0.0,"keep":2.0,"selected":1.0,"original_large":9007199254740993}');
  assert.equal(wire(h,snapshot),'{"threshold":-0.0,"float":1.0,"large":9007199254740993}');
});

test('choosing zero then the saved option restores the immutable snapshot and keeps independent edits',()=>{
  const h=helpers(),snapshot=parse(h,'{"threshold":-0.0,"other":1.0}');
  const target=h.ordinaryNumericClone(snapshot);
  h.ordinaryNumericSet(target,'threshold','0');h.ordinaryNumericSet(target,'other','2.0');
  assert.equal(wire(h,target),'{"threshold":0,"other":2.0}');
  h.ordinaryNumericCopyField(target,'threshold',snapshot);
  assert.equal(wire(h,target),'{"threshold":-0.0,"other":2.0}');
  assert.equal(wire(h,snapshot),'{"threshold":-0.0,"other":1.0}');
});

test('own numeric enum-array index can be copied by identity without Number aliasing',()=>{
  const h=helpers(),options=parse(h,'[-0.0,0,1.0,9007199254740993]'),target=parse(h,'{}');
  for(const [index,expected] of [[0,'-0.0'],[1,'0'],[2,'1.0'],[3,'9007199254740993']]){
    h.ordinaryNumericCopyField(target,'selection',options,index);
    assert.equal(wire(h,target),'{"selection":'+expected+'}');
  }
});

test('nested source field is cloned with all numeric tokens and string types intact',()=>{
  const h=helpers(),snapshot=parse(h,'{"value":{"items":[-0.0,1.0,{"large":9007199254740993}],"text":"1.0"}}');
  const target=parse(h,'{"keep":3.0}');
  h.ordinaryNumericCopyField(target,'copied',snapshot,'value');
  assert.notEqual(target.copied,snapshot.value);assert.notEqual(target.copied.items,snapshot.value.items);
  assert.equal(wire(h,target),'{"keep":3.0,"copied":{"items":[-0.0,1.0,{"large":9007199254740993}],"text":"1.0"}}');
  h.ordinaryNumericSet(target.copied.items[2],'large','2.0');
  assert.equal(wire(h,snapshot),'{"value":{"items":[-0.0,1.0,{"large":9007199254740993}],"text":"1.0"}}');
});

test('unknown or stale numeric source metadata cannot be passed off as a recovered float',()=>{
  const h=helpers(),target=parse(h,'{"value":-0.0,"keep":1.0}'),before=wire(h,target);
  for(const text of ['({value:1})','({value:{nested:1}})','({value:9007199254740993})']){
    assert.throws(()=>h.ordinaryNumericCopyField(target,'value',run(h,text)));
    assert.equal(wire(h,target),before);
  }
  const stale=parse(h,'{"value":1.0}');stale.value=2;
  assert.throws(()=>h.ordinaryNumericCopyField(target,'value',stale));
  assert.equal(wire(h,target),before);
});

test('missing fields, getters and prototype claims fail without getter execution or destination mutation',()=>{
  const h=helpers(),target=parse(h,'{"value":-0.0,"keep":1.0}'),before=wire(h,target);
  run(h,'globalThis.reads=0');
  const invalid=[
    '({})',
    '({get value(){globalThis.reads++;return 1;}})',
    '({value:{get nested(){globalThis.reads++;return 1;}}})',
    'Object.create({value:1})',
    '({value:new Date()})',
    '(()=>{const value={};value.self=value;return {value};})()',
  ];
  for(const text of invalid){
    assert.throws(()=>h.ordinaryNumericCopyField(target,'value',run(h,text)),text);
    assert.equal(wire(h,target),before);
  }
  assert.equal(run(h,'globalThis.reads'),0);
});

test('array misuse and inherited indices cannot create holes or overwrite unrelated values',()=>{
  const h=helpers(),options=parse(h,'[1.0]'),target=parse(h,'{"keep":-0.0}'),before=wire(h,target);
  for(const key of ['length','-1','01','1','missing']){
    assert.throws(()=>h.ordinaryNumericCopyField(target,'value',options,key));
    assert.equal(wire(h,target),before);
  }
  const arrayTarget=parse(h,'[1.0]'),arrayBefore=wire(h,arrayTarget);
  assert.throws(()=>h.ordinaryNumericCopyField(arrayTarget,0,options,0));
  assert.equal(wire(h,arrayTarget),arrayBefore);
});

test('a destination that cannot accept the field remains byte-identical on failure',()=>{
  const h=helpers(),target=parse(h,'{"value":-0.0,"keep":2.0}'),snapshot=parse(h,'{"value":1.0}');
  Object.freeze(target);const before=wire(h,target);
  assert.throws(()=>h.ordinaryNumericCopyField(target,'value',snapshot));
  assert.equal(wire(h,target),before);
  assert.equal(wire(h,snapshot),'{"value":1.0}');
});

test('prototype-looking own target keys and same-object copies retain metadata safely',()=>{
  const h=helpers(),snapshot=parse(h,'{"__proto__":1.0}'),target=parse(h,'{"value":-0.0}');
  h.ordinaryNumericCopyField(target,'__proto__',snapshot);
  h.ordinaryNumericCopyField(target,'other',target,'value');
  assert.ok(Object.hasOwn(target,'__proto__'));
  assert.equal(wire(h,target),'{"value":-0.0,"__proto__":1.0,"other":-0.0}');
  assert.equal(run(h,'({}).polluted'),undefined);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {newRuleDraft,savedRuleDraft,finiteInput,ruleDefinition,sameRuleDefinition,ruleSamples,compatibleRule} from '../src/local-rule-policy.mjs';
test('partial threshold is preserved, never converted into a silent zero',()=>{
 const d=newRuleDraft('e',[{field:'temperature',unit:'C'}]);assert.equal(d.rules[0].threshold,'');
 for(const value of ['', ' ', '-', '.', 'NaN', 'Infinity','true','0x10','1e16'])assert.throws(()=>finiteInput(value));
 assert.equal(finiteInput('-.5'),-.5);assert.equal(finiteInput('0'),0);
});
test('definition and saved trial are versioned; edits invalidate equality',()=>{
 const d=newRuleDraft('e',[{field:'t',unit:'C'}]);d.title='Temperature';d.rationale='Approved procedure';d.rules[0].threshold='38';d.samples={t:'39.5'};
 const row={...ruleDefinition(d),version:3,last_check:{values:{t:39.5}}};const restored=savedRuleDraft(row);
 assert(sameRuleDefinition(restored,row));assert.deepEqual(ruleSamples(restored),{t:39.5});
 assert(sameRuleDefinition(restored,{...row,rules:row.rules.map(r=>Object.fromEntries(Object.entries(r).reverse()))}));
 restored.rules[0].threshold='37';assert(!sameRuleDefinition(restored,row));
});
test('managed rules cannot silently cross business types or source units',()=>{
 const cap={data:{specification:{managed_numeric_rule:{package_id:'p',entity_type:'station'},rules:[{field:'t',unit:'C'}]}}};
 const obj={package_id:'p',entity_type:'station'}, links={air:'t'},m=[{attribute:'air',unit:'C'}];
 assert(compatibleRule(cap,obj,links,m));assert(!compatibleRule(cap,{...obj,package_id:'q'},links,m));
 assert(!compatibleRule(cap,obj,{},m));assert(!compatibleRule(cap,obj,links,[{attribute:'air',unit:'F'}]));
});

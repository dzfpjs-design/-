// The same public fixtures are checked by Python's actual compiler/reducer.
// Synthetic pins do not establish registry/IAM/Task/API or browser acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {compileNestedExecution} from '../src/nested-execution-fields.mjs';
import {graphWireDigest} from '../src/full-semantic-graph-wire.mjs';
const fixture=JSON.parse(await readFile(new URL('../../../tests/fixtures/nested_execution_editor_compiler_v196.json',import.meta.url),'utf8'));
assert.equal(fixture.synthetic,true);assert.equal(fixture.not_iam_registry_or_pg_proof,true);
for(const c of fixture.cases)test('actual UI form output stays equal to the real Python compiler golden: '+c.name,async()=>{
 const actual=compileNestedExecution(c.ui_context,c.ui_value);
 assert.equal(await graphWireDigest(actual,{crypto:webcrypto}),await graphWireDigest(c.expected,{crypto:webcrypto}));
 assert.equal(actual.causal_bindings.source_plan_hash,c.ui_context.source_plan_hash);
 assert.equal(actual.causal_bindings.scope_binding_hash,c.ui_context.scope_binding_hash);
 assert.equal(actual.execution_bindings.schema,'business-flow-nested-execution-bindings-v195');
});

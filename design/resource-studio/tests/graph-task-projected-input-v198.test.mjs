import test from 'node:test';
import assert from 'node:assert/strict';
import {graphHumanInputsReady,graphHumanCanComplete} from '../src/graph-task-controller.mjs';
import {environment,unavailable} from './graph-task-nested-input-fixture.mjs';

test('a declared projected input retains a true zero value and its producer without a write',async()=>{
 const f=environment(),row=f.state.task.graph_pending_humans[0].required_inputs[1];
 row.source='port_projection';row.source_name='上一步明确选定的资料';
 const c=f.controller();await c.open();
 assert.deepEqual(c.detail.graph_pending_humans[0].required_inputs[1],row);
 assert.equal(row.value,0);assert.equal(graphHumanInputsReady(c.detail.graph_pending_humans[0]),true);
 assert.equal(f.mutations().length,0);c.dispose();
});

test('unavailable projected input protects completion and cannot widen legacy input contracts',async()=>{
 const f=environment();unavailable(f.state.task,'missing');
 const row=f.state.task.graph_pending_humans[0].required_inputs[1];
 row.source='port_projection';row.availability_code='unavailable';
 f.state.task.graph_runtime.error='guarded_port_projection_null:context';
 const c=f.controller();await c.open();
 assert.equal(graphHumanCanComplete(c.detail.graph_pending_humans[0]),false);
 await assert.rejects(c.submit('complete','human'));
 assert.equal(f.mutations().length,0);assert.equal(c.pending,null);c.dispose();
 row.source='output';const invalid=f.controller();await assert.rejects(invalid.open());invalid.dispose();
});

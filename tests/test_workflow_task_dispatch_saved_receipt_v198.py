"""Cold saved assignment receipt checks without granting fresh dispatch.

The inherited SQL-fetch and V175 authority seams are explicitly synthetic;
the V198 full inventory, original hashes and read-only seam run unchanged.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import workflow_task_dispatch_v198 as dispatch
from generic_agents.models import Audit
from tests.test_workflow_task_dispatch_v198 import scene, dispatch as create_batch


def succeeded(s):
    result = create_batch(s)
    s.call.status = 'succeeded'; s.call.data = {**s.call.data, 'output': copy.deepcopy(result)}
    return result


def test_saved_assignment_read_receipt_uses_full_inventory_without_live_dispatch_or_new_rows(scene):
    s = scene; original = succeeded(s); before = len(s.db.added); call_data = copy.deepcopy(s.call.data)
    s.authority_error = AssertionError('Read-only saved receipt must not re-enter a live dispatcher')
    count = len(s.authority_calls)
    assert dispatch.saved_receipt(s.db, s.owner, s.cap, s.inputs, s.key, s.approval, s.call) == original
    assert len(s.db.added) == before and s.call.data == call_data and len(s.authority_calls) == count
    assert 'pg_advisory_xact_lock_shared' in s.db.locks[-1][0]


@pytest.mark.parametrize('fault', ['batch_audit', 'compound_loss', 'marker_null', 'saved_output',
    'actor', 'key', 'inputs', 'approval', 'not_success'])
def test_saved_success_does_not_replace_missing_work_orders_or_accept_a_different_scope(scene, fault):
    s = scene; succeeded(s)
    p, key, inputs, approval = s.owner, s.key, copy.deepcopy(s.inputs), copy.deepcopy(s.approval)
    if fault == 'batch_audit':
        events = s.db.rows[Audit]
        del events[next(identifier for identifier, event in events.items() if event.action == dispatch.CREATED)]
    elif fault == 'compound_loss':
        for model in (dispatch.Batch, dispatch.Item, dispatch.Command, Audit): s.db.rows[model] = {}
    elif fault == 'marker_null': s.call.data = {**s.call.data, dispatch.CALL_KEY: None}
    elif fault == 'saved_output': s.call.data['output']['service_receipt']['execution_completed'] = True
    elif fault == 'actor': p = s.other
    elif fault == 'key': key += ':different'
    elif fault == 'inputs': inputs['items'][0]['description'] += ' 改写原说明'
    elif fault == 'approval': approval['job_id'] = s.other.user_id
    else: s.call.status = 'unknown'
    before = len(s.db.added)
    with pytest.raises(HTTPException) as error:
        dispatch.saved_receipt(s.db, p, s.cap, inputs, key, approval, s.call)
    assert error.value.status_code == 409 and len(s.db.added) == before

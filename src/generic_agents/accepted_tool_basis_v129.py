"""Recover hash inputs from a frozen acceptance, never current execution state.

This checks the saved representation, not DB authority. Callers must still verify
the actual acceptance/outcome, originals, and historical invocation provenance.
No value returned here grants execution or certifies a candidate as a fact.
"""
import copy
from datetime import datetime
import math
import re

from .contracts import digest


ERROR = '原正式验收的冻结工具依据无法核对'
_HASH = re.compile(r'[0-9a-f]{64}')
_KINDS = {'evidence_sufficient', 'capability_success', 'field_value',
    'tool_value', 'query_value', 'human_confirmation'}
_WORKFLOW = {'status', 'method', 'capability_id', 'capability_name',
    'capability_hash', 'invocation_id', 'input_hash', 'output',
    'workflow_step_id', 'workflow_receipt_hash', 'candidate_only',
    'physical_state_verified'}
_EXECUTION = {'status', 'method', 'capability_id', 'output', 'invocation_id',
    'input_hash', 'output_hash', 'capability_hash', 'release_version',
    'plan_id', 'run_id', 'approval_hash', 'result_hash', 'goal_snapshot'}
_RUN_BASIS = {'id', 'version', 'status', 'approval_hash', 'result_hash',
    'review', 'history', 'started_steps'}


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _text(value):
    _need(type(value) is str and bool(value))


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value) is not None)


def _json(value, ancestors=()):
    """Reject Python-only values and cycles without coercing frozen JSON."""
    kind = type(value)
    if kind is str:
        value.encode('utf-8')
    elif value is None or kind in {bool, int}:
        return
    elif kind is float:
        _need(math.isfinite(value))
    else:
        _need(kind in {dict, list} and id(value) not in ancestors)
        parents = (*ancestors, id(value))
        if kind is dict:
            for key, item in value.items():
                _need(type(key) is str)
                key.encode('utf-8')
                _json(item, parents)
        else:
            for item in value:
                _json(item, parents)


def _time(value):
    _text(value)
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _need(stamp.utcoffset() is not None)


def _review(value):
    _need(type(value) is dict and set(value) == {'decision', 'reason', 'actor_id', 'at'})
    _need(value['decision'] in {'accepted', 'rejected'})
    _text(value['reason']); _need(bool(value['reason'].strip()))
    _text(value['actor_id']); _time(value['at'])


def _run_proof(run):
    # Full runs contain progress/steps/transport metadata too; only these fields
    # are the exact goal_gate proof. Do not invent a closed replacement run DTO.
    _need(type(run) is dict and _RUN_BASIS <= set(run) and 'scope_error' not in run)
    _text(run['id'])
    _need(type(run['version']) is int and run['version'] >= 1)
    _need(run['status'] in {'accepted', 'rejected', 'cancelled'})
    _hash(run['approval_hash'])
    if run['result_hash'] is not None:
        _hash(run['result_hash'])
    _need(type(run['history']) is list and all(type(item) is dict for item in run['history']))
    _need(type(run['started_steps']) is list)
    for item in run['started_steps']:
        _need(type(item) is dict and set(item) == {'index', 'attempt', 'at'})
        _need(type(item['index']) is int and item['index'] >= 0
            and type(item['attempt']) is int and item['attempt'] >= 0)
        _time(item['at'])
    if run['review'] is not None:
        _review(run['review'])
    if run['status'] in {'accepted', 'rejected'}:
        _need(run['result_hash'] is not None and run['review'] is not None
            and run['review']['decision'] == run['status'])
    return {'plan_id': run['id'], 'version': run['version'], 'status': run['status'],
        'approval_hash': run['approval_hash'], 'result_hash': run['result_hash'],
        'review': copy.deepcopy(run['review']), 'history_hash': digest(run['history']),
        'started_steps_hash': digest(run['started_steps'])}


def _receipt(value, family):
    _need(type(value) is dict and set(value) == family
        and value['status'] == 'succeeded' and value['method'] == 'registered_capability')
    for key in ('capability_id', 'invocation_id'):
        _text(value[key])
    for key in ('capability_hash', 'input_hash'):
        _hash(value[key])
    if family == _WORKFLOW:
        _text(value['capability_name']); _text(value['workflow_step_id'])
        _hash(value['workflow_receipt_hash'])
        _need(type(value['candidate_only']) is bool and value['physical_state_verified'] is False)
    else:
        for key in ('plan_id', 'run_id', 'release_version'):
            _text(value[key])
        for key in ('output_hash', 'approval_hash', 'result_hash'):
            _hash(value[key])
        _need(value['output_hash'] == digest(value['output']))


def _accepted_tool_basis(goal_spec, evaluation):
    _need(type(goal_spec) is dict and type(evaluation) is dict)
    _json(goal_spec); _json(evaluation)
    criteria = goal_spec.get('criteria')
    _need(type(criteria) is list and bool(criteria))
    required = set()
    for item in criteria:
        _need(type(item) is dict and type(item.get('kind')) is str and item['kind'] in _KINDS)
        if item['kind'] in {'capability_success', 'tool_value'}:
            _text(item.get('capability_id')); required.add(item['capability_id'])
    # These aliases were never emitted by assessment. They cannot stand in for
    # the two actual frozen fields or quietly select the no-tool legacy mode.
    _need(not {'workflow_results', 'execution_basis', 'tool_basis'} & set(evaluation))
    marked = {'tool_results', 'tool_execution'} & set(evaluation)
    if not marked:
        _need(not required)
        return None  # Original no-tool acceptances predate both frozen fields.
    _need(marked == {'tool_results', 'tool_execution'})
    results, execution = evaluation['tool_results'], evaluation['tool_execution']
    _need(type(results) is list and type(execution) is dict
        and set(execution) == {'all_reviewed', 'runs', 'basis_hash'}
        and execution['all_reviewed'] is True and type(execution['runs']) is list)
    _hash(execution['basis_hash'])
    runs, proofs, last_id = {}, [], None
    for run in execution['runs']:
        proof = _run_proof(run)
        _need(last_id is None or last_id < run['id'])
        last_id = run['id']; runs[last_id] = run; proofs.append(proof)
    workflow, native, last_plan, native_started = [], {}, None, False
    for item in results:
        _need(type(item) is dict)
        if set(item) == _WORKFLOW:
            _need(not native_started)
            _receipt(item, _WORKFLOW)
            _need(item['capability_id'] in required)
            workflow.append(item)
        else:
            _receipt(item, _EXECUTION)
            native_started = True
            identity = item['plan_id']
            _need(identity == item['run_id'] and identity in runs
                and (last_plan is None or last_plan <= identity))
            last_plan = identity
            run = runs[identity]
            _need(run['status'] == 'accepted' and item['approval_hash'] == run['approval_hash']
                and item['result_hash'] == run['result_hash'])
            snapshot = item['goal_snapshot']
            _need(type(snapshot) is dict and set(snapshot) == {'version', 'hash', 'spec', 'time'}
                and type(snapshot['version']) is int and snapshot['version'] >= 1)
            _hash(snapshot['hash'])
            _need(snapshot['hash'] == digest(snapshot['spec']) == digest(goal_spec))
            native.setdefault(identity, []).append(item)
    for proof in proofs:
        if proof['status'] == 'accepted':
            proof['verified_receipts_hash'] = digest(native.get(proof['plan_id'], []))
    _need(digest(proofs) == execution['basis_hash'])
    if not (workflow or runs or required):
        return None  # Presence of modern empty fields did not add a tool basis.
    return {'workflow_results': copy.deepcopy(workflow), 'execution_basis': execution['basis_hash']}


def accepted_tool_basis(goal_spec, evaluation):
    """Return the original basis third argument or None; never repair a record.

    The workflow prefix and execution suffix are deliberately not sorted or
    coerced. Frozen run proofs are checked against their frozen receipt groups;
    no present-day plan, object, capability, or invocation is consulted.
    """
    try:
        return _accepted_tool_basis(goal_spec, evaluation)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError(ERROR) from error

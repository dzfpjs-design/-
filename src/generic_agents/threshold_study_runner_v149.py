"""Actual, local-only paired threshold calls for one frozen study Job.

The store owns scope, live eligibility, reservations, cancellation, inventory
and commits. This module neither issues observations nor grants trial authority.
Worker lease/actor fencing remains attached across the Gateway's commits.
"""
from copy import deepcopy

from fastapi import HTTPException

from .gateway import invoke
from .threshold_evolution_gate_v147 import compare_frozen_thresholds
from .typed_payload_codec_v131 import canonical_json


_ERROR = '原冻结比较输入或实际数值调用回执无法核对，请保留原结果并核查'
_TERMINAL = {'completed', 'failed', 'cancelled'}
_SIDES = ('baseline', 'candidate')


def _need(condition):
    if not condition:
        raise HTTPException(409, _ERROR)


def _status(value):
    _need(type(value) is str and value in _TERMINAL)
    return value


def _comparison(frozen):
    """Compute the existing oracle from detached, originally frozen values."""
    _need(type(frozen) is dict)
    candidate = frozen['candidate']
    samples = frozen['samples']
    _need(type(candidate) is dict and type(samples) is list and bool(samples))
    inputs = []
    for sample in samples:
        basis = sample['basis']
        inputs.append({'id': sample['id'],
            'values': {basis['field']: deepcopy(basis['actual_value'])},
            'expected': sample['feedback_snapshot']['data']['label']})
    comparison = compare_frozen_thresholds(candidate['baseline_manifest'],
        candidate['candidate_manifest'], inputs, frozen['criteria'])
    _need(len(comparison['per_sample']) == len(samples))
    # Unknown labels remain comparison rows. Invalid values, however, cannot be
    # sent as a legitimate pair or converted into an actual no-alert result.
    for original, row in zip(inputs, comparison['per_sample'], strict=True):
        _need(row['id'] == original['id'] and row['issue'] is None
            and type(row['baseline_matched']) is bool
            and type(row['candidate_matched']) is bool)
    return comparison, inputs


def _checked_output(call, manifest, attributes, matched):
    """Require the exact registered handler output, including typed values."""
    _need(call.status == 'succeeded' and type(call.data) is dict)
    rule = manifest['specification']['rules'][0]
    anomalies = []
    if matched:
        anomalies.append({'field': rule['field'],
            'value': deepcopy(attributes[rule['field']]),
            'rule_id': rule['id'], 'severity': rule.get('severity', 'warning')})
    expected = {'attributes': attributes, 'anomalies': anomalies,
        'method': 'registered_numeric_rules'}
    _need(canonical_json(call.data.get('output')) == canonical_json(expected))


def process(db, p, job):
    """Run/restore the fixed pairs; failures use the existing Worker fail hook.

    An existing successful call is only cold-read. The store rejects failed,
    unknown and incomplete call/reservation inventories before any new invoke.
    No exception is swallowed or retried here, including lease and DB failures.
    """
    from . import threshold_study_store_v149 as store

    evaluation = store.owned_evaluation(db, p, job, lock=True)
    if evaluation.status in _TERMINAL:
        return _status(evaluation.status)
    _need(evaluation.status in {'queued', 'running'})
    frozen = deepcopy(evaluation.data['freeze'])
    comparison, inputs = _comparison(frozen)
    candidate = frozen['candidate']
    manifests = {'baseline': candidate['baseline_manifest'],
        'candidate': candidate['candidate_manifest']}
    capability_ids = {'baseline': candidate['baseline_id'],
        'candidate': candidate['candidate_capability_id']}

    for sample, original, row in zip(frozen['samples'], inputs,
            comparison['per_sample'], strict=True):
        for side in _SIDES:
            call = store.saved_call(db, p, job, evaluation, sample, side)
            if call is None:
                reserved = store.reserve_call(db, p, job, evaluation, sample, side)
                _need(type(reserved) is dict
                    and set(reserved) == {'key', 'inputs', 'capability_id'}
                    and type(reserved['key']) is str
                    and reserved['key'] == f'threshold-study:{evaluation.id}:{sample["id"]}:{side}'
                    and reserved['capability_id'] == capability_ids[side]
                    and canonical_json(reserved['inputs'])
                        == canonical_json({'attributes': original['values']}))
                # No commit/checkpoint may separate this reservation from the
                # original Gateway's actual running Invocation commit.
                call = invoke(db, p, reserved['capability_id'], 'perception',
                    reserved['inputs'], reserved['key'])
            _checked_output(call, manifests[side], original['values'], row[side + '_matched'])
            accepted = store.accept_call(db, p, job, evaluation, sample, side, call)
            _need(type(accepted) is bool)
            if not accepted:
                # The store returns false only for a cancelled Evaluation; its
                # already-reserved local result remains historical evidence.
                return 'cancelled'

    # Pass the raw existing comparison to the store. Its internal eligible flag
    # is only the mechanism gate; the public study always has trial=false.
    return _status(store.complete_evaluation(db, p, job, evaluation, comparison))


def fail(db, p, job, reason):
    """Preserve failed progress through the store without dispatching a call."""
    from . import threshold_study_store_v149 as store

    return _status(store.fail_evaluation(db, p, job, reason))

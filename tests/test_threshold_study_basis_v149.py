"""Pure V149 manifest/independence contracts, not source or Invocation proof.

Managed manifests are assembled by the real public rule factory. Synthetic
footprints below exercise only the frozen identity algorithm: they do not mint
Feedback, Observation, Job, Invocation, physical independence or field labels.
The new module's absence is an engineering red; Root owns actual HTTP/PG red.
"""
import copy
import json
import math
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.local_rules_api import CreateRule, _assemble_manifest
from generic_agents.threshold_study_basis_v149 import (
    check_candidate_manifests,
    independence_issues,
)


FIELD = 'temperature'


def uid(number):
    return f'00000000-0000-4000-8000-{number:012x}'


def managed(threshold, *, number=1, entity=101, operator='gt', unit='C',
            minimum=-100, owner=201, title='合成阈值候选'):
    request_id = UUID(uid(number))
    body = CreateRule.model_validate({
        'request_id': request_id, 'title': title, 'entity_id': uid(entity),
        'rationale': '仅比较持久冻结机制，不证明现场效能或最优阈值',
        'rules': [{'id': 'warm', 'field': FIELD, 'unit': unit,
                   'operator': operator, 'threshold': threshold}],
    })
    fields = {FIELD: {'type': 'number', 'unit': unit, 'minimum': minimum, 'maximum': 100}}
    return _assemble_manifest(body, request_id, digest(body.model_dump(mode='json')),
        uid(owner), uid(301), 'site', uid(entity), fields, {FIELD: unit}).model_dump(mode='json')


def pair(baseline=20, candidate=18):
    return managed(baseline), managed(candidate, number=2, owner=202, title='独立候选说明')


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def checked_pair(baseline, candidate):
    before = wire([baseline, candidate])
    result = check_candidate_manifests(baseline, candidate)
    assert wire([baseline, candidate]) == before, 'manifest admission must not rewrite either original'
    assert set(result) == {'field', 'unit', 'operator', 'baseline_threshold',
                          'candidate_threshold', 'rule_id'}
    assert result == {'field': FIELD, 'unit': 'C', 'operator': 'gt',
                      'baseline_threshold': baseline['specification']['rules'][0]['threshold'],
                      'candidate_threshold': candidate['specification']['rules'][0]['threshold'],
                      'rule_id': 'warm'}
    for key, manifest in (('baseline_threshold', baseline), ('candidate_threshold', candidate)):
        assert type(result[key]) is type(manifest['specification']['rules'][0]['threshold'])
        if result[key] == 0:
            assert math.copysign(1, result[key]) == math.copysign(
                1, manifest['specification']['rules'][0]['threshold'])
    return result


def test_real_managed_pair_allows_only_new_authoring_identity_and_changed_threshold():
    baseline, candidate = pair()
    checked_pair(baseline, candidate)
    assert baseline['specification']['managed_numeric_rule']['entity_id'] == uid(101)
    assert candidate['specification']['rules'][0]['threshold'] == 18


@pytest.mark.parametrize(('before', 'after'), [(20, 18.0), (-0.0, 1.0), (1.0, -0.0)])
def test_candidate_admission_preserves_original_threshold_number_kind_and_negative_zero(before, after):
    baseline, candidate = pair(before, after)
    checked_pair(baseline, candidate)
    for manifest, original in ((baseline, before), (candidate, after)):
        saved = manifest['specification']['rules'][0]['threshold']
        assert type(saved) is type(original)
        if saved == 0:
            assert math.copysign(1, saved) == math.copysign(1, original)


@pytest.mark.parametrize(('before', 'after'), [(20, 20), (20, 20.0), (0, -0.0)])
def test_numerically_unchanged_threshold_cannot_be_a_new_candidate(before, after):
    baseline, candidate = pair(before, after)
    with pytest.raises(ValueError):
        check_candidate_manifests(baseline, candidate)


@pytest.mark.parametrize('change', [
    {'entity': 102}, {'operator': 'lt'}, {'unit': 'K'}, {'minimum': -99},
])
def test_otherwise_valid_managed_manifest_cannot_change_production_object_or_rule_semantics(change):
    baseline = managed(20)
    candidate = managed(18, number=2, **change)
    with pytest.raises(ValueError):
        check_candidate_manifests(baseline, candidate)


@pytest.mark.parametrize(('path', 'value'), [
    (('specification', 'rules', 0, 'threshold'), True),
    (('specification', 'rules', 0, 'threshold'), '18'),
    (('specification', 'rules', 0, 'threshold'), float('nan')),
    (('specification', 'rules', 0, 'threshold'), float('inf')),
    (('specification', 'rules', 0, 'id'), 'another-rule'),
    (('specification', 'managed_numeric_rule', 'package_id'), uid(302)),
    (('specification', 'managed_numeric_rule', 'entity_type'), 'another-type'),
    (('execution', 'handler'), 'some_other_handler'),
    (('execution', 'side_effect'), True),
    (('data_policy', 'cloud_allowed'), True),
    (('input_schema', 'properties', 'attributes', 'properties', FIELD, 'minimum'), True),
    (('input_schema', 'properties', 'attributes', 'properties', FIELD, 'multipleOf'), 0),
    (('output_schema', 'additionalProperties'), True),
])
def test_manifest_tamper_or_bad_schema_is_value_error_without_mutating_inputs(path, value):
    baseline, candidate = pair()
    owner = candidate
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    before = wire([baseline, candidate])
    with pytest.raises(ValueError):
        check_candidate_manifests(baseline, candidate)
    assert wire([baseline, candidate]) == before


def test_multi_rule_manifest_is_not_the_single_threshold_family():
    baseline, candidate = pair()
    candidate['specification']['rules'].append({
        **candidate['specification']['rules'][0], 'id': 'second-rule'})
    with pytest.raises(ValueError):
        check_candidate_manifests(baseline, candidate)


SAMPLE_KEYS = {'id', 'feedback_snapshot', 'basis', 'producer_manifest', 'fingerprints'}
FINGERPRINT_KEYS = {'root_id', 'observation_id', 'entity_id', 'source_key', 'input_sha',
                    'attributes_sha', 'job_id', 'invocation_id', 'data_event_id',
                    'observed_at', 'business_day', 'lineage_scope'}
SHARED_KEYS = ('root_id', 'observation_id', 'entity_id', 'source_key', 'input_sha',
               'attributes_sha', 'job_id', 'invocation_id', 'data_event_id')


def frozen_sample(number, *, entity, observed_at):
    # The other three dicts are deliberately empty. Only feedback_samples may
    # turn real DB/receipt/proof into an authorized sample; this pure function
    # must not pretend that a synthetic footprint supplies that authority.
    return {'id': uid(2000 + number), 'feedback_snapshot': {}, 'basis': {},
            'producer_manifest': {}, 'fingerprints': {
                'root_id': uid(3000 + number), 'observation_id': uid(4000 + number),
                'entity_id': uid(entity), 'source_key': [uid(900), f'reading-{number}'],
                'input_sha': digest({'pure-input': number}),
                'attributes_sha': digest({FIELD: number}), 'job_id': uid(5000 + number),
                'invocation_id': uid(6000 + number), 'data_event_id': uid(7000 + number),
                'observed_at': observed_at, 'business_day': '2026-10-21',
                'lineage_scope': {'kind': 'plain_managed_numeric_report',
                                  'workspace_id': uid(801), 'package_id': uid(301),
                                  'entity_type': 'site'},
            }}


def partitions():
    training = [frozen_sample(1, entity=101, observed_at='2026-10-21T08:00:00+00:00'),
                frozen_sample(2, entity=101, observed_at='2026-10-21T09:00:00+00:00')]
    evaluation = [frozen_sample(number, entity=102,
                    observed_at=f'2026-10-21T{number + 8:02d}:00:00+00:00')
                  for number in range(3, 7)]
    return training, evaluation


def independence(training, evaluation):
    before = wire([training, evaluation])
    result = independence_issues(training, evaluation)
    assert type(result) is list and all(type(issue) is str and issue for issue in result)
    assert wire([training, evaluation]) == before, 'independence cannot repair or drop frozen samples'
    return result


def test_distinct_records_of_one_evaluation_object_are_technically_nonrepeated_not_representative():
    training, evaluation = partitions()
    assert all(set(item) == SAMPLE_KEYS and set(item['fingerprints']) == FINGERPRINT_KEYS
               for item in training + evaluation)
    assert independence(training, evaluation) == []
    # This positive proves only this identity/time rule. There is no sample
    # label, source-time verification, representative-data or approval claim.
    assert len({item['fingerprints']['entity_id'] for item in evaluation}) == 1


@pytest.mark.parametrize('key', SHARED_KEYS)
def test_new_feedback_uuid_cannot_hide_cross_partition_root_source_input_or_call_reuse(key):
    training, evaluation = partitions()
    evaluation[0]['fingerprints'][key] = copy.deepcopy(training[0]['fingerprints'][key])
    assert evaluation[0]['id'] != training[0]['id']
    assert independence(training, evaluation), f'cross-partition reuse of {key} must be retained as an issue'


@pytest.mark.parametrize('side', ['training', 'evaluation'])
@pytest.mark.parametrize('key', [key for key in SHARED_KEYS if key != 'entity_id'])
def test_each_partition_rejects_duplicate_source_input_or_call_even_with_different_feedback_ids(side, key):
    training, evaluation = partitions()
    rows = training if side == 'training' else evaluation
    rows[1]['fingerprints'][key] = copy.deepcopy(rows[0]['fingerprints'][key])
    assert rows[1]['id'] != rows[0]['id']
    assert independence(training, evaluation), f'{side} duplicate {key} is not another sample'


def test_processing_version_re_registration_is_still_the_same_source_record():
    training, evaluation = partitions()
    # input_sha changes for a rerun, but the stable source record must still
    # collide. No processing-version field may be added to the closed key.
    evaluation[0]['fingerprints']['source_key'] = copy.deepcopy(training[0]['fingerprints']['source_key'])
    assert evaluation[0]['fingerprints']['input_sha'] != training[0]['fingerprints']['input_sha']
    assert independence(training, evaluation)


@pytest.mark.parametrize('overlap', ['2026-10-21T08:30:00+00:00', '2026-10-21T09:00:00+00:00'])
def test_whole_time_envelopes_interleave_or_touch_even_without_any_identity_collision(overlap):
    training, evaluation = partitions()
    evaluation[0]['fingerprints']['observed_at'] = overlap
    assert independence(training, evaluation)


def test_time_envelope_order_and_timezone_spelling_cannot_hide_the_same_instant():
    training, evaluation = partitions()
    evaluation[-1]['fingerprints']['observed_at'] = '2026-10-21T02:00:00-07:00'
    assert independence(list(reversed(training)), list(reversed(evaluation)))


def test_nonoverlapping_earlier_evaluation_envelope_is_not_subject_to_a_native_method_forward_time_rule():
    training, evaluation = partitions()
    for index, sample in enumerate(evaluation):
        sample['fingerprints']['observed_at'] = f'2026-10-21T0{index}:00:00+00:00'
    assert independence(training, evaluation) == []


@pytest.mark.parametrize(('key', 'value'), [
    ('workspace_id', uid(802)), ('package_id', uid(302)), ('entity_type', 'other-type'),
])
def test_well_formed_foreign_lineage_scope_returns_an_issue_not_technical_compatibility(key, value):
    training, evaluation = partitions()
    evaluation[0]['fingerprints']['lineage_scope'][key] = value
    assert independence(training, evaluation)


@pytest.mark.parametrize(('field', 'value'), [
    ('source_key', [uid(900)]), ('source_key', [uid(900), 'reading', 'processing-v2']),
    ('source_key', (uid(900), 'reading')), ('source_key', [uid(900), True]),
    ('observed_at', '2026-10-21T11:00:00'), ('observed_at', None),
    ('lineage_scope', {'workspace_id': uid(801), 'package_id': uid(301), 'entity_type': 'site'}),
    ('lineage_scope', {'kind': 'another-kind', 'workspace_id': uid(801),
                       'package_id': uid(301), 'entity_type': 'site'}),
])
def test_malformed_fingerprints_are_value_error_instead_of_ignored_evidence(field, value):
    training, evaluation = partitions()
    evaluation[0]['fingerprints'][field] = value
    before = wire([training, evaluation])
    with pytest.raises(ValueError):
        independence_issues(training, evaluation)
    assert wire([training, evaluation]) == before


@pytest.mark.parametrize('location', ['sample-missing', 'sample-extra', 'fingerprint-missing', 'fingerprint-extra'])
def test_sample_and_fingerprint_envelopes_are_closed(location):
    training, evaluation = partitions()
    if location == 'sample-missing':
        del evaluation[0]['basis']
    elif location == 'sample-extra':
        evaluation[0]['independent'] = True
    elif location == 'fingerprint-missing':
        del evaluation[0]['fingerprints']['attributes_sha']
    else:
        evaluation[0]['fingerprints']['processing_version'] = 'new-uuid-does-not-prove-independence'
    with pytest.raises(ValueError):
        independence_issues(training, evaluation)

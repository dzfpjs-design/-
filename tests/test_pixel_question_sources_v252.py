"""Pure closed-source/math fixtures, not 1000 real model executions.

The synthetic inventory only verifies bounded arithmetic/schema behavior;
separate native PG tests use real V250 producers and independent ACKs.
"""
import copy
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import pixel_question_source_contract_v252 as c

AT = '2026-10-30T03:00:00+00:00'
LATER = '2026-10-30T03:01:00+00:00'


def uid(number):
    return str(UUID(int=number))


def audit(number, action='workflow_pixel.input_frozen_v250', obj=None):
    return {'id': uid(number), 'action': action, 'object_id': obj or uid(80),
            'actor_id': uid(3), 'data_hash': digest(['audit fixture', number]), 'created_at': AT}


def human(number):
    return {'receipt_hash': digest(['receipt fixture', number]), 'step_id': 'review-' + str(number),
        'step_name': '核查', 'actor_id': uid(3), 'role': '核查人员', 'completed_at': AT,
        'activation_id': None, 'note_sha256': digest('only a digest, no human text'), 'output_sha256': None,
        'saved_audit': audit(number + 5000, 'native_pixel.human_receipt_saved_v250'),
        'completion_audit': audit(number + 7000, 'workflow.step_completed')}


def record(number=100, *, state='received', registered=True, pixels=5, humans=0):
    """Declared pure metadata only; no saved Run/Invocation/producer assertion."""
    registered = registered and state != 'producer_failed'
    received = state == 'received'
    before = {'id': uid(11), 'name': '原图A', 'sha256': '1' * 64, 'media_type': 'image/png',
              'size_bytes': 50, 'width': 32, 'height': 32, 'created_at': AT}
    after = {**before, 'id': uid(12), 'name': '原图B', 'sha256': '2' * 64}
    sources = {'before': before, 'after': after}
    value = {'run_id': uid(number), 'task_id': uid(80), 'entity_id': uid(81),
        'producer_release_id': uid(82), 'producer_configuration_hash': digest('config'),
        'producer_package_id': uid(83), 'producer_package_hash': digest('package'),
        'capability_id': uid(84), 'capability_hash': digest('cap'), 'source_family': c.FAMILY,
        'invocation_id': uid(number + 10000), 'job_id': uid(number + 20000), 'run_status': 'succeeded',
        'producer_job_status': 'failed' if state == 'producer_failed' else 'succeeded' if registered else 'running',
        'consumed_by_task': registered, 'request_hash': digest(['request', number]), 'sources_hash': digest(sources),
        'model_trace_hash': digest('model'), 'result_hash': digest(['result', number]), 'sources': sources,
        'source_anchors': {'input': audit(number + 30000, obj=uid(number)),
            'result': audit(number + 40000, 'workflow_pixel.result_frozen_v250', uid(number)),
            'completion': audit(number + 50000, 'workflow.ability_completed') if registered else None,
            'settlement': None,
            'issued': audit(number + 60000, 'native_pixel.receipt_issued_v250', uid(number + 70000)) if registered else None,
            'derived_source': audit(90, 'native_pixel.source_derived_v250') if registered else None},
        'reception': {'state': state, 'observation_id': uid(number + 70000) if registered else None,
            'event_id': uid(number + 80000) if registered else None,
            'memory': {'id': uid(number + 90000), 'version': 1, 'data_hash': digest('memory')} if received else None,
            'packet_hash': digest('packet') if registered else None, 'event_base_hash': digest('event') if registered else None,
            'receipt_hash': digest(['ACK', number]) if received else None},
        'timing': {'run_started_at': AT, 'model_completed_at': AT, 'registered_at': AT if registered else None,
            'registration_day': '2026-10-30' if registered else None, 'received_at': AT if received else None,
            'observed_at': None},
        'metrics': {'candidate_pixels': pixels, 'common_valid_pixels': 1024, 'unknown_pixels': 0} if received else None,
        'artifacts': [{'kind': kind, 'evidence_id': uid(number + 100000 + i), 'sha256': digest(kind),
            'size_bytes': 1, 'media_type': 'application/octet-stream'} for i, kind in enumerate(sorted(c.KINDS))] if received else None,
        'human_receipts': [human(number + i) for i in range(humans)], 'association_basis': c.ASSOCIATION}
    return c.seal_record(value)


def snapshot(records=(), *, limit=1000, at=AT):
    rows = sorted(copy.deepcopy(list(records)), key=lambda r: r['run_id'])
    return c.seal_snapshot({'schema': c.SCHEMA, 'workspace_id': uid(1), 'instance_id': uid(2),
        'captured_by': uid(3), 'scope': {'task_id': None, 'entity_id': None}, 'captured_at': at,
        'consistency': 'single_repeatable_read_view', 'inventory': {'complete': True, 'limit': limit,
            'run_ids': [r['run_id'] for r in rows]}, 'records': rows,
        'time_basis': 'machine_result_registered', 'time_zone': 'Asia/Shanghai',
        'human_basis': c.HUMAN_BASIS, 'candidate_only': True, 'not_authoritative': True,
        'field_truth_verified': False, 'source_time_verified': False})


def ir(operation='count', *, reception='any', group='none', days=None, human='any'):
    return {'schema': 'pixel-question-ir-v252', 'source_facet': 'received_pixel_machine_results_v250',
        'scope': {'instance_id': uid(2), 'authorization_release_id': uid(82), 'task_id': None, 'entity_id': None},
        'operation': operation, 'metric': 'candidate_pixels' if operation not in {'read', 'count'} else None,
        'group_by': group, 'time': {'basis': 'machine_result_registered', 'registration_days': days, 'timezone': 'Asia/Shanghai'},
        'filters': {'reception': reception, 'human_completion': human},
        'grounding': {'operation_quote': '统计', 'metric_quote': None, 'time_quote': None, 'filter_quotes': []},
        'limitations': []}


def bad(call, message=None):
    with pytest.raises(HTTPException) as failure: call()
    assert failure.value.status_code == 409
    if message: assert message in str(failure.value.detail)


def test_full_51_and_1000_run_inventory_is_not_a_first_50_page():
    # Boundary metadata fixtures only; explicitly not 1000 inference claims.
    for count in (51, 1000):
        source = snapshot(record(100 + i, pixels=i % 10) for i in range(count))
        answer = c.compute(source, ir('sum', reception='received'))
        assert answer['groups'][0]['value_text'] == str(sum(i % 10 for i in range(count)))
        assert answer['coverage'] == {'complete': True, 'scanned_records': count, 'matched_records': count,
            'unknown_registration_day_excluded': 0, 'limit': 1000}
        assert len(answer['references']) == count == len(answer['groups'][0]['run_ids'])


def test_1001_runs_and_over_16mib_never_turn_into_a_partial_total():
    bad(lambda: snapshot(record(100 + i) for i in range(1001)), '上限')
    original = snapshot()
    oversized = copy.deepcopy(original)
    oversized['private_extra'] = 'x' * c.MAX_BYTES
    bad(lambda: c.compute(oversized, ir()), '16 MiB')
    assert original['records'] == []


def test_all_stages_are_visible_but_only_received_contributes_values():
    source = snapshot([record(100, pixels=0), record(101, state='waiting_data'),
                       record(102, state='waiting_data', registered=False), record(103, state='producer_failed')])
    answer = c.compute(source, ir('sum', reception='received'))
    assert answer['state_counts'] == {'processing_records': 4, 'registered_results': 2,
        'received_results': 1, 'registered_waiting': 1, 'unregistered_waiting': 1, 'producer_failed': 1}
    assert answer['coverage']['matched_records'] == 1 and answer['groups'][0]['value_text'] == '0'
    assert answer['references'][0]['run_id'] == uid(100)
    assert answer['candidate_only'] and not answer['field_truth_verified']
    assert c.compute(source, ir(reception='registered'))['groups'][0]['value_text'] == '2'
    assert c.compute(source, ir())['groups'][0]['value_text'] == '4'
    bad(lambda: c.compute(source, ir('sum', reception='any')))
    assert all(r['metrics'] is None and r['artifacts'] is None for r in source['records'][1:])


@pytest.mark.parametrize('operation,kind,text,numerator,denominator', [
    ('count', 'integer', '2', '2', None), ('sum', 'integer', '7', '7', None),
    ('mean', 'rational', '7/2', '7', 2), ('min', 'integer', '2', '2', None), ('max', 'integer', '5', '5', None)])
def test_exact_integer_and_rational_arithmetic(operation, kind, text, numerator, denominator):
    source = snapshot([record(100, pixels=2), record(101, pixels=5)])
    answer = c.compute(source, ir(operation, reception='received'))
    value = answer['groups'][0]
    assert (value['value_kind'], value['value_text'], value['numerator'], value['denominator']) == (
        kind, text, numerator, denominator)
    assert set(answer) == {'schema', 'source_snapshot_hash', 'operation', 'metric', 'group_by', 'time', 'filters',
        'coverage', 'state_counts', 'groups', 'references', 'unit', 'metrics_basis', 'time_basis', 'human_basis',
        'candidate_only', 'not_authoritative', 'field_truth_verified', 'source_time_verified', 'limitations'}


@pytest.mark.parametrize('operation', ['read', 'count', 'sum', 'mean', 'min', 'max'])
def test_empty_inputs_do_not_fabricate_received_zero_pixels(operation):
    answer = c.compute(snapshot(), ir(operation, reception='received'))
    assert answer['references'] == []
    if operation == 'read': assert answer['groups'] == []
    elif operation == 'count': assert answer['groups'][0]['value_text'] == '0'
    else:
        assert answer['groups'][0]['value_kind'] == 'none'
        assert all(answer['groups'][0][k] is None for k in ('value_text', 'numerator', 'denominator'))
        assert 'no_received_matching_results' in answer['limitations']
    assert c.compute(snapshot(), ir(operation, reception='received', group='task_id'))['groups'] == []


def test_two_human_receipts_do_not_double_count_one_run_and_no_text_is_stored():
    source = snapshot([record(100, pixels=11, humans=2), record(101, pixels=7)])
    answer = c.compute(source, ir('sum', reception='received', group='human_completion'))
    values = {g['key']: g for g in answer['groups']}
    assert values['has_completed_receipt']['value_text'] == '11'
    assert values['has_completed_receipt']['matched_records'] == 1
    assert len(answer['references'][0]['human_receipt_hashes']) == 2
    assert all('note' not in h and 'output' not in h for h in source['records'][0]['human_receipts'])
    assert len(c.compute(source, ir(human='has_completed_receipt'))['references']) == 1


def test_registration_date_filter_excludes_unknown_without_borrowing_run_or_ack_clock():
    source = snapshot([record(100), record(101, state='waiting_data', registered=False)])
    answer = c.compute(source, ir(days=['2026-10-30']))
    assert answer['coverage']['unknown_registration_day_excluded'] == 1
    assert answer['state_counts']['processing_records'] == 1
    assert answer['groups'][0]['value_text'] == '1'
    assert c.compute(source, ir(days=['2026-10-29']))['groups'][0]['value_text'] == '0'


def test_capture_clock_is_not_part_of_stable_revision_but_is_in_snapshot_hash():
    a = snapshot([record()]); b = snapshot([record()], at=LATER)
    assert a['revision_hash'] == b['revision_hash'] and a['snapshot_hash'] != b['snapshot_hash']
    original = copy.deepcopy(a)
    c.compute(a, ir())
    assert a == original


@pytest.mark.parametrize('fault', ['bool_pixel', 'float_pixel', 'missing_metric', 'duplicate_run', 'extra_note',
    'partial', 'bad_record_hash', 'bad_snapshot_hash', 'foreign_scope', 'image_order', 'pending_metric'])
def test_rehashing_a_bad_shape_or_partial_inventory_does_not_make_it_valid(fault):
    source = snapshot([record()])
    r = source['records'][0]
    if fault == 'bool_pixel': r['metrics']['candidate_pixels'] = True
    elif fault == 'float_pixel': r['metrics']['candidate_pixels'] = 5.0
    elif fault == 'missing_metric': r['metrics'].pop('unknown_pixels')
    elif fault == 'duplicate_run': source['records'].append(copy.deepcopy(r)); source['inventory']['run_ids'].append(r['run_id'])
    elif fault == 'extra_note': r['human_receipts'] = [human(100)]; r['human_receipts'][0]['note'] = '不可复制的全文'
    elif fault == 'partial': source['inventory']['complete'] = False
    elif fault == 'bad_record_hash': r['record_hash'] = '0' * 64
    elif fault == 'bad_snapshot_hash': source['snapshot_hash'] = '0' * 64
    elif fault == 'foreign_scope': source['scope']['entity_id'] = uid(900)
    elif fault == 'image_order': r['sources']['after'] = copy.deepcopy(r['sources']['before'])
    elif fault == 'pending_metric': r['reception']['state'] = 'waiting_data'
    if fault not in {'bad_record_hash', 'bad_snapshot_hash'}:
        for item in source['records']: item['record_hash'] = digest({k: v for k, v in item.items() if k != 'record_hash'})
        source['inventory']['inventory_hash'] = c.inventory_hash(source['scope'], source['records'])
        source['revision_hash'] = digest({k: v for k, v in source.items() if k not in {'captured_at', 'revision_hash', 'snapshot_hash'}})
        source['snapshot_hash'] = digest({k: v for k, v in source.items() if k != 'snapshot_hash'})
    bad(lambda: c.compute(source, ir('sum', reception='received')))


@pytest.mark.parametrize('patch', [ {'operation': 'eval'}, {'operation': ['sum']}, {'group_by': 'geo_area'},
    {'metric': 'object_area', 'operation': 'sum'}, {'sql': 'SELECT *'}, {'scope': {'instance_id': uid(900)}} ])
def test_pure_tool_cannot_grow_scope_or_accept_code_or_unknown_metric(patch):
    question = ir(); question.update(patch)
    bad(lambda: c.compute(snapshot([record()]), question))

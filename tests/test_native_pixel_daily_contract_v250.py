import copy
from types import SimpleNamespace as N

import pytest
from pydantic import ValidationError

from generic_agents.native_pixel_daily_rebuild_v250 import Rebuild
from generic_agents import native_pixel_daily_v250 as subject


def body():
    return {'schema': 'native-pixel-daily-rebuild-v250', 'request_id': 'daily/原号?保留-250',
        'registration_day': '2026-10-31', 'version': 1,
        'expected_summary_hash': 'a' * 64, 'expected_blocked_fingerprint': None,
        'reason': '恢复原件后只重建本地日汇总', 'confirm_rebuild': True}


@pytest.mark.parametrize('change', [{'version': True}, {'confirm_rebuild': 1}, {'confirm_rebuild': False},
    {'registration_day': '2026-10-3'}, {'expected_summary_hash': 'bad'},
    {'request_id': 'short'}, {'reason': '  '}, {'extra_bypass': True}])
def test_rebuild_is_closed_typed_explicit_and_keeps_original_scope(change):
    with pytest.raises(ValidationError): Rebuild.model_validate({**body(), **change})


def test_original_unicode_nonce_and_null_initial_head_are_not_reconstructed():
    value = {**body(), 'version': 0, 'expected_summary_hash': None}
    assert Rebuild.model_validate(value).model_dump() == value


def test_no_ack_metrics_are_unknown_but_actual_received_zero_is_a_model_calculation(monkeypatch):
    packet = {'input': 'source'}
    run = N(id='run', task_id='task', data={'result': {'candidate_pixels': 0,
        'common_valid_pixels': 20, 'unknown_pixels': 0}, 'result_hash': 'r', 'sources_hash': 's'})
    context = {'run': run, 'packet': packet, 'observation': N(id='obs', entity_id='entity')}
    reception = {'state': 'waiting_data'}
    monkeypatch.setattr(subject, 'inventory', lambda *args: [(context, reception)])
    value, _ = subject.content(None, N(workspace_id='workspace'), 'instance', '2026-10-31')
    assert value['counts'] == {'registered_results': 1, 'received_results': 0, 'waiting_results': 1}
    assert all(v is None for v in value['metrics'].values())
    reception.update(state='received', event_id='event', memory_id='memory', receipt={'receipt_hash': 'receipt'})
    value, _ = subject.content(None, N(workspace_id='workspace'), 'instance', '2026-10-31')
    assert value['metrics'] == {'candidate_pixels': 0, 'common_valid_pixels': 20, 'unknown_pixels': 0}
    assert value['metrics_basis'] == 'independently_received_results_only'
    assert value['totals_unit'] == 'model_result_pixels_not_object_count_or_area'
    assert value['human_receipts_basis'] == subject.HUMAN_BASIS
    assert value['candidate_only'] and not value['object_state_updated']

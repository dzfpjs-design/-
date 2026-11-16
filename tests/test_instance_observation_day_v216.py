"""Projection/negative controls, not evidence of real media or accepted sources.

The pure records below are explicitly synthetic. Original source/Task/receipt
validators are not replaced with successful validators in these tests. Root
must additionally verify this view against real bound media and persisted ACKs.
"""
import json
import socket
import subprocess
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import QueryParams

from generic_agents import instance_observation_day_v216 as view
from generic_agents import media_daily_delivery as delivery
from generic_agents import workspace_api


DAY = '2026-10-27'


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Projection tests cannot connect, execute or spend')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.mark.parametrize('bad', ['', '20261027', '2026-1-01', '2026-02-30', '2026-W01-1',
    '2026-10-27T00:00:00Z', ' 2026-10-27', True, 20261027])
def test_date_is_canonical_iso_only(bad):
    with pytest.raises(HTTPException) as error:
        view.requested_day(bad)
    assert error.value.status_code == 422


def test_default_date_uses_china_day_and_valid_explicit_date_is_unchanged(monkeypatch):
    monkeypatch.setattr(view, 'now', lambda: datetime(2026, 10, 26, 17, tzinfo=timezone.utc))
    assert view.requested_day() == DAY
    assert view.requested_day('2024-02-29') == '2024-02-29'


@pytest.mark.parametrize('query', ['day=2026-10-27&day=2026-10-26', 'day=2026-10-27&instance_id=other'])
def test_route_rejects_ambiguous_or_foreign_query_before_database_access(query, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid query must not access instance data')
    monkeypatch.setattr(view, 'read_day', forbidden)
    with pytest.raises(HTTPException) as error:
        view.observation_day('instance', NS(query_params=QueryParams(query)), None, None, DAY)
    assert error.value.status_code == 422


def test_receipt_or_processing_day_is_separate_from_unknown_capture_time():
    assert view._matches_day('audio', {'processing_completed_day': DAY}, '2026-10-20', DAY)
    assert view._matches_day('imagery', {'processing_request_day': DAY}, '2026-10-20', DAY)
    assert view._matches_day('video', {'received_day': DAY}, '2026-10-26', DAY)
    assert not view._matches_day('audio', {'recorded_at': DAY}, '2026-10-20', DAY)


class ReceiptRows:
    """Only the two explicit inventory SELECTs; unknown SQL is not swallowed."""
    def __init__(self, events=(), witnesses=()):
        self.events, self.witnesses, self.reads = list(events), list(witnesses), []
    def scalars(self, statement):
        sql = str(statement)
        params = list(statement.compile().params.values())
        assert 'workspace' in params and 'daily' in params and 3 in params
        self.reads.append(sql)
        if 'FROM outbox_events' in sql:
            assert delivery.KIND in params
            return iter(self.events)
        if 'FROM audit_events' in sql:
            assert delivery.PUBLICATION in params
            return iter(self.witnesses)
        raise AssertionError('Unexpected SELECT: ' + sql)
    def add(self, *_args): raise AssertionError('Read projection cannot create a row')
    def flush(self): raise AssertionError('Read projection cannot flush')
    def commit(self): raise AssertionError('Read projection cannot commit')


def test_producer_memory_is_not_an_ack_and_unpublished_is_not_received():
    db = ReceiptRows()
    daily = NS(id='daily', version=3, data={'data_memory_id': 'producer-memory'})
    ack, proof = view._ack(db, NS(workspace_id='workspace'), daily, 'audio')
    assert ack['status'] == 'not_published' and ack['verified_received'] is False
    assert proof is None and len(db.reads) == 2


def test_deleted_event_with_independent_publication_witness_is_corrupt_not_unpublished():
    db = ReceiptRows(witnesses=['original-publication-audit'])
    ack, proof = view._ack(db, NS(workspace_id='workspace'), NS(id='daily', version=3), 'audio')
    assert ack['status'] == 'integrity_error' and ack['verified_received'] is False
    assert proof is None


def test_unverifiable_independent_receipt_never_uses_event_received_flag(monkeypatch):
    db = ReceiptRows(events=[NS(id='event', status='received')])
    def corrupt(*args):
        raise HTTPException(409, 'Synthetic missing independent receipt Memory')
    monkeypatch.setattr(delivery, 'checked', corrupt)
    ack, proof = view._ack(db, NS(workspace_id='workspace'), NS(id='daily', version=3), 'video')
    assert ack['status'] == 'integrity_error' and not ack['verified_received'] and proof is None


def test_instance_tag_on_a_standalone_audio_run_is_not_a_task_binding(monkeypatch):
    monkeypatch.setattr(view, 'scoped', lambda *_args: NS(id='run', data={}))
    record = {'id': 'run', 'kind': 'transcription', 'instance_id': 'instance', 'task_id': None}
    with pytest.raises(ValueError):
        view._binding(None, None, 'audio', record, NS(id='instance'), NS(id='release'), {}, False)


def test_unbound_upload_is_not_counted_even_within_same_workspace(monkeypatch):
    monkeypatch.setattr(view, 'scoped', lambda *_args: NS(id='run', data={}))
    assert view._binding(None, None, 'audio', {'id': 'run', 'kind': 'transcription'},
        NS(id='instance'), NS(id='release'), {}, False) is None


def test_foreign_task_is_excluded_before_expensive_source_or_execution_reads(monkeypatch):
    source = NS(id='run', data={'workflow_task_id': 'foreign-task'})
    task = NS(data={'instance_id': 'other-instance'})
    monkeypatch.setattr(view, 'scoped', lambda _db, cls, *_args: task if cls is view.Task else source)
    def forbidden(*args, **kwargs):
        raise AssertionError('Foreign Task must not be reclassified or inspected')
    monkeypatch.setattr(workspace_api, 'execution', forbidden)
    assert view._binding(None, None, 'audio', {'id': 'run', 'kind': 'transcription', 'instance_id': 'other-instance'},
        NS(id='instance'), NS(id='release'), {}, False) is None


def test_claimed_current_instance_cannot_hide_a_foreign_actual_task(monkeypatch):
    source = NS(id='run', data={'workflow_task_id': 'foreign-task'})
    task = NS(data={'instance_id': 'other-instance'})
    monkeypatch.setattr(view, 'scoped', lambda _db, cls, *_args: task if cls is view.Task else source)
    with pytest.raises(ValueError):
        view._binding(None, None, 'audio', {'id': 'run', 'kind': 'transcription', 'instance_id': 'instance'},
            NS(id='instance'), NS(id='release'), {}, False)


def test_video_requires_independent_publication_before_considering_a_loose_target(monkeypatch):
    monkeypatch.setattr(view, 'scoped', lambda *_args: NS(id='run', data={}))
    record = {'id': 'run', 'mode': 'upload', 'business_target': {'instance_id': 'instance'}}
    assert view._binding(None, None, 'video', record, NS(id='instance'), NS(id='release'), {}, False) is None


def test_missing_task_objects_or_duplicates_are_not_guessed():
    for identities in ([], ['same', 'same']):
        with pytest.raises(ValueError): view._entities(None, None, None, None, identities)


def test_entity_verification_explicitly_uses_the_readonly_source_path(monkeypatch):
    from generic_agents import entity_release_compat
    monkeypatch.setattr(view, 'scoped', lambda *_args: NS(id='entity', external_id='样区'))
    modes = []
    def verify(*_args, **kwargs):
        modes.append(kwargs)
        raise HTTPException(409, 'Synthetic negative check, not a verified entity')
    monkeypatch.setattr(entity_release_compat, 'require_entity', verify)
    with pytest.raises(HTTPException): view._entities(None, None, None, None, ['entity'])
    assert modes == [{'lock_sources': False}]


def test_same_file_relation_requires_both_evidence_id_and_hash_across_modalities():
    def record(identity, modality, evidence, sha):
        return {'id': identity, 'modality': modality, 'originals': [{'evidence_id': evidence, 'sha256': sha}]}
    video = record('v', 'video', 'evidence', 'a' * 64)
    assert view.same_evidence_links([video, record('a', 'audio', 'another-upload', 'a' * 64)]) == []
    assert view.same_evidence_links([video, record('a', 'audio', 'evidence', 'b' * 64)]) == []
    assert view.same_evidence_links([video, record('v2', 'video', 'evidence', 'a' * 64)]) == []
    links = view.same_evidence_links([video, record('a', 'audio', 'evidence', 'a' * 64)])
    assert len(links) == 1 and links[0]['relation'] == 'same_stored_evidence'
    assert '不代表同物种' in links[0]['label']


def test_unknown_or_corrupt_candidates_do_not_appear_as_zero_species_or_objects():
    for verified, record in [(False, {'saved_frames': 999}), (True, {'integrity_verified': False, 'candidate_masks': 0})]:
        candidate = view._candidate('imagery', record, verified)
        assert '未采用' in candidate['summary'] and '0个' not in candidate['summary']
        assert candidate['not_authoritative'] is True


@pytest.mark.parametrize('status', ['queued', 'running', 'failed', 'cancelled', None])
def test_unsuccessful_processing_is_not_rendered_as_a_successful_zero_detection(status):
    result = view._candidate('audio', {'status': status, 'integrity_verified': True,
        'saved_asr_segments': 0, 'vad_intervals': 0}, True)
    assert '尚无成功保存' in result['summary'] and '0段' not in result['summary']


@pytest.mark.parametrize('kind,extra,expected,excluded', [
    ('transcription', {'whole_asr_performed': True, 'saved_asr_segments': 0, 'vad_intervals': 0},
        '实际执行的整段语音转写', '转写片段0'),
    ('activity', {'whole_asr_performed': False, 'saved_asr_segments': 0, 'vad_intervals': 3},
        '候选区间3段；未执行语音转写', '转写片段0'),
    ('segments', {'whole_asr_performed': False, 'saved_asr_segments': 2, 'vad_intervals': 0},
        '分段转写2段', '活动区间0'),
    ('segments', {'no_speech_candidates': True, 'saved_asr_segments': 0, 'vad_intervals': 0},
        '未选择可转写片段', '分段转写0')])
def test_audio_daily_kinds_only_describe_the_capability_actually_performed(kind, extra, expected, excluded):
    record = {'kind': kind, 'status': 'succeeded', 'integrity_verified': True, **extra}
    result = view._candidate('audio', record, True)
    assert expected in result['summary'] and excluded not in result['summary']
    if kind == 'transcription': assert '0段' not in result['summary']


def test_pure_record_projection_retains_task_object_set_and_hides_raw_result():
    record = {'id': 'run', 'key': 'transcription:run', 'kind': 'transcription', 'name': '任务音频', 'status': 'succeeded',
        'whole_asr_performed': True, 'saved_asr_segments': 0, 'vad_intervals': 0, 'integrity_verified': True,
        'processing_request_day': DAY, 'review_state': 'unreviewed', 'result': {'raw_private_field': 'DO_NOT_RENDER'}}
    binding = {'instance_id': 'instance', 'release_id': 'release', 'business_id': 'business',
        'business_name': '日整理', 'entities': [{'id': 'one', 'name': '样区1'}, {'id': 'two', 'name': '样区2'}],
        'task_id': 'task', 'relation': 'workflow_task_object_set'}
    proof = {'body': {'source_pins': [{'id': 'run', 'kind': 'transcription', 'source_version': 2,
        'evidence': [{'id': 'evidence', 'sha256': 'a' * 64}]}]}}
    item = view._item('audio', record, binding, NS(id='daily', version=3, received_day=DAY),
        {'status': 'pending_delivery', 'verified_received': False, 'integrity_verified': True}, proof)
    assert item['binding']['entities'] == binding['entities'] and item['candidate_only'] is True
    assert item['dates']['original_capture_time'] is None and item['dates']['original_capture_time_state'] == 'unknown'
    assert 'DO_NOT_RENDER' not in json.dumps(item) and 'raw_private_field' not in json.dumps(item)
    assert item['collection']['ack']['verified_received'] is False
    assert item['task_url'].endswith('&task=task')


def test_scan_failure_is_unknown_partial_not_a_successful_empty_day():
    class FailedRead:
        def scalars(self, _statement): raise SQLAlchemyError('Synthetic read failure')
    section = view._section(FailedRead(), NS(workspace_id='workspace'), 'audio', DAY,
        NS(id='instance'), NS(id='release'), {})
    assert section['status'] == 'unavailable' and section['record_count'] is None
    assert not section['count_complete'] and section['issues'][0]['code'] == 'day_read_failed'


def test_collection_scan_cap_is_explicit_and_cannot_claim_zero_complete(monkeypatch):
    monkeypatch.setattr(view, 'MAX_COLLECTIONS', 1)
    monkeypatch.setattr(view, '_ack', lambda *_args: ({'status': 'not_published'}, None))
    class Rows:
        def scalars(self, _statement): return iter([NS(data={'records': []}, version=1), NS(data={'records': []}, version=1)])
    section = view._section(Rows(), NS(workspace_id='workspace'), 'audio', DAY,
        NS(id='instance'), NS(id='release'), {})
    assert section['truncated'] and not section['count_complete'] and section['record_count'] is None
    assert any(issue['code'] == 'collection_scan_limit' for issue in section['issues'])


def test_record_scan_cap_is_explicit_even_when_scanned_sources_are_unbound(monkeypatch):
    monkeypatch.setattr(view, 'MAX_SCAN_RECORDS', 1)
    monkeypatch.setattr(view, '_ack', lambda *_args: ({'status': 'not_published'}, None))
    monkeypatch.setattr(view, '_binding', lambda *_args: None)
    daily = NS(id='daily', version=1, received_day=DAY, data={'records': [
        {'id': 'one', 'kind': 'transcription'}, {'id': 'two', 'kind': 'transcription'}]})
    class Rows:
        def scalars(self, _statement): return iter([daily])
    section = view._section(Rows(), NS(workspace_id='workspace'), 'audio', DAY,
        NS(id='instance'), NS(id='release'), {})
    assert section['truncated'] and not section['count_complete']
    assert section['record_count'] is None
    assert any(issue['code'] == 'record_scan_limit' for issue in section['issues'])


def test_read_projection_requests_all_modalities_without_flush_receive_or_refresh(monkeypatch):
    db = ReceiptRows(); db.no_autoflush = nullcontext()
    monkeypatch.setattr(view, '_scope', lambda *_args: (NS(id='instance'), NS(id='release'), {}))
    calls = []
    def section(_db, _p, modality, selected, *_args):
        calls.append((modality, selected))
        return {'modality': modality, 'records': [], 'count_complete': True}
    monkeypatch.setattr(view, '_section', section)
    result = view.read_day(db, NS(role='viewer'), 'instance', DAY)
    assert calls == [('video', DAY), ('audio', DAY), ('imagery', DAY)]
    assert result['status'] == 'available' and result['empty_message'] is not None
    assert result['cloud_calls'] == 0 and result['ecological_report'] is False
    assert result['entity_state_updated'] is result['species_identification_verified'] is False
    assert not db.reads


def test_scope_permission_failure_is_not_relabelled_as_an_empty_instance(monkeypatch):
    def denied(*args): raise HTTPException(403, 'Synthetic ordinary membership denial')
    monkeypatch.setattr(view, '_scope', denied)
    db = NS(no_autoflush=nullcontext())
    with pytest.raises(HTTPException) as error:
        view.read_day(db, NS(role='viewer'), 'instance', DAY)
    assert error.value.status_code == 403

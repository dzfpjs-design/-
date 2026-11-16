"""Synthetic native recovery scopes, authored SOURCE ONLY for Root's owned PG.

Only provider HTTP transport and the named real bridge/FS failure point are
fake. Normal API registration, source selection, reserve/finish, worker, capture,
both journal versions, projections and recovery run unchanged. These cases do
not prove actual model quality, token fit, pricing or a physical restart.
"""
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.db import engine
from generic_agents.models import Audit, SetupReportDraft
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids, original
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, DELIVERY, REDUCE5, NativeHTTP, v7_scene, start, mapped, get,
    capture_view, original_capture, storage_files, call_job_rows,
)


def stage_value(draft, key):
    return draft['reduction'] if key == 'reduce' else next(
        item for item in draft['maps'] if item['chunk_id'] == key)


@pytest.mark.parametrize('mode', [
    'last_map_before_journal', 'reduce_before_journal', 'reduce_half_journal',
])
def test_native_free_recovery_completes_exact_all_maps_or_reduction(v7_scene, monkeypatch, mode):
    scene = v7_scene
    http = NativeHTTP(monkeypatch)
    reducing = mode != 'last_map_before_journal'
    if reducing:
        preview, _, _, middle = mapped(scene, http)
        body = {'revision': middle['revision'], 'request_id': 'fer_' + str(uuid4())}
        reduce_preview = reply(scene['client'].post(BASE + '/' + middle['id'] + '/reduce-preview', json=body))
        spec = reduce_preview['reduction_preparation']['generation_contract']
        assert spec['schema'] == REDUCE5 and spec['evidence_stage'] == 'reduce-v5'
        assert spec['delivery'] == DELIVERY and spec['dispatch_options']['max_tokens'] == 32768
        assert len(http.requests) == http.expected_maps
        command = {**body, 'preview_hash': reduce_preview['preview_hash'], 'consent': True}
        accepted = reply(scene['client'].post(BASE + '/' + middle['id'] + '/reduce', json=command), 202)
        assert accepted['receipt']['schema'] == 'report-evidence-reduce-receipt-v3'
        target = 'reduce'
    else:
        preview, _, accepted = start(scene, http)
        # The actual complete plan chooses the last stage; never assume two.
        target = preview['map_preparation']['plan']['chunks'][-1]['chunk_id']
        assert target == 'map-' + str(http.expected_maps)
        before_missing = all_rows(), storage_files()
        queued = get(scene, accepted['draft']['id'])
        missing = capture_view(scene, queued, target)
        assert queued == accepted['draft'] and queued['status'] == 'queued'
        assert missing['reserved_call_id'] is None and missing['capture_state'] == 'missing'
        assert missing['body_bytes'] == 0 and missing['response_complete'] is False
        assert missing['recovery_eligible'] is False and missing['capture_hash'] is None
        assert (all_rows(), storage_files()) == before_missing and http.requests == []

    fired = []
    with monkeypatch.context() as fault:
        if mode == 'reduce_half_journal':
            adapter_io = pipeline.journal_v2
            actual_new = adapter_io._new_file
            def interrupted(directory, name, raw):
                if name == 'receipt.bin' and not fired:
                    fired.append(target)
                    raise OSError('PUBLIC synthetic Reduce-v2 journal interruption after wire fsync')
                return actual_new(directory, name, raw)
            fault.setattr(adapter_io, '_new_file', interrupted)
        else:
            actual_write = bridge.write_missing_journal
            def interrupted(adapter_io, storage_root, binding, wire, receipt):
                if binding['stage'] == target and not fired:
                    fired.append(target)
                    raise OSError('PUBLIC synthetic complete native capture before target journal')
                return actual_write(adapter_io, storage_root, binding, wire, receipt)
            fault.setattr(bridge, 'write_missing_journal', interrupted)
        assert process_once(accepted['draft']['job_id'])
    # Failure injection is now removed; recovery goes through the normal API.
    assert fired == [target]
    stopped = get(scene, accepted['draft']['id'])
    assert stopped['status'] in {'failed', 'response_unknown'} and stopped['model_result'] is None
    assert len(http.requests) == http.expected_maps + int(reducing)
    target_before = stage_value(stopped, target)
    call_id = target_before['reserved_call_id']
    assert call_id and target_before['status'] != 'succeeded' and target_before['journal'] is None
    assert all(item['status'] == 'succeeded' for item in (stopped['maps'] if reducing else stopped['maps'][:-1]))
    assert http.requests[-1]['stage'] == ('reduce-v5' if reducing else 'map-v7')

    context, state, raw = original_capture(scene, stopped, target)
    assert raw == http.responses[-1]
    assert state['state'] == 'complete' and state['response_complete'] is True
    assert state['ready'] is True and state['content_encoding_safe'] is True
    assert context['binding']['reserved_call_id'] == call_id and context['binding']['stage'] == target
    assert context['binding']['schema'] == 'report-evidence-journal-binding-v' + ('2' if reducing else '1')
    assert context['max_bytes'] == (1024 * 1024 if reducing else 256 * 1024)
    rows, files, money = all_rows(), storage_files(), fees(call_ids(stopped))
    calls_and_jobs, request_count = call_job_rows(), len(http.requests)
    view = capture_view(scene, stopped, target)
    assert view['capture_hash'] == state['capture_hash'] and view['recovery_eligible'] is True
    assert view['response_complete'] is True and view['content_encoding_safe'] is True
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], stopped['id']), scene['p']) == stopped
    stale = scene['client'].post(BASE + '/' + stopped['id'] + '/recover-saved',
        json={'revision': stopped['revision'] - 1, 'request_id': str(uuid4())})
    assert stale.status_code == 409
    assert all_rows() == rows and storage_files() == files and fees(call_ids(stopped)) == money

    namespace = 'report-evidence-reduction-journal-v2' if reducing else 'report-evidence-journal'
    wire_name = str(Path(namespace) / stopped['workspace_id'] / stopped['id'] / target / call_id / 'wire.bin')
    assert (wire_name in files) == (mode == 'reduce_half_journal')
    if mode == 'reduce_half_journal':
        assert files[wire_name][0].decode('utf-8', 'surrogatepass') == raw.decode('utf-8', 'surrogateescape')

    recovery_command = {'revision': stopped['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + stopped['id'] + '/recover-saved', json=recovery_command))
    current, receipt = recovered['draft'], recovered['receipt']
    target_after = stage_value(current, target)
    assert receipt['schema'] == 'report-evidence-recovery-receipt-v2'
    assert receipt['completion_scope'] == ('reduction' if reducing else 'all_maps')
    assert receipt['remaining_chunks'] == [] and receipt['new_cloud_calls'] == 0
    assert receipt['accepted_revision'] == stopped['revision'] + 1 == current['revision']
    assert receipt['recovered_stages'] == [{'stage': target, 'call_id': call_id,
        'capture_hash': state['capture_hash'],
        'journal_descriptor_hash': target_after['journal']['descriptor_hash'],
        'result_hash': target_after['result_hash']}]
    assert target_after['status'] == 'succeeded' and target_after['reserved_call_id'] == call_id
    assert target_after['journal']['schema'] == 'report-evidence-journal-descriptor-v' + ('2' if reducing else '1')
    assert current['preview'] == preview and current['review_history'] == stopped['review_history'] == []
    if reducing:
        assert current['status'] == 'review' and current['phase'] == 'review'
        assert current['model_result'] is not None and current['model_result_hash']
        assert current['maps'] == stopped['maps']
        assert current['reduction']['preview'] == reduce_preview
        assert current['reduction']['projection_metadata'] is not None
    else:
        assert current['status'] == 'awaiting_merge' and current['phase'] == 'awaiting_reduce'
        assert all(item['status'] == 'succeeded' for item in current['maps'])
        assert current['maps'][:-1] == stopped['maps'][:-1]
        assert current['model_result'] is None and current['reduction'] == stopped['reduction']

    saved = original(scene, current, target)
    assert saved['content'].encode('utf-8', 'surrogateescape') == raw
    assert saved['descriptor'] == target_after['journal']
    assert saved['receipt']['call_id'] == call_id and saved['receipt']['capture_hash'] == state['capture_hash']
    assert saved['receipt']['response_complete'] is True
    assert saved['receipt']['http_content_encoding'] == state['http_content_encoding']
    assert saved['receipt']['finish_reason'] == 'stop' and saved['receipt']['response_choice_count'] == 1
    assert saved['receipt']['tool_call_count'] == 0
    assert all(saved['receipt'][name] is None for name in ('tool_call_id', 'tool_name', 'tool_type', 'tool_calls_hash'))
    assert call_ids(current) == call_ids(stopped) and call_job_rows() == calls_and_jobs
    assert fees(call_ids(current)) == money and len(http.requests) == request_count
    after_files = storage_files()
    # In particular the half journal's already-fsynced wire inode/bytes/mtime
    # remain exact; recovery may only add absent files for this same call.
    assert all(after_files[name] == value for name, value in files.items())
    after_rows = all_rows()
    for table, before in rows.items():
        if table not in {SetupReportDraft.__tablename__, Audit.__tablename__}:
            assert after_rows[table] == before
    assert set(after_rows[SetupReportDraft.__tablename__]) == set(rows[SetupReportDraft.__tablename__])
    assert all(after_rows[SetupReportDraft.__tablename__][identity] == value
        for identity, value in rows[SetupReportDraft.__tablename__].items() if identity != current['id'])
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], current['id']), scene['p']) == current
    replay_rows, replay_files = all_rows(), storage_files()
    assert reply(scene['client'].post(BASE + '/' + current['id'] + '/recover-saved', json=recovery_command)) == recovered
    assert reply(scene['client'].get(BASE + '/' + current['id'] + '/recovery-receipts/' + recovery_command['request_id'])) == recovered
    assert reply(scene['client'].get(BASE + '/' + current['id'])) == current
    changed = {**recovery_command, 'revision': current['revision']}
    assert scene['client'].post(BASE + '/' + current['id'] + '/recover-saved', json=changed).status_code == 409
    assert all_rows() == replay_rows and storage_files() == replay_files
    assert fees(call_ids(current)) == money and len(http.requests) == request_count

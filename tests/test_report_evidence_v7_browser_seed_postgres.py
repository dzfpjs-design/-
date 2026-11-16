"""Root-only one-case browser seed in a fresh EMPTY owned PG63131.

The owned harness supplies and privately retains the normal account. This file
does not override an account, inspect credentials or clean retained rows/files.
Only public synthetic documents/OCR and HTTP MockTransport are used; the normal
API, reservation, worker, native capture, journals, projections and two human
saves are real. Seed success proves neither model quality nor browser behavior.
Root alone runs this source with its isolated owned-PG fixture plugin, then
cleanly stops and physically copies the new cluster/storage for browser work.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.db import engine
from generic_agents.models import Base
from generic_agents.report_evidence_manual_v6_adapter import manual_from_projection_v6
from generic_agents import report_evidence_reduction_wire_v6 as wire6
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids, original
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, GEN7, REDUCE5, DELIVERY, NativeHTTP, v7_scene, mapped, reduced,
    original_capture, storage_files,
)


TEST_NAME = 'test_v7_browser_seed_retains_normal_native_model_and_two_full_human_saves'
LATEST_SERIALIZER = 'v7-browser-saved-latest.json'
LOCATOR = 'v7-browser-seed-locator.json'


def _owned_63131(*, empty=False):
    fence()
    assert os.environ.get('GA_F11_DUAL_TESTS_OWNED') == '63131', 'v7_browser_seed_owned_port'
    url = engine().url
    assert (url.host in {'127.0.0.1', 'localhost'} and url.port == 63131
        and url.database == 'ga_f11_dual_tests'), 'v7_browser_seed_owned_database'
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        actual = db.execute(text("SELECT current_database(),inet_server_port(),"
            "shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")).one()
        assert tuple(actual) == ('ga_f11_dual_tests', 63131, 'owned-disposable-f11-dual')
        if empty:
            assert len(Base.metadata.tables) == 106
            assert all(db.scalar(select(func.count()).select_from(table)) == 0
                for table in Base.metadata.sorted_tables), 'v7_browser_seed_requires_empty_database'


@pytest.fixture(autouse=True)
def v7_browser_seed_empty_owned_database(request):
    # Function-scoped autouse precedes explicit v7_scene/account fixtures. Root
    # owns the one-case fresh-cluster/reset ordering; this is an additional real
    # read-only emptiness gate, never an inference from a successful fixture.
    assert request.node.name == TEST_NAME
    _owned_63131(empty=True)


def _write_public(directory, name, raw, maximum):
    assert type(raw) is bytes and len(raw) <= maximum
    assert directory.is_absolute() and directory.is_dir() and not directory.is_symlink()
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw); output.flush(); os.fsync(output.fileno())
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def test_v7_browser_seed_retains_normal_native_model_and_two_full_human_saves(v7_scene, monkeypatch):
    _owned_63131()
    directory = os.environ.get('GA_PUBLIC_DTO_OUT')
    assert directory, 'v7_browser_seed_requires_public_output_directory'
    output = Path(directory)
    assert output.is_absolute() and output.is_dir() and not output.is_symlink()
    scene = v7_scene
    models = reply(scene['client'].get('/api/setup/understanding/models'))
    listed = [item for item in models if item['id'] == scene['cap']['id']]
    assert len(listed) == 1
    assert listed[0]['provider'] == scene['cap']['data']['execution']['provider'] == 'qwen'
    assert listed[0]['model'] == scene['cap']['data']['execution']['model'] == 'qwen3.8-flash'
    http = NativeHTTP(monkeypatch)
    preview, start_command, accepted, middle = mapped(scene, http)
    _, reduce_command, reduce_accepted, final = reduced(scene, http, middle)
    assert preview['map_preparation']['generation_contract']['schema'] == GEN7
    assert final['reduction']['preview']['reduction_preparation']['generation_contract']['schema'] == REDUCE5
    assert final['schema'] == 'report-evidence-reading-draft-v3' and final['status'] == 'review'
    assert len(http.requests) == http.expected_maps + 1
    assert [item['stage'] for item in http.requests] == ['map-v7'] * http.expected_maps + ['reduce-v5']
    for index, key in enumerate([item['chunk_id'] for item in final['maps']] + ['reduce']):
        context, state, raw = original_capture(scene, final, key)
        saved = original(scene, final, key)
        assert state['ready'] is True and state['response_complete'] is True and state['content_encoding_safe'] is True
        assert raw == http.responses[index] == saved['content'].encode('utf-8', 'surrogateescape')
        assert saved['receipt']['delivery'] == DELIVERY and saved['receipt']['finish_reason'] == 'stop'
        assert saved['receipt']['tool_call_count'] == 0 and saved['receipt']['response_choice_count'] == 1
        assert saved['receipt']['capture_hash'] == state['capture_hash']
        assert context['binding']['schema'] == 'report-evidence-journal-binding-v' + ('2' if key == 'reduce' else '1')

    model, maps, reduction = (copy.deepcopy(final[key]) for key in ('model_result', 'maps', 'reduction'))
    human = manual_from_projection_v6(model, note='公开合成原生读取浏览器种子；完整职责和出处待核。')
    assert human['schema'] == 'report-evidence-manual-review-v5'
    assert human['result'] == wire6.decode_reduction_wire_v6(http.values[-1])['result']
    assert human['result']['global_claims'] and human['result']['businesses'][0]['steps']
    assert all(human['result']['businesses'][0][key] for key in ('participants', 'interactions', 'conditions', 'coordination'))
    first_command = {'revision': final['revision'], 'request_id': str(uuid4()), 'value': human}
    first = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=first_command))
    second_value = copy.deepcopy(human)
    second_value['note'] += ' 第二次正常人工保存已读回。'
    second_value['result']['businesses'][0]['steps'][0]['role'] = '合成原生后来值班员'
    second_command = {'revision': first['draft']['revision'], 'request_id': str(uuid4()), 'value': second_value}
    second = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=second_command))
    latest = second['draft']
    assert second['current_matches'] is True and latest['review_value'] == second_value
    assert [item['value'] for item in latest['review_history']] == [human, second_value]
    assert latest['model_result'] == model and latest['model_result_hash'] == final['model_result_hash']
    assert latest['preview'] == preview and latest['maps'] == maps and latest['reduction'] == reduction
    assert latest['can_apply'] is latest['can_publish'] is False

    rows, files, money, calls = all_rows(), storage_files(), fees(call_ids(latest)), len(http.requests)
    replay = reply(scene['client'].put(BASE + '/' + latest['id'] + '/review', json=first_command))
    assert replay['receipt'] == first['receipt'] and replay['current_matches'] is False and replay['draft'] == latest
    assert reply(scene['client'].get(BASE + '/' + latest['id'] + '/review-receipts/' + first_command['request_id'])) == replay
    assert reply(scene['client'].put(BASE + '/' + latest['id'] + '/review', json=second_command)) == second
    assert reply(scene['client'].post(BASE, json=start_command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + latest['id'] + '/reduce', json=reduce_command), 202)['receipt'] == reduce_accepted['receipt']
    assert scene['client'].put(BASE + '/' + latest['id'] + '/review',
        json={**first_command, 'request_id': str(uuid4())}).status_code == 409
    changed = copy.deepcopy(first_command); changed['value']['note'] += ' 同号改体必须拒绝。'
    assert scene['client'].put(BASE + '/' + latest['id'] + '/review', json=changed).status_code == 409
    found = reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id']))
    response = scene['client'].get(BASE + '/' + latest['id'])
    assert found['draft'] == reply(response) == latest
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], latest['id']), scene['p']) == latest
    assert all_rows() == rows and storage_files() == files and fees(call_ids(latest)) == money
    assert len(http.requests) == calls and call_ids(latest) == call_ids(final)

    _write_public(output, LATEST_SERIALIZER, response.content, 8 * 1024 * 1024)
    me = reply(scene['client'].get('/api/me'))
    locator = {'schema': 'report-evidence-v7-browser-seed-v1', 'synthetic_empty_database': True,
        'browser_verified': False, 'model_quality_verified': False, 'real_provider_calls': 0,
        'actor_id': me['user_id'], 'workspace_id': me['workspace_id'], 'draft_id': latest['id'],
        'material_id': scene['original']['id'], 'copy_id': scene['copy']['id'], 'model_id': scene['cap']['id'],
        'request_id': latest['request_id'], 'draft_revision': latest['revision'], 'review_count': 2,
        'review_receipt_hash': second['receipt']['receipt_hash'], 'model_result_hash': latest['model_result_hash'],
        'source_hash': preview['source_hash'], 'latest_serializer': LATEST_SERIALIZER,
        'latest_serializer_sha256': hashlib.sha256(response.content).hexdigest(),
        'seed_test_file': Path(__file__).name, 'seed_test_name': TEST_NAME}
    raw = (json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    _write_public(output, LOCATOR, raw, 8192)
    assert all_rows() == rows and storage_files() == files and fees(call_ids(latest)) == money
    assert len(http.requests) == calls
    _owned_63131()

"""Source-version compatibility and strict schemas; no provider or database.

All journal content here is newly created synthetic text. Historical V1 is
constructed with its original public task contract, never a real paid answer.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import report_evidence_preparation as preparation
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import MapResult, evidence_payload, validate_map_result
from tests.test_report_evidence_preparation_pure import cap, catalog, reverse_json_keys
from tests.test_report_evidence_pipeline_pure import offline, scene, store, map_value


V1_TASK_SHA256 = '573772f882562bc644ad129944878273b7963c7d69a04b09e9ab09f39d1e2189'


def historical_scene(version=preparation.GENERATION_V1):
    p, model, body, row = scene()
    preview = pipeline.build_preview(p, body, {'businesses': ['原业务']}, model,
        row.data['preview']['source_snapshot'], generation_version=version)
    command = pipeline.StartCommand(**body.model_dump(mode='json'), preview_hash=preview['preview_hash'], consent=True)
    row.data = pipeline._new_data(p, row.id, command, preview, row.data['job_id'])
    return p, model, body, row, command


def forbidden(*args, **kwargs):
    raise AssertionError('No fresh authorization, provider, SQL or checkpoint may occur here')


def test_new_api_default_is_v3_while_the_legacy_preparation_helper_remains_v2():
    assert pipeline.CURRENT_MAP_GENERATION == preparation.GENERATION_V3
    assert preparation.GENERATION == preparation.GENERATION_V2
    p, model, body, row = scene()
    preview = pipeline.build_preview(p, body, {'businesses': ['原业务']}, model,
        row.data['preview']['source_snapshot'])
    assert preview['map_preparation']['generation_contract']['schema'] == preparation.GENERATION_V3
    command = pipeline.StartCommand(**body.model_dump(mode='json'), preview_hash=preview['preview_hash'], consent=True)
    row.data = pipeline._new_data(p, row.id, command, preview, row.data['job_id'])
    assert pipeline.draft_view(row, p)['schema'] == 'report-evidence-reading-draft-v2'


def test_v1_task_literal_and_exact_original_message_layout_remain_frozen():
    assert hashlib.sha256(preparation.EVIDENCE_MAP_TASK_V1.encode()).hexdigest() == V1_TASK_SHA256
    model, source, context, note = cap(), catalog(), {'object_names': []}, ' 原文字词＋+不变。 '
    value = preparation.build_map_preparation(model, source, context, note,
        generation_version=preparation.GENERATION_V1)
    spec = value['generation_contract']
    assert spec['schema'] == 'report-evidence-map-generation-v1'
    assert set(spec) == {'schema', 'task', 'output_schema', 'gateway_prefix_hash', 'dispatch_options',
        'input_policy', 'max_prepared_bytes', 'max_chunks'}
    assert preparation.check_map_preparation(reverse_json_keys(value), reverse_json_keys(source), model) == value
    for chunk in value['plan']['chunks']:
        payload = evidence_payload(source, chunk['span_refs'])
        requested = [
            {'role': 'system', 'content': json.dumps({'schema': 'report-evidence-map-task-v1',
                'task': preparation.EVIDENCE_MAP_TASK_V1, 'purpose': 'candidate_only',
                'source_policy': 'untrusted_evidence', 'full_report_confirmed': False}, ensure_ascii=False, sort_keys=True)},
            {'role': 'user', 'content': json.dumps({**payload, 'context': context, 'user_instruction': note},
                ensure_ascii=False, sort_keys=True)}]
        expected, _, images = preparation.prepare_cloud_json(model, requested, spec['output_schema'])
        rebuilt = preparation.rebuild_map_request(value, source, model, chunk['chunk_id'])
        assert images == 0 and rebuilt['messages'] == expected
        assert rebuilt['messages_hash'] == digest(expected) == chunk['messages_hash']
        assert rebuilt['input_summary'] == chunk['input_summary']


def test_new_preview_v2_explains_the_unchanged_schema_enums_and_field_constraints():
    model, source = cap(), catalog()
    old = preparation.map_generation(model, generation_version=preparation.GENERATION_V1)
    value = preparation.build_map_preparation(model, source, {})
    spec = value['generation_contract']; properties = MapResult.model_json_schema()['$defs']['EvidenceClaim']['properties']
    assert spec['schema'] == preparation.GENERATION_V2 == preparation.GENERATION
    assert spec['output_schema'] == old['output_schema']
    assert spec['task'].startswith(preparation.EVIDENCE_MAP_TASK_V1)
    for field in ('kind', 'status'):
        assert json.dumps(properties[field]['enum'], ensure_ascii=False, separators=(',', ':')) in spec['task']
    assert 'key、kind、statement、span_refs、status、explanation六个字段' in spec['task']
    assert all(word in spec['task'] for word in ('2000', '1000', '最多16', '其他', '不确定性'))
    chunk = value['plan']['chunks'][0]
    request = preparation.rebuild_map_request(value, source, model, chunk['chunk_id'])
    assert json.loads(request['messages'][1]['content'])['schema'] == 'report-evidence-map-task-v2'
    assert request['messages_hash'] != preparation.rebuild_map_request(
        preparation.build_map_preparation(model, source, {}, generation_version=preparation.GENERATION_V1),
        source, model, chunk['chunk_id'])['messages_hash']
    p, _, _, row = scene()
    assert pipeline.check_preview(row.data['preview'], p) == row.data['preview']


@pytest.mark.parametrize('version', [preparation.GENERATION_V1, preparation.GENERATION_V2])
def test_each_frozen_version_roundtrips_jsonb_and_rebuilds_its_own_dispatch(version):
    model, source = cap(), catalog()
    value = preparation.build_map_preparation(model, source, {}, generation_version=version)
    stored = reverse_json_keys(value)
    assert preparation.check_map_generation(stored['generation_contract'], model) == value['generation_contract']
    assert preparation.check_map_preparation(stored, source, model) == value
    for chunk in value['plan']['chunks']:
        before = preparation.rebuild_map_request(value, source, model, chunk['chunk_id'])
        after = preparation.rebuild_map_request(stored, source, model, chunk['chunk_id'])
        actual, _, _ = preparation.prepare_cloud_json(model, after['messages'][1:], value['generation_contract']['output_schema'])
        assert before == after and actual == before['messages']


@pytest.mark.parametrize('change', ['unknown_version', 'v1_with_v2_task', 'v2_with_v1_task'])
def test_unknown_or_mixed_generation_is_not_repaired_even_after_rehash(change):
    model, source = cap(), catalog(); value = preparation.build_map_preparation(model, source, {})
    if change == 'unknown_version': value['generation_contract']['schema'] = 'report-evidence-map-generation-v99'
    elif change == 'v1_with_v2_task': value['generation_contract']['schema'] = preparation.GENERATION_V1
    else: value['generation_contract']['task'] = preparation.EVIDENCE_MAP_TASK_V1
    value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
    with pytest.raises(ValueError): preparation.check_map_generation(value['generation_contract'], model)
    with pytest.raises(ValueError): preparation.check_map_preparation(value, source, model)
    with pytest.raises(ValueError): preparation.rebuild_map_request(value, source, model, 'map-1')
    with pytest.raises(ValueError): preparation.map_generation(model, generation_version='unknown')


@pytest.mark.parametrize('field,value', [('kind', '未登记的新分类'), ('status', 'confirmed')])
def test_v2_does_not_expand_or_normalize_invalid_original_claims(field, value):
    source = catalog(); ref = next(iter(source['index']))
    original = {'schema': 'report-evidence-map-v1', 'claims': [{'key': 'claim_1', 'kind': '职责',
        'statement': '合成原声明。', 'span_refs': [ref], 'status': 'explicit', 'explanation': ''}]}
    original['claims'][0][field] = value; before = copy.deepcopy(original)
    with pytest.raises(ValidationError): validate_map_result(original, source, [ref])
    assert original == before


@pytest.mark.parametrize('version', [preparation.GENERATION_V1, preparation.GENERATION_V2])
def test_accepted_v1_nonce_and_failed_journal_stay_readable_before_fresh_gates(monkeypatch, version):
    p, _, _, row, command = historical_scene(version); key = row.data['maps'][0]['chunk_id']
    bad = map_value(row, key); bad['claims'][0]['kind'] = '未登记的新分类'
    store(row, key, bad, succeeded=False); pipeline._stage(row, key)['status'] = 'failed'
    row.status = 'failed'; row.data['phase'] = 'failed'; before = copy.deepcopy(row.data)
    descriptor = pipeline._descriptor(row, key)
    monkeypatch.setattr(pipeline.legacy, 'by_nonce', lambda *args: row)
    monkeypatch.setattr(pipeline, 'require', forbidden)
    monkeypatch.setattr(pipeline, '_fresh_cap', forbidden)
    reply = pipeline.start_route(command, p, object())
    assert reply['receipt'] == before['start_receipt'] and reply['draft']['status'] == 'failed'
    assert reply['draft']['maps'][0]['result'] is None and reply['draft']['maps'][0]['journal'] == descriptor
    assert pipeline.journal.parse_response(pipeline._journal_root(), pipeline.journal_binding(
        row, key, pipeline._stage(row, key)['reserved_call_id']), descriptor)['value'] == bad
    assert row.data == before


@pytest.mark.parametrize('version', [preparation.GENERATION_V1, preparation.GENERATION_V2])
def test_original_v1_reduction_preparation_and_receipt_rebuild_without_using_current_map_default(monkeypatch, version):
    p, _, _, row, _ = historical_scene(version)
    for stage in row.data['maps']:
        store(row, stage['chunk_id'], map_value(row, stage['chunk_id']))
    row.status = 'awaiting_merge'; row.data['phase'] = 'awaiting_reduce'
    body = pipeline.ReducePreviewCommand(revision=row.version, request_id='fer_' + str(uuid4()))
    preview = pipeline.reduce_preview(row, p, body)
    command = pipeline.ReduceCommand(**body.model_dump(mode='json'), preview_hash=preview['preview_hash'], consent=True)
    row.data['reduction'].update(preview=preview, start_receipt=pipeline._reduce_receipt(row, p, command, preview))
    row.version += 1; before = copy.deepcopy(row.data)
    request = pipeline._request(row, 'reduce'); prepared = preview['reduction_preparation']
    assert prepared['generation_contract']['map_generation_hash'] == digest(row.data['preview']['map_preparation']['generation_contract'])
    assert request['messages_hash'] == prepared['messages_hash']
    assert pipeline.check_row(row, p) is row
    monkeypatch.setattr(pipeline, 'owned', lambda *args: row)
    monkeypatch.setattr(pipeline, 'require', forbidden)
    monkeypatch.setattr(pipeline, '_fresh_cap', forbidden)
    assert pipeline.reduce_start_route(UUID(row.id), command, p, object())['receipt'] == before['reduction']['start_receipt']
    assert row.data == before


@pytest.mark.parametrize('map_source', [True, False])
@pytest.mark.parametrize('version', [preparation.GENERATION_V1, preparation.GENERATION_V2])
def test_v1_new_map_or_reduce_authorization_stops_before_model_source_or_sql(monkeypatch, map_source, version):
    p, _, _, row, _ = historical_scene(version); before = copy.deepcopy(row.data)
    monkeypatch.setattr(pipeline.legacy.old(), 'model', forbidden)
    with pytest.raises(HTTPException) as error:
        pipeline._fresh_cap(object(), p, row, map_source=map_source)
    assert error.value.status_code == 409 and error.value.detail['code'] == 'evidence_task_version_changed'
    assert row.data == before


@pytest.mark.parametrize('version', [preparation.GENERATION_V1, preparation.GENERATION_V2])
def test_v1_worker_cannot_pay_for_an_unstarted_stage(monkeypatch, version):
    p, _, _, row, _ = historical_scene(version); before = copy.deepcopy(row.data)
    monkeypatch.setattr(pipeline.legacy.old(), 'model', forbidden)
    monkeypatch.setattr(pipeline, 'checkpoint', forbidden)
    with pytest.raises(HTTPException) as error:
        pipeline._run_stage(object(), p, row, row.data['maps'][0]['chunk_id'])
    assert error.value.detail['code'] == 'evidence_task_version_changed' and row.data == before


@pytest.mark.parametrize('version', [preparation.GENERATION_V1, preparation.GENERATION_V2])
def test_free_v1_raw_recovery_uses_original_validation_and_never_upgrades_bad_kind(monkeypatch, version):
    p, _, _, row, _ = historical_scene(version); originals = {}
    for stage in row.data['maps']:
        key = stage['chunk_id']; value = map_value(row, key)
        if not originals: value['claims'][0]['kind'] = '未登记的新分类'
        originals[key] = copy.deepcopy(value); store(row, key, value, succeeded=False)
        pipeline._stage(row, key)['status'] = 'failed'
    row.status = 'failed'; row.data['phase'] = 'failed'; before = copy.deepcopy(row.data)
    monkeypatch.setattr(pipeline, 'owned', lambda *args: row)
    monkeypatch.setattr(pipeline, '_fresh_cap', forbidden)
    monkeypatch.setattr(pipeline, '_settle', lambda *args: None)  # pure test has no fee database
    with pytest.raises(HTTPException) as error:
        pipeline.recover_saved_route(UUID(row.id), pipeline.RecoveryCommand(
            revision=row.version, request_id=uuid4()), p, NS(rollback=lambda: None))
    assert error.value.status_code == 422 and error.value.detail['code'] == 'evidence_saved_response_invalid'
    assert row.data == before
    for key, value in originals.items():
        assert pipeline._parsed(row, key)['value'] == value


def test_unaccepted_v1_preview_does_not_silently_start_the_new_v2(monkeypatch):
    p, model, body, row, old_command = historical_scene()
    fresh = pipeline.build_preview(p, body, {'businesses': ['原业务']}, model, row.data['preview']['source_snapshot'],
        generation_version=preparation.GENERATION_V2)
    monkeypatch.setattr(pipeline.legacy, 'by_nonce', lambda *args: None)
    monkeypatch.setattr(pipeline.legacy, 'workspace_draft', lambda *args: None)
    monkeypatch.setattr(pipeline.legacy, 'guard_existing_paid', lambda *args: None)
    monkeypatch.setattr(pipeline, 'make_preview', lambda *args: fresh)
    with pytest.raises(HTTPException) as error:
        pipeline.start_route(old_command, p, object())
    assert error.value.status_code == 409 and error.value.detail['code'] == 'preview_changed'
    assert fresh['preview_hash'] != old_command.preview_hash

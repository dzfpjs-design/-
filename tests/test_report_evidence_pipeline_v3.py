"""Owned empty-PG V3 integration; root alone executes this file.

Only provider transport and local source conversion/OCR are synthetic. Actual
authorization, CloudCall reserve/settle, Job claims/leases, immutable journal,
wire projection and private review persistence stay production code. This is
protocol/durability evidence, never a real-report semantic-quality result.
"""
import copy
from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session

from generic_agents import cloud_budget
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_preparation as preparation
from generic_agents import report_evidence_wire_v3 as wire
from generic_agents.api import app
from generic_agents.cloud_evidence_profiles import evidence_policy, evidence_options
from generic_agents.cloud_input_profiles import measure_input
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.gateway import ModelBlocked, cloud_json_options, prepare_cloud_json
from generic_agents.leases import LeaseLost
from generic_agents.models import Base, CloudCall, Job, SetupReportDraft
from generic_agents.report_evidence_manual_v5 import manual_from_projection
from generic_agents.services import validate
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene, example_candidate
from tests.test_report_evidence_pipeline import reply, capture
from tests.test_vertical_slice import publish


BASE = '/api/setup/report-evidence-readings'


@pytest.fixture
def v3_scene(reading_scene):
    fence()
    assert preparation.GENERATION == preparation.GENERATION_V2  # legacy pure helper contract stays unchanged
    assert pipeline.CURRENT_MAP_GENERATION == preparation.GENERATION_V3
    scene = reading_scene
    manifest = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    manifest.update(name='synthetic_evidence_wire_' + uuid4().hex[:10],
        description='Synthetic V3 protocol only; no provider HTTP or model-quality claim')
    manifest['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192,
        report_evidence_output={'schema': 'report-evidence-output-v1', 'max_tokens': 131072})
    # Actual register -> validate -> publish API path. The old Qwen publication
    # from reading_scene remains unchanged; no capability row is SQL-rewritten.
    cap = publish(scene['client'], manifest)
    execution = cap['data']['execution']
    assert cap['status'] == 'published' and execution['max_tokens'] == 8192
    assert execution['report_evidence_output'] == {'schema': 'report-evidence-output-v1', 'max_tokens': 131072}
    assert cloud_json_options(execution)['max_tokens'] == 8192
    body = {**copy.deepcopy(scene['body']), 'request_id': 'fe_' + str(uuid4()), 'model_id': cap['id']}
    return {**scene, 'body': body, 'cap': cap}


def get(scene, identity):
    return reply(scene['client'].get(BASE + '/' + identity))


def all_rows():
    """Opaque logical snapshots of this EMPTY synthetic owned database only."""
    fence()
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        return {model.__tablename__: dict(db.execute(text('SELECT id,row_to_json(t)::text FROM "'
            + model.__tablename__ + '" t ORDER BY id')).all())
            for model in {mapper.class_ for mapper in Base.registry.mappers}}


def journal_files():
    root = pipeline._journal_root()
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob('*') if path.is_file()} if root.exists() else {}


def fees(call_ids):
    with Session(engine()) as db:
        return {identity: (call.status, call.reserved_tokens, call.charged_tokens, copy.deepcopy(call.data))
            for identity in call_ids for call in [db.get(CloudCall, identity)]}


def start(scene):
    preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    assert preview['map_preparation']['generation_contract']['schema'] == preparation.GENERATION_V3
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(scene['client'].post(BASE, json=command), 202)
    assert accepted['draft']['schema'] == 'report-evidence-reading-draft-v2'
    for name, value in [('account', reply(scene['client'].get('/api/me'))), ('preview', preview),
                        ('start-command', command), ('queued', accepted)]:
        capture(scene, 'v3-' + name, value)
    return preview, command, accepted


def candidate(payload):
    """Explicit new wire fixture; old short keys are removed before sending."""
    def spans(value):
        if type(value) is dict:
            if set(value) == {'source_ref', 'quote'}:
                return {'span_ref': value['source_ref'] + '/s1'}
            return {key: spans(item) for key, item in value.items()}
        return [spans(item) for item in value] if type(value) is list else value
    value = spans(example_candidate())
    value.update(schema=wire.CANDIDATE_WIRE, source_hash=payload['source_hash'],
        catalog_hash=payload['catalog_hash'], global_claims=copy.deepcopy(payload['input_claims']))
    for business in value['businesses']:
        steps = {item['key']: index for index, item in enumerate(business['steps'])}
        people = {item['key']: index for index, item in enumerate(business['participants'])}
        for step in business['steps']:
            step.pop('key')
            for old, new in [('next_key', 'next_index'), ('else_key', 'else_index')]:
                target = step.pop(old)
                step[new] = None if target == 'end' else steps[target]
        for person in business['participants']:
            person.pop('key')
        for collection in ('interactions', 'conditions', 'coordination'):
            for item in business[collection]:
                item.pop('key')
                item['step_indices'] = [steps[key] for key in item.pop('step_keys')]
                if collection == 'interactions':
                    item['from_participant_index'] = people[item.pop('from_participant')]
                    item['to_participant_index'] = people[item.pop('to_participant')]
        evidence = copy.deepcopy(business['participants'][0]['evidence'])
        business['interactions'] = [{'from_participant_index': 0, 'to_participant_index': 1,
            'purpose': '合成请求核验', 'condition': '', 'step_indices': [0, 1], 'evidence': evidence}]
    return {'result': value, 'reconciliation': [
        {'input_position': position, 'disposition': 'kept', 'output_kind': 'global_claims',
            'output_index': position, 'reason': '仅合成协议示例；保留全部输入及原引用。'}
        for position in range(len(payload['input_claims']))]}


class SyntheticProvider:
    """Only replace transport; real reserve, hashes, callbacks and finish run."""
    def __init__(self, scene, monkeypatch, mode='success'):
        self.scene, self.mode = scene, mode
        self.calls, self.receipts, self.values, self.payloads, self.stages = [], [], [], [], []
        monkeypatch.setattr(pipeline, 'cloud_json', self)

    def __call__(self, cap, messages, schema, principal, **callbacks):
        assert callbacks['full_report'] is True
        stage = callbacks['evidence_stage']
        payload = json.loads(messages[-1]['content'])
        reducing = payload['schema'] == 'report-evidence-reduction-payload-v2'
        assert stage == ('reduce-v2' if reducing else 'map-v3')
        profile = evidence_policy(cap.data['execution'], stage)
        policy = {'single_batch': True, **profile}
        options = evidence_options(cap.data['execution'], cloud_json_options(cap.data['execution']), stage)
        prepared, _, images = prepare_cloud_json(cap, messages, schema)
        assert images == 0
        summary = measure_input(prepared, policy, options['max_tokens'])
        call_id = cloud_budget.reserve(principal, cap, summary['token_bound'], digest(prepared))
        with Session(engine()) as db:
            call = db.get(CloudCall, call_id)
            call.data = {**call.data, 'dispatch_options_hash': digest(options),
                'input_policy_hash': digest(policy), 'synthetic_only': True}
            db.commit()
        self.calls.append(call_id); self.payloads.append(copy.deepcopy(payload)); self.stages.append(stage)
        receipt, succeeded = None, False
        try:
            callbacks['on_reserved'](call_id)
            if self.mode == 'unknown':
                raise TimeoutError('synthetic after actual reserve, before any response')
            value = candidate(payload) if reducing else {'schema': wire.MAP_WIRE, 'claims': [{
                'kind': '职责', 'statement': ' 合成协议声明＋+，含义仍待人工核对。 ',
                'span_refs': [payload['spans'][0][0]], 'status': 'explicit', 'explanation': ''}]}
            receipt = {'provider': cap.data['execution']['provider'], 'model': cap.data['execution']['model'],
                'call_id': call_id, 'request_id': 'synthetic-' + str(uuid4()),
                'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20},
                'evidence_profile': copy.deepcopy(profile),
                'response_choice_count': 2 if self.mode == 'last_map_multiple' and not reducing
                    and len(self.calls) == 2 else 1,
                'finish_reason': 'length' if self.mode == 'last_map_length' and not reducing
                    and len(self.calls) == 2 else 'stop'}
            self.values.append(copy.deepcopy(value)); self.receipts.append(copy.deepcopy(receipt))
            callbacks['on_response'](json.dumps(value, ensure_ascii=False, indent=2), copy.deepcopy(receipt))
            if self.mode == 'after_raw':
                raise LeaseLost('synthetic after original journal and DB checkpoint')
            # Match real gateway: original response callback precedes completion
            # and schema validation, even for syntactically valid truncated JSON.
            if receipt['finish_reason'] != 'stop':
                raise ModelBlocked('synthetic non-stop completion; never accepted')
            if receipt['response_choice_count'] != 1:
                raise ModelBlocked('synthetic multiple choices; first original never accepted')
            validate(schema, value); succeeded = True
            return copy.deepcopy(value), copy.deepcopy(receipt)
        finally:
            cloud_budget.finish(call_id, receipt, 'succeeded' if succeeded else 'failed')


def mapped(scene, accepted):
    assert process_once(accepted['draft']['job_id'])
    value = get(scene, accepted['draft']['id'])
    assert value['status'] == 'awaiting_merge' and value['phase'] == 'awaiting_reduce', value.get('error')
    assert all(stage['status'] == 'succeeded' and stage['raw_response_saved'] for stage in value['maps'])
    capture(scene, 'v3-mapped', value)
    return value


def authorize_reduce(scene, value):
    body = {'revision': value['revision'], 'request_id': 'fer_' + str(uuid4())}
    preview = reply(scene['client'].post(BASE + '/' + value['id'] + '/reduce-preview', json=body))
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(scene['client'].post(BASE + '/' + value['id'] + '/reduce', json=command), 202)
    receipt = accepted['receipt']; prepared = preview['reduction_preparation']
    assert receipt['schema'] == 'report-evidence-reduce-receipt-v2'
    assert receipt['map_inputs_hash'] == prepared['map_inputs_hash']
    assert receipt['map_plan_hash'] == prepared['map_plan_hash']
    assert 'map_results_hash' not in receipt
    for name, item in [('reduce-preview', preview), ('reduce-command', command), ('reduce-accepted', accepted)]:
        capture(scene, 'v3-' + name, item)
    return preview, command, accepted


def reduced(scene, value):
    preview, command, accepted = authorize_reduce(scene, value)
    assert process_once(accepted['draft']['job_id'])
    final = get(scene, value['id'])
    capture(scene, 'v3-reduced', final)
    return preview, command, accepted, final


def reclaim(scene, stopped, provider):
    """Real DB-time expiry and actual new-token claim, no sleeps or fake lease."""
    assert stopped['status'] == 'running'
    calls, original_fees, original_files = list(provider.calls), fees(provider.calls), journal_files()
    with Session(engine()) as db:
        job = db.get(Job, stopped['job_id']); clock = db.scalar(select(func.clock_timestamp()))
        token, recoveries = job.lease_token, job.data['lease_recoveries']
        assert job.status == 'running' and job.lease_until > clock
        job.lease_until = clock - timedelta(seconds=1); db.commit()
    assert process_once(stopped['job_id'])
    with Session(engine()) as db:
        job = db.get(Job, stopped['job_id'])
        assert job.lease_token != token and job.data['lease_recoveries'] == recoveries + 1
        assert job.status in {'succeeded', 'failed'} and job.lease_until is None
    assert provider.calls == calls and fees(calls) == original_fees and journal_files() == original_files
    return get(scene, stopped['id'])


def original_map_inputs(row):
    catalog = pipeline._catalog(row.data['preview']); result = []
    for stage, chunk in zip(row.data['maps'], row.data['preview']['map_preparation']['plan']['chunks']):
        descriptor = pipeline._descriptor(row, stage['chunk_id'])
        binding = pipeline.journal_binding(row, stage['chunk_id'], stage['reserved_call_id'])
        expected_dispatch = {'schema': 'report-evidence-v3-dispatch-v1',
            'model_id': row.data['preview']['body']['model_id'], 'authorized': chunk,
            'map_preparation_hash': row.data['preview']['map_preparation']['preparation_hash'],
            'map_plan_hash': row.data['preview']['map_preparation']['plan']['plan_hash']}
        assert binding['dispatch_hash'] == descriptor['binding']['dispatch_hash'] == digest(expected_dispatch)
        response = pipeline.journal.parse_response(pipeline._journal_root(), binding, descriptor)
        item = {'wire': response['value'], 'binding': descriptor['binding'], 'expected_binding': binding,
            'wire_sha256': descriptor['wire_sha256'], 'allowed_span_refs': chunk['span_refs']}
        item['projection'] = wire.build_map_projection(item['wire'], catalog=catalog,
            **{key: item[key] for key in ('binding', 'expected_binding', 'wire_sha256', 'allowed_span_refs')})
        result.append(item)
    return catalog, result


def test_v3_actual_map_reduce_manual_nonce_cas_and_independent_session_readback(v3_scene, monkeypatch):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch)
    preview, start_body, accepted = start(scene); middle = mapped(scene, accepted)
    assert preview['input_summary']['map_call_count'] == 2 and len(provider.calls) == 2
    assert preview['map_preparation']['generation_contract']['dispatch_options']['max_tokens'] == 8192
    assert all(stage['result']['schema'] == wire.MAP_PROJECTION for stage in middle['maps'])
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], middle['id']); catalog, inputs = original_map_inputs(row)
        assert [item['projection'] for item in inputs] == [stage['result'] for stage in middle['maps']]
        assert all('result' not in stage and 'wire' not in stage for stage in row.data['maps'])
    reduce_preview, reduce_body, reduce_start, final = reduced(scene, middle)
    assert reduce_preview['reduction_preparation']['generation_contract']['dispatch_options']['max_tokens'] == 131072
    assert reduce_preview['reduction_preparation']['output_capacity']['max_output_tokens'] == 131072
    assert final['status'] == 'review' and final['schema'] == 'report-evidence-reading-draft-v2'
    assert final['can_apply'] is final['can_publish'] is False
    assert final['model_result']['schema'] == wire.CANDIDATE_PROJECTION
    assert provider.stages == ['map-v3', 'map-v3', 'reduce-v2'] and len(provider.calls) == 3
    metadata = final['reduction']['projection_metadata']
    assert metadata['schema'] == wire.REDUCE_PROJECTION and 'result' not in metadata and 'reconciliation' not in metadata
    full_projection = {**copy.deepcopy(metadata), 'result': copy.deepcopy(final['model_result']),
        'reconciliation': copy.deepcopy(final['reduction']['reconciliation'])}
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], final['id']); catalog, inputs = original_map_inputs(row)
        descriptor = pipeline._descriptor(row, 'reduce')
        binding = pipeline.journal_binding(row, 'reduce', row.data['reduction']['reserved_call_id'])
        response = pipeline.journal.parse_response(pipeline._journal_root(), binding, descriptor)
        assert response['value'] == provider.values[-1]
        assert wire.check_reduce_projection(full_projection, response['value'], binding=descriptor['binding'],
            expected_binding=binding, wire_sha256=descriptor['wire_sha256'], catalog=catalog,
            allowed_span_refs=[item[0] for item in reduce_preview['reduction_preparation']['payload']['spans']],
            context=preview['map_preparation']['context'], map_inputs=inputs) == full_projection
        assert all(key not in row.data for key in ('model_result', 'raw_response'))
    original = copy.deepcopy(final['model_result']); original_fees, original_files = fees(provider.calls), journal_files()
    value = manual_from_projection(original, '人工私人核对🙂')
    value['result']['global_claims'][0]['statement'] += ' 人工补充＋+。'
    review = {'revision': final['revision'], 'request_id': str(uuid4()), 'value': value}
    saved = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=review))
    assert saved['current_matches'] and saved['draft']['review_value'] == value
    assert saved['draft']['can_apply'] is saved['draft']['can_publish'] is False
    assert saved['receipt']['schema'] == 'report-evidence-review-receipt-v1'
    assert saved['receipt']['value_hash'] == digest(value) and saved['draft']['model_result'] == original
    assert set(value) == {'schema', 'result', 'note'} and value['schema'] == 'report-evidence-manual-review-v5'
    assert 'key' not in value['result']['businesses'][0]['steps'][0]
    assert value['result']['businesses'][0]['steps'][0]['next_index'] is None
    snapshot = all_rows()
    assert reply(scene['client'].post(BASE, json=start_body), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + final['id'] + '/reduce', json=reduce_body), 202)['receipt'] == reduce_start['receipt']
    assert reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=review)) == saved
    assert reply(scene['client'].get(BASE + '/' + final['id'] + '/review-receipts/' + review['request_id'])) == saved
    assert all_rows() == snapshot
    stale = {**review, 'request_id': str(uuid4())}
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json=stale).status_code == 409
    changed = copy.deepcopy(review); changed['value']['note'] += '改体'
    assert scene['client'].put(BASE + '/' + final['id'] + '/review', json=changed).status_code == 409
    with Session(engine()) as db:
        # A fresh real Session rebuilds from persisted state and original files,
        # not a cached API serializer. This is not a native process restart.
        row = pipeline.owned(db, scene['p'], final['id'])
        assert pipeline.draft_view(row, scene['p']) == saved['draft']
        history = row.data['review_history'][-1]
        body = pipeline.ReviewCommand.model_validate(review)
        human = pipeline.v3.manual_projection(pipeline, row, scene['p'], body, history['value'], pipeline._catalog(preview))
        assert human['review_context']['request_id'] == review['request_id']
        assert human['result']['global_claims'][0]['key'] != original['global_claims'][0]['key']
    assert fees(provider.calls) == original_fees and journal_files() == original_files
    actual_cap = next(item for item in reply(scene['client'].get('/api/capabilities')) if item['id'] == scene['cap']['id'])
    assert actual_cap['data']['execution']['max_tokens'] == cloud_json_options(actual_cap['data']['execution'])['max_tokens'] == 8192
    for name, item in [('model-result', original), ('review-command', review), ('review-reply', saved),
        ('status', reply(scene['client'].get(BASE + '/' + final['id'] + '/status'))),
        ('by-request', reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id'])))]:
        capture(scene, 'v3-' + name, item)


@pytest.mark.parametrize('mode', ['last_map_length', 'last_map_multiple'])
def test_incomplete_or_multiple_valid_json_is_failed_readonly_facts_and_never_free_adopted(v3_scene, monkeypatch, mode):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch, mode)
    _, command, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and len(provider.calls) == 2
    assert failed['maps'][0]['status'] == 'succeeded'
    assert failed['maps'][1]['raw_response_saved'] and failed['maps'][1]['result'] is None
    assert provider.receipts[1]['finish_reason'] == ('length' if mode == 'last_map_length' else 'stop')
    assert provider.receipts[1]['response_choice_count'] == (1 if mode == 'last_map_length' else 2)
    snapshot, files, original_fees = all_rows(), journal_files(), fees(provider.calls)
    facts = reply(scene['client'].get(BASE + '/' + failed['id'] + '/saved-facts?stage=map-2'))
    assert facts['original_protocol_valid'] is False and facts['facts'][0]['field_constraints_valid'] is True
    assert any(issue['code'] == 'response_incomplete' for issue in facts['protocol_issues'])
    assert facts['facts'][0]['fields'] == provider.values[1]['claims'][0]
    assert facts['facts'][0]['protocol_key'] is None and facts['meaning_review_status'] == 'unreviewed'
    assert facts['eligible_for_reduce'] is facts['can_apply'] is facts['can_publish'] is False
    assert get(scene, failed['id']) == failed and all_rows() == snapshot and journal_files() == files
    recovery = {'revision': failed['revision'], 'request_id': str(uuid4())}
    # Every Map has a saved wire. This must fail on completion, not merely on
    # the absence of an unstarted future batch.
    response = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=recovery), 422)
    assert response['detail']['code'] == 'evidence_saved_response_invalid'
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert get(scene, failed['id']) == failed and all_rows() == snapshot
    assert fees(provider.calls) == original_fees and journal_files() == files and len(provider.calls) == 2
    capture(scene, 'v3-' + mode.replace('_', '-') + '-failed', failed)
    capture(scene, 'v3-' + mode.replace('_', '-') + '-saved-facts', facts)


def test_actual_reserved_unknown_never_resumes_or_recovers_as_an_unstarted_batch(v3_scene, monkeypatch):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch, 'unknown')
    _, command, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    unknown = get(scene, accepted['draft']['id'])
    assert unknown['status'] == 'response_unknown' and len(provider.calls) == 1
    assert unknown['maps'][0]['reserved_call_id'] == provider.calls[0]
    assert not any(stage['raw_response_saved'] for stage in unknown['maps'])
    snapshot, files, before_fees = all_rows(), journal_files(), fees(provider.calls)
    body = {'revision': unknown['revision'], 'request_id': 'fes_' + str(uuid4())}
    assert scene['client'].post(BASE + '/' + unknown['id'] + '/resume-preview', json=body).status_code == 409
    recovery = {'revision': unknown['revision'], 'request_id': str(uuid4())}
    response = reply(scene['client'].post(BASE + '/' + unknown['id'] + '/recover-saved', json=recovery), 409)
    assert response['detail']['code'] == 'evidence_saved_response_incomplete'
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert get(scene, unknown['id']) == unknown and all_rows() == snapshot
    assert fees(provider.calls) == before_fees and journal_files() == files and len(provider.calls) == 1
    capture(scene, 'v3-unknown', unknown)


def test_saved_map_raw_reclaimed_lease_then_explicit_resume_only_pays_remaining(v3_scene, monkeypatch):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch, 'after_raw')
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    stopped = get(scene, accepted['draft']['id'])
    assert stopped['status'] == 'running' and len(provider.calls) == 1
    failed = reclaim(scene, stopped, provider)
    assert failed['status'] == 'failed' and failed['maps'][0]['result'] is None
    original = copy.deepcopy(failed['maps'][0]['journal']); call_ids = list(provider.calls)
    original_fees = fees(call_ids)
    request = {'revision': failed['revision'], 'request_id': 'fes_' + str(uuid4())}
    preview = reply(scene['client'].post(BASE + '/' + failed['id'] + '/resume-preview', json=request))
    assert preview['saved_chunks'] == ['map-1'] and preview['completed_chunks'] == []
    assert [chunk['chunk_id'] for chunk in preview['remaining_chunks']] == ['map-2']
    assert preview['input_summary']['call_count'] == 1
    command = {**request, 'preview_hash': preview['preview_hash'], 'consent': True}
    resumed = reply(scene['client'].post(BASE + '/' + failed['id'] + '/resume', json=command), 202)
    assert resumed['draft']['maps'][0]['result']['schema'] == wire.MAP_PROJECTION
    assert resumed['draft']['maps'][0]['journal'] == original and provider.calls == call_ids
    provider.mode = 'success'; assert process_once(resumed['draft']['job_id'])
    final = get(scene, failed['id'])
    assert final['status'] == 'awaiting_merge' and len(provider.calls) == 2
    assert final['maps'][0]['journal'] == original and fees(call_ids) == original_fees
    snapshot = all_rows()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/resume', json=command), 202)['receipt'] == resumed['receipt']
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/resume-receipts/' + request['request_id']))['receipt'] == resumed['receipt']
    assert all_rows() == snapshot
    for name, item in [('resume-preview', preview), ('resume-command', command), ('resume-accepted', resumed), ('resumed', final)]:
        capture(scene, 'v3-' + name, item)


def test_last_map_and_reduce_saved_raw_recover_free_without_new_call_or_journal(v3_scene, monkeypatch):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch)
    real_consume = pipeline._consume; fired = False
    def before_last(db, p, row, key):
        nonlocal fired
        if key == 'map-2' and not fired:
            fired = True; raise LeaseLost('synthetic last Map original saved, before consume')
        return real_consume(db, p, row, key)
    monkeypatch.setattr(pipeline, '_consume', before_last)
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    failed = reclaim(scene, get(scene, accepted['draft']['id']), provider)
    assert failed['status'] == 'failed' and len(provider.calls) == 2
    before_fees, before_files = fees(provider.calls), journal_files()
    body = {'revision': failed['revision'], 'request_id': str(uuid4())}
    recovered = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body))
    assert recovered['draft']['status'] == 'awaiting_merge' and len(provider.calls) == 2
    assert fees(provider.calls) == before_fees and journal_files() == before_files
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/recovery-receipts/' + body['request_id'])) == recovered
    provider.mode = 'after_raw'
    _, _, _, stopped = reduced(scene, recovered['draft'])
    failed_reduce = reclaim(scene, stopped, provider)
    assert failed_reduce['status'] == 'failed' and failed_reduce['reduction']['raw_response_saved'] and len(provider.calls) == 3
    before_fees, before_files = fees(provider.calls), journal_files()
    body2 = {'revision': failed_reduce['revision'], 'request_id': str(uuid4())}
    final = reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body2))
    assert final['draft']['status'] == 'review' and final['draft']['model_result']['schema'] == wire.CANDIDATE_PROJECTION
    assert fees(provider.calls) == before_fees and journal_files() == before_files and len(provider.calls) == 3
    snapshot = all_rows()
    assert reply(scene['client'].post(BASE + '/' + failed['id'] + '/recover-saved', json=body2)) == final
    assert reply(scene['client'].get(BASE + '/' + failed['id'] + '/recovery-receipts/' + body2['request_id'])) == final
    assert all_rows() == snapshot
    for name, item in [('map-recovery-command', body), ('map-recovery-reply', recovered),
                       ('reduce-recovery-command', body2), ('reduce-recovery-reply', final)]:
        capture(scene, 'v3-' + name, item)


@pytest.mark.parametrize('change', ['missing_map', 'map_projection_hash', 'reduce_map_proof'])
def test_changed_complete_plan_or_original_projection_cannot_be_publicly_rehashed(v3_scene, monkeypatch, change):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch)
    _, _, accepted = start(scene); value = mapped(scene, accepted)
    if change == 'reduce_map_proof':
        _, _, authorized = authorize_reduce(scene, value)
        value = authorized['draft']
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, value['id']); data = copy.deepcopy(row.data)
        if change == 'missing_map': data['maps'].pop()
        elif change == 'map_projection_hash':
            forged = copy.deepcopy(value['maps'][0]['result'])
            forged['claims'][0]['key'] = 'mc_' + 'f' * 32
            forged['projection_hash'] = digest({key: item for key, item in forged.items() if key != 'projection_hash'})
            data['maps'][0]['result_hash'] = digest(forged)
        else:
            preview = data['reduction']['preview']; prepared = preview['reduction_preparation']
            prepared['map_proofs'][0]['projection_hash'] = 'f' * 64
            prepared['map_inputs_hash'] = digest(prepared['map_proofs'])
            prepared['generation_contract']['map_inputs_hash'] = prepared['map_inputs_hash']
            prepared['generation_contract_hash'] = digest(prepared['generation_contract'])
            prepared['payload']['map_inputs_hash'] = prepared['map_inputs_hash']
            prepared['payload_hash'] = digest(prepared['payload'])
            prepared['preparation_hash'] = digest({key: item for key, item in prepared.items() if key != 'preparation_hash'})
            preview['preview_hash'] = digest({key: item for key, item in preview.items() if key != 'preview_hash'})
        row.data = data; db.commit()
    snapshot, files, before_fees = all_rows(), journal_files(), fees(provider.calls)
    response = reply(scene['client'].get(BASE + '/' + value['id']), 409)
    assert response['detail']['code'] == 'evidence_state_integrity'
    assert all_rows() == snapshot and journal_files() == files and fees(provider.calls) == before_fees


@pytest.mark.parametrize('role', ['operator', 'admin'])
def test_new_projection_and_manual_readbacks_remain_private_to_original_actor(v3_scene, monkeypatch, role):
    scene = v3_scene; provider = SyntheticProvider(scene, monkeypatch)
    _, _, accepted = start(scene); middle = mapped(scene, accepted)
    _, _, reduction, final = reduced(scene, middle)
    review = {'revision': final['revision'], 'request_id': str(uuid4()),
        'value': manual_from_projection(final['model_result'], '本人私人核对稿')}
    saved = reply(scene['client'].put(BASE + '/' + final['id'] + '/review', json=review))
    username, password = 'other_wire_' + uuid4().hex[:10], 'Synthetic-wire-' + uuid4().hex
    reply(scene['client'].post('/api/users', json={'username': username, 'password': password, 'role': role}), 201)
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        before = all_rows()
        for suffix in ['', '/status', '/saved-facts?stage=map-1',
            '/reduce-receipts/' + reduction['receipt']['request_id'], '/review-receipts/' + review['request_id']]:
            assert other.get(BASE + '/' + final['id'] + suffix).status_code == 404
        assert other.get(BASE + '/by-request/' + scene['body']['request_id']).status_code == 404
        assert other.put(BASE + '/' + final['id'] + '/review', json=review).status_code == 404
        assert reply(other.get(BASE))['items'] == []
        assert all_rows() == before
    assert get(scene, final['id']) == saved['draft'] and len(provider.calls) == 3

"""Real received partial media and formal document sources; Root executes.

The inherited media/model IO and legacy graph eligibility are explicit public
fixture seams. Capture/processing Jobs, actual Frames, Daily ACK, copied Task,
saved QA, document parsing/search and their independent proofs are not faked.
These controls do not establish actual field clocks, camera or model accuracy.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, instance_material_sources as sources
from generic_agents import media_daily_delivery as delivery, video_daily
from generic_agents import frame_supplement_v236 as plans
from generic_agents.contracts import digest
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_frame_supplement_source_v236 import (
    frame_supplement_case, daily_frame_source, daily_scene, daily_camera_source,
    media_runtime, mocked_runtime, explicit_no_alert_fixture, propose, advance,
)
from tests.test_media_daily_task_pg_v226 import reconfirm_camera
from tests.test_runtime_documents_pg_v205 import uploaded, run as run_document, search as create_search


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf8')


def counts(db, p):
    return {cls.__tablename__: db.scalar(select(func.count()).select_from(cls).where(
        cls.workspace_id == p.workspace_id)) for cls in
        (m.Task, m.Job, m.Event, m.Memory, m.Evidence, m.Audit, m.CloudCall, m.SupplementPlan)}


def scope_for(db, case, kinds=('supplement_captures',)):
    task = db.get(m.Task, case.task_id)
    from generic_agents.instance_observation_day_v216 import TZ
    day = str(db.get(m.StreamSession, case.session_id).created_at.astimezone(TZ).date())
    return {'instance_id': task.data['instance_id'], 'release_id': task.data['instance_release_id'],
        'business_ids': [task.data['business_id']], 'entity_ids': list(task.data['entity_ids']),
        'batch_day': day, 'source_kinds': list(kinds), 'document_search_ids': []}


@pytest.fixture
def partial_material(frame_supplement_case, monkeypatch):
    """Requested8/actual2.4 from the normal capture handler, not row edits."""
    from tests import test_media as media_tests
    case = frame_supplement_case
    proposal = propose(case)
    body = {'schema': 'frame-supplement-approve-v236', 'request_id': str(uuid4()),
        'version': proposal.plan['version'], 'expected_decision_hash': proposal.plan['decision_hash'],
        'seconds': 8, 'confirm_one_time_read': True}
    with Session(case.bound) as db:
        approved = plans.approve(db, case.p, case.task_id, proposal.plan_id, raw(body))
        db.commit()
    session_id = approved['plan']['session']['id']
    transport = media_tests.media.child

    def partial_transport(script, request, directory, guard, receive):
        def received(value, temp):
            if value.get('kind') == 'capture.complete':
                assert value['requested_seconds'] == 8
                value = {**value, 'partial': True, 'duration_seconds': 2.4,
                    'received_start_unix': value['received_end_unix']-2.4,
                    'error': 'explicit mocked upstream ended before requested8'}
            return receive(value, temp)
        return transport(script, request, directory, guard, received)

    monkeypatch.setattr(media_tests.media, 'child', partial_transport)
    assert process_once(approved['plan']['session']['job_id'], deliver=False)
    with Session(case.bound) as db:
        session = db.get(m.StreamSession, session_id)
        assert session.status == 'partial' and session.data['receipt']['duration_seconds'] == 2.4
        assert db.get(m.Job, session.data['job_id']).status == 'succeeded'
        run = db.get(m.VideoRun, session.video_run_id)
        run_id, job_id = run.id, run.data['job_id']
    original = media_tests.local_video.execute_video

    def partial_video(content, backend, configuration, checkpoints, guard, receive, lock_fd=None):
        emitted = 0
        def received(value, temp):
            nonlocal emitted
            if value.get('kind') == 'frame':
                if value['seconds'] > 2.4:
                    return
                emitted += 1
            if value.get('kind') == 'complete':
                value = {**value, 'duration_seconds': 2.4, 'sample_count': emitted}
            return receive(value, temp)
        return original(content, backend, configuration, checkpoints, guard, received, lock_fd=lock_fd)

    monkeypatch.setattr(media_tests.local_video, 'execute_video', partial_video)
    assert process_once(job_id, deliver=False)
    blocked = advance(case, proposal.plan_id)
    assert blocked['status'] == 'blocked' and blocked['followup'] is None
    with Session(case.bound) as db:
        run = db.get(m.VideoRun, run_id)
        assert run.status == 'succeeded' and db.get(m.Job, job_id).status == 'succeeded'
        assert db.scalar(select(func.count()).select_from(m.VideoFrame).where(m.VideoFrame.run_id == run_id)) > 0
        video_daily.reconcile(db, case.p.workspace_id)
        db.commit()
    receipt = delivery.consume_once(case.p.workspace_id)
    assert receipt['received'] >= 1 and receipt['blocked'] == 0
    item = NS(**{**vars(case), 'session_id': session_id, 'run_id': run_id,
                  'plan_id': proposal.plan_id, 'processing_job_id': job_id})
    with Session(case.bound) as db:
        scope = scope_for(db, item)
        before = counts(db, case.p)
        baseline = sources.collect(db, case.p, scope)
        assert len(baseline['records']) == 1
        assert baseline['records'][0]['capture']['actual_seconds'] == 2.4
        assert baseline['records'][0]['delivery']['status'] == 'received'
        assert sources.recheck(db, case.p, baseline['snapshot']) == baseline
        assert counts(db, case.p) == before
    return NS(**vars(item), scope=scope, baseline=baseline)


@pytest.mark.parametrize('change', [
    {'instance_id': 'not-a-uuid'}, {'batch_day': '2026-2-01'}, {'batch_day': None},
    {'source_kinds': ['module:arbitrary']}, {'source_kinds': ['observation_day', 'observation_day']},
    {'entity_ids': []}, {'business_ids': []}, {'source_scope': {}},
])
def test_closed_scope_rejects_before_any_database_read(change):
    scope = {'instance_id': str(uuid4()), 'release_id': str(uuid4()), 'business_ids': ['b'],
        'entity_ids': [str(uuid4())], 'batch_day': '2026-10-27',
        'source_kinds': ['supplement_captures'], 'document_search_ids': []}
    with pytest.raises(HTTPException) as error:
        sources.collect(object(), None, {**scope, **change})
    assert error.value.status_code == 422


@pytest.mark.parametrize('value', [None, True, False, -1, float('nan'), float('inf'), '0'])
def test_unknown_or_invalid_duration_never_becomes_zero(value):
    assert sources._number(value) is None


def test_registry_is_closed_code_not_a_module_loader():
    assert set(sources.REGISTRY) == set(sources.KINDS)
    for identity, provider in sources.REGISTRY.items():
        assert provider.id == identity and provider.version == '1'
        assert all(callable(x) for x in (provider.collector, provider.recheck, provider.citation))
    assert sources._number(0) == 0 and sources._number(2.4) == 2.4


def test_date_filter_does_not_move_late_cohort_or_promote_a_declared_scene_clock():
    # Projection-only examples, not synthetic successful source/ACK proofs.
    late = sources._day_basis('video', {'received_day': '2026-10-27'}, '2026-10-26', '2026-10-27')
    assert late['collection_day'] == '2026-10-26' and late['matched_axes'] == ['received_day']
    assert late['scene_time_trust'] == 'unknown'
    declared = sources._day_basis('imagery', {'date_basis': 'original_upload_request',
        'sources': [{'observed_at': '2026-10-27', 'time_basis': 'user_declared_unverified'}]},
        '2026-10-27', '2026-10-27')
    assert declared['reader_definition'] == 'original_upload_request'
    assert declared['scene_time_trust'] == 'unverified'


def test_real_partial_record_has_separate_capture_processing_and_independent_receipt(partial_material):
    case = partial_material
    record = case.baseline['records'][0]
    assert set(record) == {'key', 'kind', 'label', 'business_ids', 'entity_ids', 'batch_day',
        'received_at', 'scene_observed_at', 'scene_time_trust', 'capture', 'processing',
        'delivery', 'references', 'text', 'limitations'}
    assert record['capture']['status'] == 'partial'
    assert record['capture']['requested_seconds'] == 8
    assert record['capture']['actual_seconds'] == 2.4
    assert record['processing']['status'] == 'succeeded' and record['processing']['frames_saved'] > 0
    assert record['delivery']['status'] == 'received' and record['received_at']
    assert record['scene_observed_at'] is None and record['scene_time_trust'] == 'unknown'
    assert record['entity_ids'] == case.scope['entity_ids']
    assert case.baseline['coverage']['expected_sources_verified'] is False
    assert all(r['physical_recheck'] == 'checked' for r in record['references'])
    assert not any(word in json.dumps(record, ensure_ascii=False) for word in
                   ('storage_key', 'secret_ref', 'rtsp_origin', 'gateway_origin'))
    with Session(case.bound) as db:
        assert db.get(m.VideoRun, case.run_id).data['source_context'].get('domain_binding') is None
        assert plans.checked_plan(db, case.p, case.task_id, case.plan_id).status == 'blocked'


def test_normal_capture_failure_without_a_video_has_unknown_duration_not_zero(frame_supplement_case, monkeypatch):
    from tests import test_media as media_tests
    from tests.test_frame_supplement_source_v236 import approve
    case = frame_supplement_case
    proposal = propose(case)
    approved = approve(case, proposal)
    session_id = approved.plan['session']['id']
    def failed(script, request, directory, guard, receive):
        guard()
        return receive({'kind': 'capture.failed', 'reason': '未读到可保存的视频',
                        'error_code': 'explicit_mocked_timeout'}, directory)
    monkeypatch.setattr(media_tests.media, 'child', failed)
    assert process_once(approved.plan['session']['job_id'], deliver=False)
    with Session(case.bound) as db:
        session = db.get(m.StreamSession, session_id)
        assert session.status == 'failed' and session.evidence_id is None and session.video_run_id is None
        assert db.get(m.Job, session.data['job_id']).status.startswith('failed')
        video_daily.reconcile(db, case.p.workspace_id)
        db.commit()
    delivery.consume_once(case.p.workspace_id)
    scoped_case = NS(**vars(case), session_id=session_id)
    with Session(case.bound) as db:
        before = counts(db, case.p)
        result = sources.collect(db, case.p, scope_for(db, scoped_case))
        assert len(result['records']) == 1
        record = result['records'][0]
        assert record['capture']['status'] == 'failed' and record['capture']['actual_seconds'] is None
        assert record['processing'] == {'status': 'not_started', 'frames_saved': None}
        assert record['references'][0]['source_path'] is None
        assert sources.recheck(db, case.p, result['snapshot']) == result
        assert counts(db, case.p) == before


@pytest.mark.parametrize('fault', ['deleted_event', 'deleted_memory', 'changed_run_context',
                                 'deleted_plan_state', 'deleted_capture_audit'])
def test_real_baseline_then_broken_independent_source_is_not_empty_or_received(partial_material, fault):
    case = partial_material
    with Session(case.bound) as db:
        original = sources.recheck(db, case.p, case.baseline['snapshot'])
        assert original == case.baseline
        entry = original['snapshot']['entries'][0]
        event = db.get(m.Event, entry['source_ref']['receipt']['event_id'])
        proof = delivery.checked(db, case.p, event)
        if fault == 'deleted_event':
            db.delete(event)
        elif fault == 'deleted_memory':
            db.delete(db.get(m.Memory, proof['state']['memory_id']))
        elif fault == 'changed_run_context':
            run = db.get(m.VideoRun, case.run_id)
            run.data = {**run.data, 'source_context': {**run.data['source_context'], 'session_id': str(uuid4())}}
        elif fault == 'deleted_plan_state':
            audit = db.scalar(select(m.Audit).where(m.Audit.object_id == case.plan_id,
                m.Audit.action == plans.STATE).order_by(m.Audit.created_at).limit(1))
            assert audit is not None
            db.delete(audit)
        else:
            audit = db.scalar(select(m.Audit).where(m.Audit.object_id == case.session_id,
                m.Audit.action == 'media.capture.saved'))
            assert audit is not None
            db.delete(audit)
        db.commit()
    with Session(case.bound) as db:
        before = counts(db, case.p)
        for read in (lambda: sources.collect(db, case.p, case.scope),
                     lambda: sources.recheck(db, case.p, case.baseline['snapshot'])):
            with pytest.raises(HTTPException) as error:
                read()
            assert error.value.status_code in {404, 409}
        assert counts(db, case.p) == before


def test_frame_storage_symlink_never_counts_as_a_successful_physical_recheck(partial_material, tmp_path):
    from generic_agents.config import settings
    case = partial_material
    with Session(case.bound) as db:
        assert sources.recheck(db, case.p, case.baseline['snapshot']) == case.baseline
        frame = db.scalar(select(m.VideoFrame).where(m.VideoFrame.run_id == case.run_id)
                          .order_by(m.VideoFrame.sample_index).limit(1))
        evidence = db.get(m.Evidence, frame.evidence_id)
        path = settings().storage_dir/evidence.storage_key
        saved = path.read_bytes()
    # Only a disposable test file; no real/private path is read or targeted.
    external = tmp_path/'unowned-frame.jpg'
    external.write_bytes(saved)
    path.unlink()
    path.symlink_to(external)
    try:
        with Session(case.bound) as db:
            for read in (lambda: sources.collect(db, case.p, case.scope),
                         lambda: sources.recheck(db, case.p, case.baseline['snapshot'])):
                with pytest.raises(HTTPException) as error:
                    read()
                assert error.value.status_code in {404, 409}
    finally:
        path.unlink()
        path.write_bytes(saved)


def test_normal_camera_reconfiguration_and_current_entity_edit_keep_frozen_recheck(partial_material):
    from generic_agents.services import update_entity
    case = partial_material
    reconfirm_camera(case.case.scene)
    with Session(case.bound) as db:
        task = db.get(m.Task, case.task_id)
        entity = db.get(m.Entity, task.data['entity_ids'][0])
        fields = task.data['object_snapshots'][0]['fields']
        field = next(f['key'] for f in fields if f['kind'] == '文本' and isinstance(entity.data.get(f['key']), str))
        changed = {**entity.data, field: entity.data[field]+'（后续正常修订）'}
        update_entity(db, case.p, entity.id, changed, entity.version, '不改原任务冻结输入')
        db.commit()
    with Session(case.bound) as db:
        assert sources.recheck(db, case.p, copy.deepcopy(case.baseline['snapshot'])) == case.baseline


def test_actual_membership_and_instance_scope_are_required_even_for_saved_snapshot(partial_material):
    case = partial_material
    with Session(case.bound) as db:
        assert sources.recheck(db, case.p, case.baseline['snapshot']) == case.baseline
        wrong = Principal(case.p.user_id, str(uuid4()), case.p.role, case.p.session_id)
        with pytest.raises(HTTPException) as error:
            sources.recheck(db, wrong, case.baseline['snapshot'])
        assert error.value.status_code in {403, 404}
        invalid = copy.deepcopy(case.scope); invalid['entity_ids'] = [str(uuid4())]
        with pytest.raises(HTTPException) as error:
            sources.collect(db, case.p, invalid)
        assert error.value.status_code in {403, 404, 409}


def test_catalog_is_metadata_only_and_document_uncovered_is_explicit(partial_material, monkeypatch):
    from generic_agents import daily_frame_assessment_source_v233 as old_source
    case = partial_material
    def forbidden(*args, **kwargs):
        raise AssertionError('Catalog cannot read the original media or execute collection')
    monkeypatch.setattr(old_source, 'read_source', forbidden)
    with Session(case.bound) as db:
        before = counts(db, case.p)
        catalog = sources.catalog(db, case.p, case.scope['instance_id'], case.scope['batch_day'])
        assert catalog['catalog_hash'] == digest({k: v for k, v in catalog.items() if k != 'catalog_hash'})
        assert case.scope['entity_ids'][0] in {x['id'] for x in catalog['entities']}
        assert catalog['document_searches'] == []
        assert counts(db, case.p) == before
        document_scope = {**case.scope, 'source_kinds': ['document_searches'], 'entity_ids': []}
        uncovered = sources.collect(db, case.p, document_scope)
        assert uncovered['records'] == [] and uncovered['coverage']['scan_complete'] is False
        assert any('SetupMaterial' in text for text in uncovered['limitations'])
        assert sources.recheck(db, case.p, uncovered['snapshot']) == uncovered


@pytest.fixture
def formal_document(partial_material):
    from generic_agents import runtime_document_receipts as receipts
    case = partial_material
    with Session(case.bound) as db:
        release = db.get(m.InstanceRelease, case.scope['release_id'])
        release_view = {'id': release.id, 'instance_id': release.instance_id}
    client_scene = NS(client=case.client, workspace=case.p.workspace_id)
    document_response, _ = uploaded(client_scene, release_view,
        '正式运行资料：每日应核对原始资料及其独立接收回执。\n不表示现场时间已核验。'.encode())
    assert document_response.status_code == 201, document_response.text[:300]
    document = document_response.json()
    assert run_document(client_scene, document['latest_version_id']) == 'succeeded'
    receipts.consume_once(case.p.workspace_id)
    search = create_search(client_scene, release_view, '每日')
    assert run_document(client_scene, search['id'], 'search_runtime_documents') == 'succeeded'
    from generic_agents.runtime_document_models import RuntimeDocumentVersion as Version
    from generic_agents.instance_observation_day_v216 import TZ
    with Session(case.bound) as db:
        registration_day = str(db.get(Version, document['latest_version_id']).created_at.astimezone(TZ).date())
    scope = {**case.scope, 'source_kinds': ['document_searches'], 'entity_ids': [],
             'document_search_ids': [search['id']], 'batch_day': registration_day}
    with Session(case.bound) as db:
        baseline = sources.collect(db, case.p, scope)
        assert len(baseline['records']) == 1
        assert sources.recheck(db, case.p, baseline['snapshot']) == baseline
    return NS(**{**vars(case), 'document': document, 'search_id': search['id'],
                  'document_scope': scope, 'document_baseline': baseline})


def test_formal_document_uses_fixed_original_excerpt_not_setup_report_or_latest(formal_document):
    case = formal_document
    record = case.document_baseline['records'][0]
    assert record['entity_ids'] == [] and record['kind'] == 'document_excerpt'
    assert '每日应核对' in record['text']
    ref = record['references'][0]
    assert ref['kind'] == 'document_excerpt' and ref['excerpt'] == record['text']
    assert ref['physical_recheck'] == 'checked'
    assert ref['source_path'].startswith('/api/runtime-documents/versions/')
    assert 'attempt_id=' in ref['source_path'] and 'expected_parse_hash=' in ref['source_path']
    assert case.document_baseline['snapshot']['document_controls'][0]['keyword'] == '每日'
    assert case.document_baseline['snapshot']['entries'][0]['source_ref']['keyword'] == '每日'
    assert case.document_baseline['snapshot']['date_basis']['document_searches'] == sources.DATE_BASIS['document_searches']
    response = case.client.get(ref['source_path'])
    assert response.status_code == 200 and record['text'] in response.json()['text']
    with Session(case.bound) as db:
        before = counts(db, case.p)
        assert sources.recheck(db, case.p, case.document_baseline['snapshot']) == case.document_baseline
        assert counts(db, case.p) == before


@pytest.mark.parametrize('kind,status', [('search', 'running'), ('search', 'failed'),
                                         ('parse', 'running'), ('parse', 'blocked_integrity')])
def test_completed_rows_with_unsettled_or_failed_actual_document_jobs_do_not_supply_answers(formal_document, kind, status):
    from generic_agents.runtime_document_models import RuntimeDocumentSearch as Search, RuntimeDocumentVersion as Version
    case = formal_document
    with Session(case.bound) as db:
        assert sources.recheck(db, case.p, case.document_baseline['snapshot']) == case.document_baseline
        search = db.get(Search, case.search_id)
        if kind == 'search':
            identity = search.data['job_id']
        else:
            version = db.get(Version, case.document['latest_version_id'])
            identity = version.data['origin']['job_id']
        # Explicit post-success ledger fault injection, not a claimed natural
        # crash or a successful source/Job fabricated for the fixture.
        db.get(m.Job, identity).status = status
        db.commit()
    with Session(case.bound) as db:
        fresh = sources.collect(db, case.p, case.document_scope)
        assert fresh['records'] == [] and fresh['coverage']['scan_complete'] is False
        assert any('结算' in message for message in fresh['limitations'])
        with pytest.raises(HTTPException) as error:
            sources.recheck(db, case.p, case.document_baseline['snapshot'])
        assert error.value.status_code == 409


@pytest.fixture
def formal_many_hits(partial_material):
    """One real search with28 distinct, normally parsed and received blocks."""
    from generic_agents import runtime_document_receipts as receipts
    from generic_agents.runtime_document_models import RuntimeDocumentVersion as Version
    from generic_agents.instance_observation_day_v216 import TZ
    case = partial_material
    release = {'id': case.scope['release_id'], 'instance_id': case.scope['instance_id']}
    scene = NS(client=case.client, workspace=case.p.workspace_id)
    content = '\n'.join('每日核对第'+str(index)+'项原始资料和独立接收回执。' for index in range(28)).encode('utf8')
    response, _ = uploaded(scene, release, content, 'many-blocks.txt')
    assert response.status_code == 201, response.text[:300]
    document = response.json()
    assert run_document(scene, document['latest_version_id']) == 'succeeded'
    receipts.consume_once(case.p.workspace_id)
    search = create_search(scene, release, '每日')
    assert run_document(scene, search['id'], 'search_runtime_documents') == 'succeeded'
    with Session(case.bound) as db:
        version = db.get(Version, document['latest_version_id'])
        evidence_id = version.evidence_id
        scope = {**case.scope, 'batch_day': str(version.created_at.astimezone(TZ).date()),
            'source_kinds': ['document_searches'], 'entity_ids': [], 'document_search_ids': [search['id']]}
        baseline = sources.collect(db, case.p, scope)
        assert len(baseline['records']) == len(baseline['snapshot']['entries']) == 28
        assert sources.recheck(db, case.p, baseline['snapshot']) == baseline
    return NS(**{**vars(case), 'many_scope': scope, 'many_baseline': baseline,
        'many_search_id': search['id'], 'many_evidence_id': evidence_id})


def test_multihit_collect_and_each_recheck_cold_verify_one_search_once_and_detect_changed_bytes(formal_many_hits, monkeypatch):
    from generic_agents import runtime_documents as documents
    from generic_agents.config import settings
    case = formal_many_hits
    checked, reads = [], []
    original_check, original_bytes = documents.checked_search, documents.evidence_bytes

    def search_spy(db, p, search):
        checked.append(search.id)
        return original_check(db, p, search)

    def bytes_spy(db, p, identity, *args, **kwargs):
        if identity == case.many_evidence_id:
            reads.append(identity)
        return original_bytes(db, p, identity, *args, **kwargs)

    monkeypatch.setattr(documents, 'checked_search', search_spy)
    monkeypatch.setattr(documents, 'evidence_bytes', bytes_spy)
    with Session(case.bound) as db:
        before = counts(db, case.p)
        assert sources.collect(db, case.p, case.many_scope) == case.many_baseline
        assert checked == [case.many_search_id] and reads == [case.many_evidence_id]
        assert sources.recheck(db, case.p, case.many_baseline['snapshot']) == case.many_baseline
        assert checked == [case.many_search_id]*2 and reads == [case.many_evidence_id]*2
        assert sources.recheck(db, case.p, case.many_baseline['snapshot']) == case.many_baseline
        assert checked == [case.many_search_id]*3 and reads == [case.many_evidence_id]*3
        assert counts(db, case.p) == before
        evidence = db.get(m.Evidence, case.many_evidence_id)
        path = settings().storage_dir/evidence.storage_key
        saved = path.read_bytes()
        try:
            # Controlled corruption of the genuine disposable source after
            # prior successful reads; neither metadata nor hashes are edited.
            path.write_bytes(b'changed after a previous successful recheck')
            with pytest.raises(HTTPException) as error:
                sources.recheck(db, case.p, case.many_baseline['snapshot'])
            assert error.value.status_code in {404, 409}
            assert len(checked) == 4 and len(reads) == 4
        finally:
            path.write_bytes(saved)
        # Neither the earlier success nor failed proof may survive this call.
        assert sources.recheck(db, case.p, case.many_baseline['snapshot']) == case.many_baseline
        assert len(checked) == 5 and len(reads) == 5
        assert counts(db, case.p) == before


@pytest.mark.parametrize('fault', ['entry_keyword', 'entry_version', 'control_result_hash', 'control_pending'])
def test_conflicting_fixed_search_pins_never_borrow_the_first_entry_proof(formal_many_hits, monkeypatch, fault):
    from generic_agents import runtime_documents as documents
    case = formal_many_hits
    snapshot = copy.deepcopy(case.many_baseline['snapshot'])
    if fault == 'entry_keyword':
        snapshot['entries'][1]['source_ref']['keyword'] = '另一个原文词'
    elif fault == 'entry_version':
        snapshot['entries'][1]['source_ref']['version'] += 1
    elif fault == 'control_result_hash':
        snapshot['document_controls'][0]['result_hash'] = '0'*64
    else:
        snapshot['document_controls'][0]['result_hash'] = None
    checks = []
    original = documents.checked_search
    def checked(db, p, search):
        checks.append(search.id)
        return original(db, p, search)
    monkeypatch.setattr(documents, 'checked_search', checked)
    with Session(case.bound) as db:
        before = counts(db, case.p)
        with pytest.raises(HTTPException) as error:
            sources.recheck(db, case.p, snapshot)
        assert error.value.status_code == 409
        assert checks == [case.many_search_id]
        assert counts(db, case.p) == before


@pytest.mark.parametrize('fault', ['hit_hash', 'hit_index', 'date_basis'])
def test_shared_batch_proof_still_checks_each_original_hit_and_date(formal_many_hits, monkeypatch, fault):
    from generic_agents import runtime_documents as documents
    case = formal_many_hits
    snapshot = copy.deepcopy(case.many_baseline['snapshot'])
    ref = snapshot['entries'][1]['source_ref']
    if fault == 'hit_hash':
        ref['hit_hash'] = '0'*64
    elif fault == 'hit_index':
        ref['hit_index'] = 28
    else:
        ref['date_basis']['batch_day'] = '2000-01-01'
    checks = []
    original = documents.checked_search
    def checked(db, p, search):
        checks.append(search.id)
        return original(db, p, search)
    monkeypatch.setattr(documents, 'checked_search', checked)
    with Session(case.bound) as db:
        with pytest.raises(HTTPException) as error:
            sources.recheck(db, case.p, snapshot)
        assert error.value.status_code == 409
        assert checks == [case.many_search_id]

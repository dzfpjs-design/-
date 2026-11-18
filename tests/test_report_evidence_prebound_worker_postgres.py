"""Real fenced worker with synthetic PG data and HTTP; never contacts a provider."""
from uuid import uuid4
from datetime import timedelta
import os
import io
import copy
import zipfile
import json
from pathlib import Path
import signal
import subprocess
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import report_evidence_delegated_budget_start as budget_start
from generic_agents import report_evidence_delegated_dispatch_gate as gate
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, SetupReportDraft
from generic_agents.worker import process_once
from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview
from tests.test_report_evidence_delegated_wire_postgres import owned_capture_roots, install_http
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_evidence_pipeline_v3 import v3_scene
import pytest


@pytest.fixture
def long_reading_scene(client, monkeypatch, local_runtime):
    from tests import test_report_full_reading_source as source_tests
    from tests import test_report_full_reading_pipeline as full_tests
    original = source_tests.word_bytes()
    source = zipfile.ZipFile(io.BytesIO(original))
    out = io.BytesIO()
    prefix = '人工先响应🙂。'
    content = prefix + '林' * (21 * 240 - len(prefix))
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body><w:p><w:r><w:t>' + content + '</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(out, 'w') as archive:
        for entry in source.infolist():
            archive.writestr(entry, xml.encode() if entry.filename == 'word/document.xml' else source.read(entry.filename))
    monkeypatch.setattr(source_tests, 'word_bytes', lambda: out.getvalue())
    full_scene = source_tests.full_source_scene.__wrapped__(client, monkeypatch, local_runtime)
    return full_tests.reading_scene.__wrapped__(full_scene, monkeypatch)


def start_plan(reading_scene, monkeypatch):
    monkeypatch.setattr(budget_start, '_enabled', lambda: True)
    monkeypatch.setattr(gate, 'ready', lambda: True)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    p, request, command, preview, old = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        started = budget_start.start_tx(db, p, request, command,
            preview_id=preview['preview_id'], allowance_preview=preview['allowance'])
        db.commit()
    return p, started['result']['draft'], started['grant_id'], old


def test_legacy_fe_map_reduce_still_works_in_isolated_database(monkeypatch, request):
    from tests import test_report_evidence_pipeline_v3 as legacy_tests
    def check():
        assert engine().url.host == '127.0.0.1' and engine().url.database.startswith('ga_test_')
    monkeypatch.setattr(legacy_tests, 'fence', check)
    scene = request.getfixturevalue('v3_scene')
    provider = legacy_tests.SyntheticProvider(scene, monkeypatch)
    preview, _, accepted = legacy_tests.start(scene)
    middle = legacy_tests.mapped(scene, accepted)
    _, _, _, final = legacy_tests.reduced(scene, middle)
    assert preview['input_summary']['map_call_count'] == 2
    assert provider.stages == ['map-v3', 'map-v3', 'reduce-v2']
    assert final['status'] == 'review' and len(provider.calls) == 3
    with Session(engine()) as db:
        assert pipeline.owned(db, scene['p'], final['id']).status == 'review'


def test_fenced_worker_scoped_plan_multiple_stages(reading_scene, monkeypatch):
    p, draft, grant_id, _ = start_plan(reading_scene, monkeypatch)
    requests = []
    install_http(monkeypatch, requests)
    assert process_once(draft['job_id'])
    with Session(engine()) as db:
        row = pipeline.owned(db, p, draft['id'])
        assert len(row.data['maps']) >= 2
        assert row.status == 'awaiting_merge', (row.data.get('error_code'), requests)
        assert all(stage['status'] == 'succeeded' for stage in row.data['maps'])
        assert len(requests) == len(row.data['maps'])
        grant = db.get(Job, grant_id)
        assert [x['call_id'] for x in grant.data['consumed']] == [s['reserved_call_id'] for s in row.data['maps']]
        assert all(db.get(CloudCall, s['reserved_call_id']).status == 'succeeded' for s in row.data['maps'])
        from generic_agents import report_evidence_delegated_reduce_preflight as preflight
        command = pipeline.ReducePreviewCommand(revision=row.version, request_id='fer_' + str(uuid4()))
        checked = preflight.preview(db, p, row, command)
        assert checked['can_send'] is False
        reduction = pipeline.reduce_preview(row, p, command)
        row.status = 'running'
        row.data = {**row.data, 'phase':'reduce',
            'reduction':pipeline._empty_stage('queued', preview=reduction, start_receipt=None)}
        assert preflight.preview(db, p, row, command, _rechecking=True) == checked
        intent_audit = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'cloud.delegated_evidence.transport_intent_v1'))
        intent_audit.data = {**intent_audit.data, 'intent_hash':'0'*64}
        db.flush()
        with pytest.raises(preflight.ReducePreflightDenied):
            preflight.preview(db, p, row, command, _rechecking=True)
        db.rollback()


def _fourteen_frozen_maps_and_reduce(long_reading_scene, monkeypatch, mode):
    from generic_agents import report_evidence_delegated_origin as origin
    from generic_agents import report_evidence_preparation as prep
    from generic_agents.models import Capability
    from generic_agents.security import authenticated
    from tests.test_cloud_report_plan_allowance_postgres import prepared_request
    from tests.test_report_full_second_batch_preview import scene_with_candidate
    from tests.test_vertical_slice import publish
    scoped_mode=mode=='scoped_success'
    monkeypatch.setattr(budget_start, '_enabled', lambda: not scoped_mode)
    monkeypatch.setattr(gate, 'ready', lambda: not scoped_mode)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 20)
    _, old, _ = scene_with_candidate(long_reading_scene, monkeypatch)
    model = json.loads((Path(__file__).parents[1] / 'examples/deepseek.json').read_text())
    model.update(name='synthetic_fourteen_' + uuid4().hex[:10], description='Synthetic no-network scope')
    model['execution'].update(model='deepseek-v4-pro', thinking_mode='disabled', max_tokens=8192)
    cap_view = publish(long_reading_scene['client'], model)
    request = prepared_request(long_reading_scene)
    with Session(engine()) as db:
        p = authenticated(request, db)
    reading = {**copy.deepcopy(long_reading_scene['body']), 'request_id': 'fl_' + str(uuid4()), 'model_id':cap_view['id']}
    command = origin.LinkedPreviewCommand(original_draft_id=old['id'], original_revision=old['revision'], reading=reading)
    with Session(engine()) as db:
        natural = origin._proposal(db, p, command)['reading_preview']
        catalog = pipeline._catalog(natural)
        refs = [ref for ref in catalog['index'] if ref.startswith('w:')]
        assert len(refs) == 21
        generation = natural['map_preparation']['generation_contract']
        context = natural['map_preparation']['context']
        cap = db.get(Capability, reading['model_id'])
        sizes = [prep.prepare_map_request(cap, generation, prep.evidence_payload(catalog, refs[:n]), context, '')
                 ['input_summary']['prepared_messages_bytes'] for n in (2, 3)]
    assert sizes[0] < sizes[1]
    # This synthetic profile is more restrictive, never more permissive; the
    # real splitter and all subsequent source/hash checks remain unpatched.
    monkeypatch.setattr(prep, 'MAX_MAP_PREPARED_BYTES', (sizes[0] + sizes[1]) // 2)
    with Session(engine(), expire_on_commit=False) as db:
        preview = budget_start.preview_tx(db, p, request, command)
        assert len(preview['allowance']['stages']) == 14
        db.commit()
    if scoped_mode:
        from generic_agents import report_evidence_scoped_activation as activation
        with Session(engine(),expire_on_commit=False) as db:
            activation.issue_tx(db,p,request,preview['preview_id'])
            db.commit()
    start = origin.LinkedStartCommand(**command.model_dump(mode='json'),
        linked_preview_hash=preview['linked_preview_hash'], consent=True)
    with Session(engine(), expire_on_commit=False) as db:
        result = budget_start.start_tx(db, p, request, start, preview_id=preview['preview_id'],
            allowance_preview=preview['allowance'])
        db.commit()
    draft, grant_id = result['result']['draft'], result['grant_id']
    requests = []
    install_http(monkeypatch, requests)
    assert process_once(draft['job_id'])
    with Session(engine()) as db:
        row = pipeline.owned(db, p, draft['id'])
        grant = db.get(Job, grant_id)
        assert row.status == 'awaiting_merge', (row.status, row.data.get('error_code'))
        stages = row.data['maps']
        assert len(stages) == len(requests) == len(grant.data['consumed']) == 14
        call_ids = [s['reserved_call_id'] for s in stages]
        assert len(set(call_ids)) == 14
        assert [c['stage_id'] for c in grant.data['consumed']] == [s['chunk_id'] for s in stages]
        assert [c['call_id'] for c in grant.data['consumed']] == call_ids
        assert sum(c['token_bound'] for c in grant.data['consumed']) <= grant.data['manifest']['max_tokens']
        assert all(db.get(CloudCall, call).status == 'succeeded' for call in call_ids)
        assert all(s['status'] == 'succeeded' for s in stages)
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 15  # 14 new plus historical synthetic call
        from generic_agents import report_evidence_delegated_reduce_preflight as preflight
        command = pipeline.ReducePreviewCommand(revision=row.version, request_id='fer_' + str(uuid4()))
        value = preflight.preview(db, p, row, command)
        assert value['map_calls'] == 14 and value['additional_call_count'] == 1
        assert value['token_bound'] > 0 and value['additional_cost'] is True
        assert value['can_start'] is value['can_send'] is value['user_confirmed_business'] is False
        assert value['source_hash'] == row.data['preview']['source_hash']
    assert not process_once(draft['job_id'])
    assert len(requests) == 14
    from generic_agents import report_evidence_delegated_reduce_start as reduce_start
    route='/api/setup/report-evidence-readings/'+draft['id']+'/delegated-reduce'
    preview_response=long_reading_scene['client'].post(route+'/preview',json=command.model_dump(mode='json'))
    assert preview_response.status_code==200,preview_response.text
    user_preview=preview_response.json()
    assert user_preview['preflight']==value and user_preview['can_start'] is scoped_mode
    assert user_preview['maximum_call_count']==1 and user_preview['token_upper_bound']==value['token_bound']
    assert user_preview['price_limit_available'] is False and user_preview['price_upper_bound'] is None
    assert user_preview['supported_map_count']==14 and '无法计算或保证费用上限' in user_preview['price_note']
    start_body={'revision':command.revision,'request_id':command.request_id,
        'reduction_preview_hash':value['reduction_preview_hash'],'preflight_hash':value['preflight_hash'],'consent':True}
    if not scoped_mode:
        denied=long_reading_scene['client'].post(route+'/start',json=start_body)
        assert denied.status_code==409
    monkeypatch.setattr(reduce_start, '_enabled', lambda: not scoped_mode)
    if mode == 'issue_sql_drift':
        from sqlalchemy import event, update
        from generic_agents.models import User
        def drift(session):
            if any(isinstance(item,Job) and item.kind=='cloud_report_reduce_allowance_v1'
                    for item in session.identity_map.values()):
                session.connection().execute(update(User).where(User.id==p.user_id).values(active=False))
        event.listen(Session,'before_commit',drift)
        try:
            tampered=long_reading_scene['client'].post(route+'/start',json=start_body)
        finally:
            event.remove(Session,'before_commit',drift)
        assert tampered.status_code==409,tampered.text
        with Session(engine()) as db:
            row=pipeline.owned(db,p,draft['id'])
            assert row.status=='awaiting_merge' and db.get(User,p.user_id).active is True
            assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_report_reduce_allowance_v1'))==0
            assert db.scalar(select(func.count()).select_from(CloudCall))==15
        return
    response=long_reading_scene['client'].post(route+'/start',json=start_body)
    assert response.status_code==202,response.text
    issue={'job_id':response.json()['job_id'],'receipt':response.json()['receipt']}
    from generic_agents import cloud_scoped_extra_reservation as scoped
    with Session(engine()) as db:
        grant=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
            Job.idempotency_key=='evidence-reduce-allowance-v1:'+digest({'job_id':issue['job_id']})))
        permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
            Job.idempotency_key==scoped.permit_key(issue['job_id'],'reduce')))
        issue.update(grant_id=grant.id,permit_id=permit.id)
    monkeypatch.setattr(reduce_start, '_enabled', lambda: False)
    monkeypatch.setattr(gate, 'ready', lambda: False)
    replay=long_reading_scene['client'].post(route+'/start',json=start_body)
    assert replay.status_code==202 and replay.json()['replayed'] is True and replay.json()['job_id']==issue['job_id']
    with Session(engine()) as db:
        permit=db.get(Job,issue['permit_id'])
        permit.data={**permit.data,'used_call_id':str(uuid4())}
        db.commit()
    forged=long_reading_scene['client'].post(route+'/start',json=start_body)
    assert forged.status_code==409
    with Session(engine()) as db:
        permit=db.get(Job,issue['permit_id'])
        permit.data={**permit.data,'used_call_id':None}
        db.commit()
    monkeypatch.setattr(reduce_start, '_enabled', lambda: not scoped_mode)
    monkeypatch.setattr(gate, 'ready', lambda: not scoped_mode)
    with Session(engine()) as db:
        row = pipeline.owned(db, p, draft['id'])
        assert row.status == 'queued' and row.data['phase'] == 'reduce'
        assert db.get(Job, issue['job_id']).data['report_draft_id'] == draft['id']
        assert db.get(Job, issue['permit_id']).data['used_call_id'] is None
        assert db.get(Job, issue['grant_id']).data['manifest']['user_confirmed_business'] is False
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 15
    if mode == 'reserved':
        from generic_agents.leases import attach
        from generic_agents.worker import claim
        leased=claim(issue['job_id'],kinds=['read_report_draft'],workspace_id=p.workspace_id)
        assert leased is not None
        with Session(engine(),expire_on_commit=False) as db:
            attach(db,*leased);db.info['worker_actor']=p
            row=pipeline.owned(db,p,draft['id'],True);row.status='running'
            stage=pipeline._stage(row,'reduce')
            pipeline._replace_stage(row,'reduce',{**stage,'status':'running','inflight':True})
            db.commit()
        with Session(engine(),expire_on_commit=False) as db:
            attach(db,*leased);db.info['worker_actor']=p
            manifest=db.get(Job,issue['grant_id']).data['manifest']
            call_id=scoped.reserve_scoped_tx(db,p,issue['permit_id'],manifest['model_id'],
                manifest['token_bound'],manifest['request_hash'],manifest['options_hash'])
            db.commit()
        with Session(engine()) as db:
            row=pipeline.owned(db,p,draft['id'])
            assert pipeline._stage(row,'reduce')['reserved_call_id']==call_id
            assert db.get(Job,issue['grant_id']).data['used_call_id']==call_id
            assert db.get(Job,issue['permit_id']).data['used_call_id']==call_id
            assert db.get(CloudCall,call_id).status=='reserved'
        return
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    if mode == 'after_wire':
        original = delegated.response_context
        def interrupted(ctx,*args,**kwargs):
            if ctx['binding']['stage']=='reduce':
                monkeypatch.setattr(delegated,'response_context',original)
                raise ValueError('synthetic interruption after reduce wire fsync')
            return original(ctx,*args,**kwargs)
        monkeypatch.setattr(delegated,'response_context',interrupted)
    assert process_once(issue['job_id'],kinds=['read_report_draft'],deliver=False,workspace_id=p.workspace_id)
    with Session(engine()) as db:
        row = pipeline.owned(db, p, draft['id'])
        call_id = pipeline._stage(row, 'reduce')['reserved_call_id']
        assert call_id is not None
        assert db.get(Job, issue['grant_id']).data['used_call_id'] == call_id
        assert db.get(Job, issue['permit_id']).data['used_call_id'] == call_id
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 16
        assert len([item for item in requests if isinstance(item,dict)]) == 15
        if mode == 'after_wire':
            assert row.status == 'response_unknown' and db.get(CloudCall, call_id).status == 'reserved'
            version = row.version
        else:
            assert row.status in {'review','needs_clarification'} and db.get(CloudCall, call_id).status == 'succeeded'
    if mode == 'after_wire':
        recovered=long_reading_scene['client'].post('/api/setup/report-evidence-readings/'+draft['id']+'/recover-saved',
            json={'revision':version,'request_id':str(uuid4())})
        assert recovered.status_code==200,recovered.text
        with Session(engine()) as db:
            row=pipeline.owned(db,p,draft['id'])
            assert row.status in {'review','needs_clarification'}
            assert db.get(CloudCall,call_id).status=='succeeded'
            assert db.scalar(select(func.count()).select_from(CloudCall)) == 16
        assert len([item for item in requests if isinstance(item,dict)]) == 15


def test_fourteen_frozen_maps_consume_one_scoped_call_each(long_reading_scene, monkeypatch):
    _fourteen_frozen_maps_and_reduce(long_reading_scene,monkeypatch,'reserved')


@pytest.mark.parametrize('mode',['success','after_wire'])
def test_fourteen_maps_then_reduce_real_worker(long_reading_scene, monkeypatch, mode):
    _fourteen_frozen_maps_and_reduce(long_reading_scene,monkeypatch,mode)


def test_fourteen_maps_then_reduce_with_persisted_activation(long_reading_scene,monkeypatch):
    _fourteen_frozen_maps_and_reduce(long_reading_scene,monkeypatch,'scoped_success')


def test_reduce_issuance_rejects_late_direct_sql(long_reading_scene, monkeypatch):
    _fourteen_frozen_maps_and_reduce(long_reading_scene,monkeypatch,'issue_sql_drift')


def test_paid_wire_recovery_does_not_reserve_or_send_again(reading_scene, monkeypatch):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    p, draft, grant_id, _ = start_plan(reading_scene, monkeypatch)
    requests = []
    install_http(monkeypatch, requests)
    original = delegated.response_context
    def interrupted(*args, **kwargs):
        monkeypatch.setattr(delegated, 'response_context', original)
        raise ValueError('synthetic crash after wire persistence')
    monkeypatch.setattr(delegated, 'response_context', interrupted)
    assert process_once(draft['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        assert row.status == 'response_unknown'
        version = row.version
        call_id = row.data['maps'][0]['reserved_call_id']
        assert db.get(CloudCall, call_id).status == 'reserved'
        before = db.scalar(select(func.count()).select_from(CloudCall))
        assert len(db.get(Job, grant_id).data['consumed']) == 1
    response = reading_scene['client'].post('/api/setup/report-evidence-readings/' + draft['id'] + '/recover-saved',
        json={'revision': version, 'request_id': str(uuid4())})
    assert response.status_code == 200, response.text
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        assert row.data['maps'][0]['status'] == 'succeeded'
        assert db.get(CloudCall, call_id).status == 'succeeded'
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
        assert len(db.get(Job, grant_id).data['consumed']) == 1
    assert len([r for r in requests if isinstance(r, dict)]) == 1
    base = '/api/setup/report-evidence-readings/' + draft['id']
    resume = {'revision':row.version, 'request_id':'fes_' + str(uuid4())}
    result = reading_scene['client'].post(base + '/resume-preview', json=resume)
    assert result.status_code == 200, result.text
    assert result.json()['can_start'] is False
    result = reading_scene['client'].post(base + '/resume',
        json={**resume, 'preview_hash':result.json()['preview_hash'], 'consent':True})
    assert result.status_code == 409 and result.json()['detail']['code'] == 'delegated_resume_handoff_unavailable'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before


def test_legacy_delegated_entry_cannot_start_even_with_dispatch_gate_enabled(reading_scene, monkeypatch):
    from tests.test_report_evidence_delegated_origin import proposed, launch
    monkeypatch.setattr(gate, 'ready', lambda: True)
    _, _, _, body, proposal = proposed(reading_scene, monkeypatch)
    assert proposal['can_start'] is False
    with Session(engine()) as db:
        before = (db.scalar(select(func.count()).select_from(Job)),
                  db.scalar(select(func.count()).select_from(CloudCall)))
    result = launch(reading_scene, body, proposal)
    assert result.status_code == 409 and result.json()['detail']['code'] == 'delegated_dispatch_not_ready'
    with Session(engine()) as db:
        assert (db.scalar(select(func.count()).select_from(Job)),
                db.scalar(select(func.count()).select_from(CloudCall))) == before


def test_legacy_delegated_entry_replays_existing_number_when_gates_closed(reading_scene, monkeypatch):
    monkeypatch.setattr(budget_start, '_enabled', lambda: True)
    monkeypatch.setattr(gate, 'ready', lambda: True)
    p, request, command, proposal, _ = scene_and_preview(reading_scene, monkeypatch)
    with Session(engine(), expire_on_commit=False) as db:
        result = budget_start.start_tx(db, p, request, command, preview_id=proposal['preview_id'],
            allowance_preview=proposal['allowance'])
        db.commit()
    monkeypatch.setattr(budget_start, '_enabled', lambda: False)
    monkeypatch.setattr(gate, 'ready', lambda: False)
    with Session(engine()) as db:
        before = (db.scalar(select(func.count()).select_from(Job)), db.scalar(select(func.count()).select_from(CloudCall)))
    replay = reading_scene['client'].post('/api/setup/report-evidence-delegated', json=command.model_dump(mode='json'))
    assert replay.status_code == 202, replay.text
    assert replay.json()['draft']['id'] == result['result']['draft']['id']
    with Session(engine()) as db:
        assert (db.scalar(select(func.count()).select_from(Job)), db.scalar(select(func.count()).select_from(CloudCall))) == before


def test_expired_lease_cannot_dispatch_prebound_call(reading_scene, monkeypatch):
    from generic_agents import report_evidence_prebound_transport as transport
    p, draft, grant_id, _ = start_plan(reading_scene, monkeypatch)
    requests = []
    install_http(monkeypatch, requests)
    original = transport.commit_transport_intent
    def expire(db, principal, stage, call_id, messages, options, sink):
        with Session(engine()) as other:
            job = other.get(Job, draft['job_id'])
            job.lease_until = other.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
            other.commit()
        return original(db, principal, stage, call_id, messages, options, sink)
    monkeypatch.setattr(transport, 'commit_transport_intent', expire)
    assert process_once(draft['job_id'])
    assert not [r for r in requests if isinstance(r, dict)]
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        call_id = row.data['maps'][0]['reserved_call_id']
        assert call_id and db.get(CloudCall, call_id).status == 'reserved'
        assert len(db.get(Job, grant_id).data['consumed']) == 1
        before = db.scalar(select(func.count()).select_from(CloudCall))
    monkeypatch.setattr(transport, 'commit_transport_intent', original)
    assert process_once(draft['job_id'])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        assert row.status == 'response_unknown'
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
    assert not [r for r in requests if isinstance(r, dict)]


def test_killed_worker_recovers_persisted_wire_without_new_send(reading_scene, monkeypatch, tmp_path):
    from generic_agents import report_evidence_delegated_wire_v1 as delegated
    p, draft, grant_id, old = start_plan(reading_scene, monkeypatch)
    script = tmp_path / 'killed_worker.py'
    script.write_text('''import json,os,signal,sys,httpx
from generic_agents import gateway,report_evidence_delegated_wire_v1 as delegated,report_evidence_pipeline as pipeline
from generic_agents import report_full_reading_pipeline as full
from generic_agents import report_evidence_delegated_dispatch_gate as gate
from generic_agents.config import settings
from generic_agents.worker import process_once
gate.ready=lambda:True
settings().cloud_daily_call_limit=20
settings().cloud_daily_token_limit=4000000
measure=full.measure_input
def stricter(messages,policy,max_tokens):
 payload=json.loads(messages[-1]['content'])
 selected={**policy,'max_input_bytes':min(policy['max_input_bytes'],int(sys.argv[3]))} if payload.get('schema')=='report-full-reading-extract-evidence-v1' else policy
 return measure(messages,selected,max_tokens)
full.measure_input=stricter
original=httpx.Client
class Body(httpx.SyncByteStream):
 def __init__(self,data):self.data=data
 def __iter__(self):yield self.data
def handler(request):
 assert str(request.url)=='https://api.deepseek.com/chat/completions'
 payload=json.loads(json.loads(request.content)['messages'][-1]['content'])
 value={'schema':'report-evidence-map-v3','claims':[{'kind':'职责','statement':'合成声明','span_refs':[payload['spans'][0][0]],'status':'explicit','explanation':''}]}
 data={'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}}
 return httpx.Response(200,stream=Body(json.dumps(data,ensure_ascii=False).encode()))
gateway.httpx.Client=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)
def traced(*args,**kwargs):
 try:return gateway.cloud_json(*args,**kwargs)
 except Exception:
  import traceback;traceback.print_exc();raise
pipeline.cloud_json=traced
delegated.response_context=lambda *a,**kw:os.kill(os.getpid(),signal.SIGKILL)
process_once(sys.argv[1],kinds=['read_report_draft'],deliver=False,workspace_id=sys.argv[2])
''')
    env = os.environ.copy()
    env['GA_DATABASE_URL'] = engine().url.render_as_string(hide_password=False)
    env['GA_STORAGE_DIR'] = str(settings().storage_dir)
    env['DEEPSEEK_API_KEY'] = 'synthetic-test-only'
    bound = old['preview']['batches'][0]['input_summary']['max_input_bytes']
    result = subprocess.run([str(Path(__file__).parents[1]/'.venv/bin/python'), str(script), draft['job_id'], p.workspace_id, str(bound)],
        cwd=Path(__file__).parents[1], env=env, capture_output=True, timeout=60)
    with Session(engine()) as db:
        diagnostic = db.get(SetupReportDraft, draft['id'])
        details = (diagnostic.status, diagnostic.data.get('error_code'), diagnostic.data.get('error_message'))
    assert result.returncode == -signal.SIGKILL, (details, result.stderr.decode(errors='replace')[-2500:])
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        stage = row.data['maps'][0]
        assert stage['reserved_call_id'] and stage['journal'] is None
        _, state = delegated.load(pipeline, row, stage['chunk_id'])
        assert state['ready'] and state['response_complete']
        call_id = stage['reserved_call_id']
        before = db.scalar(select(func.count()).select_from(CloudCall))
        job = db.get(Job, draft['job_id'])
        job.lease_until = db.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
        db.commit()
    requests=[]
    install_http(monkeypatch, requests)
    assert process_once(draft['job_id'], kinds=['read_report_draft'], deliver=False, workspace_id=p.workspace_id)
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        assert row.status == 'response_unknown'
        version = row.version
    response = reading_scene['client'].post('/api/setup/report-evidence-readings/' + draft['id'] + '/recover-saved',
        json={'revision': version, 'request_id': str(uuid4())})
    assert response.status_code == 200, response.text
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, draft['id'])
        assert row.data['maps'][0]['status'] == 'succeeded'
        assert db.get(CloudCall, call_id).status == 'succeeded'
        assert db.scalar(select(func.count()).select_from(CloudCall)) == before
        assert len(db.get(Job, grant_id).data['consumed']) == 1
    assert not requests

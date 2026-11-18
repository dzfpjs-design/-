"""The raw report POST interfaces follow the same workspace policy as GET."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import map_access
from generic_agents.db import engine
from generic_agents.models import Job, Membership
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_evidence_delegated_origin import owned_disposable_database, proposed


def _set_operator(scene, *, policy):
    p=scene['p']
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='operator'
        if policy:
            db.add(Job(workspace_id=p.workspace_id,kind=map_access.KIND,status='internal',
                idempotency_key=map_access.KEY,data={'businesses':{}}))
        db.commit()


def test_linked_preview_denied_after_policy_and_demotion(reading_scene,monkeypatch):
    scene=reading_scene
    _,_,_,body,initial=proposed(scene,monkeypatch)
    assert initial['reading_preview']['source_snapshot']['original']['reading']['blocks']
    _set_operator(scene,policy=True)
    assert scene['client'].get('/api/setup/report-evidence-readings').status_code==403
    response=scene['client'].post('/api/setup/report-evidence-delegated/preview',json=body)
    assert response.status_code==403
    assert 'source_snapshot' not in response.text


def test_ordinary_preview_denied_after_policy_and_demotion(reading_scene):
    scene=reading_scene
    body={**copy.deepcopy(scene['body']), 'request_id':'fe_'+str(uuid4())}
    baseline=scene['client'].post('/api/setup/report-evidence-readings/preview',json=body)
    assert baseline.status_code==200,baseline.text
    _set_operator(scene,policy=True)
    response=scene['client'].post('/api/setup/report-evidence-readings/preview',json=body)
    assert response.status_code==403
    assert 'source_snapshot' not in response.text


def test_operator_keeps_legacy_preview_without_policy(reading_scene):
    scene=reading_scene
    body={**copy.deepcopy(scene['body']), 'request_id':'fe_'+str(uuid4())}
    _set_operator(scene,policy=False)
    response=scene['client'].post('/api/setup/report-evidence-readings/preview',json=body)
    assert response.status_code==200,response.text
    assert response.json()['source_snapshot']['original']['reading']['blocks']


def test_admin_can_preview_with_policy(reading_scene):
    scene=reading_scene
    with Session(engine()) as db:
        db.add(Job(workspace_id=scene['p'].workspace_id,kind=map_access.KIND,status='internal',
            idempotency_key=map_access.KEY,data={'businesses':{}}));db.commit()
    body={**copy.deepcopy(scene['body']), 'request_id':'fe_'+str(uuid4())}
    response=scene['client'].post('/api/setup/report-evidence-readings/preview',json=body)
    assert response.status_code==200,response.text
    assert response.json()['source_snapshot']['original']['reading']['blocks']


@pytest.mark.parametrize('route', ['preview', 'preview-v2'])
def test_reduce_preview_cannot_bypass_policy(reading_scene, route):
    scene=reading_scene
    _set_operator(scene,policy=True)
    path='/api/setup/report-evidence-readings/'+str(uuid4())+'/delegated-reduce/'+route
    assert scene['client'].post(path,json={}).status_code==403


def test_full_reading_original_preview_denied_after_demotion(reading_scene):
    scene=reading_scene
    baseline=scene['client'].post('/api/setup/report-full-readings/preview',json=scene['body'])
    assert baseline.status_code==200,baseline.text
    assert baseline.json()['source_snapshot']['original']['reading']['blocks']
    _set_operator(scene,policy=True)
    response=scene['client'].post('/api/setup/report-full-readings/preview',json=scene['body'])
    assert response.status_code==403
    assert 'source_snapshot' not in response.text


def test_full_reading_saved_original_denied_after_demotion(reading_scene):
    from tests.test_report_full_reading_pipeline import start
    scene=reading_scene
    _,_,created=start(scene)
    identity=created['draft']['id']
    assert scene['client'].get('/api/setup/report-full-readings/'+identity).json()['preview']['source_snapshot']['original']['reading']['blocks']
    _set_operator(scene,policy=True)
    assert scene['client'].get('/api/setup/report-full-readings/'+identity).status_code==403
    assert scene['client'].get('/api/setup/report-full-readings/by-request/'+scene['body']['request_id']).status_code==403


def test_legacy_report_original_preview_and_reading_denied_after_demotion(reading_scene):
    scene=reading_scene
    body={key:copy.deepcopy(scene['body'][key]) for key in ('revision','model_id','material_refs','note')}
    body['request_id']='rd_'+str(uuid4())
    baseline=scene['client'].post('/api/setup/report-drafts/preview',json=body)
    assert baseline.status_code==200,baseline.text
    assert baseline.json()['source_snapshot']['materials']
    source=scene['original']
    path='/api/setup/report-drafts/readings/'+source['id']
    params={'version':source['version'],'hash':source['hash']}
    assert scene['client'].get(path,params=params).status_code==200
    _set_operator(scene,policy=True)
    assert scene['client'].post('/api/setup/report-drafts/preview',json=body).status_code==403
    assert scene['client'].get(path,params=params).status_code==403


def test_admin_full_reading_uses_read_only_snapshot_under_policy(reading_scene):
    from tests.test_report_full_reading_pipeline import start
    scene=reading_scene
    _,_,created=start(scene)
    with Session(engine()) as db:
        db.add(Job(workspace_id=scene['p'].workspace_id,kind=map_access.KIND,status='internal',
            idempotency_key=map_access.KEY,data={'businesses':{}}));db.commit()
    response=scene['client'].get('/api/setup/report-full-readings/'+created['draft']['id'])
    assert response.status_code==200,response.text
    assert response.json()['preview']['source_snapshot']['original']['reading']['blocks']


def test_operator_keeps_report_list_access_under_policy(reading_scene):
    scene=reading_scene
    _set_operator(scene,policy=True)
    assert scene['client'].get('/api/setup/report-full-readings').status_code==200
    assert scene['client'].get('/api/setup/report-drafts').status_code==200

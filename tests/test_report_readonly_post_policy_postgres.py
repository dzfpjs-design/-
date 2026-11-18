"""Real HTTP coverage for the two protected read-only POST routes."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import map_access
from generic_agents.db import engine
from generic_agents.models import Job, Membership
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_origin import authorized_proposed, authorized_launch
from tests.test_report_evidence_delegated_budget_start_postgres import scene_and_preview


def _policy(scene):
    with Session(engine()) as db:
        db.add(Job(workspace_id=scene['p'].workspace_id,kind=map_access.KIND,status='internal',
            idempotency_key=map_access.KEY,data={'businesses':{}}));db.commit()


def _observe_guard(monkeypatch):
    observed=[];original=map_access.require_config
    def checked(db,p):
        original(db,p)
        observed.append(db.execute(text('SHOW transaction_read_only')).scalar_one())
    monkeypatch.setattr(map_access,'require_config',checked)
    return observed


@pytest.mark.parametrize('policy',[False,True])
def test_linked_origin_and_plan_lookup_run_inside_read_only_snapshot(reading_scene,monkeypatch,policy):
    scene=reading_scene
    # Synthetic contract gate; no worker or provider call is started.
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    from generic_agents import report_evidence_delegated_budget_start as budget
    monkeypatch.setattr(gate,'ready',lambda:True)
    monkeypatch.setattr(budget,'_enabled',lambda:True)
    _,old,_,body,preview=authorized_proposed(scene,monkeypatch)
    created=authorized_launch(scene,body,preview)
    assert created.status_code==202,created.text
    identity=created.json()['draft']['id']
    if policy:_policy(scene)
    observed=_observe_guard(monkeypatch)
    find=scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/find',
        json={'original_draft_id':old['id']})
    assert find.status_code==200,find.text
    assert find.json()['actor_id']==scene['p'].user_id and find.json()['workspace_id']==scene['p'].workspace_id
    assert len(find.json()['items'])==1 and observed and all(v=='on' for v in observed)
    observed.clear()
    origin=scene['client'].post('/api/setup/report-evidence-delegated/'+identity+'/origin')
    assert origin.status_code==200,origin.text
    assert origin.json()['origin']['payload']['basis']['actor_id']==scene['p'].user_id
    assert observed and all(v=='on' for v in observed)
    if policy:
        with Session(engine()) as db:
            m=db.scalar(select(Membership).where(Membership.workspace_id==scene['p'].workspace_id,
                                                  Membership.user_id==scene['p'].user_id))
            m.role='operator';db.commit()
        assert scene['client'].post('/api/setup/report-evidence-delegated/budget-plan/find',
            json={'original_draft_id':old['id']}).status_code==403
        assert scene['client'].post('/api/setup/report-evidence-delegated/'+identity+'/origin').status_code==403


@pytest.mark.parametrize('policy',[False,True])
def test_activation_write_remains_read_write_and_persists(reading_scene,monkeypatch,policy):
    scene=reading_scene
    _,_,_,preview,_=scene_and_preview(scene,monkeypatch)
    if policy:_policy(scene)
    observed=_observe_guard(monkeypatch)
    response=scene['client'].post('/api/setup/report-evidence-delegated/activation',
        json={'preview_id':preview['preview_id'],'consent':True})
    assert response.status_code==201,response.text
    assert observed and all(v=='off' for v in observed)
    with Session(engine()) as db:
        row=db.get(Job,response.json()['activation_id'])
        assert row and row.workspace_id==scene['p'].workspace_id and row.status=='internal'

"""Route-level and bridge orchestration checks with explicit store I/O doubles."""
from types import SimpleNamespace as Namespace

import pytest
from fastapi import HTTPException

from generic_agents import setup_api
from generic_agents import report_business_catalogue_api_v211 as api
from generic_agents import report_business_catalogue_store_v211 as s


@pytest.fixture
def scene(monkeypatch):
    db, actor = object(), object()
    origin = {'metadata': {'source_snapshot_hash': 'a' * 64}}
    document = {'businesses': [{'key': 'monitor', 'name': '监测业务'},
        {'key': 'survey', 'name': '调查业务'}]}
    reference = {'plan_id': 'real-plan', 'plan_hash': 'b' * 64}
    row = Namespace(id='00000000-0000-0000-0000-000000000002', version=3, document_hash='c' * 64,
        data={'origin': origin, 'document': document, 'plan_ref': reference})
    plan = Namespace(id='real-plan', producer_id='original-report', version=2, revision=4,
        data={'ledger': {'scopes': [{'scope_id': 'monitor', 'name': '监测业务'},
            {'scope_id': 'survey', 'name': '调查业务'}]}})
    calls = []

    def check_plan(actual_db, actual_actor, ref, doc, original):
        assert (actual_db, actual_actor, ref, doc, original) == (db, actor, reference, document, origin)
        calls.append('protected-plan')
        return plan

    def check_source(actual_db, actual_actor, original, *, lock):
        assert (actual_db, actual_actor, original, lock) == (db, actor, origin, False)
        calls.append('current-source')

    monkeypatch.setattr(s.plans, '_iam', lambda *_: calls.append('iam'))
    monkeypatch.setattr(s, '_lock', lambda *_: calls.append('read-lock'))
    monkeypatch.setattr(s, '_inventory', lambda *_: ({row.id: row}, {}, {}))
    monkeypatch.setattr(s, '_check_plan_ref', check_plan)
    monkeypatch.setattr(s, '_current_source', check_source)
    return Namespace(db=db, actor=actor, row=row, plan=plan, calls=calls)


def test_only_selected_business_and_original_verified_flow_plan_are_returned(scene):
    v = scene
    assert s.business_flow_source(v.db, v.actor, v.row.id, 'survey') == {
        'schema': 'report-catalogue-flow-source-v316', 'producer_id': 'original-report',
        'revision': 4, 'draft_id': v.row.id, 'draft_version': 3, 'document_hash': 'c' * 64,
        'plan_id': 'real-plan', 'plan_version': 2, 'plan_hash': 'b' * 64,
        'scope_id': 'survey', 'business_name': '调查业务', 'source_snapshot_hash': 'a' * 64,
    }
    assert v.calls == ['iam', 'read-lock', 'protected-plan', 'current-source']


@pytest.mark.parametrize('damage,scope,status', [
    ('unconfirmed', 'survey', 409), ('unselected', 'foreign', 404),
    ('missing', 'survey', 409), ('renamed', 'survey', 409),
])
def test_unconfirmed_or_different_catalogue_scope_cannot_transfer(scene, damage, scope, status):
    v = scene
    if damage == 'unconfirmed':
        v.row.data['plan_ref'] = None
    if damage == 'missing':
        v.plan.data['ledger']['scopes'].pop()
    if damage == 'renamed':
        v.plan.data['ledger']['scopes'][-1]['name'] = '不同业务'
    with pytest.raises(HTTPException) as error:
        s.business_flow_source(v.db, v.actor, v.row.id, scope)
    assert error.value.status_code == status and 'current-source' not in v.calls


def test_stale_current_source_is_not_returned_as_new_work(scene, monkeypatch):
    def stale(*args, **kwargs):
        raise HTTPException(409, '原配置已经变化')

    monkeypatch.setattr(s, '_current_source', stale)
    with pytest.raises(HTTPException, match='原配置已经变化'):
        s.business_flow_source(scene.db, scene.actor, scene.row.id, 'survey')


def test_api_forwards_the_exact_catalogue_and_scope_to_the_verified_bridge(monkeypatch):
    db, actor = object(), object()
    draft = '00000000-0000-0000-0000-000000000001'
    response = {'schema': 'report-catalogue-flow-source-v316'}

    def source(actual_db, actual_actor, actual_draft, scope):
        assert (actual_db, actual_actor, actual_draft, scope) == (db, actor, draft, 'survey')
        return response

    monkeypatch.setattr(s, 'business_flow_source', source)
    assert api.business_flow_source(draft, actor, db, scope_id='survey') is response

"""Same-request source reuse is projection only, never dispatch authority."""
from types import SimpleNamespace
from copy import deepcopy
import pytest
from fastapi import HTTPException
from generic_agents import threshold_shadow_store_v151 as store


def fixture(monkeypatch):
    calls=[];p=SimpleNamespace(workspace_id='workspace',user_id='actor',role='admin')
    scope={'workspace_id':'workspace','source_id':'source','instance_id':'instance'}
    pin={'result_hash':'actual-result'}
    row=lambda identity:SimpleNamespace(id=identity,workspace_id='workspace',actor_id='approver',evaluation_id='evaluation',data={'scope':deepcopy(scope),'evaluation':deepcopy(pin)})
    monkeypatch.setattr(store,'_approver',lambda *args:calls.append('approver'))
    monkeypatch.setattr(store,'lock_resources',lambda *args:calls.append('lock'))
    def evaluation(*args):calls.append('evaluation');return object(),deepcopy(scope),deepcopy(pin)
    monkeypatch.setattr(store,'evaluation_context',evaluation)
    monkeypatch.setattr(store,'frontier',lambda *args:calls.append('frontier'))
    return p,row,calls


def test_list_projection_reuses_same_source_once_but_checks_each_approver(monkeypatch):
    p,row,calls=fixture(monkeypatch);context={}
    store._current_grant(object(),p,row('first'),False,context)
    store._current_grant(object(),p,row('second'),False,context)
    assert calls.count('evaluation')==calls.count('frontier')==1
    assert calls.count('approver')==2 and 'lock' not in calls


def test_next_request_does_not_reuse_previous_source_result(monkeypatch):
    p,row,calls=fixture(monkeypatch)
    store._current_grant(object(),p,row('first'),False,{})
    def withdrawn(*args):raise HTTPException(409,'source withdrawn after first read')
    monkeypatch.setattr(store,'evaluation_context',withdrawn)
    with pytest.raises(HTTPException) as error:store._current_grant(object(),p,row('second'),False,{})
    assert error.value.status_code==409


def test_locked_authorization_rejects_read_projection_context_before_using_it(monkeypatch):
    p,row,calls=fixture(monkeypatch)
    with pytest.raises(store.ShadowInventoryError):store._current_grant(object(),p,row('first'),True,{})
    assert calls==[]


def test_dispatch_authorization_rechecks_source_and_locks_every_time(monkeypatch):
    p,row,calls=fixture(monkeypatch)
    store.fresh_grant(object(),p,row('first'));store.fresh_grant(object(),p,row('second'))
    assert calls.count('evaluation')==calls.count('frontier')==calls.count('lock')==2


def test_shared_evaluation_cannot_hide_different_saved_scope_or_result(monkeypatch):
    p,row,calls=fixture(monkeypatch);context={};store._current_grant(object(),p,row('first'),False,context)
    bad=row('second');bad.data['scope']['instance_id']='another-instance'
    with pytest.raises(HTTPException):store._current_grant(object(),p,bad,False,context)
    bad=row('third');bad.data['evaluation']['result_hash']='another-result'
    with pytest.raises(HTTPException):store._current_grant(object(),p,bad,False,context)


def projection_row():
    """Only the checked row attributes used by the read-projection seam."""
    workspace='10000000-0000-4000-8000-000000000001'
    row=SimpleNamespace(
        id='10000000-0000-4000-8000-000000000002',workspace_id=workspace,
        actor_id='10000000-0000-4000-8000-000000000003',
        evaluation_id='10000000-0000-4000-8000-000000000004',
        scope_key='a'*64,status='active',version=1,
        data={'schema':'threshold-shadow-grant-record-v151',
            'scope':{'workspace_id':workspace},'evaluation':{},
            'frontier':{'schema':'threshold-shadow-frontier-v151',
                'observation_ids':[],'source_record_ids':[]},
            'epoch':1,'max_inputs':1,'expires_at':'2030-01-01T00:00:00+00:00',
            'title':'原影子观察','reason':'原批准理由'})
    row.record_hash=store.hash_value(store.snapshot(row))
    return SimpleNamespace(workspace_id=workspace,user_id=row.actor_id,role='admin'),row


@pytest.mark.parametrize('changed',['version','record_hash','data'])
def test_consumption_refresh_cannot_mix_new_grant_with_checked_command_history(monkeypatch,changed):
    # Simulate only the populate_existing refresh inside the actual view call;
    # this is a source-level projection unit test, not a PG concurrency test.
    from generic_agents import threshold_shadow_execution_v151 as execution
    from generic_agents import threshold_study_views_v149 as study_views
    p,row=projection_row();events=[]
    checked=([row],[],{row.id:store.projection_pin(row)})

    def refreshed(*args):
        events.append('consumptions')
        if changed=='version':row.version+=1
        elif changed=='record_hash':row.record_hash='b'*64
        else:
            row.data=deepcopy(row.data)
            row.data['reason']='并发控制保存的新理由'
        return []

    def forbidden_current(*args,**kwargs):
        pytest.fail('changed projection must be rejected before current-source qualification')

    def forbidden_rule(*args,**kwargs):
        pytest.fail('changed projection must be rejected before rule-summary display')

    monkeypatch.setattr(execution,'consumption_inventory',refreshed)
    monkeypatch.setattr(store,'_current_grant',forbidden_current)
    monkeypatch.setattr(study_views,'rule_summary',forbidden_rule)
    with pytest.raises(HTTPException) as error:
        store.view(object(),p,row,_verified_inventory=checked,_readonly_context={})
    assert error.value.status_code==409
    assert events==['consumptions']


@pytest.mark.parametrize('changed',['version','max_inputs'])
def test_projection_pin_distinguishes_original_boolean_and_integer_types(changed):
    _,row=projection_row();original=store.projection_pin(row)
    if changed=='version':row.version=True
    else:
        row.data=deepcopy(row.data)
        row.data['max_inputs']=True
    altered=store.projection_pin(row)
    assert original['record_hash']==altered['record_hash']
    assert original['snapshot_hash']!=altered['snapshot_hash']
    assert store._same(original,altered) is False
    assert type(original['version']) is int
    if changed=='version':assert type(altered['version']) is bool

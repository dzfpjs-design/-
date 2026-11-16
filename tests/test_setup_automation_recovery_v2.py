"""M48 restore/undo boundaries on actual disposable PostgreSQL; no provider calls."""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SetupDraft,SetupRevision,SetupAnalysis,CloudCall
from generic_agents.setup_automation import safe_policy
from tests.test_instance_workspace import configuration,save,OBJECTS
from tests.test_setup_safe_automation import confirm_flow
from tests.test_setup_understanding import prepared,start,process
from tests.test_setup_objects_runtime import fake_v2,TEXT


def state(view):
    return next(x for x in view['automation_review']['businesses'] if x['business_name']=='资料核验')


def install_legacy(client):
    c=configuration();p=c['automation']['policies']['资料核验']
    p.update(triggers=['定时运行'],time='08:00',human_note='保留旧人工规则')
    view=client.get('/api/setup/draft').json()
    with Session(engine()) as db:
        row=db.get(SetupDraft,view['id']);row.data=copy.deepcopy(c);db.commit()
    return client.get('/api/setup/draft').json()


def explicit_review(client,view):
    response=client.post('/api/setup/automation/review',json={'revision':view['revision'],
        'business_id':state(view)['business_id'],'request_id':str(uuid4())})
    assert response.status_code==200,response.text
    return response.json()


def restore(client,target):
    body={'revision':client.get('/api/setup/draft').json()['revision'],'target_revision':target}
    preview=client.post('/api/setup/draft/restore/preview',json=body);assert preview.status_code==200,preview.text
    response=client.post('/api/setup/draft/restore',json={**body,'preview_hash':preview.json()['preview_hash']})
    assert response.status_code==200,response.text
    return preview.json(),response.json()


@pytest.mark.parametrize('snapshot',['legacy','confirmed'])
def test_restore_deleted_business_old_policy_is_reviewed_again_before_compilation(client,snapshot):
    before=install_legacy(client)
    if snapshot=='confirmed':before=explicit_review(client,before)
    empty=copy.deepcopy(before['configuration']);empty.update(businesses=[],flows={},automation={'version':1,'policies':{}})
    save(client,empty)
    preview,restored=restore(client,before['revision'])
    assert preview['configuration']['automation']['policies']['资料核验']['flow_review']['status']=='needs_review'
    assert state(restored)['status']=='needs_review'
    policy=restored['configuration']['automation']['policies']['资料核验']
    assert policy['triggers']==['定时运行'] and policy['human_note']=='保留旧人工规则'
    member=client.get('/api/instances/members').json()[0]
    checked=client.post('/api/instances/check',json={'revision':restored['revision'],'objects':OBJECTS,
        'role_bindings':{'核验负责人':member['id']}})
    assert checked.status_code==200 and not checked.json()['businesses'][0]['available']
    with Session(engine()) as db:
        historical=db.scalar(select(SetupRevision).where(SetupRevision.draft_id==before['id'],SetupRevision.version==before['revision']))
        assert historical.data==before['configuration']
    assert state(explicit_review(client,restored))['status']=='confirmed'


def test_restore_same_current_policy_keeps_server_confirmation_even_if_history_is_legacy(client):
    before=install_legacy(client);confirmed=explicit_review(client,before)
    _,restored=restore(client,before['revision'])
    assert state(restored)['status']=='confirmed'
    assert restored['configuration']['automation']['policies']['资料核验']['flow_review']==confirmed['configuration']['automation']['policies']['资料核验']['flow_review']


@pytest.mark.parametrize('change',['flow','policy'])
def test_restore_changed_current_semantics_cannot_inherit_historical_authorization(client,change):
    before=install_legacy(client);c=copy.deepcopy(before['configuration'])
    if change=='flow':c['flows']['资料核验']['output']='新的人工输出';confirm_flow(c,'资料核验')
    else:c['automation']['policies']['资料核验']['time']='09:00'
    save(client,c);explicit_review(client,client.get('/api/setup/draft').json())
    _,restored=restore(client,before['revision'])
    assert state(restored)['status']=='needs_review'


def test_restore_deleted_exact_conservative_business_can_remain_safe(client):
    c=configuration();c['automation']['policies']['资料核验']=safe_policy();save(client,c)
    before=client.get('/api/setup/draft').json();assert state(before)['status']=='conservative_safe'
    empty=copy.deepcopy(c);empty.update(businesses=[],flows={},automation={'version':1,'policies':{}});save(client,empty)
    _,restored=restore(client,before['revision'])
    assert state(restored)['status']=='conservative_safe'


def existing_proposal(client,monkeypatch):
    body,_=prepared(client,TEXT,initial=configuration());body.update(contract_version=2,request_id=str(uuid4()))
    before=explicit_review(client,client.get('/api/setup/draft').json());body['revision']=before['revision']
    def update(value,payload):
        value['businesses'][0].update(name='资料核验',original_name='资料核验')
    calls=fake_v2(monkeypatch,update);row,_=start(client,body);process(row)
    reviewed=client.get('/api/setup/understanding/'+row['id']).json();assert reviewed['status']=='review',reviewed.get('error')
    with Session(engine()) as db:paid=copy.deepcopy(db.get(SetupAnalysis,row['id']).data['raw_response'])
    return before,reviewed,calls,paid


def applied_existing(client,monkeypatch):
    before,reviewed,calls,paid=existing_proposal(client,monkeypatch)
    # The supported legacy apply route can add objects; existing business
    # changes require the scoped edit flow and must not replace an entire flow.
    response=client.post('/api/setup/understanding/'+reviewed['id']+'/apply',json={'version':reviewed['version'],
        'revision':before['revision'],'names':[],'object_names':['设备']})
    assert response.status_code==200,response.text
    return before,response.json(),calls,paid


def test_existing_confirmed_business_cannot_be_replaced_by_legacy_whole_business_apply(client,monkeypatch):
    before,reviewed,calls,paid=existing_proposal(client,monkeypatch)
    saved_before=client.get('/api/setup/draft').json()
    response=client.post('/api/setup/understanding/'+reviewed['id']+'/apply',json={'version':reviewed['version'],
        'revision':before['revision'],'names':['资料核验'],'object_names':[]})
    assert response.status_code==409 and '逐项修改' in response.json()['detail']
    assert client.get('/api/setup/draft').json()==saved_before
    assert client.get('/api/setup/understanding/'+reviewed['id']).json()==reviewed
    with Session(engine()) as db:
        assert db.get(SetupAnalysis,reviewed['id']).data['raw_response']==paid
        assert len(db.scalars(select(CloudCall)).all())==1
    assert len(calls)==1


def test_object_only_undo_preserves_flow_confirmation_and_paid_original(client,monkeypatch):
    before,applied,calls,paid=applied_existing(client,monkeypatch)
    response=client.post('/api/setup/understanding/'+applied['analysis']['id']+'/undo',json={
        'version':applied['analysis']['version'],'revision':applied['revision'],'names':['资料核验']})
    assert response.status_code==200,response.text
    current=client.get('/api/setup/draft').json()
    assert state(current)['status']=='confirmed'
    assert current['configuration']['flows']==before['configuration']['flows']
    assert current['configuration']==before['configuration']
    with Session(engine()) as db:
        assert db.get(SetupAnalysis,applied['analysis']['id']).data['raw_response']==paid
        assert len(db.scalars(select(CloudCall)).all())==1
    assert len(calls)==1


def test_undo_cannot_overwrite_later_human_draft(client,monkeypatch):
    before,applied,calls,paid=applied_existing(client,monkeypatch)
    c=copy.deepcopy(applied['configuration']);c['setupNotes']={'manual':'后续人工修改不可覆盖'}
    revision=save(client,c);saved=client.get('/api/setup/draft').json()
    response=client.post('/api/setup/understanding/'+applied['analysis']['id']+'/undo',json={
        'version':applied['analysis']['version'],'revision':revision,'names':['资料核验']})
    assert response.status_code==409
    assert client.get('/api/setup/draft').json()==saved and len(calls)==1

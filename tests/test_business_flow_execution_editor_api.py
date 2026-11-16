"""Pure actual API composition boundaries; authority adapters are explicit spies."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from generic_agents import business_flow_execution_adoption as service
from tests.test_business_flow_execution_adoption import fixture, command


def raw_command():
    binding=fixture(1)
    value=command(binding);value.update(schema='business-flow-execution-adopt-command-v1',
        request_id='70000000-0000-4000-8000-000000000007',preview_hash='7'*64,
        consent=True,share_source=True)
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()


@pytest.mark.parametrize('code',[409,422,403,500])
def test_only_absent_original_read_preparation_rejection_can_clear_original_intent(monkeypatch,code):
    raw=raw_command();p=NS(role='admin');seen=[]
    monkeypatch.setattr(service,'recover',lambda *args:seen.append('original') or {'found':False})
    def prepare(*args):
        seen.append('read_only_prepare');raise HTTPException(code,'current source unavailable')
    monkeypatch.setattr(service,'prepare',prepare)
    with pytest.raises(HTTPException) as error:service.adopt(None,p,raw)
    assert seen==['original','read_only_prepare'] and error.value.status_code==code
    if code in (409,422):
        assert error.value.detail=={'code':'business_flow_execution_adoption_not_applied','not_applied':True,
            'request_id':json.loads(raw)['request_id'],'body_sha256':hashlib.sha256(raw).hexdigest(),
            'reason':'current source unavailable'}
    else:assert error.value.detail=='current source unavailable'


def test_corrupt_original_inventory_never_becomes_proof_of_absence(monkeypatch):
    raw=raw_command()
    def recover(*args):raise HTTPException(409,'inventory damaged')
    monkeypatch.setattr(service,'recover',recover)
    monkeypatch.setattr(service,'prepare',lambda *args:pytest.fail('must not load source'))
    with pytest.raises(HTTPException) as error:service.adopt(None,NS(role='admin'),raw)
    assert error.value.detail=='inventory damaged'


def test_creation_errors_are_not_relabelled_as_absent_original(monkeypatch):
    raw=raw_command();binding=fixture(1);config=NS(data={},version=1,id='config')
    monkeypatch.setattr(service,'recover',lambda *args:{'found':False})
    monkeypatch.setattr(service,'prepare',lambda *args:(config,{'preview_hash':'7'*64,'binding':binding}))
    def proposed(*args):raise HTTPException(409,'outside read preparation')
    monkeypatch.setattr(service,'proposed',proposed)
    with pytest.raises(HTTPException) as error:service.adopt(None,NS(role='admin'),raw)
    assert error.value.detail=='outside read preparation'


def world(monkeypatch):
    from generic_agents import business_flow_execution_api as api
    p=NS(role='admin',user_id='author',workspace_id='workspace')
    qid='70000000-0000-4000-8000-000000000001';origin={'adoption_id':'row'}
    binding={'business_name':'业务甲','qualification_id':qid,'qualification_hash':'a'*64,
        'flow_id':'flow_a','flow':{'steps':[]},'binding_hash':'b'*64}
    config=NS(id='config',version=3,data={service.CONFIG_KEY:{'业务甲':origin},
        'businesses':['业务甲'],'flows':{'业务甲':binding['flow']}})
    record=NS(id='row',configuration_id='config',accepted_revision=2)
    monkeypatch.setattr(api.qualifications,'get',lambda *args:{'qualification_hash':'a'*64,'value':{'business':{'flows':[]}}})
    monkeypatch.setattr(service,'current_configuration',lambda *args:config)
    monkeypatch.setattr(service,'_inventory',lambda *args:[record])
    def checked(db,principal,value):
        assert value==origin;return copy.deepcopy(binding)
    monkeypatch.setattr(service,'checked_origin',checked)
    return api,p,qid,config,record


def test_context_lists_actual_adoptions_per_selected_qualification_without_marking_execution(monkeypatch):
    api,p,qid,config,record=world(monkeypatch)
    result=api.context(None,p,qid)
    assert result['adopted_flows']==[{'flow_id':'flow_a','business_name':'业务甲','adoption_id':'row',
        'accepted_revision':2,'qualification_id':qid,'qualification_hash':'a'*64,'binding_hash':'b'*64}]
    assert result['current_source_checked'] is result['can_apply'] is result['can_execute'] is False
    assert config.version==3


@pytest.mark.parametrize('fault',['missing_origin','wrong_flow','malformed_origin','missing_ledger'])
def test_context_damaged_current_configuration_does_not_silently_show_unadopted(monkeypatch,fault):
    api,p,qid,config,record=world(monkeypatch)
    if fault=='missing_origin':config.data[service.CONFIG_KEY]={}
    elif fault=='wrong_flow':config.data['flows']['业务甲']={'steps':[{'name':'changed'}]}
    elif fault=='malformed_origin':config.data[service.CONFIG_KEY]['业务甲']['adoption_id']=[]
    else:monkeypatch.setattr(service,'_inventory',lambda *args:[])
    with pytest.raises(HTTPException) as error:api.context(None,p,qid)
    assert error.value.status_code==409

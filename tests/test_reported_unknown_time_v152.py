"""Original unknown times are integrity-checked, never filled with now()."""
from copy import deepcopy
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from generic_agents import perception_signals as signals
from generic_agents import instance_api
from generic_agents.contracts import digest


def guarded(monkeypatch,wire_time,sql_time,*,present=True):
    p=SimpleNamespace(workspace_id='workspace',user_id='operator')
    instance=SimpleNamespace(id='instance');release=SimpleNamespace(id='release')
    source=SimpleNamespace(id='source');extractor=SimpleNamespace(id='extractor');entity=SimpleNamespace(id='entity')
    contract={'schema':'local-reported-rule-v1','instance_id':instance.id,'release_id':release.id}
    original={'source_id':source.id,'source_record_id':'packet','processing_version':'1',
        'entity_id':entity.id,'business_day':'2026-10-21','attributes':{'count':19}}
    if present:original['observed_at']=wire_time
    row=SimpleNamespace(id='observation',source_id=source.id,entity_id=entity.id,
        source_record_id='packet',processing_version='1',business_day='2026-10-21',observed_at=sql_time,
        data={'reported_rule_contract':deepcopy(contract),'original_request':original,
            'input_hash':digest(original),'attributes':{'count':19},'extractor_id':extractor.id})
    records={v.id:v for v in (instance,release,source,extractor,entity)}
    monkeypatch.setattr(signals,'scoped',lambda db,model,identity,*args,**kw:records[identity])
    monkeypatch.setattr(signals,'published',lambda db,p,identity,*args:records[identity])
    monkeypatch.setattr(signals,'rule_contract',lambda *args:deepcopy(contract))
    monkeypatch.setattr(instance_api,'active',lambda *args:(instance,release))
    db=SimpleNamespace(scalar=lambda query:'operator',refresh=lambda value:None)
    return db,p,row


def test_saved_unknown_time_stays_unknown_after_actual_reported_guard(monkeypatch):
    db,p,row=guarded(monkeypatch,None,None);before=deepcopy(row.data)
    signals.guard_rule(db,p,row)
    assert row.observed_at is None and row.data==before

@pytest.mark.parametrize('wire_time,sql_time,present',[
    (None,datetime(2026,10,21,tzinfo=timezone.utc),True),
    ('2026-10-21T00:00:00+00:00',None,True),(None,None,False),
    ('2026-10-21T00:00:00',datetime(2026,10,21,tzinfo=timezone.utc),True)])
def test_unknown_cannot_hide_changed_missing_or_naive_time(monkeypatch,wire_time,sql_time,present):
    db,p,row=guarded(monkeypatch,wire_time,sql_time,present=present)
    with pytest.raises(HTTPException) as error:signals.guard_rule(db,p,row)
    assert error.value.status_code==409

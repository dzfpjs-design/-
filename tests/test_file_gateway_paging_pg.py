"""Paging must preserve existing file-gateway histories in a disposable PostgreSQL."""
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents import ftp_gateway_api, smb_gateway_api

@pytest.mark.parametrize('protocol,module',[('ftp',ftp_gateway_api),('smb',smb_gateway_api)])
def test_gateway_large_history_and_stable_cursor(client,account,monkeypatch,protocol,module):
    ws=account[2];stamp=datetime(2026,1,1,tzinfo=timezone.utc);target=str(uuid4());other=str(uuid4())
    monkeypatch.setattr(module,'find_grant',lambda *a,**kw: (_ for _ in ()).throw(module.FTPError('revoked') if protocol=='ftp' else module.SMBError('revoked')))
    with Session(engine()) as db:
        db.add_all([Job(id=target,workspace_id=ws,idempotency_key=str(uuid4()),kind=module.CONNECTION,status='internal',created_at=stamp,data={'name':'目标目录','grant_id':'gone','status':'active'})]+[
            Job(id=str(uuid4()),workspace_id=ws,idempotency_key=str(uuid4()),kind=module.CONNECTION,status='internal',created_at=stamp,data={'name':f'目录{i}','grant_id':'gone','status':'active'}) for i in range(105)])
        for i in range(1105):
            rid=str(uuid4());belongs=i<105
            data={'request_id':rid,'state':'succeeded','kind':'listing','submission':{'connection_id':target if belongs else other},'result':{'listing_id':rid,'files':[]}}
            db.add(Job(id=rid,workspace_id=ws,idempotency_key=str(uuid4()),kind=module.REQUEST,status='internal',created_at=stamp,data=data))
        db.commit()
    root='/api/'+protocol
    first=client.get(root+'/connections');assert first.status_code==200
    c1=first.json();assert len(c1['connections'])==100 and c1['next_cursor']
    second=client.get(root+'/connections',params={'cursor':c1['next_cursor']});assert second.status_code==200
    c2=second.json();assert len(c2['connections'])==6 and c2['next_cursor'] is None
    assert len({x['id'] for x in c1['connections']+c2['connections']})==106
    base=root+'/connections/'+target+'/listings';one=client.get(base);assert one.status_code==200
    a=one.json();assert len(a['listings'])==100 and a['source_available'] is False and a['next_cursor']
    two=client.get(base,params={'cursor':a['next_cursor']});assert two.status_code==200
    b=two.json();assert len(b['listings'])==5 and b['next_cursor'] is None
    assert len({x['request_id'] for x in a['listings']+b['listings']})==105
    assert client.get(base,params={'cursor':c1['next_cursor']}).status_code==409
    assert client.get(base,params={'cursor':'bad'}).status_code==422

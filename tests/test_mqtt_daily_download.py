"""Authenticated HTTP files: real bytes, complete frozen membership and recoverable refusals."""
import asyncio
import csv
import io
from hashlib import sha256
from tempfile import SpooledTemporaryFile
from uuid import uuid4
import pytest
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import (MqttDailyLedger,MqttDailyChange,MqttReceipt,Evidence,Membership,
    AuthSession,User,Workspace,MqttGateway,CloudCall)
from generic_agents.mqtt_daily import CSV_COLUMNS,summary
from generic_agents.mqtt_inbox import create_gateway,set_status
from generic_agents.security import Principal
from generic_agents.contracts import digest
from tests.test_mqtt_inbox import gateway,save,packet,config
from tests.test_mqtt_daily import current
from tests.test_mqtt_binding_routes import managed_pair,process_all
from tests.test_mqtt_observations import save as bound_save


def csv_request(client,value,workspace,kind='original',total=None,instance=None,**overrides):
    params={'workspace_id':workspace,'revision':value['revision'],
        'expected_revision_hash':value['revision_hash'],'expected_total':value['counts']['raw_total'] if total is None else total,
        'kind':kind,'status':'all',**({'instance_id':instance} if instance else {}),**overrides}
    return client.get('/api/mqtt/daily/'+value['id']+'/export.csv',params=params)


def parsed(response):
    assert response.status_code==200,response.text[:300]
    assert response.headers['content-type'].startswith('text/csv')
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.content.startswith(b'\xef\xbb\xbf') and response.content.endswith(b'\r\n')
    assert response.headers['x-content-sha256']==sha256(response.content).hexdigest()
    assert int(response.headers['content-length'])==len(response.content)
    rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'),newline='')))
    assert rows[0]==list(CSV_COLUMNS) and all(len(row)==14 for row in rows)
    return rows[1:]


def refused(response,code):
    assert response.status_code==code,response.text[:300]
    assert response.headers['content-type'].startswith('text/html')
    assert '下载未开始' in response.text and '进度保留' in response.text
    assert 'content-disposition' not in response.headers and 'x-content-sha256' not in response.headers


def test_http_csv_1006_actual_originals_has_last_member_fixed_revision_and_real_file_hash(gateway,client):
    for index in range(1006):save(gateway,packet(record_id=f'csv-{index}'))
    value=current(gateway)
    save(gateway,packet(record_id='late-excluded'))
    response=csv_request(client,value,gateway[0].workspace_id)
    rows=parsed(response)
    assert len(rows)==1006 and len({row[1] for row in rows})==1006
    assert rows[-1][1]=='csv-1005' and 'late-excluded' not in {row[1] for row in rows}
    assert rows[0][3]=='2026-10-11 07:00:00' and rows[0][5]=='2026-10-11'
    assert response.headers['x-mqtt-record-count']=='1006'
    assert response.headers['x-mqtt-revision']==str(value['revision'])
    assert response.headers['x-mqtt-revision-hash']==value['revision_hash']
    assert response.headers['x-mqtt-scope-hash']==digest({'workspace_id':gateway[0].workspace_id,'ledger_id':value['id'],
        'revision':value['revision'],'revision_hash':value['revision_hash'],'instance_id':None,'kind':'original','status':'all','total':1006})
    assert current(gateway)['counts']['raw_total']==1007
    with Session(engine()) as db:assert db.query(CloudCall).count()==0


@pytest.mark.parametrize('bad',['total','revision_hash','missing_member','member_hash','original_bytes'])
def test_download_refuses_bad_scope_or_any_member_before_csv_headers(gateway,client,bad):
    first=save(gateway,packet(record_id='first'));save(gateway,packet(record_id='second'));value=current(gateway)
    overrides={}
    if bad=='total':overrides['expected_total']=1
    elif bad=='revision_hash':overrides['expected_revision_hash']='0'*64
    else:
        with Session(engine()) as db:
            change=db.scalar(select(MqttDailyChange).where(MqttDailyChange.ledger_id==value['id'],
                MqttDailyChange.receipt_id==first.id))
            if bad=='missing_member':db.delete(change)
            elif bad=='member_hash':change.data={**change.data,'item':{**change.data['item'],'record_label':'篡改'}}
            else:
                evidence=db.get(Evidence,first.evidence_id)
                (settings().storage_dir/evidence.storage_key).write_bytes(b'broken-original')
            db.commit()
    refused(csv_request(client,value,gateway[0].workspace_id,**overrides),409)


def test_unknown_day_and_formula_values_are_text_without_invented_capture_time(gateway,client):
    save(gateway,packet(record_id=' \t=HYPERLINK("https://invalid")',observed_at=None))
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));value=summary(db,gateway[0],ledger.id)
    response=csv_request(client,value,gateway[0].workspace_id)
    row=parsed(response)[0]
    # 无效设备数据编号不会伪造为已识别编号；原字节仍独立保留。
    assert row[1]=='未识别数据编号' and row[3]=='未知' and row[5]=='采集时间待核'
    assert 'unknown' in response.headers['content-disposition']


@pytest.mark.parametrize('value',['=2+2','+2+2','-2+2','@SUM(A1)','  =2+2','\t=2+2','\r=2+2','\n=2+2'])
def test_human_csv_fields_remain_literal_text_with_quotes_and_leading_whitespace(value):
    from generic_agents.mqtt_daily import _csv_text,_csv_row
    item={'kind':'processing','status':'pending','instance_name':value,'needs_review':False}
    buffer=io.StringIO(newline='');writer=csv.writer(buffer,quoting=csv.QUOTE_ALL,lineterminator='\r\n')
    writer.writerow([_csv_text(cell) for cell in _csv_row(item)])
    row=next(csv.reader(io.StringIO(buffer.getvalue(),newline='')))
    assert row[8]=="'"+value and len(row)==14


def test_instance_http_files_include_only_own_frozen_target_and_preserve_pending_history(client,account,monkeypatch):
    fixture,destinations,_=managed_pair(client,account,monkeypatch)
    original,raw=bound_save(fixture,record='shared-http')
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));before=summary(db,fixture[3],ledger.id)
    process_all(original.id)
    unrelated,_=bound_save(fixture,record='later-unrelated')
    instance=destinations[0][0]['instance_id'];workspace=fixture[3].workspace_id
    own=client.get('/api/mqtt/daily/'+ledger.id,params={'revision':before['revision'],'instance_id':instance}).json()
    old=parsed(csv_request(client,own,workspace,kind='processing',total=1,instance=instance))
    assert len(old)==1 and old[0][8]=='真实业务实例1' and old[0][6]=='见原件收讫记录'
    assert old[0][7]=='等待后台处理'
    originals=parsed(csv_request(client,own,workspace,total=1,instance=instance));assert [r[1] for r in originals]==['shared-http']
    params={'workspace_id':workspace,'revision':before['revision'],'expected_revision_hash':before['revision_hash'],
        'instance_id':instance,'expected_input_sha256':original.input_sha256}
    base='/api/mqtt/daily/'+ledger.id+'/original/'
    downloaded=client.get(base+original.id+'/download',params=params)
    assert downloaded.status_code==200 and downloaded.content==raw
    assert downloaded.headers['x-content-sha256']==original.input_sha256
    assert downloaded.headers['x-mqtt-scope-hash']==digest({'workspace_id':workspace,'ledger_id':ledger.id,
        'revision':before['revision'],'revision_hash':before['revision_hash'],'instance_id':instance,
        'receipt_id':original.id,'input_sha256':original.input_sha256})
    refused(client.get(base+unrelated.id+'/download',params={**params,'expected_input_sha256':unrelated.input_sha256}),404)


def test_completed_historical_processing_can_download_after_route_and_instance_pause(client,account,monkeypatch):
    fixture,destinations,routes=managed_pair(client,account,monkeypatch)
    original,_=bound_save(fixture,record='completed-before-pause');process_all(original.id)
    instance=destinations[0][0]['instance_id']
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));value=summary(db,fixture[3],ledger.id,instance_id=instance)
    paused=client.post('/api/mqtt/routes/'+routes[0]['id']+'/status',json={'request_id':str(uuid4()),
        'expected_version':routes[0]['version'],'status':'paused','include_existing':False})
    assert paused.status_code==200,paused.text
    assert client.post('/api/instances/'+instance+'/pause',json={'version':destinations[0][1]['version']}).status_code==200
    rows=parsed(csv_request(client,value,fixture[3].workspace_id,kind='processing',total=1,instance=instance))
    assert rows[0][7]=='已提取候选信息' and rows[0][8]=='真实业务实例1'


def test_filter_is_bound_to_actual_complete_count_and_cannot_reuse_another_kind_total(gateway,client):
    save(gateway,packet());value=current(gateway)
    rows=parsed(csv_request(client,value,gateway[0].workspace_id,status='pending'));assert len(rows)==1
    empty=parsed(csv_request(client,value,gateway[0].workspace_id,total=0,status='received'));assert empty==[]
    refused(csv_request(client,value,gateway[0].workspace_id,status='received'),409)
    refused(csv_request(client,value,gateway[0].workspace_id,kind='processing'),409)


@pytest.mark.parametrize('revocation',['membership','session','user'])
def test_download_rechecks_real_authority_after_full_prepare_and_closes_spool(gateway,client,monkeypatch,revocation):
    import generic_agents.mqtt_daily as daily
    original=save(gateway,packet());value=current(gateway);real=daily.prepare_csv;streams=[]
    def revoke(*args,**kwargs):
        ready=real(*args,**kwargs);streams.append(ready['stream'])
        with Session(engine()) as db:
            if revocation=='membership':db.execute(delete(Membership).where(Membership.user_id==gateway[0].user_id))
            elif revocation=='session':
                for row in db.scalars(select(AuthSession).where(AuthSession.user_id==gateway[0].user_id)):row.revoked=True
            else:db.get(User,gateway[0].user_id).active=False
            db.commit()
        return ready
    monkeypatch.setattr(daily,'prepare_csv',revoke)
    refused(csv_request(client,value,gateway[0].workspace_id),403 if revocation=='membership' else 401)
    assert streams and streams[0].closed


def test_workspace_selection_on_plain_cookie_http_link_is_real_membership_not_first_workspace(gateway,client,account):
    with Session(engine(),expire_on_commit=False) as db:
        workspace=Workspace(name='第二个合法下载工作区');db.add(workspace);db.flush()
        db.add(Membership(user_id=gateway[0].user_id,workspace_id=workspace.id,role='admin'));db.flush()
        p=Principal(gateway[0].user_id,workspace.id,'admin','test')
        row=create_gateway(db,p,'第二工作区来源',config());set_status(db,p,row.id,row.version,'active');db.commit()
        second=(p,row.id,row.version)
    save(second,packet(record_id='second-workspace'))
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==workspace.id))
        member.role='viewer';db.commit()
        ledger=db.scalar(select(MqttDailyLedger).where(MqttDailyLedger.gateway_id==second[1]));value=summary(db,p,ledger.id)
    client.headers.pop('X-Workspace-ID')
    assert parsed(csv_request(client,value,workspace.id))[0][1]=='second-workspace'
    refused(csv_request(client,value,str(uuid4())),403)
    client.headers['X-Workspace-ID']=account[2]
    refused(csv_request(client,value,workspace.id),403)


def test_download_unauthenticated_and_malformed_query_are_readable_without_attachment(gateway,client):
    save(gateway,packet());value=current(gateway)
    refused(client.get('/api/mqtt/daily/'+value['id']+'/export.csv'),422)
    client.cookies.clear();refused(csv_request(client,value,gateway[0].workspace_id),401)


def test_explicit_json_preflight_refusal_keeps_detail_without_attachment(gateway,client):
    save(gateway,packet());value=current(gateway)
    client.headers['Accept']='application/json'
    response=csv_request(client,value,gateway[0].workspace_id,expected_total=999)
    assert response.status_code==409 and '下载总数' in response.json()['detail']
    assert 'content-disposition' not in response.headers
    missing=client.get('/api/mqtt/daily/'+value['id']+'/export.csv')
    assert missing.status_code==422 and '范围不完整' in missing.json()['detail']


def test_original_download_hash_refusal_is_readable_and_never_a_fake_file(gateway,client):
    receipt=save(gateway,packet());value=current(gateway)
    response=client.get('/api/mqtt/daily/'+value['id']+'/original/'+receipt.id+'/download',params={
        'workspace_id':gateway[0].workspace_id,'revision':value['revision'],
        'expected_revision_hash':value['revision_hash'],'expected_input_sha256':'0'*64})
    refused(response,409)


def test_spool_is_closed_when_real_stream_disconnects_before_first_body_byte(gateway,client,monkeypatch):
    import generic_agents.mqtt_api as api
    from starlette.requests import Request
    stream=SpooledTemporaryFile(max_size=16,mode='w+b');stream.write(b'\xef\xbb\xbf"header"\r\n');stream.seek(0)
    ready={'stream':stream,'filename':'test.csv','size':stream.seek(0,2),'sha256':'a'*64,
           'revision':1,'revision_hash':'b'*64,'total':0,'scope_hash':'c'*64};stream.seek(0)
    monkeypatch.setattr(api,'_file_principal',lambda *a:gateway[0])
    monkeypatch.setattr(api,'_recheck_file_principal',lambda *a:gateway[0])
    monkeypatch.setattr('generic_agents.mqtt_daily.prepare_csv',lambda *a:ready)
    with Session(engine()) as db:
        response=api.daily_csv_file('ledger',Request({'type':'http','headers':[]}),db,gateway[0].workspace_id,1,'b'*64,0,None,'original','all')
    async def disconnect():
        sent=[]
        async def receive():return {'type':'http.disconnect'}
        async def send(message):
            sent.append(message['type'])
            if message['type']=='http.response.start':await asyncio.Event().wait()
        await response({'type':'http','asgi':{'spec_version':'2.3'}},receive,send)
        assert 'http.response.body' not in sent
    asyncio.run(disconnect());assert stream.closed

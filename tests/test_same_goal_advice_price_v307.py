"""Actual fixed parser/price contract over explicit HTTP, storage and SQL IO.

The HTML below mirrors the parent-inspected public table shape (including td
headers and row/colspans). It is synthetic, not a real GET, Price Audit, model
publication, persisted Evidence, account permission, cloud call or money grant.
"""
import copy
import hashlib
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_price_v307 as price
from generic_agents.contracts import Manifest, digest
from generic_agents.models import Audit, Capability, Evidence
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate

AT = datetime(2026, 11, 2, 22, tzinfo=timezone.utc)


def uid(): return str(uuid4())


def table():
    header='<tr><td colspan="3">模型</td><td>deepseek-flash<sup>(1)</sup></td><td>deepseek-v4-pro</td></tr>'
    revision='<tr><td colspan="3">模型版本</td><td>DeepSeek-V4.1-Flash-1017</td><td>DeepSeek-V4-Pro-0813</td></tr>'
    feature='<tr><td rowspan="7">功能</td><td colspan="2">特性0</td><td>支持</td><td>支持</td></tr>'
    feature+=''.join('<tr><td colspan="2">特性'+str(i)+'</td><td>支持</td><td>支持</td></tr>' for i in range(1,7))
    rows=[
        '<tr><td rowspan="6">价格<sup>(2)</sup></td><td rowspan="2">百万tokens输入<br>（缓存命中）</td><td>空闲时段</td><td>0.02元</td><td>0.15元</td></tr>',
        '<tr><td>高峰时段</td><td>0.04元</td><td>0.30元</td></tr>',
        '<tr><td rowspan="2">百万tokens输入<br>（缓存未命中）</td><td>空闲时段</td><td>1元</td><td>4.5元</td></tr>',
        '<tr><td>高峰时段</td><td>2元</td><td>9.0元</td></tr>',
        '<tr><td rowspan="2">百万tokens输出</td><td>空闲时段</td><td>4元</td><td>13.5元</td></tr>',
        '<tr><td>高峰时段</td><td>8元</td><td>27.0元</td></tr>']
    return '<table>'+header+revision+feature+''.join(rows)+'</table>'


def html():
    # Includes a separate irrelevant table and >20KB original HTML. Internal
    # registration must preserve every byte without widening normal uploads.
    return ('<!doctype html><html><head><script>ignore: 0元 fake price</script></head><body>'
        +table()+'<table><tr><th>说明</th><th>时段</th></tr><tr><td>保留原价</td><td>公开</td></tr></table>'
        +'<!--'+'public padding '*2200+'--></body></html>').encode('utf-8')


@pytest.fixture
def scene(monkeypatch):
    p=Principal(uid(),uid(),'admin','worker')
    data=Manifest(kind='foundation_model',namespace='official_price_fixture',name='model',version='1.0.0',
        owner='explicit registry IO',description='not an actual connected model',
        input_schema={'type':'object'},output_schema={'type':'object'},allowed_agent_roles=['business'],
        execution={'provider':'deepseek','model':'deepseek-v4-pro','max_tokens':4096,'thinking_mode':'disabled'},
        data_policy={'cloud_allowed':True},budget={'max_attempts':1,'max_concurrency':1},
        dependencies=[],specification={}).model_dump(mode='json')
    model=Capability(id=uid(),workspace_id=p.workspace_id,kind='foundation_model',status='published',
        content_hash=digest(data),validated_hash=digest(data),release_version='1.0.0',data=data)
    body={'request_id':uid(),'model_pin':price.store._pin(model),'validity_seconds':1800,'reason':'登记真实官网价格依据'}
    s=NS(p=p,model=model,body=body,time=AT,raw=html(),status=200,content_type='text/html; charset=utf-8',
        reader_allowed=True,issuer_active=True,http_failure=False,fetches=0,options=[],flushes=0,
        rows={Capability:[model],Audit:[],Evidence:[]},files={},reads=[])
    class DB:
        @property
        def no_autoflush(self):return nullcontext()
        def scalar(self,statement):
            if 'clock_timestamp' in str(statement):return s.time
            model=statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            s.reads.append((model,statement._for_update_arg is not None))
            items=[item for item in s.rows[model] if predicate(statement.whereclause,item)]
            assert len(items)<=1
            return items[0] if items else None
        def scalars(self,statement):
            model=statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            assert price.store.MAX_ROWS+1 in statement.compile().params.values()
            return [item for item in s.rows[model] if predicate(statement.whereclause,item)]
        def add(self,item):
            assert type(item) is Audit
            if item.id is None:item.id=uid()  # Explicit DB-generated identifier IO.
            if item.created_at is None:item.created_at=s.time
            s.rows[Audit].append(item)
        def flush(self):s.flushes+=1
        def commit(self):pytest.fail('only API caller commits official price registration')
        @contextmanager
        def begin_nested(self):
            before=list(s.rows[Audit])
            try:yield
            except BaseException:
                s.rows[Audit][:]=before
                raise
    s.db=DB()
    def reader(db,principal,**kwargs):
        assert db is s.db
        if not s.reader_allowed or principal.workspace_id!=p.workspace_id:
            raise HTTPException(403,'explicit current reader IAM denial')
    monkeypatch.setattr(price.store,'_reader',reader)
    monkeypatch.setattr(price,'lock_current_actor',lambda *args:None)  # Actual User/AuthSession lock IO.
    def member(db,principal,identity,*,admin=False):
        if not s.issuer_active:raise HTTPException(403,'explicit active admin issuer denial')
    monkeypatch.setattr(price.store,'_member',member)
    def published(db,principal,identity,kind):
        price.need(identity==model.id and model.status=='published' and kind=='foundation_model')
        return model
    monkeypatch.setattr(price,'published',published)
    def save(db,principal,raw,media_type,metadata,cloud_model_id=None):
        assert db is s.db and principal==p and raw==s.raw and media_type=='text/plain' and cloud_model_id is None
        row=Evidence(id=uid(),workspace_id=p.workspace_id,sha256=hashlib.sha256(raw).hexdigest(),
            media_type=media_type,size_bytes=len(raw),version=1,storage_key='explicit-storage-IO-only',
            data={**copy.deepcopy(metadata),'cloud_model_ids':[],'uploaded_by':p.user_id})
        s.rows[Evidence].append(row);s.files[row.id]=raw
        s.rows[Audit].append(Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,
            action='evidence.upload',object_id=row.id,created_at=s.time,
            data={'media_type':media_type,'sha256':row.sha256,'cloud_model_id':None,'consent':False}))
        return row
    monkeypatch.setattr(price.evidence,'save_immutable',save)
    def content(db,principal,identity):
        assert db is s.db
        rows=[item for item in s.rows[Evidence] if item.id==identity]
        price.need(len(rows)==1)
        return rows[0],s.files[identity]
    monkeypatch.setattr(price.evidence,'evidence_bytes',content)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        @property
        def status_code(self):return s.status
        @property
        def headers(self):return {'content-type':s.content_type}
        url=price.URL
        def iter_bytes(self):
            for start in range(0,len(s.raw),4096):yield s.raw[start:start+4096]
    class Client:
        def __init__(self,**options):
            assert options=={'timeout':10.0,'follow_redirects':False,'trust_env':False}
            s.options.append(options)
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def stream(self,method,url):
            assert method=='GET' and url==price.URL
            s.fetches+=1
            if s.http_failure:raise httpx.ReadTimeout('explicit public GET failure')
            return Response()
    monkeypatch.setattr(price.httpx,'Client',Client)
    return s


def auth(s,body=None):return price.authorize(s.db,s.p,body or s.body)


def test_real_parser_expands_actual_td_rowspan_colspan_shape_and_chooses_peak_cachemiss():
    result=price.parse_official(html())
    assert result['provider_model_revision']=='deepseek-v4-pro-0813'
    assert result['input_cache_miss_minor_per_million']==900 and result['output_minor_per_million']==2700
    assert result['currency']=='CNY' and result['rate_unit_tokens']==1_000_000
    assert result['cache_discount_assumed'] is result['off_peak_discount_assumed'] is False


@pytest.mark.parametrize('fault',['other-model','usd','bad-unit','missing-row','bad-rowspan',
    'fractional-minor','offpeak-above-peak','two-target-tables','nonutf8','nul'])
def test_parser_never_guesses_ambiguous_model_unit_currency_or_incomplete_table(fault):
    raw=html()
    if fault=='other-model':raw=raw.replace(b'deepseek-v4-pro',b'deepseek-other')
    elif fault=='usd':raw=raw.replace('9.0元'.encode(),b'$9.0')
    elif fault=='bad-unit':raw=raw.replace('百万tokens'.encode(),'千tokens'.encode())
    elif fault=='missing-row':raw=raw.replace('<tr><td>高峰时段</td><td>8元</td><td>27.0元</td></tr>'.encode(),b'')
    elif fault=='bad-rowspan':raw=raw.replace(b'rowspan="6"',b'rowspan="5"')
    elif fault=='fractional-minor':raw=raw.replace('9.0元'.encode(),'9.001元'.encode())
    elif fault=='offpeak-above-peak':raw=raw.replace('13.5元'.encode(),'28.0元'.encode())
    elif fault=='two-target-tables':raw+=table().encode()
    elif fault=='nonutf8':raw=b'\xff'
    else:raw+=b'\x00'
    with pytest.raises(HTTPException):price.parse_official(raw)


def test_registration_preserves_full_html_upload_audit_and_returns_no_send_grant(scene):
    s=scene;result=auth(s)
    assert s.fetches==1 and len(s.rows[Evidence])==1
    event=[a for a in s.rows[Audit] if a.action==price.PRICE_BASIS][0]
    file=s.rows[Evidence][0]
    assert len(s.files[file.id])>20000 and s.files[file.id]==s.raw
    assert price.OfficialEvidenceRef.model_validate(result['evidence_ref']).size_bytes==len(s.raw)
    with pytest.raises(ValueError):price.c.EvidenceRef.model_validate(result['evidence_ref'])
    assert file.data['cloud_model_ids']==[] and file.data['http_original_content_type']==s.content_type
    assert event.object_id==s.model.id and event.data['body_hash']==digest(s.body)
    assert 'basis_ref' not in event.data
    assert result['basis_ref']=={'id':event.id,'record_hash':digest(event.data)}
    assert result['price']['official_original']['sha256']==hashlib.sha256(s.raw).hexdigest()
    assert all(result[key] is False for key in price.money.FLAGS)
    current=price.current(s.db,s.p,s.body['model_pin'],basis_ref=result['basis_ref'])
    assert current['price']==result['price'] and s.fetches==1
    assert any(model is Capability and locked for model,locked in s.reads)


def test_nonce_retry_never_fetches_again_or_renews_old_registration(scene):
    s=scene;first=auth(s);before=copy.deepcopy([a.data for a in s.rows[Audit]])
    s.time+=timedelta(hours=2);s.model.status='retired'
    again=auth(s)
    assert again['basis_ref']==first['basis_ref'] and again['price']==first['price']
    assert again['purpose']=='original-registration-receipt-not-send-permission'
    assert s.fetches==1 and [a.data for a in s.rows[Audit]]==before
    changed={**s.body,'validity_seconds':3600}
    with pytest.raises(HTTPException):auth(s,changed)
    assert s.fetches==1


def test_current_never_uses_old_low_price_after_new_registration_even_if_old_still_valid(scene):
    s=scene;first=auth(s)
    s.time+=timedelta(minutes=1)
    newer=auth(s,{**s.body,'request_id':uid(),'validity_seconds':60})
    assert newer['basis_ref']!=first['basis_ref'] and s.fetches==2
    with pytest.raises(HTTPException):price.current(s.db,s.p,s.body['model_pin'],basis_ref=first['basis_ref'])
    s.time+=timedelta(minutes=2)
    # Latest is expired, while first remains valid. No older-price fallback.
    with pytest.raises(HTTPException):price.current(s.db,s.p,s.body['model_pin'])
    assert s.fetches==2


def test_historical_original_uses_protected_preview_time_without_current_renewal(scene):
    s=scene;registered=auth(s);original=copy.deepcopy(registered['price'])
    s.time+=timedelta(hours=2);s.model.status='retired';s.issuer_active=False
    viewer=Principal(uid(),s.p.workspace_id,'viewer','worker')
    old=price.checked_original(s.db,viewer,s.body['model_pin'],basis_ref=registered['basis_ref'],priced_at=AT)
    assert old['price']==original and old['purpose']=='historical-not-current-price-authorization'
    assert s.fetches==1 and all(old[key] is False for key in price.money.FLAGS)
    with pytest.raises(HTTPException):price.current(s.db,viewer,s.body['model_pin'],basis_ref=registered['basis_ref'])
    with pytest.raises(HTTPException):
        price.checked_original(s.db,viewer,s.body['model_pin'],basis_ref=registered['basis_ref'],priced_at=s.time)


@pytest.mark.parametrize('fault',['redirect','not-html','oversize','network','bad-table','operator','disabled-admin'])
def test_failed_fetch_or_authority_cannot_mint_a_price_or_evidence(scene,fault):
    s=scene;principal=s.p
    if fault=='redirect':s.status=302
    elif fault=='not-html':s.content_type='application/json'
    elif fault=='oversize':s.raw=b'x'*(price.MAX_BYTES+1)
    elif fault=='network':s.http_failure=True
    elif fault=='bad-table':s.raw=b'<html>No fixed pricing table</html>'
    elif fault=='operator':principal=Principal(s.p.user_id,s.p.workspace_id,'operator','worker')
    else:s.issuer_active=False
    with pytest.raises(HTTPException):price.authorize(s.db,principal,s.body)
    assert s.rows[Audit]==s.rows[Evidence]==[]


def test_normal_later_model_consent_does_not_change_original_current_or_historical_price(scene,monkeypatch):
    s=scene;registered=auth(s);file=s.rows[Evidence][0]
    before=copy.deepcopy(registered['price']);basis=copy.deepcopy(registered['basis_ref'])
    def actual_model(db,principal,identity,kind):
        assert db is s.db and principal==s.p and identity==s.model.id and kind=='foundation_model'
        return s.model  # Published registry/IAM is an explicit IO boundary.
    monkeypatch.setattr(price.evidence,'published',actual_model)
    # Run the real existing consent mutation and real independent audit writer,
    # while its scoped SQL and account/publication authority remain IO spies.
    actual=price.evidence.authorize_cloud(s.db,s.p,file.id,s.model.id,file.version,'明确授权此公开官网原件用于该模型')
    s.db.flush()
    assert actual is file and file.version==2 and file.data['cloud_model_ids']==[s.model.id]
    grants=[a for a in s.rows[Audit] if a.action=='evidence.cloud_authorize' and a.object_id==file.id]
    assert len(grants)==1 and grants[0].actor_id==s.p.user_id
    assert grants[0].data=={'model_id':s.model.id,'reason':'明确授权此公开官网原件用于该模型',
        'sha256':file.sha256,'version':2}
    assert price.current(s.db,s.p,s.body['model_pin'],basis_ref=basis)['price']==before
    s.time+=timedelta(hours=2)
    historical=price.checked_original(s.db,s.p,s.body['model_pin'],basis_ref=basis,priced_at=AT)
    assert historical['price']==before and historical['basis_ref']==basis and s.fetches==1
    assert all(historical[key] is False for key in price.money.FLAGS)


@pytest.mark.parametrize('fault',['rate-drift','raw-drift','upload-missing','price-marker','url-marker',
    'invalid-version','self-ref','actor-drift','future-fetch'])
def test_cold_current_requires_independent_upload_actual_bytes_and_exact_original_parse(scene,fault):
    s=scene;result=auth(s)
    event=[a for a in s.rows[Audit] if a.action==price.PRICE_BASIS][0];file=s.rows[Evidence][0]
    if fault=='rate-drift':event.data['parsed_rates']['input_cache_miss_minor_per_million']=1
    elif fault=='raw-drift':s.files[file.id]=s.raw+b' '
    elif fault=='upload-missing':s.rows[Audit]=[a for a in s.rows[Audit] if a.action!='evidence.upload']
    elif fault=='price-marker':file.data['price_schema']='other-price-family'
    elif fault=='url-marker':file.data['official_source_url']='https://example.invalid/pricing/'
    elif fault=='invalid-version':file.version=True
    elif fault=='self-ref':event.data['basis_ref']=result['basis_ref']
    elif fault=='actor-drift':event.actor_id=uid()
    else:event.data['http_receipt']['requested_at']=(AT+timedelta(seconds=1)).isoformat()
    with pytest.raises(HTTPException):price.current(s.db,s.p,s.body['model_pin'])
    assert s.fetches==1


@pytest.mark.parametrize('field',['url','rates','input_price','currency','python','actor_id'])
def test_authorize_body_cannot_replace_official_fetch_or_server_actor(scene,field):
    with pytest.raises(ValueError):price.Authorize.model_validate({**scene.body,field:'caller-supplied'})


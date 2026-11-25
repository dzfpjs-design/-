"""Disposable PG + loopback fake provider; never invokes real DeepSeek."""
import base64,copy,json,os,subprocess,sys,tempfile,time
from datetime import timedelta
from pathlib import Path
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Audit,AuthSession,CloudCall,Job
from generic_agents import gateway
from generic_agents import same_goal_advice_deepseek_adapter_candidate_v2 as subject
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as reconcile
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import setup,synthetic_source_expiry
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),reason='isolated fake provider only')


@pytest.fixture(autouse=True)
def private_response_spool(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='advice-v2-response-spool-') as path:
        monkeypatch.setattr(settings(),'v2_response_spool_enabled',True)
        monkeypatch.setattr(settings(),'v2_response_spool_root',Path(path))
        yield


def test_formal_switch_defaults_closed_without_database_or_credentials(monkeypatch):
    assert type(settings()).model_fields['v2_deepseek_dispatch_enabled'].default is False
    monkeypatch.setattr(settings(),'v2_deepseek_dispatch_enabled',False)
    with pytest.raises(ModelBlocked):subject._execute(None,'a','b','c','d','e',subject._PRODUCTION)
    def forbidden(*_args,**_kwargs):raise AssertionError('must not access DB or network')
    monkeypatch.setattr(subject,'_execute',forbidden)
    with pytest.raises(ModelBlocked):subject.execute(None,'a','b','c','d','e')


def test_gateway_policy_and_secret_reference_are_shared(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-only'))
    execution={'provider':'deepseek','secret_ref':'DEEPSEEK_API_KEY','model':'synthetic'}
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    endpoint,_=gateway._resolve_cloud_access(execution)
    assert endpoint=='https://api.deepseek.com/chat/completions'
    monkeypatch.setattr(settings(),'cloud_allowed_providers','qwen')
    with pytest.raises(ModelBlocked):gateway._resolve_cloud_access(execution)
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    with pytest.raises(ModelBlocked):gateway._resolve_cloud_access({**execution,'secret_ref':'OTHER'})


def test_loopback_success_capture_settlement_and_restart_without_resend(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with Session(engine()) as db:original_day=db.get(CloudCall,args[-1]).data['day']
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        outcome=subject.execute_synthetic_test_only(*args)
        assert outcome['state']=='settled' and outcome['answer_state']=='succeeded'
        assert outcome['retry_authorized'] is False and len(server.requests)==1
        p,_,_,_,_,call_id=args;intent_id=outcome['intent_audit_id'];engine().dispose()
        assert reconcile.reconcile(p.workspace_id,call_id,intent_id)['state']=='settled'
        with Session(engine()) as db:
            value=reconcile.read_result(db,p,call_id,intent_id)
            raw,receipt,_=storage.read(db,p.workspace_id,call_id,intent_id)
            assert value['model_value']==opinion and value['business_result_verified'] is False
            decoded=[(base64.b64decode(k).lower(),base64.b64decode(v)) for k,v in receipt['header_raw_base64']]
            assert all(k!=b'set-cookie' and b'synthetic-cookie' not in v for k,v in decoded)
            assert db.get(CloudCall,call_id).charged_tokens==5
            daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key=='cloud-budget:'+original_day))
            assert daily.data['day']==original_day and daily.data['tokens']==5 and daily.data['calls']==1
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        assert len(server.requests)==1


def test_loopback_gzip_is_captured_and_settled(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='gzip',content=json.dumps(opinion),usage={'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}) as server:
        result=subject.execute_synthetic_test_only(*args)
        assert result['answer_state']=='succeeded' and len(server.requests)==1
        with Session(engine()) as db:
            raw,receipt,_=storage.read(db,args[0].workspace_id,args[-1],result['intent_audit_id'])
            decoded=[(base64.b64decode(k).lower(),base64.b64decode(v)) for k,v in receipt['header_raw_base64']]
            assert raw.startswith(b'\x1f\x8b') and all(k!=b'set-cookie' and b'synthetic-cookie' not in v for k,v in decoded)
            assert db.get(CloudCall,args[-1]).charged_tokens==5


def test_loopback_connection_loss_stays_unknown_no_retry(started):
    _,args,_=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='drop') as server:
        result=subject.execute_synthetic_test_only(*args)
        assert result['state']=='unknown' and result['retry_authorized'] is False and len(server.requests)==1
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        with Session(engine()) as db:
            row=db.get(CloudCall,args[-1]);assert row.status=='reserved' and row.charged_tokens==row.reserved_tokens
        assert len(server.requests)==1


def test_loopback_slow_stream_is_bounded_and_not_retried(started,monkeypatch):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    monkeypatch.setattr(settings(),'v2_deepseek_total_timeout_seconds',1)
    with provider(port,mode='slow',content=json.dumps(opinion)) as server:
        result=subject.execute_synthetic_test_only(*args)
        assert result['state']=='unknown' and result['retry_authorized'] is False and len(server.requests)==1
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        assert len(server.requests)==1


@pytest.mark.parametrize("point,exit_code",[("after_capture",76),("after_spool",77),("mid_stream",78)])
def test_process_crash_after_capture_recovers_without_second_post(started,prepared_preview,tmp_path,point,exit_code):
    from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intent
    s,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);p=args[0]
    resolved=document.sources.resolve_documents(None,None,None,None)
    resolved['content']=base64.b64encode(resolved['content']).decode('ascii')
    case={'principal':{'user_id':p.user_id,'workspace_id':p.workspace_id,'role':p.role,'session_id':p.session_id},
        'arguments':list(args[1:]),'frame':copy.deepcopy(prepared_preview[3]),'resolved':resolved}
    file=tmp_path/'synthetic-crash.json';file.write_text(json.dumps(case));file.chmod(0o600)
    env=os.environ.copy();env['GA_SYNTHETIC_CRASH_CASE']=str(file);env['GA_SYNTHETIC_CRASH_POINT']=point;env['GA_STORAGE_DIR']=str(settings().storage_dir);env['GA_V2_RESPONSE_SPOOL_ENABLED']='true';env['GA_V2_RESPONSE_SPOOL_ROOT']=str(settings().v2_response_spool_root)
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        child=subprocess.run([sys.executable,'-m','tests.synthetic_deepseek_adapter_crash_child_v2'],env=env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)
        assert child.returncode==exit_code and len(server.requests)==1
        engine().dispose()
        with Session(engine()) as db:
            original=db.scalar(select(Audit).where(Audit.action==intent.ACTION,Audit.object_id==args[2]))
            assert original is not None;intent_id=original.id
        from generic_agents import same_goal_advice_spool_recovery_candidate_v2 as recovery
        from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
        from generic_agents import same_goal_advice_response_spool_test_v2 as spool
        with Session(engine()) as db:
            permit=db.scalar(select(Audit).where(Audit.action==permits.ACTION,Audit.object_id==args[2]));permit_id=permit.id
        if point=='mid_stream':
            with pytest.raises(spool.SpoolError):recovery.recover(p.workspace_id,args[-1],intent_id,permit_id)
            with Session(engine()) as db:assert db.get(CloudCall,args[-1]).status=='reserved'
            with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
            assert len(server.requests)==1
            return
        result=recovery.recover(p.workspace_id,args[-1],intent_id,permit_id)
        assert result['state']=='settled' and result['answer_state']=='succeeded'
        with Session(engine()) as db:assert db.get(CloudCall,args[-1]).charged_tokens==5
        assert len(server.requests)==1
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        assert len(server.requests)==1


def test_loopback_response_size_limit_preserves_unknown_no_retry(started,monkeypatch):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    monkeypatch.setattr(subject,'MAX_WIRE_BYTES',100)
    with provider(port,content=json.dumps(opinion)) as server:
        result=subject.execute_synthetic_test_only(*args)
        assert result['state']=='unknown' and result['retry_authorized'] is False and len(server.requests)==1
        with Session(engine()) as db:
            assert db.get(CloudCall,args[-1]).status=='reserved'
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        assert len(server.requests)==1


def test_official_mode_uses_fixed_gateway_policy_with_inprocess_transport(started,monkeypatch):
    """MockTransport is in-process: no DNS or external socket is contacted."""
    import httpx
    _,args,opinion=setup(started);seen=[]
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-only'))
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    monkeypatch.setattr(settings(),'v2_deepseek_dispatch_enabled',True)
    real_client=httpx.AsyncClient
    def handle(request):
        seen.append((str(request.url),request.headers.get('authorization')))
        body=json.dumps({
            'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(opinion)}}],
            'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}).encode()
        return httpx.Response(200,headers={'Set-Cookie':'synthetic-cookie=must-not-be-recorded'},
            stream=httpx.ByteStream(body))
    def local_client(**kwargs):return real_client(transport=httpx.MockTransport(handle),**kwargs)
    monkeypatch.setattr(subject.httpx,'AsyncClient',local_client)
    result=subject.execute(*args)
    assert result['state']=='settled' and result['answer_state']=='succeeded'
    assert seen==[('https://api.deepseek.com/chat/completions','Bearer synthetic-only')]
    with Session(engine()) as db:
        raw,receipt,_=storage.read(db,args[0].workspace_id,args[-1],result['intent_audit_id'])
        decoded=[(base64.b64decode(k).lower(),base64.b64decode(v)) for k,v in receipt['header_raw_base64']]
        assert all(k!=b'set-cookie' and b'synthetic-cookie' not in v for k,v in decoded)
        assert db.get(CloudCall,args[-1]).charged_tokens==5
    with pytest.raises((HTTPException,ModelBlocked)):subject.execute(*args)
    assert len(seen)==1


def test_malformed_business_answer_still_settles_proven_usage(started):
    _,args,_=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content='not-json',usage={'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}) as server:
        result=subject.execute_synthetic_test_only(*args)
        assert result['state']=='settled' and result['answer_state']=='failed'
        assert result['usage_unavailable'] is False and len(server.requests)==1
        with Session(engine()) as db:
            row=db.get(CloudCall,args[-1]);assert row.status=='failed' and row.charged_tokens==5
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        assert len(server.requests)==1


def test_no_table_or_session_lock_held_during_http(started):
    _,args,opinion=setup(started);p=args[0];port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);observed=[]
    def revoke_after_request():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout='500ms'"))
            db.execute(text('LOCK TABLE public.cloud_model_calls, public.audit_events IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
            current=db.get(AuthSession,p.session_id);current.revoked=True;db.commit();observed.append('committed')
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5},
                  on_received=revoke_after_request) as server:
        result=subject.execute_synthetic_test_only(*args)
        assert observed==['committed'] and result['state']=='settled' and len(server.requests)==1
        with Session(engine()) as db:
            with pytest.raises(HTTPException):reconcile.read_result(db,p,args[-1],result['intent_audit_id'])
            assert db.get(CloudCall,args[-1]).charged_tokens==5


def _mock_formal_client(monkeypatch,opinion,seen):
    import httpx
    real_client=httpx.AsyncClient
    def handle(request):
        seen.append((str(request.url),request.headers.get('authorization')))
        body=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(opinion)}}],
            'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}).encode()
        return httpx.Response(200,stream=httpx.ByteStream(body))
    monkeypatch.setattr(subject.httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw))


def test_credential_rotation_after_permit_uses_current_secret(started,monkeypatch):
    from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
    _,args,opinion=setup(started);seen=[];calls=[]
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-old'))
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    monkeypatch.setattr(settings(),'v2_deepseek_dispatch_enabled',True)
    original=gateway._resolve_cloud_access
    def rotate(execution):
        calls.append(1)
        if len(calls)==3:
            with Session(engine()) as db:
                assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==permits.ACTION))==1
            monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-new'))
        return original(execution)
    monkeypatch.setattr(gateway,'_resolve_cloud_access',rotate)
    _mock_formal_client(monkeypatch,opinion,seen)
    result=subject.execute(*args)
    assert result['state']=='settled' and seen==[('https://api.deepseek.com/chat/completions','Bearer synthetic-new')]


def test_provider_disable_after_permit_prevents_send_and_keeps_unknown(started,monkeypatch):
    from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
    from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
    _,args,opinion=setup(started);seen=[];calls=[]
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-old'))
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    monkeypatch.setattr(settings(),'v2_deepseek_dispatch_enabled',True)
    original=gateway._resolve_cloud_access
    def disable(execution):
        calls.append(1)
        if len(calls)==3:
            with Session(engine()) as db:
                assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==permits.ACTION))==1
            monkeypatch.setattr(settings(),'cloud_allowed_providers','qwen')
        return original(execution)
    monkeypatch.setattr(gateway,'_resolve_cloud_access',disable)
    _mock_formal_client(monkeypatch,opinion,seen)
    result=subject.execute(*args)
    assert result['state']=='unknown' and result['retry_authorized'] is False
    assert seen==[]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==permits.ACTION))==1
        row=db.get(CloudCall,args[-1]);assert row.status=='reserved' and row.charged_tokens==row.reserved_tokens
        intent_id=db.scalar(select(Audit.id).where(Audit.action==intents.ACTION))
    with pytest.raises(HTTPException):reconcile.reconcile(args[0].workspace_id,args[-1],intent_id)
    assert seen==[]


def test_slow_first_byte_over_three_seconds_succeeds(started):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='slow_first_byte',content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        before=time.monotonic();result=subject.execute_synthetic_test_only(*args)
        assert time.monotonic()-before>=3 and result['state']=='settled' and len(server.requests)==1


def test_database_failure_after_complete_spool_recovers_without_resend(started,monkeypatch):
    from generic_agents import same_goal_advice_spool_recovery_candidate_v2 as recovery
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        with monkeypatch.context() as failure:
            def unavailable(*_args):raise RuntimeError('synthetic database unavailable')
            failure.setattr(storage,'capture',unavailable)
            result=subject.execute_synthetic_test_only(*args)
        assert result['state']=='capture_pending' and result['retry_authorized'] is False and len(server.requests)==1
        engine().dispose()
        recovered=recovery.recover(args[0].workspace_id,args[-1],result['intent_audit_id'],result['permit_audit_id'])
        assert recovered['state']=='settled'
        assert recovery.recover(args[0].workspace_id,args[-1],result['intent_audit_id'],result['permit_audit_id'])['state']=='settled'
        with Session(engine()) as db:assert db.get(CloudCall,args[-1]).charged_tokens==5
        with pytest.raises((HTTPException,ModelBlocked)):subject.execute_synthetic_test_only(*args)
        assert len(server.requests)==1


def test_full_spool_refuses_before_intent_permit_or_http(started):
    from uuid import uuid4
    from generic_agents import same_goal_advice_response_spool_test_v2 as spool
    from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
    from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
    _,args,_=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    for _ in range(spool.MAX_SLOTS):
        with spool.allocate(settings().v2_response_spool_root,str(uuid4())):pass
    with provider(port) as server:
        with pytest.raises(ModelBlocked):subject.execute_synthetic_test_only(*args)
        assert not server.requests
    with Session(engine()) as db:
        assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action.in_([intents.ACTION,permits.ACTION])))


def test_spool_creation_failure_refuses_before_intent_permit_or_http(started,monkeypatch):
    from generic_agents import same_goal_advice_response_spool_test_v2 as spool
    from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
    from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
    _,args,_=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    def fail(*_args):raise OSError('synthetic disk failure')
    monkeypatch.setattr(spool,'_new',fail)
    with provider(port) as server:
        with pytest.raises(ModelBlocked):subject.execute_synthetic_test_only(*args)
        assert not server.requests
    with Session(engine()) as db:
        assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action.in_([intents.ACTION,permits.ACTION])))

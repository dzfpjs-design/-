"""One synthetic loopback POST against real isolated v2 accounting records.

Original Goal/approval/frame are fixture substitutes. This is not DeepSeek or
an application route and cannot touch a real workspace or provider.
"""
import json,os
import pytest
from fastapi import HTTPException
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit,CloudCall,Job,Plan
from generic_agents import cloud_budget
from generic_agents import same_goal_advice_contracts_v302 as old
from generic_agents import same_goal_advice_unlimited_contracts_v2 as contracts
from generic_agents import same_goal_advice_first_response_candidate_v2 as response
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reserve
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as subject
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permits
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_runtime_v308 import _opinion
from tests.test_advice_v2_cloud_reservation_candidate_pg import started,_reserve
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),
    reason='requires explicitly allocated isolated loopback provider port')


@pytest.fixture(autouse=True)
def synthetic_source_expiry(monkeypatch):
    from datetime import timedelta
    # The fixture substitutes Goal/evaluation originals; this is not a real
    # evaluation expiration or a proof of approval provenance.
    monkeypatch.setattr(permits,'_source_expiry',lambda db,*_:db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5))


def setup(started):
    s,created,owned=started;p=s['principal']
    with Session(engine()) as db:
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db);db.commit()
    with Session(engine()) as db:allocated=_reserve(db,started);db.commit()
    with Session(engine()) as db:
        shown=db.scalar(select(Audit).where(Audit.action==preview.ACTION))
        opinion=_opinion(shown.data['preview_record']['cloud_request']['decision_input'])
    args=(p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],allocated['cloud_call_id'])
    return s,args,opinion


def assert_single_unknown(args,server,expected_requests=1):
    p,task_id,plan_id,job_id,token,call_id=args
    with Session(engine()) as db:
        events=list(db.scalars(select(Audit).where(Audit.action==intent.ACTION)))
        assert len(events)==1
        original=storage.read(db,p.workspace_id,call_id,events[0].id)
        assert original=={'state':'unknown','retry_authorized':False}
        call=db.get(CloudCall,call_id);assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
    with pytest.raises(HTTPException):subject.execute_once(*args)
    assert len(server.requests)==expected_requests


def test_one_real_loopback_response_is_preserved_settled_and_not_repeated(started):
    s,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        result=subject.execute_once(*args)
        assert result['state']=='captured' and result['retry_authorized'] is False and len(server.requests)==1
        engine().dispose()
        p,task_id,plan_id,_,_,call_id=args;intent_id=result['intent_audit_id']
        with Session(engine()) as db:
            raw,receipt,sealed=storage.read(db,p.workspace_id,call_id,intent_id)
            value,proven=response.parse_saved(sealed,cloud_call_id=call_id,intent_audit_id=intent_id,
                schema=old.model_value_schema())
            initial=db.scalar(select(Audit).where(Audit.action==start.ACTION,Audit.object_id==plan_id))
            prep=db.get(Plan,plan_id).data['preparation_snapshot']
            from generic_agents.models import Capability
            advice=db.get(Capability,prep['advice_capability_ref']['id'])
            recommendation=contracts.derive_result(initial.data['snapshot'],value,preparation=prep,manifest=advice.data)
            assert recommendation['review_state']=='ready_for_review' and proven['usage']['total_tokens']==5
            call=db.get(CloudCall,call_id);anchor=call.data[reserve.KEY]
            expected={'workspace_id':p.workspace_id,'capability_id':call.capability_id,'actor_id':p.user_id,
                'request_hash':anchor['request_hash'],'dispatch_options_hash':anchor['dispatch_options_hash'],
                'provider':'deepseek','model':receipt['model']}
            cloud_budget.settle_paid_response(db,call_id,proven,expected,'succeeded');db.commit()
        engine().dispose()
        with Session(engine()) as db:
            assert storage.read(db,p.workspace_id,call_id,intent_id)[0]==raw
            assert db.get(CloudCall,call_id).charged_tokens==5
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert ledger.data['tokens']==5 and ledger.data['calls']==1
        with pytest.raises(HTTPException):subject.execute_once(*args)
        assert len(server.requests)==1


def test_response_connection_loss_is_unknown_and_not_repeated(started):
    _,args,_=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='drop') as server:
        result=subject.execute_once(*args)
        assert result['state']=='unknown' and result['retry_authorized'] is False
        assert_single_unknown(args,server)


def test_capture_failure_after_http_is_unknown_and_not_repeated(started,monkeypatch):
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion)) as server:
        with monkeypatch.context() as m:
            m.setattr(storage,'capture',lambda *_:(_ for _ in ()).throw(RuntimeError('synthetic capture failure')))
            with pytest.raises(RuntimeError):subject.execute_once(*args)
        assert_single_unknown(args,server)


def test_http_error_is_captured_and_unknown_usage_is_not_zero(started):
    from generic_agents.gateway import ModelBlocked
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='http_error',content=json.dumps(opinion)) as server:
        result=subject.execute_once(*args)
        assert result['state']=='captured' and len(server.requests)==1
        p,_,_,_,_,call_id=args;intent_id=result['intent_audit_id']
        with Session(engine()) as db:
            raw,receipt,sealed=storage.read(db,p.workspace_id,call_id,intent_id)
            assert receipt['http_status']==429
            with pytest.raises(ModelBlocked):response.parse_saved(sealed,cloud_call_id=call_id,intent_audit_id=intent_id,
                schema=old.model_value_schema())
            call=db.get(CloudCall,call_id);anchor=call.data[reserve.KEY]
            expected={'workspace_id':p.workspace_id,'capability_id':call.capability_id,'actor_id':p.user_id,
                'request_hash':anchor['request_hash'],'dispatch_options_hash':anchor['dispatch_options_hash'],
                'provider':'deepseek','model':receipt['model']}
            bound=call.reserved_tokens
            cloud_budget.settle_paid_response(db,call_id,receipt,expected,'failed');db.commit()
        with Session(engine()) as db:
            call=db.get(CloudCall,call_id);assert call.charged_tokens==bound and call.data['usage_unavailable'] is True
            assert storage.read(db,p.workspace_id,call_id,intent_id)[0]==raw
        with pytest.raises(HTTPException):subject.execute_once(*args)
        assert len(server.requests)==1


def test_response_is_preserved_after_current_actor_revocation(started,monkeypatch):
    from generic_agents.models import AuthSession
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);p=args[0]
    original=storage.capture
    def revoke_then_capture(db,*values):
        with Session(engine()) as admin_db:
            session=admin_db.get(AuthSession,p.session_id);session.revoked=True;admin_db.commit()
        return original(db,*values)
    with provider(port,content=json.dumps(opinion)) as server:
        with monkeypatch.context() as m:
            m.setattr(storage,'capture',revoke_then_capture)
            result=subject.execute_once(*args)
        assert result['state']=='captured' and len(server.requests)==1
        with Session(engine()) as db:
            assert isinstance(storage.read(db,p.workspace_id,args[-1],result['intent_audit_id']),tuple)


@pytest.mark.parametrize('boundary,code,request_count',[('before_http',74,0),('before_capture',73,1)])
def test_actual_subprocess_crash_never_repeats(started,prepared_preview,tmp_path,boundary,code,request_count):
    import base64,copy,subprocess,sys
    from generic_agents.config import settings
    from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
    s,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);p=args[0]
    resolved=document.sources.resolve_documents(None,None,None,None)
    resolved['content']=base64.b64encode(resolved['content']).decode('ascii')
    case={'principal':{'user_id':p.user_id,'workspace_id':p.workspace_id,'role':p.role,'session_id':p.session_id},
        'arguments':list(args[1:]),'frame':copy.deepcopy(prepared_preview[3]),'resolved':resolved}
    file=tmp_path/'synthetic-crash-case.json';file.write_text(json.dumps(case));file.chmod(0o600)
    env=os.environ.copy();env['GA_SYNTHETIC_CRASH_CASE']=str(file);env['GA_STORAGE_DIR']=str(settings().storage_dir)
    env['GA_SYNTHETIC_CRASH_BOUNDARY']=boundary
    with provider(port,content=json.dumps(opinion)) as server:
        child=subprocess.run([sys.executable,'-m','tests.synthetic_deepseek_crash_child_v2'],env=env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)
        assert child.returncode==code,'synthetic subprocess did not reach the requested crash boundary'
        assert_single_unknown(args,server,expected_requests=request_count)


def test_gzip_wire_body_preserved_and_decoded_without_sensitive_headers(started):
    import gzip
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);p=args[0]
    with provider(port,mode='gzip',content=json.dumps(opinion),
        usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        result=subject.execute_once(*args)
        assert result['state']=='captured' and len(server.requests)==1
        with Session(engine()) as db:
            raw,receipt,sealed=storage.read(db,p.workspace_id,args[-1],result['intent_audit_id'])
            assert raw.startswith(b'\x1f\x8b') and json.loads(gzip.decompress(raw))['usage']['total_tokens']==5
            assert 'synthetic-cookie' not in json.dumps(receipt)
            value,proven=response.parse_saved(sealed,cloud_call_id=args[-1],intent_audit_id=result['intent_audit_id'],
                schema=old.model_value_schema())
            assert value==opinion and proven['usage']['total_tokens']==5
            audit=db.scalar(select(Audit).where(Audit.action==storage.ACTION))
            assert 'synthetic-cookie' not in json.dumps(audit.data)


def test_slow_stream_has_total_bound_and_never_retries(started,monkeypatch):
    import time
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    monkeypatch.setattr(subject,'MAX_STREAM_SECONDS',.25)
    with provider(port,mode='slow',content=json.dumps(opinion)) as server:
        before=time.monotonic();result=subject.execute_once(*args);duration=time.monotonic()-before
        assert result['state']=='unknown' and duration<5
        assert_single_unknown(args,server)


def test_revocation_during_http_does_not_wait_on_database_locks(started):
    from generic_agents.models import AuthSession
    _,args,opinion=setup(started);port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']);p=args[0]
    observed=[]
    def revoke():
        import time
        before=time.monotonic()
        with Session(engine()) as db:
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            db.get(AuthSession,p.session_id).revoked=True;db.commit()
        observed.append(time.monotonic()-before)
    with provider(port,content=json.dumps(opinion),on_received=revoke) as server:
        result=subject.execute_once(*args)
        assert result['state']=='captured' and len(server.requests)==1 and observed and observed[0]<2
        with Session(engine()) as db:
            assert isinstance(storage.read(db,p.workspace_id,args[-1],result['intent_audit_id']),tuple)


def _start_intent(started):
    s,args,opinion=setup(started);p,task_id,plan_id,job_id,token,call_id=args
    with Session(engine()) as db:
        made=intent.issue(db,p,task_id,plan_id,job_id,token,call_id);db.commit()
    return s,args,made['intent_audit_id'],opinion


def test_revocation_first_blocks_dispatch_permit(started):
    from generic_agents.models import AuthSession
    s,args,intent_id,_=_start_intent(started);p,task_id,plan_id,job_id,token,call_id=args
    with Session(engine()) as db:db.get(AuthSession,p.session_id).revoked=True;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):permits.issue(db,p,task_id,plan_id,job_id,token,call_id,intent_id)
        db.rollback()
        assert not list(db.scalars(select(Audit).where(Audit.action==permits.ACTION)))


def test_permit_commits_before_waiting_revocation(started):
    from queue import Queue
    from threading import Thread
    from time import monotonic,sleep
    from generic_agents.models import AuthSession
    s,args,intent_id,_=_start_intent(started);p,task_id,plan_id,job_id,token,call_id=args
    pidq=Queue();errors=[]
    def revoke():
        try:
            with Session(engine()) as db:
                pidq.put(db.scalar(select(func.pg_backend_pid())))
                db.get(AuthSession,p.session_id).revoked=True;db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        owner=first.scalar(select(func.pg_backend_pid()))
        issued=permits.issue(first,p,task_id,plan_id,job_id,token,call_id,intent_id)
        thread=Thread(target=revoke,daemon=True);thread.start();pid=pidq.get(timeout=5)
        try:
            until=monotonic()+5;blocked=False
            while monotonic()<until:
                with engine().connect() as obs:blocked=owner in obs.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    with Session(engine()) as db:
        saved=db.get(Audit,issued['permit_audit_id'])
        assert saved.data['irrevocable_after_commit'] is True and saved.data['intent_ref']['id']==intent_id
        assert db.get(AuthSession,p.session_id).revoked is True
        assert len(list(db.scalars(select(Audit).where(Audit.action==permits.ACTION))))==1


def test_expired_permit_before_commit_is_not_persisted(started,monkeypatch):
    import time
    from generic_agents.temporal import instant
    monkeypatch.setattr(permits,'MAX_SECONDS',1)
    _,args,intent_id,_=_start_intent(started);p,task_id,plan_id,job_id,token,call_id=args
    with Session(engine()) as db:
        issued=permits.issue(db,p,task_id,plan_id,job_id,token,call_id,intent_id)
        delay=(instant(issued['send_before'])-db.scalar(select(func.clock_timestamp()))).total_seconds()
        time.sleep(max(0,delay)+.1)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==permits.ACTION)))
        assert db.get(CloudCall,call_id).status=='reserved'

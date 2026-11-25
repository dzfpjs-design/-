"""Default-off DeepSeek transport candidate; isolated PG only, no route.

Both modes use the same one-shot/capture/reconcile path. Production mode uses
only the existing provider allowlist, credential resolver and fixed endpoint;
the test mode accepts only a numeric loopback port in an external sandbox.
The existing v2 permit/custody still reject non-disposable databases, so this
candidate cannot be enabled as a production deployment by setting a flag.
"""
import copy
import asyncio
import os
import re
import time
import httpx
from sqlalchemy.orm import Session
from . import cloud_budget_policy as policy
from . import cloud_business_reasoning_v197 as wire
from . import gateway
from . import same_goal_advice_dispatch_permit_candidate_v2 as permits
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_loopback_dispatch_candidate_v2 as loopback
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_response_reconcile_candidate_v2 as reconcile
from . import same_goal_advice_response_spool_test_v2 as spool
from . import same_goal_advice_spool_recovery_candidate_v2 as spool_recovery
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_transport_intent_candidate_v2 as intents
from . import same_goal_advice_unlimited_store_v2 as custody
from .config import settings
from .db import engine
from .models import Capability,Plan,Task


_PRODUCTION=object()
_SYNTHETIC=object()
MAX_WIRE_BYTES=16*1024*1024


def _production_access(db,p,task_id,plan_id):
    """Use the original gateway's exact provider, endpoint and secret policy."""
    guard.require_disposable_database(db)
    task=store.existing._task(db,p,task_id,lock=True)
    plan=store._fresh(db,p,Plan,plan_id)
    store.need(plan.task_id==task.id)
    prep,_,_reservation=custody.read_reserved_test_only(db,p,task,plan)
    cap=store._fresh(db,p,Capability,prep['model_pin']['id'])
    store.need(policy._provider(db,p,cap,lock=True)=='deepseek'
        and cap.content_hash==prep['model_pin']['content_hash'])
    execution=copy.deepcopy(cap.data['execution'])
    store.need(execution.get('provider')=='deepseek' and execution.get('secret_ref')=='DEEPSEEK_API_KEY')
    endpoint,key=gateway._resolve_cloud_access(execution)
    store.need(endpoint==wire.ENDPOINTS['deepseek'])
    return endpoint,key,execution


def _test_endpoint():
    value=os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT','')
    store.need(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,4}',value) is not None
        and 1<=int(value)<=65535)
    return 'http://127.0.0.1:'+value+'/chat/completions'


def execute(p,task_id,plan_id,job_id,lease_token,call_id):
    """Provider candidate: disabled by default and still disposable-DB-only."""
    if settings().v2_deepseek_dispatch_enabled is not True:
        raise gateway.ModelBlocked('V2 DeepSeek 派发尚未启用')
    return _execute(p,task_id,plan_id,job_id,lease_token,call_id,_PRODUCTION)


def execute_synthetic_test_only(p,task_id,plan_id,job_id,lease_token,call_id):
    """Use only a test server on 127.0.0.1, never a provider credential."""
    return _execute(p,task_id,plan_id,job_id,lease_token,call_id,_SYNTHETIC)


def _execute(p,task_id,plan_id,job_id,lease_token,call_id,mode):
    store.need(mode is _PRODUCTION or mode is _SYNTHETIC)
    if mode is _PRODUCTION and settings().v2_deepseek_dispatch_enabled is not True:
        raise gateway.ModelBlocked('V2 DeepSeek 派发尚未启用')
    if settings().v2_response_spool_enabled is not True or settings().v2_response_spool_root is None:
        raise gateway.ModelBlocked('业务建议首答暂存未启用')
    try:spool.validate_root(settings().v2_response_spool_root)
    except (OSError,spool.SpoolError):raise gateway.ModelBlocked('业务建议首答暂存不可用') from None
    try:asyncio.get_running_loop()
    except RuntimeError:pass
    else:raise gateway.ModelBlocked('当前执行环境不支持同步派发；未创建发送申请')
    if mode is _PRODUCTION:
        # Preflight credentials before creating an irreversible intent.
        with Session(engine()) as db:
            _endpoint,_key,execution=_production_access(db,p,task_id,plan_id)
            db.rollback()
    else:
        with Session(engine()) as db:guard.require_disposable_database(db)
        _test_endpoint()
        execution=None
    with Session(engine()) as db:
        guard.require_disposable_database(db)
        made=intents.issue(db,p,task_id,plan_id,job_id,lease_token,call_id)
        store.need(made['replayed'] is False and made['transport_authorized'] is False)
        db.commit()
    intent_id=made['intent_audit_id']
    with Session(engine()) as db:
        permission=permits.issue(db,p,task_id,plan_id,job_id,lease_token,call_id,intent_id)
        if mode is _PRODUCTION:
            endpoint,key,current_execution=_production_access_after_permit(db,p,plan_id)
            store.need(current_execution==execution and current_execution['model']==permission['model'])
        db.commit()
    if mode is _PRODUCTION:
        # The operator can disable the provider or rotate its credential while
        # the permit is being created; never use stale or arbitrary endpoints.
        if settings().v2_deepseek_dispatch_enabled is not True:
            return {'state':'unknown','retry_authorized':False,'intent_audit_id':intent_id}
        try:endpoint,key=gateway._resolve_cloud_access(execution)
        except gateway.ModelBlocked:
            return {'state':'unknown','retry_authorized':False,'intent_audit_id':intent_id}
        store.need(endpoint==wire.ENDPOINTS['deepseek'])
    else:
        endpoint,key=_test_endpoint(),'synthetic-loopback-only'
    prepared=permission['prepared'];model=permission['model'];send_by=permission['monotonic_send_before']
    try:
        context=spool_recovery.current_context(p.workspace_id,call_id,intent_id,permission['permit_audit_id'])
        with spool.prepare(settings().v2_response_spool_root,context) as sink:
            asyncio.run(_post_to_spool(endpoint,key,prepared,model,call_id,send_by,sink))
    except (httpx.HTTPError,httpx.StreamError,OSError,ValueError,TimeoutError,spool.SpoolError):
        return {'state':'unknown','retry_authorized':False,'intent_audit_id':intent_id}
    # Full authenticated spool exists before the first DB capture attempt.
    # A DB outage leaves it available for explicit, no-send recovery.
    try:result=spool_recovery.recover(p.workspace_id,call_id,intent_id,permission['permit_audit_id'])
    except Exception:
        return {'state':'capture_pending','retry_authorized':False,'intent_audit_id':intent_id,
            'permit_audit_id':permission['permit_audit_id']}
    return {**result,'intent_audit_id':intent_id}


async def _post_to_spool(endpoint,key,prepared,model,call_id,send_by,sink):
    connect=settings().v2_deepseek_connect_timeout_seconds
    read=settings().v2_deepseek_read_timeout_seconds
    total=settings().v2_deepseek_total_timeout_seconds
    store.need(all(type(x) in (int,float) and not isinstance(x,bool) for x in (connect,read,total))
        and 1<=connect<=60 and 1<=read<=600 and 1<=total<=1800)
    timeout=httpx.Timeout(connect=min(connect,total),read=min(read,total),write=min(30,total),pool=min(10,total))
    async with asyncio.timeout(total):
        async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=timeout) as client:
            if time.monotonic()>=send_by:raise ValueError('permit expired before send')
            async with client.stream('POST',endpoint,headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'},
                json={**prepared['options'],'messages':prepared['messages']}) as response:
                headers,request_id,encoding_unverified=loopback._safe_headers(response.headers.raw)
                receipt={'provider':'deepseek','model':model,'call_id':call_id,'request_id':request_id,
                    'http_status':response.status_code,'header_raw_base64':headers,
                    'encoding_unverified':encoding_unverified,'usage':{},'usage_unavailable':True,
                    'usage_unavailable_reason':'unparsed_original_http','finish_reason':None,
                    'response_choice_count':None,'stage':wire.STAGE}
                sink.begin(receipt);size=0
                async for chunk in response.aiter_raw():
                    size+=len(chunk)
                    if size>MAX_WIRE_BYTES:raise ValueError('response exceeds capture bound')
                    sink.append(chunk)
                sink.finish()


def _production_access_after_permit(db,p,plan_id):
    plan=store._fresh(db,p,Plan,plan_id)
    pin=plan.data['preparation_snapshot']['model_pin']
    cap=store._fresh(db,p,Capability,pin['id'])
    store.need(policy._provider(db,p,cap,lock=True)=='deepseek' and cap.content_hash==pin['content_hash'])
    execution=copy.deepcopy(cap.data['execution'])
    store.need(execution.get('provider')=='deepseek' and execution.get('secret_ref')=='DEEPSEEK_API_KEY')
    endpoint,key=gateway._resolve_cloud_access(execution)
    store.need(endpoint==wire.ENDPOINTS['deepseek'])
    return endpoint,key,execution

"""One-shot HTTP experiment, strictly limited to a synthetic loopback server.

Never registered with a route or worker and never uses a real provider key.
The persisted intent is an uncertainty fence, not a retry authorization. A
process crash after intent or after HTTP but before capture stays unknown.
"""
import os
import re
import base64
import time
import httpx
from sqlalchemy.orm import Session
from . import cloud_business_reasoning_v197 as wire
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_dispatch_permit_candidate_v2 as permits
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_transport_intent_candidate_v2 as intents
from .db import engine

MAX_STREAM_SECONDS=15.0
READ_TIMEOUT_SECONDS=3.0


def _safe_headers(headers):
    selected=[];unverified=False;request_id=None
    for name in (b'content-encoding',b'content-type',b'x-request-id'):
        matches=[(k,v) for k,v in headers if k.lower()==name]
        if len(matches)!=1:
            if name==b'content-encoding' and matches:unverified=True
            continue
        key,value=matches[0]
        if name==b'content-encoding':valid=re.fullmatch(rb'[A-Za-z0-9._-]{1,32}',value) is not None
        elif name==b'x-request-id':valid=re.fullmatch(rb'[A-Za-z0-9._:-]{1,128}',value) is not None
        else:valid=(len(value)<=128 and re.fullmatch(
            rb'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(?:;\s*charset=[A-Za-z0-9._-]+)?',value,re.I) is not None)
        if not valid:
            if name==b'content-encoding':unverified=True
            continue
        if name==b'x-request-id':request_id=value.decode('ascii')
        selected.append([base64.b64encode(key).decode('ascii'),base64.b64encode(value).decode('ascii')])
    return selected,request_id,unverified


def _endpoint():
    value=os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT','')
    old.need(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,4}',value) is not None
        and 1<=int(value)<=65535,'需要隔离的本机模拟服务端口')
    return 'http://127.0.0.1:'+value+'/chat/completions'


def execute_once(p,task_id,plan_id,job_id,lease_token,call_id):
    """Issue intent, POST once, and capture bytes. Unknown outcomes never retry.

    This function can only operate in a verified disposable database and send
    to the explicitly configured loopback port. A caller must have installed
    the test capture table; no external credentials or fallback are supported.
    """
    endpoint=_endpoint()
    with Session(engine()) as db:
        guard.require_disposable_database(db)
        # No intent exists yet. Its single durable creation is the only entry
        # to this same-process attempt; a later invocation cannot recreate it.
        made=intents.issue(db,p,task_id,plan_id,job_id,lease_token,call_id)
        old.need(made['replayed'] is False and made['transport_authorized'] is False)
        db.commit()
    intent_id=made['intent_audit_id'];raw=None;receipt=None
    # The permit commits the explicitly irreversible one-shot dispatch point.
    # If revocation commits first this transaction refuses; if permit commits
    # first, that one attempt may proceed before its short deadline. Product UI
    # and real revocation receipts do not yet expose this test-only semantics.
    with Session(engine()) as db:
        permission=permits.issue(db,p,task_id,plan_id,job_id,lease_token,call_id,intent_id)
        db.commit()
    model=permission['model'];prepared=permission['prepared']
    send_by=permission['monotonic_send_before']
    try:
        # This network operation holds no database transaction or row/table
        # locks. A dedicated external sandbox must restrict the test port.
        timeout=httpx.Timeout(READ_TIMEOUT_SECONDS)
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=timeout) as client:
            old.need(time.monotonic()<send_by,'原授权已超过发送检查时限；未请求模拟服务')
            until=time.monotonic()+MAX_STREAM_SECONDS
            with client.stream('POST',endpoint,headers={'Authorization':'Bearer synthetic-loopback-only',
                'Accept-Encoding':'identity'},
                json={**prepared['options'],'messages':prepared['messages']}) as response:
                raw_headers,request_id,encoding_unverified=_safe_headers(response.headers.raw)
                chunks=[];size=0
                for chunk in response.iter_raw():
                    size+=len(chunk)
                    if size>16*1024*1024 or time.monotonic()>until:
                        raise ValueError('synthetic response exceeds bounded capture')
                    chunks.append(chunk)
                if time.monotonic()>until:raise ValueError('synthetic response exceeds time bound')
                raw=b''.join(chunks)
                receipt={'provider':'deepseek','model':model,'call_id':call_id,
                    'request_id':request_id,'http_status':response.status_code,
                    'header_raw_base64':raw_headers,'encoding_unverified':encoding_unverified,
                    'usage':{},'usage_unavailable':True,'usage_unavailable_reason':'unparsed_original_http',
                    'finish_reason':None,'response_choice_count':None,'stage':wire.STAGE}
    except (httpx.HTTPError,OSError,ValueError):
        return {'intent_audit_id':intent_id,'cloud_call_id':call_id,'state':'unknown','retry_authorized':False}
    with Session(engine()) as db:
        storage.capture(db,p.workspace_id,call_id,intent_id,raw,receipt);db.commit()
    return {'intent_audit_id':intent_id,'cloud_call_id':call_id,'state':'captured','retry_authorized':False}

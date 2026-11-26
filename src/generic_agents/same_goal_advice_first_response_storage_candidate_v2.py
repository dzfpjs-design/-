"""Isolated-test-only first response custody; not a sender or production API.

The fixed original-intent verifier is deliberately unconnected and rejects all
operations outside tests. No result establishes that a network request occurred.
"""
import copy
import hashlib
import re
from uuid import UUID
from sqlalchemy import event, func, or_, select, text
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_first_response_candidate_v2 as response
from . import same_goal_advice_response_intent_gate_candidate_v2 as gate
from .contracts import digest
from .models import Audit, CloudCall, uid
from .temporal import instant

ACTION = 'same_goal_advice.first_response_captured_test_v2'
TABLE = 'ga_test_advice_v2_first_response'
FIELDS = {'schema', 'workspace_id', 'call_id', 'intent_id', 'actor_id', 'anchor',
          'raw_sha256', 'raw_size', 'receipt', 'audit_id', 'captured_at', 'record_hash'}
SCHEMA = 'same-goal-advice-first-response-custody-test-v2'


def _id(value):
    old.need(type(value) is str and str(UUID(value)) == value)
    return value


def install(db):
    """Install only in a designated disposable database, never migrate production."""
    guard.require_disposable_database(db)
    old.need(db.get_nested_transaction() is None)
    old.need(db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None,
        '测试首答表已存在，请在新的隔离测试库运行')
    db.execute(text('''CREATE TABLE public.ga_test_advice_v2_first_response (
      workspace_id varchar(36) NOT NULL REFERENCES public.workspaces(id),
      call_id varchar(36) NOT NULL REFERENCES public.cloud_model_calls(id),
      intent_id varchar(36) NOT NULL REFERENCES public.audit_events(id),
      audit_id varchar(36) NOT NULL REFERENCES public.audit_events(id),
      raw_bytes bytea NOT NULL,
      CONSTRAINT ga_test_response_pk PRIMARY KEY(workspace_id,call_id),
      CONSTRAINT ga_test_response_intent UNIQUE(intent_id),
      CONSTRAINT ga_test_response_audit UNIQUE(audit_id)
    )'''))


def _require_schema(db):
    # Hold a PostgreSQL table lock while validating the disposable experiment.
    db.execute(text('LOCK TABLE public.ga_test_advice_v2_first_response IN ACCESS SHARE MODE NOWAIT'))
    columns=db.execute(text("SELECT attname,format_type(atttypid,atttypmod),attnotnull FROM pg_attribute "
        "WHERE attrelid=to_regclass('public.ga_test_advice_v2_first_response') AND attnum>0 "
        "AND NOT attisdropped ORDER BY attnum")).all()
    old.need(columns==[(name,'character varying(36)',True) for name in
        ('workspace_id','call_id','intent_id','audit_id')]+[('raw_bytes','bytea',True)])
    defs=sorted(db.scalars(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid=to_regclass('public.ga_test_advice_v2_first_response')")))
    expected=sorted(['PRIMARY KEY (workspace_id, call_id)','UNIQUE (intent_id)','UNIQUE (audit_id)',
        'FOREIGN KEY (workspace_id) REFERENCES workspaces(id)',
        'FOREIGN KEY (call_id) REFERENCES cloud_model_calls(id)',
        'FOREIGN KEY (intent_id) REFERENCES audit_events(id)',
        'FOREIGN KEY (audit_id) REFERENCES audit_events(id)'])
    old.need(defs==expected,'测试首答表的约束不可核对')
    indices=db.execute(text("SELECT i.indisvalid,i.indisready,i.indislive FROM pg_index i "
        "JOIN pg_constraint c ON c.conindid=i.indexrelid WHERE "
        "c.conrelid=to_regclass('public.ga_test_advice_v2_first_response') AND c.contype IN ('p','u')")).all()
    old.need(len(indices)==3 and all(tuple(row)==(True,True,True) for row in indices))


def _original(db, workspace_id, call_id, intent_id):
    from . import same_goal_advice_cloud_claim_test_v2 as claims
    claims.require_test_database(db)
    _id(workspace_id); _id(call_id); _id(intent_id)
    # Fixed service module, never a caller-provided validator or principal.
    pin = gate.check_original(db, workspace_id, call_id, intent_id)
    old.need(type(pin) is dict and set(pin) == {'actor_id', 'anchor_hash', 'provider', 'model'}
        and type(pin['anchor_hash']) is str and re.fullmatch(r'[0-9a-f]{64}', pin['anchor_hash']) is not None)
    _id(pin['actor_id'])
    old.need(pin['provider']=='deepseek' and type(pin['model']) is str and bool(pin['model']))
    return pin


def _read(db, workspace_id, call_id, intent_id, pin):
    row=db.execute(text('SELECT intent_id,audit_id,raw_bytes FROM public.ga_test_advice_v2_first_response '
        'WHERE workspace_id=:w AND call_id=:c'), {'w':workspace_id,'c':call_id}).one_or_none()
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id, or_(
        (Audit.action==ACTION) & (Audit.object_id==call_id),
        (Audit.data['schema'].as_string()==SCHEMA) & (Audit.data['call_id'].as_string()==call_id)
        )).execution_options(populate_existing=True)))
    if row is None:
        old.need(not audits, '原首答回执存在但内容缺失；不得重发')
        return None
    old.need(row.intent_id==intent_id and len(audits)==1 and audits[0].id==row.audit_id
        and audits[0].action==ACTION and audits[0].object_id==call_id,
        '原首答或回执不完整；不得重发')
    audit=audits[0]; value=audit.data; raw=bytes(row.raw_bytes)
    old.need(type(value) is dict and set(value)==FIELDS and value['schema']==SCHEMA
        and audit.actor_id==value['actor_id']==pin['actor_id'] and audit.version==1
        and audit.created_at==instant(value['captured_at'])
        and value['workspace_id']==workspace_id and value['call_id']==call_id
        and value['intent_id']==intent_id and value['audit_id']==audit.id
        and value['anchor']==pin['anchor_hash'] and value['receipt'].get('provider')==pin['provider']
        and value['receipt'].get('model')==pin['model'] and value['raw_size']==len(raw)
        and value['raw_sha256']==hashlib.sha256(raw).hexdigest()
        and value['record_hash']==digest({k:v for k,v in value.items() if k!='record_hash'}),
        '原首答、来源或回执不一致；不得重发')
    sealed=response.seal(cloud_call_id=call_id,intent_audit_id=intent_id,raw=raw,receipt=value['receipt'])
    return raw, copy.deepcopy(value['receipt']), sealed


@old.checked
def check_no_local_response_for_permit(db, workspace_id, call_id, intent_id):
    """Check local custody only; this never proves unsent or grants dispatch.

    The test-only permit caller must first authenticate and lock its fixed
    original intent/reservation. Hold the same table locks as capture until
    that caller commits, so a concurrent response cannot appear in between.
    """
    guard.require_disposable_database(db)
    old.need(db.get_nested_transaction() is None)
    _id(workspace_id); _id(call_id); _id(intent_id)
    db.execute(text('LOCK TABLE public.ga_test_advice_v2_first_response, public.audit_events '
        'IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    _require_schema(db)
    row=db.execute(text('SELECT 1 FROM public.ga_test_advice_v2_first_response '
        'WHERE (workspace_id=:w AND call_id=:c) OR intent_id=:i LIMIT 1'),
        {'w':workspace_id,'c':call_id,'i':intent_id}).first()
    audits=list(db.scalars(select(Audit.id).where(Audit.workspace_id==workspace_id,or_(
        (Audit.action==ACTION) & (Audit.object_id==call_id),
        (Audit.data['schema'].as_string()==SCHEMA) & or_(
            Audit.data['call_id'].as_string()==call_id,Audit.data['intent_id'].as_string()==intent_id)
        )).limit(1)))
    old.need(row is None and not audits, '已有首答或不完整回执；不得创建派发许可')


@old.checked
def read(db, workspace_id, call_id, intent_id):
    """Internal historical read: gate must authenticate saved originals offline."""
    pin=_original(db,workspace_id,call_id,intent_id)
    _require_schema(db)
    value = _read(db,workspace_id,call_id,intent_id,pin)
    return value if value is not None else {'state': 'unknown', 'retry_authorized': False}


@old.checked
def capture(db, workspace_id, call_id, intent_id, raw, receipt):
    """Store exact bytes atomically; no send, retry, settlement or cloud call."""
    old.need(db.get_nested_transaction() is None, '测试首答写入不支持调用方保存点')
    pin=_original(db,workspace_id,call_id,intent_id)
    sealed=response.seal(cloud_call_id=call_id,intent_audit_id=intent_id,raw=raw,receipt=receipt)
    old.need(receipt.get('provider')==pin['provider'] and receipt.get('model')==pin['model'])
    # Serialize even the absence check; a separate unique constraint is a fence
    # against cooperating concurrent writers. Requires a dedicated test DB.
    db.execute(text('LOCK TABLE public.ga_test_advice_v2_first_response, public.audit_events '
        'IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    _require_schema(db)
    existing=_read(db,workspace_id,call_id,intent_id,pin)
    if existing is not None:
        old.need(existing[2]==sealed, '首答与原内容不一致；不得覆盖或重发')
        return existing
    audit_id=uid(); captured=db.scalar(select(func.clock_timestamp()))
    old.need(captured is not None and captured.tzinfo is not None)
    value={'schema':SCHEMA,'workspace_id':workspace_id,'call_id':call_id,'intent_id':intent_id,
        'actor_id':pin['actor_id'],'anchor':pin['anchor_hash'],'raw_sha256':sealed['raw_sha256'],
        'raw_size':len(raw),'receipt':copy.deepcopy(receipt),'audit_id':audit_id,
        'captured_at':captured.isoformat()}
    value['record_hash']=digest(value)
    audit=Audit(id=audit_id,workspace_id=workspace_id,actor_id=pin['actor_id'],action=ACTION,
        object_id=call_id,created_at=captured,data=value)
    db.add(audit); db.flush()
    db.execute(text('INSERT INTO public.ga_test_advice_v2_first_response '
        '(workspace_id,call_id,intent_id,audit_id,raw_bytes) VALUES (:w,:c,:i,:a,:r)'),
        {'w':workspace_id,'c':call_id,'i':intent_id,'a':audit_id,'r':raw})
    # Refuse same-transaction raw SQL mutation after capture; cleanup at root
    # transaction end. Capture isn't exposed as an HTTP route or worker.
    old.need(db.info.get('v2_first_response_test_guard') is None)
    expected=copy.deepcopy(value)
    def check(session):
        actual=_read(session,workspace_id,call_id,intent_id,_original(session,workspace_id,call_id,intent_id))
        old.need(actual is not None and actual[0]==raw and actual[1]==receipt)
        saved=session.get(Audit,audit_id,populate_existing=True)
        old.need(saved is not None and saved.action==ACTION and saved.object_id==call_id
            and saved.workspace_id==workspace_id and saved.actor_id==pin['actor_id'] and saved.data==expected)
    def cleanup(session, transaction):
        if transaction.parent is None:
            listener=session.info.pop('v2_first_response_test_guard',None)
            if listener is not None and event.contains(session,'before_commit',listener):event.remove(session,'before_commit',listener)
    if not db.info.get('v2_first_response_cleanup'):
        event.listen(db,'after_transaction_end',cleanup);db.info['v2_first_response_cleanup']=True
    event.listen(db,'before_commit',check);db.info['v2_first_response_test_guard']=check
    return raw, copy.deepcopy(receipt), sealed

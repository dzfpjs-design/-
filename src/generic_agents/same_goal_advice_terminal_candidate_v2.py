"""Test-only atomic settlement evidence; not a sender or production route.

An existing settled CloudCall cannot prove that its contribution reached a
shared daily total.  This experiment records that transition in the same
transaction as settlement. It does not classify a Plan as closed or grant a
second request. Existing settled records without a terminal remain blocked.
"""
import copy

from sqlalchemy import event, func, or_, select, text

from . import cloud_budget
from . import same_goal_advice_cloud_claim_test_v2 as claims
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_first_response_candidate_v2 as responses
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_response_intent_gate_candidate_v2 as gate
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .gateway import ModelBlocked
from .models import Audit, CloudCall, Job, uid

TABLE = 'ga_test_advice_v2_terminals'
ACTION = 'same_goal_advice.unlimited_terminal_test_v2'
SCHEMA = 'same-goal-advice-unlimited-terminal-test-v2'
FIELDS = {'schema','workspace_id','task_id','plan_id','call_id','intent_id','actor_id',
    'audit_id','anchor','response_hash','receipt_hash','day','status','charged_tokens',
    'reserved_tokens','ledger_id','ledger_before','ledger_after','created_at','record_hash'}


def _schema(db):
    columns = db.execute(text("SELECT attname,format_type(atttypid,atttypmod),attnotnull FROM pg_attribute "
        "WHERE attrelid=to_regclass('public.ga_test_advice_v2_terminals') AND attnum>0 AND NOT attisdropped ORDER BY attnum")).all()
    store.need(columns == [(name,'character varying(36)',True) for name in
        ('workspace_id','plan_id','call_id','intent_id','audit_id')], '测试终态表结构不一致')
    definitions = sorted(db.scalars(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid=to_regclass('public.ga_test_advice_v2_terminals')")))
    store.need(definitions == sorted(['PRIMARY KEY (workspace_id, plan_id)', 'UNIQUE (call_id)',
        'UNIQUE (intent_id)', 'UNIQUE (audit_id)', 'FOREIGN KEY (workspace_id) REFERENCES workspaces(id)',
        'FOREIGN KEY (plan_id) REFERENCES plan_versions(id)', 'FOREIGN KEY (call_id) REFERENCES cloud_model_calls(id)',
        'FOREIGN KEY (intent_id) REFERENCES audit_events(id)', 'FOREIGN KEY (audit_id) REFERENCES audit_events(id)']),
        '测试终态表约束不一致')
    indices=db.execute(text("SELECT i.indisvalid,i.indisready,i.indislive FROM pg_index i JOIN pg_constraint c "
        "ON c.conindid=i.indexrelid WHERE c.conrelid=to_regclass('public.ga_test_advice_v2_terminals') "
        "AND c.contype IN ('p','u')")).all()
    store.need(len(indices)==4 and all(tuple(row)==(True,True,True) for row in indices))


@store.checked
def install(db):
    """Install only in the identity-verified disposable PG; no production DDL."""
    claims.require_test_database(db)
    store.need(db.get_nested_transaction() is None)
    db.execute(text('LOCK TABLE public.cloud_model_calls, public.audit_events IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    existing=db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_terminals')"))
    if existing is None:
        store.need(not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==ACTION)),
            '存在未知终态回执；未建立测试表')
        db.execute(text('''CREATE TABLE public.ga_test_advice_v2_terminals (
          workspace_id varchar(36) NOT NULL REFERENCES public.workspaces(id),
          plan_id varchar(36) NOT NULL REFERENCES public.plan_versions(id),
          call_id varchar(36) NOT NULL UNIQUE REFERENCES public.cloud_model_calls(id),
          intent_id varchar(36) NOT NULL UNIQUE REFERENCES public.audit_events(id),
          audit_id varchar(36) NOT NULL UNIQUE REFERENCES public.audit_events(id),
          PRIMARY KEY (workspace_id, plan_id))'''))
    _schema(db)


def _captured(db, workspace_id, call_id, intent_id):
    pin=gate.check_original(db,workspace_id,call_id,intent_id)
    captured=storage.read(db,workspace_id,call_id,intent_id)
    store.need(type(captured) is tuple and len(captured)==3,'原首答尚未保全；不得重发')
    _raw,_receipt,sealed=captured
    proven=responses.proven_receipt(sealed,cloud_call_id=call_id,intent_audit_id=intent_id)
    usage=proven.get('usage')
    store.need(proven.get('usage_unavailable') is False and type(usage) is dict
        and type(usage.get('total_tokens')) is int and usage['total_tokens']>=0,
        '供应商实际用量未知；保留预留，等待核对')
    try:
        _value,parsed=responses.parse_saved(sealed,cloud_call_id=call_id,intent_audit_id=intent_id,
            schema=contracts.model_value_schema())
        store.need(all(parsed.get(key)==proven.get(key) for key in
            ('call_id','provider','model','request_id','usage','usage_unavailable')))
        status='succeeded'
    except ModelBlocked:
        status='failed'
    return pin,sealed,proven,status


def _daily(db, call):
    day=call.data.get('day')
    row=db.scalar(select(Job).where(Job.workspace_id==call.workspace_id,
        Job.idempotency_key=='cloud-budget:'+str(day)).with_for_update().execution_options(populate_existing=True))
    store.need(row is not None and row.kind=='cloud_budget' and row.status=='internal'
        and type(row.data) is dict and row.data.get('day')==day
        and type(row.data.get('tokens')) is int and row.data['tokens']>=0
        and type(row.data.get('calls')) is int and row.data['calls']>=1,'原日账本无法核对')
    return row


@store.checked
def verify(db,workspace_id,call_id,intent_id):
    """Authenticate one already-atomic terminal; grants no new operation."""
    claims.require_test_database(db); _schema(db)
    pin,sealed,proven,status=_captured(db,workspace_id,call_id,intent_id)
    call=db.scalar(select(CloudCall).where(CloudCall.workspace_id==workspace_id,CloudCall.id==call_id)
        .with_for_update().execution_options(populate_existing=True))
    store.need(call is not None and call.status==status and call.charged_tokens==proven['usage']['total_tokens']
        and call.data.get('usage_unavailable') is False and digest(call.data.get('receipt'))==digest(proven))
    daily=_daily(db,call)
    row=db.execute(text('SELECT plan_id,intent_id,audit_id FROM public.ga_test_advice_v2_terminals '
        'WHERE workspace_id=:w AND call_id=:c'),{'w':workspace_id,'c':call_id}).one_or_none()
    store.need(row is not None and row.intent_id==intent_id,'原终态不存在；保留记录等待核对')
    intent=db.get(Audit,intent_id)
    audit=db.scalar(select(Audit).where(Audit.workspace_id==workspace_id,Audit.id==row.audit_id)
        .with_for_update(read=True).execution_options(populate_existing=True))
    related=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace_id,Audit.action==ACTION,or_(
        Audit.object_id==row.plan_id,Audit.data['call_id'].as_string()==call_id,
        Audit.data['intent_id'].as_string()==intent_id)).execution_options(populate_existing=True)))
    value=audit.data if audit is not None else None
    store.need(len(related)==1 and related[0].id==row.audit_id
        and type(value) is dict and set(value)==FIELDS and audit.action==ACTION and audit.version==1
        and audit.actor_id==pin['actor_id'] and audit.object_id==row.plan_id
        and value['schema']==SCHEMA and value['record_hash']==digest({k:v for k,v in value.items() if k!='record_hash'})
        and value['workspace_id']==workspace_id and value['task_id']==intent.data['task_id']
        and value['plan_id']==row.plan_id==intent.data['plan_id'] and value['call_id']==call_id
        and value['intent_id']==intent_id and value['audit_id']==audit.id and value['actor_id']==pin['actor_id']
        and value['anchor']==pin['anchor_hash'] and value['response_hash']==digest(sealed)
        and value['receipt_hash']==digest(proven) and value['day']==call.data['day']
        and value['status']==status and value['charged_tokens']==call.charged_tokens
        and value['reserved_tokens']==call.reserved_tokens and value['ledger_id']==daily.id
        and audit.created_at is not None and audit.created_at.isoformat()==value['created_at'])
    before,after=value['ledger_before'],value['ledger_after']
    store.need(type(before) is dict and set(before)=={'tokens','calls'} and type(after) is dict
        and set(after)=={'tokens','calls'} and all(type(x) is int and x>=0 for x in (*before.values(),*after.values()))
        and before['calls']==after['calls'] and before['calls']>=1
        and after['tokens']==before['tokens']-call.reserved_tokens+call.charged_tokens)
    return {'plan_id':row.plan_id,'call_id':call_id,'intent_id':intent_id,'terminal_audit_id':audit.id,
        'status':status,'charged_tokens':call.charged_tokens,'usage_unavailable':False,
        'retry_authorized':False,'new_request_authorized':False}


def _unbind(session):
    check=session.info.pop('v2_atomic_terminal_guard',None)
    if check is not None and event.contains(session,'before_commit',check):
        event.remove(session,'before_commit',check)


def _ended(session,transaction):
    if transaction.parent is None:_unbind(session)


def _bind(db,workspace_id,call_id,intent_id,proof,record_hash,ledger_after):
    store.need(db.info.get('v2_atomic_terminal_guard') is None)
    def check(session):
        store.need(not session.new and not session.dirty and not session.deleted,
            '终态写入后出现未提交修改；未完成结算')
        actual=verify(session,workspace_id,call_id,intent_id)
        store.need(actual==proof)
        row=session.scalar(select(Audit).where(Audit.workspace_id==workspace_id,
            Audit.id==proof['terminal_audit_id']).execution_options(populate_existing=True))
        store.need(row is not None and digest(row.data)==record_hash)
        call=session.get(CloudCall,call_id);daily=_daily(session,call)
        store.need({key:daily.data[key] for key in ('tokens','calls')}==ledger_after,
            '终态提交前日账本发生变化；未完成结算')
    if not db.info.get('v2_atomic_terminal_cleanup_installed'):
        event.listen(db,'after_transaction_end',_ended)
        db.info['v2_atomic_terminal_cleanup_installed']=True
    event.listen(db,'before_commit',check);db.info['v2_atomic_terminal_guard']=check


@store.checked
def settle_and_record(db,workspace_id,call_id,intent_id):
    """Atomically settle one original paid response and write its proof."""
    claims.require_test_database(db); store.need(db.get_nested_transaction() is None)
    store.need(not db.new and not db.dirty and not db.deleted,
        '当前事务存在其他未提交更改；未完成结算')
    claims.lock_and_check(db); _schema(db)
    existing=db.execute(text('SELECT audit_id FROM public.ga_test_advice_v2_terminals '
        'WHERE workspace_id=:w AND call_id=:c'),{'w':workspace_id,'c':call_id}).first()
    if existing is not None:return verify(db,workspace_id,call_id,intent_id)
    pin,sealed,proven,status=_captured(db,workspace_id,call_id,intent_id)
    intent=db.get(Audit,intent_id)
    call=db.scalar(select(CloudCall).where(CloudCall.workspace_id==workspace_id,CloudCall.id==call_id)
        .with_for_update().execution_options(populate_existing=True))
    store.need(call is not None and call.status=='reserved',
        '该历史调用已结算但没有原子终态回执；不能补造或重发')
    daily=_daily(db,call)
    before={key:daily.data[key] for key in ('tokens','calls')}
    original=call.data['same_goal_advice_unlimited_v2']
    expected={'workspace_id':workspace_id,'capability_id':call.capability_id,'actor_id':pin['actor_id'],
        'request_hash':original['request_hash'],'dispatch_options_hash':original['dispatch_options_hash'],
        'provider':pin['provider'],'model':pin['model']}
    cloud_budget.settle_paid_response(db,call_id,copy.deepcopy(proven),expected,status);db.flush()
    after={key:daily.data[key] for key in ('tokens','calls')}
    store.need(after['calls']==before['calls'] and after['tokens']==before['tokens']-call.reserved_tokens+call.charged_tokens)
    now=db.scalar(select(func.clock_timestamp())); aid=uid()
    value={'schema':SCHEMA,'workspace_id':workspace_id,'task_id':intent.data['task_id'],
        'plan_id':intent.data['plan_id'],'call_id':call_id,'intent_id':intent_id,'actor_id':pin['actor_id'],
        'audit_id':aid,'anchor':pin['anchor_hash'],'response_hash':digest(sealed),'receipt_hash':digest(proven),
        'day':call.data['day'],'status':status,'charged_tokens':call.charged_tokens,'reserved_tokens':call.reserved_tokens,
        'ledger_id':daily.id,'ledger_before':before,'ledger_after':after,'created_at':now.isoformat()}
    value['record_hash']=digest(value)
    db.add(Audit(id=aid,workspace_id=workspace_id,actor_id=pin['actor_id'],action=ACTION,
        object_id=intent.data['plan_id'],created_at=now,data=value));db.flush()
    db.execute(text('INSERT INTO public.ga_test_advice_v2_terminals '
        '(workspace_id,plan_id,call_id,intent_id,audit_id) VALUES (:w,:p,:c,:i,:a)'),
        {'w':workspace_id,'p':intent.data['plan_id'],'c':call_id,'i':intent_id,'a':aid})
    store.need(gate.check_original(db,workspace_id,call_id,intent_id)==pin)
    result=verify(db,workspace_id,call_id,intent_id)
    _bind(db,workspace_id,call_id,intent_id,result,digest(value),after)
    return result

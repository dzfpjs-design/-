"""Real pure preparation/DTO/ledger checks with explicitly synthetic SQL IO.

These fixtures are public in-memory inputs, not saved producer authority, a
provider response, PostgreSQL, a budget grant or an executed Worker. Root owns
the independent authenticated source/PG/gateway/capture acceptance journey.
"""
import asyncio
import copy
import json
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import UniqueConstraint, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, OperationalError

from generic_agents import report_saved_map_design_consumer_v176 as app
from generic_agents.contracts import digest
from generic_agents.models import Audit, CloudCall, Job
from generic_agents.leases import LeaseLost, ExecutionUnauthorized
from generic_agents.report_saved_map_design_models_v176 import (
    ReportSavedMapDesignV176 as Consumer, ReportSavedMapDesignCommandV176 as Command)
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_saved_map_design_preparation_v176 import source_scene, identity


@pytest.fixture(autouse=True)
def no_network_or_process(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Pure consumer tests cannot connect or launch a process')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)


def scene():
    cap, source = source_scene()
    p = SimpleNamespace(user_id=source['snapshot']['actor_id'],workspace_id=source['snapshot']['workspace_id'],role='operator')
    cap.id=identity(44);cap.workspace_id=p.workspace_id;cap.status='published';cap.content_hash=digest(cap.data)
    body=app.PreviewBody(producer_id=source['snapshot']['producer']['id'],revision=7,model_id=cap.id,note=' 独立新方案，不是旧回答续写。 ')
    prep=app.preparation.build_preparation(cap,source,note=body.note)
    budget={'schema':'report-saved-map-design-budget-v176','call_limit':1,
        'token_bound':prep['input_summary']['token_bound'],'maximum_cost_usd':3,
        'pricing_basis_hash':'b'*64,'grant_hash':'c'*64}
    preview={'schema':app.PREVIEW,'actor_id':p.user_id,'workspace_id':p.workspace_id,
        'body':body.model_dump(),'source_context':copy.deepcopy(source),'capability':app._cap_value(cap),
        'preparation':prep,'budget':budget,**app.FALSE_FLAGS}
    preview['preview_hash']=app.h(preview)
    start={**body.model_dump(),'request_id':identity(80),'source_snapshot_hash':source['snapshot']['snapshot_hash'],
        'preview_hash':preview['preview_hash'],'consent':True}
    raw=json.dumps(start,ensure_ascii=False).encode('utf-8')
    _,exact,sha=app.read_wire(raw,app.StartBody)
    row=Consumer(id=identity(81),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=start['request_id'],
        producer_id=body.producer_id,model_id=body.model_id,grant_hash=budget['grant_hash'],revision=body.revision,
        version=1,status='queued',job_id=identity(82),reserved_call_id=None,data={
            'schema':app.STATE,'preview':preview,'dispatch_started':False,'dispatch_identity':None,
            'call_pin':None,'response':None,'projection':None,'manual_value':None,'error':None,
            'command_refs':[{'id':identity(83),'request_id':start['request_id'],'operation':'start','wire_sha256':sha}]})
    command=Command(id=identity(83),workspace_id=p.workspace_id,actor_id=p.user_id,request_id=start['request_id'],
        consumer_id=row.id,operation='start',method='POST',path=app.PREFIX+'/start',exact_body=exact,
        body_sha256=sha,data={'schema':app.COMMAND,'result':app.result(row,'start')})
    command.command_hash=app.h(app._command_pin(command))
    anchors=[Audit(id=identity(84),workspace_id=p.workspace_id,actor_id=p.user_id,action=app.COMMAND_AUDIT,
        object_id=command.id,data=app._command_anchor(command)),
        Audit(id=identity(85),workspace_id=p.workspace_id,actor_id=p.user_id,action=app.STATE_AUDIT,
        object_id=row.id,data=app._state_anchor(row,None))]
    job=Job(id=row.job_id,workspace_id=p.workspace_id,kind=app.JOB,status='queued',version=1,
        idempotency_key='saved-map-design:'+row.request_id,data={'actor_id':p.user_id,'consumer_id':row.id},
        lease_token=identity(86))
    return p,body,cap,source,row,command,anchors,job,raw


def equal_statement(actual, expected):
    left,right=actual.compile(),expected.compile()
    # ORM Select.compare traverses a Mapper as a SQL expression on the installed
    # SQLAlchemy version. Compile the full real SELECT instead; retain every
    # typed payload/owner column, predicate and exact typed bind value.
    assert str(left)==str(right), 'actual SQL differs from the complete expected scoped SELECT'
    assert canonical_json(left.params)==canonical_json(right.params)


def matches_statement(actual,expected):
    left,right=actual.compile(),expected.compile()
    return str(left)==str(right) and canonical_json(left.params)==canonical_json(right.params)


class InventoryIO:
    """Strict actual SQL shape and bind checks, not a database substitute."""
    def __init__(self,p,rows=(),commands=(),audits=(),jobs=(),calls=()):
        self.p=p;self.rows=list(rows);self.commands=list(commands);self.audits=list(audits)
        self.jobs=list(jobs);self.calls=list(calls);self.locks=[];self.commits=0;self.added=[];self.info={}

    def execute(self,statement,params):
        assert statement.text in {'SELECT pg_advisory_xact_lock(:k)','SELECT pg_advisory_xact_lock_shared(:k)'}
        assert set(params)=={'k'} and type(params['k']) is int
        self.locks.append((statement.text,params['k']))

    def closure_choices(self, statement, *, scalar):
        """Exact bounded Closure SQL over these actual synthetic fixture rows.

        Derive candidate nonce values from typed binds, then compare the WHOLE
        expected SELECT/predicates/limit/binds below. This is not an arbitrary
        SQL parser or a production bypass: a missing workspace/action/OR arm,
        changed limit or wide query still has no matching fixture operation.
        """
        from generic_agents import catalogue_current_allocation_closure_v260 as closure
        p=self.p;nonces=set()
        def collect(value):
            if type(value) is str:
                candidate=value[len('saved-map-design:'):] if value.startswith('saved-map-design:') else value
                try:
                    if str(UUID(candidate))==candidate:nonces.add(candidate)
                except (ValueError,TypeError,AttributeError):pass
            elif type(value) is dict:
                for child in value.values():collect(child)
            elif type(value) in (list,tuple):
                for child in value:collect(child)
        collect(statement.compile().params)
        def payload(row):return row.data if type(row.data) is dict else {}
        def references(row,nonce):
            refs=payload(row).get('command_refs',[])
            return type(refs) is list and any(type(ref) is dict and ref.get('request_id')==nonce for ref in refs)
        choices=[]
        for nonce in sorted(nonces):
            fid=closure.fence_identity(p.workspace_id,nonce);key='saved-map-design:'+nonce
            ref={'command_refs':[{'request_id':nonce}]}
            if not scalar:
                choices.extend([
                    (select(Job).where(Job.workspace_id==p.workspace_id,
                        or_(Job.id==fid,Job.idempotency_key==key)).limit(3)
                        .execution_options(populate_existing=True),
                        [row for row in self.jobs if row.workspace_id==p.workspace_id
                            and (row.id==fid or row.idempotency_key==key)][:3]),
                    (select(Audit).where(Audit.workspace_id==p.workspace_id,
                        Audit.action==closure.AUDIT_ACTION,
                        or_(Audit.object_id==fid,Audit.data['request_id'].as_string()==nonce)).limit(3),
                        [row for row in self.audits if row.workspace_id==p.workspace_id
                            and row.action==closure.AUDIT_ACTION
                            and (row.object_id==fid or payload(row).get('request_id')==nonce)][:3]),
                    (select(Command).where(Command.workspace_id==p.workspace_id,
                        Command.request_id==nonce).limit(2),
                        [row for row in self.commands if row.workspace_id==p.workspace_id
                            and row.request_id==nonce][:2])])
            else:
                matches=[row for row in self.rows if row.workspace_id==p.workspace_id
                    and (row.request_id==nonce or references(row,nonce))]
                anchors=[row for row in self.audits if row.workspace_id==p.workspace_id
                    and row.action in {app.COMMAND_AUDIT,app.STATE_AUDIT}
                    and (payload(row).get('request_id')==nonce or references(row,nonce))]
                jobs=[row for row in self.jobs if row.workspace_id==p.workspace_id and row.idempotency_key==key]
                choices.extend([
                    (select(Consumer.id).where(Consumer.workspace_id==p.workspace_id,
                        or_(Consumer.request_id==nonce,cast(Consumer.data,JSONB).contains(ref))).limit(1),
                        matches[0].id if matches else None),
                    (select(Audit.id).where(Audit.workspace_id==p.workspace_id,
                        Audit.action.in_([app.COMMAND_AUDIT,app.STATE_AUDIT]),
                        or_(Audit.data['request_id'].as_string()==nonce,cast(Audit.data,JSONB).contains(ref))).limit(1),
                        anchors[0].id if anchors else None),
                    (select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key)
                        .execution_options(populate_existing=True),jobs[0] if jobs else None)])
        if scalar:
            for row_id in {row.id for row in self.rows}|{row.consumer_id for row in self.commands}:
                matches=[row for row in self.rows if row.workspace_id==p.workspace_id
                    and row.id==row_id and row.actor_id==p.user_id]
                choices.append((select(Consumer).where(Consumer.workspace_id==p.workspace_id,
                    Consumer.id==row_id,Consumer.actor_id==p.user_id),matches[0] if matches else None))
            call_identity=CloudCall.data['gateway_dispatch']['identity']
            present=select(Consumer.id).join(Job,Job.id==Consumer.job_id).where(
                Consumer.workspace_id==CloudCall.workspace_id,
                Consumer.id==call_identity['suggestion_id'].as_string(),
                Consumer.job_id==call_identity['job_id'].as_string(),
                Consumer.actor_id==call_identity['actor_id'].as_string(),
                Consumer.model_id==CloudCall.capability_id,
                Job.workspace_id==CloudCall.workspace_id,Job.kind==app.JOB,
                Job.data['consumer_id'].as_string()==Consumer.id,
                Job.data['actor_id'].as_string()==Consumer.actor_id,
                Job.data['generation_profile'].as_string()==closure.PROFILE).exists()
            orphan=None
            for call in self.calls:
                dispatch=payload(call).get('gateway_dispatch',{})
                claim=dispatch.get('identity',{}) if type(dispatch) is dict else {}
                task=claim.get('task_id') if type(claim) is dict else None
                if call.workspace_id!=p.workspace_id or type(task) is not str or not task.startswith(closure.TASK_PREFIX):continue
                linked=any(row.workspace_id==call.workspace_id and row.id==claim.get('suggestion_id')
                    and row.job_id==claim.get('job_id') and row.actor_id==claim.get('actor_id')
                    and row.model_id==call.capability_id and any(job.id==row.job_id
                        and job.workspace_id==call.workspace_id and job.kind==app.JOB
                        and payload(job).get('consumer_id')==row.id and payload(job).get('actor_id')==row.actor_id
                        and payload(job).get('generation_profile')==closure.PROFILE for job in self.jobs)
                    for row in self.rows)
                if not linked:orphan=call.id;break
            choices.append((select(CloudCall.id).where(CloudCall.workspace_id==p.workspace_id,
                call_identity['task_id'].as_string().startswith(closure.TASK_PREFIX),~present).limit(1),orphan))
        return choices

    def scalars(self,statement):
        p=self.p
        for expected,result in self.closure_choices(statement,scalar=False):
            if matches_statement(statement,expected):equal_statement(statement,expected);return list(result)
        ordinary_audits=[event for event in self.audits if event.workspace_id==p.workspace_id
            and event.action in {app.STATE_AUDIT,app.COMMAND_AUDIT}]
        lifecycle_actions=[app.ALLOCATION_LINK_AUDIT,app.ALLOCATION_CHILD_LINK_AUDIT,app.ALLOCATION_END_AUDIT,
            app.CURRENT_ALLOCATION_LINK_AUDIT,app.CURRENT_ALLOCATION_CHILD_LINK_AUDIT,app.CURRENT_ALLOCATION_END_AUDIT]
        allocation_links=[event for event in self.audits if event.workspace_id==p.workspace_id
            and event.action in set(lifecycle_actions)
            and (event.actor_id==p.user_id or type(event.data) is dict and event.data.get('actor_id')==p.user_id)]
        choices=[(select(Consumer).where(Consumer.workspace_id==p.workspace_id,Consumer.actor_id==p.user_id),self.rows),
            (select(Command).where(Command.workspace_id==p.workspace_id,Command.actor_id==p.user_id),self.commands),
            (select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,
                Audit.action.in_([app.STATE_AUDIT,app.COMMAND_AUDIT])),
                [event for event in ordinary_audits if event.actor_id==p.user_id]),
            (select(Audit).where(Audit.workspace_id==p.workspace_id,
                or_(Audit.actor_id==p.user_id,Audit.data['actor_id'].as_string()==p.user_id),
                Audit.action.in_(lifecycle_actions)),allocation_links),
            (select(Consumer).where(Consumer.workspace_id==p.workspace_id),self.rows),
            (select(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.action.in_([app.STATE_AUDIT,app.COMMAND_AUDIT])),ordinary_audits)]
        for row in self.rows:
            choices.append((select(CloudCall).where(CloudCall.workspace_id==p.workspace_id,
                CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==row.id),
                [call for call in self.calls if call.data.get('gateway_dispatch',{}).get('identity',{}).get('suggestion_id')==row.id]))
        for expected,result in choices:
            if matches_statement(statement,expected):equal_statement(statement,expected);return list(result)
        raise AssertionError('Unexpected SELECT: '+str(statement))

    def scalar(self,statement):
        p=self.p
        for expected,result in self.closure_choices(statement,scalar=True):
            if matches_statement(statement,expected):equal_statement(statement,expected);return result
        for job in self.jobs:
            expected=select(Job).where(Job.workspace_id==p.workspace_id,Job.id==job.id)
            if matches_statement(statement,expected):equal_statement(statement,expected);return job
        # Request lookup returns an actually present synthetic command or None.
        for nonce in {item.request_id for item in self.commands}|{identity(999)}:
            expected=select(Command).where(Command.workspace_id==p.workspace_id,Command.request_id==nonce)
            if matches_statement(statement,expected):equal_statement(statement,expected);return next((item for item in self.commands if item.request_id==nonce),None)
        raise AssertionError('Unexpected scalar SELECT: '+str(statement))

    def add(self,row):
        self.added.append(row)
        if isinstance(row,Audit):
            row.id=identity(200+len(self.audits));self.audits.append(row)
        else:raise AssertionError('No new consumer, command, job or call is allowed in this read-only recovery case')

    def commit(self):self.commits+=1


def io_scene():
    values=scene();p,_,_,_,row,command,anchors,job,_=values
    return values,InventoryIO(p,[row],[command],anchors,[job])


class StartFlushIO(InventoryIO):
    """Foreign-key phase assertions, not a PG/UoW ordering simulation.

    Admission/IAM is an explicit callback in this narrowly scoped fixture. The
    real route, wire, nonce inventory, preview, commands and Audit values stay
    intact; only Root's real PG proves database insertion/rollback behavior.
    """
    def __init__(self, p, nonce, grant_hash, *, fail_at=None, failure=None):
        super().__init__(p)
        self.nonce=nonce;self.grant_hash=grant_hash;self.pending=[];self.phases=[]
        self.fail_at=fail_at;self.failure=failure;self.rollbacks=0;self.events=[]

    def scalar(self, statement):
        expected=select(Command).where(Command.workspace_id==self.p.workspace_id,Command.request_id==self.nonce)
        if matches_statement(statement,expected):
            equal_statement(statement,expected)
            return next((row for row in self.commands if row.request_id==self.nonce),None)
        expected=select(Consumer.id).where(Consumer.workspace_id==self.p.workspace_id,Consumer.grant_hash==self.grant_hash)
        if matches_statement(statement,expected):
            equal_statement(statement,expected)
            return next((row.id for row in self.rows if row.grant_hash==self.grant_hash),None)
        return super().scalar(statement)

    def scalars(self, statement):
        choices=[(select(Consumer).where(Consumer.workspace_id==self.p.workspace_id),self.rows),
            (select(Audit).where(Audit.workspace_id==self.p.workspace_id,
                Audit.action.in_([app.STATE_AUDIT,app.COMMAND_AUDIT])),self.audits)]
        for expected,rows in choices:
            if matches_statement(statement,expected):equal_statement(statement,expected);return list(rows)
        return super().scalars(statement)

    def add(self, row):
        assert type(row) in {Job,Consumer,Command,Audit}
        self.pending.append(row)

    def flush(self):
        kinds=tuple(type(row) for row in self.pending)
        expected=[(Job,),(Consumer,),(Command,),(Audit,Audit)]
        phase=len(self.phases)+1
        assert kinds==expected[phase-1], 'Each FK parent must be flushed alone before its child is added'
        self.phases.append(kinds);self.events.append(('flush',phase))
        if self.fail_at==phase:raise self.failure
        for row in self.pending:
            if type(row) is Job:
                assert self.rows==self.commands==[];self.jobs.append(row)
            elif type(row) is Consumer:
                assert row.job_id in {job.id for job in self.jobs};self.rows.append(row)
            elif type(row) is Command:
                assert row.consumer_id in {item.id for item in self.rows};self.commands.append(row)
            else:
                assert self.commands and self.rows
                row.id=identity(600+len(self.audits));self.audits.append(row)
        self.pending=[]

    def commit(self):
        assert len(self.phases)==3 and tuple(type(row) for row in self.pending)==(Audit,Audit)
        self.flush();self.commits+=1;self.events.append(('commit',self.commits))

    def rollback(self):
        self.rollbacks+=1;self.events.append(('rollback',self.rollbacks))
        self.pending=[];self.jobs=[];self.rows=[];self.commands=[];self.audits=[]


@pytest.fixture
def start_flush(monkeypatch):
    p,body,cap,source,old_row,*tail=scene();raw=tail[-1]
    nonce=json.loads(raw)['request_id'];authority=[]
    def admitted(db,principal,producer,revision,model,*,lock,consumer=None,note=''):
        assert principal is p and (producer,revision,model)==(body.producer_id,body.revision,body.model_id)
        assert lock is True and consumer is None and note==body.note
        authority.append('admission-spy')
        return {'source_context':source,'capability':cap,'revision':revision,'budget':old_row.data['preview']['budget']}
    def current_actor(db,principal):
        assert principal is p;authority.append('current-actor-spy')
    monkeypatch.setattr(app,'runtime',lambda:SimpleNamespace(admit=admitted))
    monkeypatch.setattr(app,'lock_current_actor',current_actor)
    async def request_body():return raw
    request=SimpleNamespace(method='POST',url=SimpleNamespace(path=app.PREFIX+'/start'),body=request_body)
    return SimpleNamespace(p=p,raw=raw,nonce=nonce,authority=authority,request=request,
        grant_hash=old_row.grant_hash,db=lambda **kwargs:StartFlushIO(p,nonce,old_row.grant_hash,**kwargs))


def test_start_flushes_job_then_consumer_then_original_command_before_a_single_atomic_commit(start_flush):
    case=start_flush;db=case.db()
    result=asyncio.run(app.start_route(case.request,case.p,db))
    assert db.phases==[(Job,),(Consumer,),(Command,),(Audit,Audit)]
    assert db.commits==1 and db.rollbacks==0 and db.events[-1]==('commit',1)
    assert len(db.jobs)==len(db.rows)==len(db.commands)==1 and len(db.audits)==2
    row,command=db.rows[0],db.commands[0]
    assert row.job_id==db.jobs[0].id and command.consumer_id==row.id
    assert command.exact_body.encode('utf-8')==case.raw and command.request_id==case.nonce
    assert result==command.data['result'] and result['status']=='queued'
    assert row.reserved_call_id is None and row.data['dispatch_started'] is False and db.calls==[]
    # Exact replay keeps the original wire/result and cannot add or flush a job.
    before=list(db.events)
    assert asyncio.run(app.start_route(case.request,case.p,db))==result
    assert db.events==before and db.commits==1 and len(db.jobs)==1


@pytest.mark.parametrize('phase',[1,2,3,4])
def test_start_any_flush_fk_failure_rolls_back_job_consumer_command_and_audits(start_flush,phase):
    original=RuntimeError('synthetic PG foreign-key failure')
    original.diag=SimpleNamespace(constraint_name='report_saved_map_design_commands_v176_consumer_id_fkey')
    error=IntegrityError('SYNTHETIC INSERT',{},original)
    db=start_flush.db(fail_at=phase,failure=error)
    with pytest.raises(IntegrityError) as caught:asyncio.run(app.start_route(start_flush.request,start_flush.p,db))
    assert caught.value is error and db.rollbacks==1 and db.commits==0
    assert db.jobs==db.rows==db.commands==db.audits==db.pending==[]


@pytest.mark.parametrize('failure_kind',['grant_conflict','lease_lost'])
def test_start_preserves_grant_classification_and_propagates_lease_failure_with_rollback(start_flush,failure_kind):
    if failure_kind=='grant_conflict':
        original=RuntimeError('synthetic unique activity grant conflict')
        original.diag=SimpleNamespace(constraint_name='uq_report_saved_map_design_grant_v176')
        error=IntegrityError('SYNTHETIC INSERT',{},original)
    else:error=LeaseLost('synthetic commit fencing failure')
    db=start_flush.db(fail_at=2 if failure_kind=='grant_conflict' else 4,failure=error)
    with pytest.raises(HTTPException if failure_kind=='grant_conflict' else LeaseLost) as caught:
        asyncio.run(app.start_route(start_flush.request,start_flush.p,db))
    if failure_kind=='grant_conflict':assert caught.value.status_code==409 and '许可已被另一提交占用' in caught.value.detail
    else:assert caught.value is error
    assert db.rollbacks==1 and db.commits==0 and db.jobs==db.rows==db.commands==db.audits==[]


def test_new_models_have_independent_nonce_grant_and_single_call_constraints():
    assert Consumer.__tablename__=='report_saved_map_designs_v176'
    assert Command.__tablename__=='report_saved_map_design_commands_v176'
    unique={tuple(column.name for column in item.columns) for item in Consumer.__table__.constraints if isinstance(item,UniqueConstraint)}
    assert {('workspace_id','request_id'),('workspace_id','grant_hash'),('reserved_call_id',),('job_id',)} <= unique
    assert ('workspace_id','request_id') in {tuple(column.name for column in item.columns) for item in Command.__table__.constraints if isinstance(item,UniqueConstraint)}


@pytest.mark.parametrize('change',[{'revision':True},{'revision':1.0},{'consent':1},{'consent':False},
    {'producer_id':'not-a-uuid'},{'model_id':'not-a-uuid'},{'source_snapshot_hash':'x'*64},
    {'old_consent':True},{'note':'x'*2001}])
def test_start_is_closed_strict_and_requires_new_explicit_consent(change):
    *_,raw=scene();value=json.loads(raw);value.update(change)
    with pytest.raises(HTTPException) as caught:app.read_wire(json.dumps(value).encode(),app.StartBody)
    assert caught.value.status_code==422


@pytest.mark.parametrize('raw',[b'{"request_id":"a","request_id":"b"}',b'{"x":NaN}',
    b'{"x":Infinity}',b'[]',b'\xff',b'{} '+b'x',b' '*65537])
def test_original_json_rejects_duplicate_nonfinite_incomplete_or_oversize(raw):
    with pytest.raises(HTTPException) as caught:app.read_wire(raw,app.StartBody)
    assert caught.value.status_code==422


def test_original_utf8_hash_is_byte_exact_and_note_is_not_trimmed():
    *_,raw=scene();body,exact,sha=app.read_wire(raw,app.StartBody)
    compact=json.dumps(json.loads(raw),ensure_ascii=False,separators=(',',':')).encode()
    again,_,other=app.read_wire(compact,app.StartBody)
    assert body.model_dump()==again.model_dump() and sha!=other
    assert exact.encode('utf-8')==raw and body.note.startswith(' ')


@pytest.mark.parametrize('change',[{'call_limit':True},{'call_limit':2},{'token_bound':True},
    {'maximum_cost_usd':False},{'maximum_cost_usd':0},{'maximum_cost_usd':float('inf')},
    {'grant_hash':'bad'},{'pricing_basis_hash':'bad'},{'legacy_consent':True}])
def test_budget_needs_exact_current_new_grant_and_price_bound(change):
    p,_,_,_,row,*_=scene();budget=copy.deepcopy(row.data['preview']['budget']);budget.update(change)
    with pytest.raises(HTTPException):app.check_budget(budget,row.data['preview']['preparation']['input_summary']['token_bound'])


def test_actual_pure_preview_is_lossless_and_has_no_consumer_permission_or_calls(monkeypatch):
    p,body,cap,source,row,*_=scene();before=canonical_json([cap.data,source])
    called=[]
    def admit(db,principal,producer_id,revision,model_id,**options):
        assert db is None and principal is p;called.append((producer_id,revision,model_id,options))
        return {'source_context':source,'capability':cap,'revision':revision,'budget':row.data['preview']['budget']}
    monkeypatch.setattr(app,'runtime',lambda:SimpleNamespace(admit=admit))
    value=app.prepare(None,p,body)
    assert app.check_preview(value,p) is value
    shown=app.preview_view(value)
    assert shown['call_count']==1 and shown['requires_separate_authorization'] is True
    assert all(shown[key] is False for key in app.FALSE_FLAGS)
    assert shown['sent_payload']['input_order_hash']==source['snapshot']['input_order_hash']
    assert len(shown['sent_payload']['input_claims'])==len(source['snapshot']['input_order'])
    assert canonical_json([cap.data,source])==before
    assert called==[(body.producer_id,body.revision,body.model_id,{'lock':False,'consumer':None,'note':body.note})]


def test_unwired_central_runtime_fails_closed_without_generic_dispatch(monkeypatch):
    def missing(name,package):
        assert name=='.report_saved_map_design_runtime_v176'
        error=ModuleNotFoundError('explicit central seam absent');error.name=package+name;raise error
    monkeypatch.setattr(app.importlib,'import_module',missing)
    with pytest.raises(HTTPException) as caught:app.runtime()
    assert caught.value.status_code==503


def test_complete_original_ledger_reads_without_current_source_or_new_authorization(monkeypatch):
    values,db=io_scene();p,_,_,_,row,command,*_=values
    monkeypatch.setattr(app,'runtime',lambda:(_ for _ in ()).throw(AssertionError('history must not renew source or budget')))
    records,commands,calls=app._inventory(db,p)
    assert records[row.id] is row and commands[command.id] is command and calls=={}
    assert db.added==[] and db.commits==0
    assert all('lock_shared' in sql for sql,_ in db.locks)


@pytest.mark.parametrize('fault',['missing_record','missing_command','missing_both_command_audit',
    'missing_command_audit','missing_state_audit','duplicate_audit','raw_changed','state_changed',
    'version_bool','grant_changed','refs_changed','family_changed'])
def test_independent_inventory_never_treats_residue_or_resealed_row_as_missing(fault):
    values,db=io_scene();p,_,_,_,row,command,anchors,*_=values
    if fault=='missing_record':db.rows=[]
    elif fault=='missing_command':db.commands=[]
    elif fault=='missing_both_command_audit':db.commands=[];db.audits=[item for item in db.audits if item.action!=app.COMMAND_AUDIT]
    elif fault=='missing_command_audit':db.audits=[item for item in db.audits if item.action!=app.COMMAND_AUDIT]
    elif fault=='missing_state_audit':db.audits=[item for item in db.audits if item.action!=app.STATE_AUDIT]
    elif fault=='duplicate_audit':db.audits.append(copy.deepcopy(anchors[0]))
    elif fault=='raw_changed':command.exact_body+=' '
    elif fault=='state_changed':row.data={**row.data,'error':'forged result'}
    elif fault=='version_bool':row.version=True
    elif fault=='grant_changed':row.grant_hash='d'*64
    elif fault=='refs_changed':row.data={**row.data,'command_refs':[]}
    else:row.data={**row.data,'schema':'report-evidence-reading-state-v172'}
    with pytest.raises(app.InventoryError):app._inventory(db,p)
    assert db.added==[] and db.commits==0


def test_truly_empty_actor_inventory_is_distinct_from_orphan_and_has_no_writes():
    p,*_=scene();db=InventoryIO(p)
    assert app._inventory(db,p)==({},{},{})
    assert len(db.locks)==1 and db.added==[] and db.commits==0


def test_recovery_reference_is_exact_and_never_replaces_original_result_version():
    p,_,_,_,row,command,_,_,raw=scene()
    assert app._replay({command.id:command},p,command.request_id,'start',app.PREFIX+'/start',raw.decode(),command.body_sha256)==command.data['result']
    with pytest.raises(HTTPException):app._replay({command.id:command},p,command.request_id,'recover-saved',app.PREFIX+'/'+row.id+'/recover-saved',raw.decode(),command.body_sha256)
    row.version=9;row.status='response_unknown'
    assert command.data['result']['version']==1 and command.data['result']['status']=='queued'


def test_same_reserved_call_binding_is_independent_of_old_producer_or_map_stages():
    p,_,_,_,row,*_=scene();row.reserved_call_id=identity(88)
    binding=app.journal_binding(row);actual=app.dispatch_identity(row)
    assert binding['draft_id']==row.id and binding['stage']=='reduce' and binding['reserved_call_id']==identity(88)
    assert binding['draft_id']!=row.producer_id and binding['dispatch_hash']==digest(actual)
    assert actual['task_id']=='saved-map-design-v176' and actual['job_id']==row.job_id
    assert binding['generation_hash']==digest({'published_model_hash':row.data['preview']['capability']['content_hash'],'contract':row.data['preview']['preparation']['generation_contract']})


@pytest.mark.parametrize('fault',['model','body','options','delivery','endpoint','bytes','policy','proof','dispatch_actor','reservation_tokens'])
def test_reserved_call_requires_every_actual_native_request_and_dispatch_pin(fault):
    p,_,_,_,row,*_=scene();prep=row.data['preview']['preparation'];spec=prep['generation_contract']
    call=CloudCall(id=identity(88),workspace_id=p.workspace_id,capability_id=row.model_id,status='reserved',
        reserved_tokens=prep['input_summary']['token_bound'],charged_tokens=prep['input_summary']['token_bound'],data={
            'actor_id':p.user_id,'request_hash':prep['request_hash'],
            'dispatch_options_hash':digest(spec['dispatch_options']),'delivery':app.native.DELIVERY,
            'request_endpoint':app.native.ENDPOINT,'request_body_bytes':prep['input_summary']['prepared_request_bytes'],
            'input_policy_hash':digest(spec['input_policy']),'schema_proof_hash':digest(spec['schema_proof']),
            'gateway_dispatch':{'schema':'cloud-dispatch-v130','identity':app.dispatch_identity(row),
                'attempt_id':identity(89),'reservation_request_hash':prep['request_hash']}})
    assert app._check_call(row,call) is call
    call.data=copy.deepcopy(call.data)
    if fault=='model':call.capability_id=identity(99)
    elif fault=='dispatch_actor':call.data['gateway_dispatch']['identity']['actor_id']=identity(99)
    elif fault=='reservation_tokens':call.reserved_tokens=True
    else:
        key={'body':'request_hash','options':'dispatch_options_hash','delivery':'delivery',
            'endpoint':'request_endpoint','bytes':'request_body_bytes','policy':'input_policy_hash','proof':'schema_proof_hash'}[fault]
        call.data[key]=False if fault=='bytes' else 'wrong'
    with pytest.raises(app.InventoryError):app._check_call(row,call)


def test_unknown_intent_reclaim_only_reads_original_and_does_not_enter_dispatch(monkeypatch):
    values,db=io_scene();p,_,_,_,row,_,_,job,_=values
    app._transition(db,p,row,{**row.data,'dispatch_started':True,'dispatch_identity':app.dispatch_identity(row)},'running')
    job.status='running';db.info['worker_lease']={'job_id':job.id,'lease_token':job.lease_token}
    read=[]
    def read_response(actual_db,principal,actual,*,recover_saved):
        assert actual_db is db and principal is p and actual is row and recover_saved is True
        read.append(actual.id);return None
    def forbidden(*args,**kwargs):raise AssertionError('unknown intent must never reserve/send or renew source grant')
    monkeypatch.setattr(app,'runtime',lambda:SimpleNamespace(read_response=read_response,dispatch=forbidden,admit=forbidden))
    result=app.run(db,p,job)
    assert result['status']=='response_unknown' and row.reserved_call_id is None
    assert result['result'] is None and read==[row.id]
    assert len([item for item in db.added if isinstance(item,Audit)])==2
    assert all(not isinstance(item,(CloudCall,Job,Consumer,Command)) for item in db.added)
    assert db.commits==1


@pytest.mark.parametrize('failure',[LeaseLost('lease lost'),ExecutionUnauthorized('actor revoked'),OperationalError('SELECT',{},Exception('offline'))])
def test_inventory_never_reclassifies_lease_iam_or_database_errors(failure):
    values,db=io_scene();p=values[0]
    def fail(statement):raise failure
    db.scalars=fail
    with pytest.raises(type(failure)) as caught:app._inventory(db,p)
    assert caught.value is failure


def test_raw_paths_and_recover_versions_are_not_a_retire_or_paid_retry_protocol():
    with pytest.raises(HTTPException):app.read_wire(json.dumps({'request_id':identity(1),'expected_version':True}).encode(),app.RecoverBody)
    with pytest.raises(HTTPException):app.read_wire(json.dumps({'request_id':identity(1),'expected_version':1,'retry_paid':True}).encode(),app.RecoverBody)
    with pytest.raises(HTTPException) as caught:app.route_identity('../legacy')
    assert caught.value.status_code==422

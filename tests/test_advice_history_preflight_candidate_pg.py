"""Read-only aggregate observations in an isolated PostgreSQL cluster only."""
import json
from types import SimpleNamespace

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from generic_agents import advice_history_preflight_candidate as candidate
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, Plan, Task, User, Workspace, uid


def test_disabled_and_invalid_target_never_connect():
    class NoConnection:
        url = SimpleNamespace(drivername='postgresql', host='127.0.0.1', port=55486)
        def connect(self):
            raise AssertionError('must not connect')
    assert candidate.inspect_isolated(NoConnection())['state'] == 'disabled'
    assert candidate.inspect_isolated(NoConnection(), [], enabled=True)['state'] == 'unknown'


def test_aggregates_are_read_only_scoped_and_never_a_release_decision(account):
    username, _, workspace_id = account
    role = 'ga_preflight_' + uid().replace('-', '')[:12]
    child_role = 'ga_preflight_' + uid().replace('-', '')[:12]
    with Session(engine()) as db:
        actor = db.scalar(select(User).where(User.username == username))
        other = Workspace(name='合成工作区私密名称')
        db.add(other); db.flush()
        cap = Capability(workspace_id=workspace_id, namespace='synthetic', name='synthetic',
            release_version='1', kind='package', status='draft', content_hash='a'*64, data={})
        db.add(cap); db.flush()
        task = Task(workspace_id=workspace_id, owner_id=actor.id, package_id=cap.id,
            status='draft', title='不应输出的任务标题', data={})
        db.add(task); db.flush()
        plan = Plan(workspace_id=other.id, task_id=task.id, status='review', data={})
        db.add(plan); db.flush()
        db.add_all([
            Audit(workspace_id=other.id, actor_id=actor.id, action='same_goal_advice.synthetic',
                object_id=uid(), data={'task_id':task.id}),
            Audit(workspace_id=workspace_id, actor_id=actor.id, action='same_goal_advice.synthetic',
                object_id=uid(), data={'task_id':uid()}),
            CloudCall(workspace_id=workspace_id, capability_id=cap.id, status='reserved',
                reserved_tokens=1, charged_tokens=1, data={'same_goal_advice_unlimited_v2':{}}),
            CloudCall(workspace_id=workspace_id, capability_id=cap.id, status='reserved',
                reserved_tokens=1, charged_tokens=1, data={'same_goal_advice_unlimited_v2':{
                    'task_id':task.id,'plan_id':plan.id}}),
            CloudCall(workspace_id=workspace_id, capability_id=cap.id, status='reserved',
                reserved_tokens=1, charged_tokens=1, data={}),
        ])
        db.execute(text('CREATE ROLE '+role+' NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT'))
        db.execute(text('CREATE ROLE '+child_role+' NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT'))
        db.execute(text('GRANT UPDATE (status) ON tasks TO '+role))
        db.execute(text('GRANT '+role+' TO '+child_role))
        db.execute(text('ALTER TABLE cloud_model_calls OWNER TO '+role))
        db.commit()
    states=[]
    def check_readonly(conn, cursor, statement, parameters, context, many):
        assert statement.split(maxsplit=1)[0].upper() in {'SET', 'SELECT', 'WITH'}
        if 'plan_cross_workspace_task' in statement:
            raise AssertionError('no interpolated output labels expected')
        if 'SELECT count(*) FROM plan_versions' in statement:
            cursor.execute('SHOW transaction_read_only')
            states.append(cursor.fetchone()[0])
    event.listen(engine(), 'before_cursor_execute', check_readonly)
    try:
        result = candidate.inspect_isolated(engine(), [role, child_role, 'absent_preflight_role', 'advice_test'], enabled=True)
    finally:
        event.remove(engine(), 'before_cursor_execute', check_readonly)
    assert states == ['on']
    assert result['state'] == 'observed' and result['authorizes_send'] is False
    assert result['authorizes_migration'] is False and 'lock_order' in result['unproven']
    assert result['role_counts']['requested'] == 4 and result['role_counts']['missing'] == 1
    assert result['role_counts']['elevated'] >= 1 and result['role_counts']['table_or_column_write'] >= 3
    assert result['role_counts']['owner_or_member'] >= 2
    assert result['record_counts'] == {'plan_cross_workspace_task':1,'advice_audit_missing_task':1,
        'advice_audit_cross_workspace_task':1,
        'identifiable_v2_calls_without_authoritative_binding_proof':2,
        'all_calls_without_authoritative_binding_proof':3,'v2_calls_missing_declared_links':2,
        'v2_calls_with_cross_workspace_declared_links':1}
    rendered=json.dumps(result,ensure_ascii=False)
    assert username not in rendered and workspace_id not in rendered and role not in rendered and child_role not in rendered
    assert '不应输出的任务标题' not in rendered and '合成工作区私密名称' not in rendered


def test_failed_query_produces_unknown_without_error_details():
    def deny(conn, cursor, statement, parameters, context, many):
        if 'WITH wanted AS' in statement:
            raise PermissionError('PRIVATE-SECRET-DETAIL')
    event.listen(engine(), 'before_cursor_execute', deny)
    try:
        result=candidate.inspect_isolated(engine(), [], enabled=True)
    finally:
        event.remove(engine(), 'before_cursor_execute', deny)
    assert result['state']=='unknown' and result['record_counts'] is None
    assert result['authorizes_migration'] is False and 'PRIVATE' not in repr(result)


def test_empty_data_does_not_prove_safe():
    result=candidate.inspect_isolated(engine(), [], enabled=True)
    assert result['state']=='observed' and result['role_counts']['requested']==0
    assert result['record_counts']['all_calls_without_authoritative_binding_proof']==0
    assert result['authorizes_migration'] is False and result['authorizes_send'] is False
    assert 'writer_inventory' in result['unproven'] and 'file_immutability' in result['unproven']

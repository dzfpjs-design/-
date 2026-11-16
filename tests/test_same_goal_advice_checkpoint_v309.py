"""Actual before_commit/checkpoint control over explicit connection/IAM IO.

These spies inspect the actual Core SQL but do not prove PostgreSQL locking,
account permissions, clock races, commits, paid sending or capture durability.
The separate PG file uses disposable real accounts and transaction fences.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy.dialects import postgresql

from generic_agents import leases
from generic_agents.models import Job, User
from generic_agents.security import Principal
from tests.test_local_goal_renewable_lease import attached, JOB, TOKEN, NOW, no_external

KIND='generate_same_goal_advice_v302'
SCHEMA='same-goal-advice-send-job-v308'
USER='22222222-2222-4222-8222-222222222222'
WORKSPACE='33333333-3333-4333-8333-333333333333'


@pytest.fixture
def scene(monkeypatch):
    db,callbacks=attached(monkeypatch)
    p=Principal(USER,WORKSPACE,'operator','worker')
    class Connection:
        def __init__(self):
            self.row=NS(kind=KIND,workspace_id=WORKSPACE,data={'schema':SCHEMA,'actor_id':USER},
                lease_token=TOKEN,status='running',lease_until=NOW+timedelta(seconds=30))
            self.clock=NOW;self.actor_allowed=True;self.expire_at_update=False;self.events=[];self.sql=[]
        def execute(self,statement):
            sql=str(statement.compile(dialect=postgresql.dialect()));self.sql.append(sql)
            params=statement.compile().params
            if getattr(statement,'is_update',False):
                self.events.append('renew')
                assert all(value in params.values() for value in (JOB,TOKEN,WORKSPACE,USER,KIND,SCHEMA,'running'))
                assert timedelta(seconds=180) in params.values() and leases.LEASE_SECONDS==180
                assert 'jobs.lease_until > clock_timestamp()' in sql
                assert 'UPDATE jobs SET lease_until=' in sql and 'clock_timestamp() +' in sql
                assert {key.key if hasattr(key,'key') else key for key in statement._values}=={'lease_until'}
                if self.expire_at_update:self.clock=self.row.lease_until
                ok=(self.row.lease_token==TOKEN and self.row.status=='running' and self.row.lease_until>self.clock
                    and self.row.kind==KIND and self.row.data.get('schema')==SCHEMA
                    and self.row.workspace_id==WORKSPACE and self.row.data.get('actor_id')==USER)
                if ok:self.row.lease_until=self.clock+timedelta(seconds=180)
                return NS(rowcount=int(ok))
            entity=statement.column_descriptions[0]['entity']
            if entity is User:
                self.events.append('actor');return NS(first=lambda:NS(id=USER) if self.actor_allowed else None)
            assert entity is Job and params['id_1']==JOB
            if 'FOR UPDATE' in sql:
                self.events.append('fence');return NS(first=lambda:self.row)
            self.events.append('family')
            assert KIND in params.values() and SCHEMA in params.values()
            row=self.row if self.row.kind==KIND and self.row.data.get('schema')==SCHEMA else None
            return NS(first=lambda:row)
        def scalar(self,statement):
            assert 'clock_timestamp' in str(statement);return self.clock
    connection=Connection();db.connection_value=connection;db.info['worker_actor']=p
    return NS(db=db,callbacks=callbacks,connection=connection,p=p,
        commit=lambda:callbacks['before_commit'](db))


def test_original_fence_and_actual_actor_precede_single_core_only_renewal(scene):
    t=scene;row=t.connection.row;before=copy.deepcopy(row.data);retained_deadline=row.lease_until
    retained=Job(id=JOB,lease_token=TOKEN,status='running',lease_until=retained_deadline,data=copy.deepcopy(before))
    t.commit()
    assert t.connection.events==['fence','actor','family','renew']
    assert row.lease_until==NOW+timedelta(seconds=180) and row.lease_token==TOKEN and row.status=='running'
    assert row.data==before and retained.lease_until==retained_deadline and retained.status=='running'
    assert leases.LEASE_SECONDS==180 and leases.HEARTBEAT_SECONDS==30


@pytest.mark.parametrize('mode',['no_actor','fake_actor','old_kind','wrong_schema','timer','timer_hash'])
def test_other_families_or_unowned_scopes_cannot_receive_checkpoint_extension(scene,mode):
    t=scene;before=t.connection.row.lease_until
    if mode=='no_actor':t.db.info.pop('worker_actor')
    if mode=='fake_actor':t.db.info['worker_actor']=NS(user_id=USER,workspace_id=WORKSPACE,role='operator')
    if mode=='old_kind':t.connection.row.kind='execute_full_semantic_graph_node'
    if mode=='wrong_schema':t.connection.row.data['schema']='same-goal-advice-prepare-job-v302'
    if mode=='timer':t.connection.row.data['time_trigger']=None
    if mode=='timer_hash':t.connection.row.data['time_trigger_hash']=None
    t.commit()
    assert t.connection.row.lease_until==before and 'renew' not in t.connection.events


@pytest.mark.parametrize('fault',['expired','equal_expiry','takeover','terminal','revoked','other_actor',
    'other_workspace','changed_fence','expires_during_actor_check'])
def test_stale_owner_clock_or_actor_failure_never_revives_or_relabels_original_job(scene,fault):
    t=scene;row=t.connection.row
    if fault=='expired':row.lease_until=NOW-timedelta(microseconds=1)
    if fault=='equal_expiry':row.lease_until=NOW
    if fault=='takeover':row.lease_token='successor-token'
    if fault=='terminal':row.status='succeeded'
    if fault=='revoked':t.connection.actor_allowed=False
    if fault=='other_actor':row.data['actor_id']='44444444-4444-4444-8444-444444444444'
    if fault=='other_workspace':row.workspace_id='44444444-4444-4444-8444-444444444444'
    if fault=='changed_fence':t.db.info['worker_lease']['lease_token']='other-installed-token'
    if fault=='expires_during_actor_check':t.connection.expire_at_update=True
    before=copy.deepcopy(vars(row))
    expected=leases.ExecutionUnauthorized if fault in {'revoked','other_actor','other_workspace'} else leases.LeaseLost
    with pytest.raises(expected):t.commit()
    assert vars(row)==before
    if fault=='expires_during_actor_check':assert t.connection.events[-1]=='renew'
    else:assert 'renew' not in t.connection.events

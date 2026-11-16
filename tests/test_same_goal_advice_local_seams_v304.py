"""Actual lease/recovery callbacks; SQL is an explicit IO boundary, not PG."""
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from generic_agents import leases, job_recovery
from tests.test_local_goal_renewable_lease import attached,begin,last_job_sql,JOB,TOKEN,no_external
from generic_agents import threshold_policy_execution_v153 as unrelated_policy


@pytest.mark.parametrize('schema,timer',[
    ('same-goal-advice-prepare-job-v302',False),('wrong',False),
    (None,False),('same-goal-advice-prepare-job-v302',True)])
def test_only_real_local_preparation_family_changes_begin_and_always_fences_commit(monkeypatch,schema,timer):
    db,callbacks=attached(monkeypatch)
    db.connection_value.row.kind='prepare_same_goal_advice_v302'
    db.connection_value.row.data={'schema':schema}
    if timer:db.connection_value.row.data['time_trigger']=None
    with leases.local_goal_phase(db,JOB,TOKEN) as phase:
        begin(db,callbacks);assert 'FOR UPDATE' in last_job_sql(db)
        phase.activate()
        if schema=='same-goal-advice-prepare-job-v302' and not timer:
            begin(db,callbacks);assert 'FOR UPDATE' not in last_job_sql(db)
            callbacks['before_commit'](db);assert 'FOR UPDATE' in last_job_sql(db)
        else:
            with pytest.raises(leases.LeaseLost):begin(db,callbacks)


@pytest.mark.parametrize('calls',[[],[NS(status='running')],[NS(status='succeeded')]])
def test_local_recovery_never_recovers_an_unexpected_sdk_as_a_legacy_plan(monkeypatch,calls):
    job=NS(id=JOB,kind='prepare_same_goal_advice_v302',data={})
    monkeypatch.setattr(unrelated_policy,'recover',lambda *args:False)
    class DB:
        def scalars(self,statement):
            return NS(all=lambda:calls)
    if calls:
        before=[call.status for call in calls]
        with pytest.raises(HTTPException) as error:job_recovery.recover_calls(DB(),NS(workspace_id='workspace'),job)
        assert error.value.status_code==409 and [call.status for call in calls]==before
    else:
        assert job_recovery.recover_calls(DB(),NS(workspace_id='workspace'),job)==0

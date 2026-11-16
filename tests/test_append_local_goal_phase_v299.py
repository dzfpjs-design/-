"""Actual lease callbacks at SQL IO boundaries, never a fabricated live lease."""
import pytest
from generic_agents import leases
from tests.test_local_goal_renewable_lease import attached,begin,last_job_sql,JOB,TOKEN,no_external


@pytest.mark.parametrize('kind,schema',[
    ('append_goal_evaluation_v297','goal-evaluation-append-job-v297'),
    ('promote_goal_evaluation_v299','goal-evaluation-promotion-job-v299')])
def test_only_fixed_local_family_changes_begin_lock_and_commit_stays_fenced(monkeypatch,kind,schema):
    db,callbacks=attached(monkeypatch)
    db.connection_value.row.kind=kind
    db.connection_value.row.data={'schema':schema,'task_id':'original-task'}
    with leases.local_goal_phase(db,JOB,TOKEN) as phase:
        begin(db,callbacks);assert 'FOR UPDATE' in last_job_sql(db)
        phase.activate();begin(db,callbacks)
        assert 'FOR UPDATE' not in last_job_sql(db)
        callbacks['before_commit'](db);assert 'FOR UPDATE' in last_job_sql(db)


@pytest.mark.parametrize('data',[{}, {'schema':'wrong'},
    {'schema':'goal-evaluation-append-job-v297','time_trigger':None}])
def test_new_kind_or_json_marker_never_alone_authorizes_reduced_begin_lock(monkeypatch,data):
    db,callbacks=attached(monkeypatch);db.connection_value.row.kind='append_goal_evaluation_v297'
    db.connection_value.row.data=data
    with leases.local_goal_phase(db,JOB,TOKEN) as phase:
        phase.activate()
        with pytest.raises(leases.LeaseLost):begin(db,callbacks)

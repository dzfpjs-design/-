"""Original query/goal constraints against isolated PG and actual localhost supplier."""
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import DataRun,Task,DataGap,Acquisition,ObservationPlan,Entity,now
from generic_agents.goals import snapshot
from generic_agents.worker import process_once
from .test_sensing_selection import supplier,setup,asset,monitor,scan,observe,dispatch
from .test_vertical_slice import explicit_goal
from .test_data_gaps import post,query,gap,drain

def delegated(c,obj,*,mode='allow_new',verification='unverified_extraction',age=60,records=1):
    with Session(engine()) as db:pkg=db.get(Entity,obj['id']).schema_capability_id
    spec=explicit_goal(obj,mode=mode,source='observations');spec['evidence'].update(minimum_verification=verification,
        minimum_records_per_entity=records,time={'mode':'latest','max_age_seconds':age})
    task=post(c,'/api/tasks',{'title':'原目标补证约束','package_id':pkg,'entity_ids':[obj['id']],'goal_spec':spec})
    run=query(c,obj,evidence_requirement=spec['evidence'])
    with Session(engine()) as db:
        row=db.get(DataRun,run['id']);parent=db.get(Task,task['id']);row.data={**row.data,
            'goal_delegation':{'task_id':task['id'],'plan_id':'synthetic-origin','goal_snapshot':snapshot(parent)}};db.commit()
    return task,run

def dispatch_job(c,identifier):
    job=next(j for j in c.get('/api/overview').json()['jobs'] if j['data'].get('acquisition_id')==identifier)
    assert process_once(job['id'])

def test_existing_only_parent_never_borrows_monitor_permission(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);monitor(client,obj,src,[a])
    task,run=delegated(client,obj,mode='existing_only');drain();g=gap(client,run)
    assert g['status']=='needs_existing_evidence' and not supplier['calls']
    assert not client.get('/api/overview').json()['acquisitions']
    assert g['data']['business_task_id']==task['id']
    post(client,'/api/data/gaps/'+g['id']+'/retry',{'expected_version':g['version'],'reason':'重评不扩大原目标'})
    drain();assert gap(client,run)['status']=='needs_existing_evidence' and not supplier['calls']

def test_parent_cancelled_after_selection_blocks_before_http(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);task,run=delegated(client,obj)
    g=gap(client,run)
    from generic_agents.data_gaps import route
    from .test_sensing_selection import principal
    with Session(engine()) as db:assert route(db,principal(m),g['id'])=='succeeded';db.commit()
    acqs=client.get('/api/overview').json()['acquisitions'];assert len(acqs)==1
    current=next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==task['id'])
    post(client,'/api/tasks/'+task['id']+'/cancel',{'expected_version':current['version'],'reason':'停止原业务目标'})
    dispatch_job(client,acqs[0]['id']);assert not supplier['calls']
    with Session(engine()) as db:assert db.get(Acquisition,acqs[0]['id']).status=='cancelled'

def test_parent_confirmation_is_stronger_than_monitor(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);task,run=delegated(client,obj,verification='confirmed')
    from generic_agents.data_gaps import route
    from .test_sensing_selection import principal
    with Session(engine()) as db:route(db,principal(m),gap(client,run)['id']);db.commit()
    acq=client.get('/api/overview').json()['acquisitions'][0];dispatch_job(client,acq['id'])
    obs=observe(client,m,obj,src,acq['id'],{'temperature':27})
    with Session(engine()) as db:assert db.get(Acquisition,acq['id']).status=='awaiting_observation'
    obs=next(o for o in client.get('/api/overview').json()['observations'] if o['id']==obs['id'])
    post(client,'/api/observations/'+obs['id']+'/verify',{'expected_version':obs['version'],'decision':'confirmed','reason':'核对合成温度27'})
    drain();assert gap(client,run)['status']=='resolved'
    with Session(engine()) as db:assert db.get(Acquisition,acq['id']).status=='succeeded'

def test_delegated_and_autonomous_plan_have_separate_original_requirements(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);auto=scan(client,m)[0]
    task,run=delegated(client,obj,verification='confirmed')
    from generic_agents.data_gaps import route
    from .test_sensing_selection import principal
    with Session(engine()) as db:route(db,principal(m),gap(client,run)['id']);db.commit()
    with Session(engine()) as db:
        plans=db.scalars(select(ObservationPlan)).all();assert len(plans)==2
        child=next(p for p in plans if p.data.get('observation_request'))
        assert child.data['observation_request']['query_id']==run['id']
        assert child.data['observation_request']['goal_delegation']['goal_snapshot']['spec']['evidence']['minimum_verification']=='confirmed'

def routed(c,m,run):
    from generic_agents.data_gaps import route
    from .test_sensing_selection import principal
    with Session(engine()) as db:route(db,principal(m),gap(c,run)['id']);db.commit()
    return c.get('/api/overview').json()['acquisitions']

def test_only_original_missing_fields_are_dispatched_under_broader_monitor(client,supplier):
    obj,src=setup(client,supplier,fields=['temperature','humidity']);a=asset(client,obj,src,fields=['temperature','humidity'])
    m=monitor(client,obj,src,[a],fields=['temperature','humidity']);task,run=delegated(client,obj)
    acq=routed(client,m,run)[0];dispatch_job(client,acq['id'])
    assert supplier['calls'][0]['fields']==['temperature']
    with Session(engine()) as db:
        constraints=db.get(Acquisition,acq['id']).data['dispatch_observation_constraints']
        assert constraints['fields']==['temperature'] and constraints['max_age_seconds']==60

def test_cancelled_gap_blocks_prepared_request_without_changing_monitor(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);b=asset(client,obj,src,latency_seconds=40);m=monitor(client,obj,src,[a,b]);task,run=delegated(client,obj)
    acq=routed(client,m,run)[0];g=gap(client,run)
    post(client,'/api/data/gaps/'+g['id']+'/cancel',{'expected_version':g['version'],'reason':'停止原补证'})
    dispatch_job(client,acq['id']);assert not supplier['calls']
    assert next(x for x in client.get('/api/overview').json()['monitor_policies'] if x['id']==m['id'])['status']=='active'
    assert scan(client,m)  # Separately authorized autonomous monitoring is still available.

def test_parent_version_change_stops_prepared_old_request(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);task,run=delegated(client,obj)
    acq=routed(client,m,run)[0]
    with Session(engine()) as db:
        parent=db.get(Task,task['id']);parent.data={**parent.data,'goal_version':parent.data['goal_version']+1};db.commit()
    dispatch_job(client,acq['id']);assert not supplier['calls']
    with Session(engine()) as db:assert db.get(Acquisition,acq['id']).status=='cancelled'

def test_dispatched_work_retains_quality_and_hold_after_parent_cancel(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);task,run=delegated(client,obj,verification='confirmed')
    acq=routed(client,m,run)[0];dispatch_job(client,acq['id'])
    current=next(t for t in client.get('/api/overview').json()['tasks'] if t['id']==task['id'])
    post(client,'/api/tasks/'+task['id']+'/cancel',{'expected_version':current['version'],'reason':'取消后只核对已发动作'})
    obs=observe(client,m,obj,src,acq['id'],{'temperature':27})
    with Session(engine()) as db:assert db.get(Acquisition,acq['id']).status=='awaiting_observation'
    current=next(o for o in client.get('/api/overview').json()['observations'] if o['id']==obs['id'])
    post(client,'/api/observations/'+obs['id']+'/verify',{'expected_version':current['version'],'decision':'confirmed','reason':'只核对已结束原动作温度27'})
    drain();assert len(supplier['calls'])==1
    with Session(engine()) as db:
        assert db.get(Acquisition,acq['id']).status=='succeeded'
        assert db.get(Task,task['id']).status=='cancelled'
        assert db.get(DataGap,gap(client,run)['id']).status=='cancelled'

def test_two_required_samples_need_two_bound_actions_not_one(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);b=asset(client,obj,src,latency_seconds=40)
    m=monitor(client,obj,src,[a,b]);task,run=delegated(client,obj,records=2)
    acq=routed(client,m,run)[0];dispatch_job(client,acq['id']);observe(client,m,obj,src,acq['id'],{'temperature':27})
    assert gap(client,run)['status']!='resolved'
    acqs=client.get('/api/overview').json()['acquisitions'];assert len(acqs)==2
    second=next(a for a in acqs if a['id']!=acq['id'])
    observe(client,m,obj,src,second['id'],{'temperature':27});assert gap(client,run)['status']=='resolved'
    assert len(supplier['calls'])==2

def test_parent_deadline_bounds_plan_and_stops_before_http(client,supplier,monkeypatch):
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);task,run=delegated(client,obj)
    from generic_agents.contracts import digest
    deadline=(now()+timedelta(seconds=10)).isoformat()
    with Session(engine()) as db:
        parent=db.get(Task,task['id']);spec={**parent.data['goal_spec'],'deadline':deadline}
        parent.data={**parent.data,'goal_spec':spec,'goal_hash':digest(spec)}
        row=db.get(DataRun,run['id']);row.data={**row.data,'goal_delegation':{**row.data['goal_delegation'],'goal_snapshot':snapshot(parent)}};db.commit()
    acq=routed(client,m,run)[0]
    with Session(engine()) as db:assert db.scalars(select(ObservationPlan)).one().data['deadline']==deadline
    monkeypatch.setattr('generic_agents.goal_queries.now',lambda:now()+timedelta(seconds=20))
    dispatch_job(client,acq['id']);assert not supplier['calls']
    with Session(engine()) as db:assert db.get(Acquisition,acq['id']).status=='cancelled'

def test_monitor_freshness_cannot_weaken_original_sixty_second_need(client,supplier):
    from .test_vertical_slice import publish,manifest
    from .test_data_gaps import observe as older_observe
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a])
    extractor=publish(client,manifest('tool',name='older_numeric',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[]}))
    older_observe(client,obj,src,extractor,at=now()-timedelta(seconds=120));drain()
    assert scan(client,m)==[]  # Existing observation satisfies only the independent monitor.
    task,run=delegated(client,obj,age=60);acqs=routed(client,m,run);assert len(acqs)==1
    dispatch_job(client,acqs[0]['id']);assert len(supplier['calls'])==1

def test_escalation_returns_to_its_original_goal_not_another_gap(client,supplier):
    from .test_sensing_selection import principal
    from generic_agents.sensing_selection import escalate
    from generic_agents.worker import deliver_events
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a]);first,run1=delegated(client,obj)
    routed(client,m,run1);second,run2=delegated(client,obj);routed(client,m,run2)
    with Session(engine()) as db:
        plan=db.scalar(select(ObservationPlan).where(ObservationPlan.data['observation_request']['query_id'].as_string()==run1['id']))
        escalate(db,principal(m),plan,'synthetic_delay','仅合成原委托超时回执');plan_id=plan.id;db.commit()
    deliver_events()
    with Session(engine()) as db:
        plan=db.get(ObservationPlan,plan_id);assert plan.data['business_task_id']==first['id']
        assert db.get(Task,second['id']).data.get('perception_escalations') is None


def test_manual_recheck_returns_to_saved_constraint_instead_of_stuck_verifying(client,supplier):
    obj,src=setup(client,supplier);a=asset(client,obj,src);monitor(client,obj,src,[a]);task,run=delegated(client,obj,mode='existing_only')
    drain();g=gap(client,run);assert g['status']=='needs_existing_evidence'
    post(client,'/api/data/gaps/'+g['id']+'/recheck',{'expected_version':g['version'],'reason':'复核原需求不改权限'})
    drain();assert gap(client,run)['status']=='needs_existing_evidence' and not supplier['calls']


def test_one_confirmed_existing_sample_does_not_block_a_second_required_sample(client,supplier):
    from .test_vertical_slice import publish,manifest
    from .test_data_gaps import observe as manual_observe
    obj,src=setup(client,supplier);a=asset(client,obj,src);m=monitor(client,obj,src,[a])
    extractor=publish(client,manifest('tool',name='sample_numeric',allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[]}))
    obs=manual_observe(client,obj,src,extractor);drain()
    obs=next(o for o in client.get('/api/overview').json()['observations'] if o['id']==obs['id'])
    post(client,'/api/observations/'+obs['id']+'/verify',{'expected_version':obs['version'],'decision':'confirmed','reason':'原有合成样本确认'})
    task,run=delegated(client,obj,verification='confirmed',records=2);acqs=routed(client,m,run)
    assert len(acqs)==1 and gap(client,run)['status']=='awaiting_observation'
    dispatch_job(client,acqs[0]['id']);second=observe(client,m,obj,src,acqs[0]['id'],{'temperature':27})
    assert gap(client,run)['status']!='resolved'
    second=next(o for o in client.get('/api/overview').json()['observations'] if o['id']==second['id'])
    post(client,'/api/observations/'+second['id']+'/verify',{'expected_version':second['version'],'decision':'confirmed','reason':'第二条独立请求样本确认'})
    drain();assert gap(client,run)['status']=='resolved' and len(supplier['calls'])==1

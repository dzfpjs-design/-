"""Current public PG fixtures exercise only the changed V134/V247 boundary.

The fixture is a genuinely published manual business, explicitly not a V134
executable graph. An unissued preview hash is never accepted or fabricated.
The observation/extraction/ACK/draft case uses actual V177 loopback HTTP. No
old report fixture, historical qualification, private port or marker is used;
these checks do not re-verify the frozen full Graph workflow/acceptance journey.
"""
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m,signal_graph_routing_v134 as graph
from generic_agents.contracts import digest
from generic_agents.signal_approval_models_v247 import (SignalApprovalRouteV247 as Route,
    SignalApprovalOccurrenceV247 as Occurrence,SignalApprovalCommandV247 as Command)
from generic_agents.signal_graph_routing_models_v134 import SignalGraphDelivery
from tests.test_signal_approval_routing_pg_v247 import (scene,good,prefix,collect,
    process_pending,ack,occurrence,no_new_effects)
from tests.test_acquisition_ack_settlement_pg_v245 import simulated_supplier
from tests.test_instance_workspace import object_row


def inventory(s):
    with Session(s.bound) as db:
        return {model.__tablename__:{row.id:digest({'version':getattr(row,'version',None),
            'status':getattr(row,'status',None),'data':getattr(row,'data',None),
            'payload_hash':getattr(row,'payload_hash',None)}) for row in db.scalars(select(model).where(
                model.workspace_id==s.workspace))}
            for model in (m.DomainInstance,Route,Occurrence,Command,SignalGraphDelivery,
                m.Task,m.Job,m.Invocation,m.CloudCall,m.Memory,m.Audit)}


def public_graph_command(s,entity_id):
    instance=next(row for row in good(s.client.get('/api/instances')) if row['id']==s.release['instance_id'])
    return {'schema':'signal-graph-routing-enable-command-v134','request_id':str(uuid4()),
        'expected_version':instance['version'],'release_id':s.release['id'],'source_id':s.source['id'],
        'extractor_id':s.extractor['id'],'entity_ids':[entity_id],'business_id':s.business_id,
        'entity_binding':'each_trigger_object','entity_input_fields':[],'graph_inputs':{},'max_tasks':1,
        'goal_spec':{'purpose':'核对原温度资料并保留人工验收','entity_ids':[entity_id],
            'required_fields':[s.temperature],'observation_mode':'existing_only','max_steps':12,'max_tool_calls':2,
            'evidence':{'source':'observations','minimum_verification':'unverified_extraction',
                'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}},
            'subgoals':[{'id':'check','description':'核对原资料','criterion_ids':['enough','human']}],
            'criteria':[{'id':'enough','kind':'evidence_sufficient','description':'所需原字段资料充分'},
                {'id':'human','kind':'human_confirmation','description':'明确人工验收'}]},
        # Intentionally unissued: this test proves the changed guard and the
        # unchanged manual-vs-Graph rejection, never a successful Graph grant.
        'preview_hash':'0'*64,'confirm_graph_execution':True,
        'reason':'只验证当前真实来源的互斥检查，不冒充已取得完整图执行资格'}


def observe_original_preview(monkeypatch):
    calls=[];original=graph.preview
    def observed(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(graph,'preview',observed)
    return calls


def graph_path(s):return '/api/workspace/instances/'+s.release['instance_id']+'/signal-graph-routing-v134/enable'


def test_actual_graph_enable_is_stopped_by_same_scope_real_approval_grant_before_preview(scene,monkeypatch):
    s=scene;calls=observe_original_preview(monkeypatch)
    body=public_graph_command(s,s.entity['id']);before=inventory(s)
    response=s.client.post(graph_path(s),json=body)
    assert response.status_code==409,response.text[:500]
    assert response.json()['detail']=='请先明确暂停异常审批草稿授权，再启用其它自动交接'
    assert calls==[] and inventory(s)==before
    assert s.sim.calls==[]


@pytest.mark.parametrize('scope',['new_object','paused'])
def test_nonoverlap_or_normal_pause_reaches_real_preview_but_never_promotes_manual_business_to_graph(scene,monkeypatch,scope):
    s=scene;entity_id=s.entity['id']
    if scope=='new_object':entity_id=object_row(s.client,s.release)['id']
    else:
        state=good(s.client.get(prefix(s)))
        good(s.client.post(prefix(s)+'/'+s.route['id']+'/pause',json={'schema':'signal-approval-pause-v247',
            'request_id':str(uuid4()),'expected_version':state['instance_version'],
            'reason':'明确暂停新增审批草稿，再核对其它交接配置'}))
    calls=observe_original_preview(monkeypatch)
    body=public_graph_command(s,entity_id);before=inventory(s)
    response=s.client.post(graph_path(s),json=body)
    assert response.status_code==409,response.text[:500]
    assert response.json()['detail']=='请选择当前已启用的完整业务流程'
    assert calls==[True] and inventory(s)==before
    assert s.sim.calls==[]


def test_real_approval_occurrence_is_seen_by_old_source_family_guard_and_normal_signal_task_cannot_duplicate_it(scene):
    s=collect(scene)
    with Session(s.bound) as db:
        original=db.get(m.Observation,s.observation_id)
        from generic_agents.signal_routing import source_identity
        assert graph.has_source_family(db,s.workspace,s.release['instance_id'],source_identity(original))
        signal=db.get(m.PerceptionSignal,s.signal_id)
        version,basis_hash=signal.version,signal.data['basis_hash']
    before=inventory(s)
    response=s.client.post('/api/workspace/instances/'+s.release['instance_id']+'/tasks',json={
        'request_id':str(uuid4()),'release_id':s.release['id'],'business_id':s.business_id,
        'entity_ids':[s.entity['id']],'title':'尝试手动交接同一已登记异常',
        'signal_id':s.signal_id,'signal_version':version,'signal_basis_hash':basis_hash})
    assert response.status_code==409,response.text[:500]
    assert '原登记已有完整流程交接记录' in response.json()['detail']
    assert inventory(s)==before
    ack(s);process_pending(s)
    saved=occurrence(s);assert saved['status']=='approval_draft' and saved['task_id']
    with Session(s.bound) as db:
        assert len(db.get(Route,s.route['id']).data['task_ids'])==1
        assert len(list(db.scalars(select(m.Task.id).where(m.Task.workspace_id==s.workspace))))==2
        assert not list(db.scalars(select(SignalGraphDelivery.id).where(SignalGraphDelivery.workspace_id==s.workspace)))
    no_new_effects(s)

"""Topology guards for formal replacement proof; upstream ORM proof has its own integration checks."""
import copy
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from generic_agents import formal_continuation_proof_v346 as proof

@pytest.fixture
def graph(monkeypatch):
    state={'status':'running','error':None,'goal_receipts':[],'ready_human_ids':[],'eligible_ability_ids':['retired'],'activations':{
      'done':{'id':'done','node_id':'source','status':'completed'},
      'route':{'id':'route','node_id':'branch','status':'completed'},
      'retired':{'id':'retired','node_id':'reasoning','status':'ready'}}}
    graph={'nodes':[{'id':'source','kind':'ability'},{'id':'branch','kind':'decision'},{'id':'reasoning','kind':'ability'}]}
    task=NS(status='workflow_waiting',data={'graph_runtime':state,'graph_jobs':{'done':{},'retired':{}}})
    monkeypatch.setattr(proof.kernel,'check_run',lambda graph,state:state)
    return task,graph

def test_only_retired_reasoning_may_remain_open(graph):
    task,g=graph;_,_,_,abilities=proof._frontier(task,g,'retired');assert abilities=={'done','retired'}

@pytest.mark.parametrize('fault',[
 lambda t:t.data['graph_runtime']['eligible_ability_ids'].append('done'),
 lambda t:t.data['graph_runtime']['ready_human_ids'].append('foreign'),
 lambda t:t.data['graph_runtime'].update(error='unresolved failure'),
 lambda t:t.data['graph_runtime']['goal_receipts'].append({'fake':'result'}),
 lambda t:t.data['graph_runtime']['activations']['done'].update(status='ready'),
 lambda t:t.data['graph_runtime']['activations']['retired'].update(status='completed'),
 lambda t:t.data['graph_jobs'].pop('done'),
 lambda t:t.data['graph_jobs'].update(foreign={}),
 lambda t:setattr(t,'status','workflow_completed'),
])
def test_other_unfinished_or_forged_graph_nodes_cannot_be_completed_by_replacement(graph,fault):
    task,g=graph;fault(task)
    with pytest.raises(HTTPException) as error:proof._frontier(task,g,'retired')
    assert error.value.status_code==409

"""New sound releases declare storage; unrelated history is not upgraded."""
import copy
import pytest
from generic_agents.native_sound_data_plan import build, verify, declared, KEY


def source(adapter='local_environment_sound'):
    return {'businesses':[{'id':'business-A','name':'声音核查','flow':{'steps':[
        {'id':'listen','execution':{'adapter':adapter,'capability_id':'cap-A',
         'capability_hash':'a'*64,'release_version':'1.0.1','agent_role':'perception',
         'output_schema':{'type':'object','properties':{'sound_result':{'type':'object'}}}}}]}}]}


def test_unrelated_history_is_not_rewritten():
    s=source('local_audio');before=copy.deepcopy(s)
    assert build(s) is None and verify(s) is None and s==before


def test_new_release_requires_exact_automatic_declaration():
    s=source();plan=build(s)
    with pytest.raises(ValueError):verify(s)
    s[KEY]=plan
    assert verify(s)==plan
    assert declared(s,'business-A','cap-A','a'*64)[1][0]['candidate_only'] is True
    assert plan['requires_independent_ack'] and not plan['field_truth_verified']
    assert plan['recording_time_unknown_is_preserved']


@pytest.mark.parametrize('change', ['remove','permission','capability','output','business'])
def test_plan_or_workflow_drift_fails_closed(change):
    s=source();s[KEY]=build(s)
    if change=='remove':s.pop(KEY)
    elif change=='permission':s[KEY]['deletion_authorized']=True
    elif change=='capability':s['businesses'][0]['flow']['steps'][0]['execution']['capability_hash']='b'*64
    elif change=='output':s['businesses'][0]['flow']['steps'][0]['execution']['output_schema']['type']='array'
    else:s['businesses'][0]['name']='其它业务'
    with pytest.raises(ValueError):verify(s)


def test_no_retroactive_marker_and_no_arbitrary_capability_declaration():
    s=source('local_audio');s[KEY]={'schema':'fake'}
    with pytest.raises(ValueError):verify(s)
    s=source();s[KEY]=build(s)
    with pytest.raises(ValueError):declared(s,'business-A','other-cap','a'*64)


def test_graph_execution_is_declared_with_its_actual_node():
    s=source();pin=s['businesses'][0]['flow']['steps'][0]['execution']
    s['semantic_runtime_plan']={'graph_adoptions':[{'binding':{'business_name':'声音核查',
        'graph':{'nodes':[{'id':'node-listen','source_step_id':'listen','kind':'ability','execution':pin}]}}}]}
    s[KEY]=build(s)
    assert verify(s)['bindings'][0]['node_id']=='node-listen'


def test_duplicate_execution_binding_is_rejected():
    s=source();s['businesses'][0]['flow']['steps']*=2
    with pytest.raises(ValueError):build(s)

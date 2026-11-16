"""Pure registered-contract recipe and real graph-kernel transitions.

Registry pins, actors and producer receipts here are explicit synthetic test
values. They prove topology/input/entry boundaries, never registry authority,
leased tool execution, real IAM or perception ACK; Root verifies those in PG.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import authored_image_workflow_graph_v243 as image
from generic_agents import authored_workflow_graph_v208 as document
from generic_agents import authored_workflow_runtime_v208 as runtime
from generic_agents import business_automation_admission as admission
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import workflow_data_quality_v219 as quality
from generic_agents import workflow_quality_archive_v220 as archive
from generic_agents.contracts import digest
from tests.test_workflow_data_quality_v219 import outcome


def scene(count=3, name='通用图像原件复核🙂'):
    actor_ids = {operation:str(uuid4()) for operation in image.OPERATIONS}
    roles = {image.tool_role(name, operation):{'user_id':actor_ids[operation],
        'username':'公开合成工具执行人', 'membership_role':'operator'} for operation in image.OPERATIONS}
    steps = []
    for index in range(count):
        role = '原人工岗位'+str(index+1)
        roles[role] = {'user_id':str(uuid4()),'username':'公开合成复核人','membership_role':'operator'}
        steps.append({'id':'original_'+str(index), 'name':'原人工核查'+str(index+1),
            'purpose':'保留原职责及输入依据', 'agent':'人工确认','role':role,'output':'原人工结论'+str(index+1),
            'condition':'','elseId':'end','nextId':'original_'+str(index+1) if index+1 < count else 'end'})
    flow = {'trigger':'明确人工或已批准的自动入口','output':'技术检查结果及原人工回执', 'steps':steps}
    manifests = {'quality':quality.manifest(quality.QUALITY), 'dedup':quality.manifest(quality.DEDUP),
        'report':archive.manifest()}
    pins = {}
    for operation, manifest in manifests.items():
        data = manifest.model_dump(mode='json')
        cap = NS(id=str(uuid4()),content_hash=digest(data),release_version=data['version'],data=data)
        pins[operation] = document.capability_pin(cap,'data')
    return NS(name=name,flow=flow,roles=roles,pins=pins,actors=actor_ids)


def built(s): return image.build_graph(s.flow,s.roles,s.pins,s.actors,business_name=s.name)


def test_server_recipe_preserves_original_three_human_steps_and_pins_real_tool_contracts():
    s = scene(); before = copy.deepcopy(vars(s)); result = built(s)
    assert vars(s) == before and result['template'] == image.TEMPLATE
    graph = result['graph']; ids = [node['id'] for node in graph['nodes']]
    humans = [row for row in graph['nodes'] if row['kind'] == 'human']
    assert ids[:2] == ['quality','dedup'] and ids[-2:] == ['report','end']
    assert [row['source_step_id'] for row in humans] == [row['id'] for row in s.flow['steps']]
    assert [row['actor_id'] for row in humans] == [s.roles[row['role']]['user_id'] for row in s.flow['steps']]
    assert [row['output_schema']['properties']['result']['title'] for row in humans] == [row['output'] for row in s.flow['steps']]
    assert graph['input_schema']['required'] == ['task_image_ids','entity_ids']
    assert graph['input_schema']['properties']['task_image_ids']['minItems'] == 1
    assert graph['input_schema']['properties']['task_image_ids']['maxItems'] == 3
    assert graph['subscriptions'] == [] and graph['loops'] == []
    assert image.check_graph(result,s.flow,s.roles,s.pins,s.actors,business_name=s.name) == result


def finish(graph, state, node_id, output):
    activation = next(row for row in state['activations'].values() if row['node_id'] == node_id and row['status'] == 'ready')
    actor = next(row['actor_id'] for row in graph['nodes'] if row['id'] == node_id)
    return kernel.complete(graph,state,activation['id'],output,'explicit-complete-'+activation['id'],
        actor,activation['version'],activation['epoch'],10)


def test_real_kernel_orders_producers_and_humans_before_technical_report_without_autoapproval(tmp_path):
    s = scene(); value = built(s); graph = value['graph']
    state = kernel.new_run(graph,{'task_image_ids':[str(uuid4())],'entity_ids':[str(uuid4())]},10)
    first = state['eligible_ability_ids'][0]
    assert state['activations'][first]['node_id'] == 'quality' and state['ready_human_ids'] == []
    assert kernel.ability_inputs(graph,state,first) == {'scope_ref':quality.scope_ref()}
    produced = outcome(tmp_path)
    state = finish(graph,state,'quality',produced)
    second = state['eligible_ability_ids'][0]
    assert state['activations'][second]['node_id'] == 'dedup'
    assert kernel.ability_inputs(graph,state,second) == {'scope_ref':quality.scope_ref(),'quality_report':produced['report']}
    duplicate = copy.deepcopy(produced)
    duplicate['report']['operation'] = 'deduplicate'
    duplicate['report']['producer']['node_id'] = 'dedup'
    duplicate['report']['producer']['job_id'] = str(uuid4())
    duplicate['report']['report_hash'] = digest({key:val for key,val in duplicate['report'].items() if key != 'report_hash'})
    state = finish(graph,state,'dedup',duplicate)
    for position, mapped in enumerate(value['step_mapping']):
        assert state['eligible_ability_ids'] == []
        ready = state['ready_human_ids']
        assert len(ready) == 1 and state['activations'][ready[0]]['node_id'] == mapped['node_id']
        assert not any(row['node_id'] == 'report' for row in state['activations'].values())
        state = finish(graph,state,mapped['node_id'],{'result':'真实图内人工步骤的合成测试回执'+str(position)})
    report = state['eligible_ability_ids'][0]
    assert state['activations'][report]['node_id'] == 'report' and state['ready_human_ids'] == []
    assert kernel.ability_inputs(graph,state,report) == {'quality_report':produced['report'],'dedup_report':duplicate['report']}
    assert state['status'] == 'running'  # The real report worker has not run.


@pytest.mark.parametrize('count', [1,16])
def test_normal_human_stage_count_is_configured_instead_of_hardcoded(count):
    s = scene(count,name='另一个通用业务')
    assert len(built(s)['step_mapping']) == count


@pytest.mark.parametrize('fault', ['extra_port','missing_port','http','role','boolean_retry','loose_input',
    'loose_output','arbitrary_argument','arbitrary_binding','same_capability','missing_human','wrong_actor','reserved_post','branch'])
def test_recipe_never_accepts_arbitrary_tools_bindings_or_rewrites_human_authority(fault):
    s = scene()
    if fault == 'extra_port': s.pins['execute_url'] = copy.deepcopy(s.pins['quality'])
    elif fault == 'missing_port': s.pins.pop('report')
    elif fault == 'http': s.pins['quality']['adapter'] = 'http'
    elif fault == 'role': s.pins['quality']['agent_role'] = 'business'
    elif fault == 'boolean_retry': s.pins['quality']['max_attempts'] = True
    elif fault == 'loose_input': s.pins['quality']['input_schema'] = {'type':'object'}
    elif fault == 'loose_output': s.pins['report']['output_schema'] = {'type':'object'}
    elif fault == 'arbitrary_argument': s.pins['quality']['arguments'] = {'scope_ref':{'path':'arbitrary-file'}}
    elif fault == 'arbitrary_binding': s.pins['dedup']['input_bindings'] = {'quality_report':'client_report'}
    elif fault == 'same_capability': s.pins['report']['capability_id'] = s.pins['quality']['capability_id']
    elif fault == 'missing_human': s.roles.pop(s.flow['steps'][0]['role'])
    elif fault == 'wrong_actor': s.actors['quality'] = str(uuid4())
    elif fault == 'reserved_post': s.flow['steps'][0]['role'] = image.tool_role(s.name,'quality')
    elif fault == 'branch': s.flow['steps'][0]['condition'] = '需要分支'
    before = copy.deepcopy(vars(s))
    with pytest.raises(ValueError): built(s)
    assert vars(s) == before


@pytest.mark.parametrize('fault', ['skip_human','report_source','constant_report','extra_input','retag_template','change_mapping'])
def test_cold_structure_check_detects_resealed_or_type_valid_pipeline_changes(fault):
    s = scene(); value = built(s)
    if fault == 'skip_human': next(edge for edge in value['graph']['edges'] if edge['from'] == 'dedup')['to'] = 'report'
    elif fault == 'report_source': value['graph']['references'][image.DEDUP_REF]['node_id'] = 'quality'
    elif fault == 'constant_report': next(node for node in value['graph']['nodes'] if node['id'] == 'report')['execution']['arguments']['dedup_report'] = {}
    elif fault == 'extra_input': value['graph']['input_schema']['properties']['path'] = {'type':'string'}
    elif fault == 'retag_template': value['template'] = document.TEMPLATE
    else: value['step_mapping'][0]['role'] = '另一个岗位'
    with pytest.raises(ValueError): image.check_graph(value,s.flow,s.roles,s.pins,s.actors,business_name=s.name)


def binding_for_entry(triggers):
    return {'schema':image.BINDING,'template':image.TEMPLATE,'binding_hash':'b'*64,
        'automation':{'policy':{'triggers':triggers}}}


@pytest.mark.parametrize('triggers', [['新观测到达'],['定时运行'],[],None,'人工发起'])
def test_manual_task_cannot_borrow_automatic_only_image_entry(triggers):
    with pytest.raises(HTTPException) as error:
        runtime.require_task_start(binding_for_entry(triggers),verified_anomaly_handoff=False)
    assert error.value.status_code == 409


def test_manual_entry_requires_confirmed_manual_policy_and_automatic_entry_requires_real_private_token():
    manual = binding_for_entry(['人工发起','新观测到达'])
    runtime.require_task_start(manual,verified_anomaly_handoff=False)
    with pytest.raises(HTTPException): runtime.require_task_start(manual,verified_anomaly_handoff=True)
    with pytest.raises(HTTPException): runtime.require_task_start(manual,verified_anomaly_handoff=False,automation_handoff=True)
    from tests.test_business_automation_admission_v242 import synthetic_token
    _, token = synthetic_token()
    automatic = binding_for_entry(['新观测到达'])
    runtime.require_task_start(automatic,verified_anomaly_handoff=False,automation_handoff=token)
    assert admission._manual(automatic) is True


def test_projection_names_actual_results_and_retains_old_document_template_bytes():
    from tests.test_authored_workflow_v208 import built as document_built
    old = document_built(); old_hash = digest(old)
    s = scene(); result = built(s)
    binding = {'schema':image.BINDING,'template':image.TEMPLATE,'business_name':s.name,'flow':s.flow,**result}
    projection = image.projection(binding)
    assert projection['uses_model'] is False and projection['physical_state_verified'] is False
    assert projection['can_execute'] is False and projection['cloud_authorized'] is False
    assert projection['steps'][-1]['name'] == '生成内部技术报告（质检与去重）'
    assert any('人工办理回执单独保留' in value for value in projection['limitations'])
    assert digest(document_built()) == old_hash and old == document_built()

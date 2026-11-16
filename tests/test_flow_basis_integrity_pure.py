"""Shipping flow-save evidence normalization, without app plugins or services."""
import ast
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType

import pytest

from generic_agents.flow_confirmation import synchronize_flow_basis, synchronize_flow_confirmations
from generic_agents.flow_sources import flow_source_ids


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('flow integrity pure tests forbid services or children')
    for name in ('connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'src/generic_agents/instance_compiler.py').read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'signature_value', 'flow_for'}]
    module = ModuleType('generic_agents.instance_compiler')
    module.flow_source_ids = flow_source_ids
    module.copy = copy
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'shipping-signature', 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module.signature_value


def citation(identity):
    return {'material_id': identity, 'block_id': 'block-' + identity, 'quote': '原文-' + identity}


def configuration():
    step = {'id': 'first', 'name': '核对', 'purpose': '核对原件', 'agent': '人工确认', 'role': '负责人',
            'output': '记录', 'sourceId': 'A', 'citation': '原说明-A', 'citations': [citation('A')],
            'inferenceReason': '模型解释-A', 'nextId': 'end'}
    flow = {'trigger': '人工', 'output': '结论', 'citations': [citation('C')], 'inferenceReason': '业务解释-C',
            'steps': [step, {**copy.deepcopy(step), 'id': 'second', 'sourceId': 'C', 'citations': [citation('C')]}]}
    return {'instanceProfile': {'domain': '核查', 'goal': '核对资料'},
            'materials': [{'id': identity, 'name': identity + '.txt', 'hash': 'hash-' + identity} for identity in 'ABC'],
            'flows': {'核查': flow}}


def test_old_or_forged_client_stamp_cannot_confirm_hidden_old_citations(isolate_runtime):
    before = configuration(); proposed = copy.deepcopy(before); flow = proposed['flows']['核查']
    flow['steps'][0].update(sourceId='B', citation='人工填写的新依据')
    # The signature is internally valid for the stale payload, even when a client
    # deliberately recreates it after editing. Server cleanup must invalidate it.
    flow['confirmedSignature'] = json.dumps(isolate_runtime(proposed, flow), ensure_ascii=False)
    flow['confirmedAt'] = 'old-or-forged-client'
    saved = synchronize_flow_confirmations(before, proposed)['flows']['核查']; current = saved['steps'][0]
    assert saved['confirmedSignature'] is None and saved['confirmedAt'] is None
    assert current['sourceId'] == 'B' and current['citation'] == '人工填写的新依据'
    assert current['citations'] == [] and current['inferenceReason'] == '' and current['basisKind'] == 'manual'
    assert current['basisHistory'][0]['citations'] == [citation('A')]
    assert current['basisHistory'][0]['inferenceReason'] == '模型解释-A'
    assert saved['steps'][1] == before['flows']['核查']['steps'][1]
    assert saved['citations'] == [citation('C')] and saved['inferenceReason'] == '业务解释-C'
    assert flow_source_ids(saved) == ['B', 'C']
    assert before == configuration()


def test_current_ui_can_confirm_the_normalized_basis_and_read_it_back(isolate_runtime):
    before = configuration(); proposed = copy.deepcopy(before); flow = proposed['flows']['核查']
    flow['steps'][0].update(sourceId='B', citation='新人工说明')
    proposed['flows']['核查'] = synchronize_flow_basis(before['flows']['核查'], flow)
    flow = proposed['flows']['核查']; flow['confirmedAt'] = 'reviewed'
    flow['confirmedSignature'] = json.dumps(isolate_runtime(proposed, flow), ensure_ascii=False)
    saved = synchronize_flow_confirmations(before, proposed)
    assert saved == proposed
    assert synchronize_flow_confirmations(saved, json.loads(json.dumps(saved))) == saved


@pytest.mark.parametrize('patch', [{'citation': '新手工说明'}, {'role': '新负责人'}, {'output': '新产出'}])
def test_copied_basis_is_retired_for_only_the_changed_step(patch):
    before = configuration(); proposed = copy.deepcopy(before); flow = proposed['flows']['核查']
    flow['steps'][0].update(patch)
    saved = synchronize_flow_confirmations(before, proposed)['flows']['核查']
    assert saved['steps'][0]['citations'] == [] and saved['steps'][0]['inferenceReason'] == ''
    assert saved['steps'][1] == before['flows']['核查']['steps'][1]
    assert saved['citations'] == before['flows']['核查']['citations']


def test_business_facts_retire_only_business_basis_and_preserve_original():
    before = configuration(); proposed = copy.deepcopy(before); proposed['flows']['核查']['trigger'] = '新启动条件'
    saved = synchronize_flow_confirmations(before, proposed)['flows']['核查']
    assert saved['citations'] == [] and saved['inferenceReason'] == ''
    assert saved['basisHistory'][0]['citations'] == [citation('C')]
    assert saved['steps'] == before['flows']['核查']['steps']


def test_new_v6_references_survive_and_source_only_replacement_clears_old_text():
    before = configuration(); old = before['flows']['核查']; proposed = copy.deepcopy(old)
    proposed['steps'][0]['sourceId'] = 'B'
    assert synchronize_flow_basis(old, proposed)['steps'][0]['citation'] == ''
    proposed['steps'][0].update(citation='block-B：原文-B', citations=[citation('B')], inferenceReason='本次模型解释-B')
    saved = synchronize_flow_basis(old, proposed)['steps'][0]
    assert saved['citations'] == [citation('B')] and saved['inferenceReason'] == '本次模型解释-B'
    assert saved['basisHistory'][0]['citations'] == [citation('A')]


def test_history_is_idempotent_bounded_and_not_a_current_cloud_fact():
    from generic_agents.workflow_context import workflow_context
    value = configuration()['flows']['核查']; original = copy.deepcopy(value)
    for index in range(80):
        proposed = copy.deepcopy(value); proposed['steps'][0]['citation'] = f'人工说明{index}'
        value = synchronize_flow_basis(value, proposed)
    assert len(value['steps'][0]['basisHistory']) == 1
    assert synchronize_flow_basis(original, value) == value
    config = configuration(); config.update(businesses=['核查'], flows={'核查': value})
    context = workflow_context(config, [{'material_id': 'A', 'block_id': 'block-A', 'text': '原文-A'}], scrub_text=lambda value: value)
    current = context['核查']['steps'][0]
    assert current['citations'] == [] and current['inference_reason'] == ''
    assert current['citation'] == '人工说明79' and 'basisHistory' not in current
    for index in range(35):
        proposed = copy.deepcopy(value); proposed['steps'][0].update(sourceId='B', citation=f'新依据{index}', citations=[citation(str(index))], inferenceReason=f'新说明{index}')
        value = synchronize_flow_basis(value, proposed)
        proposed = copy.deepcopy(value); proposed['steps'][0]['citation'] = f'人工修改{index}'
        value = synchronize_flow_basis(value, proposed)
    assert len(value['steps'][0]['basisHistory']) == 20
    assert value['steps'][0]['basisHistory'][0]['citations'] == [citation('A')]
    assert value['steps'][0]['basisHistoryOmitted'] > 0


def test_fact_only_edit_cannot_resurrect_original_quote_through_legacy_context():
    from generic_agents.workflow_context import workflow_context
    before = configuration(); proposed = copy.deepcopy(before); proposed['businesses'] = ['核查']
    proposed['flows']['核查']['steps'][0]['purpose'] = '人工修改后的用途'
    saved = synchronize_flow_confirmations(before, proposed); current = saved['flows']['核查']['steps'][0]
    assert current['sourceId'] == '' and current['citation'] == '' and current['citations'] == []
    assert current['basisHistory'][0]['sourceId'] == 'A'
    assert flow_source_ids(saved['flows']['核查']) == ['C']
    context = workflow_context(saved, [{'material_id': 'A', 'block_id': 'block-A', 'text': '原文-A'}], scrub_text=lambda value: value)
    projected = context['核查']['steps'][0]
    assert projected['citation'] == '' and projected['citations'] == [] and projected['inference_reason'] == ''
    assert '原说明-A' not in json.dumps(projected, ensure_ascii=False)


def test_reordering_duplicate_or_irrelevant_metadata_cannot_recertify_stale_refs():
    before = configuration(); old = before['flows']['核查']; old['steps'][0]['citations'].append(citation('C'))
    proposed = copy.deepcopy(old)
    proposed['steps'][0].update(sourceId='B', citation='人工新说明',
                                citations=[{**citation('C'), 'note': 'irrelevant metadata'}, citation('A'), citation('A')])
    current = synchronize_flow_basis(old, proposed)['steps'][0]
    assert current['citations'] == [] and current['sourceId'] == 'B' and current['citation'] == '人工新说明'
    assert current['basisHistory'][0]['citations'] == [citation('A'), citation('C')]


def test_history_overhead_is_rejected_by_final_configuration_limit_without_mutating_previous():
    """A valid incoming payload can grow when original basis is retained."""
    import hashlib
    from fastapi import HTTPException
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'src/generic_agents/setup_api.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'validate_configuration')
    namespace = {'json': json, 'HTTPException': HTTPException}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'shipping-configuration-validation', 'exec'), namespace)
    validate = namespace['validate_configuration']
    before = configuration(); before.update(version=1, resources=[], connections=[], padding='')
    before['flows']['核查']['steps'][0]['citations'][0]['quote'] = '原文证据' * 8000
    encoded = lambda value: json.dumps(value, ensure_ascii=False).encode()
    before['padding'] = 'x' * (2 * 1024 * 1024 - len(encoded(before)) - 100)
    digest = hashlib.sha256(encoded(before)).hexdigest()
    validate(before)
    proposed = copy.deepcopy(before)
    proposed['flows']['核查']['steps'][0].update(sourceId='B', citation='新依据')
    validate(proposed)  # Incoming data fits; the final retained history must also fit.
    final = synchronize_flow_confirmations(before, proposed)
    assert len(encoded(final)) > 2 * 1024 * 1024
    with pytest.raises(HTTPException) as error:
        validate(final)
    assert error.value.status_code == 413
    assert hashlib.sha256(encoded(before)).hexdigest() == digest
    assert before['flows']['核查']['steps'][0]['sourceId'] == 'A'
    assert 'basisHistory' not in before['flows']['核查']['steps'][0]


def test_truncating_old_refs_cannot_make_remaining_old_quote_current_for_manual_change():
    old = configuration()['flows']['核查']; old['steps'][0]['citations'].append(citation('C'))
    proposed = copy.deepcopy(old)
    proposed['steps'][0].update(sourceId='B', citation='人工新说明', citations=[citation('A')])
    assert synchronize_flow_basis(old, proposed)['steps'][0]['citations'] == []

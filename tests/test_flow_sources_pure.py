"""Flow-basis lifecycle regressions using shipping functions and in-memory rows only."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException


SOURCE = Path(__file__).resolve().parents[1] / 'src/generic_agents'


@pytest.fixture(autouse=True)
def forbid_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('flow-basis pure tests forbid services and children')
    for name in ('connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def shipping_functions(filename, names, **values):
    """Execute actual function bodies without importing app/DB initialization."""
    tree = ast.parse((SOURCE / filename).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    namespace = {'__package__': 'generic_agents', 'copy': copy, 'json': json,
                 'HTTPException': HTTPException, 'digest': digest, **values}
    if (SOURCE / 'flow_sources.py').exists():
        from generic_agents.flow_sources import flow_source_ids
        namespace['flow_source_ids'] = flow_source_ids
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE / filename), 'exec'), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


def configuration():
    return {'instanceProfile': {'domain': '巡查', 'goal': '核对资料'}, 'businesses': ['核对'],
            'connections': [], 'resources': [], 'materials': [
                {'id': identity, 'name': identity + '.txt', 'hash': identity * 64,
                 'version': 1, 'note': '', 'kind': '业务资料', 'archived': False}
                for identity in ('a', 'b', 'c')]}


def flow():
    return {'trigger': '人工发起', 'output': '核对结果', 'steps': [
        {'id': 'check', 'name': '核对', 'output': '记录', 'agent': '人工确认', 'role': '操作员',
         'nextId': 'end', 'sourceId': 'a', 'citations': [
             {'material_id': 'a', 'block_id': 'text', 'quote': '原文'},
             {'material_id': 'b', 'block_id': 'text', 'quote': '原文'}]}]}


def test_secondary_citation_material_metadata_changes_confirmation():
    compiler = shipping_functions('instance_compiler.py', ['signature_value'])
    config, value = configuration(), flow()
    before = compiler.signature_value(config, value)
    config['materials'][1]['archived'] = True
    assert compiler.signature_value(config, value) != before
    assert [item['id'] for item in before['sources']] == ['a', 'b']


def test_compiled_material_collection_includes_flow_level_citation():
    compiler = shipping_functions('instance_compiler.py', ['material_ids_for_flow'])
    value = flow(); value['citations'] = [{'material_id': 'c', 'block_id': 'text', 'quote': '原文'}]
    assert compiler.material_ids_for_flow(value) == {'a', 'b', 'c'}


class Query:
    def where(self, *args): return self
    def order_by(self, *args): return self
    def with_for_update(self): return self


def recovery(monkeypatch, value, *, changed='b', missing=None, other_flows=None):
    config = configuration()
    if missing:
        config['materials'] = [m for m in config['materials'] if m['id'] != missing]
    config['flows'] = {'核对': value, **(other_flows or {})}
    historical = SimpleNamespace(data=copy.deepcopy(config), version=1)
    current = SimpleNamespace(data=copy.deepcopy(config), version=2)
    rows = {m['id']: SimpleNamespace(**m, sha256=m['hash'], evidence_id='e-' + m['id'],
                                      status='parsed', data={'blocks': []}) for m in configuration()['materials']}
    if changed: rows[changed].version += 1
    for name, members in {
        'generic_agents.setup_automation': {'synchronize_reviews': lambda before, after: after},
        'generic_agents.setup_understanding': {'names': lambda data: data['businesses']},
    }.items():
        module = ModuleType(name); module.__dict__.update(members); monkeypatch.setitem(sys.modules, name, module)
    def scalar_rows(query):
        from generic_agents.report_configuration_models import ReportConfigurationRecord
        if any(item.get('entity') is ReportConfigurationRecord for item in getattr(query, 'column_descriptions', ())):
            return []
        return SimpleNamespace(all=lambda: list(rows.values()))
    db = SimpleNamespace(scalars=scalar_rows)
    model = SimpleNamespace(workspace_id=None, created_at=None, id=None)
    def view(row, db):
        return {**{key: getattr(row, key) for key in ('id', 'name', 'version', 'note', 'kind', 'archived', 'status')},
                'hash': row.sha256}
    module = shipping_functions('setup_recovery.py', ['recovery_plan'],
        draft=lambda *args: current, check_version=lambda *args: None,
        history_row=lambda *args: historical, validate_configuration=lambda data: None,
        scoped=lambda db, model, identity, p, *args: rows[identity], SetupMaterial=model,
        evidence_bytes=lambda db, p, identity: (SimpleNamespace(sha256=rows[identity[2:]].sha256), b''),
        material_view=view, select=lambda *args: Query())
    _, plan = module.recovery_plan(db, SimpleNamespace(workspace_id='w'),
                                   SimpleNamespace(revision=2, target_revision=1))
    return plan


def test_recovery_reconfirms_secondary_citation(monkeypatch):
    value = confirmed_flow()
    plan = recovery(monkeypatch, value)
    assert plan['flows_requiring_confirmation'] == ['核对']
    assert plan['configuration']['flows']['核对']['confirmedSignature'] is None


def test_source_order_preserves_legacy_repetition_before_all_citations():
    from generic_agents.flow_sources import flow_source_ids
    value = {'steps': [
        {'sourceId': 'a', 'citations': [{'material_id': 'b'}, {'material_id': 'c'}]},
        {'sourceId': 'b', 'citations': [{'material_id': 'a'}, {'material_id': 'd'}]},
        {'sourceId': 'a', 'citations': [{'material_id': 'c'}]},
    ], 'citations': [{'material_id': 'e'}, {'material_id': 'b'}, {'material_id': 'd'}]}
    assert flow_source_ids(value) == ['a', 'b', 'a', 'c', 'd', 'e']


def test_source_ids_are_nonempty_strings_without_normalization():
    from generic_agents.flow_sources import flow_source_ids
    value = {'steps': [{'sourceId': ''}, {'sourceId': None}, {'sourceId': 1},
                      {'sourceId': ' a ', 'citations': [None, {}, {'material_id': ''},
                         {'material_id': 2}, {'material_id': 'a'}]}],
             'citations': [{'material_id': ' a '}, {'material_id': 'a'}]}
    assert flow_source_ids(value) == [' a ', 'a']


@pytest.mark.parametrize('empty_fields', [{}, {'citations': []}, {'inferenceReason': ''},
                                        {'citations': [], 'inferenceReason': ''}])
def test_source_id_only_legacy_signature_retains_exact_structure_and_bytes(empty_fields):
    compiler = shipping_functions('instance_compiler.py', ['signature_value'])
    config = configuration()
    value = {'trigger': '人工发起', 'output': '记录', 'steps': [
        {'sourceId': 'a'}, {'sourceId': 'missing'}, {'sourceId': 'a'}], **empty_fields}
    expected = {'domain': '巡查', 'goal': '核对资料', 'trigger': '人工发起', 'output': '记录',
                'steps': value['steps'], 'sources': [
                    {'id': 'a', 'archived': False, 'missing': False, 'name': 'a.txt', 'hash': 'a' * 64},
                    {'id': 'missing', 'archived': False, 'missing': True},
                    {'id': 'a', 'archived': False, 'missing': False, 'name': 'a.txt', 'hash': 'a' * 64}]}
    assert json.dumps(compiler.signature_value(config, value), ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


@pytest.mark.parametrize('extra', [
    {'citations': [{'material_id': 'c', 'block_id': 'text', 'quote': '原文'}]},
    {'inferenceReason': '按原文推断'},
    {'citations': [{'material_id': 'c', 'block_id': 'text', 'quote': '原文'}], 'inferenceReason': '按原文推断'},
])
def test_flow_level_evidence_and_inference_are_both_bound_when_present(extra):
    compiler = shipping_functions('instance_compiler.py', ['signature_value'])
    value = {**flow(), **extra}; result = compiler.signature_value(configuration(), value)
    assert result['citations'] == extra.get('citations', [])
    assert result['inferenceReason'] == extra.get('inferenceReason', '')
    changed = copy.deepcopy(value); changed['inferenceReason'] = '修改推断'
    assert compiler.signature_value(configuration(), changed) != result


@pytest.mark.parametrize('identity', ['b', 'c'])
@pytest.mark.parametrize('change', ['name', 'hash', 'archived', 'missing'])
def test_every_cited_material_snapshot_affects_confirmation(identity, change):
    compiler = shipping_functions('instance_compiler.py', ['signature_value'])
    value = flow(); value['citations'] = [{'material_id': 'c', 'block_id': 'text', 'quote': '原文'}]
    config = configuration(); before = compiler.signature_value(config, value)
    if change == 'missing': config['materials'] = [m for m in config['materials'] if m['id'] != identity]
    else:
        item = next(m for m in config['materials'] if m['id'] == identity)
        item[change] = True if change == 'archived' else 'changed'
    assert compiler.signature_value(config, value) != before


@pytest.mark.parametrize('kind', ['secondary', 'flow'])
@pytest.mark.parametrize('condition', ['changed', 'missing'])
def test_recovery_reconfirms_all_evidence_families(monkeypatch, kind, condition):
    value = flow(); identity = 'b' if kind == 'secondary' else 'c'
    if kind == 'flow': value['citations'] = [{'material_id': 'c', 'block_id': 'text', 'quote': '原文'}]
    value = confirmed_flow(value)
    plan = recovery(monkeypatch, value, changed=identity if condition == 'changed' else None,
                    missing=identity if condition == 'missing' else None)
    restored = plan['configuration']['flows']['核对']
    assert plan['flows_requiring_confirmation'] == ['核对']
    assert restored['confirmedSignature'] is None and restored['confirmedAt'] is None


def test_unrelated_material_changes_do_not_clear_flow_confirmation(monkeypatch):
    value = confirmed_flow()
    plan = recovery(monkeypatch, value, changed='c')
    assert plan['flows_requiring_confirmation'] == []
    assert plan['configuration']['flows']['核对']['confirmedSignature'] == value['confirmedSignature']


def confirmed_flow(value=None):
    value = copy.deepcopy(value if value is not None else flow())
    compiler = shipping_functions('instance_compiler.py', ['signature_value'])
    value['confirmedSignature'] = json.dumps(compiler.signature_value(configuration(), value), ensure_ascii=False)
    value['confirmedAt'] = 'saved-at'
    return value


def test_recovery_reports_legacy_multi_citation_stamp_without_material_changes(monkeypatch):
    value = flow(); value['steps'][0]['purpose'] = '保留用户手动补充的核对目的'
    value['inferenceReason'] = ''
    value = confirmed_flow(value)
    # Synthetic old format: the former server signature included only sourceId A,
    # while the saved step itself already held the A+B citation list.
    legacy = json.loads(value['confirmedSignature']); legacy['sources'] = legacy['sources'][:1]
    value['confirmedSignature'] = json.dumps(legacy, ensure_ascii=False)
    original = copy.deepcopy(value)
    plan = recovery(monkeypatch, value, changed=None)
    restored = plan['configuration']['flows']['核对']
    assert plan['material_changes'] == []
    assert plan['flows_requiring_confirmation'] == ['核对']
    assert restored['confirmedSignature'] is None and restored['confirmedAt'] is None
    assert {k: v for k, v in restored.items() if k not in {'confirmedAt', 'confirmedSignature'}} == {
        k: v for k, v in original.items() if k not in {'confirmedAt', 'confirmedSignature'}}
    assert value == original


def test_recovery_lists_only_invalid_legacy_stamp_and_keeps_valid_source_id_only(monkeypatch):
    legacy = confirmed_flow()
    old = json.loads(legacy['confirmedSignature']); old['sources'] = old['sources'][:1]
    legacy['confirmedSignature'] = json.dumps(old, ensure_ascii=False)
    valid = flow(); valid['steps'][0].pop('citations')
    valid = confirmed_flow(valid)
    plan = recovery(monkeypatch, legacy, changed=None, other_flows={'合法旧流程': valid})
    assert plan['flows_requiring_confirmation'] == ['核对']
    assert plan['configuration']['flows']['合法旧流程'] == valid


def citation_check(kind='flow', problem=None):
    value = flow(); value['steps'][0]['sourceId'] = ''; value['steps'][0]['citations'] = []
    citation = {'material_id': 'c', 'block_id': 'text', 'quote': '原文'}
    target = value if kind == 'flow' else value['steps'][0]
    target['citations'] = [citation]
    material = SimpleNamespace(id='c', sha256='c' * 64, evidence_id='e-c', archived=False,
                               status='parsed', data={'blocks': [{'id': 'text', 'text': '这是原文依据'}]})
    if problem == 'archived': material.archived = True
    if problem == 'unparsed': material.status = 'pending'
    if problem == 'block': citation['block_id'] = 'unknown'
    if problem == 'quote': citation['quote'] = '未出现在原文中'
    if problem == 'malformed': del citation['quote']
    checked = []
    def scoped(db, model, identity, p):
        if problem in ('missing', 'foreign'): raise HTTPException(404, 'not scoped')
        assert identity == 'c'
        return material
    def evidence(db, p, identity):
        checked.append(identity)
        if problem == 'original': raise HTTPException(409, 'integrity')
    compiler = shipping_functions('instance_compiler.py', ['signature_value', '_citation_issues', 'flow_issues'],
                                  scoped=scoped, evidence_bytes=evidence, SetupMaterial=object())
    config = configuration()
    value['confirmedSignature'] = json.dumps(compiler.signature_value(config, value), ensure_ascii=False)
    value['confirmedAt'] = 'saved-at'
    return compiler.flow_issues(None, object(), config, value), checked


@pytest.mark.parametrize('kind', ['step', 'flow'])
def test_valid_citations_check_original_integrity(kind):
    issues, checked = citation_check(kind)
    assert issues == [] and checked == ['e-c']


@pytest.mark.parametrize('kind', ['step', 'flow'])
@pytest.mark.parametrize('problem', ['missing', 'foreign', 'archived', 'unparsed', 'block', 'quote', 'original', 'malformed'])
def test_late_guard_blocks_invalid_step_and_flow_citations(kind, problem):
    issues, checked = citation_check(kind, problem)
    assert len(issues) == 1
    assert '原文引用' in issues[0] or '引用原件完整性' in issues[0]
    assert '流程或资料已变化' not in issues[0]


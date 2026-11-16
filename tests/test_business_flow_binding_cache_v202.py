"""Pure frozen-binding reuse; IO spies are not PG or execution acceptance.

Actual V195 source/compiler checks are exercised. Separate explicit revision,
Audit and current-authority boundaries prove that a warm pure check never
replaces adoption history or today's source/member/capability/automation gates.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_nested_adoption_v195 as nested
from generic_agents import business_flow_execution_adoption as shared
from generic_agents import report_full_semantic_graph_adoption as current
from tests.test_business_flow_nested_adoption_v195 import (
    fixture as binding_fixture, historical_record, identity, reseal, RecordIO)
from tests.test_report_business_flow_wire_v184 import no_external


@pytest.fixture(scope='module')
def original_binding():
    return binding_fixture()


@pytest.fixture(autouse=True)
def empty_cache(no_external):
    with nested._BINDING_CACHE_LOCK:
        nested._BINDING_CHECKED.clear()
    yield
    with nested._BINDING_CACHE_LOCK:
        nested._BINDING_CHECKED.clear()


@pytest.fixture
def checks(monkeypatch):
    original = nested._check_binding_uncached
    seen = []

    def counted(value):
        seen.append(copy.deepcopy(value))
        return original(value)

    monkeypatch.setattr(nested, '_check_binding_uncached', counted)
    return seen


def reverse_keys(value):
    if type(value) is dict:
        return {key: reverse_keys(child) for key, child in reversed(list(value.items()))}
    if type(value) is list:
        return [reverse_keys(child) for child in value]
    return value


def test_whole_current_value_reuses_pure_success_without_reordering_first_compile(original_binding, checks):
    value = reverse_keys(original_binding)
    result = nested.check_binding(value)
    assert result == value and result is not value
    assert list(checks[0]) == list(value)
    assert list(checks[0]['graph']) == list(value['graph'])
    assert nested.check_binding(copy.deepcopy(original_binding)) == original_binding
    assert len(checks) == 1
    assert len(nested._BINDING_CHECKED) == 1
    assert list(nested._BINDING_CHECKED.values()) == [None]
    assert next(iter(nested._BINDING_CHECKED)) == nested.reviews._bytes(original_binding)


def test_returned_nested_values_cannot_mutate_a_success_proof(original_binding, checks):
    result = nested.check_binding(original_binding)
    result['graph']['nodes'].clear()
    result['source']['qualification']['value']['business']['name'] = 'caller-only mutation'
    second = nested.check_binding(original_binding)
    second['role_bindings'].clear()
    assert nested.check_binding(original_binding) == original_binding
    assert len(checks) == 1 and original_binding['graph']['nodes']


@pytest.mark.parametrize('fault', ['actor', 'pins', 'source_ast', 'graph', 'translation',
    'causal', 'execution', 'entry', 'simulation', 'bool_index', 'float_index'])
def test_warm_proof_cannot_launder_resealed_deep_mutation(original_binding, checks, fault):
    nested.check_binding(original_binding)
    value = copy.deepcopy(original_binding)
    if fault == 'actor':
        next(iter(value['role_bindings'].values()))['user_id'] = identity('cache-other-actor')
    elif fault == 'pins':
        value['capability_pins'].pop(next(iter(value['capability_pins'])))
    elif fault == 'source_ast':
        value['source']['qualification']['value']['business']['name'] = 'changed original AST'
    elif fault == 'graph':
        value['graph']['nodes'].pop()
    elif fault == 'translation':
        value['translation']['source_mapping']['actions'].pop()
    elif fault == 'causal':
        value['causal_bindings']['input_bindings'][0]['sources'].pop()
    elif fault == 'execution':
        node = next(iter(value['execution_bindings']['nodes'].values()))
        node['actor_id'] = identity('cache-other-bound-actor')
    elif fault == 'entry':
        value['entry_policy']['mode'] = 'declared_manual'
    elif fault == 'simulation':
        value['simulation_only'] = False
    elif fault == 'bool_index':
        value['flow_index'] = False
    else:
        value['flow_index'] = 0.0
    value = reseal(value)
    for _ in range(2):
        with pytest.raises((HTTPException, ValueError)):
            nested.check_binding(value)
    assert len(checks) == 3  # Failures are never cached, even after a valid warm entry.
    assert len(nested._BINDING_CHECKED) == 1


def test_original_quote_snapshot_is_part_of_the_key_not_an_implicit_authority(original_binding, checks):
    nested.check_binding(original_binding)
    changed = copy.deepcopy(original_binding)
    # This pure checker preserves source snapshots but does not establish that
    # this text belongs to an actual document. The independent DB source gate
    # remains responsible for that proof on every read/execution.
    changed['source']['material_snapshot'] = {'original_quote': '公开合成原文，尚未核对实际来源'}
    changed = reseal(changed)
    assert nested.check_binding(changed) == changed
    assert len(checks) == 2 and len(nested._BINDING_CHECKED) == 2


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), ('not', 'json'), {1: 'numeric key'}])
def test_strict_json_validation_still_precedes_cache_or_compiler(original_binding, checks, bad):
    nested.check_binding(original_binding)
    changed = copy.deepcopy(original_binding)
    changed['source']['material_snapshot'] = bad
    with pytest.raises(HTTPException):
        nested.check_binding(changed)
    assert len(checks) == 1 and len(nested._BINDING_CHECKED) == 1


def test_key_size_limit_is_utf8_bytes_and_oversize_uses_original_check(original_binding, monkeypatch, checks):
    size = len(nested.reviews._bytes(original_binding))
    monkeypatch.setattr(nested, '_BINDING_CACHE_KEY_BYTES', size - 1)
    for _ in range(2):
        assert nested.check_binding(original_binding) == original_binding
    assert len(checks) == 2 and not nested._BINDING_CHECKED
    monkeypatch.setattr(nested, '_BINDING_CACHE_KEY_BYTES', size)
    nested.check_binding(original_binding)
    nested.check_binding(original_binding)
    assert len(checks) == 3 and len(nested._BINDING_CHECKED) == 1


def test_lru_payload_is_bounded_and_eviction_requires_another_check(original_binding, monkeypatch):
    # This test isolates cache policy; the actual compiler is exercised above.
    checks = []

    def pure_boundary(value):
        checks.append(value['business_name'])
        return copy.deepcopy(value)

    monkeypatch.setattr(nested, '_check_binding_uncached', pure_boundary)
    values = []
    for index in range(nested._BINDING_CACHE_ENTRIES + 1):
        value = copy.deepcopy(original_binding)
        value['business_name'] = '公开缓存策略样本' + str(index)
        value = reseal(value)
        values.append(value)
        nested.check_binding(value)
    assert len(nested._BINDING_CHECKED) == 8
    assert all(type(key) is bytes and len(key) <= 1024 * 1024 for key in nested._BINDING_CHECKED)
    assert sum(map(len, nested._BINDING_CHECKED)) <= 8 * 1024 * 1024
    nested.check_binding(values[-1])
    assert len(checks) == 9
    nested.check_binding(values[0])
    assert len(checks) == 10 and len(nested._BINDING_CHECKED) == 8


@pytest.mark.parametrize('missing', ['audit', 'revision', 'source_qualification'])
def test_warm_pure_check_still_reads_and_rejects_missing_adoption_history(monkeypatch, missing):
    p, row, before, after, event = historical_record()
    calls = []
    monkeypatch.setattr(shared.sources, 'check_shared_qualification', lambda *_: calls.append('source'))
    nested.check_binding(row.data['binding'])
    assert shared.checked_row(RecordIO(before, after, event), p, row) == row.data
    db = RecordIO(before, after, event)
    if missing == 'audit':
        db.events.clear()
    elif missing == 'revision':
        db.snapshots[1] = None
    else:
        def revoked(*_):
            calls.append('revoked_source')
            raise HTTPException(409, 'synthetic source qualification no longer verifiable')
        monkeypatch.setattr(shared.sources, 'check_shared_qualification', revoked)
    with pytest.raises(HTTPException):
        shared.checked_row(db, p, row)
    assert db.reads == ['revision', 'revision']
    assert calls == (['source', 'revoked_source'] if missing == 'source_qualification' else ['source'])


class OriginIO(RecordIO):
    """Actual adoption/revision/Audit SELECT seams, with no authority cache."""
    def __init__(self, row, before, after, event):
        super().__init__(before, after, event)
        self.row = row
        self.origins = 0
        self.audits = 0

    def scalar(self, statement):
        entity = statement.column_descriptions[0]['entity']
        if entity is shared.Adoption:
            self.origins += 1
            return self.row
        assert entity is shared.SetupRevision
        self.reads.append('revision')
        return self.snapshots[(len(self.reads) - 1) % 2]

    def scalars(self, statement):
        assert statement.column_descriptions[0]['entity'] is shared.Audit
        self.audits += 1
        return self.events


@pytest.mark.parametrize('revoked', ['materials', 'members', 'capabilities', 'automation'])
def test_warm_binding_never_reuses_current_authority_or_history(monkeypatch, revoked):
    p, row, before, after, event = historical_record()
    binding = row.data['binding']
    db = OriginIO(row, before, after, event)
    calls = []
    fail = {'gate': None}

    def gate(name, result=None):
        def checked(*args, **kwargs):
            calls.append(name)
            if fail['gate'] == name:
                raise HTTPException(403, 'synthetic current gate revoked')
            return copy.deepcopy(result)
        return checked

    monkeypatch.setattr(shared.sources, 'check_shared_qualification', gate('qualification'))
    monkeypatch.setattr(shared, 'current_configuration', gate('configuration', type('Config', (), {
        'data': {shared.CONFIG_KEY: {binding['business_name']: row.data['origin']}}})()))
    monkeypatch.setattr(shared.sources, 'current_materials', gate('materials'))
    monkeypatch.setattr(current, 'members', gate('members', binding['role_bindings']))
    monkeypatch.setattr(current, 'ability_capabilities', gate('capabilities', binding['capability_pins']))
    monkeypatch.setattr(current, 'automation', gate('automation'))
    nested.check_binding(binding)
    assert shared.verify_origin(db, p, row.data['origin']) == binding
    assert calls == ['qualification', 'configuration', 'materials', 'members', 'capabilities', 'automation']
    fail['gate'] = revoked
    with pytest.raises(HTTPException) as caught:
        shared.verify_origin(db, p, row.data['origin'])
    assert caught.value.status_code == 403 and calls.count(revoked) == 2
    assert db.origins == db.audits == 2 and len(db.reads) == 4

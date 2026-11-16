"""Pure wire/history regressions, not substitutes for Root's actual PG journey.

All sources and accounts are invented. History doubles exercise complete cold
rebuilds; no source/registration/execution authority is mocked into acceptance.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID, uuid5
import pytest
from fastapi import HTTPException
from generic_agents.contracts import digest
from generic_agents import report_full_semantic_graph_adoption as base
from generic_agents import report_full_semantic_graph_adoption_v4 as v4
from generic_agents import report_full_semantic_graph_revision as revision
from generic_agents import report_full_semantic_graph_runtime_v3 as runtime3
from generic_agents import report_full_semantic_graph_runtime_v4 as runtime4
from generic_agents import report_full_semantic_graph_binding_v3 as binding3
from generic_agents.report_full_semantic_graph_revision_models import FullSemanticGraphRevision
from generic_agents.graph_wait_sources_v118 import POLICY
from tests.test_report_full_semantic_graph_revision import root_contract, HistoryDB
from tests.test_report_full_semantic_graph_runtime_v3 import snapshot, reseal_projection
from tests.test_report_full_semantic_graph_binding_v3 import fixture, build
from tests.test_graph_event_observation_sources import contract_fixture
from tests.test_report_full_semantic_schema_v1_pure import uid


def binding4(original, *, sources=None, policy=None):
    return v4.build_binding(original['source_design'], original['source_manifest'], original['source_proof'],
        original['business_name'], original['graph'], original['owner_bindings'], proof_id=original['proof_id'],
        capability_pins=original['capability_pins'], role_bindings=original['role_bindings'],
        event_source_bindings={} if sources is None else sources,
        event_admission_policy=copy.deepcopy(POLICY) if policy is None else policy)


def wait_contract():
    values = fixture(wait=True); contract = contract_fixture()
    subscription = values['graph']['subscriptions'][0]
    subscription.update(event_type=contract['event_kind'], output_schema=copy.deepcopy(contract['output_schema']))
    sources = {subscription['id']: {'producer_id': uid(11901), 'contract_hash': digest(contract),
        'contract': contract, 'scope': {'entity_policy': 'frozen_task_entities',
            'correlation_ref': subscription['correlation_ref'], 'source_policy': 'exact_registered_source'}}}
    return binding4(build(values), sources=sources)


def root4():
    p, root, old_history = root_contract(); before = copy.deepcopy(old_history[3])
    binding = binding4(root.data['record']['binding'])
    original = base.Adopt.model_validate(root.data['command'])
    values = {key: value for key, value in base.preview_body(original).model_dump(mode='json').items() if key != 'schema'}
    preview_body = v4.Preview(schema='report-full-semantic-graph-preview-command-v4', **values,
        event_source_bindings={}, event_admission_policy=copy.deepcopy(POLICY))
    preview = base.make_preview(p, root.handoff_id, preview_body, binding, family=v4)
    body = v4.Adopt.model_validate({**preview_body.model_dump(mode='json'),
        'schema': 'report-full-semantic-graph-command-v4', 'request_id': uid(11902),
        'preview_hash': preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'})
    identity = str(uuid5(UUID(root.handoff_id), v4.ID_NAMESPACE + p.workspace_id + ':' + p.user_id + ':' + str(body.request_id)))
    record = copy.deepcopy(root.data['record'])
    signature = base.command_hash(p, root.handoff_id, body)
    origin = v4.origin_value(identity, binding, command_hash=signature)
    after = copy.deepcopy(before); after[base.CONFIG_KEY] = {binding['business_name']: origin}
    record.update(id=identity, request_id=str(body.request_id), binding=binding, origin=origin,
        command_hash=signature, preview_hash=body.preview_hash, configuration_hash=digest(after))
    record = base.seal({key: value for key, value in record.items() if key != 'record_hash'}, 'record_hash')
    receipt = base.seal({key: record[key] for key in ('id', 'workspace_id', 'actor_id', 'handoff_id',
        'request_id', 'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash')}, 'receipt_hash')
    data = base.seal({'schema': v4.LEDGER, 'record': record, 'command': body.model_dump(mode='json'), 'receipt': receipt}, 'ledger_hash')
    row = NS(**{key: record[key] for key in ('id', 'workspace_id', 'actor_id', 'handoff_id', 'configuration_id', 'request_id')},
        proof_id=binding['proof_id'], business_name=binding['business_name'], version=1, data=data)
    return p, row, {3: before, 4: after}


def successor4(p, parent, before, version):
    original = copy.deepcopy(parent.data['record']['binding'])
    # A subsequent v4 design is a real displayed graph change, not a no-op.
    original['graph']['id'] += '_v4_' + str(version)
    binding = binding4(original)
    values = {'schema': 'report-full-semantic-graph-revision-preview-command-v4',
        'configuration_revision': version, 'configuration_hash': digest(before), 'proof_id': binding['proof_id'],
        'business_name': binding['business_name'], 'graph': binding['graph'], 'owner_bindings': binding['owner_bindings'],
        'role_bindings': {name: actor['user_id'] for name, actor in binding['role_bindings'].items()},
        'external_node_ids': [], 'node_actions': {'b': 'data'}, 'event_source_bindings': {},
        'event_admission_policy': copy.deepcopy(POLICY),
        'parent': {'id': parent.id, 'receipt_hash': parent.data['receipt']['receipt_hash']}}
    preview_body = v4.RevisionPreview.model_validate(values)
    root_id = parent.data['record'].get('root_id', parent.id)
    previous = parent.data['record']['binding']['binding_hash']
    preview = revision.make_preview(p, parent.handoff_id, preview_body, binding,
        root_id=root_id, previous_binding_hash=previous, family=v4)
    body = v4.RevisionAdopt.model_validate({**values, 'schema': 'report-full-semantic-graph-revision-command-v4',
        'request_id': uid(11910 + version), 'preview_hash': preview['preview_hash'],
        'consent': True, 'semantic_mode': 'workspace_workflow_design'})
    identity = revision.revision_id(p, parent.handoff_id, body.request_id, family=v4)
    signature = revision.command_hash(p, parent.handoff_id, body, family=v4)
    origin = v4.origin_value(identity, binding, command_hash=signature)
    after = copy.deepcopy(before); after[base.CONFIG_KEY][binding['business_name']] = origin
    record = base.seal({'schema': v4.REVISION_RECORD, 'id': identity, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'handoff_id': parent.handoff_id, 'configuration_id': parent.configuration_id,
        'request_id': str(body.request_id), 'business_name': binding['business_name'], 'proof_id': binding['proof_id'],
        'binding': binding, 'origin': origin, 'command_hash': signature, 'preview_hash': body.preview_hash,
        'accepted_revision': version + 1, 'configuration_hash': digest(after), 'parent_id': parent.id, 'root_id': root_id,
        'parent_receipt_hash': parent.data['receipt']['receipt_hash'], 'previous_binding_hash': previous}, 'record_hash')
    data = base.seal({'schema': v4.REVISION_LEDGER, 'record': record, 'command': body.model_dump(mode='json'),
        'receipt': revision.make_receipt(record, family=v4)}, 'ledger_hash')
    row = FullSemanticGraphRevision(**{key: record[key] for key in revision._ROW_FIELDS}, version=1, data=data)
    return row, after


def test_v4_preserves_the_exact_original_v3_binding_hash_and_all_source_owners():
    original = build(fixture()); frozen = copy.deepcopy(original)
    value = binding4(original)
    assert value['base_binding_hash'] == original['binding_hash']
    assert value['owner_bindings'] == original['owner_bindings']
    assert v4.check_binding(value) == value and binding3.check_binding(value) == value
    assert original == frozen
    value['event_admission_policy']['sequence']['open'] = 'inclusive'
    with pytest.raises(ValueError): v4.check_binding(value)


def test_explicit_wait_freezes_actual_subscription_envelope_and_scope_without_mutation():
    value = wait_contract(); frozen = copy.deepcopy(value)
    checked = v4.check_binding(value)
    assert checked == frozen and checked is not value
    assert checked['event_source_bindings']['sub']['contract']['output_schema'] == checked['graph']['subscriptions'][0]['output_schema']
    checked['event_source_bindings']['sub']['scope']['entity_policy'] = 'workspace_all'
    assert value == frozen


@pytest.mark.parametrize('change', ['missing-source', 'extra-source', 'producer', 'contract-hash', 'scope',
    'correlation', 'output-envelope', 'policy', 'base-hash', 'unknown-field', 'type-confusion'])
def test_v4_source_inventory_policy_and_original_coverage_are_closed_even_when_self_resealed(change):
    value = wait_contract(); source = value['event_source_bindings']['sub']
    if change == 'missing-source': value['event_source_bindings'].clear()
    elif change == 'extra-source': value['event_source_bindings']['other'] = copy.deepcopy(source)
    elif change == 'producer': source['producer_id'] = 'not-a-registered-uuid'
    elif change == 'contract-hash': source['contract_hash'] = 'f' * 64
    elif change == 'scope': source['scope']['entity_policy'] = 'workspace_all'
    elif change == 'correlation': source['scope']['correlation_ref'] = 'unapproved'
    elif change == 'output-envelope': value['graph']['subscriptions'][0]['output_schema']['additionalProperties'] = True
    elif change == 'policy': value['event_admission_policy']['source_time_verified'] = True
    elif change == 'base-hash': value['base_binding_hash'] = 'f' * 64
    elif change == 'unknown-field': source['allow_historical_backfill'] = True
    else: value['event_admission_policy']['source_time_verified'] = 0
    value['binding_hash'] = digest({key: item for key, item in value.items() if key != 'binding_hash'})
    with pytest.raises(ValueError): v4.check_binding(value)


def test_old_commands_stay_byte_equivalent_and_cannot_acquire_missing_v4_fields():
    p, root, history = root_contract(); before = copy.deepcopy(root.data)
    old = base.Adopt.model_validate(root.data['command'])
    assert old.model_dump(mode='json') == root.data['command']
    assert base.checked_base(HistoryDB([root], history), p, root) == before
    assert set(base.preview_body(old).model_dump(mode='json')) == set(base.Preview.model_fields)
    assert not {'event_source_bindings', 'event_admission_policy'} & set(old.model_dump(mode='json'))
    for key in ('event_source_bindings', 'event_admission_policy'):
        upgraded = {**old.model_dump(mode='json'), 'schema': 'report-full-semantic-graph-command-v4',
            'event_source_bindings': {}, 'event_admission_policy': copy.deepcopy(POLICY)}
        upgraded.pop(key)
        with pytest.raises(ValueError): v4.Adopt.model_validate(upgraded)
    with pytest.raises(ValueError): base.Adopt.model_validate({**old.model_dump(mode='json'), 'event_source_bindings': {}})
    assert root.data == before


@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_v4_requires_separate_strict_consent(value):
    _, row, _ = root4(); command = copy.deepcopy(row.data['command']); command['consent'] = value
    with pytest.raises(ValueError): v4.Adopt.model_validate(command)


@pytest.mark.parametrize('root_factory', [root_contract, root4])
def test_v3_or_v4_root_to_v4_successors_cold_rebuild_the_actual_parent_chain(root_factory, monkeypatch):
    p, root, history = root_factory(); first, after = successor4(p, root, history[4], 4); history[5] = after
    second, after = successor4(p, first, after, 5); history[6] = after
    db = HistoryDB([root, first, second], history)
    monkeypatch.setattr(base, 'lookup_adoption', lambda store, identity, lock=False: store.rows.get(identity))
    frozen = copy.deepcopy((root.data, first.data, second.data, history))
    assert base.checked_base(db, p, root) == root.data
    assert revision.checked(db, p, first) == first.data
    assert revision.checked(db, p, second) == second.data
    assert base.checked(db, p, second) == second.data
    assert frozen == (root.data, first.data, second.data, history)
    monkeypatch.setattr(revision, 'MAX_CHAIN', 2)
    with pytest.raises(HTTPException): revision._checked_parent(db, p, second)


def test_resealed_v4_consent_command_cannot_replace_actual_accepted_history(monkeypatch):
    p, root, history = root_contract(); row, after = successor4(p, root, history[4], 4); history[5] = after
    db = HistoryDB([root, row], history)
    monkeypatch.setattr(base, 'lookup_adoption', lambda store, identity, lock=False: store.rows.get(identity))
    assert revision.checked(db, p, row) == row.data
    record = row.data['record']; owner = NS(workspace_id=p.workspace_id, user_id=p.user_id)
    row.data['command']['node_actions']['b'] = 'observe'
    command = v4.RevisionAdopt.model_validate(row.data['command'])
    preview = revision.make_preview(owner, row.handoff_id, command, record['binding'], root_id=row.root_id,
        previous_binding_hash=record['previous_binding_hash'], family=v4)
    row.data['command']['preview_hash'] = preview['preview_hash']
    command = v4.RevisionAdopt.model_validate(row.data['command'])
    signature = revision.command_hash(owner, row.handoff_id, command, family=v4)
    record.update(command_hash=signature, preview_hash=command.preview_hash,
        origin=v4.origin_value(row.id, record['binding'], command_hash=signature))
    record = base.seal({key: item for key, item in record.items() if key != 'record_hash'}, 'record_hash')
    row.data = base.seal({'schema': v4.REVISION_LEDGER, 'record': record, 'command': command.model_dump(mode='json'),
        'receipt': revision.make_receipt(record, family=v4)}, 'ledger_hash')
    # Its local closed shape is consistent; the actual accepted origin is not.
    assert revision.checked_ledger(row, p) == row.data
    with pytest.raises(HTTPException): revision.checked(db, p, row)
    assert history[5][base.CONFIG_KEY][row.business_name] != record['origin']


def test_old_revision_refuses_a_real_v4_parent_before_new_source_or_execution_authority(monkeypatch):
    p, root, history = root4(); binding = root.data['record']['binding']
    db = HistoryDB([root], history)
    configuration = NS(id=root.configuration_id, version=4, data=copy.deepcopy(history[4]))
    monkeypatch.setattr(base, 'lookup_adoption', lambda store, identity, lock=False: store.rows.get(identity))
    monkeypatch.setattr(base.review, 'context', lambda *args, **kwargs: (configuration,
        {'design': binding['source_design']}, {root.handoff_id: {}}, binding['source_manifest'], None, None))
    def unexpected(*args, **kwargs):
        pytest.fail('v3 revision must reject a v4 parent before checking or granting fresh authority')
    monkeypatch.setattr(base.old, '_formal', unexpected)
    values = {key: root.data['command'][key] for key in base.Preview.model_fields if key != 'schema'}
    values.update(configuration_revision=4, configuration_hash=digest(history[4]))
    body = revision.Preview(schema='report-full-semantic-graph-revision-preview-command-v1', **values,
        parent={'id': root.id, 'receipt_hash': root.data['receipt']['receipt_hash']})
    with pytest.raises(HTTPException) as error: revision.prepare(db, p, root.handoff_id, body)
    assert error.value.status_code == 409


def test_v4_never_reuses_the_old_command_or_revision_hash_domain_for_the_same_nonce():
    p, old, _ = root_contract(); command = copy.deepcopy(old.data['command'])
    newer = v4.Adopt.model_validate({**command, 'schema': 'report-full-semantic-graph-command-v4',
        'event_source_bindings': {}, 'event_admission_policy': copy.deepcopy(POLICY)})
    original = base.Adopt.model_validate(command)
    assert newer.request_id == original.request_id
    assert base.command_hash(p, old.handoff_id, newer) != base.command_hash(p, old.handoff_id, original)
    values = newer.model_dump(mode='json')
    revised = v4.RevisionAdopt.model_validate({**values, 'schema': 'report-full-semantic-graph-revision-command-v4',
        'parent': {'id': old.id, 'receipt_hash': old.data['receipt']['receipt_hash']}})
    assert revision.command_hash(p, old.handoff_id, revised, family=v4) != base.command_hash(p, old.handoff_id, newer)


def projection4():
    value = snapshot(); plan = value['semantic_runtime_plan']; item = plan['graph_adoptions'][0]
    binding = binding4(item['binding'])
    origin = v4.origin_value(item['origin']['adoption_id'], binding, command_hash=item['origin']['command_hash'])
    item = {'origin': origin, 'binding': binding}
    value['businesses'][0][runtime4.BUSINESS_MARKER] = origin
    value['semantic_runtime_plan'] = runtime4.plan_value(plan['designs'], value['businesses'], [item], plan['legacy_plan'], value['roles'])
    return value


def test_mixed_v2_and_v4_snapshot_has_exact_independent_nested_authority():
    value = projection4(); before = copy.deepcopy(value)
    assert runtime4.verify_plan(value) == value['semantic_runtime_plan']
    release = NS(data={'snapshot': value})
    assert runtime4.business_binding(release, value['businesses'][0]) == value['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    assert value == before


@pytest.mark.parametrize('change', ['v3-envelope', 'v3-origin', 'drop-origin', 'missing-source-policy', 'strip-all-v4'])
def test_frozen_v4_plan_cannot_be_downcast_by_outer_hashes_or_marker_rewrites(change):
    value = projection4(); plan = value['semantic_runtime_plan']; item = plan['graph_adoptions'][0]
    if change == 'v3-envelope': plan['schema'] = runtime3.SCHEMA
    elif change == 'v3-origin':
        item['origin']['schema'] = 'report-full-semantic-graph-origin-v3'
        value['businesses'][0][runtime4.BUSINESS_MARKER] = copy.deepcopy(item['origin'])
    elif change == 'drop-origin': value['businesses'][0].pop(runtime4.BUSINESS_MARKER)
    elif change == 'missing-source-policy': item['binding'].pop('event_admission_policy')
    else:
        item['binding']['schema'] = binding3.SCHEMA
        for key in ('event_source_bindings', 'event_admission_policy', 'base_binding_hash'): item['binding'].pop(key)
        item['binding']['binding_hash'] = digest({k: v for k, v in item['binding'].items() if k != 'binding_hash'})
    reseal_projection(value)
    with pytest.raises(ValueError): runtime4.verify_plan(value)
    with pytest.raises(ValueError): runtime3.verify_plan(value)


def dependency_snapshot(source_contract=None):
    """One source design, two complete adopted businesses, with v4 + old v3.

    Every owner row is retained for each independent translation. The sibling
    business is explicitly unresolved in that translation, rather than silently
    omitted, and therefore does not authorize the sibling's execution.
    """
    waiting = fixture(wait=True, ability=True, extra_business=True)
    legacy = fixture(ability=True, extra_business=True)
    assert waiting['design'] == legacy['design']
    contract = contract_fixture() if source_contract is None else copy.deepcopy(source_contract)
    subscription = waiting['graph']['subscriptions'][0]
    subscription.update(event_type=contract['event_kind'], output_schema=copy.deepcopy(contract['output_schema']))
    source_bindings = {subscription['id']: {'producer_id': uid(11901), 'contract_hash': digest(contract),
        'contract': contract, 'scope': {'entity_policy': 'frozen_task_entities',
            'correlation_ref': subscription['correlation_ref'], 'source_policy': 'exact_registered_source'}}}
    current = binding4(build(waiting), sources=source_bindings)
    design = legacy['design']; old_name = design['result']['businesses'][1]['name']
    legacy['business_name'] = old_name; legacy['graph']['id'] += '_legacy_sibling'
    source_ids = dict(zip(design['business_bindings'][0]['step_ids'], design['business_bindings'][1]['step_ids']))
    for node in legacy['graph']['nodes']:
        if node['source_step_id'] is not None: node['source_step_id'] = source_ids[node['source_step_id']]
        if node['kind'] == 'ability':
            node['execution']['capability_id'] = 'capability.synthetic.legacy'
            legacy['capability_pins'][node['id']] = copy.deepcopy(node['execution'])
    templates = {owner['owner_path']: copy.deepcopy(owner) for owner in legacy['owner_bindings']}
    for owner in legacy['owner_bindings']:
        path = owner['owner_path']
        if path.startswith('/businesses/0'):
            owner['resolution'] = 'unresolved'
            for clause in owner['clauses']: clause['targets'] = []
        elif path.startswith('/businesses/1'):
            template = templates[path.replace('/businesses/1', '/businesses/0', 1)]
            owner.update(resolution=template['resolution'], reason=template['reason'],
                clauses=copy.deepcopy(template['clauses']), scope_businesses=[old_name])
        elif owner['resolution'] == 'typed': owner['scope_businesses'] = [old_name]
    previous = build(legacy)
    origins = [v4.origin_value(uid(11951), current, command_hash='a' * 64),
        base.origin_value(uid(11952), previous, command_hash='b' * 64)]
    bindings = [current, previous]
    businesses = [{'id': uid(11960 + index), 'name': binding['business_name'],
        'flow': runtime4.semantic.derive_flows(design)[binding['business_name']], 'issues': [],
        'available': True, 'execution_mode': 'confirmed_graph_workflow',
        runtime4.BUSINESS_MARKER: origins[index]} for index, binding in enumerate(bindings)]
    roles = {name: {key: actor[key] for key in ('user_id', 'username')}
        for binding in bindings for name, actor in binding['role_bindings'].items()}
    plan = runtime4.plan_value([design], businesses,
        [{'origin': origin, 'binding': binding} for origin, binding in zip(origins, bindings)], None, roles)
    return {'businesses': businesses, 'roles': roles, 'semantic_runtime_plan': plan}


def test_v4_dependencies_preserve_all_graph_abilities_and_exact_source_extractor_in_mixed_v3_plan():
    value = dependency_snapshot(); frozen = copy.deepcopy(value)
    current, previous = [item['binding'] for item in value['semantic_runtime_plan']['graph_adoptions']]
    assert current['schema'] == 'report-full-semantic-graph-binding-v4'
    assert previous['schema'] == binding3.SCHEMA
    assert previous['graph']['subscriptions'] == []
    abilities = [node['execution']['capability_id'] for binding in (current, previous)
        for node in binding['graph']['nodes'] if node['kind'] == 'ability']
    assert len(set(abilities)) == 2
    pin = current['event_source_bindings']['sub']
    dependencies = runtime4.dependencies(value)
    assert set(dependencies) == set(abilities) | {pin['contract']['source_id'], pin['contract']['extractor_id']}
    assert len(dependencies) == len(set(dependencies))
    assert pin['producer_id'] not in dependencies
    assert runtime4.verify_plan(value) == value['semantic_runtime_plan']
    assert value == frozen


def test_v4_learned_dependencies_keep_actual_model_capability_and_frozen_version_without_fake_extractor():
    from tests.test_graph_event_native_learned_geo_source_v141 import contract_fixture as learned_contract
    contract = learned_contract(); value = dependency_snapshot(contract); frozen = copy.deepcopy(value)
    current, previous = [item['binding'] for item in value['semantic_runtime_plan']['graph_adoptions']]
    pin = current['event_source_bindings']['sub']
    abilities = {node['execution']['capability_id'] for binding in (current, previous)
        for node in binding['graph']['nodes'] if node['kind'] == 'ability'}
    dependencies = runtime4.dependencies(value)
    assert set(dependencies) == abilities | {contract['source_id'], contract['capability_id']}
    assert len(dependencies) == len(set(dependencies))
    assert pin['producer_id'] not in dependencies and contract['model_trace']['installation_id'] not in dependencies
    assert 'extractor_id' not in pin['contract'] and pin['contract'] == contract
    assert pin['contract_hash'] == digest(contract)
    assert pin['contract']['capability_release_version'] == '1.0.0'
    assert pin['contract']['capability_hash'] == contract['capability_hash']
    assert pin['contract']['model_trace_hash'] == digest(contract['model_trace'])
    assert runtime4.verify_plan(value) == value['semantic_runtime_plan'] and value == frozen


def test_v4_data_requirements_keep_full_source_scope_policy_and_envelope_detached():
    from generic_agents import instance_data_contract_dispatch as data_dispatch
    value = dependency_snapshot()
    value.update(entity_schemas={}, object_types=[], resources=[], sensor_sources=[], materials=[],
        automation={}, equipment=None, general_issues=[], object_origins={}, full_semantic_schema_adoptions=[],
        relation_types={}, relation_origins={})
    frozen = copy.deepcopy(value)
    requirements = data_dispatch.build_requirements(value)
    binding = value['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    pin = binding['event_source_bindings']['sub']
    output = next(item for item in requirements['businesses'][0]['outputs'] if item['kind'] == 'wait')
    assert requirements['schema'] == 'instance-data-requirements-v3'
    assert output['producer_id'] == pin['producer_id'] and output['contract_hash'] == pin['contract_hash']
    assert output['source_scope'] == pin['scope']
    assert output['admission_policy'] == binding['event_admission_policy'] == POLICY
    assert output['output_schema'] == pin['contract']['output_schema']
    assert requirements['dependency_hashes']['semantic_runtime_plan'] == digest(value['semantic_runtime_plan'])
    output['source_scope']['entity_policy'] = 'unapproved'
    output['admission_policy']['deadline']['boundary'] = 'inclusive'
    assert value == frozen

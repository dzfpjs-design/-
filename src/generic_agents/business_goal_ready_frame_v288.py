"""Authenticated decision frame over the independent V288 evaluation and V207 documents.

No query, parser, receiver, model call or execution engine lives here. Capability
and policy snapshots stay local; only explicit scalar records and a metadata
allowlist enter DecisionInput. Historical checking never renews monitor rights.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Annotated, Literal

from pydantic import Field, StrictStr, TypeAdapter

from . import business_decision_contracts_v274 as decision
from . import business_goal_ready_sources_v288 as sources
from . import workflow_graph_scope as scope
from . import workflow_graph as workflow
from . import workflow_service_context_v196 as resense
from . import workflow_task_context_v196 as reader
from . import device_gateway_tools_v269 as managed_tools
from . import device_gateway_sources_v270 as managed_sources
from . import http_observation_mapping_v177 as mapping
from .business_flow_review_store import _bytes
from .contracts import digest
from .models import Capability, Entity, Job, MonitorPolicy, Observation, Task
from .services import validate

PIN_SCHEMA = 'business-goal-ready-frame-pins-v288'
MAX_BYTES = 24 * 1024 * 1024


class SpecPort(decision.Closed):
    field: decision.Ref
    pointer: Annotated[StrictStr, Field(max_length=300)]


class SpecCapability(decision.Closed):
    capability_ref: decision.Ref
    capability_id: decision.ID
    capability_hash: decision.Hash
    release_version: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    delivery: Literal['output', 'requested_observation']
    ports: list[SpecPort] = Field(max_length=30)


class CandidateSpec(decision.Closed):
    candidate_ref: decision.Ref
    proposal: decision.Text
    claims: list[decision.Claim] = Field(max_length=16)
    capability_pins: list[SpecCapability] = Field(min_length=1, max_length=8)


Specs = Annotated[list[CandidateSpec], Field(min_length=1, max_length=16)]


def candidate_specs_schema():
    value = TypeAdapter(Specs).json_schema(); definitions = value.pop('$defs', {})
    def inline(item):
        if type(item) is list:
            return [inline(child) for child in item]
        if type(item) is dict:
            if '$ref' in item:
                reference = item['$ref']; sources.need(reference.startswith('#/$defs/'))
                return inline(copy.deepcopy(definitions[reference.removeprefix('#/$defs/')]))
            return {key: inline(child) for key, child in item.items()}
        return item
    return inline(value)


def _specs(inputs):
    value = TypeAdapter(Specs).validate_python(inputs['candidate_specs'])
    rows = [item.model_dump(mode='json') for item in value]
    sources.need(len({item['candidate_ref'] for item in rows}) == len(rows))
    return rows


def _at(raw):
    value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    sources.need(value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _window(report):
    window = report['resolved_time']; mode = window['mode']
    if mode in {'latest', 'range'}:
        sources.need(window['start'] is not None and window['end'] is not None
            and _at(window['start']) < _at(window['end']))
        return {'start': window['start'], 'end': window['end']}
    if mode == 'as_of':
        at = window['at']; sources.need(at is not None)
        start = (_at(at) - timedelta(seconds=window['max_age_seconds'])).isoformat() if window['max_age_seconds'] else None
        return {'start': start, 'end': at}
    sources.need(mode == 'archive')
    return {'start': None, 'end': None}


def _publication(db, p, cap, *, live):
    sources.need(cap.workspace_id == p.workspace_id and cap.content_hash == cap.validated_hash == digest(cap.data)
        and (not live or cap.status == 'published'))
    events = sources._audits(db, p, 'capability.publish', cap.id)
    sources.need(len(events) == 1 and events[0].data.get('hash') == cap.content_hash)
    event = events[0]
    return {'id': event.id, 'actor_id': event.actor_id, 'data': copy.deepcopy(event.data)}


def _identity(db, p, task, job, cap, inputs, *, live, _server_protocol='business_goal_ready_v288'):
    sources.need(type(_server_protocol) is str and _server_protocol == 'business_goal_ready_v288')
    task = sources.fresh(db, p, Task, task.id); job = sources.fresh(db, p, Job, job.id)
    actual_cap = sources.fresh(db, p, Capability, cap.id)
    actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    transport = scope._job_matches(actual, job, state, activation, node)
    sources.need(actual.id == task.id and job.kind == scope.KIND
        and job.data['task_id'] == task.id and node['actor_id'] == job.data['actor_id']
        and node['execution']['capability_id'] == actual_cap.id == cap.id
        and node['execution']['capability_hash'] == actual_cap.content_hash == cap.content_hash
        and node['execution']['adapter'] == actual_cap.data['execution']['adapter'] == _server_protocol
        and node['execution']['agent_role'] == 'business'
        and node['execution']['release_version'] == actual_cap.release_version
        and sources.same(node['execution']['input_schema'], actual_cap.data['input_schema'])
        and sources.same(node['execution']['output_schema'], actual_cap.data['output_schema'])
        and sources.same(inputs, job.data['inputs']) and sources.same(inputs, activation['inputs_snapshot']['arguments'])
        and digest(inputs) == job.data['input_hash']
        and (not live or (job.status == 'running' and node['actor_id'] == p.user_id
            and state['status'] in {'running', 'waiting'} and activation['status'] == 'ready'
            and activation['id'] in state['eligible_ability_ids'] and activation['epoch'] == state['cancel_epoch']
            and transport.get('phase') == 'running')))
    _publication(db, p, actual_cap, live=live)
    binding = workflow.business_binding(release, business)
    pin = {'task_id': task.id, 'job_id': job.id, 'activation_id': activation['id'],
        'actor_id': node['actor_id'], 'input_hash': digest(inputs), 'graph_hash': state['graph_hash'],
        'capability_id': actual_cap.id, 'capability_hash': actual_cap.content_hash}
    return task, release, binding, pin


def _field_pins(db, p, task, release, frozen_sources, *, live):
    package = sources.fresh(db, p, Capability, task.package_id)
    publication = _publication(db, p, package, live=live)
    sources.need(package.kind == 'domain_package' and package.id == release.package_id
        and package.content_hash == frozen_sources['package_hash'] and release.id == frozen_sources['release_id'])
    data = frozen_sources['data']; record = data['evaluation_record']
    request = record['evaluation_request']; report = record['evaluation_result']['result']; goal = record['goal_snapshot']
    from .goals import snapshot
    sources.need(goal == snapshot(task) and goal['spec']['entity_ids'] == request['entity_ids'] == task.data['entity_ids']
        and goal['spec']['required_fields'] == request['fields']
        and sources.same(report['evidence_requirement'], request['evidence_requirement'])
        and report['evidence_requirement']['source'] in {'observations', 'archive', 'either'}
        and report['status'] == 'sufficient' and record['scope']['goal_snapshot_hash'] == digest(goal)
        and record['scope']['task_id'] == task.id and record['scope']['release_id'] == release.id)
    schemas = package.data['specification']['entity_schemas']; entities = []; fields = []
    object_snapshots = task.data['object_snapshots']
    sources.need([item['id'] for item in object_snapshots] == request['entity_ids'])
    from .entity_release_compat import verify_task_binding_snapshot
    object_bindings = verify_task_binding_snapshot(task, release)
    for identity in request['entity_ids']:
        entity = sources.fresh(db, p, Entity, identity)
        sources.need(entity.entity_type in schemas and entity.namespace == package.namespace)
        if object_bindings is not None:
            definition = next(item['definition'] for item in object_bindings['objects'] if item['entity_id'] == identity)
            sources.need(definition['entity_type'] == entity.entity_type and definition['target_package_id'] == package.id)
        else:
            sources.need(entity.schema_capability_id == package.id)
        archived = [item['entity'] for item in data['fact_origins'] if item['kind'] == 'archive' and item['entity']['id'] == identity]
        for old in archived:
            sources.need(old['entity_type'] == entity.entity_type and old['package_id'] == entity.schema_capability_id)
        entities.append({'id': entity.id, 'entity_type': entity.entity_type,
            'schema_capability_id': entity.schema_capability_id})
        for field in request['fields']:
            prop = schemas[entity.entity_type]['properties'][field]
            mapping._no_refs(prop)
            sources.need(type(prop) is dict and prop.get('type') in {'number', 'integer', 'string', 'boolean'}
                and not {'allOf', 'anyOf', 'oneOf', 'not', 'if'} & set(prop))
            labels = [item for item in release.data['snapshot']['object_types'] if item['key'] == entity.entity_type]
            sources.need(len(labels) == 1)
            labels = [item for item in labels[0]['fields'] if item['key'] == field]
            sources.need(len(labels) == 1)
            units = [unit for unit in (prop.get('unit'), prop.get('x-unit'), labels[0].get('unit')) if unit is not None]
            sources.need(not units or all(unit == units[0] for unit in units))
            fields.append({'entity_id': identity, 'field': field, 'value_type': prop['type'],
                'unit': units[0] if units else None, 'schema': copy.deepcopy(prop)})
    return {'package': {'id': package.id, 'hash': package.content_hash, 'data': copy.deepcopy(package.data), 'publication': publication},
        'release': {'id': release.id, 'instance_id': release.instance_id, 'package_id': release.package_id,
            'configuration_hash': release.configuration_hash, 'snapshot_hash': digest(release.data['snapshot'])},
        'entities': entities, 'fields': fields, 'object_snapshots_hash': digest(object_snapshots),
        'goal_snapshot_hash': digest(goal)}


def _output_port(schema, spec, fields):
    pointer = spec['pointer']; sources.need(pointer.startswith('/') and len(pointer) > 1)
    parts = pointer[1:].split('/'); selected = schema
    for part in parts:
        sources.need('~' not in part.replace('~0', '').replace('~1', ''))
        part = part.replace('~1', '/').replace('~0', '~')
        sources.need(type(selected) is dict and selected.get('type') == 'object'
            and not {'$ref', '$dynamicRef', 'allOf', 'anyOf', 'oneOf', 'not', 'if'} & set(selected)
            and part in selected.get('required', []) and part in selected.get('properties', {}))
        selected = selected['properties'][part]
    mapping._no_refs(selected)
    sources.need(selected.get('type') in {'number', 'integer', 'string', 'boolean'}
        and not {'allOf', 'anyOf', 'oneOf', 'not', 'if'} & set(selected)
        and (parts[-1].replace('~1', '/').replace('~0', '~') == spec['field']
            or selected.get('field_key') == spec['field']))
    unit = selected.get('unit', selected.get('x-unit'))
    sources.need('unit' not in selected or 'x-unit' not in selected or selected['unit'] == selected['x-unit'])
    matching = [row for row in fields if row['field'] == spec['field']]
    for field in matching:
        sources.need(field['value_type'] == selected['type'] and field['unit'] == unit)
    return {'pointer': pointer, 'field': spec['field'], 'value_type': selected['type'], 'unit': unit}


def _capability_pins(db, p, task, frozen_sources, specs, fields, package, *, live, original=None):
    unique = {}; identities = {}; result = []; policies = []
    original = original or {'capabilities': [], 'policies': []}
    # Post-evaluation reasoning proposes no new acquisition context.
    # Capability target declarations remain declarations and grant no rights.
    contexts = []
    for candidate in specs:
        refs = [item['capability_ref'] for item in candidate['capability_pins']]
        sources.need(len(refs) == len(set(refs)))
        for spec in candidate['capability_pins']:
            ref = spec['capability_ref']
            if ref in unique:
                sources.need(sources.same(unique[ref], spec)); continue
            sources.need(spec['capability_id'] not in identities and spec['capability_id'] in package['data']['dependencies'])
            unique[ref] = spec; identities[spec['capability_id']] = ref
            cap = sources.fresh(db, p, Capability, spec['capability_id'])
            publication = _publication(db, p, cap, live=live)
            sources.need(cap.kind in {'tool', 'specialist_model'} and cap.data['kind'] == cap.kind
                and cap.content_hash == spec['capability_hash'] and cap.release_version == spec['release_version'])
            execution = cap.data['execution']; action = None
            if cap.kind == 'specialist_model':
                roles = cap.data['allowed_agent_roles']
                sources.need(type(roles) is list and bool(roles) and len(roles) == len(set(roles))
                    and set(roles) <= {'data', 'perception', 'business'} and type(execution.get('side_effect')) is bool)
                action = 'analysis' if execution['side_effect'] is False else 'operational_action'
            elif managed_tools.applies(cap.data):
                binding = managed_tools._checked_binding(cap.data['specification']['device_gateway_binding'])
                sources.need(cap.data == managed_tools._manifest(binding))
                managed_tools._issued(db, p, cap, binding); managed_tools._historical_grant(db, p, binding)
                if live: managed_tools.validate_tool(db, p, cap, purpose='prepare')
                action = 'information_acquisition' if binding['action'] in {'get_status', 'read_telemetry'} else 'operational_action'
            elif execution.get('adapter') == 'workflow_service' and execution.get('operation') == resense.OP:
                resense.validate_manifest(cap.data); action = 'information_acquisition'
            else:
                sources.need(type(execution.get('side_effect')) is bool)
                action = 'operational_action' if execution['side_effect'] else 'analysis'
            ports = []; seen = set()
            for selected in spec['ports']:
                sources.need((selected['field'], selected['pointer']) not in seen); seen.add((selected['field'], selected['pointer']))
                if spec['delivery'] == 'output':
                    port = _output_port(cap.data['output_schema'], selected, fields)
                else:
                    sources.need(execution.get('adapter') == 'workflow_service' and execution.get('operation') == resense.OP
                        and selected['pointer'] == '/requested_observation/' + selected['field'])
                    resense.validate_manifest(cap.data)
                    matches = []
                    if not contexts:
                        # A sufficient query, or a conflict requiring review,
                        # may have no safe acquisition context. Its registered
                        # fields describe future targets only; they grant no
                        # device rights. Actual conflicts remain in the facts
                        # and cannot pass the decision/planner execution gate.
                        query = frozen_sources['data']['evaluation_record']['evaluation_result']
                        sources.need(query['status'] == 'sufficient')
                        matches = [(field['value_type'], field['unit']) for field in fields
                            if field['field'] == selected['field']]
                    sources.need(matches and len(set(matches)) == 1)
                    port = {**selected, 'value_type': matches[0][0], 'unit': matches[0][1]}
                ports.append({'port_ref': 'port_' + str(len(ports) + 1), **port})
            sources.need(spec['delivery'] != 'requested_observation' or bool(ports))
            result.append({'capability_ref': ref, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
                'release_version': cap.release_version, 'data': copy.deepcopy(cap.data), 'publication': publication,
                'delivery': spec['delivery'], 'ports': ports, 'action_kind': action})
    sources.need(0 < len(result) <= 16)
    return result, policies


def _facts(db, p, frozen_sources, fields):
    from .state_receipt_guard_v245 import check as reception_status
    from .instance_data_receipts import observation_receipt
    from .observation_integrity import checked_report
    data = frozen_sources['data']; report = data['evaluation_record']['evaluation_result']['result']
    records = report['evidence'] + report['observation_evidence']; private = data['fact_origins']
    sources.need(len(records) == len(private) and sorted(digest(row) for row in records) == sorted(digest(item['record']) for item in private))
    facts = []; declared = []; origins = {}
    for item in private:
        record = item['record']; entity = record['entity_id']; kind = item['kind']; source_ref = 'data_packet'
        mqtt_units = None
        if kind == 'observation':
            saved = item['observation']; obs = sources.fresh(db, p, Observation, saved['id'])
            status = reception_status(db, p, obs); receipt = observation_receipt(db, p, obs)
            checked = checked_report(db, p, obs)
            sources.need(status is not None and status['status'] == 'received'
                and sources.same(receipt, item['reception']) and receipt['data_reception']['receipt_hash'] == status['receipt_hash']
                and obs.version == saved['version'] and obs.entity_id == entity and obs.source_id == saved['source_id']
                and sources.same(obs.data, saved['data']) and sources.same(checked, item['report']))
            source_ref = 'observation_' + str(len(declared) + 1)
            source_value={'entity_id': entity, 'observed_at': record['observed_at'], 'attributes': record['attributes'],
                'field_units': item['report'].get('field_units', item['report']['extracted'].get('field_units'))}
            from .mqtt_unit_evidence_v342 import KEY as MQTT_UNIT_KEY
            if MQTT_UNIT_KEY in obs.data:
                # checked_report has verified the marker, original packet,
                # binding and protected capture Audit. Also bind it to the
                # immutable MQTT origin admitted by this particular evaluation.
                from .goal_ack_origin_mqtt_v297 import KIND as MQTT_ORIGIN
                proof=obs.data[MQTT_UNIT_KEY];origin=item.get('origin') or {};pins=origin.get('mqtt_origin') or {}
                sources.need(origin.get('origin_kind')==MQTT_ORIGIN and proof['receipt_id']==pins.get('receipt_id')
                    and proof['binding_id']==pins.get('binding_id') and proof['binding_hash']==pins.get('binding_hash')
                    and proof['input_sha256']==pins.get('input_sha256'))
                mqtt_units=proof['fields']
                selected_units={key:value for key,value in mqtt_units.items() if key in record['attributes']}
                source_value['field_units']={key:value['unit'] for key,value in selected_units.items()}
                basis_labels={'message':'原报文提供','confirmed_source_mapping':'已确认接入映射，报文未提供','unknown':'单位未核对'}
                source_value['field_unit_basis']={key:basis_labels[value['basis']] for key,value in selected_units.items()}
            text = json.dumps(source_value,
                ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            declared.append({'source_ref': source_ref, 'kind': 'observation', 'source_id': obs.id,
                'content_hash': digest(item), 'text': text, 'readability': 'readable'})
        else:
            sources.need(kind == 'archive' and item['entity']['id'] == entity and record['source'] == 'resource_entity')
            source_ref = 'archive_' + str(len(declared) + 1)
            declared.append({'source_ref': source_ref, 'kind': 'archive_record', 'source_id': entity,
                'content_hash': digest(item['entity']), 'text': None, 'readability': 'readable'})
        for field in fields:
            if field['entity_id'] != entity or field['field'] not in record['attributes']: continue
            value = record['attributes'][field['field']]
            if value is None: continue
            validate(field['schema'], value)
            at = record.get('observed_at')
            if at is not None: _at(at)
            fact_unit = field['unit']
            if kind == 'observation':
                sources.need(not (item.get('origin') or {}).get('exchange_id') or item.get('http_exchange') is not None)
                explicit_unit = False
                unit_views = [item['report'].get('field_units'), item['report']['extracted'].get('field_units'),
                    item['observation']['data'].get('field_units'), (item.get('origin') or {}).get('field_units')]
                if item.get('http_exchange') is not None:
                    exchange = item['http_exchange']['data']
                    unit_views.append(exchange['projection']['field_units'])
                    frozen = mapping._frozen(exchange['freeze']['mapping'])
                    expected = {link['field_key']: link['unit'] for link in frozen['mapping']['links'].values()}
                    sources.need(field['field'] in expected and expected[field['field']] == field['unit']
                        and sources.same(frozen['field_schemas'][field['field']], field['schema']))
                    explicit_unit = True
                for units in unit_views:
                    if units is not None and field['field'] in units:
                        sources.need(units[field['field']] == field['unit'])
                        explicit_unit = explicit_unit or units[field['field']] is not None
                if mqtt_units is not None and field['field'] in mqtt_units:
                    proven=mqtt_units[field['field']]
                    if proven['unit'] is not None:
                        sources.need(field['unit'] is None or field['unit']==proven['unit'])
                        fact_unit=proven['unit'];explicit_unit=True
                if field['value_type'] in {'number', 'integer'} and not explicit_unit:
                    fact_unit = None  # A domain expectation does not supply missing measurement units.
            citation = {'source_ref': source_ref, 'quote_start': None, 'quote_end': None, 'quote': None}
            if kind == 'observation':
                text = declared[-1]['text']; citation.update(quote_start=0, quote_end=min(len(text), 1200), quote=text[:1200])
            fact = {'fact_ref': 'fact_' + str(len(facts) + 1), 'kind': 'observation' if kind == 'observation' else 'document_statement',
                'entity_id': entity, 'field': field['field'], 'unit': fact_unit,
                'semantics': ('statement' if kind == 'archive' and report['resolved_time']['mode'] == 'archive'
                    else 'instantaneous' if at is not None else 'unknown'),
                'time_scope': {'start': at, 'end': at}, 'value': copy.deepcopy(value), 'source_refs': [citation]}
            facts.append(fact); origins[fact['fact_ref']] = record.get('observation_id')
    conflicts = []
    for left, right in combinations(facts, 2):
        if decision._comparable(left, right) and not decision._same_value(left['value'], right['value']):
            conflicts.append({'conflict_ref': 'conflict_' + str(len(conflicts) + 1),
                'fact_refs': [left['fact_ref'], right['fact_ref']], 'reason': '同一对象、字段、单位及原观测时刻存在不同原值，尚未核实。'})
    for conflict in report['conflicts']:
        eligible = [fact for fact in facts if fact['entity_id'] == conflict['entity_id'] and fact['field'] == conflict['field']]
        if conflict.get('observed_at') is not None:
            eligible = [fact for fact in eligible if fact['time_scope']['start'] is not None and _at(fact['time_scope']['start']) == _at(conflict['observed_at'])]
        covered = [row for row in conflicts if set(row['fact_refs']) <= {fact['fact_ref'] for fact in eligible}]
        represented = {origins[ref] for row in covered for ref in row['fact_refs'] if origins[ref] is not None}
        sources.need(covered and set(conflict.get('observation_ids', [])) <= represented)
    return facts, declared, conflicts


def compose_content(*, report, fields, entities, candidate_specs, capability_pins,
        data_source, document_source, facts, observation_sources, conflicts):
    """Pure content over independently checked inputs; no scope or authority.

    report is the original evaluation_result envelope (status plus result).
    capability_pins contains only the original capabilities and policies lists.
    Source DTOs describe real packets or Evidence; no execution identity is
    constructed here. The caller still applies its own full input contract.
    """
    query_status = report['status']; report = report['result']
    requested = report['evidence_requirement']['source']; window = _window(report)
    archive_mode = report['resolved_time']['mode'] == 'archive'
    sources.need(not archive_mode or requested in {'archive', 'either'})
    requirements = [{'requirement_ref': 'requirement_' + str(index + 1), 'entity_id': field['entity_id'],
        'field': field['field'], 'value_type': field['value_type'], 'unit': field['unit'],
        'semantics': 'statement' if archive_mode else 'instantaneous', 'time_scope': copy.deepcopy(window),
        'time_mode': 'not_required' if archive_mode else 'bounded',
        'required_kind': {'observations': 'observation', 'archive': 'document_statement', 'either': 'observed_or_recorded'}[requested]}
        for index, field in enumerate(fields)]
    facts = copy.deepcopy(facts)
    source_rows = copy.deepcopy([data_source, document_source, *observation_sources])
    capabilities = []
    for index, pin in enumerate(capability_pins['capabilities']):
        source_ref = 'capability_' + str(index + 1); execution = pin['data']['execution']
        metadata = {'capability_id': pin['capability_id'], 'kind': pin['data']['kind'],
            'allowed_agent_roles': pin['data']['allowed_agent_roles'], 'release_version': pin['release_version'],
            'adapter': execution['adapter'], 'operation': execution.get('operation'),
            'side_effect': execution['side_effect'], 'delivery': pin['delivery'], 'ports': pin['ports']}
        text = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        source_rows.append({'source_ref': source_ref, 'kind': 'capability', 'source_id': pin['capability_id'],
            'content_hash': pin['capability_hash'], 'text': text, 'readability': 'readable'})
        capabilities.append({key: pin[key] for key in ('capability_ref', 'capability_id', 'capability_hash', 'release_version', 'action_kind')})
        capabilities[-1].update(input_schema_hash=digest(pin['data']['input_schema']), output_schema_hash=digest(pin['data']['output_schema']),
            source_ref=source_ref, output_ports=copy.deepcopy(pin['ports']) if pin['delivery'] == 'output' else [],
            information_targets=copy.deepcopy(pin['ports']) if pin['delivery'] == 'requested_observation' else [])
        facts.append({'fact_ref': 'declaration_' + str(index + 1), 'kind': 'capability_declaration',
            'entity_id': entities[0]['id'], 'field': 'capability_declaration', 'unit': None, 'semantics': 'statement',
            'time_scope': {'start': None, 'end': None}, 'value': '已登记能力声明；实际调用和独立收件须另行核对。',
            'source_refs': [{'source_ref': source_ref, 'quote_start': 0, 'quote_end': min(len(text), 1200), 'quote': text[:1200]}]})
    candidates = []
    for spec in _specs({'candidate_specs': candidate_specs}):
        mappings = []
        for selected in spec['capability_pins']:
            pin = next(item for item in capability_pins['capabilities'] if item['capability_ref'] == selected['capability_ref'])
            for port in pin['ports']:
                for requirement in requirements:
                    if all(port[key] == requirement[key] for key in ('field', 'unit', 'value_type')):
                        declaration_only_continue = (query_status == 'sufficient')
                        if selected['delivery'] == 'requested_observation' and not declaration_only_continue and not any(
                            item['context']['gap']['entity_id'] == requirement['entity_id'] and port['field'] in item['context']['gap']['fields']
                            for item in capability_pins['policies']): continue
                        mappings.append({'requirement_ref': requirement['requirement_ref'], 'capability_ref': pin['capability_ref'],
                            'delivery': selected['delivery'], 'port_ref': port['port_ref']})
        candidates.append({'candidate_ref': spec['candidate_ref'], 'proposal': spec['proposal'], 'claims': spec['claims'],
            'capability_refs': [item['capability_ref'] for item in spec['capability_pins']], 'requirement_mappings': mappings})
    return {'requirements': requirements, 'sources': source_rows, 'capabilities': capabilities,
        'candidates': candidates, 'facts': facts, 'known_conflicts': copy.deepcopy(conflicts)}


def _compose(db, p, task, inputs, frozen_sources, pins):
    facts, observations, conflicts = _facts(db, p, frozen_sources, pins['fields'])
    packet_text = frozen_sources['data'].get('text')
    data_source = {'source_ref': 'data_packet', 'kind': 'packet', 'source_id': frozen_sources['data']['packet_id'],
        'content_hash': frozen_sources['data']['sha256'], 'text': packet_text,
        'readability': 'readable' if type(packet_text) is str else 'partial'}
    document_source = {'source_ref': 'document_packet', 'kind': 'packet', 'source_id': frozen_sources['documents']['packet_id'],
        'content_hash': frozen_sources['documents']['sha256'], 'text': frozen_sources['documents']['text'],
        'readability': 'partial' if any(item['partial'] for item in frozen_sources['documents']['entries']) else 'readable'}
    content = compose_content(report=frozen_sources['data']['evaluation_record']['evaluation_result'],
        fields=pins['fields'], entities=pins['entities'], candidate_specs=inputs['candidate_specs'],
        capability_pins={'capabilities': pins['capabilities'], 'policies': pins['policies']},
        data_source=data_source, document_source=document_source, facts=facts,
        observation_sources=observations, conflicts=conflicts)
    return decision.check_input({'schema': decision.INPUT, 'decision_intent': inputs['decision_intent'],
        'scope': {'task_id': task.id, 'activation_id': pins['scope']['activation_id'],
            'goal_snapshot_hash': pins['goal_snapshot_hash'], 'entity_ids': [item['id'] for item in pins['entities']]},
        **content})


@sources.safe
def build_frame(db, p, task, job, cap, inputs, *, _server_protocol='business_goal_ready_v288'):
    sources.need(type(_server_protocol) is str and _server_protocol == 'business_goal_ready_v288')
    with db.no_autoflush:
        sources._reader_iam(db, p)
        specs = _specs(inputs); original_sources = sources.resolve(db, p, task, inputs)
        task, release, _binding, identity = _identity(db, p, task, job, cap, inputs, live=True, _server_protocol=_server_protocol)
        pins = _field_pins(db, p, task, release, original_sources, live=True)
        capabilities, policies = _capability_pins(db, p, task, original_sources, specs, pins['fields'], pins['package'], live=True)
        pins.update(schema=PIN_SCHEMA, scope=identity, capabilities=capabilities, policies=policies)
        pins['pins_hash'] = digest(pins)
        frame = {'decision_input': _compose(db, p, task, inputs, original_sources, pins),
            'sources': original_sources, 'capability_pins': pins}
        _bytes(frame, MAX_BYTES)
        return frame


@sources.safe
def checked_frame(db, p, task, job, cap, inputs, frozen_frame, *, _server_protocol='business_goal_ready_v288'):
    sources.need(type(_server_protocol) is str and _server_protocol == 'business_goal_ready_v288')
    with db.no_autoflush:
        sources._reader_iam(db, p); _bytes(frozen_frame, MAX_BYTES)
        sources.need(type(frozen_frame) is dict and set(frozen_frame) == {'decision_input', 'sources', 'capability_pins'})
        old = frozen_frame['capability_pins']
        sources.need(type(old) is dict and set(old) == {'schema', 'scope', 'package', 'release', 'entities', 'fields',
            'object_snapshots_hash', 'goal_snapshot_hash', 'capabilities', 'policies', 'pins_hash'}
            and old['schema'] == PIN_SCHEMA and old['pins_hash'] == digest({key: value for key, value in old.items() if key != 'pins_hash'}))
        original_sources = sources.checked_frozen(db, p, task, frozen_frame['sources'])
        task, release, _binding, identity = _identity(db, p, task, job, cap, inputs, live=False, _server_protocol=_server_protocol)
        pins = _field_pins(db, p, task, release, original_sources, live=False)
        capabilities, policies = _capability_pins(db, p, task, original_sources, _specs(inputs), pins['fields'], pins['package'], live=False, original=old)
        pins.update(schema=PIN_SCHEMA, scope=identity, capabilities=capabilities, policies=policies)
        pins['pins_hash'] = digest(pins)
        sources.need(sources.same(pins, old))
        value = _compose(db, p, task, inputs, original_sources, pins)
        sources.need(sources.same(value, frozen_frame['decision_input']))
        return {'decision_input': value, 'sources': original_sources, 'capability_pins': pins}

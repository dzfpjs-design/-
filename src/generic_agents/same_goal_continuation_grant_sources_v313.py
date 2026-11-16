"""Real current data for a version-scoped additive grant; never a dispatcher.

One current evaluation proof is followed by its independent original checks.
No query, model, SDK, observation, approval, Task edit or commit is performed.
The returned private basis is server material, not a client bearer credential.
"""
import copy
import math
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from . import same_goal_continuation_grant_contracts_v313 as c
from . import same_goal_continuation_store_v312 as drafts
from . import same_goal_continuation_sources_v312 as origins
from . import goal_evaluation_provider_v288 as provider
from . import business_goal_ready_sources_v288 as originals
from . import goal_ack_origin_mqtt_v297 as mqtt_origins
from . import http_observation_mapping_v177 as http_mapping
from . import layered_approval_store_v175 as approvals
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope
from .contracts import digest
from .goals import snapshot
from .models import Capability, Entity, Plan
from .relations import lock_current_actor
from .security import require
from .services import check_version, published, validate
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

need, checked = drafts.need, drafts.checked
applies, validate_manifest, manifest = c.applies, c.validate_manifest, c.manifest
MAX_TOOLS = 100


def _clock(db):
    at = db.scalar(select(func.clock_timestamp()))
    need(type(at) is datetime and at.tzinfo is not None and at.utcoffset() is not None)
    return at


def _base(db, p, task_id, advice_id, continuation_id, *, write=False, lock=False):
    drafts._reader(db, p, write=write)
    if write: require(p, 'admin')
    task = drafts.existing._task(db, p, task_id, lock=lock or write)
    if write: lock_current_actor(db, p)
    inventory = drafts._inventory(db, p, task, advice_id)
    matches = [item for item in inventory if item['plan'].id == continuation_id]
    need(len(matches) == 1, '未找到这份原任务的完整办理草案及独立保存回执')
    saved = matches[0]; continuation = saved['plan']
    need(saved['source']['advice_plan_id'] == advice_id
        and continuation.task_id == task.id and continuation.workspace_id == p.workspace_id)
    advice = provider._fresh(db, p, Plan, advice_id)
    need(advice.task_id == task.id)
    prep, result, review, source, selected, candidates = origins._source(db, p, task, advice)
    need(digest(source) == continuation.data['source_hash'] == digest(saved['source'])
        and source['task_id'] == task.id and source['advice_plan_id'] == advice.id
        and source['decision_intent'] == 'business_conclusion',
        '原建议、人工复核或本次只读业务结论范围已变化；未替换草案来源')
    return {'task': task, 'continuation': continuation, 'revision': saved['revision'],
        'source': source, 'source_hash': digest(source), 'selected': selected}


def _current(db, p, loaded, *, lock):
    need(loaded['continuation'].status == 'draft', '原草案已撤回，不能申请新的工具授权')
    origins._planning_guard(db, p, loaded['task'], lock_sources=lock)


def _pin(cap):
    return {'id': cap.id, 'content_hash': cap.content_hash, 'release_version': cap.release_version}


def _cap(db, p, identity, *, lock=False):
    cap = provider._fresh(db, p, Capability, identity, lock=lock)
    actual = published(db, p, identity, 'tool')
    need(actual is cap and cap.release_version == cap.data['version'])
    c.validate_manifest(cap.data)
    return cap


def _tools(db, p):
    # Include independent reserved witnesses; deleting one marker cannot make
    # a malformed reserved capability look like an unrelated tool.
    from sqlalchemy import or_
    rows = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.status == 'published', or_(Capability.namespace == c.NAMESPACE,
            Capability.name.in_(list(c.NAMES)), Capability.data['execution']['dispatch_scope'].as_string() == c.SCOPE,
            Capability.data['specification']['schema'].as_string() == c.MANIFEST_SCHEMA))
        .order_by(Capability.id).limit(MAX_TOOLS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_TOOLS, '增量工具目录超过完整核对上限，未选择第一项替代')
    return [_cap(db, p, row.id) for row in rows]


def _policy(db, p, loaded, *, lock=False):
    task = loaded['task']
    policy = approvals.policy_for(db, p, task.data['instance_id'], task.data['business_id'])
    if policy is None: return None
    actual = approvals.current_policy(db, p, policy.id, lock_sources=lock)
    version, spec = approvals.policy_value(db, p, actual)
    need(actual.workspace_id == version.workspace_id == p.workspace_id
        and actual.instance_id == spec['instance_id'] == task.data['instance_id']
        and actual.business_id == spec['business_id'] == task.data['business_id']
        and version.policy_id == actual.id and type(version.revision) is int and version.revision >= 1)
    ref = {'policy_id': actual.id, 'version_id': version.id,
        'revision': version.revision, 'record_hash': digest(version.data)}
    stages = []
    for stage in spec['stages']:
        unit = approvals.unit(db, p, stage['scope_id'], lock_sources=lock)
        stages.append({'label': stage['name'], 'role_key': stage['role_key'], 'scope_name': unit.name})
    return {'ref': ref, 'spec': copy.deepcopy(spec), 'stages': stages,
        'subject_kind': c.SUBJECT_KIND, 'application': 'explicit_additive_continuation_grant'}


def _data(db, p, loaded, *, lock=False):
    task = loaded['task']
    current = provider.require_current(db, p, task, lock_sources=lock)
    need(current is not None and current['evidence']['status'] == 'sufficient'
        and not current['evidence']['conflicts'], '原目标的当前正式评估未充分，不能封存计算记录')
    row = provider._fresh(db, p, Evaluation, current['evaluation_ref']['id'])
    record = row.data; head = task.data[provider.KEY]
    need(row.task_id == task.id and row.record_hash == current['evaluation_ref']['record_hash']
        and record['record_hash'] == row.record_hash and record['scope']['task_id'] == task.id
        and record['goal_snapshot'] == snapshot(task)
        and head['evaluation_ref'] == current['evaluation_ref']
        and record['evaluation_result']['status'] == record['evaluation_result']['result']['status'] == 'sufficient'
        and not record['evaluation_result']['result']['conflicts'])
    # require_current has already checked the real SDK/Job/packet/Qe/ACK. Only
    # the actual admitted originals are re-read here, not that entire tree.
    fact_origins = originals._origins(db, p, task, record)
    report = record['evaluation_result']['result']
    need(len(fact_origins) == len(report['evidence']) + len(report['observation_evidence']))
    for item in fact_origins:
        mqtt = (item.get('origin') or {}).get('mqtt_origin')
        if mqtt is not None:
            binding = provider._fresh(db, p, mqtt_origins.MqttBindingRevision, mqtt['binding_id'])
            need(binding.content_hash == mqtt['binding_hash']
                and digest(binding.data['field_units']) == mqtt['field_units_hash'])
            item['mqtt_field_units'] = copy.deepcopy(binding.data['field_units'])
    return {'record': record, 'origins': fact_origins, 'evaluation': {
        'evaluation_ref': copy.deepcopy(current['evaluation_ref']),
        'query_ref': copy.deepcopy(record['evaluation_query_ref']), 'cut_at': record['cut_at'],
        'generation': row.generation, 'mature_head_hash': head['head_hash'],
        'goal_check_job_id': current['goal_check_job_id'], 'goal_check_id': current['goal_check_id'],
        'report_hash': current['report_hash']}}


def _fields(db, p, loaded):
    task = loaded['task']; release = loaded['selected']['release']
    package = provider._fresh(db, p, Capability, task.package_id)
    need(package.content_hash == digest(package.data) == package.validated_hash
        and package.id == release.package_id)
    schemas = package.data['specification']['entity_schemas']
    requested = snapshot(task)['spec']['required_fields']; by_entity = []; entity_ids = []
    for identity in task.data['entity_ids']:
        entity = provider._fresh(db, p, Entity, identity)
        properties = schemas[entity.entity_type]['properties']
        types = [kind for kind in release.data['snapshot']['object_types'] if kind['key'] == entity.entity_type]
        need(len(types) == 1)
        labels = {field['key']: field for field in types[0]['fields']}
        values = {}
        for key in requested:
            prop = properties.get(key, {}); kinds = prop.get('type')
            if type(kinds) is list: kinds = [item for item in kinds if item != 'null']
            else: kinds = [kinds]
            if len(kinds) != 1 or kinds[0] not in {'number', 'integer'}: continue
            label = labels.get(key, {})
            units = [unit for unit in (prop.get('unit'), prop.get('x-unit'), label.get('unit')) if unit is not None]
            if units and any(unit != units[0] for unit in units): continue
            unit = units[0] if units else None
            need(unit is None or type(unit) is str and len(unit) <= 80)
            values[key] = {'key': key, 'label': label.get('label') or prop.get('title') or key,
                'unit': unit, 'schema': copy.deepcopy(prop)}
        by_entity.append(values)
        entity_ids.append(entity.id)
    need(bool(by_entity))
    common = set.intersection(*(set(values) for values in by_entity))
    return [{**copy.deepcopy(by_entity[0][key]), 'schemas': {
        identity: copy.deepcopy(values[key]['schema']) for identity, values in zip(entity_ids, by_entity)}}
        for key in sorted(common) if len({values[key]['unit'] for values in by_entity}) == 1]


def _public_field(field): return {key: copy.deepcopy(field[key]) for key in ('key', 'label', 'unit')}


def _measurement_unit(item, field, *, required=True):
    """Compare actual unit declarations; a domain expectation is no measurement."""
    if item['kind'] == 'archive': return field['unit']
    report = item.get('report'); observation = item.get('observation'); origin = item.get('origin') or {}
    need(type(report) is dict and type(report.get('extracted')) is dict
        and type(observation) is dict and type(observation.get('data')) is dict)
    views = [report.get('field_units'), report['extracted'].get('field_units'),
        observation['data'].get('field_units'), origin.get('field_units'), item.get('mqtt_field_units')]
    need(not origin.get('exchange_id') or item.get('http_exchange') is not None)
    explicit = False
    if item.get('http_exchange') is not None:
        exchange = item['http_exchange']['data']
        views.append(exchange['projection']['field_units'])
        frozen = http_mapping._frozen(exchange['freeze']['mapping'])
        expected = {link['field_key']: link['unit'] for link in frozen['mapping']['links'].values()}
        need(field['key'] in expected and expected[field['key']] == field['unit']
            and digest(frozen['field_schemas'][field['key']]) == digest(field['schema']))
        explicit = True
    for units in views:
        if units is None: continue
        need(type(units) is dict)
        if field['key'] in units:
            need(units[field['key']] == field['unit'], '原观测单位与目标字段不一致，未换算或混合汇总')
            explicit = explicit or units[field['key']] is not None
    need(not required or field['unit'] is None or explicit, '原观测未明确测量单位，未用业务字段的期望单位补写')
    return field['unit'] if explicit else None


def _records(loaded, data, binding, field):
    kind = 'observation' if binding == 'observation_records' else 'archive'
    selected = [item for item in data['origins'] if item['kind'] == kind]
    need(0 < len(selected) <= c.MAX_RECORDS, '此记录来源没有可用于本次汇总的正式数据')
    records = []; provenance = []; finite = 0; seen = set()
    for item in selected:
        record = item['record']; entity_id = record['entity_id']
        need(entity_id in loaded['task'].data['entity_ids'] and type(record['version']) is int and record['version'] >= 1)
        identity = record['observation_id'] if kind == 'observation' else entity_id
        need(identity not in seen); seen.add(identity)
        schema = field.get('schemas', {}).get(entity_id, field['schema'])
        attributes = {}
        if field['key'] in record['attributes']:
            value = record['attributes'][field['key']]
            need(value is None or type(value) in {int, float} and math.isfinite(value),
                '原记录含不可用于数值计算的值；未将错误值当作缺测或无异常')
            if value is not None: validate(schema, value)
            attributes[field['key']] = value
            finite += value is not None
        unit = _measurement_unit(item, {**field, 'schema': schema},
            required=attributes.get(field['key']) is not None)
        records.append({'entity_id': entity_id, 'version': record['version'], 'attributes': attributes})
        provenance.append({'kind': kind, 'entity_id': entity_id, 'record_version': record['version'],
            'observation_id': identity if kind == 'observation' else None,
            'source_id': record.get('source_id'), 'observed_at': record.get('observed_at'),
            'record_hash': digest(record), 'origin_hash': digest(item), 'unit': unit})
    need(finite > 0, '所选字段尚无有效数值；不能把全部缺测当作可用计算依据')
    return records, provenance


def _availability(blocks, code, message, read):
    try: return read()
    except HTTPException as error:
        if error.status_code not in {404, 409, 422}: raise
        blocks.append({'code': code, 'message': message}); return None


@checked
@authored_goal_binding_parse_scope()
def context(db, p, task_id, advice_id, continuation_id):
    with db.no_autoflush:
        loaded = _base(db, p, task_id, advice_id, continuation_id)
        blocks = []
        def current(): _current(db, p, loaded, lock=False); return True
        active = (_availability(blocks, 'goal_not_current', '当前目标或草案不允许申请新的工具授权', current)
            if p.role in {'admin', 'operator'} else False)
        policy = _availability(blocks, 'policy_unavailable', '审批政策尚不可核对，未假定工具授权即为批准',
            lambda: _policy(db, p, loaded))
        if policy is None: blocks.append({'code': 'policy_missing', 'message': '请先明确当前业务的分级审批政策'})
        caps = _tools(db, p)
        fields = _fields(db, p, loaded)
        data = _availability(blocks, 'data_unavailable', '当前正式资料尚不能用于这次数值汇总',
            lambda: _data(db, p, loaded)) if caps and active else None
        tools = []
        for cap in caps:
            binding = cap.data['execution']['input_bindings']['records']; available_fields = []
            if data is not None:
                for field in fields:
                    try: _records(loaded, data, binding, field)
                    except HTTPException as error:
                        if error.status_code not in {409, 422}: raise
                    else: available_fields.append(_public_field(field))
            tools.append({**_pin(cap), 'name': cap.data['name'], 'description': cap.data['description'],
                'fields': available_fields, 'binding': binding})
        if not any(tool['fields'] for tool in tools):
            blocks.append({'code': 'tool_missing', 'message': '尚无已发布且有可比数值来源的独立只读汇总工具'})
        if p.role != 'admin': blocks.append({'code': 'admin_required', 'message': '增量工具授权须由当前管理员明确办理'})
        plan = loaded['continuation']; task = loaded['task']
        return {'schema': c.CONTEXT, 'task_id': task.id, 'advice_plan_id': advice_id,
            'continuation_id': plan.id, 'task_version': task.version, 'continuation_version': plan.version,
            'revision_hash': loaded['revision']['record_hash'], 'source_hash': loaded['source_hash'],
            'tools': tools, 'operations': list(c.OPERATIONS),
            'policy': {'ref': policy['ref'], 'stages': policy['stages']} if policy else None,
            'permissions': {'can_issue': bool(p.role == 'admin' and active and policy
                and any(tool['fields'] for tool in tools))}, 'blocked_reasons': blocks, **c.FLAGS}


@checked
@authored_goal_binding_parse_scope()
def issue_basis(db, p, task_id, advice_id, continuation_id, body, *, lock=True):
    value = c.dump(c.IssueRequest, body.model_dump(mode='json') if isinstance(body, c.IssueRequest) else body)
    need(type(lock) is bool)
    with db.no_autoflush:
        loaded = _base(db, p, task_id, advice_id, continuation_id, write=True, lock=lock)
        task = loaded['task']; plan = loaded['continuation']; revision = loaded['revision']
        check_version(task, value['expected_task_version']); check_version(plan, value['expected_continuation_version'])
        need(revision['record_hash'] == value['revision_hash'] and loaded['source_hash'] == value['source_hash'])
        _current(db, p, loaded, lock=lock)
        cap = _cap(db, p, value['capability_pin']['id'], lock=lock)
        need(_pin(cap) == value['capability_pin'])
        policy = _policy(db, p, loaded, lock=lock)
        need(policy is not None and digest(policy['ref']) == digest(value['policy_ref']), '原审批政策版本已变化，请重新核对')
        fields = [field for field in _fields(db, p, loaded) if field['key'] == value['field']]
        need(len(fields) == 1, '所选字段不属于原目标中单位可比的数值字段')
        field = fields[0]; data = _data(db, p, loaded, lock=lock)
        binding = cap.data['execution']['input_bindings']['records']
        records, provenance = _records(loaded, data, binding, field)
        inputs = {'field': value['field'], 'operation': value['operation'], 'records': records}
        validate(cap.data['input_schema'], inputs)
        at = _clock(db); goal = snapshot(task); expires = at + timedelta(seconds=value['validity_seconds'])
        if goal['spec']['deadline'] is not None: expires = min(expires, instant(goal['spec']['deadline']))
        need(expires > at, '原目标截止时间已到，未生成新的工具授权')
        release = loaded['selected']['release']; package = provider._fresh(db, p, Capability, task.package_id)
        basis = {'schema': c.BASIS, 'workspace_id': p.workspace_id, 'task_id': task.id,
            'advice_plan_id': advice_id, 'continuation_ref': {'id': plan.id, 'version': plan.version,
                'revision_hash': revision['record_hash'], 'source_hash': loaded['source_hash']},
            'source_hash': loaded['source_hash'], 'goal': {'hash': goal['hash'], 'version': goal['version'],
                'snapshot_hash': digest(goal), 'native_context_hash': task.data['native_goal_context_hash']},
            'release': {'id': release.id, 'configuration_hash': release.configuration_hash,
                'package_id': task.package_id, 'package_hash': package.content_hash,
                'original_graph_hash': loaded['source']['original_graph_hash']},
            'capability_pin': _pin(cap), 'agent_role': 'data',
            'input_schema_hash': digest(cap.data['input_schema']), 'output_schema_hash': digest(cap.data['output_schema']),
            'records_binding': binding, 'scope': {'entity_ids': sorted({row['entity_id'] for row in records}),
                'fields': [field['key']]}, 'field': _public_field(field), 'operation': value['operation'],
            'evaluation': data['evaluation'], 'records': records, 'provenance': provenance,
            'inputs': inputs, 'input_hash': digest(inputs),
            'policy': {key: copy.deepcopy(policy[key]) for key in ('ref', 'spec', 'subject_kind', 'application')},
            'actor_id': p.user_id, 'issued_at': at.isoformat(), 'expires_at': expires.isoformat(),
            'max_calls': 1, 'side_effect': False, 'external_allowed': False, 'cloud_allowed': False,
            'simulation_only': data['record']['simulation_only'], 'test_only': data['record']['test_only']}
        c.draft.advice.base._json(basis)
        basis['record_hash'] = c.record_hash(basis)
        return {'task': task, 'continuation': plan, 'source_hash': loaded['source_hash'],
            'source': copy.deepcopy(loaded['source']), 'basis': basis}

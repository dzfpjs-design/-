"""Read-only presentation contracts for registered manual observations.

The published input schema remains authoritative at ingestion. These helpers do
not register tools, coerce facts, approve observations or change receipts.
"""
import copy
import re

from fastapi import HTTPException

from .contracts import digest


_COMPOUND = {'$ref', 'allOf', 'anyOf', 'oneOf', 'not', 'if', 'then', 'else',
             'dependentSchemas', 'dependentRequired', 'patternProperties'}
_SCALAR = {'string', 'number', 'integer', 'boolean'}


def registration_form(manifest):
    """Describe only fields that the existing local manual adapter can accept."""
    blocked = {'registration_mode': 'unsupported', 'registration_fields': [],
               'registration_issue': '输入契约需要专用录入界面，请核对登记能力配置。'}
    try:
        execution = manifest['execution']
        if (execution.get('adapter') != 'builtin' or execution.get('handler') != 'numeric_rules'
                or execution.get('side_effect') or manifest['data_policy'].get('cloud_allowed') is not False):
            return blocked
        rules = manifest['specification'].get('rules', [])
        if not isinstance(rules, list) or len(rules) > 32:
            return blocked
        schema = manifest.get('input_schema', {})
        if (not isinstance(schema, dict) or _COMPOUND.intersection(schema)
                or schema.get('type', 'object') != 'object'):
            return blocked
        outer_required = schema.get('required', [])
        if not isinstance(outer_required, list) or set(outer_required) - {'attributes'}:
            return blocked
        attributes = schema.get('properties', {}).get('attributes', {})
        if (not isinstance(attributes, dict) or _COMPOUND.intersection(attributes)
                or attributes.get('type', 'object') != 'object'):
            return blocked
        properties = attributes.get('properties', {})
        if not isinstance(properties, dict):
            return blocked
        required = attributes.get('required', [])
        if (not isinstance(required, list) or any(not isinstance(key, str) for key in required)
                or len(set(required)) != len(required)):
            return blocked
        # Older numeric registrations supplied rules without a closed field form.
        # Keep their established numeric inputs; never infer text from thresholds.
        if not properties and rules:
            properties = {rule['field']: {'type': 'number', 'unit': rule.get('unit')}
                          for rule in rules}
            required = list(properties)
        if not properties or len(properties) > 30 or set(required) - set(properties):
            return blocked
        fields = []
        for key, definition in properties.items():
            if (not isinstance(key, str) or not key or not isinstance(definition, dict)
                    or _COMPOUND.intersection(definition) or definition.get('type') not in _SCALAR):
                return blocked
            item = {'key': key, 'label': definition.get('title') or key,
                    'type': definition['type'], 'required': key in required}
            for name in ('unit', 'format', 'enum', 'minLength', 'maxLength', 'minimum',
                         'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'pattern'):
                if name in definition:
                    item[name] = copy.deepcopy(definition[name])
            if 'const' in definition:
                item['enum'] = [copy.deepcopy(definition['const'])]
            fields.append(item)
        return {'registration_mode': 'threshold' if rules else 'passthrough',
                'registration_fields': fields, 'registration_issue': None}
    except (AttributeError, KeyError, TypeError, ValueError):
        return blocked


def verified_history(data):
    history = data.get('verification_history', [])
    if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        raise HTTPException(409, '原核查历史无法核实，请保留当前请求。')
    request_ids = []
    for row in history:
        if 'request_id' not in row:
            continue
        identifier = row['request_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}', identifier):
            raise HTTPException(409, '原核查请求编号无法核实，请保留当前请求。')
        if (any(not isinstance(row.get(name), str) or not re.fullmatch(r'[0-9a-f]{64}', row[name])
                for name in ('input_hash', 'body_hash'))
                or not isinstance(row.get('reviewed_by'), str) or not row['reviewed_by']
                or not isinstance(row.get('decision'), str) or row['decision'] not in {'confirmed', 'rejected'}
                or not isinstance(row.get('reason'), str)):
            raise HTTPException(409, '原核查请求回执无法核实，请保留当前请求。')
        request_ids.append(identifier)
    expected = data.get('verification_history_hash')
    if (request_ids and (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected))
            or expected and digest(history) != expected):
        raise HTTPException(409, '原核查历史无法核实，请保留当前请求。')
    if len(set(request_ids)) != len(request_ids):
        raise HTTPException(409, '原核查请求存在冲突，不能推断保存结果。')
    latest = data.get('human_verification')
    if latest is not None and not isinstance(latest, dict):
        raise HTTPException(409, '最新核查记录格式异常，请保留当前请求。')
    if latest and (history or latest.get('request_id')) and (not history or latest != history[-1]):
        raise HTTPException(409, '最新核查与保存历史不一致，不能推断保存结果。')
    return copy.deepcopy(history)


def review_request(data, request_id):
    """An absent historical request is unknown, never proof of non-execution."""
    matches = [row for row in verified_history(data) if row.get('request_id') == request_id]
    return {'found': bool(matches), 'review': matches[0] if matches else None}


def current_verification(data, checked):
    latest = data.get('human_verification')
    if not checked or not isinstance(latest, dict):
        return None
    current_hash = digest({'extracted': data.get('extracted', {}),
        'evidence_ids': data.get('evidence_ids', []), 'invocation_id': data.get('invocation_id')})
    decision = checked.get('review')
    if (not isinstance(decision, str) or decision not in {'confirmed', 'rejected'}
            or latest.get('decision') != decision or latest.get('input_hash') != current_hash):
        return None
    return copy.deepcopy(latest)


def observation_review_basis(db, principal, row, *, received, source, extractor):
    from .observation_integrity import checked_report, CHECK_ERRORS
    form = registration_form(extractor.data) if extractor is not None else {}
    original = row.data.get('original_request', {})
    extracted = row.data.get('extracted', {})
    original = original if isinstance(original, dict) else {}
    extracted = extracted if isinstance(extracted, dict) else {}
    issue = None
    checked = None
    history = []
    if row.status == 'processed':
        try:
            checked = checked_report(db, principal, row)
            history = verified_history(row.data)
        except CHECK_ERRORS:
            checked = None
            issue = '原登记、提取回执或核查历史无法核实，请先恢复原依据。'
    review = current_verification(row.data, checked)
    can_verify = (row.status == 'processed' and received and checked is not None
                  and principal.role in {'admin', 'operator'} and not row.data.get('native_packet'))
    if issue:
        reason = issue
    elif principal.role not in {'admin', 'operator'}:
        reason = '当前账号只可查看登记与回执。'
    elif row.status != 'processed':
        reason = '请等待本地处理完成；失败时先恢复原处理。'
    elif not received:
        reason = '数据管理尚未接收原处理结果，请等待归集回执。'
    elif row.data.get('native_packet'):
        reason = '请回到原分析任务核查。'
    else:
        reason = None
    basis = {'original_attributes': copy.deepcopy(original.get('attributes', row.data.get('attributes', {}))),
             'extracted_attributes': copy.deepcopy(extracted.get('attributes', {})),
             'fields': form.get('registration_fields', []) if checked is not None else [], 'verification': review,
             'historical_verification': copy.deepcopy(row.data.get('human_verification')) if checked and not review else None,
             'verification_input_hash': digest({'extracted': extracted,
                 'evidence_ids': row.data.get('evidence_ids', []),
                 'invocation_id': row.data.get('invocation_id')}) if checked else None,
             'source_name': source.data.get('description') if source and checked is not None else '原来源暂不可核对',
             'extractor_name': extractor.data.get('description') if extractor and checked is not None else '原登记方式暂不可核对',
             'observed_at': row.observed_at.isoformat() if row.observed_at else None,
             'evidence_ids': copy.deepcopy(row.data.get('evidence_ids', [])),
             'integrity_issue': issue, 'physical_state_verified': False,
             'verification_request_count': len(history)}
    return {'review_basis': basis, 'can_verify': bool(can_verify), 'block_reason': reason}


def reported_record_view(db, principal, row):
    from sqlalchemy import select
    from .models import Capability, Event, Job
    job = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.idempotency_key == 'extract:' + row.id))
    event = db.scalar(select(Event).where(Event.workspace_id == principal.workspace_id,
        Event.kind == 'observation.data', Event.idempotency_key == 'data:' + row.id))
    received = bool(event and event.status == 'received')
    delivery_status='received' if received else 'blocked' if event and event.status=='blocked_delivery' else 'pending'
    delivery_error='原观测或数据方案无法核对，请管理员核查原件与版本后重试接收。' if delivery_status=='blocked' else None
    if row.status=='processed':
        from .instance_data_receipts import observation_receipt
        from .observation_integrity import CHECK_ERRORS
        try:observation_receipt(db,principal,row)
        except CHECK_ERRORS:
            received=False
            if event and event.status=='received':delivery_status='needs_review'
            if delivery_status!='pending':delivery_error='已保存的接收依据无法核对，原记录保留，请管理员核查原件与版本。'
    def capability(identifier):
        return db.scalar(select(Capability).where(Capability.workspace_id == principal.workspace_id,
            Capability.id == identifier)) if identifier else None
    source = capability(row.source_id)
    extractor = capability(row.data.get('extractor_id'))
    return {'id': row.id, 'version': row.version, 'entity_id': row.entity_id, 'status': row.status,
        'source_id': row.source_id, 'extractor_id': row.data.get('extractor_id'),
        'request_id': row.source_record_id, 'processing_version': row.processing_version,
        'error': row.data.get('processing_error'),
        'retry_available': bool(row.status == 'failed' and job and job.status == 'failed' and job.attempts < 3),
        'data_received': received, 'registered_at': row.created_at.isoformat(),
        'data_delivery':{'status':delivery_status,'error':delivery_error,
            'retry_available':bool(delivery_status=='blocked' and row.status=='processed' and principal.role in {'admin','operator'})},
        **observation_review_basis(db, principal, row, received=received, source=source, extractor=extractor)}

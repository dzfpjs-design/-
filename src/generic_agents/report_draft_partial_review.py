"""Explicit local review of immutable paid answers; never a publishing authority."""
import copy
import json
from collections import Counter
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from . import report_reading_drafts as rd
from .contracts import digest
from .models import CloudCall
from .services import audit, require, scoped, validate
from .setup_object_proposals import unit_declared, validate_objects

router = APIRouter()
POLICY = 'private_partial_review_v1'
REPORT = 'report-draft-partial-validation-v1'
LOCAL = 'report-draft-local-review-v1'
RECEIPT = 'report-draft-local-review-receipt-v1'
CRITICAL = ('call_id', 'provider', 'model', 'usage', 'request_id')


class LocalReviewCommand(rd.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: UUID


def reject(code='partial_review_integrity', message='本地复核的原回答、来源或回执无法核对；既有数据保留', status=409):
    rd.fail(code, message, status)


def schema_value(value, preview):
    if not rd.jsonb_compatible(value):
        reject('partial_schema_invalid', '人工稿包含不可保存的数值或文字；本次未保存', 422)
    try:
        rd.generation_spec(preview['generation_contract'])
        validate(preview['generation_contract']['output_schema'], value)
    except (KeyError, TypeError, ValueError, rd.HTTPException):
        reject('partial_schema_invalid', '人工稿结构不合法；全部既有人工稿保留，本次未保存', 422)
    return copy.deepcopy(value)


def analyze(value, preview):
    """Report issues without normalizing, repairing or removing any candidate."""
    value = schema_value(value, preview)
    if preview.get('source_hash') != digest(preview['source_snapshot']):
        reject()
    _, blocks = rd.project_source(preview['source_snapshot'])
    sources = {(b['material_id'], b['block_id']): b['text'] for b in blocks}
    context = preview['cloud_payload'].get('context', {})
    config = {'businesses': context.get('businesses', []),
              'instanceObjects': [{'name': name} for name in context.get('object_names', [])]}
    issues = []

    def issue(code, path, message, citations=()):
        status = ('dependent' if code == 'relationship_endpoint_unconfirmed' else 'unverified'
                  if code in {'unit_unproven', 'grounding_missing', 'formal_validation_failed'} else 'invalid')
        item = {'code': code, 'path': path, 'message': message, 'status': status, 'citations': copy.deepcopy(list(citations))}
        if item not in issues:
            issues.append(item)

    def grounding(item, path):
        cites = item['citations']
        if not cites and not item['inference_reason'].strip():
            issue('grounding_missing', path + '/inference_reason', '缺少原文依据或明确的推断说明')
        for index, cite in enumerate(cites):
            text = sources.get((cite['material_id'], cite['block_id']))
            if text is None or cite['quote'] not in text:
                issue('citation_mismatch', path + '/citations/' + str(index) + '/quote', '引用未逐字匹配冻结来源', [cite])

    def passed(path):
        return not any(i['path'] == path or i['path'].startswith(path + '/') for i in issues)

    def item_status(path):
        states = {i['status'] for i in issues if i['path'] == path or i['path'].startswith(path + '/')}
        return next((state for state in ('invalid', 'unverified', 'dependent') if state in states), 'valid')

    def duplicate_names(items, root, key='name'):
        counts = Counter(item[key] for item in items)
        for index, item in enumerate(items):
            if counts[item[key]] > 1:
                issue('duplicate_name', root + '/' + str(index) + '/' + key, '名称重复，尚未通过正式校验')

    duplicate_names(value['businesses'], '/result/businesses')
    duplicate_names(value['objects'], '/result/objects')
    duplicate_names(value['relationships'], '/result/relationships')
    businesses, objects, relationships = [], [], []
    for index, business in enumerate(value['businesses']):
        path = '/result/businesses/' + str(index)
        grounding(business, path)
        for step_index, step in enumerate(business['steps']):
            grounding(step, path + '/steps/' + str(step_index))
        if business['operation'] != 'upsert' or business['original_name']:
            issue('candidate_scope', path + '/operation', '本入口仅保存新增候选，不能删除或修改既有业务')
        try:
            rd.old().validate_result({**value, 'businesses': [business], 'objects': [], 'relationships': []},
                                     blocks, config, rd.contract())
        except (ValueError, rd.HTTPException):
            if passed(path):
                issue('business_invalid', path, '业务的启动、产出、步骤、职责或去向尚未通过正式校验', business['citations'])
        businesses.append({'index': index, 'path': path, 'valid': passed(path), 'status': item_status(path), 'steps': len(business['steps'])})

    for index, obj in enumerate(value['objects']):
        path = '/result/objects/' + str(index)
        grounding(obj, path)
        duplicate_names(obj['fields'], path + '/fields', 'label')
        fields = []
        for field_index, field in enumerate(obj['fields']):
            field_path = path + '/fields/' + str(field_index)
            grounding(field, field_path)
            if field['unit'] is not None:
                proven = field['kind'] in {'整数', '数值'} and any(
                    unit_declared(field['label'], field['unit'], cite['quote'])
                    and unit_declared(field['label'], field['unit'], sources.get((cite['material_id'], cite['block_id']), ''))
                    for cite in field['citations'])
                if not proven:
                    issue('unit_unproven', field_path + '/unit',
                          '单位未由同一字段的明确原文证实，原值保留、仍待修正', field['citations'])
            fields.append({'index': field_index, 'path': field_path, 'valid': passed(field_path), 'status': item_status(field_path)})
        try:
            validate_objects([obj], blocks)
        except ValueError:
            if passed(path):
                issue('object_invalid', path, '对象或字段定义尚未通过原正式校验', obj['citations'])
        objects.append({'index': index, 'path': path, 'valid': passed(path), 'status': item_status(path), 'fields': fields})

    invalid_objects = {value['objects'][item['index']]['name'] for item in objects if not item['valid']}
    for index, relationship in enumerate(value['relationships']):
        path = '/result/relationships/' + str(index)
        grounding(relationship, path)
        for endpoint in ('source_type', 'target_type'):
            if relationship[endpoint] in invalid_objects:
                issue('relationship_endpoint_unconfirmed', path + '/' + endpoint,
                      '关系依赖的对象仍有待修正项，不能视为完整确认', relationship['citations'])
        try:
            rd.old().validate_relationships([relationship], blocks, config, value['objects'])
        except ValueError as error:
            reason = str(error)
            field, message = '', '关系声明、端点或多重性尚未通过原正式校验'
            if '多重性' in reason:
                field, message = '/cardinality', '此关系的多重性未获同一关系原文证实，未知应保留unspecified'
            elif '端点' in reason:
                available = {item['name'] for item in config['instanceObjects']} | {item['name'] for item in value['objects']}
                endpoint = 'source_type' if relationship['source_type'] not in available else 'target_type'
                field, message = '/' + endpoint, '关系端点未对应既有或本次完整定义的对象'
            elif '引用' in reason or '原文须明确' in reason:
                field, message = '/citations', '原文尚未逐字明确连接这两个对象并声明此关系，不能仅凭名称共现或推断确认'
            issue('relationship_invalid', path + field, message, relationship['citations'])
        relationships.append({'index': index, 'path': path, 'valid': passed(path), 'status': item_status(path)})

    formal = True
    try:
        rd.old().validate_result(value, blocks, config, rd.contract())
        if any(b['operation'] != 'upsert' or b['original_name'] for b in value['businesses']):
            formal = False
    except (ValueError, rd.HTTPException):
        formal = False
    if not formal and not issues:
        issue('formal_validation_failed', '/result', '整份候选尚未通过原正式语义校验；保存不代表通过')
    issues.sort(key=lambda item: (item['path'], item['code']))
    for index, item in enumerate(issues):
        item['id'] = 'issue_' + str(index + 1)
    report = {'schema': REPORT, 'source_hash': preview['source_hash'], 'value_hash': digest(value),
        'schema_valid': True, 'formal_valid': formal and not issues, 'can_apply': False, 'can_publish': False,
        'summary': {'businesses_total': len(businesses), 'businesses_valid': sum(i['valid'] for i in businesses),
            'steps_total': sum(i['steps'] for i in businesses), 'objects_total': len(objects),
            'objects_valid': sum(i['valid'] for i in objects), 'fields_total': sum(len(i['fields']) for i in objects),
            'relationships_total': len(relationships), 'relationships_valid': sum(i['valid'] for i in relationships),
            'issue_count': len(issues)},
        'items': {'businesses': businesses, 'objects': objects, 'relationships': relationships}, 'issues': issues}
    return {**report, 'report_hash': digest(report)}


def checked_raw(row):
    if row.data.get('schema') == 'report-evidence-draft-state-v1':
        reject('evidence_reading_endpoint_required', '本稿的分段原答与全局语义请在分段全文读取界面核对；原记录保留')
    raw = row.data.get('raw_response')
    if (not isinstance(raw, dict) or raw.get('hash') != digest({k: v for k, v in raw.items() if k != 'hash'})
            or not isinstance(raw.get('receipt'), dict) or row.data.get('reserved_call_id') != raw['receipt'].get('call_id')
            or row.data.get('receipts') != [raw['receipt']] or 'value' not in raw):
        reject()
    try:
        content = raw.get('content')
        if content is None and raw.get('content_encoding') == 'json-ascii-string-v1':
            content = json.loads(raw['content_json_ascii'])
        if not isinstance(content, str) or json.loads(content) != raw['value']:
            reject()
    except (ValueError, TypeError, KeyError, RecursionError):
        reject()
    schema_value(raw['value'], row.data['preview'])
    return raw


def checked_call(row, raw, call):
    expected = rd.expected_call(row)
    receipt = raw['receipt']
    if (call is None or not isinstance(call.data, dict) or call.id != receipt['call_id'] or call.workspace_id != row.workspace_id
            or call.capability_id != expected['capability_id'] or call.status not in {'succeeded', 'failed'}
            or type(call.reserved_tokens) is not int or call.reserved_tokens != row.data['preview']['input_summary']['token_bound']
            or type(call.charged_tokens) is not int or call.charged_tokens < 0
            or any(call.data.get(key) != expected[key] for key in ('actor_id', 'request_hash', 'dispatch_options_hash'))
            or call.data.get('input_policy_hash') != digest(row.data['preview']['generation_contract']['input_policy'])
            or any(receipt.get(key) != expected[key] for key in ('provider', 'model'))):
        reject('partial_call_binding', '真实调用的终态、归属或原发送范围无法核对；未恢复或调用模型')
    saved = call.data.get('receipt')
    critical = lambda value: {key: value[key] for key in CRITICAL if key in value}
    if not isinstance(saved, dict) or digest(critical(saved)) != digest(critical(receipt)):
        reject('partial_call_binding', '真实费用回执与原已保存回答不一致；未改任何原账本')
    return {'call_id': call.id, 'expected': expected, 'receipt_hash': digest(receipt),
        'ledger_receipt_hash': digest(critical(saved)), 'status': call.status,
        'reserved_tokens': call.reserved_tokens, 'charged_tokens': call.charged_tokens}


def current_value(row, original=None):
    value = row.data.get('review_value')
    original = row.data['local_review']['original_result'] if original is None else original
    if value is not None and (not isinstance(value, dict) or set(value) - {'result', 'note'}):
        reject()
    candidate = original if value is None or value.get('result') is None else value['result']
    note = '' if value is None else value.get('note', '')
    # A prior note-only value stays byte-for-byte in the old field/history.
    # The effective private candidate is explicit and separately receipted.
    try:
        return rd.ReviewValue(result=copy.deepcopy(candidate), note=note).model_dump(mode='json')
    except (ValueError, rd.HTTPException):
        reject()


def check_report(report, value, row):
    if (not isinstance(report, dict) or report.get('schema') != REPORT or report.get('source_hash') != row.data['preview']['source_hash']
            or report.get('value_hash') != digest(value['result'])
            or report.get('report_hash') != digest({k: v for k, v in report.items() if k != 'report_hash'})
            or report.get('can_apply') is not False or report.get('can_publish') is not False):
        reject()


def check_local_state(row, p):
    try:
        _check_local_state(row, p)
    except rd.HTTPException as error:
        if error.status_code == 409:
            raise
        reject()
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError):
        reject()


def _check_local_state(row, p):
    policy = row.data.get('local_review')
    if row.data.get('review_policy') != POLICY or not isinstance(policy, dict) or row.status != 'partial_review':
        reject()
    raw = checked_raw(row); binding = policy.get('call_binding', {})
    if (policy.get('schema') != LOCAL or policy.get('actor_id') != p.user_id or policy.get('workspace_id') != p.workspace_id
            or policy.get('draft_id') != row.id or policy.get('raw_hash') != raw['hash']
            or policy.get('source_hash') != row.data['preview']['source_hash']
            or policy.get('original_result') != raw['value'] or policy.get('original_hash') != digest(raw['value'])
            or policy.get('policy_hash') != digest({k: v for k, v in policy.items() if k != 'policy_hash'})
            or type(policy.get('activation_revision')) is not int or not 1 <= policy['activation_revision'] <= row.version
            or binding.get('expected') != rd.expected_call(row) or binding.get('call_id') != row.data['reserved_call_id']
            or binding.get('receipt_hash') != digest(raw['receipt'])
            or binding.get('ledger_receipt_hash') != digest({key: raw['receipt'][key] for key in CRITICAL if key in raw['receipt']})
            or binding.get('status') not in {'succeeded', 'failed'}
            or type(binding.get('reserved_tokens')) is not int or binding['reserved_tokens'] != row.data['preview']['input_summary']['token_bound']
            or type(binding.get('charged_tokens')) is not int or binding['charged_tokens'] < 0):
        reject()
    if policy.get('original_report') != analyze(raw['value'], row.data['preview']):
        reject()
    value = current_value(row)
    if row.data.get('validation_report') != analyze(value['result'], row.data['preview']):
        reject()
    revisions = []
    if not row.data.get('local_review_receipts'):
        reject()
    for name in ('local_review_receipts', 'review_history'):
        history = row.data.get(name, [])
        if not isinstance(history, list): reject()
        seen = set()
        for entry in history:
            receipt = entry.get('receipt', {})
            if name == 'review_history' and receipt.get('accepted_revision', 0) < policy['activation_revision']:
                continue
            try:
                nonce_valid = str(UUID(receipt.get('request_id', ''))) == receipt.get('request_id')
            except (ValueError, TypeError, AttributeError):
                nonce_valid = False
            if (not nonce_valid or receipt.get('request_id') in seen or receipt.get('actor_id') != p.user_id
                    or receipt.get('workspace_id') != p.workspace_id or receipt.get('draft_id') != row.id
                    or type(receipt.get('accepted_revision')) is not int
                    or not policy['activation_revision'] <= receipt['accepted_revision'] <= row.version
                    or receipt.get('schema') != (RECEIPT if name == 'local_review_receipts' else 'report-reading-review-receipt-v1')
                    or receipt.get('review_policy_hash') != policy['policy_hash']
                    or receipt.get('receipt_hash') != digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
                    or receipt.get('value_hash') != digest(entry.get('value'))):
                reject()
            seen.add(receipt.get('request_id'))
            revisions.append(receipt['accepted_revision'])
            request = {'revision': receipt['accepted_revision'] - 1, 'request_id': receipt['request_id']}
            if name == 'review_history':
                request['value'] = entry['value']
            if receipt.get('request_hash') != digest(request):
                reject()
            check_report(entry.get('validation_report'), entry['value'], row)
            if receipt.get('validation_report_hash') != entry['validation_report']['report_hash']:
                reject()
            if name == 'local_review_receipts' and (receipt.get('raw_hash') != raw['hash'] or receipt.get('source_hash') != policy['source_hash']):
                reject()
    if sorted(revisions) != list(range(policy['activation_revision'], row.version + 1)):
        reject()


def capacity(updated):
    try:
        size = len(json.dumps(updated, ensure_ascii=False, allow_nan=False).encode())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        reject()
    if size > rd.MAX_STATE_BYTES:
        reject('review_capacity', '保存容量已满，全部既有原答、费用与人工稿保留；本次未保存', 413)


def record_local_review(row, p, body, load_call):
    require(p, 'admin', 'operator'); rd.check_row(row, p)
    if row.data.get('schema') == 'report-evidence-draft-state-v1':
        reject('evidence_reading_endpoint_required', '本稿的分段原答与全局语义不能按旧本地复核策略解释；请使用分段全文读取界面')
    request_hash = digest(body.model_dump(mode='json'))
    history = row.data.get('local_review_receipts', [])
    for entry in history:
        if entry['receipt']['request_id'] == str(body.request_id):
            if entry['receipt']['request_hash'] != request_hash:
                reject('local_review_nonce_conflict', '此本地复核编号已有另一份请求；既有记录保留')
            return copy.deepcopy(entry['receipt'])
    if row.version != body.revision:
        reject('local_review_conflict', '草案已由另一页面修改；请保留本机输入并核对当前版本')
    if row.status not in {'failed', 'review', 'needs_clarification', 'partial_review'}:
        reject('local_review_not_ready', '原调用仍在处理或费用状态未知；不能本地恢复或重发')
    raw = checked_raw(row); binding = checked_call(row, raw, load_call())
    original_report = analyze(raw['value'], row.data['preview'])
    policy = row.data.get('local_review')
    if policy is None:
        policy = {'schema': LOCAL, 'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'draft_id': row.id,
            'raw_hash': raw['hash'], 'source_hash': row.data['preview']['source_hash'], 'original_result': copy.deepcopy(raw['value']),
            'original_hash': digest(raw['value']), 'original_report': original_report, 'call_binding': binding,
            'activation_revision': row.version + 1}
        policy['policy_hash'] = digest(policy)
    elif policy['call_binding'] != binding:
        reject()
    value = current_value(row, raw['value'])
    report = analyze(value['result'], row.data['preview'])
    receipt = {'schema': RECEIPT, 'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'draft_id': row.id,
        'request_id': str(body.request_id), 'request_hash': request_hash, 'accepted_revision': row.version + 1,
        'raw_hash': raw['hash'], 'source_hash': row.data['preview']['source_hash'], 'review_policy_hash': policy['policy_hash'],
        'value_hash': digest(value), 'validation_report_hash': report['report_hash']}
    receipt['receipt_hash'] = digest(receipt)
    updated = {**row.data, 'review_policy': POLICY, 'local_review': policy, 'validation_report': report,
        'local_review_receipts': [*history, {'receipt': receipt, 'value': value, 'validation_report': report}]}
    capacity(updated)
    row.data = updated; row.status = 'partial_review'; row.version += 1
    return copy.deepcopy(receipt)


def record_partial_review(row, p, body, load_call):
    require(p, 'admin', 'operator'); rd.check_row(row, p)
    if row.data.get('schema') == 'report-evidence-draft-state-v1':
        reject('evidence_reading_endpoint_required', '本稿的分段原答与全局语义请在分段全文读取界面保存；未丢弃任何字段')
    check_local_state(row, p)
    request_hash = digest(body.model_dump(mode='json'))
    history = row.data.get('review_history', [])
    for entry in history:
        if entry['receipt']['request_id'] == str(body.request_id):
            if entry['receipt']['request_hash'] != request_hash:
                reject('review_nonce_conflict', '原保存编号已用于另一份人工稿')
            return copy.deepcopy(entry['receipt'])
    if row.version != body.revision:
        reject('review_conflict', '草案已由另一页面修改；本次人工稿未覆盖服务器版本')
    raw = checked_raw(row)
    if checked_call(row, raw, load_call()) != row.data['local_review']['call_binding']:
        reject()
    value = body.value.model_dump(mode='json')
    report = analyze(value['result'], row.data['preview'])
    receipt = {'schema': 'report-reading-review-receipt-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'draft_id': row.id, 'request_id': str(body.request_id), 'request_hash': request_hash,
        'accepted_revision': row.version + 1, 'value_hash': digest(value),
        'review_policy_hash': row.data['local_review']['policy_hash'], 'validation_report_hash': report['report_hash']}
    receipt['receipt_hash'] = digest(receipt)
    updated = {**row.data, 'review_value': value, 'validation_report': report,
        'review_history': [*history, {'receipt': receipt, 'value': value, 'validation_report': report}]}
    capacity(updated)
    row.data = updated; row.version += 1
    return copy.deepcopy(receipt)


def view_fields(row):
    return {**{key: copy.deepcopy(row.data[key]) for key in ('review_policy', 'local_review', 'validation_report', 'local_review_receipts')},
            'effective_review_value': current_value(row)}


def reply_fields(row, receipt, entry):
    report = entry['validation_report']
    return {'accepted': True, 'validation_report': copy.deepcopy(report), 'can_apply': False, 'can_publish': False,
        'current_matches': row.version == receipt['accepted_revision'] and digest(current_value(row)) == receipt['value_hash']
            and row.data['validation_report']['report_hash'] == receipt['validation_report_hash']}


def local_reply(row, p, receipt):
    rd.check_row(row, p)
    entry = next((item for item in row.data['local_review_receipts'] if item['receipt'] == receipt), None)
    if entry is None:
        reject()
    return {'draft': rd.draft_view(row, p), 'receipt': copy.deepcopy(receipt), 'value': copy.deepcopy(entry['value']),
            **reply_fields(row, receipt, entry)}


@router.post('/report-drafts/{draft_id}/local-review')
def local_review(draft_id: UUID, body: LocalReviewCommand, p: rd.P, db: rd.DB):
    row = rd.owned(db, p, draft_id, True); before = row.version
    receipt = record_local_review(row, p, body, lambda: scoped(db, CloudCall, row.data.get('reserved_call_id'), p))
    if row.version != before:
        audit(db, p, 'setup.report_draft.local_review', row.id, {'request_id': str(body.request_id),
            'accepted_revision': row.version, 'policy_hash': receipt['review_policy_hash'], 'report_hash': receipt['validation_report_hash']})
    db.commit()
    return local_reply(row, p, receipt)


@router.get('/report-drafts/{draft_id}/local-review-receipts/{request_id}')
def local_review_receipt(draft_id: UUID, request_id: UUID, p: rd.P, db: rd.DB):
    row = rd.owned(db, p, draft_id)
    if row.data.get('schema') == 'report-evidence-draft-state-v1':
        reject('evidence_reading_endpoint_required', '请在分段全文读取界面核对本人原恢复回执；原记录保留')
    entry = next((item for item in row.data.get('local_review_receipts', []) if item['receipt']['request_id'] == str(request_id)), None)
    if entry is None:
        reject('receipt_not_found', '暂未找到本人此本地复核回执', 404)
    return local_reply(row, p, entry['receipt'])

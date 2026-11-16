"""Pure complete human catalogue documents, independent of machine wire.

Partial work is saveable. Position identities never change or repeat; missing
ownership blocks confirmation rather than deleting input inventory. Human text
is design intent, not an amendment to the original report or machine evidence.
"""
import copy
import re

from . import report_indexed_inputs_v178 as codec
from . import report_scoped_design_plan_v180 as planner
from .report_full_reading_semantics import _safe

SCHEMA = 'report-business-catalogue-edit-document-v211'
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_ACTIONS = 128
KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_:-]{0,127}$')


def need(ok):
    if not ok:
        raise ValueError('人工目录的字段、原位置或明确修改记录不符合合同；原稿保留')


def text(value, maximum):
    need(type(value) is str and len(value) <= maximum)


def keys(values, available):
    need(type(values) is list and len(values) <= len(available)
        and all(type(key) is str and key in available for key in values)
        and len(set(values)) == len(values))


def positions(values, count):
    need(type(values) is list and len(values) <= count)
    previous = -1
    for value in values:
        need(type(value) is int and previous < value < count)
        previous = value


def validate_document(document, input_count):
    """Strict shape/identity only; incompleteness is reported by validation()."""
    need(type(input_count) is int and 1 <= input_count <= codec.MAX_INPUTS)
    _safe(document)
    need(type(document) is dict and set(document) == {'schema', 'businesses', 'allocation_groups', 'questions'}
        and document['schema'] == SCHEMA)
    need(type(document['businesses']) is list and len(document['businesses']) <= planner.MAX_SCOPES)
    businesses = set()
    for business in document['businesses']:
        need(type(business) is dict and set(business) == {'key', 'name', 'purpose', 'boundary'})
        key = business['key']
        need(type(key) is str and KEY.fullmatch(key) is not None and key not in businesses)
        businesses.add(key)
        text(business['name'], 200); text(business['purpose'], 8000); text(business['boundary'], 8000)
    need(type(document['questions']) is list and len(document['questions']) <= input_count * 4)
    questions = set()
    for question in document['questions']:
        need(type(question) is dict and set(question) == {'key', 'kind', 'input_positions',
            'candidate_business_keys', 'question', 'status', 'resolution'})
        key = question['key']
        need(type(key) is str and KEY.fullmatch(key) is not None and key not in questions)
        questions.add(key)
        need(type(question['kind']) is str and question['kind'] in {'ownership', 'missing_info'}
            and type(question['status']) is str and question['status'] in {'open', 'resolved'})
        positions(question['input_positions'], input_count)
        keys(question['candidate_business_keys'], businesses)
        text(question['question'], 4000); text(question['resolution'], 8000)
    groups = document['allocation_groups']
    need(type(groups) is list and len(groups) <= input_count + planner.MAX_SCOPES)
    group_keys, owned = set(), set()
    for group in groups:
        need(type(group) is dict and set(group) == {'key', 'kind', 'business_keys',
            'input_positions', 'reason', 'question_keys'})
        key = group['key']
        need(type(key) is str and KEY.fullmatch(key) is not None and key not in group_keys)
        group_keys.add(key)
        need(type(group['kind']) is str and group['kind'] in {'business', 'shared', 'background', 'pending'})
        positions(group['input_positions'], input_count)
        need(not owned.intersection(group['input_positions']))
        owned.update(group['input_positions'])
        keys(group['business_keys'], businesses); keys(group['question_keys'], questions)
        text(group['reason'], 4000)
    need(len(codec.canonical(document).encode('utf-8')) <= MAX_DOCUMENT_BYTES)
    return copy.deepcopy(document)


def validation(document, input_count):
    value = validate_document(document, input_count)
    issues, owned, pending = [], set(), set()

    def issue(code, path, message, blocks=True):
        issues.append({'code': code, 'path': path, 'message': message, 'blocks_confirmation': blocks})

    if not value['businesses']:
        issue('no_businesses', '/businesses', '请先保留至少一项需要办理的业务。')
    names = set()
    for index, business in enumerate(value['businesses']):
        name = business['name']
        if not name.strip() or name != name.strip() or name in names:
            issue('business_name', f'/businesses/{index}/name', '业务名称须非空、无首尾空白且不重复。')
        names.add(name)
    for index, group in enumerate(value['allocation_groups']):
        owned.update(group['input_positions']); kind = group['kind']; recipients = group['business_keys']
        path = f'/allocation_groups/{index}'
        if not group['input_positions']:
            issue('empty_group', path + '/input_positions', '该分派组还未选择原声明。')
        if ((kind == 'business' and len(recipients) != 1)
                or (kind == 'shared' and len(recipients) < 2)
                or (kind == 'background' and recipients)):
            issue('group_recipients', path + '/business_keys', '请核对本业务、共同要求或背景的接收业务。')
        if kind in {'shared', 'background'} and not group['reason'].strip():
            issue('group_reason', path + '/reason', '请说明共同要求或背景资料的处理理由。')
        if kind in {'shared', 'background'} and len(group['reason']) > 2000:
            issue('plan_reason_capacity', path + '/reason', '完整理由已保留；范围计划中的共同要求或背景理由最多2000字。')
        if kind == 'pending':
            pending.update(group['input_positions'])
    for index, question in enumerate(value['questions']):
        if not question['question'].strip() or not question['input_positions']:
            issue('question_incomplete', f'/questions/{index}', '请补全问题说明及对应原声明。')
        if question['status'] == 'resolved' and not question['resolution'].strip():
            issue('resolution_missing', f'/questions/{index}/resolution', '已解决的问题须保留处理说明，也可恢复为待核。')
        if question['kind'] == 'ownership' and question['status'] == 'open':
            pending.update(question['input_positions'])
    unassigned = sorted(set(range(input_count)) - owned)
    if unassigned:
        issue('unassigned', '/allocation_groups', '仍有原声明没有分派，原位置库存完整保留。')
    if pending:
        issue('pending_ownership', '/questions', '业务归属仍待确定，请分派并处理对应归属问题。')
    missing = [row['key'] for row in value['questions']
        if row['kind'] == 'missing_info' and row['status'] == 'open']
    return {'unassigned_positions': unassigned, 'pending_ownership_positions': sorted(pending),
        'open_missing_info_keys': missing, 'issues': issues}


def partial_validation(document, projection):
    """Human adoption of a diagnosed original name is explicit, never inferred.

    The caller separately verifies the complete immutable partial projection.
    Other missing-information questions retain their ordinary non-blocking rule.
    """
    need(type(projection) is dict
        and projection.get('schema') == 'report-business-catalogue-partial-projection-v212'
        and type(projection.get('issues')) is list and type(projection.get('draft_seed')) is dict)
    value = validate_document(document, projection['input_count'])
    result = validation(value, projection['input_count'])
    seed = validate_document(projection['draft_seed'], projection['input_count'])
    businesses = {row['key']: row for row in value['businesses']}
    questions = {row['key']: row for row in value['questions']}
    originals = {row['key']: row for row in seed['questions']}
    for issue in projection['issues']:
        if issue['code'] != 'business_name_not_literal':
            continue
        business = businesses.get(issue['business_key'])
        if business is None or business['name'] != issue['original_name']:
            continue
        original = originals.get(issue['question_key'])
        question = questions.get(issue['question_key'])
        adopted = (original is not None and question is not None
            and all(question[key] == original[key]
                for key in ('kind', 'input_positions', 'candidate_business_keys', 'question'))
            and question['status'] == 'resolved' and bool(question['resolution'].strip()))
        if not adopted:
            result['issues'].append({'code': 'partial_name_requires_human_decision',
                'path': '/questions/' + issue['question_key'],
                'message': '该业务名称并非原引用中的明示名称。请修改业务名称，或在原命名问题中明确采用为用户命名并保存说明。',
                'blocks_confirmation': True})
    return result


def from_projection(projection):
    """Called only after actual-source projection validation at the store edge."""
    wire = projection['wire']
    document = {'schema': SCHEMA, 'businesses': [{key: copy.deepcopy(row[key])
        for key in ('key', 'name', 'purpose', 'boundary')} for row in wire['businesses']],
        'allocation_groups': [{'key': f'group_{index + 1:04d}', **copy.deepcopy(row)}
            for index, row in enumerate(wire['allocation_groups'])],
        'questions': [{**copy.deepcopy(row), 'status': 'open', 'resolution': ''} for row in wire['questions']]}
    return validate_document(document, projection['input_count'])


def validate_actions(actions, previous, document):
    """Retain explicit structural decisions instead of inventing merge intent."""
    need(type(actions) is list and len(actions) <= MAX_ACTIONS)
    available = {row['key'] for row in previous['businesses']}
    final = {row['key'] for row in document['businesses']}
    _safe(actions)
    for action in actions:
        need(type(action) is dict and set(action) == {'kind', 'source_business_keys', 'target_business_keys', 'reason'})
        kind = action['kind']; source = action['source_business_keys']; target = action['target_business_keys']
        need(type(kind) is str and kind in {'add', 'delete', 'split', 'merge', 'undo'})
        keys(source, available)
        need(type(target) is list and len(target) <= planner.MAX_SCOPES
            and all(type(key) is str and KEY.fullmatch(key) is not None for key in target)
            and len(set(target)) == len(target))
        text(action['reason'], 2000)
        need(bool(action['reason'].strip()))
        if kind == 'add': need(not source and bool(target) and not available.intersection(target))
        elif kind == 'delete': need(bool(source) and not target)
        elif kind == 'split': need(len(source) == 1 and len(target) >= 2)
        elif kind == 'merge': need(len(source) >= 2 and len(target) == 1)
        else: need(bool(source) or bool(target))
        need(not (available - set(source)).intersection(target))
        available = (available - set(source)) | set(target)
        need(len(available) <= planner.MAX_SCOPES)
    need(available == final)
    return copy.deepcopy(actions)


def scope_ledger(document, *, indexed, input_claims, source_snapshot_hash):
    frozen = codec.check_indexed_inputs(indexed, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash)
    value = validate_document(document, frozen['input_count'])
    need(not any(row['blocks_confirmation'] for row in validation(value, frozen['input_count'])['issues']))
    scopes = {row['key']: {'scope_id': row['key'], 'name': row['name'],
        'claim_positions': [], 'context_positions': []} for row in value['businesses']}
    shared, background = [], []
    for group in value['allocation_groups']:
        for position in group['input_positions']:
            if group['kind'] == 'business': scopes[group['business_keys'][0]]['claim_positions'].append(position)
            elif group['kind'] == 'shared':
                shared.append({'input_position': position, 'scope_ids': copy.deepcopy(group['business_keys']),
                    'reason': group['reason']})
            elif group['kind'] == 'background': background.append({'input_position': position, 'reason': group['reason']})
    for scope in scopes.values(): scope['claim_positions'].sort()
    return {'schema': planner.LEDGER, 'source_snapshot_hash': source_snapshot_hash,
        'indexed_inputs_hash': frozen['inputs_hash'], 'input_claims_hash': frozen['input_claims_hash'],
        'input_count': frozen['input_count'], 'scopes': list(scopes.values()),
        'shared': sorted(shared, key=lambda row: row['input_position']),
        'background': sorted(background, key=lambda row: row['input_position'])}

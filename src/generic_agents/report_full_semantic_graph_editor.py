"""Read-only, shared graph editor evidence; never execution or new consent.

Every displayed adoption is cold-checked against its real immutable ledger.
Private source-review decisions, reading notes, raw commands and credentials are
not editor context. Inactive designs remain readable without renewing a grant.
"""
import copy
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from .contracts import digest
from . import report_full_semantic_graph_adoption as base
from . import report_full_semantic_region_mapping as positions
from . import report_full_semantic_schema_adoption as schemas
from . import report_full_semantic_schema_protection as protection
from . import report_full_semantic_source_review as review
from .report_full_semantic_graph_binding_v3 import required_owner_fields
from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision as Revision
from .workflow_graph_event_sources import UNAVAILABLE


router = APIRouter()
SCHEMA = 'report-full-semantic-graph-editor-context-v1'
MAX_PROOFS = 100
MAX_CHAIN = 257
# The current revision authority counts at most 256 revision rows, followed
# by their immutable root. It remains the authority for each checked entry.
MAX_ADOPTIONS = 20 * MAX_CHAIN
INACTIVE_REASON = '此完整设计已被后续版本替换；原位置证明和采用历史保留。'
UNVERIFIED_REASON = '此位置证明当前尚不能确认原来源；已保存记录保留。'


def _need(condition, message='完整执行编辑上下文与真实来源或采用历史无法核对；原记录保留'):
    if not condition:
        base.fail(message)


def _ids(db, model, p, identity, maximum):
    values = list(db.scalars(select(model.id).where(model.workspace_id == p.workspace_id,
        model.handoff_id == str(identity)).order_by(model.created_at, model.id).limit(maximum + 1)))
    if len(values) > maximum:
        base.fail('完整执行历史超过一次完整读取上限；服务器记录保留', 413)
    _need(all(type(value) is str for value in values) and len(set(values)) == len(values))
    return values


def _proofs(db, p, identity, active, manifest):
    result = []
    for value in _ids(db, positions.Proof, p, identity, MAX_PROOFS):
        try:
            proof_id = UUID(value)
        except ValueError:
            base.fail('已保存位置证明的编号无法核对；原记录保留')
        public = positions.get_proof(identity, proof_id, p, db)
        _need(public['id'] == value and public['proof']['handoff_id'] == str(identity)
            and public['proof']['manifest_hash'] == manifest['manifest_hash'])
        verified = active and public['current_source_verified'] is True
        reason = public['reason']
        _need(reason is None or type(reason) is str)
        if not verified and not reason:
            reason = UNVERIFIED_REASON if active else INACTIVE_REASON
        result.append({'id': value, 'proof_hash': public['proof']['proof_hash'],
            'current_source_verified': verified, 'reason': reason})
    return result


def _schema_origins(db, p, identity, config, owners, *, origin_schema=None):
    # This historical authority checks all headers, including forged extras,
    # and exact adopted definitions. It deliberately does not require a live
    # source, so an unrelated setup edit or old design read stays available.
    headers = protection._authorized_state(db, p, config)
    adopted = _ids(db, schemas.Adoption, p, identity, 70)
    _need(set(adopted).issubset({header['id'] for header in headers}),
        '此设计已经采用的数据结构来源缺失，不能当作手工定义重新显示；原记录保留')
    own_ids = set()
    for header in headers:
        row = db.get(schemas.Adoption, header['id'])
        _need(row is not None and row.workspace_id == p.workspace_id)
        if row.handoff_id == str(identity):
            own_ids.add(row.id)
    inventory = {item['owner_path']: item for item in owners}
    order = {item['owner_path']: index for index, item in enumerate(owners)}
    result = {'objects': [], 'relationships': []}
    if not own_ids:
        return result
    for label, kind, definitions_key, origins_key in (
            ('objects', 'object', 'instanceObjects', 'instanceObjectOrigins'),
            ('relationships', 'relationship', 'instanceRelationTypes', 'instanceRelationOrigins')):
        definitions = {item['name']: item for item in config.get(definitions_key, [])}
        for name, origin in config.get(origins_key, {}).items():
            if origin.get('schema') != (origin_schema or protection.ORIGIN_SCHEMA) or origin.get('adoption_id') not in own_ids:
                continue
            owner = inventory.get(origin['owner_path'])
            _need(owner is not None and owner['kind'] == kind
                and owner['value_hash'] == origin['owner_value_hash']
                and origin['kind'] == kind and origin['name'] == name and name in definitions)
            result[label].append({'name': name, 'origin': copy.deepcopy(origin),
                'definition': copy.deepcopy(definitions[name])})
        result[label].sort(key=lambda item: order[item['origin']['owner_path']])
    return result


def _on_lineage(children, previous, current):
    seen = set()
    while previous not in seen:
        if previous == current:
            return True
        seen.add(previous)
        previous = children.get(previous)
        if previous is None:
            return False
    return False


def _adoptions(db, p, identity, configuration, design, active):
    config = configuration.data
    markers = config.get(base.CONFIG_KEY, {})
    _need(type(markers) is dict)
    names = {item['name'] for item in design['result']['businesses']}
    selected = _ids(db, base.Adoption, p, identity, MAX_ADOPTIONS)
    selected += _ids(db, Revision, p, identity, MAX_ADOPTIONS)
    _need(len(set(selected)) == len(selected))
    if len(selected) > MAX_ADOPTIONS:
        base.fail('完整执行历史超过一次完整读取上限；服务器记录保留', 413)
    checked = {}
    lineage = None

    def source_records():
        nonlocal lineage
        if lineage is None:
            records, _, children = review.replacement.record_set(db, p)
            _need(str(identity) in records
                and protection._same(records[str(identity)]['design'], design))
            lineage = (records, children)
        return lineage

    def read(value):
        if value not in checked:
            _need(type(value) is str)
            row = base.lookup_adoption(db, value)
            data = base.checked(db, p, row)
            record = data['record']
            _need(row.id == value and row.workspace_id == p.workspace_id
                and row.configuration_id == configuration.id
                and type(record['accepted_revision']) is int
                and record['accepted_revision'] <= configuration.version
                and record['binding']['source_design']['handoff_id'] == row.handoff_id
                and record['origin']['adoption_id'] == value)
            if row.handoff_id == str(identity):
                _need(protection._same(record['binding']['source_design'], design))
            else:
                records, _ = source_records()
                _need(row.handoff_id in records
                    and protection._same(record['binding']['source_design'], records[row.handoff_id]['design']))
            checked[value] = (row, data)
            if len(checked) > MAX_ADOPTIONS:
                base.fail('完整执行历史超过一次完整读取上限；服务器记录保留', 413)
        return checked[value]

    for value in selected:
        row, _ = read(value)
        _need(row.handoff_id == str(identity))

    # A new source version may retain the real predecessor as the current
    # execution head. A name match is never sufficient replacement authority.
    for name in sorted(names.intersection(markers)):
        origin = markers[name]
        _need(type(origin) is dict and type(origin.get('adoption_id')) is str)
        row, data = read(origin['adoption_id'])
        _need(protection._same(data['record']['origin'], origin)
            and data['record']['binding']['business_name'] == name)
        if row.handoff_id != str(identity):
            records, children = source_records()
            _need(row.handoff_id in records and (_on_lineage(children, row.handoff_id, str(identity))
                or not active and _on_lineage(children, str(identity), row.handoff_id)),
                '当前同名业务属于另一份完整设计，不能据此选择修订前版本；原记录保留')

    pending = list(checked)
    while pending:
        value = pending.pop()
        _, data = read(value)
        parent = data['record'].get('parent_id')
        if parent is not None and parent not in checked:
            read(parent)
            pending.append(parent)

    children = {}
    for value, (_, data) in checked.items():
        parent = data['record'].get('parent_id')
        if parent is not None:
            _need(type(parent) is str and parent in checked and parent not in children)
            children[parent] = value
    for value, (row, data) in checked.items():
        current = value; seen = set(); root = data['record'].get('root_id', row.id)
        while True:
            _need(current not in seen and len(seen) < MAX_CHAIN)
            seen.add(current)
            ancestor, prior = checked[current]
            _need(prior['record']['binding']['business_name'] == data['record']['binding']['business_name']
                and prior['record'].get('root_id', ancestor.id) == root)
            parent = prior['record'].get('parent_id')
            if parent is None:
                _need(current == root)
                break
            _need(checked[parent][1]['record']['accepted_revision'] < prior['record']['accepted_revision'])
            current = parent

    result = []
    for value, (row, data) in sorted(checked.items(),
            key=lambda item: (item[1][1]['record']['accepted_revision'], item[0])):
        record = data['record']; name = record['binding']['business_name']
        result.append({'business_name': name, 'origin': copy.deepcopy(record['origin']),
            'receipt': copy.deepcopy(data['receipt']), 'binding': copy.deepcopy(record['binding']),
            'execution_scope': _execution_scope(data),
            'root_id': record.get('root_id', row.id), 'parent_id': record.get('parent_id'),
            'is_current': name in markers and protection._same(markers[name], record['origin'])})
    return result


def _execution_scope(data):
    graph = data['record']['binding']['graph']
    ability = {node['id']: node for node in graph['nodes'] if node['kind'] == 'ability'}
    command = data['command']; actions = command['node_actions']; external = command['external_node_ids']
    _need(type(actions) is dict and set(actions) == set(ability)
        and all(type(value) is str and value in {'observe', 'state', 'data', 'learn'}
            for value in actions.values())
        and type(external) is list and all(type(value) is str for value in external)
        and len(external) == len(set(external))
        and set(external) == {value for value, node in ability.items()
            if node['execution']['adapter'] in {'http', 'mcp', 'cloud_extractor', 'business_reasoning'}},
        '已采用执行设计的节点许可范围无法核对；不能从当前能力推测旧许可')
    return {'node_actions': copy.deepcopy(actions), 'external_node_ids': copy.deepcopy(external)}


@router.get(base.PREFIX + '/editor-context')
def get_context(identity: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    configuration, record, heads, manifest, _, _ = review.context(db, p, identity)
    design = record['design']; active = str(identity) in heads
    _need(design.get('schema') == 'report-full-semantic-design-v1',
        '此设计采用独立的新对象执行合同，请使用对应入口；原记录保留')
    owners = required_owner_fields(design)
    result = {'schema': SCHEMA, 'handoff_id': str(identity), 'active': active,
        'design': copy.deepcopy(design), 'manifest': copy.deepcopy(manifest),
        'configuration': {'revision': configuration.version, 'hash': digest(configuration.data)},
        'owners': copy.deepcopy(owners), 'proofs': _proofs(db, p, identity, active, manifest),
        'schema_origins': _schema_origins(db, p, identity, configuration.data, owners),
        'adoptions': _adoptions(db, p, identity, configuration, design, active),
        'event_wait': {'available': False, 'reason': UNAVAILABLE}}
    return review.bounded(result)

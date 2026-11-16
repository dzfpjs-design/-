"""Explicit new-object graph decisions using the existing durable transactions.

No legacy command, binding, receipt or task is retagged. Shared transaction
helpers retain their real nonce, history, source and execution authority checks.
"""
import copy
import re
import sys
from typing import Literal
from uuid import UUID, uuid5
from fastapi import APIRouter
from pydantic import Field, field_validator
from sqlalchemy import select, text, or_, func, cast, literal
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from .contracts import digest
from .models import Audit, SetupDraft, SetupRevision
from . import report_full_semantic_graph_adoption as base
from .report_object_graph_binding_v171 import build_binding, check_binding, SCHEMA as BINDING_SCHEMA

router = APIRouter()
PREFIX = '/full-semantic-object-graphs-v171/{identity}'
LEDGER = 'report-object-graph-ledger-v171'
ID_NAMESPACE = 'object-graph-v171:'
PREVIEW_SCHEMA = 'report-object-graph-preview-v171'
RESULT_SCHEMA = 'report-object-graph-adoption-result-v171'
RECOVERY_SCHEMA = 'report-object-graph-recovery-v171'
REVISION_LEDGER = 'report-object-graph-revision-ledger-v171'
REVISION_RECORD = 'report-object-graph-revision-record-v171'
REVISION_RECEIPT = 'report-object-graph-revision-receipt-v171'
REVISION_PREVIEW_SCHEMA = 'report-object-graph-revision-preview-v171'
REVISION_RESULT_SCHEMA = 'report-object-graph-revision-result-v171'
REVISION_RECOVERY_SCHEMA = 'report-object-graph-revision-recovery-v171'
REVISION_COMMAND_HASH = 'report-object-graph-revision-command-hash-v171'
REVISION_ID_NAMESPACE = 'object-graph-revision-v171:'


class Preview(base.Preview):
    schema: Literal['report-object-graph-preview-command-v171']
    event_source_bindings: dict[str, dict]
    event_admission_policy: dict


class Adopt(Preview):
    schema: Literal['report-object-graph-command-v171']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=base.HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True: raise ValueError('请独立确认新对象的完整流程、角色、事件来源和等待范围')
        return value


class Parent(base.SafeContract):
    id: UUID
    receipt_hash: str = Field(strict=True, pattern=base.HASH)


class RevisionPreview(Preview):
    schema: Literal['report-object-graph-revision-preview-command-v171']
    parent: Parent


class RevisionAdopt(RevisionPreview):
    schema: Literal['report-object-graph-revision-command-v171']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=base.HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True: raise ValueError('请独立确认完整后继执行设计；已有版本与任务保留')
        return value


def family():
    return sys.modules[__name__]


def preview_body(body):
    values = body.model_dump(mode='json')
    return Preview.model_validate({key: ('report-object-graph-preview-command-v171'
        if key == 'schema' else values[key]) for key in Preview.model_fields})


def revision_preview_body(body):
    values = body.model_dump(mode='json')
    return RevisionPreview.model_validate({key: ('report-object-graph-revision-preview-command-v171'
        if key == 'schema' else values[key]) for key in RevisionPreview.model_fields})


def binding_arguments(body):
    return {'event_source_bindings': copy.deepcopy(body.event_source_bindings),
        'event_admission_policy': copy.deepcopy(body.event_admission_policy)}


def check_command_binding(command, binding):
    if (binding.get('schema') != BINDING_SCHEMA
            or binding['event_source_bindings'] != command.event_source_bindings
            or binding['event_admission_policy'] != command.event_admission_policy):
        raise ValueError('object_graph_command_binding')


def capabilities(db, p, body, *, lock=False):
    # Same typed event contract and actual live capability predicate, explicitly
    # called by this family; this is not an old binding builder or conversion.
    from . import report_full_semantic_graph_adoption_v4 as events
    return events.capabilities(db, p, body, lock=lock)


def origin_value(identity, binding, *, command_hash):
    if (binding.get('schema') != BINDING_SCHEMA or type(command_hash) is not str
            or re.fullmatch(base.HASH, command_hash) is None):
        raise ValueError('object_graph_origin')
    return {'schema': 'report-object-graph-origin-v171', 'adoption_id': identity,
        'handoff_id': binding['source_design']['handoff_id'], 'business_name': binding['business_name'],
        'binding_hash': binding['binding_hash'], 'command_hash': command_hash}


def check_audit(db, p, row, data):
    """An internally sealed graph row cannot replace its independent audit."""
    try:
        record = data['record']; revision = data['schema'] == REVISION_LEDGER
        if data['schema'] not in {LEDGER, REVISION_LEDGER}:
            raise ValueError('object_graph_audit_family')
        action = 'setup.full_semantic_graph.revised' if revision else 'setup.full_semantic_graph.adopted'
        details = {'request_id': row.request_id, 'record_hash': record['record_hash']}
        if revision:
            details.update(parent_id=row.parent_id, root_id=row.root_id,
                accepted_revision=record['accepted_revision'], effect_scope='future_releases_only')
        rows = list(db.scalars(select(Audit).where(Audit.object_id == row.id,
            Audit.action == action).execution_options(populate_existing=True)))
        if (len(rows) != 1 or rows[0].workspace_id != row.workspace_id
                or rows[0].workspace_id != p.workspace_id or rows[0].actor_id != row.actor_id
                or rows[0].action != action or rows[0].object_id != row.id
                or type(rows[0].data) is not dict or set(rows[0].data) != set(details)
                or digest(rows[0].data) != digest(details)):
            raise ValueError('object_graph_audit')
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        base.fail('新对象执行采用的独立审计记录无法冷核；原回执与配置保留')


def _request_identities(p, identity, request_id):
    namespace = UUID(str(identity))
    suffix = p.workspace_id + ':' + p.user_id + ':' + str(request_id)
    return [str(uuid5(namespace, prefix + suffix)) for prefix in (ID_NAMESPACE, REVISION_ID_NAMESPACE)]


def require_absent_request(db, p, identity, request_id):
    """Called only under the common nonce lock, before declaring/reusing absence.

    The root/revision tables are not their own negative evidence. Immutable
    audit and setup history can still establish that a lost row once existed.
    A matching damaged/unknown header also blocks; it is never source consent.
    """
    identifiers = _request_identities(p, identity, request_id)
    audit_id = db.scalar(select(Audit.id).where(or_(Audit.object_id.in_(identifiers),
        Audit.data['request_id'].as_string() == str(request_id))).limit(1))
    if audit_id is not None:
        base.fail('原执行请求仍有独立审计记录，不能当作未发送或再次授权；原记录保留')
    # Header comparison is deliberately marker-independent. An unknown or
    # retagged origin carrying the issued deterministic id is still evidence.
    path = '$.fullSemanticGraphBindings.* ? (@.adoption_id == $root || @.adoption_id == $revision)'
    variables = literal({'root': identifiers[0], 'revision': identifiers[1]}, type_=JSONB)
    for model in (SetupDraft, SetupRevision):
        found = db.scalar(select(model.id).where(model.workspace_id == p.workspace_id,
            func.jsonb_path_exists(model.data, cast(path, JSONPATH), variables)).limit(1))
        if found is not None:
            base.fail('原执行请求仍有真实配置或历史出处，采用行缺失不能当作未保存；原记录保留')


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    base.require(p, 'admin', 'operator')
    return base.prepare(db, p, identity, body, family=family())[1]


@router.post(PREFIX + '/adopt')
def adopt(identity: UUID, body: Adopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return base.execute_adopt(identity, body, p, db, family=family())


def recovery(db, p, identity, request_id, *, revision=False):
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key': 'full-graph-v3:' + str(request_id)})
    row = base.lookup_request(db, request_id)
    schema = REVISION_RECOVERY_SCHEMA if revision else RECOVERY_SCHEMA
    if row is None:
        require_absent_request(db, p, identity, request_id)
        return {'schema': schema, 'found': False}
    if row.workspace_id != p.workspace_id or row.actor_id != p.user_id or row.handoff_id != str(identity):
        base.fail('原请求属于其他身份或工作区', 403)
    if row.data.get('schema') != (REVISION_LEDGER if revision else LEDGER):
        base.fail('原请求已用于另一执行合同；保留原字节和编号，不能转换或重发')
    return {'found': True, **base.reply(db, p, row)}


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return recovery(db, p, identity, request_id)


@router.post(PREFIX + '/revisions/preview')
def revision_preview(identity: UUID, body: RevisionPreview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    from . import report_full_semantic_graph_revision as revisions
    base.require(p, 'admin', 'operator')
    return revisions.prepare(db, p, identity, body, family=family())[1]


@router.post(PREFIX + '/revisions/adopt')
def revision_adopt(identity: UUID, body: RevisionAdopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    from . import report_full_semantic_graph_revision as revisions
    return revisions.execute_adopt(identity, body, p, db, family=family())


@router.get(PREFIX + '/revisions/requests/{request_id}')
def recover_revision(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return recovery(db, p, identity, request_id, revision=True)


@router.get(PREFIX + '/revisions/{revision_identity}')
def get_revision(identity: UUID, revision_identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    from . import report_full_semantic_graph_revision as revisions
    row = base.lookup_adoption(db, revision_identity)
    if row is None or row.handoff_id != str(identity) or row.data.get('schema') != REVISION_LEDGER:
        base.fail('未找到此完整设计的新对象执行修订', 404)
    return revisions.reply(db, p, row)


from . import report_object_graph_editor_v171 as _editor
router.include_router(_editor.router)
base.router.include_router(router)

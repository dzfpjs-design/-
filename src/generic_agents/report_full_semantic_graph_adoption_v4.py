"""Explicit typed-event graph commands; no defaults extend old authorization.

The existing append-only root/revision transactions are shared. Their immutable
wire formats are selected explicitly, never patched globally or downcast to v3.
"""
import copy
import re
import sys
from typing import Literal
from uuid import UUID
from fastapi import APIRouter
from pydantic import Field, field_validator
from . import report_full_semantic_graph_adoption as base
from . import report_full_semantic_graph_revision as revisions
from .report_full_semantic_graph_binding_v4 import build_binding, check_binding, SCHEMA as BINDING_SCHEMA

router = APIRouter()
PREFIX = '/full-semantic-graphs-v4/{identity}'
LEDGER = 'report-full-semantic-graph-ledger-v4'
ID_NAMESPACE = 'graph-v4:'
PREVIEW_SCHEMA = 'report-full-semantic-graph-preview-v4'
REVISION_LEDGER = 'report-full-semantic-graph-revision-ledger-v4'
REVISION_RECORD = 'report-full-semantic-graph-revision-record-v4'
REVISION_RECEIPT = 'report-full-semantic-graph-revision-receipt-v4'
REVISION_PREVIEW_SCHEMA = 'report-full-semantic-graph-revision-preview-v4'
REVISION_COMMAND_HASH = 'report-full-semantic-graph-revision-command-hash-v4'
REVISION_ID_NAMESPACE = 'graph-revision-v4:'


class Preview(base.Preview):
    schema: Literal['report-full-semantic-graph-preview-command-v4']
    event_source_bindings: dict[str, dict]
    event_admission_policy: dict


class Adopt(Preview):
    schema: Literal['report-full-semantic-graph-command-v4']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=base.HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True: raise ValueError('请独立确认完整流程、真实事件来源及等待范围')
        return value


class RevisionPreview(Preview):
    schema: Literal['report-full-semantic-graph-revision-preview-command-v4']
    parent: revisions.Parent


class RevisionAdopt(RevisionPreview):
    schema: Literal['report-full-semantic-graph-revision-command-v4']
    request_id: UUID
    preview_hash: str = Field(strict=True, pattern=base.HASH)
    consent: Literal[True]
    semantic_mode: Literal['workspace_workflow_design']

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True: raise ValueError('请独立确认后续版本的完整执行及事件等待设计；已有任务保留')
        return value


def family():
    return sys.modules[__name__]


def preview_body(body):
    values = body.model_dump(mode='json')
    return Preview.model_validate({key: ('report-full-semantic-graph-preview-command-v4'
        if key == 'schema' else values[key]) for key in Preview.model_fields})


def revision_preview_body(body):
    values = body.model_dump(mode='json')
    return RevisionPreview.model_validate({key: ('report-full-semantic-graph-revision-preview-command-v4'
        if key == 'schema' else values[key]) for key in RevisionPreview.model_fields})


def binding_arguments(body):
    return {'event_source_bindings': copy.deepcopy(body.event_source_bindings),
        'event_admission_policy': copy.deepcopy(body.event_admission_policy)}


def check_command_binding(command, binding):
    if (binding['event_source_bindings'] != command.event_source_bindings
            or binding['event_admission_policy'] != command.event_admission_policy):
        raise ValueError('graph_v4_command_binding')


def capabilities(db, p, body, *, lock=False):
    from .graph_wait_sources_v118 import check_policy, check_bindings, verify_bindings
    from .full_semantic_graph_v1 import validate_graph
    from .models import Capability
    from .services import scoped
    try:
        graph = validate_graph(body.graph)
        check_policy(body.event_admission_policy)
        frozen = check_bindings(graph, body.event_source_bindings)
        actual = verify_bindings(db, p, graph, frozen, lock=lock)
        if actual != frozen: raise ValueError('registered_event_contract_changed')
        if lock:
            for identity in sorted({n['execution']['capability_id'] for n in graph['nodes'] if n['kind'] == 'ability'}):
                scoped(db, Capability, identity, p, True)
        return base.ability_capabilities(db, p, graph, body.external_node_ids)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
        base.fail('事件等待来源或完整执行设计无法核对：' + str(error), 422)


def origin_value(identity, binding, *, command_hash):
    if (binding.get('schema') != BINDING_SCHEMA or type(command_hash) is not str
            or re.fullmatch(base.HASH, command_hash) is None):
        raise ValueError('graph_v4_origin')
    return {'schema': 'report-full-semantic-graph-origin-v4', 'adoption_id': identity,
        'handoff_id': binding['source_design']['handoff_id'], 'business_name': binding['business_name'],
        'binding_hash': binding['binding_hash'], 'command_hash': command_hash}


@router.post(PREFIX + '/preview')
def preview(identity: UUID, body: Preview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    base.require(p, 'admin', 'operator')
    return base.prepare(db, p, identity, body, family=family())[1]


@router.post(PREFIX + '/adopt')
def adopt(identity: UUID, body: Adopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return base.execute_adopt(identity, body, p, db, family=family())


def recovery(db, p, identity, request_id, *, revision=False):
    row = base.lookup_request(db, request_id)
    schema = 'report-full-semantic-graph-revision-recovery-v4' if revision else 'report-full-semantic-graph-recovery-v4'
    if row is None: return {'schema': schema, 'found': False}
    if (row.workspace_id != p.workspace_id or row.actor_id != p.user_id or row.handoff_id != str(identity)):
        base.fail('原请求属于其他身份或工作区', 403)
    if row.data.get('schema') != (REVISION_LEDGER if revision else LEDGER):
        base.fail('原请求编号已用于其他执行设计命令，不能改用新版请求')
    return {'found': True, **base.reply(db, p, row)}


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return recovery(db, p, identity, request_id)


@router.post(PREFIX + '/revisions/preview')
def revision_preview(identity: UUID, body: RevisionPreview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    base.require(p, 'admin', 'operator')
    return revisions.prepare(db, p, identity, body, family=family())[1]


@router.post(PREFIX + '/revisions/adopt')
def revision_adopt(identity: UUID, body: RevisionAdopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return revisions.execute_adopt(identity, body, p, db, family=family())


@router.get(PREFIX + '/revisions/requests/{request_id}')
def recover_revision(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return recovery(db, p, identity, request_id, revision=True)


@router.get(PREFIX + '/revisions/{revision_identity}')
def get_revision(identity: UUID, revision_identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    row = base.lookup_adoption(db, revision_identity)
    if row is None or row.handoff_id != str(identity) or row.data.get('schema') != REVISION_LEDGER:
        base.fail('未找到此完整设计的新版执行修订', 404)
    return revisions.reply(db, p, row)

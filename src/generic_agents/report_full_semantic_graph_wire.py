"""Lossless JSON transport for the existing graph APIs, without new authority.

The canonical string retains Python integer/float spelling after browser JSON
decoding. All mutations, consent, CAS, recovery and actor checks remain in the
original endpoints. Hashing a typed command neither previews nor adopts it.
"""
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from . import report_full_semantic_graph_adoption as base
from . import report_full_semantic_graph_editor as editor
from . import report_full_semantic_graph_revision as revisions
from . import report_full_semantic_graph_adoption_v4 as v4
from . import report_full_semantic_graph_editor_v4 as editor_v4
from . import report_object_graph_adoption_v171 as objects_v171
from . import report_object_graph_editor_v171 as object_editor_v171
from .report_reading_drafts import SafeContract


SCHEMA = 'report-full-semantic-graph-canonical-wire-v1'
PREFIX = '/full-semantic-graph-wire/{identity}'
MAX_CANONICAL_BYTES = 24 * 1024 * 1024
router = APIRouter(prefix=PREFIX)


def canonical_wire(value):
    """Return the original public DTO plus its exact Python canonical bytes."""
    try:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False)
        size = len(canonical.encode('utf-8'))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        base.fail('完整流程响应不能无损传输，原记录与请求保留')
    if size > MAX_CANONICAL_BYTES:
        base.fail('完整流程响应超过一次无损读取上限，服务器记录保留', 413)
    return {'schema': SCHEMA, 'value': value, 'canonical': canonical}


def _shared_handoff(db, p, identity):
    # Frozen root/replacement ledgers establish shared workspace visibility.
    # Do not require a live source, proof, capability or original private draft:
    # old unknown nonces must remain recoverable after those become stale.
    records, _, _ = base.review.replacement.record_set(db, p)
    if str(identity) not in records:
        base.fail('未找到当前工作区已共享的完整设计', 404)


@router.get('/editor-context')
def editor_context(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(editor.get_context(identity, p, db))


@router.get('/ability-options')
def ability_options(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    _shared_handoff(db, p, identity)
    from .workspace_api import ability_options as existing_options
    return canonical_wire(existing_options(p, db))


@router.post('/preview')
def preview(identity: UUID, body: base.Preview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(base.preview(identity, body, p, db))


@router.post('/adopt')
def adopt(identity: UUID, body: base.Adopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return canonical_wire(base.adopt(identity, body, p, db))


@router.get('/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(base.recover(identity, request_id, p, db))


@router.post('/revisions/preview')
def revision_preview(identity: UUID, body: revisions.Preview,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(revisions.preview(identity, body, p, db))


@router.post('/revisions/adopt')
def revision_adopt(identity: UUID, body: revisions.Adopt,
        p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return canonical_wire(revisions.adopt(identity, body, p, db))


@router.get('/revisions/requests/{request_id}')
def revision_recover(identity: UUID, request_id: UUID,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(revisions.recover(identity, request_id, p, db))


@router.get('/configuration')
def configuration(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    _shared_handoff(db, p, identity)
    # setup.get_draft initializes and commits a draft. Use its actual projection
    # over the existing SELECT-only row instead, keeping this ReadP route pure.
    row = base.review.full.old().configuration_row(db, p)
    return canonical_wire(base.review.full.old().setup().draft_view(row))


class CommandHash(SafeContract):
    kind: Literal['root', 'revision']
    command: dict


@router.post('/command-hash')
def command_hash(identity: UUID, body: CommandHash,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    _shared_handoff(db, p, identity)
    module = revisions if body.kind == 'revision' else base
    try:
        command = module.Adopt.model_validate(body.command)
    except ValidationError:
        raise HTTPException(422, '完整原命令字段无法核对，原输入与请求保留') from None
    return canonical_wire({'schema': 'report-full-semantic-graph-command-hash-v1',
        'kind': body.kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(identity), 'command': command.model_dump(mode='json'),
        'command_hash': module.command_hash(p, identity, command)})


@router.get('/v4/editor-context')
def v4_editor_context(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(editor_v4.get_context(identity, p, db))


@router.post('/v4/preview')
def v4_preview(identity: UUID, body: v4.Preview, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(v4.preview(identity, body, p, db))


@router.post('/v4/adopt')
def v4_adopt(identity: UUID, body: v4.Adopt, p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return canonical_wire(v4.adopt(identity, body, p, db))


@router.get('/v4/requests/{request_id}')
def v4_recover(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(v4.recover(identity, request_id, p, db))


@router.post('/v4/revisions/preview')
def v4_revision_preview(identity: UUID, body: v4.RevisionPreview,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(v4.revision_preview(identity, body, p, db))


@router.post('/v4/revisions/adopt')
def v4_revision_adopt(identity: UUID, body: v4.RevisionAdopt,
        p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return canonical_wire(v4.revision_adopt(identity, body, p, db))


@router.get('/v4/revisions/requests/{request_id}')
def v4_revision_recover(identity: UUID, request_id: UUID,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(v4.recover_revision(identity, request_id, p, db))


@router.get('/v4/configuration')
def v4_configuration(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return configuration(identity, p, db)


@router.post('/v4/command-hash')
def v4_command_hash(identity: UUID, body: CommandHash,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    # Read-only historical hashing must survive source/proof revocation, just
    # like the original wire route. The path, not a guessed default, picks v4.
    _shared_handoff(db, p, identity)
    try:
        command = (v4.RevisionAdopt if body.kind == 'revision' else v4.Adopt).model_validate(body.command)
    except ValidationError:
        raise HTTPException(422, '新版完整原命令字段无法核对，原输入与请求保留') from None
    signature = (revisions.command_hash(p, identity, command, family=v4)
        if body.kind == 'revision' else base.command_hash(p, identity, command))
    return canonical_wire({'schema': 'report-full-semantic-graph-command-hash-v1',
        'kind': body.kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(identity), 'command': command.model_dump(mode='json'), 'command_hash': signature})


@router.get('/objects-v171/editor-context')
def object_editor_context(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(object_editor_v171.get_context(identity, p, db))


@router.get('/objects-v171/ability-options')
def object_ability_options(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return ability_options(identity, p, db)


@router.post('/objects-v171/preview')
def object_preview(identity: UUID, body: objects_v171.Preview,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(objects_v171.preview(identity, body, p, db))


@router.post('/objects-v171/adopt')
def object_adopt(identity: UUID, body: objects_v171.Adopt,
        p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return canonical_wire(objects_v171.adopt(identity, body, p, db))


@router.get('/objects-v171/requests/{request_id}')
def object_recover(identity: UUID, request_id: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(objects_v171.recover(identity, request_id, p, db))


@router.post('/objects-v171/revisions/preview')
def object_revision_preview(identity: UUID, body: objects_v171.RevisionPreview,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(objects_v171.revision_preview(identity, body, p, db))


@router.post('/objects-v171/revisions/adopt')
def object_revision_adopt(identity: UUID, body: objects_v171.RevisionAdopt,
        p: base.review.pipeline.P, db: base.review.pipeline.DB):
    return canonical_wire(objects_v171.revision_adopt(identity, body, p, db))


@router.get('/objects-v171/revisions/requests/{request_id}')
def object_revision_recover(identity: UUID, request_id: UUID,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return canonical_wire(objects_v171.recover_revision(identity, request_id, p, db))


@router.get('/objects-v171/configuration')
def object_configuration(identity: UUID, p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    return configuration(identity, p, db)


@router.post('/objects-v171/command-hash')
def object_command_hash(identity: UUID, body: CommandHash,
        p: base.review.pipeline.ReadP, db: base.review.pipeline.DB):
    _shared_handoff(db, p, identity)
    try:
        command = (objects_v171.RevisionAdopt if body.kind == 'revision'
            else objects_v171.Adopt).model_validate(body.command)
    except ValidationError:
        raise HTTPException(422, '新对象完整原命令字段无法核对，原输入与请求保留') from None
    signature = (revisions.command_hash(p, identity, command, family=objects_v171)
        if body.kind == 'revision' else base.command_hash(p, identity, command))
    return canonical_wire({'schema': 'report-full-semantic-graph-command-hash-v1',
        'kind': body.kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(identity), 'command': command.model_dump(mode='json'), 'command_hash': signature})

"""Guard new paid document-reading roots against overlapping unresolved history.

Only authenticated, workspace-scoped callers holding the setup draft lock may
invoke this guard. It returns no other actor's identities or report content.
A linked new task may exempt precisely its already verified original draft.
"""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Text, cast, func, select
from sqlalchemy.exc import DBAPIError

from .contracts import digest
from .models import CloudCall, SetupAnalysis, SetupMaterial, SetupReportDraft

_HASH = re.compile(r'^[0-9a-f]{64}$')
_MAX_HISTORY = 500
_MAX_HISTORY_BYTES = 32 * 1024 * 1024
_FULL = 'report-full-reading-draft-state-v1'
_EVIDENCE = 'report-evidence-draft-state-v1'
_BODY = 'report-reading-draft-state-v1'


def _deny(code='unresolved_report_source', message='同一资料存在尚未核清的报告读取记录；请先核对原记录，未新增模型调用。'):
    raise HTTPException(409, {'code': code, 'message': message})


def _ref(ref):
    if not isinstance(ref, dict) or type(ref.get('id')) is not str or type(ref.get('hash')) is not str:
        _deny('report_source_scope_unknown', '资料范围无法安全核对，未新增模型调用。')
    try:
        if str(UUID(ref['id'])) != ref['id'] or not _HASH.fullmatch(ref['hash']):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _deny('report_source_scope_unknown', '资料范围无法安全核对，未新增模型调用。')
    return ref['id'], ref['hash']


def _snapshot_refs(row):
    """Inspect stored frozen input, never disclose the other actor or values."""
    data = row.data
    if not isinstance(data, dict) or not isinstance(data.get('preview'), dict):
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    preview = data['preview']
    if not isinstance(preview.get('body'), dict) or not isinstance(preview.get('source_snapshot'), dict):
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    if preview.get('workspace_id') != row.workspace_id or preview.get('actor_id') != row.actor_id:
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    if (not isinstance(preview.get('preview_hash'), str)
            or preview['preview_hash'] != digest({k: v for k, v in preview.items() if k != 'preview_hash'})):
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    source = preview['source_snapshot']
    if preview.get('source_hash') != digest(source):
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    schema = data.get('schema')
    if schema in {_FULL, _EVIDENCE}:
        from .report_full_reading_source import check_full_source
        try:
            check_full_source(source)
        except (HTTPException, ValueError, TypeError, KeyError):
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        if source.get('workspace_id') != row.workspace_id:
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        selection = source.get('selection')
        if not isinstance(selection, dict) or selection != preview['body'].get('selection'):
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        refs = [selection.get('original_ref'), selection.get('copy_ref')]
    elif schema == _BODY:
        materials = source.get('materials')
        if not isinstance(materials, list) or not materials:
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        try:
            expected = [{key: material[key] for key in ('id', 'version', 'hash')} for material in materials]
        except (KeyError, TypeError):
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        if expected != preview['body'].get('material_refs'):
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        refs = materials
    else:
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    return {_ref(ref) for ref in refs}


def _closed_full_responses(db, row):
    """Allow a failed full draft only if every authorized stage has a paid wire.

    A pending stage, invalid response, absent ledger or unknown merge remains
    unresolved. This function only reads; it never settles or repairs a call.
    """
    from . import report_full_reading_pipeline as full
    try:
        preview = row.data['preview']
        stages = row.data['extractions']
        if not isinstance(stages, list) or not stages or [s['batch_id'] for s in stages] != [b['batch_id'] for b in preview['batches']]:
            return False
        keys = [stage['batch_id'] for stage in stages]
        merge = row.data['merge']
        if merge.get('preview') is not None:
            keys.append('merge')
        for key in keys:
            stage = full._stage(row, key)
            raw = stage.get('raw_response')
            if not isinstance(raw, dict) or not full.checked_hash(raw, 'hash') or not full._complete_response(raw):
                return False
            receipt = raw['receipt']; expected = full._expected(row, key)
            if stage.get('reserved_call_id') != receipt.get('call_id'):
                return False
            call = db.scalar(select(CloudCall).where(CloudCall.id == receipt['call_id'],
                CloudCall.workspace_id == row.workspace_id))
            if (not call or call.status not in {'succeeded', 'failed'} or call.capability_id != expected['capability_id']
                    or receipt.get('provider') != expected['provider'] or receipt.get('model') != expected['model']
                    or not isinstance(call.data, dict)
                    or any(call.data.get(k) != expected[k] for k in ('actor_id','request_hash','dispatch_options_hash'))
                    or call.data.get('input_policy_hash') != digest(preview['generation_contract']['input_policy'])
                    or call.reserved_tokens != full._dispatch(row, key)['input_summary']['token_bound']
                    or not isinstance(call.data.get('receipt'), dict)
                    or any(call.data['receipt'].get(k) != receipt.get(k) for k in
                        ('call_id','provider','model','usage','request_id','completion_guard',
                         'response_choice_count','finish_reason','response_body_status'))):
                return False
        return True
    except (KeyError, ValueError, TypeError, AttributeError, HTTPException):
        return False


def _closed(row, db):
    schema = row.data.get('schema')
    if schema == _FULL:
        return _closed_full_responses(db, row)
    # Legacy and segmented failed tasks have multiple journal/capture formats.
    # Until each has an independently verified all-stage closing proof, do not
    # infer from status, a partial JSON, or an empty ledger that it is settled.
    return False


def _analysis_refs(row):
    request = row.data.get('request') if isinstance(row.data, dict) else None
    if (not isinstance(request, dict) or not isinstance(request.get('materials'), list)
            or request.get('preview_hash') != digest(
                {k: v for k, v in request.items() if k != 'preview_hash'})):
        _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    if not request['materials']:
        # Versions 1/2 embed document blocks directly; newer versions use a
        # frozen source snapshot and its validated projection. A rehashed
        # contradictory snapshot must never masquerade as an empty source.
        version = request.get('contract_version', 1)
        if version in {1, 2}:
            if request.get('chunks') != [[]] or 'source_snapshot' in request:
                _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        elif version in {3, 4, 5, 6}:
            from .setup_understanding import checked_v3_request
            try:
                checked_v3_request(request)
            except (HTTPException, ValueError, TypeError, KeyError):
                _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
        else:
            _deny('report_history_scope_unknown', '历史报告的资料范围无法安全核对，未新增模型调用。')
    return {_ref(item) for item in request['materials']}


def _expand_refs(db, p, refs):
    from . import document_copy
    result = set(refs)
    for identity, sha in refs:
        material = db.scalar(select(SetupMaterial).where(SetupMaterial.id == identity,
            SetupMaterial.workspace_id == p.workspace_id))
        if material is None or material.sha256 != sha:
            _deny('report_source_scope_unknown', '资料范围已变化，未新增模型调用。')
        try:
            provenance = document_copy.checked_provenance(db, material)
        except (HTTPException, ValueError, TypeError, KeyError):
            _deny('report_source_scope_unknown', '资料来源关系无法安全核对，未新增模型调用。')
        if provenance is not None:
            result.add(_ref({'id': provenance.get('source_material_id'), 'hash': provenance.get('source_sha256')}))
    return result


def _check_history_budget(db, model, *predicates):
    # PostgreSQL computes the envelope without transferring JSON documents to
    # the application. Fail closed before loading any oversized history.
    count, byte_count = db.execute(select(func.count(model.id),
        func.coalesce(func.sum(func.octet_length(cast(model.data, Text))), 0))
        .where(*predicates)).one()
    if count > _MAX_HISTORY or byte_count > _MAX_HISTORY_BYTES:
        _deny('report_history_capacity', '历史报告记录过多或过大，暂无法安全核对；未新增模型调用。')


def _history_rows(db, model, *predicates):
    last = None
    while True:
        query = select(model).where(*predicates).order_by(model.id).limit(4)
        if last is not None:
            query = query.where(model.id > last)
        page = db.scalars(query).all()
        if not page:
            return
        last = page[-1].id
        yield from page


def guard_new_paid_sources(db, p, material_refs, *, linked_original_id=None, current_draft_id=None):
    """Reject same-document paid work while another actor's history is unresolved.

    Call after nonce replay checks and while holding the workspace SetupDraft
    lock. ``material_refs`` must come from the server's freshly frozen preview.
    The two optional exclusions are reserved for the linked-start and
    per-dispatch verifiers after they verify that exact origin; they must
    never be accepted directly from a generic request.
    """
    from . import report_reading_drafts as legacy
    if not isinstance(material_refs, (list, tuple)):
        _deny('report_source_scope_unknown', '资料范围无法安全核对，未新增模型调用。')
    if not material_refs:
        return
    legacy.workspace_draft(db, p)
    incoming = _expand_refs(db, p, {_ref(item) for item in material_refs})
    if not incoming:
        return
    protected_states = {'queued', 'running', 'response_unknown', 'awaiting_merge', 'failed'}
    # The workspace lock serializes new report starts. Take no row lock on an
    # unrelated actor's document; lock and re-read only a matching record.
    _check_history_budget(db, SetupReportDraft, SetupReportDraft.workspace_id == p.workspace_id,
        SetupReportDraft.status.in_(protected_states))
    _check_history_budget(db, SetupAnalysis, SetupAnalysis.workspace_id == p.workspace_id,
        SetupAnalysis.status == 'failed')
    rows = _history_rows(db, SetupReportDraft, SetupReportDraft.workspace_id == p.workspace_id,
        SetupReportDraft.status.in_(protected_states))
    in_ids = {item[0] for item in incoming}; in_hashes = {item[1] for item in incoming}
    for row in rows:
        old = _expand_refs(db, p, _snapshot_refs(row))
        overlaps = bool(in_ids & {x[0] for x in old} or in_hashes & {x[1] for x in old})
        if (not overlaps or (linked_original_id is not None and row.id == str(linked_original_id))
                or (current_draft_id is not None and row.id == str(current_draft_id))):
            continue
        try:
            row = db.scalar(select(SetupReportDraft).where(SetupReportDraft.id == row.id,
                SetupReportDraft.workspace_id == p.workspace_id)
                .with_for_update(nowait=True).execution_options(populate_existing=True))
        except DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) != '55P03':
                raise
            db.rollback()
            _deny('report_history_busy', '同一资料的原记录正在处理或核对，本次未新增模型调用。')
        if row is None or row.status not in protected_states:
            continue
        # Re-read after the row lock; a concurrent transition must not make a
        # previously observed scope authorize an unrelated state change.
        old = _expand_refs(db, p, _snapshot_refs(row))
        if not (in_ids & {x[0] for x in old} or in_hashes & {x[1] for x in old}):
            _deny('report_history_scope_unknown', '历史报告范围发生变化，未新增模型调用。')
        if row.status != 'failed' or not _closed(row, db):
            _deny()
    # Legacy understanding jobs also retain an uncertain paid send in
    # `inflight`. Their established writer locks analysis before workspace;
    # NOWAIT is essential here because this guard already holds workspace.
    analyses = _history_rows(db, SetupAnalysis, SetupAnalysis.workspace_id == p.workspace_id,
        SetupAnalysis.status == 'failed')
    for analysis in analyses:
        if not legacy.legacy_pending(analysis, db):
            continue
        old = _expand_refs(db, p, _analysis_refs(analysis))
        if not (in_ids & {x[0] for x in old} or in_hashes & {x[1] for x in old}):
            continue
        try:
            analysis = db.scalar(select(SetupAnalysis).where(SetupAnalysis.id == analysis.id,
                SetupAnalysis.workspace_id == p.workspace_id)
                .with_for_update(nowait=True).execution_options(populate_existing=True))
        except DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) != '55P03':
                raise
            db.rollback()
            _deny('report_history_busy', '同一资料的原记录正在处理或核对，本次未新增模型调用。')
        if analysis is None or analysis.status != 'failed' or not legacy.legacy_pending(analysis, db):
            continue
        old = _expand_refs(db, p, _analysis_refs(analysis))
        if not (in_ids & {x[0] for x in old} or in_hashes & {x[1] for x in old}):
            _deny('report_history_scope_unknown', '历史报告范围发生变化，未新增模型调用。')
        _deny()

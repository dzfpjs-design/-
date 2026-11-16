"""Bounded, workspace-scoped source choices; no execution or receipt authority.

Both lists scan at most ``limit + 1`` raw eligible-kind rows, ordered by id.
Capability rows that fail cold validation are omitted. An empty items page can
therefore have next_after: it is the last scanned id, not the last returned id.
Clients must retain the explicit read-more action rather than infer exhaustion.
Historical registrations are cold-checked separately from current availability.
"""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from .contracts import digest
from .db import session
from .graph_event_models import GraphEventProducer
from .models import Capability
from .security import Principal, authenticated


router = APIRouter(prefix='/graph-event-producers')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
EXTRACTOR_KINDS = ('tool', 'specialist_model', 'foundation_model')
DESCRIPTION_LIMIT = 500
_COLD_ERRORS = (ValueError, KeyError, TypeError, AttributeError, RecursionError,
                UnicodeError, OverflowError)
_LABELS = {
    'available': '当前发布版本可核对；尚不证明实际连接、处理管线或现场效果。',
    'registration_stopped': '此消息来源已停止，原登记与历史保留。',
    'no_execution_permission': '当前账号不能选择执行来源；原登记与历史仍可查看。',
    'source_unavailable': '来源或处理能力的发布版本、内容或权限已变化，请核对原登记。',
}


def _text(value, maximum):
    if type(value) is not str or not value.strip() or len(value) > maximum or '\x00' in value:
        return None
    try:
        value.encode('utf-8')
    except UnicodeError:
        return None
    return value


def _cursor(value):
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '分页位置无法核对，请重新读取目录') from None


def _limit(value):
    if type(value) is not int or not 1 <= value <= 50:
        raise HTTPException(422, '每页读取数量须为1至50')
    return value


def capability_item(row, kind):
    """Pure whitelist projection of a cold-valid published capability, or None."""
    from .graph_event_observation_sources import _cap, _schema
    try:
        if kind not in {'source', 'extractor'}:
            return None
        _cap(row, kind='source' if kind == 'source' else None)
        if kind == 'source' and getattr(row, 'namespace', None) == 'platform_native_receipts':
            return None
        if kind == 'extractor':
            roles = row.data.get('allowed_agent_roles')
            if row.kind not in EXTRACTOR_KINDS or type(roles) is not list or 'perception' not in roles:
                return None
            _schema(row.data.get('output_schema'))
        identity = str(UUID(row.id))
        if identity != row.id or not _text(row.name, 100) or not _text(row.release_version, 40):
            return None
        item = {'id': identity, 'name': row.name, 'kind': row.kind,
                'content_hash': row.content_hash, 'release_version': row.release_version}
        description = row.data.get('description')
        if type(description) is str:
            description = _text(description[:DESCRIPTION_LIMIT], DESCRIPTION_LIMIT)
            if description is not None:
                item['description'] = description
        return item
    except _COLD_ERRORS:
        return None


def _page(db, model, p, after, limit, *conditions):
    limit = _limit(limit)
    query = select(model).where(model.workspace_id == p.workspace_id, *conditions)
    cursor = _cursor(after)
    if cursor is not None:
        query = query.where(model.id > cursor)
    rows = list(db.scalars(query.order_by(model.id).limit(limit + 1)
        .execution_options(populate_existing=True)))
    scanned = rows[:limit]
    next_after = scanned[-1].id if len(rows) > limit else None
    return scanned, next_after


def list_capabilities(db, p, kind, *, after=None, limit=20):
    if kind not in {'source', 'extractor'}:
        raise HTTPException(422, '请选择来源或处理能力目录')
    kinds = ('source',) if kind == 'source' else EXTRACTOR_KINDS
    rows, next_after = _page(db, Capability, p, after, limit,
        Capability.kind.in_(kinds), Capability.status == 'published',
        Capability.validated_hash == Capability.content_hash)
    items = [item for row in rows if (item := capability_item(row, kind)) is not None]
    return {'items': items, 'next_after': next_after, 'can_register': p.role == 'admin'}


def _availability(code):
    return {'available': code == 'available', 'reason_code': code, 'label': _LABELS[code]}


def current_availability(db, p, row, contract):
    """A current selection hint, never permission to execute a wait or a model."""
    if row.status != 'active':
        return _availability('registration_stopped')
    if p.role not in {'admin', 'operator'}:
        return _availability('no_execution_permission')
    from .graph_event_source_families_v136 import verify_contract_current
    try:
        actual = verify_contract_current(db, p, contract, producer=row)
        if digest(actual) != row.contract_hash:
            return _availability('source_unavailable')
    except HTTPException as error:
        if error.status_code == 403:
            return _availability('no_execution_permission')
        if error.status_code not in {404, 409, 422}:
            raise
        return _availability('source_unavailable')
    except _COLD_ERRORS:
        return _availability('source_unavailable')
    # Database/driver errors are deliberately not caught or mislabeled as an
    # unavailable source. The request must fail if its actual read failed.
    return _availability('available')


def registration_item(db, p, row):
    # Import only when called: graph_event_api imports setup_api, whose router
    # includes this independent catalog after dependency aliases are defined.
    from .graph_event_api import public
    if row.workspace_id != p.workspace_id:
        raise HTTPException(404, '未找到当前工作区的消息来源')
    value = public(row)  # Corrupt history is not repaired or treated as current.
    contract = value['contract']
    from .graph_event_source_families_v136 import record_family, PROCESSING
    if record_family(row) != PROCESSING:
        raise HTTPException(409, '此来源属于独立原生结果目录，请从原入口核对')
    names = dict(db.execute(select(Capability.id, Capability.name).where(
        Capability.workspace_id == p.workspace_id,
        Capability.id.in_((contract['source_id'], contract['extractor_id'])))).all())
    return {**value, 'source_name': _text(names.get(contract['source_id']), 100),
        'extractor_name': _text(names.get(contract['extractor_id']), 100),
        'current_availability': current_availability(db, p, row, contract),
        'receipt_verified': False}


def list_registrations(db, p, *, after=None, limit=20):
    rows, next_after = _page(db, GraphEventProducer, p, after, limit)
    from .graph_event_admission import check_producer
    from .graph_event_source_families_v136 import record_family, PROCESSING
    for row in rows: check_producer(row)
    return {'items': [registration_item(db, p, row) for row in rows if record_family(row) == PROCESSING],
        'next_after': next_after, 'can_register': p.role == 'admin'}


@router.get('/catalog/capabilities')
def capabilities(kind: Literal['source', 'extractor'], p: P, db: DB,
                 after: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    return list_capabilities(db, p, kind, after=after, limit=limit)


@router.get('/catalog/registrations')
def registrations(p: P, db: DB, after: UUID | None = None,
                  limit: int = Query(20, ge=1, le=50)):
    return list_registrations(db, p, after=after, limit=limit)


@router.get('/catalog/registrations/{identity}')
def registration(identity: UUID, p: P, db: DB):
    row = db.scalar(select(GraphEventProducer).where(GraphEventProducer.id == str(identity),
        GraphEventProducer.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '未找到当前工作区的消息来源')
    return registration_item(db, p, row)

"""Read-only v4 source choices beside the existing cold editor evidence.

Availability is a current selection hint, never adoption or execution authority.
The actual v4 preview/adoption transaction rechecks every selected source pin.
Historical registrations and graph adoptions remain visible after revocation.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import report_full_semantic_graph_editor as old
from . import graph_wait_sources_v118 as sources
from . import graph_event_observation_sources as observation_sources
from . import graph_event_source_families_v136 as source_families
from .graph_event_admission import check_producer
from .graph_event_models import GraphEventProducer
from .models import Capability


SCHEMA = 'report-full-semantic-graph-editor-context-v2'
MAX_SOURCES = 200
NO_SOURCES = '尚无当前可用的已登记来源；请先登记或恢复来源及提取能力，再选择等待。'
INACTIVE = '此完整设计已被后续版本替换，来源和采用历史可查，不能据此新增采用。'
NO_PERMISSION = '当前账号无权选择执行来源；原登记、设计和采用历史保留。'
UNAVAILABLE = '来源或提取能力的发布版本、权限或内容已变化；原登记和历史保留，请核对后再选择。'
REVOKED = '此来源登记已撤销，历史可查，不能用于新的等待执行。'


def _available(db, p, contract, producer=None):
    """Use the same actual capability predicates as v4 verify_bindings, read-only."""
    source_families.verify_contract_current(db, p, contract, producer=producer)


def _source_choices(db, p):
    rows = list(db.scalars(select(GraphEventProducer).where(
        GraphEventProducer.workspace_id == p.workspace_id)
        .order_by(GraphEventProducer.created_at, GraphEventProducer.id)
        .limit(MAX_SOURCES + 1).execution_options(populate_existing=True)))
    if len(rows) > MAX_SOURCES:
        old.base.fail('来源登记超过一次完整读取上限；服务器记录保留', 413)
    actor_available = True
    try:
        sources._actor(db, p)
    except HTTPException as error:
        if error.status_code != 403:
            raise
        actor_available = False
    result = []
    for row in rows:
        # A corrupt cold record is not a safe historical contract to display.
        # Normal current source failures below affect only this source choice.
        old._need(row.workspace_id == p.workspace_id)
        contract = check_producer(row)
        available = False
        reason = NO_PERMISSION if not actor_available else REVOKED if row.status != 'active' else None
        if reason is None:
            try:
                _available(db, p, contract, row)
                available = True
            except (HTTPException, ValueError, KeyError, TypeError, AttributeError,
                    RecursionError, OverflowError, UnicodeError):
                reason = UNAVAILABLE
        result.append({'producer_id': row.id, 'label': row.data['label'], 'status': row.status,
            'contract_hash': row.contract_hash, 'contract': copy.deepcopy(contract),
            'available': available, 'reason': reason})
    return result, actor_available


def get_context(identity: UUID, p: old.review.pipeline.ReadP, db: old.review.pipeline.DB):
    # The original editor already dispatches base.checked across actual v3/v4
    # root/revision ledgers, including mixed parent chains. Do not reseal them.
    context = copy.deepcopy(old.get_context(identity, p, db))
    choices, actor_available = _source_choices(db, p)
    available = context['active'] and actor_available and any(row['available'] for row in choices)
    reason = None if available else INACTIVE if not context['active'] else NO_PERMISSION if not actor_available else NO_SOURCES
    context['schema'] = SCHEMA
    context['event_wait'] = {'available': available, 'reason': reason,
        'policy': copy.deepcopy(sources.POLICY), 'sources': choices}
    return old.review.bounded(context)

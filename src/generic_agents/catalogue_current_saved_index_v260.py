"""A page of the owner's saved Current-record pointers, not live inventory.

Only the returned page's command/state anchors are checked. The JSON filters
do not discover every possible deleted or altered record outside that page.
An empty page is not proof of no activity, no paid call, or new-call authority.
Answers, files, sources and current configuration need their separate readers.
"""
import copy

from fastapi import HTTPException, Query
from sqlalchemy import select

from . import catalogue_current_saved_record_v260 as saved
from .catalogue_current_saved_record_v260 import ReadDB, ReadP

SCHEMA = 'catalogue-current-saved-index-v260'
ERROR = '本页原请求或保存锚点无法核对；未略过坏记录、修写或发起新调用'
STATUSES = {'queued', 'running', 'response_unknown', 'review', 'needs_clarification', 'failed'}
VERIFICATION = {'command_state_anchors_checked': True,
    'complete_operational_inventory_checked': False, 'source_revalidated': False,
    'files_revalidated': False, 'current_configuration_revalidated': False,
    'can_request_new_call': False, 'can_apply': False, 'can_publish': False}


def _need(value):
    if not value:
        raise HTTPException(409, ERROR)


def saved_index(db, p, producer_id, binding_id, *, offset=0, limit=20):
    from . import report_saved_map_design_consumer_v176 as c
    from . import catalogue_current_allocation_v260 as current
    c.route_identity(producer_id); c.route_identity(binding_id); c._current_reader(db, p)
    if (type(offset) is not int or not 0 <= offset <= 9_223_372_036_854_775_807
            or type(limit) is not int or not 1 <= limit <= 20):
        raise HTTPException(422, '请使用有效的非负分页位置及1至20条的页长')
    try:
        # Select only scalar IDs, never another actor's payload or an entire
        # actor inventory. The extra ID is solely a same-snapshot page sentinel.
        ids = list(db.scalars(select(c.Consumer.id).where(
            c.Consumer.workspace_id == p.workspace_id, c.Consumer.actor_id == p.user_id,
            c.Consumer.producer_id == producer_id,
            c.Consumer.data['preview']['generation_profile'].as_string() == current.PROFILE,
            c.Consumer.data['preview']['admission_basis']['binding_id'].as_string() == binding_id)
            .order_by(c.Consumer.created_at.desc(), c.Consumer.id.desc())
            .offset(offset).limit(limit + 1)))
        records = []
        for consumer_id in ids[:limit]:
            row, commands, audits, jobs, _calls = saved._selected(db, p, consumer_id, c)
            _need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
                and row.producer_id == producer_id and row.status in STATUSES
                and type(row.data) is dict and row.data.get('schema') == c.STATE)
            start, by_id, checked = saved._commands(row, commands, audits, c, current)
            state_hash = saved._states(row, audits, by_id, checked, c)
            _prep, _spec, _scope = saved._metadata(row, start, p, c, current)
            _need(row.data['preview']['admission_basis']['binding_id'] == binding_id
                and len(jobs) == 1)
            job = jobs[0]
            _need(job.id == row.job_id and job.workspace_id == p.workspace_id
                and job.kind == c.JOB and job.idempotency_key == 'saved-map-design:' + row.request_id
                and type(job.data) is dict and job.data.get('actor_id') == p.user_id
                and job.data.get('consumer_id') == row.id
                and job.data.get('generation_profile') == current.PROFILE)
            records.append({'consumer_id': row.id, 'version': row.version, 'status': row.status,
                'note': row.data['preview']['body']['note'], 'request_sha256': start[0].body_sha256,
                'start_receipt': copy.deepcopy(start[0].data['result']), 'state_hash': state_hash})
        return {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
            'producer_id': producer_id, 'binding_id': binding_id, 'offset': offset, 'limit': limit,
            'has_more': len(ids) > limit, 'records': records, 'verification': dict(VERIFICATION)}
    except HTTPException as error:
        if error.status_code in {401, 403, 404}:
            raise
        raise HTTPException(409, ERROR) from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError,
            RecursionError, OverflowError):
        raise HTTPException(409, ERROR) from None


def register_routes(router):
    @router.get('/saved-records/by-producer/{producer_id}')
    def saved_index_route(producer_id: str, p: ReadP, db: ReadDB, binding_id: str,
            offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=20)):
        return saved_index(db, p, producer_id, binding_id, offset=offset, limit=limit)

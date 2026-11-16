"""Two server-selected source providers, one readonly database snapshot.

No API, worker, planner call, query creation, source Audit, ACK or commit is
performed. The caller creates a separate RR READ ONLY Session before calling
capture, and later persists the returned payload with its independent source
Audit. Saved verification requires that real owner-private DataRun/Audit pair.
Database as_of is not a recording, acquisition or model-event timestamp.
"""
import copy
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select, text

from . import models as m
from .contracts import digest
from .media_daily_task_adapter import current_actor
from .services import scoped
from . import multimodal_query_contracts_v262 as c
from . import pixel_question_sources_v252 as pixel
from . import task_sound_query_evidence as sound
from . import task_sound_query_frozen_v262 as frozen_sound

SOURCE_ACTION = 'multimodal_query.source_frozen_v262'
PROOF = 'multimodal-query-source-proof-v262'
ERROR = '本次多模态查询来源、登记范围或独立冻结依据无法核对；原记录保留'


def need(ok, message=ERROR, code=409):
    if not ok:
        raise HTTPException(code, message)


def _context(db, p, scope, *, live):
    current_actor(db, p)
    instance = scoped(db, m.DomainInstance, scope['instance_id'], p)
    release = scoped(db, m.InstanceRelease, scope['authorization_release_id'], p)
    need(release.instance_id == instance.id and digest(release.data['snapshot']) == release.configuration_hash)
    package = scoped(db, m.Capability, release.package_id, p)
    need(package.kind == 'domain_package' and package.content_hash == package.validated_hash == digest(package.data)
        and package.data['specification']['configuration_hash'] == release.configuration_hash)
    if live:
        need(p.role in {'admin', 'operator'}, '当前角色不能新增查询来源捕获', 403)
        need(instance.status == 'partially_active' and instance.active_release_id == release.id
            and package.status == 'published', '当前业务版本尚未启用或已变化；历史冻结来源未改写')
    if scope['task_id'] is not None:
        task = scoped(db, m.Task, scope['task_id'], p)
        need(task.data.get('instance_id') == instance.id)
        if scope['entity_id'] is not None: need(scope['entity_id'] in task.data.get('entity_ids', []))
    if scope['entity_id'] is not None:
        entity = scoped(db, m.Entity, scope['entity_id'], p)
        need(entity.namespace == instance.namespace)
    from .instance_data_receipts import verify_release
    verify_release(db, p, release)
    return instance, release


def _plans(snapshot):
    from .native_pixel_data_plan_v250 import verify as verify_pixel
    from .native_sound_data_plan import verify as verify_sound
    return {'pixel': verify_pixel(snapshot), 'task_sound': verify_sound(snapshot)}


def _descriptor(db, p, name, plan, *, enabled=None, live):
    bindings = []
    for binding in plan['bindings']:
        if enabled is not None and binding['business_id'] not in enabled: continue
        cap = scoped(db, m.Capability, binding['capability_id'], p)
        need(cap.content_hash == cap.validated_hash == digest(cap.data) == binding['capability_hash']
            and cap.release_version == binding['release_version']
            and digest(cap.data['output_schema']) == binding['output_schema_hash'])
        if live: need(cap.status == 'published', '当前登记来源能力未发布，未以空来源替代')
        bindings.append({key: copy.deepcopy(binding[key]) for key in c.PIN_KEYS})
    if not bindings: return None
    bindings.sort(key=lambda item: (item['business_id'], item['step_id'], item['node_id'] or ''))
    return {'facet_id': name, 'source_family': c.FACETS[name][0], 'data_plan_key': c.FACETS[name][1],
        'data_plan_hash': digest(plan), 'bindings': bindings}


def registry(db, p, instance_id, authorization_release_id, *, task_id=None, entity_id=None):
    """Actual current release/capability pins; no caller-provided provider map."""
    scope = c.Scope(instance_id=instance_id, authorization_release_id=authorization_release_id,
        task_id=task_id, entity_id=entity_id).model_dump(mode='json')
    instance, release = _context(db, p, scope, live=True)
    enabled = set(instance.data.get('enabled_business_ids', [])); facets = {}
    for name, plan in _plans(release.data['snapshot']).items():
        if plan is not None:
            value = _descriptor(db, p, name, plan, enabled=enabled, live=True)
            if value is not None: facets[name] = value
    need(bool(facets), '本业务版本没有已启用的像素或Task声音查询来源')
    body = {'schema': c.REGISTRY, 'workspace_id': p.workspace_id, 'instance_id': instance.id,
        'authorization_release_id': release.id, 'configuration_hash': release.configuration_hash, 'facets': facets}
    return c.checked_registry({**body, 'registry_hash': digest(body)})


def _historical_registry(db, p, scope, value):
    c.checked_registry(value)
    _, release = _context(db, p, scope, live=False)
    need(value['configuration_hash'] == release.configuration_hash)
    plans = _plans(release.data['snapshot'])
    for name, old in value['facets'].items():
        plan = plans[name]
        need(plan is not None and old['data_plan_hash'] == digest(plan))
        original = _descriptor(db, p, name, plan, live=False)
        need(original is not None)
        original_pins = {digest(item) for item in original['bindings']}
        need(all(digest(item) in original_pins for item in old['bindings']))
    # Enabled business set / current active release are deliberately not used:
    # the independent frozen source Audit attests the then-selected subset.


def capture(db, p, instance_id, authorization_release_id, *, task_id=None, entity_id=None):
    """Caller supplies ONE fresh independent RR READ ONLY transaction."""
    try:
        need(not db.new and not db.dirty and not db.deleted)
        need(db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            and db.scalar(text('SHOW transaction_read_only')) == 'on', '来源捕获需要独立一致只读事务')
        stamp = db.scalar(text('SELECT transaction_timestamp()'))
        scope = c.Scope(instance_id=instance_id, authorization_release_id=authorization_release_id,
            task_id=task_id, entity_id=entity_id).model_dump(mode='json')
        pins = registry(db, p, **scope); facets = {}; scanned = 0
        if 'pixel' in pins['facets']:
            facets['pixel'] = pixel.capture(db, p, instance_id, task_id=task_id, entity_id=entity_id, limit=c.MAX_RECORDS)
            scanned += len(facets['pixel']['inventory']['run_ids'])
        if 'task_sound' in pins['facets']:
            remaining = max(1, c.MAX_RECORDS - scanned)
            facets['task_sound'] = sound.capture(db, p, instance_id, as_of=stamp,
                task_id=task_id, entity_id=entity_id, limit=remaining)
            scanned += len(facets['task_sound']['inventory']['scanned_run_ids'])
        need(scanned <= c.MAX_RECORDS, '两个来源合计超过1000项完整库存，未返回截断总体')
        body = {'schema': c.SNAPSHOT, 'workspace_id': p.workspace_id, 'captured_by': p.user_id,
            'scope': scope, 'as_of': stamp.astimezone(timezone.utc).isoformat(), 'registry': pins,
            'facets': facets, 'record_count': sum(len(item['records']) for item in facets.values()),
            'expected_sources_verified': False}
        value = c.checked_snapshot({**body, 'snapshot_hash': digest(body)})
        for name, native in value['facets'].items():
            if name == 'pixel': pixel.validate_snapshot(native)
            else: frozen_sound.validate_snapshot(native)
        need(not db.new and not db.dirty and not db.deleted)
        return value
    except HTTPException:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        raise HTTPException(409, ERROR) from None


def source_audit_expected(query_run, snapshot):
    """Only proof metadata. Caller persists it independently; no write here."""
    c.checked_snapshot(snapshot)
    return {'schema': PROOF, 'query_id': query_run.id, 'workspace_id': query_run.workspace_id,
        'actor_id': query_run.owner_id, 'scope': copy.deepcopy(snapshot['scope']), 'as_of': snapshot['as_of'],
        'registry_hash': snapshot['registry']['registry_hash'], 'source_snapshot_hash': snapshot['snapshot_hash'],
        'source_hashes': {name: value['snapshot_hash'] for name, value in snapshot['facets'].items()},
        'record_count': snapshot['record_count']}


def verify_saved_snapshot(db, p, query_run, snapshot):
    """Real owner-private source storage proof, never a new/current read grant."""
    try:
        need(not db.new and not db.dirty and not db.deleted, '来源历史核验不能包含待保存修改')
        current_actor(db, p)
        c.checked_snapshot(snapshot)
        need(isinstance(query_run, m.DataRun) and query_run.workspace_id == p.workspace_id
            and query_run.owner_id == p.user_id and snapshot['workspace_id'] == query_run.workspace_id
            and snapshot['captured_by'] == query_run.owner_id)
        with db.no_autoflush:
            row = scoped(db, m.DataRun, query_run.id, p)
            need(row.owner_id == query_run.owner_id)
            state = row.data[c.FAMILY]
            need(digest(state['source_snapshot']) == digest(snapshot))
            audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.object_id == row.id, m.Audit.action == SOURCE_ACTION).limit(2)))
            need(len(audits) == 1 and audits[0].id == state['source_audit_id']
                and audits[0].actor_id == row.owner_id
                and digest(audits[0].data) == digest(source_audit_expected(row, snapshot)))
            _historical_registry(db, p, snapshot['scope'], snapshot['registry'])
            for name, native in snapshot['facets'].items():
                if name == 'pixel': pixel.verify_frozen_records(db, p, native)
                else: frozen_sound.verify_frozen_records(db, p, native)
    except HTTPException as error:
        if error.status_code in {401, 403}: raise
        raise HTTPException(409, ERROR) from None
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        raise HTTPException(409, ERROR) from None

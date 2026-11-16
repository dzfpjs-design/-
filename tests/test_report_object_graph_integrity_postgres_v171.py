"""Owned-PG graph inventory negatives; all destructive SQL is rolled back.

The source, confirmation, position proof, schema and both graph decisions are
created by their ordinary authenticated APIs. Session substitution only exposes
one independent connection's uncommitted negative SQL to the real endpoints.
No validator, authority helper, ledger reader or successful response is replaced.
These synthetic-source tests do not establish real-report semantic quality.
Request recovery must reject surviving independent evidence with 409. The
compiler retains its existing 404 for an absent adoption record (including the
exact missing-record detail), while a present record with a missing audit is 409.
"""
import copy
from contextlib import contextmanager
from uuid import uuid4

from fastapi import Request, Response
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine, session
from generic_agents.models import Audit, CloudCall, DomainInstance, Entity, Event, InstanceRelease, Invocation, Job, Memory, SetupDraft, SetupRevision, Task
from generic_agents.report_full_semantic_graph_models import FullSemanticGraphAdoption
from generic_agents.report_full_semantic_graph_revision_models import FullSemanticGraphRevision
from generic_agents import report_full_reading_pipeline as reading_pipeline
from tests.test_report_object_graph_postgres_v171 import (
    ROOT, command, prepare, store_inventory, reply, fence,
    local_runtime, reading_scene, v7_scene, full_source_scene, confirmed_scene,
    object_source_scene, object_schema_scene,
)


_ABSENT = object()
_MODELS = (Audit, CloudCall, DomainInstance, Entity, Event, InstanceRelease,
    Invocation, Job, Memory, SetupDraft, SetupRevision, Task,
    FullSemanticGraphAdoption, FullSemanticGraphRevision)
_ACTIONS = {False: 'setup.full_semantic_graph.adopted',
    True: 'setup.full_semantic_graph.revised'}


def _visible_inventory(connection):
    """Compare relevant actual rows in the same fault transaction, not a cache."""
    with Session(bind=connection, expire_on_commit=False,
            join_transaction_mode='create_savepoint') as db:
        return {model.__tablename__: {row.id: digest({
            'version': getattr(row, 'version', None), 'data': row.data,
            'status': getattr(row, 'status', None),
            'actor_id': getattr(row, 'actor_id', None),
            'object_id': getattr(row, 'object_id', None),
            'request_id': getattr(row, 'request_id', None),
        }) for row in db.scalars(select(model))} for model in _MODELS}


@contextmanager
def _rolled_back_fault(accepted, fault, *, revision=False):
    """Remove only real committed records, never commit the negative mutation."""
    fence()
    previous = {dependency: app.dependency_overrides.get(dependency, _ABSENT)
        for dependency in (session, reading_pipeline.read_principal)}
    model = FullSemanticGraphRevision if revision else FullSemanticGraphAdoption
    identity = accepted['receipt']['id']; action = _ACTIONS[revision]
    # Isolation is fixed before BEGIN or the first fault lookup. The production
    # read_principal cannot SET isolation after these deliberate negative reads
    # and writes, even when the request Session uses its own SAVEPOINT.
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, expire_on_commit=False,
                    join_transaction_mode='create_savepoint') as db:
                row = db.get(model, identity)
                assert row is not None
                assert row.data['receipt'] == accepted['receipt']
                configuration_id = row.configuration_id
                workspace_id = row.workspace_id
                business_name = row.business_name
                audits = list(db.scalars(select(Audit).where(
                    Audit.workspace_id == workspace_id, Audit.action == action,
                    Audit.object_id == identity)))
                assert len(audits) == 1
                audit_id = audits[0].id
                assert audits[0].actor_id == row.actor_id
                assert audits[0].data['request_id'] == row.request_id
                assert audits[0].data['record_hash'] == row.data['record']['record_hash']
                after = db.scalar(select(SetupRevision.data).where(
                    SetupRevision.draft_id == configuration_id,
                    SetupRevision.workspace_id == workspace_id,
                    SetupRevision.version == accepted['receipt']['accepted_revision']))
                assert after['fullSemanticGraphBindings'][business_name] == accepted['origin']
                assert digest(after) == accepted['receipt']['configuration_hash']
            if fault in {'record_missing', 'record_and_audit_missing'}:
                result = connection.execute(delete(model).where(model.id == identity))
                assert result.rowcount == 1
            if fault in {'audit_missing', 'record_and_audit_missing'}:
                result = connection.execute(delete(Audit).where(Audit.id == audit_id))
                assert result.rowcount == 1
            assert fault in {'record_missing', 'audit_missing', 'record_and_audit_missing'}
            # The real accepted configuration and independent history survive
            # even when both the command row and its audit have been removed.
            with Session(bind=connection, expire_on_commit=False,
                    join_transaction_mode='create_savepoint') as db:
                configured = db.get(SetupDraft, configuration_id)
                assert configured.data['fullSemanticGraphBindings'][business_name] == accepted['origin']
                assert db.scalar(select(SetupRevision.id).where(
                    SetupRevision.draft_id == configuration_id,
                    SetupRevision.version == accepted['receipt']['accepted_revision'])) is not None
                assert (db.get(Audit, audit_id) is not None) is (fault == 'record_missing')
                assert (db.get(model, identity) is not None) is (fault == 'audit_missing')

            def bound_session():
                with Session(bind=connection, expire_on_commit=False,
                        join_transaction_mode='create_savepoint') as db:
                    yield db

            def bound_read_principal(request: Request, response: Response, db: reading_pipeline.DB):
                # Preserve the real PostgreSQL read-only gate and authentication;
                # only the redundant late SET isolation is moved to connection
                # creation above. Closing this request Session rolls back its
                # SAVEPOINT, including its local READ ONLY setting. Fault SQL
                # remains exclusively in the never-committed outer transaction.
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
                assert db.scalar(text('SHOW transaction_read_only')) == 'on'
                return reading_pipeline.legacy.private_principal(request, response, db)

            app.dependency_overrides[session] = bound_session
            app.dependency_overrides[reading_pipeline.read_principal] = bound_read_principal
            yield connection
        finally:
            for dependency, prior in previous.items():
                if prior is _ABSENT:
                    app.dependency_overrides.pop(dependency, None)
                else:
                    app.dependency_overrides[dependency] = prior
            transaction.rollback()


def _accept_root(seed):
    current, member, _ = prepare(seed)
    client = seed['client']; path = ROOT + seed['record']['id']
    preview_body = command(seed, current, member)
    empty = reply(client.get(path + '/requests/' + str(uuid4())))
    assert empty == {'schema': 'report-object-graph-recovery-v171', 'found': False}
    preview = reply(client.post(path + '/preview', json=preview_body))
    body = {**preview_body, 'schema': 'report-object-graph-command-v171',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'],
        'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/adopt', json=body))
    assert reply(client.get(path + '/requests/' + body['request_id']))['receipt'] == accepted['receipt']
    return path, preview_body, body, accepted


def _compile_body(client, roles):
    saved = reply(client.get('/api/setup/draft'))
    return {'revision': saved['revision'], 'objects': saved['configuration']['instanceObjects'],
        'role_bindings': copy.deepcopy(roles)}


def _assert_faults(client, path, body, accepted, compilation, *, revision=False):
    recover = path + ('/revisions/requests/' if revision else '/requests/') + body['request_id']
    original_config = reply(client.get('/api/setup/draft'))
    original_receipt = reply(client.get(recover))
    original_rows = store_inventory()
    for fault in ('record_missing', 'audit_missing', 'record_and_audit_missing'):
        with _rolled_back_fault(accepted, fault, revision=revision) as connection:
            before = _visible_inventory(connection)
            response = client.get(recover)
            assert response.status_code == 409, (fault, response.status_code, response.text)
            # This is the actual compiler -> validated bindings -> verify_origin
            # path, not a direct invocation of a substituted integrity predicate.
            response = client.post('/api/instances/check', json=compilation)
            if fault == 'audit_missing':
                assert response.status_code == 409, (fault, response.status_code, response.text)
            else:
                assert response.status_code == 404, (fault, response.status_code, response.text)
                assert response.json() == {'detail': '采用记录不存在'}
            assert _visible_inventory(connection) == before
        assert store_inventory() == original_rows
        assert reply(client.get('/api/setup/draft')) == original_config
        assert reply(client.get(recover)) == original_receipt
    assert reply(client.post(path + ('/revisions/adopt' if revision else '/adopt'), json=body))['receipt'] == accepted['receipt']
    assert store_inventory() == original_rows


def test_new_graph_root_missing_record_or_independent_audit_never_becomes_missing(object_schema_scene):
    seed = object_schema_scene; client = seed['client']
    path, preview_body, body, accepted = _accept_root(seed)
    compilation = _compile_body(client, preview_body['role_bindings'])
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert checked['semantic_runtime_plan']['schema'] == 'instance-object-graph-runtime-plan-v171'
    _assert_faults(client, path, body, accepted, compilation)


def test_new_graph_revision_missing_inventory_is_rejected_without_changing_root(object_schema_scene):
    seed = object_schema_scene; client = seed['client']
    path, original_preview, root_body, root = _accept_root(seed)
    current = reply(client.get('/api/setup/draft'))
    revised = copy.deepcopy(original_preview)
    revised.update(schema='report-object-graph-revision-preview-command-v171',
        configuration_revision=current['revision'], configuration_hash=digest(current['configuration']),
        parent={'id': root['receipt']['id'], 'receipt_hash': root['receipt']['receipt_hash']})
    # A genuine new human-output contract, retaining all original source owners,
    # precondition, two parallel steps and all-join policy. No constant bypass.
    node = next(item for item in revised['graph']['nodes'] if item['id'] == 'a')
    node['output_schema']['properties']['review_note'] = {'type': 'string', 'maxLength': 200}
    assert node['output_schema']['required'] == ['ok']
    preview = reply(client.post(path + '/revisions/preview', json=revised))
    body = {**revised, 'schema': 'report-object-graph-revision-command-v171',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'],
        'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/revisions/adopt', json=body))
    assert accepted['receipt']['parent_id'] == root['receipt']['id']
    assert accepted['receipt']['parent_receipt_hash'] == root['receipt']['receipt_hash']
    assert accepted['effect_scope'] == 'future_releases_only'
    compilation = _compile_body(client, original_preview['role_bindings'])
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert checked['semantic_runtime_plan']['schema'] == 'instance-object-graph-runtime-plan-v171'
    _assert_faults(client, path, body, accepted, compilation, revision=True)
    assert reply(client.get(path + '/requests/' + root_body['request_id']))['receipt'] == root['receipt']
    unknown = reply(client.get(path + '/revisions/requests/' + str(uuid4())))
    assert unknown == {'schema': 'report-object-graph-revision-recovery-v171', 'found': False}

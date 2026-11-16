"""Actual V166 inventory/value/confirmation gates with explicit public IO seams.

The original report load_source dependency and material freezer's IO return a
public synthetic scene. They do not prove its machine/source cold chain, PG
locking or real adoption. V166 inventories, _source's closed basis check, the
actual IAM query and human value/confirmation gates run unchanged. Root runs.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents import report_object_adoption_source_v169 as source
from generic_agents import report_object_revision_store_v166 as revisions
from generic_agents.report_object_revision_api_v166 import command_path, wire_sha256
from generic_agents.report_object_revision_models_v166 import ReportObjectRevision as Revision
from generic_agents.report_object_revision_models_v166 import ReportObjectRevisionCommand as Command
from tests.test_report_object_semantics_v166 import scene


def uid(number):
    return '169abc00-0000-4000-8000-' + f'{number:012x}'


WS, ACTOR, REPORT, ROW, SESSION = [uid(n) for n in range(1, 6)]
P = NS(user_id=ACTOR, workspace_id=WS, role='operator', session_id=SESSION)
WHEN = datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc)


def bundle(operations=('save', 'confirm')):
    _, machine, inputs = scene()
    basis = {'report_id': REPORT, 'report_revision': 4, 'model_result_hash': 'a' * 64,
        'source_hash': inputs['catalog']['descriptor']['source_hash'],
        'catalog_hash': inputs['catalog']['descriptor']['catalog_hash'],
        'context_hash': digest(inputs['context']), 'base_value_hash': digest(machine)}
    current = {'basis': basis, 'basis_hash': digest(basis), 'value': machine, **inputs}
    row = Revision(id=ROW, workspace_id=WS, report_id=REPORT, actor_id=ACTOR,
        status='draft', version=1, basis_hash=digest(basis), record_hash='', data={})
    commands, audits, previous = [], [], None
    for version, operation in enumerate(operations, 1):
        human = copy.deepcopy(machine)
        human['note'] = '独立私人笔记，第' + str(version) + '版，禁止共享。'
        for obj in human['result']['objects']:
            for field in obj['fields']:
                field['required'] = False
        for ack in human['object_identity_confirmations']:
            ack['acknowledge_platform_identity'] = operation == 'confirm'
        body = {'request_id': 'object-adoption-source-' + str(version).zfill(4),
            'expected_version': version - 1, 'basis_hash': digest(basis), 'value': human}
        raw = json.dumps(body, ensure_ascii=False, indent=1).encode('utf-8')
        row.version = version
        row.status = 'confirmed' if operation == 'confirm' else 'draft'
        row.data = {'schema': revisions.STATE_SCHEMA, 'basis': copy.deepcopy(basis), 'value': human}
        snapshot = revisions._snapshot(row)
        row.record_hash = digest(snapshot)
        command = Command(id=uid(100 + version), workspace_id=WS, actor_id=ACTOR,
            report_id=REPORT, revision_id=ROW, record_version=version,
            request_id=body['request_id'], operation=operation, method='POST',
            path=command_path(REPORT, operation), wire_sha256=wire_sha256(raw),
            body_hash=digest(body), created_at=WHEN + timedelta(seconds=version), command_hash='',
            data={'schema': revisions.COMMAND_SCHEMA, 'wire': raw.decode(), 'body': copy.deepcopy(body),
                'record_snapshot': snapshot, 'previous_hash': previous})
        command.data['result'] = revisions._result(command)
        command.command_hash = digest(revisions._command_pin(command))
        audits.append(m.Audit(id=uid(200 + version), workspace_id=WS, actor_id=ACTOR,
            action=revisions.ACTION, object_id=command.id, data=revisions._audit_pin(command)))
        commands.append(command)
        previous = row.record_hash
    frozen = {kind: {'ref': {'id': uid(300 + index), 'version': 1, 'hash': str(index) * 64},
        'name': '公开合成' + kind, 'evidence_id': uid(310 + index)}
        for index, kind in enumerate(('original', 'copy'), 1)}
    report = m.SetupReportDraft(id=REPORT, workspace_id=WS, actor_id=ACTOR,
        request_id='synthetic-report-source', status='needs_clarification', version=4,
        data={'preview': {'source_snapshot': copy.deepcopy(frozen),
            'body': {'selection': {'public_synthetic_source': True}}}})
    return NS(row=row if operations else None, commands=commands, audits=audits,
        current=current, report=report, frozen=frozen)


class ReadIO:
    """Only the actual read/SHARE-lock families; any write is an assertion."""
    def __init__(self, data, *, permitted=True, independent_audits=None, audit_lock=True):
        self.data, self.permitted = data, permitted
        self.independent_audits = independent_audits
        self.audit_lock = audit_lock
        self.calls = []

    def execute(self, statement, params=None):
        if params is not None:
            assert str(statement) == 'SELECT pg_advisory_xact_lock_shared(:key)'
            actor = {'report_object_revision_inventory_v166': ACTOR, 'workspace_id': WS}
            report = {'report_object_revision_report_v166': REPORT, 'workspace_id': WS}
            keys = {int(digest(value)[:16], 16) - (1 << 63): name
                for name, value in [('actor', actor), ('report', report)]}
            assert params == {'key': params['key']} and params['key'] in keys
            self.calls.append(('lock', keys[params['key']]))
            return None
        # This is relations.lock_current_actor's real User/Membership/Session
        # query, not a passing replacement for that validator.
        assert [item['entity'] for item in statement.column_descriptions] == [m.User, m.Membership, m.AuthSession]
        assert statement._for_update_arg is not None and statement._for_update_arg.read is True
        assert statement._execution_options['populate_existing'] is True
        sql = str(statement)
        assert 'auth_sessions.revoked IS false' in sql and 'auth_sessions.expires_at >' in sql
        assert 'users.active IS true' in sql and 'memberships.role IN' in sql
        parameters = list(statement.compile().params.values())
        assert all(value in parameters for value in (ACTOR, WS, SESSION, P.role))
        self.calls.append(('iam',))
        return NS(first=lambda: (object(), object(), object()) if self.permitted else None)

    def scalar(self, statement):
        assert statement.column_descriptions[0]['entity'] is m.SetupReportDraft
        parameters = list(statement.compile().params.values())
        assert WS in parameters
        if statement.column_descriptions[0]['name'] == 'actor_id':
            self.calls.append(('owner',))
            return self.data.report.actor_id if REPORT in parameters and ACTOR in parameters else None
        self.calls.append(('report-row',))
        assert REPORT in parameters and ACTOR in parameters
        return self.data.report

    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        assert model in {Revision, Command, m.Audit}
        parameters = []
        for value in statement.compile().params.values():
            parameters.extend(value if type(value) is list else [value])
        assert WS in parameters
        if statement._limit_clause is not None:
            assert model is m.Audit
            identities = [item.id for item in self.data.commands if item.id in parameters]
            assert len(identities) == 1
            expected = select(m.Audit).where(m.Audit.workspace_id == WS,
                m.Audit.action == revisions.ACTION, m.Audit.object_id == identities[0]).limit(2)
            if self.audit_lock:
                expected = expected.with_for_update(read=True).execution_options(populate_existing=True)
            assert statement.compare(expected)
            if self.audit_lock:
                assert statement._for_update_arg is not None and statement._for_update_arg.read is True
                assert statement._execution_options['populate_existing'] is True
            else:
                assert statement._for_update_arg is None
            self.calls.append(('independent-audit', identities[0]))
            return (self.independent_audits if self.independent_audits is not None
                else [item for item in self.data.audits if item.object_id == identities[0]])
        assert statement._for_update_arg is None
        scope = 'report' if REPORT in parameters else 'actor' if ACTOR in parameters else None
        self.calls.append(('inventory', scope, model))
        if model is m.Audit:
            assert revisions.ACTION in parameters
        rows = {Revision: [] if self.data.row is None else [self.data.row],
            Command: self.data.commands, m.Audit: self.data.audits}[model]
        return list(rows) if scope else []

    def add(self, _):
        raise AssertionError('Source readers cannot write business rows')

    def flush(self):
        raise AssertionError('Source readers cannot flush')

    def commit(self):
        raise AssertionError('Caller owns the adoption transaction')


def expected_origin(data, version=2):
    command = next(item for item in data.commands if item.record_version == version)
    audit = next(item for item in data.audits if item.object_id == command.id)
    snapshot = command.data['record_snapshot']
    return {'schema': 'report-object-confirmed-origin-v169', 'report_id': REPORT,
        'actor_id': ACTOR, 'workspace_id': WS, 'revision_id': ROW, 'revision_version': version,
        'revision_record_hash': digest(snapshot), 'basis_hash': snapshot['basis_hash'],
        'confirmed_command_id': command.id, 'confirmed_command_hash': command.command_hash,
        'confirmed_wire_sha256': command.wire_sha256,
        'confirmed_value_hash': digest(snapshot['data']['value']), 'confirmed_audit_id': audit.id,
        'confirmed_audit_hash': digest({key: getattr(audit, key) for key in
            ('id', 'workspace_id', 'actor_id', 'action', 'object_id', 'data')})}


def current_io(monkeypatch, data, db):
    from generic_agents import report_object_revision_source_v166 as original
    from generic_agents import report_full_reading_source as materials

    def read_source(actual_db, p, report_id, lock=False):
        assert actual_db is db and p is P and report_id == REPORT
        db.calls.append(('current-source-io', lock))
        return copy.deepcopy(data.current)

    def freeze(actual_db, p, selection, *, lock=False):
        assert actual_db is db and p is P and selection == data.report.data['preview']['body']['selection']
        db.calls.append(('material-io', lock))
        return copy.deepcopy(data.frozen)

    # Explicit dependency IO only. The actual revisions._source closed checker,
    # full.material_refs, validate_value, confirm_value and all inventories run.
    monkeypatch.setattr(original, 'load_source', read_source)
    monkeypatch.setattr(materials, 'freeze_full_source', freeze)


@pytest.mark.parametrize('lock', [False, True])
def test_fresh_latest_human_confirmation_keeps_optional_fields_and_actual_audit(lock, monkeypatch):
    data = bundle(); db = ReadIO(data); current_io(monkeypatch, data, db)
    before = copy.deepcopy((data.row.data, [item.data for item in data.commands], data.current))
    result = source.load_confirmed(db, P, REPORT, lock=lock)
    assert set(result) == {'origin', 'result', 'object_identity_confirmations', 'basis', 'context', 'materials'}
    assert result['origin'] == expected_origin(data)
    assert result['result'] == data.row.data['value']['result']
    assert result['result'] != data.current['value']['result'], 'Adoption must retain real human changes'
    assert all(not field['required'] for obj in result['result']['objects'] for field in obj['fields'])
    assert all(item['acknowledge_platform_identity'] is True for item in result['object_identity_confirmations'])
    serialized = json.dumps(result, ensure_ascii=False)
    assert '独立私人笔记' not in serialized and 'note' not in result and 'wire' not in result
    assert result['origin']['confirmed_value_hash'] == digest(data.row.data['value'])
    assert result['materials'] == [{**copy.deepcopy(data.frozen[kind]['ref']),
        'name': data.frozen[kind]['name'], 'evidence_id': data.frozen[kind]['evidence_id']}
        for kind in ('original', 'copy')]
    owner = db.calls.index(('owner',)); actor = db.calls.index(('lock', 'actor'))
    report = db.calls.index(('lock', 'report')); iam = db.calls.index(('iam',))
    assert owner < actor < report < iam < db.calls.index(('current-source-io', lock))
    assert db.calls.index(('current-source-io', lock)) < db.calls.index(('material-io', lock))
    assert (data.row.data, [item.data for item in data.commands], data.current) == before
    result['result']['objects'][0]['fields'][0]['required'] = True
    result['object_identity_confirmations'].clear(); result['context'].clear(); result['materials'].clear()
    assert (data.row.data, [item.data for item in data.commands], data.current) == before


@pytest.mark.parametrize('operations', [(), ('save',), ('save', 'confirm', 'save')])
def test_empty_draft_or_confirm_then_new_draft_is_not_a_latest_confirmed_source(operations):
    data = bundle(operations); db = ReadIO(data)
    with pytest.raises(HTTPException) as error:
        source.load_confirmed(db, P, REPORT, lock=True)
    assert error.value.status_code == 409 and ('iam',) not in db.calls


def test_current_real_actor_session_gate_denies_before_any_source_io():
    data = bundle(); db = ReadIO(data, permitted=False)
    with pytest.raises(HTTPException) as error:
        source.load_confirmed(db, P, REPORT, lock=True)
    assert error.value.status_code == 403 and db.calls[-1] == ('iam',)


@pytest.mark.parametrize('fault', ['wire', 'audit', 'record-hash', 'missing-command-and-audit', 'bool-version'])
def test_existing_inventory_damage_never_becomes_a_source_or_empty_success(fault):
    data = bundle()
    if fault == 'wire': data.commands[-1].data['wire'] += ' '
    elif fault == 'audit': data.audits[-1].data['record_hash'] = 'f' * 64
    elif fault == 'record-hash': data.row.record_hash = 'f' * 64
    elif fault == 'missing-command-and-audit': data.commands.clear(); data.audits.clear()
    else: data.row.version = True
    db = ReadIO(data)
    with pytest.raises(HTTPException) as error:
        source.load_confirmed(db, P, REPORT)
    assert error.value.status_code == 409 and ('iam',) not in db.calls


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'actor', 'pin'])
def test_confirmation_reads_and_checks_an_actual_independent_audit(fault, monkeypatch):
    data = bundle(); actual = copy.deepcopy(data.audits[-1]); rows = [actual]
    if fault == 'missing': rows = []
    elif fault == 'duplicate': rows.append(copy.deepcopy(actual))
    elif fault == 'actor': actual.actor_id = uid(999)
    else: actual.data['record_hash'] = 'f' * 64
    db = ReadIO(data, independent_audits=rows); current_io(monkeypatch, data, db)
    with pytest.raises(HTTPException) as error:
        source.load_confirmed(db, P, REPORT)
    assert error.value.status_code == 409
    assert ('independent-audit', data.commands[-1].id) in db.calls
    assert not any(call[0] == 'material-io' for call in db.calls)


@pytest.mark.parametrize('fault', ['basis', 'allowed-refs', 'context', 'source-unavailable', 'materials-changed'])
def test_fresh_current_source_grounding_basis_and_materials_cannot_be_skipped(fault, monkeypatch):
    data = bundle(); db = ReadIO(data); current_io(monkeypatch, data, db)
    if fault == 'basis':
        data.current['basis']['model_result_hash'] = 'f' * 64
        data.current['basis_hash'] = digest(data.current['basis'])
    elif fault == 'allowed-refs': data.current['allowed_span_refs'] = []
    elif fault == 'context': data.current['context']['object_names'] = [data.row.data['value']['result']['objects'][0]['name']]
    elif fault == 'source-unavailable':
        from generic_agents import report_object_revision_source_v166 as original
        def unavailable(*args, **kwargs):
            raise HTTPException(409, 'Explicit source IO unavailable')
        monkeypatch.setattr(original, 'load_source', unavailable)
    else: data.frozen['original']['ref']['version'] = 2
    with pytest.raises(HTTPException) as error:
        source.load_confirmed(db, P, REPORT, lock=True)
    assert error.value.status_code == 409
    assert data.row.status == 'confirmed' and data.row.version == 2


def test_authenticated_shared_ledger_history_uses_original_confirm_after_new_draft(monkeypatch):
    data = bundle(('save', 'confirm', 'save')); db = ReadIO(data, permitted=False, audit_lock=False)
    origin = expected_origin(data)
    from generic_agents import report_object_revision_source_v166 as original
    from generic_agents import relations
    from generic_agents import report_full_semantic_handoff as full
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical shared ledger must not renew actor/source/material authority')
    monkeypatch.setattr(original, 'load_source', forbidden)
    monkeypatch.setattr(relations, 'lock_current_actor', forbidden)
    monkeypatch.setattr(full, 'material_refs', forbidden)
    # This synthetic viewer stands only for the already authenticated outer
    # shared ledger; the helper must not use it as the historical author.
    viewer = NS(user_id=uid(700), workspace_id=WS, role='viewer', session_id=uid(701))
    result = source.check_saved_origin(db, viewer, origin)
    assert set(result) == {'origin', 'result', 'object_identity_confirmations', 'basis'}
    assert result['origin'] == origin and result['origin']['revision_version'] == 2
    assert result['result'] == data.commands[1].data['record_snapshot']['data']['value']['result']
    assert 'note' not in result and '独立私人笔记' not in json.dumps(result, ensure_ascii=False)
    assert not any(call[0] in {'iam', 'owner', 'report-row'} for call in db.calls)
    assert data.row.status == 'draft' and data.row.version == 3


@pytest.mark.parametrize('field', ['revision_version', 'revision_record_hash', 'basis_hash',
    'confirmed_command_id', 'confirmed_command_hash', 'confirmed_wire_sha256',
    'confirmed_value_hash', 'confirmed_audit_id', 'confirmed_audit_hash', 'actor_id', 'workspace_id'])
def test_shared_origin_cannot_change_a_confirmed_edge_or_scope(field):
    data = bundle(); origin = expected_origin(data)
    if field == 'revision_version': origin[field] = True
    elif field.endswith('_id'): origin[field] = uid(999)
    else: origin[field] = 'f' * 64
    with pytest.raises(HTTPException) as error:
        source.check_saved_origin(ReadIO(data, audit_lock=False), P, origin)
    assert error.value.status_code == 409


@pytest.mark.parametrize('fault', ['unknown-key', 'old-family', 'float-version', 'missing-key'])
def test_origin_is_closed_and_strict_before_any_sql(fault):
    data = bundle(); origin = expected_origin(data); db = ReadIO(data)
    if fault == 'unknown-key': origin['can_adopt'] = True
    elif fault == 'old-family': origin['schema'] = 'report-evidence-manual-review-v5'
    elif fault == 'float-version': origin['revision_version'] = 2.0
    else: origin.pop('confirmed_audit_hash')
    with pytest.raises(HTTPException) as error:
        source.check_saved_origin(db, P, origin)
    assert error.value.status_code == 409 and db.calls == []


@pytest.mark.parametrize('lock', [0, 1, 'true', None])
def test_fresh_lock_mode_is_not_coerced(lock):
    db = ReadIO(bundle())
    with pytest.raises(HTTPException) as error:
        source.load_confirmed(db, P, REPORT, lock=lock)
    assert error.value.status_code == 409 and db.calls == []

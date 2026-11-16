"""Adoption witnesses over real ORM model objects and SELECT-only unit IO.

No paid CloudCall/Job or authenticated DB success is manufactured. The public
native fixture supplies untrusted pure evidence, not actual paid/source/IAM
authority. Real original-command hashes, both independent original Audits and
dual links run unchanged. Root owns positive normal paid/runtime acceptance.
"""
import base64
import copy
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents import report_business_catalogue_allocation_apply_v213 as subject
from generic_agents import report_business_catalogue_store_v211 as catalogue
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_preparation_v213 as preparation
from generic_agents import report_business_catalogue_allocation_plan_v213 as planning
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents.report_business_catalogue_models_v211 import ReportBusinessCatalogueDraftV211 as Draft
from generic_agents.contracts import digest
from tests.test_report_business_catalogue_allocation_native_v213 import scene, candidate, raw, no_io

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def uid():
    return str(uuid4())


class WitnessDB:
    """Known SELECT predicates only; no real DB/IAM or current-source claim."""
    def __init__(self, actor, workspace):
        self.actor, self.workspace = actor, workspace
        self.children, self.parents, self.rows, self.audits, self.reads = {}, {}, {}, [], []
    def scalars(self, statement):
        assert statement.is_select
        entity = statement.column_descriptions[0]['entity']
        params = list(statement.compile().params.values())
        self.reads.append(entity)
        if entity is subject.Child:
            result = [row for row in self.children.values()
                if row.workspace_id == self.workspace
                and (self.actor not in params or row.actor_id == self.actor)]
            if any(row.id in params for row in result):
                result = [row for row in result if row.id in params]
            elif any(row.request_id in params for row in result):
                result = [row for row in result if row.request_id in params]
            elif any(row.draft_id in params for row in result):
                versions = [value for value in params if type(value) is int]
                result = [row for row in result if row.draft_id in params
                    and (not versions or row.draft_version in versions)]
            else:
                result = []
            if statement.column_descriptions[0]['name'] == 'id':
                return [row.id for row in result]
            return result
        if entity is consumer.Command:
            values = list(self.parents.values())
            ids = [row.id for row in values if row.id in params]
            return [row for row in values if not ids or row.id in ids]
        if entity is consumer.Consumer:
            values = list(self.rows.values())
            ids = [row.id for row in values if row.id in params]
            return [row for row in values if not ids or row.id in ids]
        if entity is m.Audit:
            flat = [item for value in params for item in (value if type(value) is list else [value])]
            actions = {row.action for row in self.audits if row.action in flat}
            objects = {row.object_id for row in self.audits if row.object_id in flat}
            versions = [value for value in flat if type(value) is int]
            return [row for row in self.audits if row.workspace_id == self.workspace
                and row.actor_id == self.actor and row.action in actions
                and (not objects or row.object_id in objects)
                and (not versions or row.action not in {catalogue.STATE_AUDIT, consumer.STATE_AUDIT}
                    or row.data.get('version') in versions)]
        if entity in {m.CloudCall, m.Job}:
            return []
        raise AssertionError('Unexpected unit SELECT model')
    def scalar(self, statement):
        values = self.scalars(statement)
        return values[0] if values else None
    def execute(self, statement, *args, **kwargs):
        # Advisory locks/current reader are explicit synthetic boundaries. No
        # history validator or model call is replaced by a successful stub.
        if not getattr(statement, 'is_select', False):
            assert 'pg_advisory_xact_lock' in str(statement)
        return NS(first=lambda: (NS(active=True), NS(role='operator')))
    def add(self, *args, **kwargs):
        raise AssertionError('Witness/orphan unit must not manufacture a saved success')
    def flush(self):
        raise AssertionError('Witness/orphan unit must not write')


def event(db, action, object_id, data):
    row = m.Audit(id=uid(), workspace_id=db.workspace, actor_id=db.actor,
        version=1, created_at=NOW, action=action, object_id=object_id, data=copy.deepcopy(data))
    db.audits.append(row)
    return row


def child_command(db, state, document, *, nonce, previous, version):
    state = {**copy.deepcopy(state), 'version': version, 'document_hash': digest(document)}
    body = {'request_id': nonce, 'expected_version': version - 1,
        'expected_record_hash': previous, 'document': copy.deepcopy(document), 'actions': []}
    exact = wire.codec.canonical(body)
    row = subject.Child(id=uid(), workspace_id=db.workspace, actor_id=db.actor,
        version=1, created_at=NOW, draft_id=state['id'], draft_version=version,
        request_id=nonce, operation='save', method='POST',
        path=catalogue.command_path(state['id'], 'save'), exact_body=exact,
        wire_sha256=hashlib.sha256(exact.encode()).hexdigest(), data={'previous_hash': previous,
            'document': copy.deepcopy(document), 'actions': [], 'state': state,
            'result': catalogue._result(state, 'save')})
    row.command_hash = digest(catalogue._command_pin(row))
    db.children[row.id] = row
    event(db, catalogue.COMMAND_AUDIT, row.id, catalogue._command_anchor(row))
    event(db, catalogue.STATE_AUDIT, row.draft_id,
        {**catalogue._command_anchor(row), 'origin_hash': state['origin_hash'], 'record_hash': digest(state)})
    return row


@pytest.fixture
def witnesses():
    cap, source, args, _evidence = scene()
    reference = copy.deepcopy(args['draft_ref'])
    actor = NS(user_id=reference['actor_id'], workspace_id=reference['workspace_id'],
        role='operator', session_id=uid())
    db = WitnessDB(actor.user_id, actor.workspace_id)
    base_state = {'schema': catalogue.STATE, 'id': reference['id'], 'workspace_id': actor.workspace_id,
        'actor_id': actor.user_id, 'consumer_id': args['draft_origin']['metadata']['consumer_id'],
        'request_id': uid(), 'created_at': catalogue._utc(NOW), 'version': reference['version'],
        'origin_hash': reference['origin_hash'], 'document_hash': reference['document_hash'], 'plan_ref': None}
    reference['record_hash'] = digest(base_state)
    args['draft_ref'] = reference
    args['plan'] = planning.build_plan(**args['plan']['source'], draft_ref=reference,
        document=args['document'], max_batch_items=1, name_origin=args['name_origin'])
    evidence = native.build_allocation_evidence(source, **args)
    prep = preparation.build_preparation(cap, source, **args)
    original = raw(candidate(evidence))
    projected = wire.build_projection(original, plan=args['plan'], batch_ordinal=0)
    keys = args['plan']['batches'][0]['decision_keys']
    proposal = wire.construct_apply_document(projected, original, plan=args['plan'], batch_ordinal=0,
        document=args['document'], draft_ref=reference, selected_decision_keys=keys)
    nonce, row_id = uid(), uid()
    body = {'request_id': nonce, 'expected_version': 7, 'draft_ref': reference,
        'expected_projection_hash': projected['projection_hash'], 'selected_decision_keys': keys,
        'expected_apply_proposal_hash': proposal['apply_proposal_hash'], 'confirm': True}
    base = child_command(db, base_state, args['document'], nonce=uid(), previous='f' * 64,
        version=reference['version'])
    saved = child_command(db, base_state, proposal['document'], nonce=subject.child_request(nonce),
        previous=reference['record_hash'], version=reference['version'] + 1)
    row = consumer.Consumer(id=row_id, workspace_id=actor.workspace_id, actor_id=actor.user_id,
        version=8, created_at=NOW, request_id=uid(), producer_id=source['snapshot']['producer']['id'],
        model_id=uid(), revision=2, grant_hash='a' * 64, status='review', job_id=uid(), reserved_call_id=None,
        data={'preview': {'preparation': prep, 'allocation_evidence': evidence}, 'projection': projected,
            'allocation_raw_arguments_b64': base64.b64encode(original).decode('ascii'),
            'allocation_apply': None, 'command_refs': []})
    exact = wire.codec.canonical(body)
    parent = consumer.Command(id=uid(), workspace_id=actor.workspace_id, actor_id=actor.user_id,
        version=1, created_at=NOW, request_id=nonce, consumer_id=row.id, operation=subject.OPERATION,
        method='POST', path=subject.path(row.id), exact_body=exact,
        body_sha256=hashlib.sha256(exact.encode()).hexdigest(),
        data={'schema': consumer.COMMAND, 'result': consumer.result(row, subject.OPERATION)})
    core = subject._link_core(actor, row, parent, saved, proposal)
    marker = {'schema': subject.APPLICATION, 'command_id': parent.id, 'child_command_id': saved.id,
        'draft_id': saved.draft_id, 'saved_version': saved.draft_version,
        'saved_document_hash': proposal['after_document_hash'], 'selected_decision_keys': keys,
        'base_document_hash': proposal['before_document_hash'],
        'projection_hash': proposal['projection_hash'], 'link_hash': digest(core)}
    parent.data = {'schema': consumer.COMMAND, 'result': {**parent.data['result'], 'application': marker}}
    parent.command_hash = consumer.h(consumer._command_pin(parent))
    ref = {'id': parent.id, 'request_id': nonce, 'operation': subject.OPERATION,
        'wire_sha256': parent.body_sha256}
    row.data = {**row.data, 'allocation_apply': copy.deepcopy(marker), 'command_refs': [ref]}
    db.rows[row.id], db.parents[parent.id] = row, parent
    event(db, consumer.COMMAND_AUDIT, parent.id, consumer._command_anchor(parent))
    event(db, consumer.STATE_AUDIT, row.id, consumer._state_anchor(row, '9' * 64))
    for action, object_id in ((subject.LINK_AUDIT, parent.id), (subject.CHILD_LINK_AUDIT, saved.id)):
        event(db, action, object_id, {**core, 'link_hash': digest(core)})
    draft = Draft(id=reference['id'], workspace_id=actor.workspace_id, actor_id=actor.user_id,
        version=saved.draft_version, created_at=NOW, consumer_id=base_state['consumer_id'],
        request_id=base_state['request_id'], origin_hash=reference['origin_hash'],
        document_hash=proposal['after_document_hash'], record_hash=digest(saved.data['state']),
        data={'document': copy.deepcopy(proposal['document']), 'plan_ref': None,
            'origin': copy.deepcopy(args['draft_origin'])})
    return NS(db=db, p=actor, row=row, parent=parent, base=base, saved=saved,
        draft=draft, ref=ref, body=body, proposal=proposal, marker=marker)


def links(f):
    return [event for event in f.db.audits if event.action in {subject.LINK_AUDIT, subject.CHILD_LINK_AUDIT}]


def check_catalogue(f):
    subject.check_catalogue_links(f.db, f.p, {f.draft.id: f.draft}, f.db.children, links(f))


def test_actual_original_command_and_audit_witnesses_close_without_whole_inventory_recursion(witnesses, monkeypatch):
    f = witnesses
    def forbidden(*args, **kwargs):
        raise AssertionError('Frozen witnesses must not recurse into whole/current inventories')
    monkeypatch.setattr(consumer, '_inventory', forbidden)
    monkeypatch.setattr(catalogue, '_inventory', forbidden)
    monkeypatch.setattr(subject.source, 'prepare_source', forbidden)
    assert subject.check_parent_command(f.db, f.p, f.row, f.parent, ref=f.ref) == f.marker
    subject.check_inventory_links(f.db, f.p, f.db.rows, f.db.parents, links(f))
    check_catalogue(f)
    assert len(f.db.children) == 2 and len(f.db.audits) == 8


@pytest.mark.parametrize('fault', ['base_command_audit', 'base_state_audit', 'saved_command_audit',
    'saved_state_audit', 'parent_command_audit', 'parent_state_audit', 'parent_link', 'child_link', 'duplicate_link'])
def test_each_independent_original_audit_and_each_link_is_required(witnesses, fault):
    f = witnesses
    mapping = {'base_command_audit': (catalogue.COMMAND_AUDIT, f.base.id, None),
        'base_state_audit': (catalogue.STATE_AUDIT, f.base.draft_id, f.base.draft_version),
        'saved_command_audit': (catalogue.COMMAND_AUDIT, f.saved.id, None),
        'saved_state_audit': (catalogue.STATE_AUDIT, f.saved.draft_id, f.saved.draft_version),
        'parent_command_audit': (consumer.COMMAND_AUDIT, f.parent.id, None),
        'parent_state_audit': (consumer.STATE_AUDIT, f.row.id, f.row.version),
        'parent_link': (subject.LINK_AUDIT, f.parent.id, None),
        'child_link': (subject.CHILD_LINK_AUDIT, f.saved.id, None)}
    if fault == 'duplicate_link':
        old = links(f)[0]
        event(f.db, old.action, old.object_id, old.data)
    else:
        action, object_id, version = mapping[fault]
        f.db.audits = [event for event in f.db.audits if not (event.action == action
            and event.object_id == object_id and (version is None or event.data.get('version') == version))]
    with pytest.raises(HTTPException) as error:
        check_catalogue(f)
    assert error.value.status_code == 409


@pytest.mark.parametrize('fault', ['base_doc', 'saved_raw', 'parent_raw', 'marker', 'link_value', 'base_ref'])
def test_resealing_a_single_mutated_row_does_not_replace_independent_originals(witnesses, fault):
    f = witnesses
    if fault == 'base_doc':
        f.base.data['document']['businesses'][0]['purpose'] += ' 单行篡改'
        f.base.command_hash = digest(catalogue._command_pin(f.base))
    elif fault == 'saved_raw':
        f.saved.exact_body += ' '
        f.saved.command_hash = digest(catalogue._command_pin(f.saved))
    elif fault == 'parent_raw':
        f.parent.exact_body += ' '
        f.parent.command_hash = consumer.h(consumer._command_pin(f.parent))
    elif fault == 'marker':
        f.row.data['allocation_apply']['saved_document_hash'] = 'f' * 64
    elif fault == 'link_value':
        links(f)[0].data['saved_document_hash'] = 'f' * 64
    else:
        body = copy.deepcopy(f.body); body['draft_ref']['record_hash'] = 'f' * 64
        f.parent.exact_body = wire.codec.canonical(body)
        f.parent.body_sha256 = hashlib.sha256(f.parent.exact_body.encode()).hexdigest()
        f.parent.command_hash = consumer.h(consumer._command_pin(f.parent))
    with pytest.raises((HTTPException, ValueError)):
        check_catalogue(f)


def test_parent_deletion_remains_visible_from_saved_child_links(witnesses):
    f = witnesses
    f.db.parents.clear()
    with pytest.raises(HTTPException) as error:
        check_catalogue(f)
    assert error.value.status_code == 409


def test_later_human_document_edit_does_not_rebind_or_destroy_the_original_adoption_receipt(witnesses, monkeypatch):
    f = witnesses
    document = copy.deepcopy(f.draft.data['document'])
    document['businesses'][0]['purpose'] += '\n后来独立人工修改，必须保留😀'
    later = child_command(f.db, f.saved.data['state'], document, nonce=uid(),
        previous=f.draft.record_hash, version=f.draft.version + 1)
    f.draft.version += 1
    f.draft.data = {**f.draft.data, 'document': document}
    f.draft.document_hash = digest(document); f.draft.record_hash = digest(later.data['state'])
    def forbidden(*args, **kwargs):
        raise AssertionError('History is not a fresh source/review operation')
    monkeypatch.setattr(subject.source, 'prepare_source', forbidden)
    monkeypatch.setattr(catalogue, '_current_source', forbidden)
    assert subject.check_parent_command(f.db, f.p, f.row, f.parent, ref=f.ref) == f.marker
    check_catalogue(f)
    assert f.draft.data['document'] == document
    assert f.marker['saved_document_hash'] != f.draft.document_hash


@pytest.mark.parametrize('fault', ['unknown', 'bool_version', 'duplicate', 'bad_hash', 'not_confirmed'])
def test_adoption_dto_is_strict_and_never_coerces_or_confirms_a_partial_request(witnesses, fault):
    body = copy.deepcopy(witnesses.body)
    if fault == 'unknown': body['extra'] = True
    elif fault == 'bool_version': body['expected_version'] = True
    elif fault == 'duplicate': body['selected_decision_keys'] *= 2
    elif fault == 'bad_hash': body['expected_apply_proposal_hash'] = 'bad'
    else: body['confirm'] = False
    with pytest.raises((ValueError, HTTPException)):
        consumer.read_wire(wire.codec.canonical(body).encode(), subject.ApplyAllocationBody)


def test_selected_proposal_rejects_stale_consumer_before_any_current_source_or_write(witnesses, monkeypatch):
    f = witnesses
    body = subject.ApplyAllocationBody.model_validate(f.body)
    f.row.version += 1
    monkeypatch.setattr(subject.source, 'prepare_source', lambda *a, **k: pytest.fail('No fresh source for stale CAS'))
    with pytest.raises(HTTPException) as error:
        subject._selected_proposal(f.row, body)
    assert error.value.status_code == 409


def test_orphan_child_nonce_cannot_manufacture_a_paid_consumer_or_a_parent_success(witnesses, monkeypatch):
    f = witnesses
    # No Consumer or parent command exists: the REAL inventory runs over a
    # genuinely empty family, not a mocked successful paid/source inventory.
    f.db.rows.clear(); f.db.parents.clear()
    f.db.audits = [event for event in f.db.audits
        if event.action in {catalogue.COMMAND_AUDIT, catalogue.STATE_AUDIT}]
    monkeypatch.setattr(catalogue.plans, '_iam', lambda *args, **kwargs: None)
    monkeypatch.setattr(subject.source, 'prepare_source', lambda *a, **k: pytest.fail('Orphan checked before live source'))
    before = [row.id for row in f.db.children.values()]
    with pytest.raises(HTTPException) as error:
        subject.apply_selection(f.db, f.p, f.row.id, wire.codec.canonical(f.body).encode(),
            actual_method='POST', actual_path=subject.path(f.row.id))
    assert error.value.status_code == 409 and '派生保存编号' in str(error.value.detail)
    assert [row.id for row in f.db.children.values()] == before and not f.db.parents and not f.db.rows

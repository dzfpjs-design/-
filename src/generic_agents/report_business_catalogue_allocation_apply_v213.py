"""Explicit adoption and two-way witnesses for a saved batch suggestion.

History checks use frozen drafts, exact original arguments and original command
audits. They never acquire the current catalogue inventory or make model calls.
"""
import base64
import copy
import hashlib
import sys
from uuid import uuid5, NAMESPACE_URL

from fastapi import HTTPException
from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator
from sqlalchemy import select, or_

from .contracts import Contract, digest
from . import models as m
from .services import audit
from . import report_business_catalogue_store_v211 as catalogue
from . import report_business_catalogue_allocation_source_v213 as source
from . import report_business_catalogue_allocation_native_v213 as native
from . import report_business_catalogue_allocation_wire_v213 as wire
from .report_business_catalogue_models_v211 import ReportBusinessCatalogueCommandV211 as Child

LINK_AUDIT = 'report.catalogue_allocation_apply_link_v213'
CHILD_LINK_AUDIT = 'report.catalogue_allocation_child_link_v213'
LINK_SCHEMA = 'report-business-catalogue-allocation-link-v213'
APPLICATION = 'report-business-catalogue-allocation-application-v213'
OPERATION = 'apply-allocation'
DATA_KEY = 'allocation_apply'
SELECTION_PREVIEW = 'report-business-catalogue-allocation-selection-preview-v213'
MARKER_KEYS = {'schema', 'command_id', 'child_command_id', 'draft_id', 'saved_version',
    'saved_document_hash', 'selected_decision_keys', 'base_document_hash',
    'projection_hash', 'link_hash'}


class DraftReference(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: StrictStr
    workspace_id: StrictStr
    actor_id: StrictStr
    version: int = Field(strict=True, ge=1)
    record_hash: StrictStr
    origin_hash: StrictStr
    document_hash: StrictStr

    @field_validator('id', 'workspace_id', 'actor_id')
    @classmethod
    def identities(cls, value):
        return catalogue.identity(value)

    @field_validator('record_hash', 'origin_hash', 'document_hash')
    @classmethod
    def hashes(cls, value):
        if not catalogue.SHA.fullmatch(value):
            raise ValueError('请保留原目录摘要')
        return value


class AllocationSelectionBody(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(strict=True, ge=1)
    draft_ref: DraftReference
    expected_projection_hash: StrictStr
    selected_decision_keys: list[StrictStr] = Field(min_length=1, max_length=80)

    @field_validator('expected_projection_hash')
    @classmethod
    def hashes(cls, value):
        return DraftReference.hashes(value)

    @field_validator('selected_decision_keys')
    @classmethod
    def selection(cls, value):
        if value != sorted(set(value)) or any(wire._KEY.fullmatch(key) is None for key in value):
            raise ValueError('请明确选择本批要采纳的建议')
        return value


class ApplyAllocationBody(AllocationSelectionBody):
    request_id: StrictStr
    expected_apply_proposal_hash: StrictStr
    confirm: bool = Field(strict=True)

    @field_validator('request_id')
    @classmethod
    def identity(cls, value):
        return catalogue.identity(value)

    @field_validator('expected_apply_proposal_hash')
    @classmethod
    def proposal_hash(cls, value):
        return DraftReference.hashes(value)

    @model_validator(mode='after')
    def confirmation(self):
        if self.confirm is not True:
            raise ValueError('请确认采纳并保存所选建议')
        return self


ApplyBody = ApplyAllocationBody


def _protocol(value):
    if value is None: return sys.modules[__name__]
    from . import catalogue_current_allocation_apply_v260 as current
    need(value is current)
    return value


def marker_pins(row):
    return {}


def before_selection(db, p, row, body):
    return None


def _consumer():
    from . import report_saved_map_design_consumer_v176 as consumer
    return consumer


def need(ok, message='归类采纳的双方命令或独立保存证明不完整；原稿保留'):
    if not ok:
        raise HTTPException(409, message)


def path(consumer_id):
    return _consumer().PREFIX + '/' + catalogue.identity(consumer_id) + '/apply-allocation'


def child_request(request_id, workspace_id=None):
    return str(uuid5(NAMESPACE_URL, APPLICATION + ':' + catalogue.identity(request_id)))


def checked_allocation_basis(row, expected_profile):
    consumer = _consumer()
    need(expected_profile in {'catalogue-allocation-v213', 'catalogue-current-allocation-v260'})
    need(consumer._family(row.data['preview']['preparation']).id == expected_profile)
    evidence = native.check_allocation_evidence(row.data['preview']['allocation_evidence'])
    need(evidence['draft_ref']['actor_id'] == row.actor_id
        and evidence['draft_ref']['workspace_id'] == row.workspace_id)
    encoded = row.data['allocation_raw_arguments_b64']
    need(type(encoded) is str and bool(encoded))
    raw = base64.b64decode(encoded, validate=True)
    need(base64.b64encode(raw).decode('ascii') == encoded and 0 < len(raw) <= 1024 * 1024)
    raw.decode('utf-8', errors='strict')
    projection = wire.check_projection(row.data['projection'], raw,
        plan=evidence['plan'], batch_ordinal=evidence['batch_ordinal'])
    return evidence, raw, projection


def _basis(row):
    return checked_allocation_basis(row, 'catalogue-allocation-v213')


def proposal_from_verified(evidence, raw, projection, selected):
    return wire.construct_apply_document(projection, raw, plan=evidence['plan'],
        batch_ordinal=evidence['batch_ordinal'], document=evidence['document'],
        draft_ref=evidence['draft_ref'], selected_decision_keys=selected)


def _proposal(row, selected):
    evidence, raw, projection = _basis(row)
    return proposal_from_verified(evidence, raw, projection, selected)


def _owned_child(db, p, command_id):
    command = db.scalar(select(Child).where(Child.id == command_id,
        Child.workspace_id == p.workspace_id, Child.actor_id == p.user_id))
    need(command is not None)
    return command


def _child_witness(db, p, command):
    need(command.version == 1 and command.workspace_id == p.workspace_id
        and command.actor_id == p.user_id and command.method == 'POST'
        and command.command_hash == digest(catalogue._command_pin(command)))
    raw = command.exact_body.encode('utf-8')
    body = catalogue.parse_wire(raw, command.operation)
    need(command.wire_sha256 == hashlib.sha256(raw).hexdigest()
        and command.request_id == body['request_id']
        and command.path == catalogue.command_path(None if command.operation == 'create' else command.draft_id,
            command.operation)
        and type(command.data) is dict
        and set(command.data) == {'previous_hash', 'document', 'actions', 'state', 'result'})
    state = command.data['state']
    need(type(state) is dict and set(state) == {'schema', 'id', 'workspace_id', 'actor_id',
        'consumer_id', 'request_id', 'created_at', 'version', 'origin_hash', 'document_hash', 'plan_ref'}
        and state['schema'] == catalogue.STATE and state['id'] == command.draft_id
        and state['workspace_id'] == p.workspace_id and state['actor_id'] == p.user_id
        and state['version'] == command.draft_version
        and state['document_hash'] == digest(command.data['document'])
        and catalogue.same(command.data['result'], catalogue._result(state, command.operation)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.actor_id == p.user_id,
        or_((m.Audit.action == catalogue.COMMAND_AUDIT) & (m.Audit.object_id == command.id),
            (m.Audit.action == catalogue.STATE_AUDIT) & (m.Audit.object_id == command.draft_id)
            & (m.Audit.data['version'].as_integer() == command.draft_version)))))
    command_events = [event for event in events if event.action == catalogue.COMMAND_AUDIT]
    state_events = [event for event in events if event.action == catalogue.STATE_AUDIT]
    need(len(events) == 2 and len(command_events) == len(state_events) == 1
        and catalogue.same(command_events[0].data, catalogue._command_anchor(command)))
    expected_state = {**catalogue._command_anchor(command), 'origin_hash': state['origin_hash'],
        'record_hash': digest(state)}
    need(catalogue.same(state_events[0].data, expected_state))
    return body, state


def _link_core(p, row, command, child, proposal):
    return {'schema': LINK_SCHEMA, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'parent': {'id': command.id, 'consumer_id': row.id, 'request_id': command.request_id,
            'body_sha256': command.body_sha256, 'path': command.path,
            'consumer_version': command.data['result']['version']},
        'child': {**catalogue._command_anchor(child), 'wire_sha256': child.wire_sha256},
        'base_draft_ref': copy.deepcopy(proposal['base_draft_ref']),
        'projection_hash': proposal['projection_hash'],
        'apply_proposal_hash': proposal['apply_proposal_hash'],
        'selected_decision_keys': copy.deepcopy(proposal['selected_decision_keys']),
        'saved_document_hash': proposal['after_document_hash']}


def check_parent_command(db, p, row, command, *, ref, _adapter=None):
    """Validate immutable witnesses without entering either current inventory."""
    proto = _protocol(_adapter)
    consumer = _consumer()
    body, exact, sha = consumer.read_wire(command.exact_body.encode('utf-8'), proto.ApplyBody)
    need(command.operation == proto.OPERATION and command.path == proto.path(row.id)
        and command.method == 'POST' and command.body_sha256 == sha
        and command.exact_body == exact and command.request_id == body.request_id
        and command.consumer_id == row.id and command.actor_id == p.user_id
        and command.workspace_id == p.workspace_id
        and command.command_hash == consumer.h(consumer._command_pin(command))
        and consumer.same(ref, {'id': command.id, 'request_id': command.request_id,
            'operation': proto.OPERATION, 'wire_sha256': sha})
        and type(command.data) is dict and set(command.data) == {'schema', 'result'}
        and command.data['schema'] == consumer.COMMAND)
    result = command.data['result']
    need(type(result) is dict and set(result) == {'schema', 'id', 'version', 'operation',
        'status', 'job_id', 'application'} and result['schema'] == consumer.RESULT
        and result['id'] == row.id and result['job_id'] == row.job_id
        and result['operation'] == proto.OPERATION and result['version'] == body.expected_version + 1
        and result['status'] in {'review', 'needs_clarification'})
    marker = result['application']
    need(type(marker) is dict and set(marker) == proto.MARKER_KEYS and marker['schema'] == proto.APPLICATION
        and marker['command_id'] == command.id
        and consumer.same(row.data[proto.DATA_KEY], marker))
    proposal = proto._proposal(row, body.selected_decision_keys)
    need(catalogue.same(body.draft_ref.model_dump(), proposal['base_draft_ref'])
        and body.expected_projection_hash == proposal['projection_hash']
        and body.expected_apply_proposal_hash == proposal['apply_proposal_hash'])
    child = _owned_child(db, p, marker['child_command_id'])
    child_body, child_state = _child_witness(db, p, child)
    reference = proposal['base_draft_ref']
    need(reference['actor_id'] == p.user_id and reference['workspace_id'] == p.workspace_id)
    base = db.scalar(select(Child).where(Child.workspace_id == p.workspace_id,
        Child.actor_id == p.user_id, Child.draft_id == reference['id'],
        Child.draft_version == reference['version']))
    need(base is not None)
    _, base_state = _child_witness(db, p, base)
    need(digest(base_state) == reference['record_hash']
        and base_state['document_hash'] == reference['document_hash']
        and base_state['origin_hash'] == reference['origin_hash']
        and base_state['plan_ref'] is None
        and catalogue.same(base.data['document'], row.data['preview']['allocation_evidence']['document'])
        and child.operation == 'save' and child.draft_id == reference['id']
        and child.draft_version == reference['version'] + 1
        and child.request_id == proto.child_request(command.request_id, p.workspace_id)
        and child_body['expected_version'] == reference['version']
        and child_body['expected_record_hash'] == reference['record_hash']
        and child.data['previous_hash'] == reference['record_hash']
        and catalogue.same(child_body['document'], proposal['document'])
        and catalogue.same(child.data['document'], proposal['document'])
        and child_body['actions'] == child.data['actions'] == []
        and child_state['plan_ref'] is None)
    need(catalogue.same(child_state, {**base_state, 'version': child.draft_version,
        'document_hash': proposal['after_document_hash']}))
    core = proto._link_core(p, row, command, child, proposal)
    link_hash = digest(core)
    expected_marker = {'schema': proto.APPLICATION, 'command_id': command.id,
        'child_command_id': child.id, 'draft_id': child.draft_id,
        'saved_version': child.draft_version, 'saved_document_hash': proposal['after_document_hash'],
        'selected_decision_keys': proposal['selected_decision_keys'],
        'base_document_hash': proposal['before_document_hash'],
        'projection_hash': proposal['projection_hash'], 'link_hash': link_hash, **proto.marker_pins(row)}
    need(catalogue.same(marker, expected_marker))
    linked = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([proto.LINK_AUDIT, proto.CHILD_LINK_AUDIT]),
        or_(m.Audit.object_id == command.id, m.Audit.object_id == child.id))))
    need(len(linked) == 2 and {(event.action, event.object_id) for event in linked}
        == {(proto.LINK_AUDIT, command.id), (proto.CHILD_LINK_AUDIT, child.id)}
        and all(event.actor_id == p.user_id and catalogue.same(event.data, {**core, 'link_hash': link_hash})
            for event in linked))
    return copy.deepcopy(marker)


def check_inventory_links(db, p, by_consumer, by_command, link_events, *, _adapter=None):
    """Both link directions must remain visible even when a parent was deleted."""
    proto = _protocol(_adapter)
    checked, expected_events = set(), set()
    for command in by_command.values():
        if command.operation != proto.OPERATION:
            continue
        row = by_consumer.get(command.consumer_id)
        need(row is not None)
        marker = proto.check_parent_command(db, p, row, command, ref={'id': command.id,
            'request_id': command.request_id, 'operation': proto.OPERATION, 'wire_sha256': command.body_sha256})
        checked.add(command.id)
        expected_events.update({(proto.LINK_AUDIT, command.id), (proto.CHILD_LINK_AUDIT, marker['child_command_id'])})
    actual = [(event.action, event.object_id) for event in link_events]
    need(len(actual) == len(set(actual)) and set(actual) == expected_events)
    for row in by_consumer.values():
        if row.data.get(proto.DATA_KEY) is not None:
            need(row.data[proto.DATA_KEY].get('command_id') in checked)


def check_catalogue_links(db, p, by_draft, by_command, link_events, *, _adapter=None):
    """A catalogue read checks child witnesses without calling Consumer.inventory."""
    proto = _protocol(_adapter)
    consumer = _consumer()
    need(all(event.action in {proto.LINK_AUDIT, proto.CHILD_LINK_AUDIT} and event.actor_id == p.user_id
        and type(event.data) is dict for event in link_events))
    parents = list(db.scalars(select(consumer.Command).where(
        consumer.Command.workspace_id == p.workspace_id, consumer.Command.actor_id == p.user_id,
        consumer.Command.operation == proto.OPERATION)))
    expected_links = set()
    for parent in parents:
        need(type(parent.data) is dict and type(parent.data.get('result')) is dict
            and type(parent.data['result'].get('application')) is dict)
        child = by_command.get(parent.data['result']['application'].get('child_command_id'))
        need(child is not None and child.draft_id in by_draft)
        row = db.scalar(select(consumer.Consumer).where(consumer.Consumer.id == parent.consumer_id,
            consumer.Consumer.workspace_id == p.workspace_id, consumer.Consumer.actor_id == p.user_id))
        need(row is not None)
        command_anchors = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.actor_id == p.user_id, m.Audit.action == consumer.COMMAND_AUDIT,
            m.Audit.object_id == parent.id)))
        state_anchors = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.actor_id == p.user_id, m.Audit.action == consumer.STATE_AUDIT,
            m.Audit.object_id == row.id,
            m.Audit.data['version'].as_integer() == row.version)))
        need(len(command_anchors) == len(state_anchors) == 1
            and consumer.same(command_anchors[0].data, consumer._command_anchor(parent))
            and consumer.same(state_anchors[0].data,
                consumer._state_anchor(row, state_anchors[0].data['previous_state_hash']))
            and any(ref['id'] == parent.id for ref in row.data['command_refs']))
        proto.check_parent_command(db, p, row, parent, ref={'id': parent.id, 'request_id': parent.request_id,
            'operation': proto.OPERATION, 'wire_sha256': parent.body_sha256})
        expected_links.update({(proto.LINK_AUDIT, parent.id), (proto.CHILD_LINK_AUDIT, child.id)})
    pairs = [(event.action, event.object_id) for event in link_events]
    need(len(pairs) == len(set(pairs)) and set(pairs) == expected_links)


def _selected_proposal(row, body, *, _adapter=None):
    proto = _protocol(_adapter)
    need(type(row.version) is int and row.version == body.expected_version,
        '本批记录已变化，请保留选择并重新读取原申请')
    need(row.status in {'review', 'needs_clarification'} and row.data.get('projection') is not None,
        '本批还没有可核对的完整归类建议；原回答保留')
    need(row.data.get(proto.DATA_KEY) is None,
        '本批已采纳过所选建议，余下建议保留只读；请基于新稿准备下一批')
    proposal = proto._proposal(row, body.selected_decision_keys)
    need(catalogue.same(body.draft_ref.model_dump(), proposal['base_draft_ref'])
        and body.expected_projection_hash == proposal['projection_hash'],
        '所选建议与原目录版本不一致，未重新配对或覆盖')
    return proposal


def preview_selection(db, p, consumer_id, body, *, _adapter=None):
    """No expense, permission promotion, source claim, or catalogue write."""
    proto = _protocol(_adapter)
    source.lock_before_consumer(db, p)
    consumer = _consumer()
    rows, _, _ = consumer._inventory(db, p)
    row = consumer._owned(rows, p, catalogue.identity(consumer_id))
    proto.before_selection(db, p, row, body)
    proposal = proto._selected_proposal(row, body)
    _, _, _, current_ref, _, _, _, _ = source._saved(db, p, proposal['base_draft_ref']['id'])
    need(catalogue.same(current_ref, proposal['base_draft_ref']),
        '人工稿已变化，本批建议保留只读；请基于新稿准备下一批')
    return {'schema': proto.SELECTION_PREVIEW,
        'id': row.id, 'version': row.version, 'draft_ref': proposal['base_draft_ref'],
        'projection_hash': proposal['projection_hash'],
        'apply_proposal_hash': proposal['apply_proposal_hash'],
        'selected_decision_keys': proposal['selected_decision_keys'],
        'changes': proposal['changes'], 'before_document_hash': proposal['before_document_hash'],
        'after_document_hash': proposal['after_document_hash'],
        'validation': proposal['validation'], 'current_source_verified': False,
        'actual_paid_verified': False, 'can_dispatch': False, 'can_confirm': False,
        'requires_explicit_adoption': True, **proto.marker_pins(row)}


def prepare_apply_source(db, p, row, body, frozen):
    return source.prepare_source(db, p, body.draft_ref.id,
        expected_version=body.draft_ref.version, expected_document_hash=body.draft_ref.document_hash,
        expected_catalogue_review_hash=frozen['catalogue_review_hash'],
        selection=frozen['selection'], max_batch_items=frozen['max_batch_items'], write=True)


def apply_selection(db, p, consumer_id, raw, *, actual_method, actual_path, _adapter=None):
    """Caller commits only after both domains and both link audits are complete."""
    proto = _protocol(_adapter)
    consumer = _consumer()
    body, exact, sha = consumer.read_wire(raw, proto.ApplyBody)
    need(actual_method == 'POST' and actual_path == proto.path(consumer_id), '归类采纳路径与原正文不一致')
    catalogue.plans._iam(db, p, write=True)
    # Obtain the deterministic child's nonce before the Catalogue actor lock;
    # ordinary Catalogue.save follows that same order. Never invert it later.
    child_nonce = proto.child_request(body.request_id, p.workspace_id)
    catalogue._lock(db, p, child_nonce, write=True)
    consumer._nonce(db, p, body.request_id)
    consumer._any_nonce(db, p, body.request_id)
    rows, commands, _ = consumer._inventory(db, p, exclusive=True)
    prior = consumer._replay(commands, p, body.request_id, proto.OPERATION, actual_path, exact, sha)
    if prior is not None:
        need(prior['id'] == consumer_id)
        return prior
    # A child save without this original parent receipt is an orphan, never a
    # success to stitch retroactively into a newly manufactured parent command.
    need(db.scalar(select(Child.id).where(Child.workspace_id == p.workspace_id,
        Child.request_id == child_nonce)) is None,
        '派生保存编号已有记录但本次父回执不完整，未补造成功或覆盖原稿')
    row = consumer._owned(rows, p, consumer_id)
    proposal = proto._selected_proposal(row, body)
    need(body.expected_apply_proposal_hash == proposal['apply_proposal_hash'],
        '所选建议或完整修改预览已变化，未覆盖人工稿')
    evidence, _, _ = proto._basis(row)
    frozen = evidence['plan']
    current = proto.prepare_apply_source(db, p, row, body, frozen)
    need(catalogue.same(current['draft_ref'], frozen['base_draft_ref'])
        and catalogue.same(current['plan'], frozen),
        '资料、业务定义或人工稿已变化，本批保持只读')
    child_body = {'request_id': child_nonce, 'expected_version': body.draft_ref.version,
        'expected_record_hash': body.draft_ref.record_hash,
        'document': proposal['document'], 'actions': []}
    child_path = catalogue.command_path(body.draft_ref.id, 'save')
    child_raw = wire.codec.canonical(child_body).encode('utf-8')
    saved = catalogue.mutate(db, p, body.draft_ref.id, 'save', child_raw,
        actual_method='POST', actual_path=child_path)
    child = db.scalar(select(Child).where(Child.workspace_id == p.workspace_id,
        Child.actor_id == p.user_id, Child.request_id == child_nonce))
    need(child is not None and child.draft_id == body.draft_ref.id
        and saved['document_hash'] == proposal['after_document_hash'])
    command = consumer.Command(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.request_id, consumer_id=row.id, operation=proto.OPERATION,
        method='POST', path=actual_path, exact_body=exact, body_sha256=sha,
        data={'schema': consumer.COMMAND, 'result': {**consumer.result(row, proto.OPERATION),
            'version': row.version + 1}})
    core = proto._link_core(p, row, command, child, proposal)
    link_hash = digest(core)
    marker = {'schema': proto.APPLICATION, 'command_id': command.id, 'child_command_id': child.id,
        'draft_id': child.draft_id, 'saved_version': child.draft_version,
        'saved_document_hash': proposal['after_document_hash'],
        'selected_decision_keys': proposal['selected_decision_keys'],
        'base_document_hash': proposal['before_document_hash'],
        'projection_hash': proposal['projection_hash'], 'link_hash': link_hash, **proto.marker_pins(row)}
    command.data = {'schema': consumer.COMMAND,
        'result': {**command.data['result'], 'application': copy.deepcopy(marker)}}
    command.command_hash = consumer.h(consumer._command_pin(command))
    db.add(command)
    ref = {'id': command.id, 'request_id': body.request_id, 'operation': proto.OPERATION, 'wire_sha256': sha}
    consumer._transition(db, p, row, {**row.data, proto.DATA_KEY: marker,
        'command_refs': [*row.data['command_refs'], ref]})
    audit(db, p, consumer.COMMAND_AUDIT, command.id, consumer._command_anchor(command))
    for action, object_id in ((proto.LINK_AUDIT, command.id), (proto.CHILD_LINK_AUDIT, child.id)):
        audit(db, p, action, object_id, {**core, 'link_hash': link_hash})
    db.flush()
    proto.check_parent_command(db, p, row, command, ref=ref)
    return copy.deepcopy(command.data['result'])

"""Read original approved excerpts and exact, independently consented text.

These server-only DTOs include local bytes. They must not be serialized to a
public API or cloud request. The caller owns the normal Plan/Goal/role/lease
gates and separately persists any new preparation receipt or Evidence.
Nothing here creates, authorizes, submits, reconciles or sends anything.
"""
import copy
import hashlib
from functools import wraps

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import select

from . import evidence, runtime_task_documents as documents
from . import workflow_document_packet_v207 as packets
from . import workflow_evidence_consent_wait_v199 as consent
from . import business_reasoning_v197 as reasoning
from . import same_goal_advice_contracts_v302 as contracts
from .contracts import digest
from .models import Audit, Capability, Evidence, Membership, Task, User

DOCUMENTS = 'same-goal-advice-documents-resolved-v302'
CLOUD_DOCUMENTS = 'same-goal-advice-cloud-documents-v302'
ERROR = '原批准资料、独立收件、准确文字或逐模型授权无法核对；未创建资料、发送或取得执行权限'
MAX_GRANTS = 64
_SOURCE = TypeAdapter(contracts.SourceRef)
_IDS = TypeAdapter(list[contracts.ID])
_ID = TypeAdapter(contracts.ID)
_HASH = TypeAdapter(contracts.Hash)
_PACKET_PROOF = ('packet_id', 'evidence_id', 'sha256', 'task_id', 'producer_job_id',
    'invocation_id', 'source_set_id', 'source_set_hash', 'producer_hash')


def need(value):
    if not value: raise HTTPException(409, ERROR)


def checked(function):
    @wraps(function)
    def read(*args, **kwargs):
        try: return function(*args, **kwargs)
        except HTTPException: raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, UnicodeError,
                RecursionError, OverflowError):
            raise HTTPException(409, ERROR) from None
    return read


def _reader(db, p):
    # Match the actual current membership, not a cached/server-supplied role.
    need(db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role, Membership.role.in_(['admin', 'operator', 'viewer']))) == p.user_id)


def _fresh(db, p, model, identity):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
        model.id == identity).limit(2).execution_options(populate_existing=True)))
    need(len(rows) == 1)
    return rows[0]


def _text(row, content):
    need(type(content) is bytes and row.media_type == 'text/plain'
        and type(row.size_bytes) is int and 0 < row.size_bytes == len(content) <= documents.MAX_TEXT
        and row.sha256 == hashlib.sha256(content).hexdigest())
    need('\x00' not in content.decode('utf-8-sig'))
    return contracts._parse(contracts.EvidenceRef, {'evidence_id': row.id, 'sha256': row.sha256,
        'size_bytes': row.size_bytes, 'media_type': row.media_type})


def _approved(db, p, task, reference):
    # Refresh before the existing scoped source validator can reuse ORM rows.
    _fresh(db, p, documents.SourceSet, reference['source_set_id'])
    row, selected = packets.approved_set(db, p, task, reference)
    need(row.workspace_id == p.workspace_id and row.task_id == task.id
        and row.instance_id == task.data['instance_id'] and row.release_id == task.data['instance_release_id']
        and row.actor_id == task.owner_id and row.version == 1
        and row.id == reference['source_set_id'] and row.source_set_hash == reference['source_set_hash']
        and row.source_set_hash == digest(row.data))
    selected = contracts._parse(contracts.ApprovedSelection, selected)
    need(all(selected[key] == reference[key] for key in ('source_set_id', 'source_set_hash'))
        and selected['input_key'] == row.input_key)
    return row, selected


@checked
def resolve_documents(db, p, task, reference):
    """Resolve one actual V207 selection. None is the outer policy's decision.

    SourceSet validates the original command/submission, fixed native/OCR
    excerpts and Data ACK through approved_set. A Packet additionally proves
    its real original SDK/Job/graph completion through checked_packet.
    """
    with db.no_autoflush:
        _reader(db, p)
        need(isinstance(task, Task) and task.workspace_id == p.workspace_id)
        task = _fresh(db, p, Task, task.id)
        need(type(reference) is dict)
        contracts.base._json(reference)
        selection = _SOURCE.validate_python(copy.deepcopy(reference)).model_dump(mode='json')
        packet, receipt, original = None, None, None
        if selection['kind'] == 'document_packet_v207':
            original = selection['packet']
            packet = _fresh(db, p, packets.Packet, original['id'])
            need(packet.task_id == task.id and packet.version == original['version'] == 1
                and packet.evidence_id == original['evidence_id'] and packet.sha256 == original['sha256']
                and packet.source_set_id == original['source_set_id'])
            proof = packets.checked_packet(db, p, packet)
            receipt = {key: copy.deepcopy(proof[key]) for key in _PACKET_PROOF}
            for key, value in receipt.items():
                (_HASH if key in {'sha256', 'source_set_hash', 'producer_hash'} else _ID).validate_python(value)
            need(receipt['packet_id'] == packet.id and receipt['task_id'] == task.id
                and receipt['evidence_id'] == packet.evidence_id and receipt['sha256'] == packet.sha256
                and receipt['source_set_id'] == original['source_set_id']
                and receipt['source_set_hash'] == original['source_set_hash']
                and receipt['producer_job_id'] == packet.job_id and receipt['invocation_id'] == packet.invocation_id)
            source_ref = {key: original[key] for key in ('source_set_id', 'source_set_hash')}
        else:
            source_ref = selection['source_set_ref']
        source, selected = _approved(db, p, task, source_ref)
        content = documents.render(source.data['entries'])
        need(type(content) is bytes and 0 < len(content) <= documents.MAX_TEXT
            and '\x00' not in content.decode('utf-8-sig'))
        evidence_ref = None
        if packet is not None:
            _fresh(db, p, Evidence, packet.evidence_id)
            actual, raw = evidence.evidence_bytes(db, p, packet.evidence_id)
            need(actual.workspace_id == p.workspace_id and actual.id == packet.evidence_id
                and actual.sha256 == packet.sha256 == original['sha256'] and raw == content)
            evidence_ref = _text(actual, raw)
        return {'schema': DOCUMENTS, 'task_id': task.id, 'selection': selection,
            'approved_selection': selected, 'source_set': {'source_set_id': source.id,
                'source_set_hash': source.source_set_hash, 'task_id': source.task_id,
                'instance_id': source.instance_id, 'release_id': source.release_id, 'input_key': source.input_key,
                'actor_id': source.actor_id, 'source_snapshot_hash': digest(source.data)},
            'entries': copy.deepcopy(source.data['entries']), 'content': content,
            'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content),
            'packet_receipt': receipt, 'evidence_ref': evidence_ref}


def _model(db, p, model):
    need(isinstance(model, Capability) and model.workspace_id == p.workspace_id)
    expected = contracts._parse(contracts.CapabilityPin, {'id': model.id,
        'content_hash': model.content_hash, 'release_version': model.release_version})
    actual = _fresh(db, p, Capability, model.id)
    need(actual.kind == 'foundation_model' and actual.status == 'published'
        and actual.content_hash == actual.validated_hash == digest(actual.data) == expected['content_hash']
        and actual.release_version == expected['release_version']
        and actual.data['execution'].get('provider') in {'qwen', 'deepseek'}
        and actual.data['execution'].get('task') != 'embedding'
        and actual.data['execution'].get('report_evidence_output') is None
        and actual.data['data_policy'].get('cloud_allowed') is True)
    return actual, expected


@checked
def cloud_documents(db, p, evidence_ids, model):
    """Read consented text, without issuing or renewing any sending grant.

    Each current Evidence list entry also needs its independent V199 grant
    Audit. Historical source bytes may survive later consent changes, while
    this live preparation always requires the model's current consent.
    """
    with db.no_autoflush:
        _reader(db, p)
        need(type(evidence_ids) is list)
        contracts.base._json(evidence_ids)
        ids = _IDS.validate_python(copy.deepcopy(evidence_ids))
        need(1 <= len(ids) <= 3 and len(set(ids)) == len(ids))
        actual_model, model_pin = _model(db, p, model)
        docs, expected_pins, grants = [], [], []
        for identifier in ids:
            _fresh(db, p, Evidence, identifier)
            actual, content = evidence.evidence_bytes(db, p, identifier)
            need(actual.id == identifier and actual.workspace_id == p.workspace_id)
            ref = _text(actual, content)
            pin = contracts._parse(contracts.EvidencePin, {**ref, 'version': actual.version,
                'model_profile_id': actual_model.id})
            matches = consent._grants(db, actual, actual_model)
            need(0 < len(matches) <= MAX_GRANTS
                and len({item['audit_id'] for item in matches}) == len(matches))
            checked_grants=[]
            for item in matches:
                need(item['evidence_id'] == identifier)
                checked_grants.append(contracts._parse(contracts.ConsentPin,
                    {**item, 'model_profile_id': actual_model.id}))
            # The preview pins one genuine independent grant per Evidence.
            # Retain and verify every original Audit; repeated authorization
            # does not create duplicate Evidence inputs or extra permissions.
            grants.append(min(checked_grants,key=lambda item:item['audit_id']))
            expected_pins.append(pin)
            docs.append({'evidence_ref': ref, 'version': actual.version, 'content': content})
        # Reuse the actual established catalogue/text conversion. It reads the
        # specific model's current consent and stored bytes again before return.
        pins, parts = reasoning._evidence(db, p, ids, actual_model)
        need(digest(pins) == digest(expected_pins) and len(parts) == 2 * len(docs))
        for index, document in enumerate(docs):
            need(parts[2 * index + 1] == {'type': 'text', 'text': document['content'].decode('utf-8-sig')})
        return {'schema': CLOUD_DOCUMENTS, 'model_pin': model_pin, 'evidence_pins': copy.deepcopy(pins),
            'consent_pins': copy.deepcopy(grants), 'documents': docs, 'parts': copy.deepcopy(parts)}

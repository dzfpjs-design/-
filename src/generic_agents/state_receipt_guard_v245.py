"""Read-only state admission through the existing independent reception chain.

Extraction and reception are different transactions. A pending result is kept
as an excluded record; neither an Event flag nor a Memory flag admits a value.
Old global records and genuinely old generated releases retain their contract.
"""
from fastapi import HTTPException
from sqlalchemy import select

from . import instance_data_receipts as receipts
from .contracts import digest
from .models import Audit, Capability, DomainInstance, Event, Memory
from .services import scoped


def _need(value):
    if not value:
        raise HTTPException(409, '对象状态的独立收件依据无法核对；原登记与处理记录保留')


def _legacy_release(db, p, release, entity):
    """A missing modern plan must not become an unverified legacy escape."""
    package = scoped(db, Capability, release.package_id, p)
    instance = scoped(db, DomainInstance, release.instance_id, p)
    snapshot = release.data.get('snapshot')
    specification = package.data.get('specification', {})
    _need(release.status == 'prepared' and package.kind == 'domain_package'
          and type(snapshot) is dict and type(specification) is dict
          and digest(snapshot) == release.configuration_hash
          and specification.get('configuration_hash') == release.configuration_hash
          and package.content_hash == package.validated_hash == digest(package.data)
          and package.namespace == instance.namespace == entity.namespace)
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'instance.generated', Audit.object_id == release.id).limit(2)))
    _need(len(rows) == 1 and rows[0].actor_id == release.actor_id
          and rows[0].data == {'number': release.release_number, 'hash': release.configuration_hash})
    source = receipts._source_release(db, p, entity)
    if source is not None:
        _need(receipts.verify_release(db, p, source) is None)
        if source.id != release.id:
            _legacy_release(db, p, source, entity)


def _required(db, p, observation):
    # Reuse the same frozen destination as reception, including existing
    # acquisition/simulation/old-release lineage. Never choose the active one.
    release, entity, _ = receipts._destination(db, p, observation)
    if release is not None:
        if receipts.verify_release(db, p, release) is not None:
            return True
        _legacy_release(db, p, release, entity)
        return False
    if entity is not None:
        package = scoped(db, Capability, entity.schema_capability_id, p)
        specification = package.data.get('specification', {})
        _need(type(specification) is dict and 'configuration_hash' not in specification)
        # A lost release/package association is a broken managed destination,
        # not evidence that the object was an old global entity.
        _need(db.scalar(select(DomainInstance.id).where(
            DomainInstance.workspace_id == p.workspace_id,
            DomainInstance.namespace == entity.namespace).limit(1)) is None)
        _need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'instance.object.created', Audit.object_id == entity.id).limit(1)) is None)
    return False


def check(db, p, observation):
    """None: verified legacy; waiting_data: no ACK; received: cold-checked ACK.

    This function never receives, repairs, retries, mutates a record, or calls
    a model/device. Physical/source-time truth is outside its guarantee.
    """
    _need(observation.workspace_id == p.workspace_id and observation.entity_id is not None)
    try:
        if not _required(db, p, observation):
            # Preserve existing specialized reception families and reject a
            # leftover modern receipt even on an otherwise legacy release.
            value = receipts.observation_receipt(db, p, observation)
            if value.get('data_reception') is not None:
                receipt = value['data_reception']
                _need(type(receipt) is dict and type(receipt.get('receipt_hash')) is str)
                return {'status': 'received', 'receipt_hash': receipt['receipt_hash']}
            return None
        event = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id,
            Event.idempotency_key == 'data:' + observation.id))
        _need(event is None or event.kind == 'observation.data')
        claimed = 'data_reception' in observation.data
        if event is not None:
            claimed = (claimed or event.status == 'received'
                or getattr(event, 'received_at', None) is not None or 'data_reception' in event.data)
        # A withdrawn Event flag cannot turn a persisted independent recipient
        # into a new pending result. The original verifier checks its payload.
        memory = db.scalar(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['observation_id'].as_string() == observation.id,
            Memory.data['type'].as_string() == 'observation_evidence').limit(1))
        if claimed or memory is not None:
            value = receipts.observation_receipt(db, p, observation)
            receipt = value.get('data_reception')
            _need(type(receipt) is dict and type(receipt.get('receipt_hash')) is str)
            return {'status': 'received', 'receipt_hash': receipt['receipt_hash']}
        _need(event is None or event.status in {'pending_delivery', 'blocked_delivery'})
        if event is not None:
            # A valid pending result is excluded honestly. A damaged payload
            # is not disguised as normal pending work; rebuild without ACKing.
            _need(receipts._receipt(db, p, event, observation) is not None)
        return {'status': 'waiting_data',
                'delivery_status': event.status if event is not None else 'not_delivered'}
    except HTTPException as error:
        if error.status_code not in {404, 422}:
            raise
        _need(False)
    except (ValueError, KeyError, TypeError, OSError, RecursionError, AttributeError):
        _need(False)

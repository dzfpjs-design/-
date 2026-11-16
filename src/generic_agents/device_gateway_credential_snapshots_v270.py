"""Shared private snapshots, backed by an independently issued Tool or Source.

An existing ciphertext row is not authority. Each family validates its own real
capability and issuance; no Source is represented as a Tool and no missing
issued snapshot is repaired. This module neither opens credentials nor does IO.
"""
import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection
from .device_gateway_tool_models_v269 import DeviceGatewayCredentialSnapshotV269 as Snapshot
from .models import Audit, Capability, uid

ERROR = '设备网关原凭据快照或独立签发依据无法核对，未发送请求'
TOOL_ISSUED = 'device.gateway_tool_issued_v269'
SOURCE_ISSUED = 'device.gateway_source_issued_v270'
ISSUED_ACTIONS = (TOOL_ISSUED, SOURCE_ISSUED)


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def cipher_hash(snapshot):
    need(type(snapshot.token_cipher) is str and bool(snapshot.token_cipher))
    return hashlib.sha256(snapshot.token_cipher.encode('utf-8')).hexdigest()


def _valid_issuance(db, p, proof, snapshot):
    """Check an actual family, never a caller-supplied validator or self hash."""
    prior = db.scalar(select(Capability).where(Capability.id == proof.object_id,
        Capability.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    if prior is None or type(prior.data) is not dict or prior.content_hash != digest(prior.data):
        return False
    try:
        if proof.action == TOOL_ISSUED:
            from . import device_gateway_tools_v269 as family
            binding = family._checked_binding(prior.data.get('specification', {}).get('device_gateway_binding'))
            need(prior.kind == 'tool' and prior.data == family._manifest(binding))
            actual = family._issued(db, p, prior, binding)
        elif proof.action == SOURCE_ISSUED:
            from . import device_gateway_sources_v270 as family
            binding = family.checked_binding(prior.data.get('specification', {}).get(family.BINDING_KEY))
            need(prior.kind == 'source' and prior.data == family.manifest(binding))
            actual = family.issued_source(db, p, prior, binding)
        else:
            return False
        need(prior.namespace == prior.data['namespace'] and prior.name == prior.data['name']
            and prior.release_version == prior.data['version'] and actual.id == snapshot.id)
        return True
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
        return False


def ensure_snapshot(db, p, connection):
    """Called under the gateway workspace lock, in the issuance transaction."""
    need(isinstance(connection, Connection) and connection.workspace_id == p.workspace_id)
    actual = db.scalar(select(Connection).where(Connection.id == connection.id,
        Connection.workspace_id == p.workspace_id).with_for_update()
        .execution_options(populate_existing=True))
    need(actual is not None and type(actual.credential_revision) is int and actual.credential_revision >= 1)
    row = db.scalar(select(Snapshot).where(Snapshot.workspace_id == p.workspace_id,
        Snapshot.connection_id == actual.id,
        Snapshot.credential_revision == actual.credential_revision).with_for_update()
        .execution_options(populate_existing=True))
    if row is None:
        remnants = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action.in_(ISSUED_ACTIONS),
            # Both families put the immutable gateway identity at this exact path.
            Audit.data['binding']['connection_id'].as_string() == actual.id,
            Audit.data['binding']['credential_revision'].as_integer() == actual.credential_revision).limit(1))
        need(remnants is None)
        row = Snapshot(id=uid(), workspace_id=p.workspace_id, connection_id=actual.id,
            credential_revision=actual.credential_revision, token_cipher=actual.token_cipher)
        cipher_hash(row)
        db.add(row); db.flush()
        return row
    need(row.token_cipher == actual.token_cipher)
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_(ISSUED_ACTIONS), Audit.data['snapshot_id'].as_string() == row.id)
        .execution_options(populate_existing=True)))
    need(any(_valid_issuance(db, p, proof, row) for proof in proofs))
    return row

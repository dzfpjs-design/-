"""Frozen, source-backed measurement units for newly ingested MQTT observations.

This records the packet's declaration or a confirmed source mapping; it never
asserts calibration, converts values, or modifies an existing observation.
"""
import copy
import re

from fastapi import HTTPException
from sqlalchemy import literal_column,select

from .contracts import digest
from .models import Audit

KEY = 'mqtt_unit_evidence_v342'
SCHEMA = 'mqtt-measurement-unit-evidence-v342'
ACTION = 'mqtt.observation.units_captured_v342'


def need(value):
    if not value:
        raise HTTPException(409, '原报文的测量单位或来源记录无法核对，原观测保留')


def build(binding, receipt, decoded, observation_id):
    """Build only from a packet already verified against its original bytes."""
    from .mqtt_observations import binding_hash
    need(type(observation_id) is str and bool(observation_id) and type(decoded) is dict and decoded.get('usable') is True)
    links=binding.data.get('field_links');configured=binding.data.get('field_units')
    attrs=decoded.get('attributes');origins=decoded.get('unit_origin')
    need(type(links) is dict and type(configured) is dict and type(attrs) is dict and type(origins) is dict
         and set(links)==set(attrs)==set(origins) and set(configured)==set(links.values())
         and len(set(links.values()))==len(links))
    need(receipt.input_sha256==decoded.get('input_sha256') and type(receipt.evidence_id) is str)
    fields={}
    for source_field,field in links.items():
        unit=configured[field];origin=origins[source_field]
        need(type(field) is str and bool(field) and type(unit) is str
             and origin in {'message','confirmed_source_mapping'})
        # The existing protocol accepts an empty unit. Preserve that reading as
        # unknown instead of inventing a unit or passing control characters on.
        if not unit.strip() or re.search(r'[\x00-\x1f\x7f]',unit):
            fields[field]={'unit':None,'basis':'unknown'}
        else:
            fields[field]={'unit':unit,'basis':origin}
    value={'schema':SCHEMA,'observation_id':observation_id,'receipt_id':receipt.id,
           'packet_evidence_id':receipt.evidence_id,'input_sha256':receipt.input_sha256,
           'binding_id':binding.id,'binding_hash':binding_hash(binding),'fields':fields}
    return {**value,'proof_hash':digest(value)}


def capture(db,p,observation,binding,receipt,decoded):
    need(KEY not in observation.data)
    proof=build(binding,receipt,decoded,observation.id)
    observation.data={**observation.data,KEY:proof}
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=observation.id,
                 data={'schema':SCHEMA,'observation_id':observation.id,'receipt_id':receipt.id,
                       'proof_hash':proof['proof_hash']}))
    return copy.deepcopy(proof)


def verify(db,p,observation,binding,receipt,decoded):
    """Legacy records remain unchanged; a missing/tampered new proof fails closed."""
    # This is a fixed SQL literal, not user input: PostgreSQL can use the
    # partial unique index even when the driver chooses a generic plan.
    rows=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==literal_column("'mqtt.observation.units_captured_v342'"),
        Audit.object_id==observation.id).limit(2)))
    marker=observation.data.get(KEY)
    if marker is None:
        need(not rows)
        return None
    expected=build(binding,receipt,decoded,observation.id)
    need(type(marker) is dict and marker==expected and len(rows)==1)
    need(rows[0].actor_id==observation.data.get('submitted_by') and rows[0].data=={'schema':SCHEMA,'observation_id':observation.id,'receipt_id':receipt.id,
                         'proof_hash':expected['proof_hash']})
    return copy.deepcopy(expected)

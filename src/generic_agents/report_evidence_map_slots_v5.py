"""Pure fixed-slot Map wire and lossless internal validation codec.

Eighty required closed slots bound the number of claims independently of an
array maxItems provider feature. Named RootModel slots share one schema
reference without adding a JSON wrapper. Slot holes are retained explicitly.

The V3 array is an INTERNAL validation view, never an original provider reply.
Parsed values plus a caller-supplied byte hash do not prove origin, permissions,
frozen source scope or truth. decode_arguments alone derives the hash from
original strict UTF-8 bytes. No IO, provider, budget, journal or grant occurs.
"""
import copy
import hashlib
import json
import re
from typing import Literal

from pydantic import RootModel, create_model

from .contracts import digest
from .report_full_reading_semantics import Closed
from .report_evidence_wire_v3 import (
    MapClaimWireV3, MapResultWireV3, MAX_WIRE_VALUE_BYTES,
    _wire as _validate_v3_view,
)
from .report_evidence_strict_map import _loads
from .report_evidence_ref_schema_v2 import compile_ref_schema


WIRE_SCHEMA = 'report-evidence-map-slots-v5'
CODEC = 'map-slots-to-v3-v1'
SLOT_NAMES = tuple(f'slot_{number:03d}' for number in range(1, 81))
_HASH = re.compile(r'^[a-f0-9]{64}$')


class AbsentSlot(Closed):
    state: Literal['absent']


class PresentSlot(Closed):
    state: Literal['present']
    claim: MapClaimWireV3


# A normal union deliberately exports anyOf, not discriminator/oneOf keywords
# that the existing strict-schema compiler does not support.
Slot = AbsentSlot | PresentSlot


class SlotValue(RootModel[Slot]):
    pass


Fixed80Slots = create_model('Fixed80Slots', __base__=Closed,
    **{name: (SlotValue, ...) for name in SLOT_NAMES})


class MapSlotsWireV5(Closed):
    schema: Literal['report-evidence-map-slots-v5']
    slots: Fixed80Slots


def slot_names():
    return SLOT_NAMES


def _bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8', errors='strict')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Map slots require safe original JSON') from None


def _wire(value):
    if type(value) is not dict:
        raise ValueError('Map slots require an original JSON object')
    result = MapSlotsWireV5.model_validate(value).model_dump()
    if _bytes(result) != _bytes(value):
        raise ValueError('Original slot wire changed during validation')
    if len(_bytes(result)) > MAX_WIRE_VALUE_BYTES:
        raise ValueError('Original slot wire exceeds the unchanged byte capacity')
    return result


def decode(wire, *, arguments_sha256):
    """Return original wire and a labelled V3 validation view, without repairs.

    slot_positions are zero-based positions in SLOT_NAMES. They are not IDs,
    source offsets or an assertion that every source fact was extracted.
    """
    if type(arguments_sha256) is not str or not _HASH.fullmatch(arguments_sha256):
        raise ValueError('Original arguments byte hash is required')
    original = _wire(wire)
    positions, claims = [], []
    for position, name in enumerate(SLOT_NAMES):
        slot = original['slots'][name]
        if slot['state'] == 'present':
            positions.append(position)
            claims.append(copy.deepcopy(slot['claim']))
    view = _validate_v3_view({'schema': 'report-evidence-map-v3', 'claims': claims}, MapResultWireV3)
    value = {'schema': CODEC, 'wire': copy.deepcopy(original),
        'arguments_sha256': arguments_sha256, 'wire_value_hash': digest(original),
        'view': view, 'view_hash': digest(view), 'slot_positions': positions}
    return {**value, 'codec_hash': digest(value)}


def encode(view, slot_positions):
    """Reverse only with explicit original positions; never guess slot holes."""
    value = _validate_v3_view(view, MapResultWireV3)
    if (type(slot_positions) is not list or len(slot_positions) != len(value['claims'])
            or any(type(position) is not int or not 0 <= position < 80 for position in slot_positions)
            or slot_positions != sorted(set(slot_positions))):
        raise ValueError('Exact increasing original slot positions are required')
    slots = {name: {'state': 'absent'} for name in SLOT_NAMES}
    for position, claim in zip(slot_positions, value['claims']):
        slots[SLOT_NAMES[position]] = {'state': 'present', 'claim': copy.deepcopy(claim)}
    return _wire({'schema': WIRE_SCHEMA, 'slots': slots})


def check(value, wire, *, arguments_sha256):
    """Cold rebuild from original wire, not from a self-resealed codec value."""
    expected = decode(wire, arguments_sha256=arguments_sha256)
    if type(value) is not dict or _bytes(value) != _bytes(expected):
        raise ValueError('Codec does not rebuild from the original slot wire')
    if encode(expected['view'], expected['slot_positions']) != expected['wire']:
        raise ValueError('Codec inverse differs from the original slot wire')
    return expected


def decode_arguments(raw):
    """Parse original bytes; duplicate keys and invalid Unicode never disappear."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_WIRE_VALUE_BYTES:
        raise ValueError('Original arguments bytes are missing or exceed capacity')
    try:
        wire = _loads(raw.decode('utf-8', errors='strict'))
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('Original arguments are not strict UTF-8 JSON') from None
    return decode(wire, arguments_sha256=hashlib.sha256(raw).hexdigest())


def schema_proof():
    """Compile the exact local schema with the fixed reference-preserving codec."""
    return compile_ref_schema(MapSlotsWireV5.model_json_schema())


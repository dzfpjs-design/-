"""Read one independently received original observation, never an object cache.

The route's SQL occurrence and audit are the authority. A bare observation_id,
Task JSON marker, delivery flag or a reconstructed receipt grants no read.
"""
import copy
from functools import wraps

from fastapi import HTTPException

from .contracts import digest
from .models import Observation
from .services import scoped

KEY = 'signal_approval_origin_v247'
RECORD = 'received-observation-record-v247'


def need(value):
    if not value:
        raise HTTPException(409, '本次异常的原观测与独立收件依据无法核对；未采用对象档案或其它观测替代')


def closed(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException as error:
            if error.status_code not in {404, 422}:
                raise
            need(False)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError,
                OverflowError, RecursionError):
            need(False)
    return call


def verify_task(db, p, task, *, lock_sources=False):
    # This checker also reverse-queries the independent occurrence. Removing
    # the Task marker must not select the old unversioned observation reader.
    from .signal_approval_routing_v247 import checked_task_origin
    return checked_task_origin(db, p, task, lock_sources=lock_sources)


@closed
def records(db, p, task):
    origin = verify_task(db, p, task)
    if origin is None:
        return None
    from .signal_graph_http_source_v177 import checked_contract, guard
    from .instance_data_receipts import observation_receipt
    from .perception_signals import checked
    from .models import PerceptionSignal
    contract = checked_contract(origin['source_contract'])
    observation = scoped(db, Observation, origin['observation_id'], p)
    signal = scoped(db, PerceptionSignal, origin['signal_id'], p)
    # This is a historical input reader as well as a bind provider. The
    # execution entry separately holds the same sources through its final
    # check; reading details must not request locks in a read-only snapshot.
    basis = checked(db, p, signal, allow_rejected=False, lock_sources=False)
    need(task.workspace_id == p.workspace_id == observation.workspace_id
         and task.data['instance_id'] == contract['scope']['instance_id']
         and task.data['instance_release_id'] == contract['scope']['release_id']
         and task.data['entity_ids'] == [observation.entity_id]
         and signal.instance_id == task.data['instance_id']
         and signal.entity_id == observation.entity_id
         and signal.data['basis_hash'] == origin['basis_hash']
         and basis['observation_id'] == observation.id
         and observation.data['input_hash'] == origin['input_hash'])
    extracted = basis['extracted']
    proof = guard(db, p, contract, observation, result=extracted,
                  expected_origin=basis['simulation_origin'])
    receipt = observation_receipt(db, p, observation).get('data_reception')
    need(type(receipt) is dict and receipt['receipt_hash'] == origin['receipt_hash']
         and receipt['observation_id'] == observation.id
         and receipt['instance_id'] == task.data['instance_id']
         and receipt['release_id'] == task.data['instance_release_id']
         and receipt['extracted_hash'] == digest(extracted)
         and type(extracted) is dict and type(extracted.get('attributes')) is dict)
    attributes = extracted['attributes']
    need(bool(attributes) and set(attributes) <= set(contract['field_units']))
    # Preserve domain field keys and units. Titles and Entity.data are not
    # measurements. V177 supplier times remain explicitly unverified.
    return [{'schema': RECORD, 'observation_id': observation.id,
        'entity_id': observation.entity_id, 'version': observation.processing_version,
        'processing_version': observation.processing_version,
        'source_id': observation.source_id, 'source_record_id': observation.source_record_id,
        'attributes': copy.deepcopy(attributes), 'candidate_extraction': copy.deepcopy(extracted),
        'field_units': {key: contract['field_units'][key] for key in attributes},
        'reported_at': basis['reported_at'], 'source_clock_utc': 'unknown',
        'receipt_hash': receipt['receipt_hash'], 'not_authoritative': True,
        'simulation': proof['simulation'], 'test_only': True,
        'source_time_verified': False, 'physical_state_verified': False}]


def record_schema(contract):
    """Guaranteed structure only; future observations may contain fewer fields."""
    fields = copy.deepcopy(contract['mapping']['field_schemas'])
    properties = {
        'schema': {'const': RECORD}, 'observation_id': {'type': 'string'},
        'entity_id': {'type': 'string'}, 'version': {'type': 'string'},
        'processing_version': {'type': 'string'}, 'source_id': {'type': 'string'},
        'source_record_id': {'type': 'string'},
        'attributes': {'type': 'object', 'properties': fields, 'additionalProperties': False},
        'candidate_extraction': {'type': 'object'}, 'field_units': {'type': 'object'},
        'reported_at': {'type': ['string', 'null']}, 'source_clock_utc': {'const': 'unknown'},
        'receipt_hash': {'type': 'string'}, 'not_authoritative': {'const': True},
        'simulation': {'const': True}, 'test_only': {'const': True},
        'source_time_verified': {'const': False}, 'physical_state_verified': {'const': False}}
    return {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {
        'type': 'object', 'properties': properties, 'required': list(properties),
        'additionalProperties': False}}

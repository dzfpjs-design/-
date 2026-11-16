"""Explicit mapped-HTTP provenance for the existing finite graph router.

This admits a saved, isolated SIM observation, not a device command.  Preparing
a route checks current registrations; checking a saved receipt deliberately
does not impersonate the original processing worker or renew its authority.
"""
import copy
from datetime import datetime
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from . import http_observation_mapping_v177 as mapping
from . import http_observation_exchange_v177 as ledger
from . import http_observation_transport_v177 as transport
from .models import Audit, Capability, Entity
from .services import published, scoped
from .simulation_models_v176 import SimulationEnvironmentV176 as Environment

CONTRACT_KEY = 'http_mapped_source_contract_v177'
CONTRACT_SCHEMA = 'signal-graph-http-source-contract-v177'
FAMILY = 'http-mapped-acquisition-v177'
_KEYS = {'schema', 'family', 'scope', 'source_id', 'source_hash', 'extractor_id',
    'extractor_hash', 'environment_hash', 'configuration_hash', 'mapping',
    'field_units', 'simulation', 'test_only', 'field_verified'}
ERROR = 'HTTP异常的原映射、独立回执或明确授权范围无法核对；原数据保留，未重新采集或派单'


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def closed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError,
                OverflowError, RecursionError, UnicodeError):
            raise HTTPException(409, ERROR) from None
    return checked


def same(left, right):
    return mapping.canonical(left) == mapping.canonical(right)


@closed
def configured(source):
    """A real registered mapping selects the family; labels never do so."""
    specification = source.data['specification']
    need(type(specification) is dict)
    if transport.MAPPING_KEY not in specification:
        return False
    need(source.kind == 'source' and source.data['execution']['adapter'] == 'http')
    return True


@closed
def checked_contract(value):
    need(type(value) is dict and set(value) == _KEYS
        and value['schema'] == CONTRACT_SCHEMA and value['family'] == FAMILY
        and value['simulation'] is True and value['test_only'] is True
        and value['field_verified'] is False)
    mapping._frozen(value['mapping'])
    mapping._scope(value['scope'])
    mapping._uuid(value['source_id']); mapping._uuid(value['extractor_id'])
    for key in ('source_hash', 'extractor_hash', 'environment_hash', 'configuration_hash'):
        mapping._hash(value[key])
    frozen = value['mapping']
    need(same(value['scope'], frozen['mapping']['scope'])
        and value['source_id'] == frozen['source_id']
        and value['source_hash'] == frozen['source_manifest_hash']
        and same(value['field_units'], {link['field_key']: link['unit']
            for link in frozen['mapping']['links'].values()}))
    return copy.deepcopy(value)


@closed
def prepare(db, p, instance, release, source, extractor, entity_ids):
    """Freeze only actual current registrations, without dispatching HTTP."""
    need(configured(source))
    source = published(db, p, source.id, 'source')
    configured_mapping = source.data['specification'][transport.MAPPING_KEY]
    mapping._config(configured_mapping)
    configured_scope = configured_mapping['scope']
    from .simulation_loop_v176 import environment
    env, current, selected = environment(db, p, configured_scope['environment_id'], live=True)
    need(current.id == instance.id and selected.id == release.id
        and env.workspace_id == p.workspace_id and release.instance_id == instance.id
        and release.package_id == configured_scope['package_id'])
    package = published(db, p, release.package_id, 'domain_package')
    need(package.namespace == instance.namespace)
    scope = {**configured_scope, 'workspace_id': p.workspace_id,
        'environment_id': env.id, 'instance_id': instance.id, 'release_id': release.id,
        'package_id': package.id, 'package_hash': package.content_hash, 'source_id': source.id}
    frozen = mapping.freeze_mapping(source.data, scope,
        package.data['specification']['entity_schemas'], configured_mapping['links'])
    selected_extractor = transport._extractor(db, p, source, frozen)
    need(selected_extractor.id == extractor.id
        and selected_extractor.content_hash == extractor.content_hash)
    need(type(entity_ids) is list and len(entity_ids) == len(set(entity_ids)))
    for identity in entity_ids:
        entity = scoped(db, Entity, identity, p)
        need(entity.namespace == instance.namespace and entity.schema_capability_id == package.id
            and entity.entity_type == scope['entity_type'])
    value = {'schema': CONTRACT_SCHEMA, 'family': FAMILY,
        'scope': copy.deepcopy(frozen['mapping']['scope']),
        'source_id': source.id, 'source_hash': source.content_hash,
        'extractor_id': selected_extractor.id, 'extractor_hash': selected_extractor.content_hash,
        'environment_hash': mapping.digest(env.data), 'configuration_hash': release.configuration_hash,
        'mapping': frozen, 'field_units': {link['field_key']: link['unit']
            for link in frozen['mapping']['links'].values()},
        'simulation': True, 'test_only': True, 'field_verified': False}
    return checked_contract(value)


def options(db, p, instance, release):
    """Extend graph-only choices; invalid or other-instance sources stay hidden."""
    from .signal_routing import cap_ref, extractor_ref
    result = []
    query = select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.kind == 'source', Capability.status == 'published')
    for source in db.scalars(query):
        try:
            if not configured(source):
                continue
            extractor = published(db, p, source.data['specification'][transport.EXTRACTOR_ID], 'tool')
            prepare(db, p, instance, release, source, extractor, [])
            result.append({'source': cap_ref(source), 'extractor': extractor_ref(extractor)})
        except HTTPException as error:
            if error.status_code not in {404, 409, 422}:
                raise
            continue
    return result


@closed
def guard(db, p, contract, observation, *, result, expected_origin=None):
    """Historical source check only; current grants are checked by the router."""
    contract = checked_contract(contract)
    scope = contract['scope']
    need(scope['workspace_id'] == p.workspace_id and observation.workspace_id == p.workspace_id
        and observation.source_id == contract['source_id']
        and observation.data['extractor_id'] == contract['extractor_id'])
    from .mapped_acquisition_origin_v177 import guard as mapped_guard
    proof = mapped_guard(db, p, observation, execution=False, result=result)
    need(type(proof) is dict and proof.get('schema') == 'http-mapped-checked-origin-v177')
    need(all(proof[key] == scope[key] for key in ('environment_id', 'instance_id', 'release_id'))
        and proof['source_id'] == contract['source_id'] and proof['extractor_id'] == contract['extractor_id']
        and proof['entity_id'] == observation.entity_id
        and proof['mapping_hash'] == contract['mapping']['mapping_hash'])
    exchange = ledger.checked_store(db, p, proof['exchange_id'], lock=False)
    need(exchange.observation_id == observation.id
        and same(exchange.data['freeze']['mapping'], contract['mapping']))
    env = scoped(db, Environment, scope['environment_id'], p)
    need(mapping.digest(env.data) == contract['environment_hash']
        and env.data['configuration_hash'] == contract['configuration_hash'])
    source = scoped(db, Capability, contract['source_id'], p)
    extractor = scoped(db, Capability, contract['extractor_id'], p)
    need(source.content_hash == contract['source_hash'] == mapping.digest(source.data)
        and extractor.content_hash == contract['extractor_hash'] == mapping.digest(extractor.data))
    from .observation_integrity import checked_report
    report = checked_report(db, p, observation)
    need(same(report['extracted'], result))
    if expected_origin is not None:
        need(same(proof, expected_origin))
    return copy.deepcopy(proof)


def _instant(value):
    need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value


@closed
def registration_matches(db, p, grant, event, observation, basis, at):
    """Unrelated data is not an error; matched but damaged origins fail closed."""
    contract = checked_contract(grant['binding'][CONTRACT_KEY])
    if (event.kind != 'perception.anomaly' or basis.get('schema') != 'reported-anomaly-v1'
            or event.workspace_id != p.workspace_id or observation.workspace_id != p.workspace_id
            or observation.source_id != grant['source_id']
            or observation.data.get('extractor_id') != grant['extractor_id']
            or observation.entity_id not in grant['entity_ids']):
        return False
    scope = contract['scope']
    need(grant['workspace_id'] == p.workspace_id == scope['workspace_id']
        and grant['instance_id'] == scope['instance_id'] and grant['release_id'] == scope['release_id']
        and grant['source_id'] == contract['source_id'] and grant['extractor_id'] == contract['extractor_id'])
    original = observation.data['original_request']
    need(type(original) is dict and mapping.digest(original) == observation.data['input_hash']
        and all(original[key] == getattr(observation, key)
            for key in ('source_id', 'source_record_id', 'processing_version', 'entity_id'))
        and original['extractor_id'] == observation.data['extractor_id']
        and all(event.data[key] == getattr(observation, key)
            for key in ('source_id', 'entity_id'))
        and event.data['observation_id'] == observation.id
        and event.data['invocation_id'] == observation.data['invocation_id']
        and same(event.data['extracted'], basis['extracted'])
        and basis['event_hash'] == mapping.digest(event.data)
        and all(basis[key] == scope[key] for key in ('instance_id', 'release_id')))
    proof = guard(db, p, contract, observation, result=basis['extracted'],
        expected_origin=basis['simulation_origin'])
    need(same(event.data['simulation_origin'], proof)
        and event.data.get('simulation') is True and event.data.get('field_verified') is False
        and event.data.get('origin_kind') == 'simulated_equipment')
    # The independent exchange chain is checked above. Its earliest actual
    # receipt audit, not a later GET or supplier clock, prevents old-package
    # conversion from becoming a newly authorized automatic task.
    query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.object_id == proof['exchange_id'],
        Audit.data['operation'].as_string() == 'receipt.recorded')
    receipts = list(db.scalars(query))
    need(bool(receipts))
    receipt_at = min(_instant(row.created_at) for row in receipts)
    from .signal_routing import first_registration, stamp
    first = first_registration(db, observation)
    need(first is not None)
    return bool(stamp(grant['enabled_at']) <= receipt_at <= _instant(first)
        <= _instant(observation.created_at) <= _instant(event.created_at) <= _instant(at))

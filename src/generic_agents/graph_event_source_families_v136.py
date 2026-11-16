"""Closed source families; registration is never a model or Task grant.

The processing wire remains v1, byte for byte. A native AudioRun has no
extractor Invocation and must enter through its independent contract/record.
"""
import copy

from sqlalchemy import select

from .contracts import digest
from .models import Audit, Capability, Event, Membership, Observation, User

PROCESSING = 'processing'
NATIVE_AUDIO = 'native_audio'
NATIVE_LEARNED_GEO = 'native_learned_geo'
PROCESSING_CONTRACT = 'graph-event-observation-source-contract-v1'
NATIVE_CONTRACT = 'graph-event-native-audio-source-contract-v1'
PROCESSING_RECORD = 'graph-event-producer-record-v1'
NATIVE_RECORD = 'graph-event-native-audio-producer-record-v1'
NATIVE_COMMAND = 'graph-event-native-audio-producer-command-v1'
NATIVE_KEYS = {'schema', 'request_id', 'label', 'adapter_id', 'instance_id',
    'source_id', 'source_hash', 'installation_id', 'installation_hash'}
LEARNED_CONTRACT = 'graph-event-native-learned-geo-source-contract-v1'
LEARNED_RECORD = 'graph-event-native-learned-geo-producer-record-v1'
LEARNED_COMMAND = 'graph-event-native-learned-geo-producer-command-v1'
LEARNED_KEYS = {'schema', 'request_id', 'label', 'adapter_id', 'instance_id',
    'source_id', 'source_hash', 'capability_id', 'capability_hash'}


def contract_family(value):
    if type(value) is not dict:
        raise ValueError('source_family')
    if value.get('schema') == PROCESSING_CONTRACT: return PROCESSING
    if value.get('schema') == NATIVE_CONTRACT: return NATIVE_AUDIO
    if value.get('schema') == LEARNED_CONTRACT: return NATIVE_LEARNED_GEO
    raise ValueError('source_family')


def record_family(row):
    value = row.data if hasattr(row, 'data') else row
    if type(value) is not dict: raise ValueError('source_record_family')
    if value.get('schema') == PROCESSING_RECORD: expected = PROCESSING
    elif value.get('schema') == NATIVE_RECORD: expected = NATIVE_AUDIO
    elif value.get('schema') == LEARNED_RECORD: expected = NATIVE_LEARNED_GEO
    else: raise ValueError('source_record_family')
    if contract_family(value.get('contract')) != expected:
        raise ValueError('source_mixed_family')
    return expected


def _module(family):
    if family == PROCESSING:
        from . import graph_event_observation_sources as module
    elif family == NATIVE_AUDIO:
        from . import graph_event_native_audio_sources_v136 as module
    elif family == NATIVE_LEARNED_GEO:
        from . import graph_event_native_learned_geo_sources_v141 as module
    else: raise ValueError('source_family')
    return module


def check_contract(value):
    return _module(contract_family(value)).check_contract(value)


def registration_fields(value):
    checked = check_contract(value)
    return _module(contract_family(checked)).registration_fields(checked)


def check_projection(value, contract=None):
    if type(value) is not dict: raise ValueError('source_projection_family')
    schemas = {'graph-event-observation-projection-v1': PROCESSING,
        'graph-event-native-audio-projection-v1': NATIVE_AUDIO,
        'graph-event-native-learned-geo-projection-v1': NATIVE_LEARNED_GEO}
    family = schemas.get(value.get('schema'))
    if family is None or contract is not None and contract_family(contract) != family:
        raise ValueError('source_projection_family')
    return _module(family).check_projection(value, contract)


def projection_actor(projection, contract):
    check_projection(projection, contract)
    key = 'processing_actor_id' if contract_family(contract) == PROCESSING else 'receipt_actor_id'
    return projection['proof'][key]


def native_command_hash(p, command):
    # The exact parser also rejects silent UUID/text normalization. Lazy import
    # keeps model registration independent of FastAPI router initialization.
    from .graph_event_native_producer_api_v136 import Register
    typed = Register.model_validate(command).model_dump(mode='json')
    if digest(typed) != digest(command) or set(command) != NATIVE_KEYS:
        raise ValueError('native_registration_command')
    from .graph_event_admission import _uuid
    if not _uuid(p.user_id) or not _uuid(p.workspace_id):
        raise ValueError('native_registration_actor')
    return digest({'schema': 'graph-event-native-audio-producer-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'command': command})


def check_native_producer(row):
    from .graph_event_admission import need, _keys, _uuid, _integer, _text, _safe, _hash
    from .security import Principal
    family = record_family(row)
    need(family in {NATIVE_AUDIO, NATIVE_LEARNED_GEO} and _uuid(row.id) and _uuid(row.workspace_id)
        and _uuid(row.registered_by) and _uuid(row.request_id)
        and _integer(row.version) and row.status in {'active', 'revoked'})
    value = row.data
    need(_keys(value, {'schema', 'label', 'contract', 'contract_hash', 'registration'})
        and value['schema'] == (NATIVE_RECORD if family == NATIVE_AUDIO else LEARNED_RECORD)
        and _text(value['label']) and value['label'].strip())
    _safe(value); contract = check_contract(value['contract'])
    need(digest(contract) == digest(value['contract']) and _hash(row.contract_hash)
        and row.contract_hash == value['contract_hash'] == digest(contract))
    registration = value['registration']
    need(_keys(registration, {'request_id', 'actor_id', 'command_hash', 'command'})
        and registration['request_id'] == row.request_id
        and registration['actor_id'] == row.registered_by
        and registration['command_hash'] == row.command_hash and _hash(row.command_hash))
    command = registration['command']; pins = registration_fields(contract)
    native = family == NATIVE_AUDIO
    need(set(command) == (NATIVE_KEYS if native else LEARNED_KEYS)
        and command['schema'] == (NATIVE_COMMAND if native else LEARNED_COMMAND)
        and command['request_id'] == row.request_id and command['label'] == value['label']
        and set(pins) == ({'adapter_id', 'instance_id', 'source_id', 'source_hash', 'installation_id', 'installation_hash'}
            if native else {'adapter_id', 'instance_id', 'source_id', 'source_hash', 'capability_id', 'capability_hash'})
        and all(command[key] == item for key, item in pins.items()))
    p = Principal(row.registered_by, row.workspace_id, 'viewer', 'native-registration-history')
    need((native_command_hash if native else native_learned_command_hash)(p, command) == row.command_hash)
    return copy.deepcopy(contract)


def project_source(db, p, producer, event, observation, *, live=True, lock=True):
    family = record_family(producer)
    if family in {NATIVE_AUDIO, NATIVE_LEARNED_GEO}: verify_native_registration(db, p, producer)
    return _module(family).project_source(db, p, producer, event, observation, live=live, lock=lock)


def verify_native_registration(db, p, producer):
    contract = check_native_producer(producer)
    action = ('graph_event.native_producer_registered_v136' if record_family(producer) == NATIVE_AUDIO
        else 'graph_event.native_learned_geo_producer_registered_v141')
    records = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == producer.id).limit(2)))
    if (producer.workspace_id != p.workspace_id or len(records) != 1
        or records[0].actor_id != producer.registered_by
        or digest(records[0].data) != digest({'instance_id': contract['instance_id'],
            'request_id': producer.request_id, 'command_hash': producer.command_hash, 'contract_hash': producer.contract_hash})):
        raise ValueError('native_registration_audit')
    return contract


def verify_history(db, p, producer, frozen):
    if record_family(producer) == PROCESSING: return
    from .services import scoped
    event = scoped(db, Event, frozen['source_event_id'], p)
    observation = scoped(db, Observation, frozen['observation_id'], p)
    rebuilt = project_source(db, p, producer, event, observation, live=False, lock=False)
    if digest(rebuilt) != digest(frozen['projection']): raise ValueError('native_source_history_changed')


def _operator(db, workspace_id, actor_id, *, lock=False):
    query = select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == actor_id, User.active.is_(True), Membership.workspace_id == workspace_id,
        Membership.role.in_(['admin', 'operator']))
    if lock: query = query.with_for_update(read=True, of=(User, Membership))
    if db.scalar(query) is None: raise ValueError('native_source_registrar_unavailable')


def verify_contract_current(db, p, contract, *, producer=None, lock=False):
    """Current pins only; callers separately prove their Task/instance scope."""
    contract = check_contract(contract)
    family = contract_family(contract)
    if family in {NATIVE_AUDIO, NATIVE_LEARNED_GEO}:
        if producer is not None:
            if record_family(producer) != family: raise ValueError('source_mixed_family')
            verify_native_registration(db, p, producer)
            _operator(db, p.workspace_id, producer.registered_by, lock=lock)
        return _module(family).verify_contract_current(db, p, contract, lock=lock)
    from .graph_event_observation_sources import _cap
    caps = {}
    for identity in sorted({contract['source_id'], contract['extractor_id']}):
        query = select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.id == identity)
        if lock: query = query.with_for_update(read=True)
        row = db.scalar(query.execution_options(populate_existing=True))
        if row is None: raise ValueError('source_capability_missing')
        caps[identity] = row
    source, extractor = caps[contract['source_id']], caps[contract['extractor_id']]
    if getattr(source, 'namespace', None) == 'platform_native_receipts': raise ValueError('native_source_requires_native_family')
    _cap(source, kind='source'); _cap(extractor)
    if (extractor.kind not in {'tool', 'specialist_model', 'foundation_model'}
        or type(extractor.data.get('allowed_agent_roles')) is not list
        or 'perception' not in extractor.data['allowed_agent_roles']): raise ValueError('source_extractor_role')
    for prefix, row in (('source', source), ('extractor', extractor)):
        if row.content_hash != contract[prefix + '_hash'] or row.release_version != contract[prefix + '_release_version']:
            raise ValueError('source_capability_pin')
    if digest(extractor.data['output_schema']) != digest(contract['result_schema']):
        raise ValueError('source_result_schema')
    return copy.deepcopy(contract)


def require_instance(contract, instance_id):
    if contract_family(contract) in {NATIVE_AUDIO, NATIVE_LEARNED_GEO} and contract['instance_id'] != instance_id:
        raise ValueError('native_source_instance_scope')


def native_learned_command_hash(p, command):
    from .graph_event_native_learned_geo_producer_api_v141 import Register
    from .graph_event_admission import _uuid
    typed = Register.model_validate(command).model_dump(mode='json')
    if (set(command) != LEARNED_KEYS or digest(typed) != digest(command)
        or not _uuid(p.user_id) or not _uuid(p.workspace_id)):
        raise ValueError('native_learned_registration_command')
    return digest({'schema': 'graph-event-native-learned-geo-producer-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'command': command})

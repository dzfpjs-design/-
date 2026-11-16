"""Cold route identity for an existing original submission, never execution."""
from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import Audit, Entity, Job
from .services import scoped

REPORTED = {'schema', 'instance_id', 'release_id', 'entity_id', 'entity_version', 'source_hash', 'extractor_hash'}
REPORTED_LINEAGE = {'entity_source_package_id', 'entity_definition_hash'}
MQTT = {'mqtt_receipt_id', 'mqtt_binding_id', 'mqtt_binding_hash'}
LOCAL = {'candidate_only', 'local_capability_hash', 'local_source_hash', 'local_original_sha256', 'date_basis'}


def _need(value):
    if not value:
        raise HTTPException(409, '原观测的登记路径或原处理依据不一致；原记录保留，未重新处理')


def _same(left, right):
    return digest(left) == digest(right)


def _validate(db, p, row, *, reported_contract, mqtt, local):
    data = row.data
    _need(row.workspace_id == p.workspace_id and isinstance(data, dict))
    _need(('reported_rule_contract' in data) == (reported_contract is not None))
    if reported_contract is not None:
        saved = data['reported_rule_contract']
        _need(isinstance(saved, dict) and isinstance(reported_contract, dict))
        _need(set(saved) in (REPORTED, REPORTED | REPORTED_LINEAGE)
            and set(saved) == set(reported_contract)
            and saved['schema'] == 'local-reported-rule-v1'
            and type(saved['entity_version']) is int and saved['entity_version'] >= 1)
        # Entity attributes may legitimately advance after the original submit.
        # Keep the original epoch; compare stable route and definition identity.
        _need(all(_same(saved[key], reported_contract[key]) for key in set(saved) - {'entity_version'}))
        entity = scoped(db, Entity, row.entity_id, p)
        _need(saved['entity_id'] == row.entity_id and saved['entity_version'] <= entity.version)
    _need(bool(MQTT & set(data)) == (mqtt is not None))
    if mqtt is not None:
        _need(isinstance(mqtt, dict) and set(mqtt) == MQTT and MQTT.issubset(data)
            and all(_same(data[key], value) for key, value in mqtt.items())
            and data.get('source_time_verified') is False)
    _need(bool((LOCAL - {'date_basis'}) & set(data)) == (local is not None))
    if local is not None:
        _need(isinstance(local, dict) and set(local) in (LOCAL, LOCAL | {'local_instance'})
            and local['candidate_only'] is True
            and all(key in data and _same(data[key], value) for key, value in local.items())
            and ('local_instance' in data) == ('local_instance' in local))
    else:
        _need('local_instance' not in data)
    kind = ('process_mqtt_observation' if mqtt is not None else
        'process_reported_observation' if reported_contract is not None else
        'process_local_observation' if local is not None else 'process_observation')
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == 'extract:' + row.id).execution_options(populate_existing=True)))
    _need(len(jobs) == 1 and jobs[0].kind == kind
        and jobs[0].data.get('observation_id') == row.id
        and jobs[0].data.get('extractor_id') == data['extractor_id'])
    # A legitimate retry can change Job.actor_id.  The original intake audit,
    # rather than that mutable current actor, pins the original submission.
    issued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == 'observation.ingest')))
    _need(len(issued) == 1 and issued[0].actor_id == data['submitted_by'])
    return row


def validate(db, p, row, *, reported_contract, mqtt, local):
    try:
        return _validate(db, p, row, reported_contract=reported_contract, mqtt=mqtt, local=local)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OSError, RecursionError):
        raise HTTPException(409, '原观测的登记路径或原处理依据无法核对；原记录保留，未重新处理') from None

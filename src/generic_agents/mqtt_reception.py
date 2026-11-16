"""Read-only, scoped receiver health. Authorization is never a connection proof."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from .config import settings
from .contracts import digest
from .models import Membership, MqttGateway, MqttReceiverState, User, now
from .mqtt_inbox import validate_configuration
from .services import scoped


STATES = {'connecting', 'subscribing', 'receiving', 'backoff', 'blocked', 'stopped', 'standby'}
LIVE_STATES = {'connecting', 'subscribing', 'receiving', 'backoff'}
ERRORS = {'broker_unreachable', 'broker_rejected', 'subscription_rejected', 'credentials_missing',
          'permission_changed', 'configuration_changed', 'storage_failed', 'lease_lost',
          'connection_lost', 'capacity_wait', 'stopped', 'ack_failed', 'packet_rejected'}
ALIASES = {'owner_unavailable': 'permission_changed', 'owner_permission_removed': 'permission_changed',
           'broker_not_allowed': 'broker_rejected', 'configuration_invalid': 'configuration_changed',
           'receiver_fenced': 'lease_lost', 'gateway_paused': 'stopped', 'gateway_missing': 'stopped'}


def _error(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return 'receiver_unavailable'
    return value if value in ERRORS else ALIASES.get(value, 'receiver_unavailable')


def _time(value):
    try:
        timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            return None
        return timestamp.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _iso(value):
    timestamp = _time(value)
    return timestamp.isoformat() if timestamp else None


def _history(data):
    history = data.get('history')
    if not isinstance(history, list):
        return []
    result = []
    for item in history[-20:]:
        if (not isinstance(item, dict) or not isinstance(item.get('state'), str)
                or item.get('state') not in STATES or not _time(item.get('at'))):
            continue
        version = item.get('gateway_version')
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            continue
        result.append({'at': _iso(item['at']), 'state': item['state'],
                       'error_code': _error(item.get('error_code')), 'gateway_version': version})
    return result


def reception_view(db, p, identifier, *, timestamp=None):
    gateway = scoped(db, MqttGateway, identifier, p)
    timestamp = timestamp or now()
    ttl = settings().mqtt_receiver_lease_seconds
    report = db.scalar(select(MqttReceiverState).where(MqttReceiverState.workspace_id == p.workspace_id,
                                                     MqttReceiverState.gateway_id == gateway.id))
    data = report.data if report and isinstance(report.data, dict) else {}
    heartbeat = _time(report.heartbeat_at) if report else None
    age = (timestamp - heartbeat).total_seconds() if heartbeat else None
    fresh = age is not None and 0 <= age <= ttl
    lease = bool(report and report.lease_token and report.lease_until and report.lease_until > timestamp)
    matches = bool(report and report.gateway_version == gateway.version and report.config_hash == digest(gateway.data)
                   and data.get('schema') == 'mqtt-receiver-v1')
    state, error, authoritative_block = 'waiting_receiver', None, False
    if gateway.status != 'active':
        state = 'paused'
        fresh = False
    else:
        active = db.scalar(select(User.active).where(User.id == gateway.owner_id))
        role = db.scalar(select(Membership.role).where(Membership.user_id == gateway.owner_id,
                                                     Membership.workspace_id == gateway.workspace_id))
        if not active or role not in {'admin', 'operator'}:
            state, error, fresh = 'blocked', 'permission_changed', False
            authoritative_block = True
        else:
            try:
                validate_configuration(gateway.data,workspace_id=p.workspace_id,db=db)
            except HTTPException as failure:
                state, error, fresh = 'blocked', ('broker_rejected' if failure.status_code == 403
                                                 else 'configuration_changed'), False
                authoritative_block = True
            else:
                if report and not matches:
                    error, fresh = 'configuration_changed', False
                elif report:
                    state = report.status if report.status in STATES else 'stale'
                    error = _error(data.get('error_code'))
                    fresh = fresh and (lease if state in LIVE_STATES else True)
                    if not fresh:
                        state = 'stale'
    attempt = data.get('attempt', 0)
    return {'gateway_id': gateway.id, 'gateway_version': gateway.version,
            'configuration_hash': digest(gateway.data), 'authorization_state': gateway.status,
            'state': state, 'heartbeat_fresh': fresh and matches,
            'authoritative_block': authoritative_block,
            'server_time': timestamp.isoformat(), 'heartbeat_ttl_seconds': ttl,
            'last_reported_at': _iso(heartbeat), 'last_connected_at': _iso(data.get('connected_at')),
            'last_received_at': _iso(data.get('last_received_at')),
            'next_retry_at': _iso(data.get('next_retry_at')) if state == 'backoff' else None,
            'attempts': attempt if isinstance(attempt, int) and not isinstance(attempt, bool) and 0 <= attempt <= 1000000 else 0,
            'error_code': error, 'history': _history(data), 'history_limit': 20}

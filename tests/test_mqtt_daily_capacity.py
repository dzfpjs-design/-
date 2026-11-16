"""Controlled 100k *index* capacity; deliberately not 100k sensor/file inferences."""
from copy import deepcopy
from datetime import timedelta, timezone
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import MqttDailyChange, MqttDailyEntry, MqttDailyLedger, MqttReceipt, uid
from generic_agents.mqtt_daily import COUNTERS, _change_hash, _cursor, _scope, _write, checked_change, records, summary
from tests.test_mqtt_inbox import gateway, packet, save

def test_actual_initial_capture_hash_survives_database_timezone_reload(gateway):
    receipt = save(gateway, packet(record_id='timezone-hash-seed'))
    p, gateway_id, _ = gateway
    with Session(engine()) as db:
        ledger = db.scalar(select(MqttDailyLedger).where(MqttDailyLedger.gateway_id == gateway_id))
        frozen = db.scalar(select(MqttDailyChange).where(MqttDailyChange.ledger_id == ledger.id))
        utc_variant = SimpleNamespace(**{key: getattr(frozen, key) for key in (
            'id', 'workspace_id', 'ledger_id', 'entry_id', 'revision', 'item_key', 'kind',
            'status', 'instance_id', 'receipt_id', 'data')},
            item_created_at=frozen.item_created_at.astimezone(timezone.utc))
        assert frozen.content_hash == _change_hash(frozen), {
            'database_timezone': str(frozen.item_created_at.tzinfo),
            'utc_variant_matches': frozen.content_hash == _change_hash(utc_variant)}
        value = summary(db, p, ledger.id)
        page = records(db, p, ledger.id, value['revision'])
        assert value['counts']['raw_total'] == 1 and page['total'] == 1
        assert page['records'][0]['receipt_id'] == receipt.id


@pytest.mark.skipif(os.getenv('GA_MQTT_DAILY_CAPACITY') != '1',
    reason='Controlled 100k index fixture requires explicit disposable-database opt-in')


def test_controlled_hundred_thousand_index_pages_and_single_change(gateway):
    assert engine().url.database.startswith('ga_test_')
    p, gateway_id, version = gateway
    original = save(gateway, packet(record_id='capacity-real-seed'))
    began = time.monotonic()
    with Session(engine()) as db:
        ledger = db.scalar(select(MqttDailyLedger).where(MqttDailyLedger.gateway_id == gateway_id))
        seed = db.scalar(select(MqttDailyChange).where(MqttDailyChange.ledger_id == ledger.id))
        assert ledger.revision == 1
        seed_created = seed.item_created_at
        counts, prior_hash = dict(seed.data['counts']), seed.content_hash
        seed_item = deepcopy(seed.data['item'])
        receipt_rows, entry_rows, change_rows = [], [], []
        tail_created, tail_key = None, None
        for index in range(1, 100000):
            receipt_id, entry_id, change_id = uid(), uid(), uid()
            created = seed_created + timedelta(microseconds=index)
            item = {**deepcopy(seed_item), 'item_id': 'capacity-index:' + receipt_id,
                'id': receipt_id, 'receipt_id': receipt_id, 'record_label': 'Unverified capacity index ' + str(index),
                'status': 'blocked', 'data_received': False, 'needs_review': True,
                'capacity_fixture': 'structural_index_only_no_payload_verification',
                'issues': ['Synthetic index; original payload deliberately not verified']}
            counts = {**counts, 'raw_total': counts['raw_total'] + 1,
                'data_blocked': counts['data_blocked'] + 1,
                'raw_needs_review': counts['raw_needs_review'] + 1}
            receipt_rows.append({'id': receipt_id, 'workspace_id': p.workspace_id,
                'gateway_id': gateway_id, 'evidence_id': original.evidence_id,
                'packet_key': digest({'capacity_index_only': index}), 'source_record_id': 'capacity-' + str(index),
                'input_sha256': original.input_sha256, 'status': 'quarantined',
                'data': {'capacity_fixture': 'structural_index_only_no_payload_verification'},
                'created_at': created})
            change = MqttDailyChange(id=change_id, workspace_id=p.workspace_id, ledger_id=ledger.id,
                entry_id=entry_id, revision=index + 1, item_key=item['item_id'], item_created_at=created,
                kind='original', status='blocked', instance_id=None, receipt_id=receipt_id,
                data={'schema': 'mqtt-daily-change-v2', 'scope': _scope(ledger), 'item': item,
                    'before_hash': None, 'previous_hash': prior_hash, 'counts': counts,
                    'changed_at': created.isoformat()})
            signature = _change_hash(change); prior_hash = signature
            entry_rows.append({'id': entry_id, 'workspace_id': p.workspace_id, 'ledger_id': ledger.id,
                'item_key': item['item_id'], 'current_revision': index + 1, 'current_hash': signature,
                'data': {'schema': 'mqtt-daily-entry-v2', 'item': item}, 'created_at': created})
            change_rows.append({'id': change.id, 'workspace_id': p.workspace_id, 'ledger_id': ledger.id,
                'entry_id': entry_id, 'revision': change.revision, 'item_key': change.item_key,
                'item_created_at': created, 'kind': 'original', 'status': 'blocked', 'instance_id': None,
                'receipt_id': receipt_id, 'content_hash': signature, 'data': change.data, 'created_at': created})
            if len(receipt_rows) == 1000 or index == 99999:
                db.execute(insert(MqttReceipt), receipt_rows)
                db.execute(insert(MqttDailyEntry), entry_rows)
                db.execute(insert(MqttDailyChange), change_rows)
                receipt_rows, entry_rows, change_rows = [], [], []
            tail_created, tail_key = created, item['item_id']
        ledger.revision = 100000
        ledger.data = {**ledger.data, 'counts': counts, 'change_id': change.id, 'change_hash': prior_hash}
        ledger_id = ledger.id; db.commit()
    seed_seconds = time.monotonic() - began
    samples, statements = {}, []
    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.split()[0].upper())
    event.listen(engine(), 'before_cursor_execute', record_statement)
    try:
        with Session(engine()) as db:
            for sample in db.scalars(select(MqttDailyChange).where(MqttDailyChange.ledger_id == ledger_id)
                .order_by(MqttDailyChange.item_created_at, MqttDailyChange.item_key).limit(3)):
                current_ledger = db.get(MqttDailyLedger, ledger_id)
                checks = {'hash': sample.content_hash == _change_hash(sample),
                    'item_key': sample.data['item'].get('item_id') == sample.item_key,
                    'kind': sample.data['item'].get('kind') == sample.kind,
                    'status': sample.data['item'].get('status') == sample.status,
                    'instance': sample.data['item'].get('instance_id') == sample.instance_id,
                    'receipt': sample.data['item'].get('receipt_id') == sample.receipt_id,
                    'scope': sample.data['scope'] == _scope(current_ledger)}
                assert all(checks.values()), {'revision': sample.revision, 'checks': checks}
                checked_change(sample, current_ledger)
            for label, operation in [
                ('summary', lambda: summary(db, p, ledger_id, revision=100000)),
                ('first_page', lambda: records(db, p, ledger_id, 100000, limit=100)),
            ]:
                start = time.monotonic(); before = len(statements); value = operation()
                samples[label] = {'seconds': round(time.monotonic() - start, 4),
                    'sql_statements': len(statements) - before}
                if label == 'summary': assert value['counts']['raw_total'] == 100000
                else: assert value['total'] == 100000 and len(value['records']) == 100
            first_page = value
            start = time.monotonic(); before = len(statements)
            second = records(db, p, ledger_id, 100000, after=first_page['next_cursor'], limit=100)
            assert second['total'] == 100000 and len(second['records']) == 100
            assert not ({row['receipt_id'] for row in first_page['records']} &
                {row['receipt_id'] for row in second['records']})
            samples['next_page'] = {'seconds': round(time.monotonic() - start, 4),
                'sql_statements': len(statements) - before}
            frozen = db.scalar(select(MqttDailyChange).where(MqttDailyChange.ledger_id == ledger_id,
                MqttDailyChange.revision == 100000))
            scope = {'workspace_id': p.workspace_id, 'ledger_id': ledger_id, 'revision': 100000,
                'revision_hash': frozen.content_hash, 'instance_id': None, 'kind': 'original', 'status': 'all'}
            # Known controlled-fixture position: measures the actual final-page query.
            penultimate = db.scalar(select(MqttDailyChange).where(MqttDailyChange.ledger_id == ledger_id,
                MqttDailyChange.revision == 99999))
            after = _cursor(scope, [penultimate.item_created_at.isoformat(), penultimate.item_key])
            start = time.monotonic(); before = len(statements)
            last = records(db, p, ledger_id, 100000, after=after, limit=100)
            assert last['total'] == 100000 and len(last['records']) == 1 and not last['next_cursor']
            assert last['records'][0]['receipt_id'] == change.receipt_id
            samples['last_page_known_position'] = {'seconds': round(time.monotonic() - start, 4),
                'sql_statements': len(statements) - before}
            original_item = deepcopy(seed_item); original_item['data_received'] = True
            original_item['status'] = 'received'
            start = time.monotonic(); before = len(statements)
            _write(db, p, gateway_id, '2026-10-11', original_item, seed_created)
            db.commit()
            samples['one_index_change'] = {'seconds': round(time.monotonic() - start, 4),
                'sql_statements': len(statements) - before}
            # Metadata/index mutation only; does not simulate data-agent delivery or model execution.
            assert summary(db, p, ledger_id)['counts']['raw_total'] == 100000
            old = records(db, p, ledger_id, 100000, limit=100)
            assert old['records'] == first_page['records']
            assert len(statements) < 100
    finally:
        event.remove(engine(), 'before_cursor_execute', record_statement)
    output = Path(__file__).resolve().parents[1] / 'docs/evidence/mqtt-daily-full-v09/capacity-index.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'fixture': 'controlled_100000_structural_index_rows',
        'not_real_sensor_capture': True, 'not_100000_original_file_checks': True,
        'not_model_inference': True, 'full_export_not_measured': True,
        'seed_seconds': round(seed_seconds, 3), 'measured_operations': samples,
        'old_revision_first_page_unchanged_after_current_index_change': True}, indent=2) + '\n')

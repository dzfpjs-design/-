"""Actual learned writer must reject residual new-family authority before writes.

Root alone executes these two cases. The imported fixture installs through the
public API and runs the actual local model; no installation/model/worker or
provenance check is substituted. Deleted Audits are restored from their exact
original column values only after the negative public request.
"""
import copy
import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.geo_daily import record
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents import native_learned_geo_pair_provenance_v138 as origin
from tests.test_native_learned_geo_reception_v138 import actual_learned, business, configured, rasters, pytestmark


def good(response, status=200):
    assert response.status_code == status, response.text[:800]
    return response.json()


def public_rows():
    """Hash every public table's complete stored rows, not only row counts."""
    with Session(engine()) as db:
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        names = list(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
        quote = db.get_bind().dialect.identifier_preparer.quote
        rows = [[name, sorted(db.scalars(text('SELECT row_to_json(t)::text FROM public.' + quote(name) + ' t')))]
            for name in names]
    encoded = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'counts': [[name, len(values)] for name, values in rows]}


def machine_hash(pair):
    # The same old packet hash surface, without changing any authority Audit.
    value = {column.name: getattr(pair, column.name) for column in pair.__table__.columns
        if column.name not in {'data', 'version', 'created_at'} and not column.info.get('typed_payload_internal')}
    value['data'] = {key: item for key, item in pair.data.items()
        if key not in {'review', 'review_draft', 'review_history', 'reviewed_at'}}
    return digest(value)


@pytest.mark.parametrize('remove_result', [False, True], ids=['input_missing_result_issued_retained', 'input_result_missing_issued_retained'])
def test_residual_learned_anchors_cannot_fall_back_to_the_old_writer(client, actual_learned, remove_result):
    value = actual_learned; p = value['principal']
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        context = origin.checked_observation(db, p, observation)
        original_pair_data = copy.deepcopy(context['pair'].data)
        original_pair_payload = {column.name: copy.deepcopy(getattr(context['pair'], column.key))
            for column in m.GeoPair.__table__.columns
            if column.name == 'data' or column.info.get('typed_payload_internal')}
        original_packet = copy.deepcopy(context['packet'])
        original_receipt = copy.deepcopy(observation_receipt(db, p, observation)['data_reception'])
        base = '/api/workspace/instances/' + context['release'].instance_id
        chosen = [context['input_audit']] + ([context['result_audit']] if remove_result else [])
        deleted = [{column.name: copy.deepcopy(getattr(audit, column.key)) for column in m.Audit.__table__.columns}
            for audit in chosen]
        deleted_ids = [audit.id for audit in chosen]
        issued_id = context['issued_audit'].id
        original_machine_hash = machine_hash(context['pair'])
        assert original_machine_hash == original_packet['source_machine_hash']
        assert len(context['artifacts']) == 8 and context['call'].status == context['job'].status == 'succeeded'
    original_rows = public_rows()
    try:
        with Session(engine()) as db:
            for identity in deleted_ids:
                db.delete(db.get(m.Audit, identity))
            pair = db.get(m.GeoPair, value['pair_id'])
            pair.data = {**pair.data, 'v138_writer_probe_padding': 'changed machine envelope; original inference unchanged'}
            assert pair.data['result_hash'] == original_pair_data['result_hash']
            assert pair.data['request_hash'] == original_pair_data['request_hash']
            assert machine_hash(pair) != original_machine_hash
            db.commit()
        with Session(engine()) as db:
            observation = db.get(m.Observation, value['observation_id'])
            assert db.get(m.Audit, issued_id) is not None
            assert origin.ISSUED in observation.data and observation.data['data_reception'] == original_receipt
            assert origin.has_issued(db, p, observation)
            assert not origin.input_audits(db, p, value['pair_id'])
            pair = db.get(m.GeoPair, value['pair_id'])
            # The old generic collection path still considers the actual
            # inputs/result/artifacts intact. This is an authority-inventory
            # fault, not a missing runtime or a fabricated failed model call.
            assert record(db, p, 'comparison', pair, force=True)['integrity_verified'] is True
        before_request = public_rows()
        preview = good(client.get(base + '/native-receipts'))
        response = client.post(base + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']})
        assert response.status_code == 409, response.text[:800]
        assert public_rows() == before_request, 'Refused collection must neither insert nor overwrite any public row'
    finally:
        with Session(engine()) as db:
            # Restore the exact pre-fault payload envelope as well as data;
            # assigning via ORM would legitimately generate a new typed marker
            # and hide whether the refused request changed the original row.
            db.execute(m.GeoPair.__table__.update().where(m.GeoPair.id == value['pair_id'])
                .values(**copy.deepcopy(original_pair_payload)))
            for saved in deleted:
                assert db.get(m.Audit, saved['id']) is None
                db.execute(m.Audit.__table__.insert().values(**saved))
            db.commit()
    assert public_rows() == original_rows
    with Session(engine()) as db:
        observation = db.get(m.Observation, value['observation_id'])
        restored = origin.checked_observation(db, p, observation)
        assert restored['packet'] == original_packet
        assert observation_receipt(db, p, observation)['data_reception'] == original_receipt
        assert restored['call'].status == restored['job'].status == 'succeeded'
    exported = good(client.get(base + '/native-receipts/' + value['observation_id'] + '/export'))
    assert exported['observation']['data_received'] and not exported['observation']['integrity_error']
    assert exported['cloud_calls'] == 0 and exported['entity_state_updated'] is False

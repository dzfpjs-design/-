"""Actual PG storage, transactional recovery and legacy upgrade boundaries."""
import copy
import json
import math
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import String, Text, create_engine, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, aliased, mapped_column

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Base, Memory, SetupRevision
from generic_agents.services import as_dict
from generic_agents.typed_payload_codec_v131 import canonical_json, LEGACY_MARKER, ERROR
from generic_agents.typed_payload_migration_v131 import migrate_typed_payloads
from generic_agents.typed_payload_orm_v131 import FORMAT, CANONICAL, instrument_model
from tests.test_vertical_slice import manifest


VALUE = {'negative_zero': -0.0, 'integer': 1, 'float': 1.0, 'large': 1e21,
         'integer_precision': 10**80 + 31, 'values': [True, False, None, '中文']}


def test_real_api_bad_insert_is_safe_conflict_and_leaves_no_partial_capability(client):
    body = manifest('tool', name='typed_bad_' + uuid4().hex,
        description='不可保存的合成字符\0private-marker')
    response = client.post('/api/capabilities', json=body)
    assert response.status_code == 409 and response.json() == {'detail': ERROR}
    assert 'private-marker' not in response.text
    listing = client.get('/api/capabilities')
    assert listing.status_code == 200
    assert not any(row['name'] == body['name'] for row in listing.json())


def test_real_configuration_type_only_save_and_core_revision_history_keep_exact_values(client):
    view = client.get('/api/setup/draft').json()
    history = []
    for number in [-0.0, 0.0, 1, 1.0, 1e21]:
        configuration = {'version': 1, 'resources': [{'id': 'synthetic-reading',
            'parameters': {'minimum': number}}], 'connections': []}
        previous = view['revision']
        response = client.put('/api/setup/draft', content=canonical_json({
            'revision': previous, 'configuration': configuration}),
            headers={'Content-Type': 'application/json'})
        assert response.status_code == 200, response.text
        view = response.json(); assert view['revision'] == previous + 1
        expected = canonical_json(number)
        assert canonical_json(view['configuration']['resources'][0]['parameters']['minimum']) == expected
        cold = client.get('/api/setup/draft').json()
        assert canonical_json(cold) == canonical_json(view)
        unchanged = client.put('/api/setup/draft', content=canonical_json({
            'revision': view['revision'], 'configuration': view['configuration']}),
            headers={'Content-Type': 'application/json'})
        assert unchanged.status_code == 200 and unchanged.json()['revision'] == view['revision']
        history.append((view['revision'], expected))
    with Session(engine()) as db:
        for revision, expected in history:
            payload = db.scalar(select(SetupRevision.data).where(
                SetupRevision.draft_id == view['id'], SetupRevision.version == revision))
            assert canonical_json(payload['resources'][0]['parameters']['minimum']) == expected


def exact(value, expected=VALUE):
    assert canonical_json(value) == canonical_json(expected)
    assert digest(value) == digest(expected)
    assert type(value['negative_zero']) is float and math.copysign(1, value['negative_zero']) < 0
    assert type(value['float']) is float and type(value['integer']) is int


def fields(db, identity):
    return db.execute(text('SELECT _ga_data_format, _ga_data_canonical, data::text FROM memory_items WHERE id=:id'), {'id': identity}).one()


@pytest.mark.parametrize('mode', ['orm', 'core', 'multivalues', 'executemany', 'returning'])
def test_real_insert_defaults_owner_binding_and_all_full_scalar_reads(account, mode):
    workspace = account[2]
    with Session(engine()) as db:
        rows = [dict(workspace_id=workspace, text='合成数值存储', data=copy.deepcopy(VALUE)) for _ in range(2)]
        if mode == 'orm':
            objects = [Memory(**row) for row in rows]; db.add_all(objects); db.flush()
            ids = [row.id for row in objects]
        elif mode == 'multivalues':
            ids = list(db.scalars(insert(Memory).values(rows).returning(Memory.id)))
        elif mode == 'executemany':
            rows = [dict(row, id=str(uuid4())) for row in rows]
            db.execute(insert(Memory), rows); ids = [row['id'] for row in rows]
        else:
            ids = []
            for row in rows:
                response = db.execute(insert(Memory).values(**row).returning(Memory.id, Memory.data)).one()
                ids.append(response[0]); exact(response[1])
        db.commit()
    assert len(set(ids)) == 2
    with Session(engine()) as db:
        for identity in ids:
            value = db.get(Memory, identity); exact(value.data)
            exact(db.scalar(select(Memory.data).where(Memory.id == identity)))
            alias = aliased(Memory)
            exact(db.scalar(select(alias.data.label('original')).where(alias.id == identity)))
            original = fields(db, identity)
            envelope = json.loads(original[1]); assert envelope['owner']['owner_id'] == identity
            assert envelope['owner']['workspace_id'] == workspace
            assert envelope['marker'] == original[0]
            assert envelope['canonical'] == canonical_json(VALUE)
            public = as_dict(value)
            assert FORMAT not in public and CANONICAL not in public
        assert db.scalar(select(Memory.id).where(Memory.id == ids[0], Memory.data['integer'].as_integer() == 1)) == ids[0]
        assert db.scalar(select(Memory.id).where(Memory.id == ids[0], Memory.data.contains({'values': [True]}))) == ids[0]


def test_conflict_loser_status_only_updates_and_rollback_preserve_original(account):
    with Session(engine()) as db:
        row = Memory(workspace_id=account[2], text='原始请求', data=copy.deepcopy(VALUE))
        db.add(row); db.commit(); identity = row.id; before = fields(db, identity)
        assert db.scalar(insert(Memory).values(id=identity, workspace_id=account[2], text='冲突请求', data={'bad': 1.0}).on_conflict_do_nothing(index_elements=['id']).returning(Memory.id)) is None
        row.status = 'trusted'; db.commit()
        assert fields(db, identity) == before
        db.execute(update(Memory).where(Memory.id == identity).values(status='candidate'))
        db.commit(); assert fields(db, identity) == before
        row.data = {**VALUE, 'float': 2.0}; db.flush(); db.rollback()
        assert fields(db, identity) == before; exact(db.get(Memory, identity).data)


@pytest.mark.parametrize('original,replacement', [(-0.0, 0.0), (1.0, 1), (1, 1.0), (0.0, -0.0)])
def test_type_only_and_signed_zero_updates_are_real_writes(account, original, replacement):
    with Session(engine()) as db:
        row = Memory(workspace_id=account[2], text='原始数值修改', data={'number': original})
        db.add(row); db.commit(); identity = row.id; before = fields(db, identity)
        row.data = {'number': replacement}; db.commit()
        after = fields(db, identity)
        assert after[0] != before[0] and after[1] != before[1]
    with Session(engine()) as db:
        assert canonical_json(db.get(Memory, identity).data) == canonical_json({'number': replacement})


def test_storage_pairing_cannot_be_removed_or_transplanted_and_bad_hash_cold_read_is_blocked(account):
    with Session(engine()) as db:
        rows = [Memory(workspace_id=account[2], text='所属行', data=copy.deepcopy(VALUE)) for _ in range(2)]
        db.add_all(rows); db.commit(); first, second = [row.id for row in rows]
        marker, canonical, _ = fields(db, first)
    for sql, params in [
        ('UPDATE memory_items SET _ga_data_format=:legacy,_ga_data_canonical=NULL WHERE id=:id', {'legacy': LEGACY_MARKER, 'id': first}),
        ('UPDATE memory_items SET _ga_data_canonical=NULL WHERE id=:id', {'id': first}),
        ('UPDATE memory_items SET _ga_data_canonical=:canonical,_ga_data_format=:marker WHERE id=:id', {'id': second, 'canonical': canonical, 'marker': marker}),
        ('UPDATE memory_items SET data=CAST(:payload AS jsonb) WHERE id=:id', {'id': first, 'payload': '{"other":1}'}),
        ('UPDATE memory_items SET data=jsonb_set(data,\'{integer}\',\'1.0\') WHERE id=:id', {'id': first}),
    ]:
        with pytest.raises(IntegrityError), engine().begin() as connection:
            connection.execute(text(sql), params)
    damaged = json.loads(canonical); damaged['payload_hash'] = '0' * 64
    with engine().begin() as connection:
        connection.execute(text('UPDATE memory_items SET _ga_data_canonical=:value WHERE id=:id'), {'id': first, 'value': canonical_json(damaged)})
    with Session(engine()) as db, pytest.raises(HTTPException) as caught:
        db.get(Memory, first)
    assert caught.value.status_code == 409 and caught.value.detail == ERROR


def test_existing_legacy_rows_are_marked_without_payload_or_hash_rewrite_then_explicitly_upgraded():
    class LegacyBase(DeclarativeBase):
        pass
    class Legacy(LegacyBase):
        __tablename__ = 'legacy_typed_probe_v131'
        id = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
        workspace_id = mapped_column(String, nullable=True)
        data = mapped_column(JSONB)
        status = mapped_column(Text)
    instrument_model(Legacy)
    try:
        with engine().begin() as connection:
            connection.execute(text('CREATE TABLE legacy_typed_probe_v131 (id TEXT PRIMARY KEY,workspace_id TEXT,data JSONB NOT NULL,status TEXT)'))
            connection.execute(text('INSERT INTO legacy_typed_probe_v131 VALUES (\'old\',NULL,CAST(:payload AS jsonb),\'old\')'), {'payload': '{"number":-0.0,"integer":1,"hash":"old-authority"}'})
            before = connection.scalar(text('SELECT data::text FROM legacy_typed_probe_v131'))
        # A real concurrent old writer holds a relation lock. An unsuccessful
        # controlled upgrade must roll back every additive change, then retry.
        limited = create_engine(engine().url, hide_parameters=True,
            connect_args={'options': '-c lock_timeout=150ms'})
        try:
            with engine().connect() as writer:
                transaction = writer.begin()
                writer.execute(text("UPDATE legacy_typed_probe_v131 SET status='held' WHERE id='old'"))
                with pytest.raises(OperationalError) as blocked:
                    migrate_typed_payloads(limited, LegacyBase.metadata)
                assert blocked.value.orig.sqlstate == '55P03'
                with engine().connect() as observer:
                    assert observer.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='legacy_typed_probe_v131' AND column_name LIKE '_ga_%'")) == 0
                    assert observer.scalar(text('SELECT data::text FROM legacy_typed_probe_v131')) == before
                transaction.rollback()
        finally:
            limited.dispose()
        migrate_typed_payloads(engine(), LegacyBase.metadata)
        migrate_typed_payloads(engine(), LegacyBase.metadata)
        with pytest.raises(IntegrityError), engine().begin() as connection:
            connection.execute(text('UPDATE legacy_typed_probe_v131 SET data=jsonb_set(data,\'{integer}\',\'1.0\') WHERE id=\'old\''))
        with Session(engine()) as db:
            old = db.get(Legacy, 'old'); assert old.data['hash'] == 'old-authority'
            assert getattr(old, FORMAT) == LEGACY_MARKER and getattr(old, CANONICAL) is None
            old.status = 'checked'; db.commit()
            assert getattr(old, FORMAT) == LEGACY_MARKER and getattr(old, CANONICAL) is None
            assert db.scalar(text('SELECT data::text FROM legacy_typed_probe_v131')) == before
            old.data = {'number': -0.0, 'hash': 'new-explicit-content'}; db.commit()
        with Session(engine()) as db:
            old = db.get(Legacy, 'old')
            assert getattr(old, FORMAT) != LEGACY_MARKER
            assert canonical_json(old.data) == '{"hash":"new-explicit-content","number":-0.0}'
        with pytest.raises(IntegrityError), engine().begin() as connection:
            connection.execute(text('INSERT INTO legacy_typed_probe_v131 VALUES (\'raw-old-writer\',NULL,\'{}\',\'new\',\'legacy-jsonb-v1\',NULL)'))
    finally:
        with engine().begin() as connection:
            connection.execute(text('DROP TABLE IF EXISTS legacy_typed_probe_v131'))

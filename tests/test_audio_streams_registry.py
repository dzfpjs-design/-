"""Real ORM row/column regressions, with explicit install/publish interface spies.

These tests verify registry mapping and invocation shape. They do not claim a
native install, valid ready-proof chain or PostgreSQL publication succeeded.
"""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from generic_agents import audio_streams as service, models as m
from generic_agents.contracts import digest
from generic_agents.local_sound import PACK, trace
from generic_agents.security import Principal


def installation(*, pack=PACK, data=None):
    return m.SpecialistInstallation(id=str(uuid4()), workspace_id=str(uuid4()), pack_id=pack,
        version=7, status='ready', data={'generation': 2, **(data or {})})


class CatalogDB:
    def __init__(self, row): self.row = row; self.queries = []
    def execute(self, query): return []  # no MediaChannels in this unit catalog
    def scalars(self, query):
        sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        self.queries.append(sql)
        assert 'specialist_installations.pack_id =' in sql
        assert "->> 'pack_id'" not in sql
        return [self.row]


def test_catalog_filters_actual_installation_pack_column_without_duplicate_json_field(monkeypatch):
    from generic_agents import local_sound
    row = installation(); assert 'pack_id' not in row.data
    p = Principal(str(uuid4()), row.workspace_id, 'admin', 'registry-column-test'); seen = []
    def checked(db, actor, selected, *, live=False):
        seen.append((actor, selected, live)); return {'proof': {}}
    monkeypatch.setattr(local_sound, 'checked_trace', checked)
    monkeypatch.setattr(service, '_registered_cap', lambda db, actor, value: None)
    db = CatalogDB(row); result = service.catalog(db, p)
    assert len(db.queries) == 1 and len(result['installations']) == 1
    selected = result['installations'][0]
    assert selected['id'] == row.id and selected['pack_id'] == row.pack_id == PACK
    assert selected['version'] == 7 and selected['generation'] == 2 and selected['can_register'] is True
    assert selected['model_trace'] == trace(row) and seen == [(p, trace(row), True)]


def registry_interfaces(monkeypatch, row):
    from generic_agents import services, relations, local_sound
    p = Principal(str(uuid4()), row.workspace_id, 'admin', 'registry-column-test')
    checked = []; published = []; audits = []
    monkeypatch.setattr(service, 'model_registration', lambda *args: None)
    monkeypatch.setattr(service, '_nonce', lambda *args: None)
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: None)
    def scoped(db, cls, identity, actor, lock=False):
        assert cls is m.SpecialistInstallation and identity == row.id and actor is p and lock is True
        return row
    monkeypatch.setattr(services, 'scoped', scoped)
    def verify(db, actor, selected, *, live=False):
        checked.append((selected, live)); return {'proof': {}}
    monkeypatch.setattr(local_sound, 'checked_trace', verify)
    def register(db, actor, value):
        published.append(value); data = value.model_dump(mode='json')
        return m.Capability(id=str(uuid4()), workspace_id=p.workspace_id, version=3,
            namespace=value.namespace, name=value.name, release_version=value.version, kind=value.kind,
            status='published', content_hash=digest(data), validated_hash=digest(data), data=data)
    monkeypatch.setattr(service, '_register', register)
    monkeypatch.setattr(service, '_audit', lambda db, actor, action, identity, data: audits.append((action, identity, data)))
    return p, checked, published, audits


def test_register_model_reads_real_pack_column_with_original_installation_version(monkeypatch):
    row = installation(); p, checked, published, audits = registry_interfaces(monkeypatch, row)
    assert 'pack_id' not in row.data
    result = service.register_model(object(), p, {'installation_id': row.id, 'version': row.version})
    assert result['installation_id'] == row.id and result['installation_version'] == row.version
    assert result['model_trace'] == trace(row) and checked == [(trace(row), True)]
    assert len(published) == len(audits) == 1 and published[0].execution['adapter'] == 'local_sound_observation'
    assert audits[0][0] == service.MODEL_REGISTER and audits[0][2] == result
    assert result['receipt_hash'] == digest({key: value for key, value in result.items() if key != 'receipt_hash'})


def test_json_pack_claim_cannot_override_a_different_real_installation_pack(monkeypatch):
    row = installation(pack='a_different_real_model', data={'pack_id': PACK})
    p, checked, published, audits = registry_interfaces(monkeypatch, row)
    with pytest.raises(HTTPException) as error:
        service.register_model(object(), p, {'installation_id': row.id, 'version': row.version})
    assert error.value.status_code == 409 and checked == published == audits == []

"""Old source inventory must delegate known full families, never unknown kinds.

These negative pure IO seams do not certify any synthetic source or adoption.
Normal PostgreSQL configuration saving is verified by the full graph journey.
"""
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents import report_configuration_handoff as old
from generic_agents import report_full_semantic_handoff as full
from generic_agents.report_object_adoption_v169 import KIND


P = NS(workspace_id='17100000-0000-4000-8000-000000000001')


class InventoryIO:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, statement):
        params = statement.compile().params
        assert params['workspace_id_1'] == P.workspace_id
        excluded = next(value for value in params.values() if isinstance(value, (tuple, list)))
        assert set(excluded) == {'full_handoff', 'full_replace', KIND}
        return [row for row in self.rows if row.kind not in excluded]


def test_object_family_is_delegated_to_the_full_verifier_even_when_its_bytes_are_bad(monkeypatch):
    row = NS(kind=KIND, data={'broken': 'must be checked by the full-design gate'})
    called = []

    def reject(db, principal, before, proposed):
        assert principal is P and before == {} and proposed == {}
        called.append(row)
        raise HTTPException(409, 'independent full-design rejection remains mandatory')

    monkeypatch.setattr(full, 'protect_configuration', reject)
    with pytest.raises(HTTPException, match='independent full-design rejection') as error:
        old.protect_configuration_sources(InventoryIO([row]), P, {}, {})
    assert error.value.status_code == 409 and called == [row]


def test_unrecognized_record_kind_still_reaches_the_strict_legacy_rejection():
    row = NS(kind='unrecognized_source_family', data={'record': {'schema': 'unrecognized'}})
    with pytest.raises(HTTPException) as error:
        old.all_records(InventoryIO([row]), P)
    assert error.value.status_code == 409 and error.value.detail['code'] == 'source_record_integrity'

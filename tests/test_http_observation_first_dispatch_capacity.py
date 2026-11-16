"""Exercise the real virgin-dispatch guard with controlled reverse-inventory IO."""

import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import http_observation_task_consumer_v270 as subject
from tests.test_http_observation_first_dispatch_v291 import ident, scene


class ReverseAuditResult:
    def __init__(self, rows):
        self.rows = rows
        self.visited = []
        self.closed = False

    def __iter__(self):
        for row in self.rows:
            self.visited.append(row.id)
            yield row

    def close(self):
        self.closed = True


@pytest.mark.parametrize('surviving_reference', [None, 'acquisition_id', 'request_key', 'dispatch_job_id'])
def test_virgin_dispatch_checks_reverse_audits_after_workspace_row_1000(monkeypatch, surviving_reference):
    s = scene(monkeypatch)
    original_scalars = s.db.scalars
    rows = [s.anchor] + [SimpleNamespace(id=ident(100 + i), data={'unrelated': i}) for i in range(1100)]
    if surviving_reference is not None:
        value = {'acquisition_id': s.acquisition.id, 'request_key': s.acquisition.idempotency_key,
                 'dispatch_job_id': s.job.id}[surviving_reference]
        rows[-1].data = {'unrecognized_legacy_field': [{'nested_value': value}]}
    stream = ReverseAuditResult(rows)

    def scalars(statement):
        model = statement.column_descriptions[0]['entity']
        sql = str(statement.compile(compile_kwargs={'literal_binds': True}))
        where = sql.partition('WHERE')[2]
        if model is subject.m.Audit and 'audit_events.object_id' not in where:
            assert s.p.workspace_id in where
            assert statement.get_execution_options().get('yield_per', 0) > 0
            assert 'LIMIT' not in where
            return stream
        return original_scalars(statement)

    monkeypatch.setattr(s.db, 'scalars', scalars)
    before = copy.deepcopy((vars(s.acquisition), vars(s.plan), vars(s.job)))
    args = (s.db, s.p, s.task, s.acquisition, s.plan, s.source, s.origin)
    if surviving_reference is None:
        assert subject._first_dispatch_pending(*args) is True
    else:
        with pytest.raises(HTTPException) as failure:
            subject._first_dispatch_pending(*args)
        assert failure.value.status_code == 409
        assert failure.value.detail == subject.ERROR
    assert stream.visited == [row.id for row in rows]
    assert stream.closed
    assert (vars(s.acquisition), vars(s.plan), vars(s.job)) == before

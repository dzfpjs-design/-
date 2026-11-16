"""Original row/day ACK recovery clears only the projected current error."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import business_data as b, business_data_receipts as receipts, models as m
from generic_agents.business_data_models import BusinessDataImportRow as ImportRow
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import (scene, source, ready, response, dataset,
    target, binding, preview, confirm, BASE)
from tests.test_business_data_history_pg_v204 import row_counts


@pytest.mark.parametrize('kind', ['row', 'day'])
def test_recovered_ack_has_no_current_error_but_keeps_original_failure_without_reimport(scene, kind):
    s = scene; src = source(s)
    read, _ = ready(s, src, dataset([{'id': 'a', 'title': '已保存资料', 'quantity': 1}]))
    if kind == 'row':
        release, object_kind, fields = target(s)
        bind = binding(s, src, read, release, object_kind, fields)
        batch = confirm(s, preview(s, bind, read))
        with Session(engine()) as db:
            row = db.scalar(select(ImportRow).where(ImportRow.import_id == batch['id']))
            row_id, event_id, object_id = row.id, row.data['event_id'], row.data['entity_id']
            entity = db.get(m.Entity, object_id); original_entity = (entity.version, copy.deepcopy(entity.data))
            original_row = copy.deepcopy(row.data)
        path = BASE + '/imports/' + batch['id'] + '/rows/' + row_id + '/retry-data-delivery'
        anchor_action, anchor_subject = b.IMPORTED_AUDIT, row_id
    else:
        receipts.schedule_once(s.workspace)
        with Session(engine()) as db:
            event_id = db.scalar(select(m.Event.id).where(m.Event.workspace_id == s.workspace, m.Event.kind == 'business_data.daily'))
        path = BASE + '/sources/' + src['id'] + '/days/' + event_id + '/retry-data-delivery'
        anchor_action, anchor_subject = receipts.DAY_AUDIT, event_id
    with Session(engine()) as db:
        anchor = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace,
            m.Audit.object_id == anchor_subject, m.Audit.action == anchor_action))
        anchor_id, original_anchor = anchor.id, copy.deepcopy(anchor.data)
        anchor.data = {'damaged_for_original_ack_recovery': True}; db.commit()
    receipts.consume_once(s.workspace)
    with Session(engine()) as db:
        event = db.get(m.Event, event_id); error = event.data['delivery_error']; version = event.version
        assert event.status == 'blocked_delivery'
        blocked = receipts.delivery_view(db, s.p, event)
        assert blocked['error'] == error and blocked['previous_error'] is None
        db.get(m.Audit, anchor_id).data = original_anchor; db.commit()
    retried = response(s.client.post(path, json={'request_id': str(uuid4()),
        'expected_version': version, 'reason': '修复原依据后明确恢复接收'}))
    pending = retried['data_delivery']
    assert pending['status'] == 'pending_delivery' and pending['error'] is None and pending['previous_error'] == error
    receipts.consume_once(s.workspace)
    before_get = row_counts(s.workspace)
    if kind == 'row':
        saved = response(s.client.get(BASE + '/imports/' + batch['id']))['rows'][0]
    else:
        saved = next(item for item in response(s.client.get(BASE + '/sources/' + src['id'] + '/days')) if item['id'] == event_id)
        assert saved['error'] is None
    delivery = saved['data_delivery']
    assert delivery['status'] == 'received' and delivery['verified'] is True
    assert delivery['error'] is None and delivery['previous_error'] == error
    assert row_counts(s.workspace) == before_get
    with Session(engine()) as db:
        event = db.get(m.Event, event_id)
        assert event.status == 'received' and event.data['delivery_error'] == error  # Original failure is never erased.
        if kind == 'row':
            entity = db.get(m.Entity, object_id)
            assert (entity.version, entity.data) == original_entity and db.get(ImportRow, row_id).data == original_row

"""Real two-facet CPU/SDK/ACK source -> pure arithmetic, not NL/Query execution.

The reused fixture has synthetic images/silence and genuine published producer
bindings, native inference and independent receipts. Source-only DataRun/Audit
storage below is explicit and does not impersonate an accepted question/SDK.
"""
import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import multimodal_query_compute_v262 as subject
from generic_agents import pixel_question_source_contract_v252 as pixel
from tests.test_multimodal_query_facets_v262_pg import (
    two_sources, flow_scene, native_scene, pytestmark, capture, store_source, verify, receive,
)
from tests.test_multimodal_query_contracts_v262 import pixel_intent
from tests.test_multimodal_query_compute_v262 import plan, sound_query


def inventory(s):
    with Session(engine()) as db:
        return {cls.__name__: sorted((row.id, row.version, getattr(row, 'status', None), digest(row.data))
            for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)))
            for cls in (m.Job, m.Invocation, m.Observation, m.Event, m.Memory, m.Audit, m.CloudCall, m.DataRun)}


def test_actual_same_snapshot_cpu_ack_values_and_late_ack_do_not_recalculate_old_answer(two_sources):
    s = two_sources
    pending = capture(s, task_id=s.combined_task['id']); old_source = copy.deepcopy(pending)
    old_id = store_source(s, pending); verify(s, old_id, pending)
    read_pixel = pixel_intent(operation='read', metric=None, operation_quote='列出', metric_quote=None)
    read_pixel['question_quote'] = '列出像素成果'
    old_plan = plan(pending, read_pixel, sound_query('read'))
    before = inventory(s); old_answer = subject.compute(pending, old_plan)
    assert inventory(s) == before
    assert old_answer['facets'][1]['computation']['references'][0]['values'] is None
    assert old_answer['verification']['source_authority_checked'] is False

    receive(s, s.pixel_entry); receive(s, s.sound_entry)
    received = capture(s, task_id=s.combined_task['id']); new_id = store_source(s, received)
    verify(s, old_id, pending); verify(s, new_id, received)
    new_plan = plan(received, pixel_intent(),
        sound_query('sum', reception='received', metric='candidate_window_count'))
    before = inventory(s); new_answer = subject.compute(received, new_plan)
    assert inventory(s) == before
    assert new_answer['facets'][0]['computation'] == pixel.compute(received['facets']['pixel'], new_plan['subqueries'][0]['ir'])
    actual = received['facets']['task_sound']['records'][0]
    sound_answer = new_answer['facets'][1]['computation']
    assert sound_answer['groups'][0]['value'] == actual['values']['candidate_window_count']
    assert sound_answer['references'][0]['reception']['receipt_hash'] == actual['reception']['receipt_hash']
    assert subject.checked_result(received, new_plan, new_answer) == new_answer
    assert subject.compute(pending, old_plan) == old_answer and pending == old_source
    assert old_answer['source_snapshot_hash'] != new_answer['source_snapshot_hash']
    assert all(item['as_of'] == received['as_of'] for item in new_answer['facets'])
    assert new_answer['verification']['expected_sources_verified'] is False

"""Saved old-policy queries remain immutable after a future prompt update.

The inherited fixture uses real native CPU, source snapshots, SDK and PG with
an explicitly synthetic planner HTTP provider. No query, fee, ACK or accepted
answer is fabricated. This test is not evidence of real-cloud model quality.
"""
import copy
import json

from generic_agents import multimodal_question_service_v262 as service
from tests.test_multimodal_question_lifecycle_v262_pg import (
    question_scene, two_sources, flow_scene, native_scene, pytestmark,
    ask, finish, good, lookup, inventory, source_state,
)


def test_saved_prior_prompt_read_and_original_nonce_replay_never_rebuild_current_messages_or_charge(question_scene, monkeypatch):
    s = question_scene; current_messages = service._messages

    def prior_policy(*args, **kwargs):
        messages = current_messages(*args, **kwargs)
        system = json.loads(messages[0]['content'])
        # Freeze the actual previous instruction list in the normal paid
        # preview/request, rather than rewriting an already accepted record.
        system['rules'] = [
            '声音仅登记Run，已独立接收才有候选数值；read/count metric=null；sum仅received。'
            if rule.startswith('声音处理或声音成果有多少份') else rule
            for rule in system['rules'] if not rule.startswith((
                'selected_scope由服务器', '原问题未要求日期时', 'task_limited/entity_limited为false',
                '两个成果facet中的', '声音处理成果Run记录不等于原音文件数',
                '接收状态筛选与日期筛选相互独立', '原问题明确要求特定任务或对象'))]
        for family in ('pixel', 'task_sound'):
            for key in ('count_semantics', 'received_semantics', 'no_date_semantics'):
                del system['catalog'][family][key]
        messages[0]['content'] = json.dumps(system, ensure_ascii=False)
        return messages

    monkeypatch.setattr(service, '_messages', prior_policy)
    body, created = ask(s)
    completed = finish(s, created['id'])
    assert completed['persisted_status'] == 'succeeded' and completed['saved_answer'] is not None
    frozen_answer = copy.deepcopy(completed['saved_answer'])
    original_source = source_state(s, created['id']); before = inventory(s)
    assert len(s.posts) == len(s.responses) == 1

    def forbidden_current_prompt(*_args, **_kwargs):
        raise AssertionError('saved read/replay must use frozen preview and original paid answer')

    monkeypatch.setattr(service, '_messages', forbidden_current_prompt)
    assert good(s.client.get(s.question_base + '/' + created['id'])) == completed
    assert lookup(s, body)['query'] == completed
    assert good(s.client.post(s.question_base, json=body), 201) == completed
    assert inventory(s) == before and source_state(s, created['id']) == original_source
    assert completed['saved_answer'] == frozen_answer and len(s.posts) == len(s.responses) == 1

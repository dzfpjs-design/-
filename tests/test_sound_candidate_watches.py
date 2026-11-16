"""Closed command and exact original-score tests; no model inference mocks."""
import copy
import json
from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import sound_candidate_watches as sw
from generic_agents.sound_candidate_watch_api import Create, Status, Check, TargetPins


def pins():
    return {'instance_version': 1, 'release_id': str(uuid4()), 'configuration_hash': 'a' * 64,
        'business_id': 'business_weather', 'entity_id': str(uuid4()), 'entity_version': 1,
        'entity_definition_hash': 'b' * 64, 'capability_id': str(uuid4()), 'capability_version': 1,
        'capability_hash': 'c' * 64, 'labels_sha256': 'd' * 64}


def create_body():
    return {'schema': 'sound-candidate-watch-create-v1', 'request_id': '声音/原申请?保留-' + uuid4().hex,
        'name': '仅提醒核查', 'target_pins': pins(), 'class_index': 494, 'min_score_text': '0.5',
        'min_windows': 1, 'confirm_candidate_only': True}


def result(score_value=.5, index=494):
    labels = ['engineering-class-' + str(i) for i in range(521)]
    values = [0.0] * 521; values[index] = score_value
    window = {'index': 0, 'start_sample': 0, 'end_sample': 8000, 'padded_samples': 7600,
        'start_seconds': 0.0, 'end_seconds': .5, 'scores': values,
        # Deliberately outside top_classes/candidates: a watch reads all 521.
        'top_classes': []}
    return {'model': {'labels': labels}, 'classification': {'windows': [window], 'candidates': []}}


def definition(threshold='0.5', min_windows=1):
    return {'class_index': 494, 'class_label': 'engineering-class-494',
        'min_score_text': threshold, 'min_windows': min_windows}


def test_original_unicode_nonce_and_score_text_are_not_rewritten():
    body = create_body(); body['min_score_text'] = '5.00000000000000001e-1'
    raw = ('  ' + json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
    original, decoded, raw_hash, body_hash = sw.parse(raw, 'create')
    assert original.encode() == raw and decoded == body
    assert decoded['min_score_text'] == body['min_score_text'] and raw_hash != body_hash


@pytest.mark.parametrize('field,value', [('class_index', True), ('min_windows', True), ('min_windows', 0),
    ('class_index', 521), ('confirm_candidate_only', 1), ('confirm_candidate_only', False),
    ('min_score_text', .5), ('min_score_text', 'NaN'), ('min_score_text', 'Infinity'),
    ('min_score_text', '0'), ('min_score_text', '-.1'), ('min_score_text', '1.00000001'),
    ('request_id', 'short'), ('request_id', 'nonce-null-\0-value'), ('name', 'null\0value')])
def test_closed_create_rejects_unpersistable_or_ambiguous_values(field, value):
    body = create_body(); body[field] = value
    with pytest.raises(HTTPException) as error:
        sw.parse(json.dumps(body, ensure_ascii=False).encode(), 'create')
    assert error.value.status_code == 422


@pytest.mark.parametrize('raw', [b'{"schema":"x","schema":"y"}', b'{"value":NaN}', b'[]', b'\xff'])
def test_duplicate_keys_nonfinite_and_nonobject_raw_are_rejected(raw):
    with pytest.raises(HTTPException) as error: sw.parse(raw, 'create')
    assert error.value.status_code == 422


def test_decimal_threshold_does_not_round_to_float32_or_double():
    item = result(.5)
    assert len(sw.matched_windows(definition('0.5'), item)) == 1
    assert sw.matched_windows(definition('0.500000000000000001'), item) == []
    assert len(sw.matched_windows(definition('0.499999999999999999'), item)) == 1
    assert Decimal.from_float(.5) < sw.score('0.500000000000000001')


def test_positive_tiny_threshold_does_not_underflow_into_zero_score_match():
    assert sw.matched_windows(definition('1e-9999'), result(0.0)) == []
    assert len(sw.matched_windows(definition('1e-9999'), result(.5))) == 1


def test_watch_uses_full_class_scores_independent_of_model_threshold_or_top_five():
    item = result(.25); item['classification']['threshold'] = .9
    assert not item['classification']['candidates'] and not item['classification']['windows'][0]['top_classes']
    windows = sw.matched_windows(definition('.2'), item)
    assert [w['score'] for w in windows] == [.25]
    assert set(windows[0]) == {'index', 'start_sample', 'end_sample', 'padded_samples', 'start_seconds', 'end_seconds', 'score'}


def test_window_count_keeps_overlap_and_never_computes_event_duration_or_source_count():
    item = result(); second = copy.deepcopy(item['classification']['windows'][0])
    second.update(index=1, start_sample=7680, end_sample=15680, start_seconds=.48, end_seconds=.98)
    item['classification']['windows'].append(second)
    windows = sw.matched_windows(definition(min_windows=2), item)
    assert len(windows) == 2 and windows[0]['end_sample'] > windows[1]['start_sample']
    assert all('duration' not in w and 'source_count' not in w for w in windows)


def test_status_and_explicit_existing_commands_are_separate_closed_contracts():
    target = pins(); nonce = 'explicit-original-' + uuid4().hex
    pause = {'schema': 'sound-candidate-watch-status-v1', 'request_id': nonce, 'version': 1,
        'status': 'paused', 'target_pins': None}
    assert sw.parse(json.dumps(pause).encode(), 'status')[1] == pause
    check = {'schema': 'sound-candidate-watch-check-v1', 'request_id': nonce, 'version': 1,
        'target_pins': target, 'before_run_id': None, 'confirm_existing_received_only': True}
    assert sw.parse(json.dumps(check).encode(), 'check_existing')[1] == check
    check['confirm_existing_received_only'] = False
    with pytest.raises(HTTPException): sw.parse(json.dumps(check).encode(), 'check_existing')



def test_signal_family_detection_is_null_safe_and_uses_independent_event_trace():
    from types import SimpleNamespace
    p = SimpleNamespace(workspace_id=str(uuid4()))
    signal = SimpleNamespace(data={'basis': None}, event_id=str(uuid4()))
    class ReadOnlyIO:
        def __init__(self, event): self.event = event; self.calls = 0
        def scalar(self, query): self.calls += 1; return self.event
    ordinary = ReadOnlyIO(None)
    assert sw.is_sound_signal(ordinary, p, signal) is False and ordinary.calls == 1
    event = SimpleNamespace(data={'schema': sw.EVENT_SCHEMA}, idempotency_key='candidate-sound:original')
    traced = ReadOnlyIO(event)
    assert sw.is_sound_signal(traced, p, signal) is True and traced.calls == 1

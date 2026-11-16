"""Closed review-wire checks only; no DB/source/role/reception success claim.

Tests exercise the actual frozen protocol. A syntactically accepted frame ID is
not proof that the frame belongs to the source; Store and Root's actual PG flow
must check that independently. No old Run/Observation/Memory is constructed or
changed, and no model, Gateway, network, or media decoder is invoked.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from generic_agents import native_video_review_contracts_v165 as protocol


def uid(n):
    return '165abc00-0000-4000-8000-' + f'{n:012x}'


def body(*, submitted=False):
    value = {'request_id': 'video-review-adversarial-165', 'expected_version': 0,
        'note': '', 'frame_ids': [], 'decision': None}
    if submitted:
        value.update(expected_version=2, note='  仅核对已保存候选与画面，不确认现场真值。\n',
            frame_ids=[uid(3), uid(1)], decision='uncertain', confirm_candidate_only=True)
    return value


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def refused(raw, model):
    with pytest.raises(ValueError) as error:
        protocol.parse_wire(raw, model)
    assert str(error.value) == protocol.ERROR


def test_draft_preserves_unfinished_input_without_implying_a_reviewed_opinion():
    value = body(); raw = wire(value); original = deepcopy(value)
    parsed = protocol.parse_wire(raw, protocol.Draft)
    assert parsed.model_dump() == original
    assert parsed.expected_version == 0 and parsed.decision is None and parsed.frame_ids == []
    assert value == original


def test_submission_keeps_original_human_text_and_frame_order_without_promoting_facts():
    value = body(submitted=True); original = deepcopy(value); raw = wire(value)
    parsed = protocol.parse_wire(raw, protocol.Submit)
    assert parsed.model_dump() == original and value == original
    assert parsed.note.startswith('  ') and parsed.note.endswith('\n')
    assert parsed.frame_ids == [uid(3), uid(1)]
    assert parsed.decision == 'uncertain' and parsed.confirm_candidate_only is True
    assert protocol.wire_sha256(raw) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('field,value', [
    ('expected_version', True), ('expected_version', 1.0), ('expected_version', '1'),
    ('expected_version', -1), ('note', 42), ('frame_ids', uid(1)),
    ('frame_ids', [uid(1), uid(1)]), ('decision', False)],
    ids=['cas-bool', 'cas-float', 'cas-string', 'cas-negative', 'note-number',
        'frames-string', 'duplicate-frame', 'decision-bool'])
def test_draft_never_coerces_original_types_or_duplicate_frame_selection(field, value):
    value_body = body(); value_body[field] = value
    refused(wire(value_body), protocol.Draft)


@pytest.mark.parametrize('field,value', [('confirm_candidate_only', 1), ('confirm_candidate_only', False),
    ('decision', None), ('frame_ids', []), ('note', '  仅看过  ')],
    ids=['confirmation-number', 'confirmation-false', 'no-decision', 'no-frame', 'short-basis'])
def test_submit_requires_explicit_candidate_only_confirmation_selection_and_basis(field, value):
    value_body = body(submitted=True); value_body[field] = value
    refused(wire(value_body), protocol.Submit)


@pytest.mark.parametrize('model,submitted', [(protocol.Draft, False), (protocol.Submit, True)], ids=['draft', 'submit'])
@pytest.mark.parametrize('extra', ['instance_id', 'actor_id', 'task_completed'])
def test_closed_command_cannot_supply_scope_actor_or_task_completion(model, submitted, extra):
    value = body(submitted=submitted); value[extra] = uid(7) if extra != 'task_completed' else True
    refused(wire(value), model)


@pytest.mark.parametrize('fault', ['escaped-duplicate', 'nonfinite', 'invalid-utf8', 'array', 'nul', 'surrogate', 'oversize'])
def test_bad_wire_is_rejected_without_normalizing_it_into_a_different_request(fault):
    raw = wire(body())
    if fault == 'escaped-duplicate': raw = raw[:-1] + b',"\\u006eote":"second"}'
    elif fault == 'nonfinite': raw = raw.replace(b'"expected_version":0', b'"expected_version":NaN')
    elif fault == 'invalid-utf8': raw = b'\xff' + raw
    elif fault == 'array': raw = b'[' + raw + b']'
    elif fault == 'nul': raw = raw.replace(b'"note":""', b'"note":"\\u0000"')
    elif fault == 'surrogate': raw = raw.replace(b'"note":""', b'"note":"\\ud800"')
    else: raw += b' ' * (64 * 1024)
    original = raw
    refused(raw, protocol.Draft)
    assert raw == original


@pytest.mark.parametrize('frame_id', [uid(1).upper(), '../another-frame'], ids=['noncanonical-uuid', 'path-injection'])
def test_frame_reference_is_a_canonical_identity_not_an_arbitrary_location(frame_id):
    value = body(submitted=True); value['frame_ids'] = [frame_id]
    refused(wire(value), protocol.Submit)


def test_equivalent_json_spelling_keeps_different_original_wire_identity_and_fixed_paths():
    value = body(); compact = wire(value)
    spaced = json.dumps(value, ensure_ascii=False, indent=1).encode('utf-8')
    assert protocol.parse_wire(compact, protocol.Draft).model_dump() == protocol.parse_wire(spaced, protocol.Draft).model_dump()
    assert protocol.wire_sha256(compact) != protocol.wire_sha256(spaced)
    root = '/api/workspace/instances/' + uid(5) + '/native-video-reviews'
    assert protocol.root_path(uid(5)) == root
    assert protocol.command_path(uid(5), uid(6), 'submit') == root + '/observations/' + uid(6) + '/submit'
    assert protocol.command_path(uid(5), uid(6), 'draft') == root + '/observations/' + uid(6) + '/draft'


@pytest.mark.parametrize('instance_id,observation_id', [(uid(5).upper(), uid(6)), (uid(5), '../other-observation')],
    ids=['noncanonical-instance', 'observation-path-injection'])
def test_command_path_does_not_accept_another_url_or_noncanonical_scope(instance_id, observation_id):
    with pytest.raises(ValueError):
        protocol.command_path(instance_id, observation_id, 'draft')

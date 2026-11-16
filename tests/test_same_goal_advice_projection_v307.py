"""Pure privacy/contract checks over explicitly spied source/frame inputs.

No actual source, approval, consent, storage, SDK or sending authority is
claimed. The original V305 fixture declares those IO boundaries separately.
"""
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_projection_v307 as p
from generic_agents.contracts import digest
from tests.test_same_goal_advice_frame_v305 import scene as frame_scene, build

IDENTITY = 'abcdef12-abcd-4abc-8abc-abcdef123456'


@pytest.fixture
def scene(monkeypatch):
    fixture = frame_scene.__wrapped__(monkeypatch)
    frame = build(fixture); prep = copy.deepcopy(fixture.resolved['preparation'])
    source = frame['source_record']; refs = [source['data_evidence_ref'], source['document']['evidence_ref']]
    texts = [frame['decision_input']['sources'][index]['text'] for index in (0, 1)]
    pins = [{**copy.deepcopy(ref), 'version': 1, 'model_profile_id': prep['model_pin']['id']} for ref in refs]
    parts = []
    for ref, text in zip(refs, texts):
        parts.extend([{'type': 'text', 'text': json.dumps({'evidence_catalogue_entry': {'evidence_id': ref['evidence_id']},
            'instruction': p.CATALOGUE_INSTRUCTION}, ensure_ascii=False)}, {'type': 'text', 'text': text}])
    return {'frame': frame, 'preparation': prep, 'evidence_parts': parts, 'evidence_pins': pins}


def derive(value):
    return p.derive(value['frame'], value['preparation'], evidence_parts=value['evidence_parts'], evidence_pins=value['evidence_pins'])


def change_instruction(value, text):
    value['preparation']['instruction'] = text
    value['preparation']['record_hash'] = p.c.record_hash(value['preparation'])
    value['frame']['instruction'] = text


def change_document_text(value, text):
    ref = value['frame']['source_record']['document']['evidence_ref']
    ref.update(sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(), size_bytes=len(text.encode('utf-8')))
    value['frame']['source_record']['record_hash'] = p.c.record_hash(value['frame']['source_record'])
    value['frame']['decision_input']['sources'][1].update(text=text, content_hash=ref['sha256'])
    value['evidence_pins'][1].update(ref)
    value['evidence_parts'][3]['text'] = text


def logical(value):
    result = []
    def visit(item, field=None):
        if type(item) is str and field in p.uuids.LOGICAL_REFS: result.append((field, item))
        elif type(item) is dict:
            for key, child in item.items(): visit(child, key)
        elif type(item) is list:
            for child in item: visit(child, field[:-1] if field and field.endswith('_refs') else field)
    visit(value); return result


def test_actual_new_cloud_scope_and_every_logical_ref_survive_deterministic_private_projection(scene):
    before = copy.deepcopy(scene); result = derive(scene); again = derive(scene)
    assert result == again and scene == before
    assert set(result) == {'schema', 'plan_id', 'preparation_snapshot_hash', 'local_input_hash',
        'aliases', 'mapping_hash', 'cloud_request', 'projection_hash'}
    assert result['local_input_hash'] == digest(scene['frame']['decision_input'])
    cloud = result['cloud_request']
    assert set(cloud) == {'question', 'registered_instruction', 'decision_input', 'evidence_parts', 'input_hash'}
    assert cloud['input_hash'] == digest(cloud['decision_input']) != result['local_input_hash']
    assert set(cloud['decision_input']['scope']) == {'goal_snapshot_hash', 'entity_ids'}
    assert logical(cloud['decision_input']) == logical(scene['frame']['decision_input'])
    assert result['mapping_hash'] == digest(result['aliases'])
    assert result['projection_hash'] == digest({key: value for key, value in result.items() if key != 'projection_hash'})
    messages = p.messages(result); rendered = json.dumps(messages, ensure_ascii=False).lower()
    for original, alias in result['aliases'].items():
        assert original not in rendered and len(alias) == len(original) == 36
    assert not any(key in rendered for key in ('mapping_hash', 'aliases', 'preparation_snapshot_hash', 'analysis_plan_id'))
    content = json.loads(messages[1]['content'][0]['text'])
    assert set(content) == {'question', 'registered_instruction', 'decision_input', 'input_hash'}
    assert content['input_hash'] == cloud['input_hash'] and messages[1]['content'][1:] == cloud['evidence_parts']
    assert cloud['decision_input']['facts'][0]['value'] == scene['frame']['decision_input']['facts'][0]['value']
    assert type(cloud['decision_input']['facts'][0]['value']) is int
    result['aliases'].clear(); again['cloud_request']['question'] = 'caller-mutated DTO'
    assert derive(scene) != again and scene == before


@pytest.mark.parametrize('only_in', ['question', 'instruction', 'evidence_text'])
def test_uuid_occurring_only_in_one_outbound_text_is_inventoried_including_uppercase_spelling(scene, only_in):
    if only_in == 'question': scene['frame']['question'] += ' ' + IDENTITY.upper()
    elif only_in == 'instruction': change_instruction(scene, scene['preparation']['instruction'] + ' ' + IDENTITY.upper())
    else: change_document_text(scene, scene['evidence_parts'][3]['text'] + ' ' + IDENTITY.upper())
    result = derive(scene); rendered = json.dumps(p.messages(result), ensure_ascii=False).lower()
    assert IDENTITY in result['aliases'] and IDENTITY not in rendered and result['aliases'][IDENTITY] in rendered
    prep = scene['preparation']
    expected = p.uuids.project_text(IDENTITY, namespace_id=prep['scope']['analysis_plan_id'],
        salt='same-goal-advice:' + prep['record_hash'])
    assert result['aliases'][IDENTITY] == expected
    assert result['cloud_request']['registered_instruction'] == p.uuids.project_text(prep['instruction'],
        namespace_id=prep['scope']['analysis_plan_id'], salt='same-goal-advice:' + prep['record_hash'])


def test_partial_uuid_inside_a_clipped_quote_uses_unchanged_offsets_into_the_anonymous_original_source(scene):
    decision = scene['frame']['decision_input']; source = decision['sources'][2]
    source['text'] = 'prefix---' + IDENTITY.upper() + '---suffix'
    citation = decision['facts'][0]['source_refs'][0]
    citation.update(quote_start=7, quote_end=22, quote=source['text'][7:22])
    original_quote = citation['quote']; result = derive(scene)['cloud_request']['decision_input']
    projected = result['facts'][0]['source_refs'][0]
    assert (projected['quote_start'], projected['quote_end']) == (7, 22)
    assert projected['quote'] == result['sources'][2]['text'][7:22]
    assert projected['quote'] != original_quote and len(projected['quote']) == len(original_quote)
    p.c._check_decision(result)


@pytest.mark.parametrize('location', ['question', 'instruction', 'evidence_text'])
@pytest.mark.parametrize('secret', ['Authorization: Bearer public-test-secret', 'password="public-test-secret"',
    'https://user:public-test-secret@example.invalid/path'])
def test_real_credential_markers_are_refused_even_when_uuid_anonymization_is_possible(scene, location, secret):
    if location == 'question': scene['frame']['question'] = secret + ' ' + IDENTITY
    elif location == 'instruction': change_instruction(scene, secret + ' ' + IDENTITY)
    else: change_document_text(scene, secret + ' ' + IDENTITY)
    with pytest.raises(HTTPException) as error: derive(scene)
    assert 'public-test-secret' not in str(error.value.detail)


@pytest.mark.parametrize('fault', ['old_input', 'activation_scope', 'model_pin', 'evidence_order', 'wrong_model_pin',
    'image_pin', 'image_part', 'source_plan', 'prep_hash', 'bad_part_bytes', 'catalogue_extra', 'invalid_scalar'])
def test_only_exact_new_text_frame_and_matching_local_source_and_model_pins_are_projected(scene, fault):
    frame = scene['frame']
    if fault == 'old_input': frame['decision_input']['schema'] = 'business-decision-input-v274'
    elif fault == 'activation_scope': frame['decision_input']['scope']['activation_id'] = 'not-a-real-node'
    elif fault == 'model_pin': frame['model_pin']['content_hash'] = 'f' * 64
    elif fault == 'evidence_order': frame['evidence_ids'].reverse()
    elif fault == 'wrong_model_pin': scene['evidence_pins'][0]['model_profile_id'] = IDENTITY
    elif fault == 'image_pin': scene['evidence_pins'][0]['media_type'] = 'image/png'
    elif fault == 'image_part': scene['evidence_parts'][1] = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}
    elif fault == 'source_plan':
        frame['source_record']['plan_id'] = IDENTITY
        frame['source_record']['record_hash'] = p.c.record_hash(frame['source_record'])
    elif fault == 'prep_hash': scene['preparation']['record_hash'] = 'f' * 64
    elif fault == 'bad_part_bytes': scene['evidence_parts'][3]['text'] += 'changed outside original bytes'
    elif fault == 'catalogue_extra':
        catalogue = json.loads(scene['evidence_parts'][0]['text']); catalogue['unapproved_metadata'] = IDENTITY
        scene['evidence_parts'][0]['text'] = json.dumps(catalogue)
    else: frame['decision_input']['facts'][0]['value'] = float('nan')
    with pytest.raises(HTTPException): derive(scene)


def test_private_original_configuration_and_uuid_keys_never_enter_cloud_messages(scene):
    pins = scene['frame']['capability_pins']
    pins['capabilities'][0]['data']['execution']['private_' + IDENTITY.upper()] = 'Authorization: local-only-secret'
    pins['pins_hash'] = digest({key: value for key, value in pins.items() if key != 'pins_hash'})
    result = derive(scene); rendered = json.dumps(p.messages(result), ensure_ascii=False)
    assert IDENTITY in result['aliases']
    assert 'local-only-secret' not in rendered and 'private_' not in rendered and IDENTITY not in rendered.lower()


@pytest.mark.parametrize('fault', ['changed_projection', 'raw_identifier', 'local_mapping_in_request'])
def test_messages_never_accept_tampered_or_locally_expanded_cloud_request(scene, fault):
    result = derive(scene)
    if fault == 'changed_projection': result['cloud_request']['question'] += ' changed'
    else:
        if fault == 'raw_identifier': result['cloud_request']['question'] += ' ' + result['plan_id'].upper()
        else: result['cloud_request']['aliases'] = result['aliases']
        result['projection_hash'] = digest({key: value for key, value in result.items() if key != 'projection_hash'})
    with pytest.raises(HTTPException): p.messages(result)

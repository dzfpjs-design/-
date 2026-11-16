"""Synthetic v6 grounding/producer/service tests; no DB or runtime calls."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator

import generic_agents
from generic_agents import native_query_proposal_contract as v1
from generic_agents import native_query_proposal_v2 as v2
from generic_agents import native_query_proposal_v3 as v3
from generic_agents import native_query_proposal_v4 as v4
from generic_agents import native_query_proposal_v5 as v5
from generic_agents import native_query_proposal_v6 as v6
from generic_agents import native_query_proposals as service
from generic_agents.contracts import digest
from generic_agents.retrieval import text_tokens
from test_native_query_proposal_v5 import catalog, candidate, aggregate, text_step, EVIDENCE, QUESTION, LEGACY_HASHES

SOURCE = Path(__file__).resolve().parents[1] / 'src'


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('DB/network/subprocess forbidden in v6 pure tests')
    import socket
    import subprocess
    from generic_agents import db
    monkeypatch.setattr(db, 'engine', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def validate(wire=None, question=QUESTION, description=None, evidence=None):
    return v6.validate_candidate(candidate() if wire is None else wire, question,
        catalog() if description is None else description, EVIDENCE if evidence is None else evidence)


def fulltext(text='BRONZE CERAMIC', quote='BRONZE、CERAMIC', source=0):
    step = text_step(quote=quote, source=source)
    step['parameters']['text'] = text
    return step


def tokens_case(text='BRONZE CERAMIC', quote='BRONZE、CERAMIC', method='fulltext'):
    step = fulltext(text, quote)
    step['capability_id'] = method
    if method == 'keyword':
        step['parameters'] = {'field': 'title', 'text': text}
    return candidate([step]), '标题；数量；只查' + quote + '。'


def codes(result):
    return {item['code'] for item in result['source_grounding']['diagnostics']}


def test_staged_source_and_frozen_evaluators_unchanged():
    assert Path(generic_agents.__file__).resolve().parent == SOURCE / 'generic_agents'
    expected = {**LEGACY_HASHES, 'native_query_proposal_v5.py': '8114922a1d7cc632815df7245c5d498f37bd091a800f489612b329de7283af55'}
    for name, value in expected.items():
        assert hashlib.sha256((SOURCE / 'generic_agents' / name).read_bytes()).hexdigest() == value


@pytest.mark.parametrize('version,module', [(1, v1), (2, v2), (3, v3), (4, v4), (5, v5), (6, v6)])
def test_frozen_version_mapping_is_exact_and_readonly(version, module):
    frozen = {'proposal_contract_version': version}
    assert service._wire_parser(frozen) is module
    assert frozen == {'proposal_contract_version': version}
    if version >= 3:
        wire, question = tokens_case()
        result = module.validate_candidate(wire, question, catalog(), EVIDENCE)
        assert (result['candidate']['status'] == 'candidate') is (version == 6)
        assert ('source_grounding' in result) is (version == 6)


@pytest.mark.parametrize('version', [0, 7, True, '6', None])
def test_unknown_version_never_downgrades(version):
    with pytest.raises(HTTPException):
        service._wire_parser({'proposal_contract_version': version})
    assert service._wire_parser({}) is v1


@pytest.mark.parametrize('text,quote', [
    ('BRONZE CERAMIC', 'BRONZE、CERAMIC'), ('Alpha beta', 'Alpha/beta'),
    ('beta ALPHA', 'Alpha—beta'), ('alpha alpha', 'alpha,alpha'),
    ('火 热', '火、热'), ('Ａlpha', 'Alpha'), ('甲乙', '甲乙')])
def test_actual_tokenizer_grounding_preserves_original_wire(text, quote):
    wire, question = tokens_case(text, quote)
    before = copy.deepcopy((wire, question))
    result = validate(wire, question)
    grounding = result['source_grounding']; step = grounding['steps'][0]
    assert result['candidate']['status'] == 'candidate'
    assert (wire, question) == before
    assert result['candidate']['tool_steps'][0]['parameters']['text'] == text
    assert result['raw_hash'] == grounding['raw_hash'] == digest(wire)
    assert result['question_hash'] == grounding['question_hash'] == digest(question)
    assert grounding['schema'] == 'native-query-grounding-v1'
    assert grounding['tokenizer_version'] == 'zh_character_bigram_en_word_v1'
    assert grounding['semantic_equivalence_verified'] is False
    assert grounding['blocker_count'] == len(grounding['diagnostics']) == 0
    assert step['text'] == text and step['quote'] == quote and step['status'] == 'supported'
    assert question[step['start']:step['end']] == quote
    assert step['query_tokens'] == text_tokens(text)
    assert all(item['source_count'] >= item['required_count'] for item in step['tokens'])


@pytest.mark.parametrize('text,quote,expected', [
    ('alpha gamma', 'alpha beta', 'fulltext_tokens_not_grounded'),
    ('alpha alpha', 'alpha beta', 'fulltext_tokens_not_grounded'),
    ('甲乙', '甲、乙', 'fulltext_tokens_not_grounded'),
    ('!!!', '!!!', 'fulltext_empty_tokens')])
def test_missing_repeated_and_bigram_tokens_block(text, quote, expected):
    wire, question = tokens_case(text, quote)
    result = validate(wire, question)
    assert result['candidate']['status'] == 'needs_clarification'
    assert expected in codes(result)
    step = result['source_grounding']['steps'][0]
    assert step['status'] == 'blocked' and expected in step['diagnostic_codes']
    with pytest.raises(HTTPException):
        v1._candidate(result)


@pytest.mark.parametrize('quote,question', [
    ('alpha beta', '标题；数量；不是所引文字'),
    ('alpha beta', '标题；数量；alpha beta又alpha beta')])
def test_forged_or_ambiguous_quote_cannot_support_tokens(quote, question):
    wire, _ = tokens_case('alpha', quote)
    result = validate(wire, question)
    assert 'quote_not_unique' in codes(result)
    step = result['source_grounding']['steps'][0]
    assert step['start'] is step['end'] is None
    assert step['status'] == 'blocked' and step['tokens'][0]['source_count'] == 0


def test_same_text_different_step_quotes_never_share_grounding():
    wire = candidate([fulltext('alpha', '第一处alpha'), fulltext('alpha', '第二处beta')])
    result = validate(wire, '标题；数量；第一处alpha；第二处beta')
    first, second = result['source_grounding']['steps']
    assert first['status'] == 'supported' and second['status'] == 'blocked'
    assert first['tokens'][0]['source_count'] == 1 and second['tokens'][0]['source_count'] == 0
    assert result['candidate']['tool_steps'][0]['parameters']['text'] == result['candidate']['tool_steps'][1]['parameters']['text'] == 'alpha'


@pytest.mark.parametrize('text,quote,supported', [('alpha beta', 'alpha、beta', False), ('ALPHA', 'alpha', True)])
def test_keyword_keeps_contiguous_casefold_contract(text, quote, supported):
    wire, question = tokens_case(text, quote, method='keyword')
    result = validate(wire, question)
    assert (result['candidate']['status'] == 'candidate') is supported
    assert result['source_grounding']['steps'] == []
    assert ('keyword_not_contiguous' in codes(result)) is (not supported)


def test_all_blockers_retained_before_display_truncation():
    description = catalog()
    description['fields'] = [dict(description['fields'][0], key='field_' + str(i), label='字段' + str(i)) for i in range(15)]
    description['field_conflicts'] = [{'key': item['key']} for item in description['fields']]
    wire = candidate(); wire['fields'] = [{'key': item['key'], 'quote': '不存在' + str(i)} for i, item in enumerate(description['fields'])]
    result = validate(wire, '原需求', description)
    grounding = result['source_grounding']
    assert grounding['blocker_count'] == len(grounding['diagnostics']) == 45
    assert len(result['candidate']['questions']) == 10
    assert result['candidate']['status'] == 'needs_clarification'
    assert any('field_14' in item['message'] for item in grounding['diagnostics'])


@pytest.mark.parametrize('kind,field,accepted', [
    ('field_value', 'quantity', True), ('field_value', 'value', False),
    ('query_value', 'value', True), ('query_value', 'unknown', False)])
def test_criterion_schema_discriminates_source_and_possible_output(kind, field, accepted):
    wire = candidate([aggregate()]); wire['result_criterion'] = {
        'kind': kind, 'field': field, 'operator': 'gte', 'expected': 20, 'quote': '至少20'}
    schema = v6.result_schema(catalog())
    assert (next(Draft202012Validator(schema).iter_errors(wire), None) is None) is accepted
    if not accepted:
        with pytest.raises(v6.CandidateValidationError):
            validate(wire, QUESTION + '至少20')


@pytest.mark.parametrize('wire,expected', [
    (candidate([aggregate(), text_step(source=1)]), 'producer_field'),
    (candidate([aggregate()]), 'criterion_producer'),
    (candidate(), 'query_criterion_without_steps')])
def test_exact_producer_frame_remains_a_gate(wire, expected):
    wire = copy.deepcopy(wire)
    if expected != 'producer_field':
        wire['result_criterion'] = {'kind': 'query_value', 'field': 'title', 'operator': 'eq', 'expected': 'abc', 'quote': '须为abc'}
    result = validate(wire, QUESTION + '须为abc')
    assert expected in codes(result) and result['candidate']['status'] == 'needs_clarification'


@pytest.mark.parametrize('operation,unit', [('count', '条证据记录'), ('mean', '件'), ('sum', '件'), ('min', '件'), ('max', '件')])
def test_registered_effect_units_and_ranked_subset_are_unchanged(operation, unit):
    wire = candidate([text_step(), aggregate(operation, source=1)])
    result = validate(wire)
    assert result['candidate']['status'] == 'candidate'
    assert result['constraints']['ranked_subset'] and result['constraints']['requires_ranked_subset_confirmation']
    effect = result['query_effect']
    assert effect == v5.derive_query_effect(result['candidate'], catalog())
    output = {item['key']: item['unit'] for item in effect['steps'][-1]['output']['fields']}
    assert output['value'] == unit and output['sample_count'] == '条证据记录'
    assert effect['steps'][-1]['denominator']['frame_id'] == 'operation_1'


def test_unknown_units_remain_blocked_and_count_is_not_arithmetic():
    description = catalog(); description['fields'][1]['unit'] = None
    assert 'unknown_calculation_unit' in codes(validate(candidate([aggregate()]), description=description))
    assert validate(candidate([aggregate('count')]), description=description)['candidate']['status'] == 'candidate'


@pytest.mark.parametrize('change', ['numeric_text', 'unknown_tool', 'future_input', 'extra_field', 'wrong_expected'])
def test_fixed_structure_type_evidence_gates_cannot_be_bypassed(change):
    wire = candidate([text_step()]); evidence = EVIDENCE
    if change == 'numeric_text': wire['operations'][0]['parameters']['field'] = 'quantity'
    if change == 'unknown_tool': wire['operations'][0]['capability_id'] = 'external'
    if change == 'future_input': wire['operations'][0]['input_from'] = 1
    if change == 'extra_field': wire['source_grounding'] = {'status': 'supported'}
    if change == 'wrong_expected': wire['result_criterion'] = {'kind': 'field_value', 'field': 'quantity', 'operator': 'gte', 'expected': None, 'quote': '数量'}
    with pytest.raises(v6.CandidateValidationError): validate(wire, evidence=evidence)


@pytest.mark.parametrize('case,expected', [('direction', 'relation_direction'), ('duplicate', 'duplicate_relation_scope'),
    ('ambiguous', 'ambiguous_root'), ('future', 'future_relation_time'), ('invalid_time', 'invalid_relation_time')])
def test_relation_scope_identity_and_time_gates(case, expected, monkeypatch):
    description = catalog(); wire = candidate()
    wire['relation_query'] = {'roots': [{'key': 'root', 'quote': '枢纽'}],
        'types': [{'key': 'route', 'quote': '联络'}], 'direction': 'outgoing', 'max_hops': 2, 'include_roots': False, 'at': None}
    if case == 'direction': description['relation_types'][0]['source_types'] = ['different']
    if case == 'duplicate': wire['relation_query']['roots'] *= 2
    if case == 'ambiguous': description['objects'][1]['label'] = '枢纽'
    if case == 'future': wire['relation_query']['at'] = '2099-01-01T00:00:00Z'
    if case == 'invalid_time': wire['relation_query']['at'] = 'bad'
    from generic_agents import models
    monkeypatch.setattr(models, 'now', lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    result = validate(wire, QUESTION + '枢纽经联络', description)
    assert expected in codes(result) and result['candidate']['status'] == 'needs_clarification'


def test_criterion_expected_numeric_value_and_selected_scope_not_inferred():
    wire = candidate([aggregate()]); wire['fields'] = wire['fields'][:1]
    result = validate(wire)
    assert 'producer_field' in codes(result)
    wire = candidate([aggregate()]); wire['result_criterion'] = {'kind': 'query_value', 'field': 'value', 'operator': 'gte', 'expected': 77, 'quote': '数量'}
    assert 'criterion_number_not_grounded' in codes(validate(wire))


def test_model_clarification_and_no_fulltext_have_explicit_empty_grounding():
    wire = {'status': 'needs_clarification', 'summary': '请核对', 'questions': ['请明确单位'], 'limitations': []}
    result = validate(wire)
    assert result['source_grounding']['steps'] == []
    assert result['source_grounding']['blocker_count'] == 1
    assert result['query_effect']['status'] == 'not_applicable'
    assert validate()['source_grounding']['blocker_count'] == 0


def test_new_preview_fixes_v6_and_generic_grounding_guidance(monkeypatch):
    cap = SimpleNamespace(id='pure-profile', content_hash='pure-hash', data={'execution': {'provider': 'qwen', 'model': 'pure-test'}, 'data_policy': {}})
    monkeypatch.setattr(service, '_current', lambda *args: ({'mode': 'query'}, catalog(), {'question': QUESTION, 'evidence': EVIDENCE, 'revision': 1}))
    monkeypatch.setattr(service, 'model', lambda *args: cap)
    monkeypatch.setattr(service, 'model_view', lambda *args: {'id': cap.id})
    frozen = service.preview(None, None, None, cap.id)
    assert frozen == service.preview(None, None, None, cap.id)
    assert frozen['proposal_contract_version'] == 6 and frozen['schema'] == v6.result_schema(catalog())
    content = json.loads(frozen['request_messages'][0]['content'])
    for text in ('连续', '词项', '重复次数', 'field_value', 'query_value', 'producer'):
        assert text in content['instruction'] + json.dumps(content['query_rules'], ensure_ascii=False)
    assert frozen['question'] == QUESTION and frozen['preview_hash'] == digest({k: v for k, v in frozen.items() if k != 'preview_hash'})


def response_row(wire=None, version=6):
    raw = {'content': json.dumps(candidate() if wire is None else wire, ensure_ascii=False), 'receipt': {'saved': True}}
    frozen = {'proposal_contract_version': version, 'question': QUESTION, 'catalog': catalog(), 'evidence': EVIDENCE}
    return SimpleNamespace(status='running', version=1, data={'frozen': frozen, 'raw_response': raw, 'raw_response_hash': digest(raw)})


def test_fixed_v6_contract_failure_has_sealed_exact_stage_binding(monkeypatch):
    wire = candidate(); wire['result_criterion'] = {'kind': 'field_value', 'field': 'value', 'operator': 'gte', 'expected': 20, 'quote': '数量'}
    row = response_row(wire)
    monkeypatch.setattr(service, '_active_context', lambda *a, **k: row.data['frozen'])
    assert service._consume(None, None, None, row) == 'failed'
    assert row.data['validation_failure'] == {'stage': 'candidate_validation', 'wire_contract_version': 6,
        'raw_response_hash': row.data['raw_response_hash'], 'frozen_hash': digest(row.data['frozen'])}
    assert row.data['record_hash'] == digest({k: v for k, v in row.data.items() if k != 'record_hash'})


@pytest.mark.parametrize('failure', ['context', 'permission', 'hash', 'json', 'question', 'evidence', 'unknown_http', 'unknown_type', 'database', 'legacy'])
def test_other_failure_origins_never_claim_candidate_validation(failure, monkeypatch):
    row = response_row(version=5 if failure == 'legacy' else 6)
    def active(*args, **kwargs):
        if failure in {'context', 'permission'}: raise HTTPException(403 if failure == 'permission' else 409, 'scope gate')
        return row.data['frozen']
    monkeypatch.setattr(service, '_active_context', active)
    if failure == 'hash': row.data['raw_response_hash'] = 'wrong'
    if failure == 'json':
        row.data['raw_response']['content'] = '{bad'; row.data['raw_response_hash'] = digest(row.data['raw_response'])
    if failure == 'question': row.data['frozen']['question'] = ''
    if failure == 'evidence': row.data['frozen']['evidence'] = {'source': 'invented'}
    if failure in {'unknown_http', 'unknown_type', 'database', 'legacy'}:
        issue = {'unknown_http': HTTPException(422, 'unknown'), 'unknown_type': TypeError('unknown'),
            'database': RuntimeError('database outage'), 'legacy': v6.CandidateValidationError('not a v6 call')}[failure]
        def reject(*args): raise issue
        monkeypatch.setattr(v5 if failure == 'legacy' else v6, 'validate_candidate', reject)
    if failure == 'database':
        with pytest.raises(RuntimeError): service._consume(None, None, None, row)
    else:
        assert service._consume(None, None, None, row) == 'failed'
    assert 'validation_failure' not in row.data


def test_public_view_exposes_original_bound_grounding_without_backfill():
    row = response_row(); row.id = 'pure-id'; row.created_at = datetime.now(timezone.utc)
    row.data.update(request_id='pure-request')
    row.data['frozen']['context'] = {'mode': 'query'}
    row.data['validated'] = validate()
    before = copy.deepcopy(row.data)
    public = service.row_view(row)
    assert public['source_grounding'] == row.data['validated']['source_grounding']
    assert public['original_question'] == QUESTION and public['candidate_hash'] == digest(row.data['validated'])
    assert row.data == before
    row.data['validated'] = v5.validate_candidate(candidate(), QUESTION, catalog(), EVIDENCE)
    assert service.row_view(row)['source_grounding'] is None

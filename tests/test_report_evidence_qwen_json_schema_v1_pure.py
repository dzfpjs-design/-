"""Root-run pure native helper checks using only public synthetic wire values.

No network, DB, file persistence, paid contract or provider acceptance is mocked.
Successful parsing cannot prove original HTTP capture, actor/source authority,
complete Map coverage, budget, full semantic reconciliation or model quality.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

from generic_agents import report_evidence_qwen_json_schema_v1 as subject
STAGES = ('map-v7', 'reduce-v5')
TEXT = ' 合成公开文字："保留引号"。\nC:\\public\\reports ＋+。 '
CLAIM = {'kind': '职责', 'statement': TEXT, 'span_refs': ['w:p1/s1'], 'status': 'explicit', 'explanation': ''}
USAGE = {'prompt_tokens': 8, 'completion_tokens': 12, 'total_tokens': 20}


def raw(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False, **kwargs).encode('utf-8')


def wire(stage):
    if stage == 'map-v7':
        from generic_agents.report_evidence_map_slots_v5 import SLOT_NAMES
        value = {'schema': 'report-evidence-map-slots-v5', 'slots': {name: {'state': 'absent'} for name in SLOT_NAMES}}
        value['slots']['slot_003'] = {'state': 'present', 'claim': copy.deepcopy(CLAIM)}
        return value
    def step(index):
        return {'name': '公开步骤' + str(index), 'purpose': TEXT, 'agent': '人工确认', 'role': TEXT,
            'output': '合成记录', 'next_index': {'kind': 'step', 'index': 1} if index == 0 else {'kind': 'end'},
            'branch_condition': '', 'else_index': {'kind': 'end'}, 'citations': [], 'inference_reason': '合成协议用例'}
    return {'result': {'schema': 'report-evidence-candidate-wire-v6', 'source_hash': 'a' * 64,
        'catalog_hash': 'b' * 64, 'domain': TEXT, 'global_claims': [copy.deepcopy(CLAIM)], 'objects': [],
        'relationships': [], 'businesses': [{'operation': 'upsert', 'original_name': '', 'name': '合成业务',
            'trigger': TEXT, 'output': TEXT, 'steps': [step(0), step(1)], 'citations': [],
            'inference_reason': '合成协议用例', 'participants': [], 'interactions': [], 'conditions': [], 'coordination': []}],
        'questions': ['公开合成协议，不代表实际报告质量。']}, 'reconciliation': [
            {'input_position': 0, 'disposition': 'kept', 'output_kind': 'global_claims', 'output_index': 0,
             'reason': '合成输入显式保留，不代表完整来源证明。'}]}


def schema(stage): return subject._model(stage).model_json_schema()


def options(stage):
    return {'model': subject.MODEL, 'max_tokens': 8192 if stage == 'map-v7' else 32768,
        'enable_thinking': False, 'stream': False, 'n': 1}


def envelope(stage, content=None):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': raw(wire(stage)).decode('utf-8') if content is None else content}}], 'usage': copy.deepcopy(USAGE)}


def inspect(body, stage, **kwargs):
    return subject.inspect_response(body, 200, stage, response_complete=True, http_content_encoding=None, **kwargs)


def parse(body, stage, **kwargs):
    return subject.parse_arguments(body, 200, stage, response_complete=True, http_content_encoding=None, **kwargs)


def reorder(value):
    if type(value) is dict: return {key: reorder(item) for key, item in reversed(list(value.items()))}
    if type(value) is list: return [reorder(item) for item in value]
    return value


class NativeQwenHelperPureTests(unittest.TestCase):
    def setUp(self):
        def forbidden(*args, **kwargs): raise AssertionError('Native helper is pure; IO is forbidden')
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo', 'subprocess.Popen'):
            manager = patch(target, side_effect=forbidden); manager.start(); self.addCleanup(manager.stop)

    def test_exact_stages_and_options_have_no_tool_or_legacy_fallback(self):
        for stage in STAGES:
            value = subject.strict_options(schema(stage), options(stage), stage)
            self.assertEqual(set(value), {'delivery', 'endpoint', 'schema_proof', 'options'})
            self.assertEqual(value['endpoint'], 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions')
            self.assertEqual(set(value['options']), {'model', 'max_tokens', 'enable_thinking', 'stream', 'n', 'response_format'})
            self.assertEqual(value['options']['response_format']['json_schema']['schema'], schema(stage))
            self.assertIs(value['options']['response_format']['json_schema']['strict'], True)
            for field, invalid in [('model', 'qwen-plus'), ('n', True), ('n', 2), ('stream', True),
                    ('enable_thinking', True), ('max_tokens', True), ('max_tokens', 127),
                    ('max_tokens', subject.stage_policy(stage)['max_output_tokens'] + 1)]:
                changed = options(stage); changed[field] = invalid
                with self.subTest(stage=stage, field=field), self.assertRaises(ValueError):
                    subject.strict_options(schema(stage), changed, stage)
            changed = options(stage); changed['tools'] = []
            with self.assertRaises(ValueError): subject.strict_options(schema(stage), changed, stage)
        upper = options('reduce-v5'); upper['max_tokens'] = 131072
        self.assertEqual(subject.strict_options(schema('reduce-v5'), upper, 'reduce-v5')['options']['max_tokens'], 131072)
        for unknown in ('map-v6', 'reduce-v4', 'map-v8', None, True):
            with self.assertRaises(ValueError): subject.stage_policy(unknown)

    def test_complete_original_schema_identity_and_proof_are_cold_rebuilt(self):
        for stage in STAGES:
            local = schema(stage); before = copy.deepcopy(local)
            proof = subject.identity_schema_proof(local, stage)
            self.assertEqual(set(proof), {'schema', 'delivery', 'stage', 'payload_origin', 'local_schema_hash',
                'provider_schema', 'provider_schema_hash', 'proof_hash'})
            self.assertEqual(proof['payload_origin'], 'message.content')
            self.assertEqual(proof['local_schema_hash'], proof['provider_schema_hash'])
            self.assertEqual(proof['provider_schema'], local)
            self.assertEqual(subject.check_schema_proof(reorder(proof), reorder(local), stage), proof)
            self.assertEqual(local, before)
            for mode in ('changed_local', 'changed_proof', 'old_delivery', 'extra'):
                changed, input_schema = copy.deepcopy(proof), copy.deepcopy(local)
                if mode == 'changed_local': input_schema['title'] += 'changed'
                elif mode == 'changed_proof': changed['provider_schema']['title'] += 'changed'
                elif mode == 'old_delivery': changed['delivery'] = 'deepseek-strict-report-tool-v3'
                else: changed['unknown'] = True
                changed['provider_schema_hash'] = subject._hash(changed['provider_schema'])
                changed['proof_hash'] = subject._hash({k: v for k, v in changed.items() if k != 'proof_hash'})
                with self.subTest(stage=stage, mode=mode), self.assertRaises(ValueError):
                    subject.check_schema_proof(changed, input_schema, stage)

    def test_prepared_canonical_post_preserves_exact_source_and_does_not_repeat_schema(self):
        for stage in STAGES:
            local = schema(stage)
            messages = [{'role': 'system', 'content': subject._OLD_PREFIX + json.dumps(local, ensure_ascii=False)},
                {'role': 'system', 'content': '合成可信任务。'}, {'role': 'user', 'content': TEXT}]
            original = copy.deepcopy(messages)
            result = subject.prepare_request(messages, local, options(stage), stage)
            self.assertEqual(set(result), {'delivery', 'endpoint', 'schema_proof', 'request', 'body', 'body_sha256', 'body_bytes'})
            self.assertEqual(result['body'], subject._bytes(result['request']))
            self.assertEqual(result['body_sha256'], hashlib.sha256(result['body']).hexdigest())
            self.assertEqual(result['body_bytes'], len(result['body']))
            self.assertNotEqual(result['body_sha256'], subject._hash(result['request']['messages']))
            self.assertEqual(result['request']['messages'][1:], original[1:])
            self.assertEqual(result['request']['messages'][0]['content'], subject.system_prefix(stage))
            self.assertEqual(messages, original)
            again = subject.prepare_request(messages, reorder(local), reorder(options(stage)), stage)
            self.assertEqual(again['body'], result['body'])
            bad = copy.deepcopy(messages); bad[0]['content'] = subject._OLD_PREFIX + '{}'
            with self.assertRaises(ValueError): subject.prepare_request(bad, local, options(stage), stage)
            bad = copy.deepcopy(messages); bad[1]['content'] = [{'type': 'image_url'}]
            with self.assertRaises(ValueError): subject.prepare_request(bad, local, options(stage), stage)

    def test_native_stop_metadata_and_content_hashes_are_not_fabricated_tool_evidence(self):
        for stage in STAGES:
            body = raw(envelope(stage)); original = body[:]
            result = inspect(body, stage); metadata = result['metadata']
            self.assertEqual(set(result), {'metadata', 'arguments', 'usage', 'response_complete', 'http_content_encoding'})
            self.assertEqual(set(metadata), set(subject.METADATA_KEYS)); self.assertEqual(len(metadata), 19)
            self.assertEqual(metadata['finish_reason'], 'stop'); self.assertEqual(metadata['response_choice_count'], 1)
            self.assertEqual(metadata['tool_call_count'], 0)
            for key in ('tool_call_id', 'tool_name', 'tool_type', 'tool_calls_hash'): self.assertIsNone(metadata[key])
            content = result['arguments'].encode('utf-8')
            self.assertEqual(metadata['arguments_sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(metadata['arguments_bytes'], len(content))
            self.assertEqual(metadata['response_body_sha256'], hashlib.sha256(body).hexdigest())
            self.assertNotEqual(metadata['arguments_sha256'], metadata['response_body_sha256'])
            self.assertEqual(parse(body, stage), wire(stage)); self.assertEqual(body, original)

    def test_transport_facts_are_required_and_partial_or_encoded_content_cannot_be_free_parsed(self):
        body = raw(envelope('map-v7'))
        for function in (subject.inspect_response, subject.parse_arguments):
            with self.assertRaises(TypeError): function(body, 200, 'map-v7')
            with self.assertRaises(TypeError): function(body, 200, 'map-v7', response_complete=True)
            for invalid in (None, 1, 'true'):
                with self.assertRaises(ValueError): function(body, 200, 'map-v7', response_complete=invalid, http_content_encoding=None)
        for complete, encoding in ((False, None), (True, 'gzip'), (True, 'br'), (True, 'gzip,identity')):
            result = subject.inspect_response(body, 200, 'map-v7', response_complete=complete, http_content_encoding=encoding)
            self.assertFalse(result['metadata']['envelope_valid']); self.assertIsNone(result['arguments'])
            self.assertIsNone(result['metadata']['arguments_encoding'])
            with self.assertRaises(ValueError): subject.parse_arguments(body, 200, 'map-v7', response_complete=complete, http_content_encoding=encoding)
        for encoding in (True, [], '\x00', '\r\n'):
            with self.assertRaises(ValueError): subject.inspect_response(body, 200, 'map-v7', response_complete=True, http_content_encoding=encoding)
        for encoding in ('', 'identity', ' Identity '):
            result = subject.inspect_response(body, 200, 'map-v7', response_complete=True, http_content_encoding=encoding)
            self.assertTrue(result['metadata']['envelope_valid']); self.assertEqual(result['http_content_encoding'], encoding)

    def test_http400_length_multi_refusal_and_real_tools_are_preserved_but_never_adopted(self):
        for stage in STAGES:
            for mode in ('http', 'length', 'multi', 'no_choice', 'tool', 'function', 'refusal', 'null_content', 'role'):
                value = envelope(stage); status = 200; choice = value['choices'][0]; message = choice['message']
                if mode == 'http': status = 400
                elif mode == 'length': choice['finish_reason'] = 'length'
                elif mode == 'multi': value['choices'].append(copy.deepcopy(choice))
                elif mode == 'no_choice': value['choices'] = []
                elif mode == 'tool': message['tool_calls'] = [{'id': 'must_not_execute', 'function': {'name': 'tool'}}]
                elif mode == 'function': message['function_call'] = {'name': 'must_not_execute'}
                elif mode == 'refusal': message['refusal'] = 'Public synthetic refusal'
                elif mode == 'null_content': message['content'] = None
                else: message['role'] = 'system'
                body = raw(value); before = body[:]
                result = subject.inspect_response(body, status, stage, response_complete=True, http_content_encoding=None)
                self.assertFalse(result['metadata']['envelope_valid']); self.assertIsNone(result['arguments'])
                with self.subTest(stage=stage, mode=mode), self.assertRaises(ValueError):
                    subject.parse_arguments(body, status, stage, response_complete=True, http_content_encoding=None)
                self.assertEqual(body, before)

    def test_bad_original_json_duplicates_and_nonfinite_values_are_not_repaired(self):
        for stage in STAGES:
            for body in (b'not json', b'\xff', b'{"choices":[],"choices":[]}', b'{"x":NaN}'):
                self.assertFalse(inspect(body, stage)['metadata']['envelope_valid'])
                with self.assertRaises(ValueError): parse(body, stage)
            for text in ('{"broken":"inside "quote""}', '{"x":0,"x":1}', '{"x":Infinity}', '[]'):
                body = raw(envelope(stage, text))
                self.assertTrue(inspect(body, stage)['metadata']['envelope_valid'])
                with self.assertRaises(ValueError): parse(body, stage)

    def test_missing_bad_and_unsafe_usage_never_veto_otherwise_legal_original_content(self):
        deep = 'unsafe extra'
        for _ in range(52): deep = [deep]
        invalids = [None, [], {'prompt_tokens': True, 'completion_tokens': 12, 'total_tokens': 13},
            {'prompt_tokens': 8, 'completion_tokens': 12, 'total_tokens': 19}, {**USAGE, 'extra': '\x00'},
            {**USAGE, 'extra': '\ud800'}, {**USAGE, 'extra': deep}, {**USAGE, 'total_tokens': 2**40}]
        for usage in invalids:
            value = envelope('map-v7'); value['usage'] = usage
            body = json.dumps(value, ensure_ascii=True).encode('ascii')
            result = inspect(body, 'map-v7')
            self.assertTrue(result['metadata']['envelope_valid']); self.assertFalse(result['metadata']['usage_available'])
            self.assertEqual(result['usage'], usage); self.assertEqual(parse(body, 'map-v7'), wire('map-v7'))
        value = envelope('reduce-v5'); value.pop('usage'); body = raw(value)
        self.assertFalse(inspect(body, 'reduce-v5')['metadata']['usage_present'])
        self.assertEqual(parse(body, 'reduce-v5'), wire('reduce-v5'))

    def test_complete_local_map_fields_limits_and_original_indices_are_not_loosened(self):
        for mode in ('bad_kind', 'old_key', 'missing_slot', 'extra_slot', 'long_statement', 'missing_field'):
            value = wire('map-v7'); claim = value['slots']['slot_003']['claim']
            if mode == 'bad_kind': claim['kind'] = 'unregistered'
            elif mode == 'old_key': claim['key'] = 'forbidden'
            elif mode == 'missing_slot': del value['slots']['slot_080']
            elif mode == 'extra_slot': value['slots']['slot_081'] = {'state': 'absent'}
            elif mode == 'long_statement': claim['statement'] = '字' * 2001
            else: del claim['explanation']
            with self.assertRaises(ValueError): parse(raw(envelope('map-v7', raw(value).decode('utf-8'))), 'map-v7')
        for invalid in (True, -1, '0', 0.0):
            value = wire('reduce-v5'); value['result']['businesses'][0]['steps'][0]['next_index']['index'] = invalid
            with self.assertRaises(ValueError): parse(raw(envelope('reduce-v5', raw(value).decode('utf-8'))), 'reduce-v5')
        value = wire('reduce-v5'); del value['result']['businesses'][0]['steps'][0]['else_index']
        with self.assertRaises(ValueError): parse(raw(envelope('reduce-v5', raw(value).decode('utf-8'))), 'reduce-v5')

    def test_response_capacity_is_stage_specific_and_never_truncates_original(self):
        for stage in STAGES:
            value = envelope(stage); value['public_padding'] = 'x' * subject.stage_policy(stage)['max_response_bytes']
            body = raw(value); before = body[:]
            result = inspect(body, stage)
            self.assertIn('response_body_capacity', result['metadata']['envelope_errors'])
            self.assertEqual(result['metadata']['response_body_bytes'], len(body)); self.assertEqual(body, before)
            with self.assertRaises(ValueError): parse(body, stage)
        value = envelope('reduce-v5'); value['public_padding'] = 'x' * (300 * 1024)
        body = raw(value)
        self.assertEqual(parse(body, 'reduce-v5'), wire('reduce-v5'))
        self.assertFalse(inspect(body, 'map-v7')['metadata']['envelope_valid'])


if __name__ == '__main__': unittest.main()

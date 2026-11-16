"""Root-only public protocol export. No DB, credentials, provider or model IO.

Run against the reviewed public backend release with its src on PYTHONPATH.
Both output paths must be new files in existing directories. The fixture is
entirely invented protocol data, never an API/durability/quality assertion.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID


def forbidden(*args, **kwargs):
    raise RuntimeError('Public protocol export cannot connect or spawn')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def write_new(path, value):
    data = b'// Public synthetic protocol export; not a model/API result.\nexport default ' + encoded(value) + b';\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec-output', required=True, type=Path)
    parser.add_argument('--fixture-output', required=True, type=Path)
    args = parser.parse_args()
    if args.spec_output.absolute() == args.fixture_output.absolute():
        parser.error('Two distinct new output files are required')
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.getaddrinfo = forbidden
    subprocess.Popen = forbidden
    from generic_agents import report_evidence_preparation as prep
    from generic_agents import report_evidence_pipeline_v4 as adapter
    from generic_agents import report_evidence_strict_map as strict
    from generic_agents.contracts import digest
    from generic_agents.report_evidence_plan import build_evidence_source

    def uid(number):
        return str(UUID('10000000-0000-4000-8000-' + f'{number:012x}'))

    cap = SimpleNamespace(data={'execution': {'provider': 'deepseek',
        'model': 'deepseek-v4-pro', 'max_tokens': 8192, 'thinking_mode': 'disabled',
        'report_evidence_output': {'schema': 'report-evidence-output-v2',
            'map_transport': strict.DELIVERY, 'max_tokens': 131072}},
        'data_policy': {'redact_fields': []}})
    catalog = build_evidence_source('a' * 64,
        [['w:p1', ' 仅合成原文＋+；不代表真实报告。 '], ['w:p2', ''], ['o:p1', '合成OCR原文。']])
    preparation = prep.build_map_preparation(cap, catalog, {'object_names': []},
        ' 保留合成空白。 ', generation_version=prep.GENERATION_V4)
    prep.check_map_preparation(preparation, catalog, cap)
    requests, responses = [], []
    for position, chunk in enumerate(preparation['plan']['chunks']):
        request = prep.rebuild_map_request(preparation, catalog, cap, chunk['chunk_id'])
        requests.append({key: value for key, value in request.items() if key != 'request_body'})
        assert encoded(request['request']) == request['request_body']
        wire = {'schema': 'report-evidence-map-v3', 'claims': [{'kind': '职责',
            'statement': ' 合成声明＋+。 ', 'span_refs': [chunk['span_refs'][0]],
            'status': 'explicit', 'explanation': ''}]}
        arguments = json.dumps(wire, ensure_ascii=False, indent=2)
        envelope = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'id': 'synthetic_tool_' + str(position + 1), 'type': 'function',
             'function': {'name': strict.TOOL_NAME, 'arguments': arguments}}]}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}}
        body = json.dumps(envelope, ensure_ascii=True, indent=2).encode('utf-8')
        metadata = strict.inspect_strict_map_response(body, 200)['metadata']
        authorized = chunk
        dispatch = {'schema': 'report-evidence-v4-dispatch-v1', 'model_id': uid(5),
            'authorized': authorized, 'map_preparation_hash': preparation['preparation_hash'],
            'map_plan_hash': preparation['plan']['plan_hash'], 'request_hash': chunk['request_hash']}
        binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': uid(1),
            'workspace_id': uid(2), 'draft_id': uid(3), 'stage': chunk['chunk_id'],
            'reserved_call_id': uid(10 + position), 'source_hash': catalog['descriptor']['source_hash'],
            'catalog_hash': catalog['descriptor']['catalog_hash'],
            'generation_hash': digest({'published_model_hash': 'b' * 64,
                'contract': preparation['generation_contract']}),
            'dispatch_hash': digest(dispatch), 'messages_hash': chunk['messages_hash']}
        descriptor = {'binding': binding, 'binding_hash': digest(binding),
            'wire_sha256': hashlib.sha256(body.decode('utf-8', errors='surrogateescape')
                .encode('utf-8', errors='surrogatepass')).hexdigest()}
        policy = copy.deepcopy(preparation['generation_contract']['input_policy'])
        policy.pop('single_batch')
        projection = adapter.build_projection(wire, binding=binding, expected_binding=binding,
            descriptor=descriptor, metadata=metadata, evidence_profile=policy,
            catalog=catalog, allowed_span_refs=chunk['span_refs'])
        responses.append({'wire': wire, 'arguments': arguments, 'http_body': body.decode(),
            'metadata': metadata, 'descriptor': descriptor, 'projection': projection})
    spec = {'map': preparation['generation_contract'],
        'strict_prefix': strict.STRICT_MAP_SYSTEM_PREFIX, 'metadata_keys': list(strict.METADATA_KEYS),
        'delivery': strict.DELIVERY, 'endpoint': strict.ENDPOINT, 'tool_name': strict.TOOL_NAME}
    fixture = {'synthetic_only': True, 'account': {'user_id': uid(1), 'workspace_id': uid(2),
        'role': 'admin'}, 'model_id': uid(5), 'published_model_hash': 'b' * 64,
        'cap': cap.data, 'catalog': catalog, 'preparation': preparation,
        'requests': requests, 'responses': responses}
    spec_sha = write_new(args.spec_output, spec)
    fixture_sha = write_new(args.fixture_output, fixture)
    print(json.dumps({'schema': 'public-evidence-v4-export-v1', 'spec_sha256': spec_sha,
        'fixture_sha256': fixture_sha, 'synthetic_only': True}, sort_keys=True))


if __name__ == '__main__':
    main()

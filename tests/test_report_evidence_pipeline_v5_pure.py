"""Synthetic adapter unit checks, not provider/journal/authenticity evidence.

MemoryJournal returns known original synthetic bytes without filesystem IO.
Reduce's preparation boundary is an explicit unit stub that verifies the exact
collected Maps, frozen request and scope. The actual reduction-preparation
compiler and durable gateway/journal transaction need their separate tests.
Original argument parsing, Map projection and complete Reduce semantic checks
below are real product functions, not substituted success responses.
"""
import copy
import hashlib
import json
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from generic_agents import report_evidence_pipeline_v5 as subject
from generic_agents import report_evidence_journal as journal_v1
from generic_agents import report_evidence_journal_v2 as journal_v2
from generic_agents import report_evidence_map_slots_v5 as slots
from generic_agents import report_evidence_projection_v5 as map_projection
from generic_agents import report_evidence_reduction_wire_v6 as reduction_wire
from generic_agents import report_evidence_structured_tool_v2 as tool
from generic_agents.contracts import digest
from tests.test_report_evidence_wire_v3_pure import reduce_case, identity


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure adapter cannot connect or execute')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def profile(stage):
    return {'version': 'cloud-evidence-2026-10-17/' + stage, 'stage': stage,
        'max_input_bytes': 900000, 'transport_timeout_seconds': 600 if stage == 'reduce-v3' else 180,
        'max_output_tokens': 131072 if stage == 'reduce-v3' else 8192,
        'max_images': 0, 'context_token_bound': 1000000, 'full_report': True, 'long_context_verified': True}


class MemoryJournal:
    def __init__(self, pipeline, reduce):
        self.pipeline, self.reduce = pipeline, reduce
        self.JournalBinding = (journal_v2 if reduce else journal_v1).JournalBinding

    def read_response(self, root, binding, descriptor):
        key = binding['stage']
        assert root == ('synthetic-reduce-v2-root' if self.reduce else 'synthetic-map-v1-root')
        assert (key == 'reduce') is self.reduce
        assert binding == self.pipeline.bindings[key]
        assert descriptor == self.pipeline.responses[key]['descriptor']
        self.pipeline.trace.append(('read', key))
        return copy.deepcopy(self.pipeline.responses[key])


class Pipeline:
    """Only already-owned row/spec/receipt interfaces; no DB or authorization."""
    def __init__(self):
        candidate_v5, old = reduce_case()
        self.catalog, self.context = old['catalog'], old['context']
        self.trace, self.responses, self.bindings, self.authorized = [], {}, {}, {}
        self.cap = SimpleNamespace(data={'synthetic_cap_boundary': True})
        prep = {'generation_contract': {'schema': subject.GENERATION_V5}, 'preparation_hash': 'a' * 64,
            'context': copy.deepcopy(self.context), 'user_instruction': '',
            'plan': {'schema': 'report-evidence-read-plan-v2', 'plan_hash': 'b' * 64, 'chunks': []}}
        self.row = SimpleNamespace(id=identity(3), actor_id=identity(1), workspace_id=identity(2),
            data={'preview': {'body': {'model_id': identity(5)}, 'map_preparation': prep}, 'maps': []})
        self.states = {}
        for index, item in enumerate(old['map_inputs'], 1):
            key = 'map-' + str(index)
            wire = {'schema': slots.WIRE_SCHEMA, 'slots': {name: {'state': 'absent'} for name in slots.SLOT_NAMES}}
            wire['slots']['slot_003'] = {'state': 'present', 'claim': copy.deepcopy(item['wire']['claims'][0])}
            chunk = {'chunk_id': key, 'span_refs': copy.deepcopy(item['allowed_span_refs']),
                'messages_hash': digest({'messages': key}), 'request_hash': digest({'request': key})}
            prep['plan']['chunks'].append(chunk); self.authorized[key] = chunk
            self.states[key] = {'chunk_id': key, 'status': 'succeeded', 'inflight': False,
                'reserved_call_id': identity(10 + index)}
            self.row.data['maps'].append(self.states[key])
            self.install_response(key, wire)
            response = self.responses[key]
            inspected = tool.inspect_response(response['content'].encode('utf-8'), 200, 'map-v5')
            proof = map_projection.build_map_projection(wire, binding=self.bindings[key], expected_binding=self.bindings[key],
                descriptor=response['descriptor'], metadata=inspected['metadata'], evidence_profile=profile('map-v5'),
                catalog=self.catalog, allowed_span_refs=chunk['span_refs'])
            self.states[key].update(journal=copy.deepcopy(response['descriptor']), result_hash=digest(proof), result_count=1)
        self.reduce_wire = reduction_wire.encode_reduction_wire_v6(candidate_v5)
        self.states['reduce'] = {'status': 'running', 'inflight': True, 'reserved_call_id': identity(99), 'journal': None}
        self.install_response('reduce', self.reduce_wire)
        self.map_journal, self.reduce_journal = MemoryJournal(self, False), MemoryJournal(self, True)

    def _stage(self, row, key):
        assert row is self.row
        return self.states[key]

    def _descriptor(self, row, key):
        assert row is self.row
        return copy.deepcopy(self.responses[key]['descriptor'])

    def _journal_for(self, row, key):
        assert row is self.row
        return self.reduce_journal if key == 'reduce' else self.map_journal

    def _journal_root(self, row, key):
        assert row is self.row
        return 'synthetic-reduce-v2-root' if key == 'reduce' else 'synthetic-map-v1-root'

    def journal_binding(self, row, key, call_id):
        assert row is self.row and call_id == self.states[key]['reserved_call_id']
        return copy.deepcopy(self.bindings[key])

    def _spec(self, row, key):
        assert row is self.row
        return {'input_policy': {'single_batch': True, **profile('reduce-v3' if key == 'reduce' else 'map-v5')}}

    def _authorized(self, row, key):
        assert row is self.row
        return self.authorized[key]

    def _cap(self, preview):
        assert preview is self.row.data['preview']
        return self.cap

    def _check_receipt_identity(self, row, key, receipt):
        assert row is self.row
        if (receipt.get('call_id') != self.states[key]['reserved_call_id']
                or receipt.get('provider') != 'deepseek' or receipt.get('model') != 'deepseek-v4-pro'):
            raise ValueError('synthetic original receipt identity differs')
        self.trace.append(('identity', key))

    def install_response(self, key, wire, *, status=200, finish='tool_calls', choices=1, extra_space=0):
        reducing = key == 'reduce'; stage = 'reduce-v3' if reducing else 'map-v5'
        binding = {'schema': 'report-evidence-journal-binding-v' + ('2' if reducing else '1'),
            'actor_id': self.row.actor_id, 'workspace_id': self.row.workspace_id, 'draft_id': self.row.id,
            'stage': key, 'reserved_call_id': self.states[key]['reserved_call_id'],
            'source_hash': self.catalog['descriptor']['source_hash'], 'catalog_hash': self.catalog['descriptor']['catalog_hash'],
            'generation_hash': digest({'generation': stage}), 'dispatch_hash': digest({'dispatch': key}),
            'messages_hash': digest({'messages': key})}
        self.bindings[key] = binding
        arguments = ' ' * extra_space + json.dumps(wire, ensure_ascii=False, indent=2)
        body = json.dumps({'choices': [{'finish_reason': finish, 'message': {'tool_calls': [
            {'type': 'function', 'id': 'synthetic-' + key, 'function': {
                'name': tool.stage_policy(stage)['tool_name'], 'arguments': arguments}}]}}] * choices},
            ensure_ascii=False).encode('utf-8')
        inspected = tool.inspect_response(body, status, stage)
        receipt = {**inspected['metadata'], 'call_id': binding['reserved_call_id'],
            'provider': 'deepseek', 'model': 'deepseek-v4-pro', 'evidence_profile': profile(stage)}
        receipt_bytes = encoded(receipt)
        envelope = {'schema': 'report-evidence-journal-envelope-v' + ('2' if reducing else '1'),
            'binding': copy.deepcopy(binding), 'binding_hash': digest(binding), 'wire_kind': 'text',
            'encoding': 'utf-8-surrogatepass-v1', 'wire_bytes': len(body), 'wire_sha256': hashlib.sha256(body).hexdigest(),
            'receipt_status': 'saved', 'receipt_bytes': len(receipt_bytes), 'receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
            'consumable': True, 'quarantine_reasons': []}
        envelope_bytes = encoded(envelope)
        descriptor = {**envelope, 'schema': 'report-evidence-journal-descriptor-v' + ('2' if reducing else '1'),
            'envelope_bytes': len(envelope_bytes), 'envelope_sha256': hashlib.sha256(envelope_bytes).hexdigest()}
        descriptor['descriptor_hash'] = digest(descriptor)
        self.responses[key] = {'descriptor': descriptor, 'content': body.decode('utf-8'), 'receipt': receipt}


def reduce_boundary(pipeline, monkeypatch):
    """Explicit preparation unit boundary; cannot prove the real prep compiler."""
    maps = subject.map_inputs(pipeline, pipeline.row, pipeline.catalog)
    items = {f'map-{number}/claims/{index}': copy.deepcopy(claim)
        for number, item in enumerate(maps, 1) for index, claim in enumerate(item['projection']['claims'])}
    references = [item['allowed_span_refs'][0] for item in maps]
    values = {'scope': {'actor_id': pipeline.row.actor_id, 'workspace_id': pipeline.row.workspace_id, 'draft_id': pipeline.row.id},
        'map_inputs_hash': digest(maps), 'context': copy.deepcopy(pipeline.context),
        'input_order': list(items), 'input_items': items, 'allowed_span_refs': references}
    authorized = {'preparation_hash': 'e' * 64, 'map_inputs_hash': values['map_inputs_hash'],
        'input_order': list(items), 'request_hash': digest({'reduce_request': True}),
        'payload': {'spans': [[ref, pipeline.catalog['index'][ref]['text']] for ref in references]}}
    pipeline.authorized['reduce'] = copy.deepcopy(authorized)
    expected_prep = copy.deepcopy(pipeline.row.data['preview']['map_preparation'])
    expected_catalog = copy.deepcopy(pipeline.catalog)
    def gather(prep, catalog, cap, actual_maps):
        assert prep == expected_prep and catalog == expected_catalog and cap is pipeline.cap
        if digest(actual_maps) != values['map_inputs_hash']:
            raise ValueError('synthetic frozen map inputs changed')
        pipeline.trace.append(('gather', 'reduce'))
        return copy.deepcopy(values)
    def check(value, catalog, cap, prep, actual_maps):
        if encoded(value) != encoded(authorized): raise ValueError('synthetic frozen Reduce preparation changed')
        gather(prep, catalog, cap, actual_maps)
        pipeline.trace.append(('check-preparation', 'reduce'))
        return value
    monkeypatch.setitem(sys.modules, 'generic_agents.report_evidence_reduction_v3',
        SimpleNamespace(reduction_inputs_v3=gather, check_reduction_preparation=check))
    pipeline.trace.clear()
    return values


def test_map_read_precedes_metadata_identity_and_argument_parse_without_mutating_inputs():
    pipeline = Pipeline(); before = copy.deepcopy((pipeline.row, pipeline.responses, pipeline.bindings))
    proof = subject.stage_projection(pipeline, pipeline.row, 'map-1', pipeline.catalog)
    assert pipeline.trace == [('read', 'map-1'), ('identity', 'map-1')]
    assert proof['schema'] == map_projection.MAP_PROJECTION and proof['slot_positions'] == [2]
    assert pipeline.row.__dict__ == before[0].__dict__ and pipeline.responses == before[1] and pipeline.bindings == before[2]


def test_inspect_never_calls_semantic_argument_parser_and_invalid_envelope_stays_invalid(monkeypatch):
    pipeline = Pipeline(); pipeline.install_response('map-1', {'synthetic': 'invalid wire'}, finish='length')
    loaded = pipeline.responses['map-1']
    def forbidden(*args, **kwargs): raise AssertionError('inspect cannot parse arguments')
    monkeypatch.setattr(subject, 'parse_arguments', forbidden)
    _, inspected = subject.inspect_response(pipeline, pipeline.row, 'map-1', loaded, loaded['descriptor'])
    assert inspected['metadata']['envelope_valid'] is False and inspected['arguments'] is None


@pytest.mark.parametrize('status,finish,choices', [(400, 'tool_calls', 1), (200, 'length', 1), (200, 'stop', 1), (200, 'tool_calls', 2)])
def test_parsed_response_does_not_adopt_http_error_length_or_multi_output(status, finish, choices):
    pipeline = Pipeline(); wire = slots.encode({'schema': 'report-evidence-map-v3', 'claims': []}, [])
    pipeline.install_response('map-1', wire, status=status, finish=finish, choices=choices)
    loaded = pipeline.responses['map-1']
    with pytest.raises(ValueError): subject.parsed_response(pipeline, pipeline.row, 'map-1', loaded, loaded['descriptor'])


def test_complete_map_inputs_are_closed_and_every_original_journal_and_stage_hash_are_rechecked():
    pipeline = Pipeline(); maps = subject.map_inputs(pipeline, pipeline.row, pipeline.catalog)
    assert len(maps) == 3
    assert pipeline.trace == [(kind, key) for key in ['map-1', 'map-2', 'map-3'] for kind in ['read', 'identity']]
    for index, item in enumerate(maps, 1):
        assert set(item) == {'wire', 'binding', 'expected_binding', 'descriptor', 'metadata', 'evidence_profile', 'allowed_span_refs', 'projection'}
        assert item['binding']['stage'] == f'map-{index}'
        assert digest(item['projection']) == pipeline.states[f'map-{index}']['result_hash']


@pytest.mark.parametrize('mode', ['pending', 'unknown', 'inflight', 'missing', 'extra', 'order', 'hash', 'count_bool', 'journal'])
def test_partial_duplicate_reordered_or_changed_maps_never_reach_reduction(mode):
    pipeline = Pipeline(); stages = pipeline.row.data['maps']
    if mode in {'pending', 'unknown'}: stages[1]['status'] = mode
    elif mode == 'inflight': stages[1]['inflight'] = True
    elif mode == 'missing': stages.pop()
    elif mode == 'extra': stages.append(copy.deepcopy(stages[-1]))
    elif mode == 'order': stages.reverse()
    elif mode == 'hash': stages[1]['result_hash'] = 'f' * 64
    elif mode == 'count_bool': stages[1]['result_count'] = True
    else: stages[1]['journal'] = None
    with pytest.raises(ValueError): subject.map_inputs(pipeline, pipeline.row, pipeline.catalog)


@pytest.mark.parametrize('mode', ['receipt_scope', 'metadata', 'profile', 'descriptor_binding', 'descriptor_hash', 'body', 'loaded_descriptor', 'generation'])
def test_changed_original_identity_metadata_profile_or_bytes_fail_closed(mode):
    pipeline = Pipeline(); loaded = pipeline.responses['map-1']
    if mode == 'receipt_scope': loaded['receipt']['call_id'] = identity(88)
    elif mode == 'metadata': loaded['receipt']['tool_name'] = 'submit_report_evidence_reduction'
    elif mode == 'profile': loaded['receipt']['evidence_profile']['stage'] = 'map-v4'
    elif mode == 'descriptor_binding':
        loaded['descriptor']['binding']['actor_id'] = identity(88)
        loaded['descriptor']['descriptor_hash'] = digest({key: value for key, value in loaded['descriptor'].items() if key != 'descriptor_hash'})
    elif mode == 'descriptor_hash': loaded['descriptor']['descriptor_hash'] = 'f' * 64
    elif mode == 'body': loaded['content'] += ' '
    elif mode == 'loaded_descriptor': loaded = {**copy.deepcopy(loaded), 'descriptor': {}}
    else: pipeline.row.data['preview']['map_preparation']['generation_contract']['schema'] = 'report-evidence-map-generation-v4'
    descriptor = pipeline.responses['map-1']['descriptor']
    with pytest.raises(ValueError): subject.parsed_response(pipeline, pipeline.row, 'map-1', loaded, descriptor)


def test_reduce_reads_journal_v2_then_complete_semantics_and_compact_wrapper_bind_all_hashes(monkeypatch):
    pipeline = Pipeline(); values = reduce_boundary(pipeline, monkeypatch)
    proof = subject.stage_projection(pipeline, pipeline.row, 'reduce', pipeline.catalog)
    assert proof['schema'] == subject.REDUCE_TOOL_PROJECTION
    assert proof['binding']['schema'] == 'report-evidence-journal-binding-v2'
    assert ('check-preparation', 'reduce') in pipeline.trace
    assert pipeline.trace.index(('check-preparation', 'reduce')) < pipeline.trace.index(('read', 'reduce'))
    assert proof['map_inputs_hash'] == values['map_inputs_hash']
    assert proof['arguments_sha256'] == proof['reduction_projection_metadata']['wire_bytes_sha256']
    assert proof['arguments_projection_hash'] == proof['reduction_projection_metadata']['projection_hash']
    assert proof['result']['schema'] == reduction_wire.CANDIDATE
    assert 'result' not in proof['reduction_projection_metadata'] and 'reconciliation' not in proof['reduction_projection_metadata']
    assert len(proof['reconciliation']) == len(values['input_order'])
    assert proof['projection_hash'] == digest({key: value for key, value in proof.items() if key != 'projection_hash'})
    assert subject.stage_projection(pipeline, pipeline.row, 'reduce', pipeline.catalog) == proof


def test_reduce_accepts_its_explicit_larger_journal_without_widening_map_bytes(monkeypatch):
    pipeline = Pipeline(); reduce_boundary(pipeline, monkeypatch)
    pipeline.install_response('reduce', pipeline.reduce_wire, extra_space=270000)
    assert pipeline.responses['reduce']['descriptor']['wire_bytes'] > journal_v1.MAX_RESPONSE_BYTES
    proof = subject.stage_projection(pipeline, pipeline.row, 'reduce', pipeline.catalog)
    assert proof['http_body_bytes'] > 256 * 1024
    assert journal_v1.MAX_RESPONSE_BYTES == 256 * 1024


@pytest.mark.parametrize('mode', ['scope', 'frozen_preparation', 'union', 'v1_descriptor', 'reconcile', 'missing_map'])
def test_reduce_scope_frozen_preparation_evidence_old_journal_and_semantic_corruption_are_rejected(mode, monkeypatch):
    pipeline = Pipeline(); values = reduce_boundary(pipeline, monkeypatch)
    if mode == 'scope': values['scope']['actor_id'] = identity(88)
    elif mode == 'frozen_preparation': pipeline.authorized['reduce']['preparation_hash'] = 'f' * 64
    elif mode == 'union': values['allowed_span_refs'] = ['w:p1/s1']
    elif mode == 'v1_descriptor':
        descriptor = pipeline.responses['reduce']['descriptor']; descriptor['schema'] = 'report-evidence-journal-descriptor-v1'
        descriptor['descriptor_hash'] = digest({key: value for key, value in descriptor.items() if key != 'descriptor_hash'})
    elif mode == 'reconcile':
        broken = copy.deepcopy(pipeline.reduce_wire); broken['reconciliation'].pop()
        pipeline.install_response('reduce', broken)
    else: pipeline.states['map-3']['status'] = 'response_unknown'
    with pytest.raises(ValueError): subject.stage_projection(pipeline, pipeline.row, 'reduce', pipeline.catalog)


def test_dispatch_binds_full_plan_and_request_and_unknown_versions_do_not_upgrade():
    pipeline = Pipeline(); preview = pipeline.row.data['preview']
    value = subject.dispatch(preview, 'map-1', pipeline.authorized['map-1'])
    assert value['map_plan_hash'] == preview['map_preparation']['plan']['plan_hash']
    assert value['map_preparation_hash'] == preview['map_preparation']['preparation_hash']
    assert value['request_hash'] == pipeline.authorized['map-1']['request_hash']
    old = copy.deepcopy(preview); old['map_preparation']['generation_contract']['schema'] = 'report-evidence-map-generation-v4'
    assert subject.applies(old) is False
    with pytest.raises(ValueError): subject.dispatch(old, 'map-1', pipeline.authorized['map-1'])

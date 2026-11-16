"""Pure contract/isolation tests; native model execution is a separate Root-run gate."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from generic_agents import local_sound as sound

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def runner():
    return module(ROOT / 'src/generic_agents/local_sound_runner.py', '_sound_pure_runner')


def audio(samples=4000):
    return {'sample_rate': 16000, 'channels': 1, 'samples': samples, 'duration_seconds': samples / 16000,
            'selected_channel': 'mono', 'rms': 0.0, 'clipped_fraction': 0.0, 'peak': 0.0,
            'silence_detected': True, 'channel_policy': 'explicit', 'noise_quality': 'not_measured', 'source_time': 'unknown'}


def result(samples=4000):
    labels = sound.RECIPE['labels']
    windows = []
    start = 0
    index = 0
    while True:
        end = min(samples, start + 15600)
        scores = [0.0] * 521
        scores[76] = .75 if index == 0 else .25
        order = sorted(range(521), key=lambda i: (-scores[i], i))[:5]
        windows.append({'index': index, 'start_sample': start, 'end_sample': end, 'padded_samples': 15600 - (end - start),
                        'start_seconds': start / 16000, 'end_seconds': end / 16000, 'scores': scores,
                        'top_classes': [{'class_index': i, 'label': labels[i], 'score': scores[i]} for i in order]})
        if start + 15600 >= samples: break
        start += 7680; index += 1
    return {'schema': 'local-environment-sound-v1', 'deployment': 'local', 'backend': 'cpu', 'cloud_calls': 0,
            'input_sha256': hashlib.sha256(b'original').hexdigest(), 'recipe_hash': sound.RECIPE_HASH,
            'model': {'pack_id': sound.PACK, 'model_sha256': sound.RECIPE['files'][0]['sha256'],
                      'labels_sha256': sound.RECIPE['files'][1]['sha256'], 'labels_count': 521, 'labels': labels.copy(),
                      'native_input': {'name': 'waveform_binary', 'shape': [15600], 'dtype': 'float32'},
                      'native_output': {'name': 'tower0/network/layer32/final_output', 'shape': [1, 521], 'dtype': 'float32'},
                      'metadata': {k: sound.RECIPE['license_evidence'][k] for k in ['name', 'author', 'version', 'license']},
                      'quantized_tensors': 89, 'weight_precision': 'internally_quantized_not_all_fp32',
                      'runtime_versions': {**sound.RECIPE['environment'], 'flatbuffers': 'test-version'}},
            'audio': {**audio(samples), 'analysis_sample_rate': 16000, 'analysis_samples': samples,
                      'resampling': 'original_16000_no_resample'},
            'classification': {'sample_rate': 16000, 'window_samples': 15600, 'hop_samples': 7680, 'threshold': .5,
                               'top_k': 5, 'score_semantics': 'uncalibrated_classification_score',
                               'timing': 'analysis_windows_not_event_boundaries', 'inference_performed': True,
                               'all_samples_covered': True, 'vad_used': False, 'asr_used': False, 'domain_quality': 'not_verified',
                               'windows': windows, 'candidates': [{'class_index': 76, 'label': labels[76], 'max_score': .75, 'window_indices': [0]}]},
            'elapsed_seconds': 1.0}


@pytest.mark.parametrize('samples,expected', [(1, 1), (4000, 1), (15600, 1), (15601, 2), (23280, 2), (960000, 124)])
def test_complete_window_coverage_stops_at_first_covering_window(runner, samples, expected):
    windows = list(runner.window_positions(samples))
    assert len(windows) == expected
    assert windows[0][0] == 0 and windows[-1][1] == samples
    assert all(b[0] - a[0] == 7680 and b[0] <= a[1] for a, b in zip(windows, windows[1:]))
    assert all(end - start == 15600 for start, end in windows[:-1])
    assert len(windows) == 1 or windows[-2][1] < samples


@pytest.mark.parametrize('samples', [0, -1, True, 960001, 1.5])
def test_bad_or_overlong_analysis_is_rejected_not_truncated(runner, samples):
    with pytest.raises(ValueError): list(runner.window_positions(samples))


def test_recipe_pins_real_shape_assets_labels_and_code():
    assert sound.PACK == 'yamnet-mediapipe-v1'
    assert sound.RECIPE['parameters']['native_input_shape'] == [15600]
    assert sound.RECIPE['parameters']['native_output_shape'] == [1, 521]
    assert sound.RECIPE['files'][0]['bytes'] == 4126810
    assert sound.RECIPE['files'][0]['sha256'] == '4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf'
    assert hashlib.sha256(('\n'.join(sound.RECIPE['labels']) + '\n').encode()).hexdigest() == sound.RECIPE['files'][1]['sha256']
    assert sound.file_sha(sound.RUNNER) == sound.RECIPE['runner_sha256']
    assert sound.file_sha(sound.DECODER) == sound.RECIPE['decoder_sha256']
    assert sound.file_sha(ROOT / 'scripts/prepare_local_sound.py') == sound.RECIPE['runtime_preparer_sha256']
    assert sound.RECIPE['parameters']['amplitude_policy'] == 'reject_out_of_range_no_clipping'


def test_complete_silent_clip_still_has_real_inference_and_original_scores():
    saved = result(16000)
    assert sound.validate(saved, b'original', {'channel': 'mono', 'threshold': .5}) is saved
    assert saved['audio']['silence_detected'] is True
    assert saved['classification']['inference_performed'] is True
    assert len(saved['classification']['windows']) == 2
    assert all(len(w['scores']) == 521 for w in saved['classification']['windows'])


@pytest.mark.parametrize('path,value', [
    (('input_sha256',), '0' * 64), (('recipe_hash',), '0' * 64), (('cloud_calls',), True),
    (('model', 'native_input', 'shape'), [1, 15600]), (('model', 'quantized_tensors'), 0),
    (('model', 'metadata', 'author'), 'unverified'), (('model', 'runtime_versions', 'ai-edge-litert'), '0.0'),
    (('classification', 'all_samples_covered'), False), (('classification', 'vad_used'), True),
    (('classification', 'asr_used'), True), (('classification', 'timing'), 'precise_event_boundaries'),
    (('classification', 'domain_quality'), 'verified'), (('audio', 'source_time'), 'upload_is_recording_time'),
    (('audio', 'analysis_samples'), 4001), (('elapsed_seconds',), float('inf')),
])
def test_typed_result_rejects_scope_shape_quality_or_coverage_forgery(path, value):
    saved = result()
    target = saved
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError): sound.validate(saved, b'original', {'channel': 'mono', 'threshold': .5})


@pytest.mark.parametrize('kind', ['missing_tail', 'extra_tail', 'gap', 'wrong_padding', 'nan_score', 'bool_score',
                                  'not_float32', 'missing_class', 'wrong_top_order', 'wrong_label', 'wrong_candidate'])
def test_full_raw_scores_and_candidate_derivation_are_checked(kind):
    saved = result(16000)
    rows = saved['classification']['windows']
    if kind == 'missing_tail': rows.pop()
    elif kind == 'extra_tail': rows.append(copy.deepcopy(rows[-1]))
    elif kind == 'gap': rows[1]['start_sample'] += 1
    elif kind == 'wrong_padding': rows[1]['padded_samples'] += 1
    elif kind == 'nan_score': rows[0]['scores'][0] = float('nan')
    elif kind == 'bool_score': rows[0]['scores'][0] = True
    elif kind == 'not_float32': rows[0]['scores'][0] = .1
    elif kind == 'missing_class': rows[0]['scores'].pop()
    elif kind == 'wrong_top_order': rows[0]['top_classes'].reverse()
    elif kind == 'wrong_label': saved['model']['labels'][76] = 'made_up'
    elif kind == 'wrong_candidate': saved['classification']['candidates'][0]['window_indices'].append(1)
    with pytest.raises(ValueError): sound.validate(saved, b'original', {'channel': 'mono', 'threshold': .5})


@pytest.mark.parametrize('value', [{'channel': 'auto', 'threshold': .5}, {'channel': 'mono', 'threshold': True},
                                   {'channel': 'mono', 'threshold': 0}, {'channel': 'mono', 'threshold': 1},
                                   {'channel': 'mono', 'threshold': float('nan')},
                                   {'channel': 'mono', 'threshold': .5, 'language': 'auto'}])
def test_configuration_never_infers_channel_or_accepts_asr_configuration(value):
    with pytest.raises(ValueError): sound.configuration(value)


def test_multichannel_metadata_requires_explicit_valid_choice():
    meta = audio(); meta.update(channels=2)
    with pytest.raises(ValueError): sound.validate_metadata(meta, 'mono')
    meta['selected_channel'] = '2'
    assert sound.validate_metadata(meta, '2') is meta
    meta['selected_channel'] = '3'
    with pytest.raises(ValueError): sound.validate_metadata(meta, '3')
    meta['selected_channel'] = 'mix'
    assert sound.validate_metadata(meta, 'mix') is meta


def test_metadata_child_needs_no_model_and_receives_no_credentials(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'must-not-pass')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-pass')
    monkeypatch.setenv('HTTPS_PROXY', 'must-not-pass')
    monkeypatch.setattr(sound, '_runtime_ready', lambda selected: seen.update(runtime_checked=True))
    monkeypatch.setattr(sound, 'ready', lambda **kw: pytest.fail('metadata must not require any model'))
    class Child:
        pid = 123; returncode = 0
        def __init__(self, command, **kwargs):
            seen.update(command=command, env=kwargs['env'])
            kwargs['stdout'].write(json.dumps({'schema': 'local-sound-metadata-v1',
                'input_sha256': hashlib.sha256(b'original').hexdigest(), 'audio': audio()}).encode())
        def poll(self): return 0
        def wait(self, timeout): return 0
    monkeypatch.setattr(sound.subprocess, 'Popen', Child)
    selected = sound.SoundPaths(tmp_path, tmp_path / 'python', tmp_path / 'missing_weights')
    assert sound.metadata(b'original', _paths=selected) == audio()
    assert seen['runtime_checked'] and '--weights' not in seen['command']
    assert '-I' in seen['command'] and 'metadata' in seen['command']
    assert not {'DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY', 'HTTPS_PROXY', 'PYTHONPATH'} & seen['env'].keys()
    assert seen['env']['HOME'] != str(Path.home()) and seen['env']['OMP_NUM_THREADS'] == '1'


def test_lease_or_cancel_guard_kills_child_and_never_accepts_late_result(monkeypatch, tmp_path):
    children = []
    calls = []
    class Cancelled(Exception): pass
    class Child:
        pid = 123; returncode = None
        def __init__(self, *args, **kwargs): self.terminated = False; children.append(self)
        def poll(self): return None
        def terminate(self): self.terminated = True
        def wait(self, timeout): self.returncode = -15; return -15
    def guard():
        calls.append(1)
        if len(calls) >= 3: raise Cancelled()
    monkeypatch.setattr(sound, '_runtime_ready', lambda selected: None)
    monkeypatch.setattr(sound.subprocess, 'Popen', Child)
    with pytest.raises(Cancelled):
        sound.metadata(b'original', guard=guard, _paths=sound.SoundPaths(tmp_path, tmp_path/'python', tmp_path/'weights'))
    assert len(children) == 1 and children[0].terminated


def test_read_regular_rejects_links_and_hash_changes(runner, tmp_path):
    path = tmp_path / 'file'; path.write_bytes(b'original')
    assert runner.read_file(path, 100, hashlib.sha256(b'original').hexdigest(), 8) == b'original'
    with pytest.raises(ValueError): runner.read_file(path, 100, '0'*64)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(OSError): runner.read_file(link, 100)


def test_runtime_status_is_read_only_and_not_a_model_or_quality_claim(monkeypatch):
    monkeypatch.setattr(sound, 'paths', lambda: SimpleNamespace())
    monkeypatch.setattr(sound, '_runtime_ready', lambda selected: None)
    assert sound.runtime_available() is True
    def missing(selected): raise ValueError('runtime missing')
    monkeypatch.setattr(sound, '_runtime_ready', missing)
    assert sound.runtime_available() is False


def test_runtime_install_record_is_immutable_and_uses_only_fixed_new_environment(tmp_path):
    bootstrap = module(ROOT / 'scripts/prepare_local_sound.py', '_sound_bootstrap_pure')
    assert bootstrap.REQ == ['ai-edge-litert==2.3.0', 'numpy==2.5.3', 'av==16.1.0']
    path = tmp_path / 'receipt'
    bootstrap.publish(path, b'first')
    bootstrap.publish(path, b'first')
    with pytest.raises(ValueError): bootstrap.publish(path, b'changed')
    assert path.read_bytes() == b'first'


@pytest.fixture
def original_installation():
    """An IO-spy for immutable chain selection, not real installation/PG proof."""
    from generic_agents.models import SpecialistInstallation, Job, Audit
    p = SimpleNamespace(workspace_id='workspace', user_id='different-reviewer')
    installation = SimpleNamespace(id='installation', workspace_id=p.workspace_id, pack_id=sound.PACK, status='ready',
        data={'generation': 1, 'recipe_hash': sound.RECIPE_HASH, 'backend': 'cpu', 'source': 'official', 'job_id': 'job',
              'artifact_hashes': {x['name']: x['sha256'] for x in sound.RECIPE['files']},
              'smoke': {'inference_performed': True, 'runner_sha256': sound.RECIPE['runner_sha256']}})
    job = SimpleNamespace(id='job', workspace_id=p.workspace_id, kind='install_specialist', status='succeeded',
        idempotency_key='specialist-install:installation:1', data={'installation_id': installation.id, 'generation': 1, 'actor_id': 'original-installer'})
    queued = SimpleNamespace(id='queued', workspace_id=p.workspace_id, action='specialist.install.queued', object_id=installation.id,
        actor_id='original-installer', data={'source': 'official', 'recipe_hash': sound.RECIPE_HASH, 'installation_id': installation.id,
                                           'generation': 1, 'job_id': job.id, 'backend': 'cpu'})
    completed = SimpleNamespace(id='ready', workspace_id=p.workspace_id, action='sound.install.ready', object_id=installation.id,
        actor_id='original-installer', data={'recipe_hash': sound.RECIPE_HASH, 'generation': 1,
            'artifact_hashes': installation.data['artifact_hashes'].copy(), 'engineering_probe_only': True})
    class IO:
        def __init__(self): self.jobs = [job]; self.queued = [queued]; self.completed = [completed]; self.queries = []
        def scalar(self, statement):
            self.queries.append(statement)
            assert statement.column_descriptions[0]['entity'] is SpecialistInstallation
            return installation
        def scalars(self, statement):
            self.queries.append(statement)
            entity = statement.column_descriptions[0]['entity']
            if entity is Job: return self.jobs
            assert entity is Audit
            params = statement.compile().params.values()
            if 'specialist.install.queued' in params: return self.queued
            assert 'sound.install.ready' in params
            return self.completed
    return SimpleNamespace(p=p, installation=installation, job=job, queued=queued, completed=completed,
                           db=IO(), trace=sound.trace(installation))


def test_historical_installation_trace_uses_original_actor_job_and_audits_not_current_state(original_installation, monkeypatch):
    s = original_installation
    before = sound.checked_trace(s.db, s.p, s.trace)['proof']
    s.installation.status = 'failed'
    s.installation.data = {'generation': 2, 'recipe_hash': 'later-recipe', 'source': 'prepared_local'}
    monkeypatch.setattr(sound, 'ready', lambda **kw: pytest.fail('historical trace must not reload current model'))
    assert sound.checked_trace(s.db, s.p, s.trace)['proof'] == before
    assert before['installer_id'] == 'original-installer' != s.p.user_id
    assert before['install_job_id'] == s.job.id and before['ready_audit_id'] == s.completed.id
    assert all(not statement._for_update_arg for statement in s.db.queries)
    with pytest.raises(sound.HTTPException) as error: sound.checked_trace(s.db, s.p, s.trace, live=True)
    assert error.value.status_code == 409


@pytest.mark.parametrize('damage', ['missing_job', 'duplicate_job', 'failed_job', 'wrong_key', 'wrong_job_actor', 'bool_job_generation',
                                    'missing_queue', 'duplicate_queue', 'wrong_queue_actor', 'wrong_queue_job', 'bool_queue_generation',
                                    'missing_ready', 'duplicate_ready', 'wrong_ready_actor', 'wrong_artifacts', 'false_probe'])
def test_installation_trace_rejects_missing_or_rebound_original_chain(original_installation, damage):
    s = original_installation
    if damage == 'missing_job': s.db.jobs = []
    elif damage == 'duplicate_job': s.db.jobs.append(s.job)
    elif damage == 'failed_job': s.job.status = 'failed'
    elif damage == 'wrong_key': s.job.idempotency_key += ':other'
    elif damage == 'wrong_job_actor': s.job.data['actor_id'] = 'other'
    elif damage == 'bool_job_generation': s.job.data['generation'] = True
    elif damage == 'missing_queue': s.db.queued = []
    elif damage == 'duplicate_queue': s.db.queued.append(s.queued)
    elif damage == 'wrong_queue_actor': s.queued.actor_id = 'other'
    elif damage == 'wrong_queue_job': s.queued.data['job_id'] = 'other'
    elif damage == 'bool_queue_generation': s.queued.data['generation'] = True
    elif damage == 'missing_ready': s.db.completed = []
    elif damage == 'duplicate_ready': s.db.completed.append(s.completed)
    elif damage == 'wrong_ready_actor': s.completed.actor_id = 'other'
    elif damage == 'wrong_artifacts': s.completed.data['artifact_hashes'] = {}
    elif damage == 'false_probe': s.completed.data['engineering_probe_only'] = False
    with pytest.raises(sound.HTTPException) as error: sound.checked_trace(s.db, s.p, s.trace)
    assert error.value.status_code == 409


def test_live_installation_trace_requires_current_generation_and_physical_ready(original_installation, monkeypatch):
    s = original_installation; checks = []
    monkeypatch.setattr(sound, 'ready', lambda **kw: checks.append(True))
    proof = sound.checked_trace(s.db, s.p, s.trace, live=True)['proof']
    assert checks == [True] and proof['source'] == 'official'
    s.installation.data['job_id'] = 'different-job'
    with pytest.raises(sound.HTTPException): sound.checked_trace(s.db, s.p, s.trace, live=True)
    assert checks == [True]



def test_native_float32_scores_use_the_exact_user_threshold_without_rounding_it_down(tmp_path):
    """Real NumPy in the isolated sound interpreter; no backend NumPy or model load."""
    import os
    import subprocess
    python = Path(os.environ.get('GA_SOUND_TEST_PYTHON', str(ROOT / '.runtime/venvs/yamnet-litert/bin/python')))
    assert python.is_absolute() and python.is_file(), '请先准备固定的独立声音运行环境；此检查不能跳过'
    code = r'''
import importlib.util, json, math, socket, sys, types
from importlib.metadata import version
from pathlib import Path
import numpy as np
assert version('numpy') == '2.5.3'
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('_sound_threshold_runtime', root / 'src/generic_agents/local_sound_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
recipe = json.loads((root / 'model-packs/yamnet-mediapipe-v1.json').read_text())
def deny(*args, **kwargs): raise AssertionError('threshold test must not use the network')
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
scores = np.zeros(521, dtype=np.float32)
scores[76] = np.float32(.1)
raw_score = float(scores[76])
strict_threshold = math.nextafter(raw_score, math.inf)
inputs_seen = []
class Input:
    def write(self, values):
        assert values.dtype == np.float32 and values.shape == (15600,)
        inputs_seen.append(values.copy())
class Output:
    def read(self, count, dtype):
        assert count == 521 and dtype == np.float32
        return scores.copy()
class Model:
    @classmethod
    def from_file(cls, path, hardware_accel):
        assert str(path) == 'not_loaded' and hardware_accel == 'cpu-only'
        return cls()
    def create_input_buffers(self, index): return [Input()]
    def create_output_buffers(self, index): return [Output()]
    def run_by_index(self, index, inputs, outputs): assert index == 0
parent = types.ModuleType('ai_edge_litert'); parent.__path__ = []
compiled = types.ModuleType('ai_edge_litert.compiled_model')
compiled.CompiledModel = Model
compiled.HardwareAccelerator = types.SimpleNamespace(CPU='cpu-only')
sys.modules['ai_edge_litert'] = parent
sys.modules['ai_edge_litert.compiled_model'] = compiled
stricter = runner.classify(np.zeros(4000, dtype=np.float32), recipe['labels'], Path('not_loaded'), strict_threshold)
equal = runner.classify(np.zeros(4000, dtype=np.float32), recipe['labels'], Path('not_loaded'), raw_score)
assert stricter['candidates'] == []
assert [item['class_index'] for item in equal['candidates']] == [76]
assert stricter['windows'][0]['scores'][76] == equal['windows'][0]['scores'][76] == raw_score
assert len(inputs_seen) == 2
print(json.dumps({'schema': 'sound-float32-threshold-test-v1', 'numpy': version('numpy'),
                  'stricter_candidate_count': len(stricter['candidates']),
                  'equal_candidate_indices': [item['class_index'] for item in equal['candidates']],
                  'original_scores_unchanged': True, 'real_model_loaded': False}))
'''
    completed = subprocess.run([str(python), '-I', '-B', '-c', code, str(ROOT)], cwd=tmp_path,
        env=sound.clean_environment(tmp_path), stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    assert completed.returncode == 0, {'returncode': completed.returncode,
        'stderr_tail': completed.stderr.decode(errors='replace')[-1600:]}
    assert len(completed.stdout) <= 4096
    assert json.loads(completed.stdout) == {'schema': 'sound-float32-threshold-test-v1', 'numpy': '2.5.3',
        'stricter_candidate_count': 0, 'equal_candidate_indices': [76],
        'original_scores_unchanged': True, 'real_model_loaded': False}

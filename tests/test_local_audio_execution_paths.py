"""Real adapter validation with tiny fake packs/Popen; never loads audio models."""
import copy
import hashlib
import json
from pathlib import Path
import stat
import subprocess

import pytest

from generic_agents import local_audio as asr
from generic_agents import local_segmented as segmented
from generic_agents import local_vad as vad
from generic_agents.local_audio_paths import LocalAudioPaths

CONTENT = b'synthetic source bytes, not an audio model'


def write(path, raw, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(raw); path.chmod(mode)


@pytest.fixture(autouse=True)
def forbid_external_work(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('pure audio adapter tests cannot execute external work')
    import psycopg
    import sqlalchemy
    import httpx
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(asr.sys, 'platform', 'darwin')


@pytest.fixture
def packs(tmp_path, monkeypatch):
    root = tmp_path.resolve() / 'server-runtime'; root.mkdir(mode=0o700)
    result = {}
    for name, module in (('audio', asr), ('vad', vad)):
        binary = root / 'private-base' / (name + '-python'); write(binary, b'not an executable interpreter', 0o700)
        python = root / 'venvs' / name / 'bin/python'; python.parent.mkdir(parents=True, mode=0o700); python.symlink_to(binary)
        weights = root / (name + '-bootstrap/weights'); weights.mkdir(parents=True, mode=0o700)
        raw = b'fake ' + name.encode() + b' weight bytes'; name_file = 'pure-fixture.bin'
        write(weights / name_file, raw)
        # Only temporary fixture metadata is substituted. Fixed source/runner
        # checks and the production result validators remain active.
        monkeypatch.setattr(module, 'RECIPE', {**module.RECIPE, 'files': [{'name': name_file,
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]})
        result[name] = LocalAudioPaths(root, python, weights)
    sandbox = root / 'not-a-real-sandbox-exec'; write(sandbox, b'not executed', 0o700)
    for module in (asr, vad, segmented): monkeypatch.setattr(module, '_SANDBOX', sandbox)
    monkeypatch.setattr(asr, 'audio_paths', lambda: result['audio'])
    monkeypatch.setattr(vad, 'vad_paths', lambda: result['vad'])
    monkeypatch.setattr(segmented, 'audio_paths', lambda: result['audio'])
    return result


def sample(kind):
    if kind == 'asr':
        configuration = {'language': 'zh', 'channel': 'mono'}
        text = 'pure fake transcript'
        result = {'schema': 'local-audio-transcription-v1', 'deployment': 'local', 'backend': 'cpu', 'cloud_calls': 0,
            'input_sha256': hashlib.sha256(CONTENT).hexdigest(),
            'audio': {'duration_seconds': .25, 'rms': 0., 'clipped_fraction': 0., 'peak': 0., 'selected_channel': 'mono', 'samples': 4000, 'sample_rate': 16000},
            'transcription': {'text': text, 'word_timestamps': False, 'speaker_identity': False,
                'environment_classifier_used': False, 'language_requested': 'zh', 'segments': [
                    {'start_seconds': 0, 'end_seconds': .25, 'text': text, 'timing': 'input_clip_only'}]}}
        return (CONTENT, configuration), result
    if kind == 'vad':
        configuration = {'channel': 'mono', 'threshold': .5}
        result = {'schema': 'local-speech-activity-v1', 'deployment': 'local', 'backend': 'cpu',
            'providers': ['CPUExecutionProvider'], 'cloud_calls': 0, 'input_sha256': hashlib.sha256(CONTENT).hexdigest(),
            'model_sha256': vad.RECIPE['files'][0]['sha256'], 'audio': {'selected_channel': 'mono', 'duration_seconds': .25},
            'activity': {'resampled_samples': 4000, 'sample_rate': 16000, 'window_samples': 512, 'step_seconds': .032,
                'configuration': configuration, 'parameters': copy.deepcopy(vad.RECIPE['parameters']), 'probabilities': [0.] * 8,
                'inference_performed': True, 'probabilities_calibrated': False, 'source_time': 'unknown',
                'domain_quality': 'not_verified', 'environment_audio_preserved': True, 'environment_classifier_used': False,
                'transcription_performed': False, 'intervals': []}}
        return (CONTENT, configuration), result
    source = {'input_sha256': hashlib.sha256(CONTENT).hexdigest(), 'resampled_samples': 4000,
        'intervals': [{'start_sample': 1000, 'end_sample': 2000}]}
    configuration = {'indexes': [0], 'language': 'zh'}
    start = max(0, 1000 - segmented.RECIPE['context_samples']); end = min(4000, 2000 + segmented.RECIPE['context_samples'])
    value = {'index': 0, 'candidate_start_sample': 1000, 'candidate_end_sample': 2000,
        'clip_start_sample': start, 'clip_end_sample': end, 'clip_start_seconds': start / 16000, 'clip_end_seconds': end / 16000,
        'language': 'zh', 'inference_performed': True, 'word_timestamps': False, 'speaker_identity': False,
        'timing': 'vad_candidate_with_context_not_word_alignment', 'cloud_calls': 0,
        'pcm_sha256': 'a' * 64, 'text': 'pure fake segment', 'coarse_tags': []}
    receipt = {'event': 'done', 'input_sha256': source['input_sha256'], 'indexes': [0], 'model_load_count': 1, 'cloud_calls': 0}
    return (CONTENT, source, configuration, [0]), [{'event': 'segment', 'value': value}, receipt]


def fake_popen(monkeypatch, payload, *, alive=False):
    launches = []; children = []
    class Child:
        pid = 999999999
        def __init__(self): self.returncode = None if alive else 0; self.actions = []
        def poll(self): return self.returncode
        def terminate(self): self.actions.append('terminate'); self.returncode = -15
        def kill(self): self.actions.append('kill'); self.returncode = -9
        def wait(self, timeout=None): self.actions.append(('wait', timeout)); return self.returncode
    def spawn(command, **kwargs):
        cwd = Path(kwargs['cwd']); environment = kwargs['env']
        assert stat.S_IMODE(cwd.stat().st_mode) == 0o700
        assert kwargs['stdin'] == subprocess.DEVNULL
        assert all(environment[k] == str(cwd) for k in ('HOME', 'TMPDIR', 'TMP', 'TEMP'))
        assert all(Path(environment[k]).is_relative_to(cwd) for k in ('XDG_CACHE_HOME', 'HF_HOME', 'HUGGINGFACE_HUB_CACHE', 'TORCH_HOME', 'MODELSCOPE_CACHE', 'NUMBA_CACHE_DIR'))
        assert environment['PYTHONDONTWRITEBYTECODE'] == environment['PYTHONNOUSERSITE'] == '1'
        assert environment['HF_HUB_OFFLINE'] == environment['TRANSFORMERS_OFFLINE'] == '1'
        assert not {'GA_DATABASE_URL', 'GA_REDIS_URL', 'DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY', 'PGPASSWORD'} & set(environment)
        assert not (cwd / '.env').exists()
        lines = payload if type(payload) is list else [payload]
        kwargs['stdout'].write(b''.join((json.dumps(line) + '\n').encode() for line in lines)); kwargs['stdout'].flush()
        launches.append((command, kwargs)); child = Child(); children.append(child); return child
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    return launches, children


@pytest.mark.parametrize('module,key', [(asr, 'audio'), (vad, 'vad'), (segmented, 'audio')])
def test_ready_no_argument_compatibility_and_explicit_same_snapshot(packs, monkeypatch, module, key):
    assert module.ready() is None
    selector = 'vad_paths' if module is vad else 'audio_paths'
    monkeypatch.setattr(module, selector, lambda: pytest.fail('explicit snapshot must not select again'))
    if module is segmented:
        monkeypatch.setattr(asr, 'audio_paths', lambda: pytest.fail('nested ASR must retain segmented snapshot'))
    assert module.ready(_paths=packs[key]) is None


@pytest.mark.parametrize('kind,module,key', [('asr', asr, 'audio'), ('vad', vad, 'vad'), ('segmented', segmented, 'audio')])
@pytest.mark.parametrize('explicit', [False, True])
def test_execution_uses_one_snapshot_literal_python_and_private_offline_job(packs, monkeypatch, kind, module, key, explicit):
    paths = packs[key]; selected = []
    def select(): selected.append(paths); assert len(selected) == 1; return paths
    monkeypatch.setattr(module, 'vad_paths' if module is vad else 'audio_paths', select)
    if module is segmented:
        monkeypatch.setattr(asr, 'audio_paths', lambda: pytest.fail('nested ready must not reselect'))
    args, payload = sample(kind); launches, children = fake_popen(monkeypatch, payload)
    events = []; keywords = {'on_started': lambda pid: events.append(('started', pid)), 'guard': lambda: events.append(('guard',))}
    if explicit: keywords['_paths'] = paths
    if kind == 'segmented': keywords['on_segment'] = lambda value: events.append(('segment', value['index']))
    result = module.call(*args, **keywords)
    assert selected == ([] if explicit else [paths]) and len(launches) == 1
    command, options = launches[0]
    assert command[:6] == [str(module._SANDBOX), '-p', '(version 1)(allow default)(deny network*)', str(paths.python), '-B', str(module.RUNNER)]
    assert command[command.index('--weights') + 1] == str(paths.weights)
    assert str(paths.python.resolve()) != command[3]  # Keep the literal venv link.
    assert ('started', children[0].pid) in events and ('guard',) in events
    assert not options['cwd'].exists()  # Temporary inputs/caches are removed.
    if kind == 'segmented':
        assert ('segment', 0) in events and result[0][0] == payload[0]['value'] and result[1] == payload[1]
    else: assert result == payload


@pytest.mark.parametrize('module,key', [(asr, 'audio'), (vad, 'vad'), (segmented, 'audio')])
@pytest.mark.parametrize('unavailable', ['platform', 'sandbox'])
def test_no_os_isolation_is_a_hard_block(packs, monkeypatch, module, key, unavailable):
    if unavailable == 'platform': monkeypatch.setattr(asr.sys, 'platform', 'linux')
    else: monkeypatch.setattr(module, '_SANDBOX', packs[key].root / 'absent-sandbox')
    with pytest.raises(ValueError, match='隔离'): module.ready(_paths=packs[key])


@pytest.mark.parametrize('module,key', [(asr, 'audio'), (vad, 'vad')])
def test_changed_weight_bytes_are_not_accepted_at_new_root(packs, module, key):
    target = packs[key].weights / 'pure-fixture.bin'; target.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='摘要不符'): module.ready(_paths=packs[key])


@pytest.mark.parametrize('kind,module,key', [('asr', asr, 'audio'), ('vad', vad, 'vad'), ('segmented', segmented, 'audio')])
def test_guard_cancellation_still_stops_held_child(packs, monkeypatch, kind, module, key):
    args, payload = sample(kind); _, children = fake_popen(monkeypatch, payload, alive=True)
    class Cancelled(Exception): pass
    def guard(): raise Cancelled('lease changed')
    with pytest.raises(Cancelled): module.call(*args, guard=guard, _paths=packs[key])
    assert children[0].actions == ['terminate', ('wait', 3)]


@pytest.mark.parametrize('kind,module,key', [('asr', asr, 'audio'), ('vad', vad, 'vad'), ('segmented', segmented, 'audio')])
def test_original_source_validation_not_bypassed_by_path_selection(packs, monkeypatch, kind, module, key):
    args, payload = sample(kind)
    if kind == 'segmented': payload[-1]['input_sha256'] = 'f' * 64
    else: payload['input_sha256'] = 'f' * 64
    fake_popen(monkeypatch, payload)
    with pytest.raises(ValueError): module.call(*args, _paths=packs[key])

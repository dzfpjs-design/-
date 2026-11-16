"""Isolated protocol/filesystem/timeline unit tests, not actual AV/RTSP proof.

The fake decoder supplies declared PCM samples; it never substitutes for Root's
required real PyAV codec and RTSP execution. No NumPy/runtime model dependency.
"""
import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import wave
import pytest
from generic_agents import audio_stream_capture_runner as capture


def request(root, **changes):
    return {'schema': 'audio-stream-capture-v1', 'url': 'rtsp://user:synthetic-secret@127.0.0.1:8554/audio',
        'directory': str(root.resolve()), 'audio_stream_index': 0, 'segment_seconds': 1,
        'max_segments': 1, 'max_runtime_seconds': 30, 'max_segment_bytes': capture.MAX_SEGMENT_BYTES, **changes}


class Plane:
    def __init__(self, value): self.value = value; self.buffer_size = len(value)
    def __bytes__(self): return self.value


class Frame:
    def __init__(self, samples, *, rate=8000, channels=2, pts=0, basis=None, layout=None, pcm=None):
        self.samples = samples; self.sample_rate = rate; self.pts = pts
        self.time_base = Fraction(1, rate) if basis is None else basis
        self.layout = SimpleNamespace(name=layout or ('mono' if channels == 1 else 'stereo'), channels=list(range(channels)))
        self.format = SimpleNamespace(name='s16', is_planar=False)
        self.pcm = pcm if pcm is not None else struct.pack('<' + 'h' * channels, *range(100, 100 + channels)) * samples
        self.planes = [Plane(self.pcm + b'\0' * 32)]  # AV plane alignment is not saved as samples.


class Input:
    def __init__(self, frames, streams, *, error=None, hook=None):
        self.frames = frames; self.streams = SimpleNamespace(audio=streams); self.error = error; self.hook = hook
        self.selected = None
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def decode(self, stream):
        self.selected = stream
        for index, frame in enumerate(self.frames):
            if self.hook: self.hook(index)
            yield frame
        if self.error: raise self.error


class AV:
    def __init__(self, frames, *, audio_tracks=1, error=None, hook=None):
        self.tracks = [SimpleNamespace(codec_context=SimpleNamespace(name='pcm_s16le')) for _ in range(audio_tracks)]
        self.input = Input(frames, self.tracks, error=error, hook=hook); self.calls = []; self.converters = []
    def open(self, url, **options):
        self.calls.append((url, options)); return self.input
    def AudioResampler(self, **configuration):
        self.converters.append(configuration)
        class Converter:
            def resample(self, frame): return [] if frame is None else [frame]
        return Converter()


def run(tmp_path, frames, **options):
    av = options.pop('av', AV(frames)); body = request(tmp_path, **options); events = []
    state = capture.capture(body, av, events.append)
    return state, events, av


def saved(events): return [value for value in events if value['kind'] == 'segment.saved']


def read_wav(path):
    with wave.open(str(path), 'rb') as stream:
        return (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getnframes(), stream.readframes(stream.getnframes()))


@pytest.mark.parametrize('field,value', [('schema', 'other'), ('audio_stream_index', True), ('audio_stream_index', -1),
    ('audio_stream_index', 32), ('segment_seconds', 0), ('segment_seconds', 61), ('segment_seconds', 1.0),
    ('max_segments', 0), ('max_segments', 101), ('max_runtime_seconds', 0), ('max_runtime_seconds', 3601),
    ('max_segment_bytes', True), ('max_segment_bytes', capture.MAX_SEGMENT_BYTES + 1), ('max_segment_bytes', 44),
    ('url', 'https://127.0.0.1/audio'), ('url', 'rtsp://'), ('url', 'rtsp://127.0.0.1:0/audio'),
    ('url', 'rtsp://127.0.0.1/audio#fragment'), ('directory', 'relative/path'), ('directory', '/safe/../other')])
def test_closed_bounded_stdin_contract_rejects_invalid_values(tmp_path, field, value):
    body = request(tmp_path); body[field] = value
    with pytest.raises(capture.CaptureError) as error: capture.parse_request(json.dumps(body).encode())
    assert error.value.code == 'invalid_request'


@pytest.mark.parametrize('raw', [b'[]', b'{"schema":"a","schema":"b"}', b'{"x":NaN}', b'\xff', b' ' * 16385])
def test_duplicate_nonfinite_nonobject_and_oversized_requests_are_rejected(raw):
    with pytest.raises(capture.CaptureError) as error: capture.parse_request(raw)
    assert error.value.code == 'invalid_request'


def test_two_exact_pcm_segments_preserve_rate_channels_all_samples_and_source_offsets(tmp_path):
    frame = Frame(16000); state, events, av = run(tmp_path, [frame], max_segments=2)
    assert state == 'completed' and events[-1]['kind'] == 'stream.completed'
    rows = saved(events); assert len(rows) == 2
    pcm = b''
    for index, value in enumerate(rows):
        rate, channels, width, samples, data = read_wav(tmp_path / value['file'])
        assert (rate, channels, width, samples) == (8000, 2, 2, 8000)
        assert value['index'] == index and value['sample_count'] == 8000 and value['bytes'] == 32044
        assert value['sha256'] == hashlib.sha256((tmp_path / value['file']).read_bytes()).hexdigest()
        assert value['capture_representation'] == 'decoded_source_pcm16' and value['source_encoding_preserved'] is False
        assert value['source_codec'] == 'pcm_s16le' and value['codec'] == 'pcm_s16le'
        assert value['source_clock_utc'] == 'unknown' and value['channel_mixing'] is False and value['sample_rate_conversion'] is False
        pcm += data
    assert pcm == frame.pcm
    assert rows[0]['continuity_verified'] and not rows[0]['continuous_with_previous']
    assert rows[1]['continuous_with_previous'] and rows[1]['source_start_seconds'] == '1/1'
    assert rows[1]['first_source_pts'] == 0 and rows[1]['first_frame_offset_samples'] == 8000
    assert av.input.selected is av.tracks[0] and av.converters == [{'format': 's16', 'layout': 'stereo', 'rate': 8000}]
    assert av.calls[0][1]['options']['allowed_media_types'] == 'audio'
    assert av.calls[0][1]['options']['protocol_whitelist'] == 'rtsp,tcp'
    assert not list(tmp_path.glob('*.tmp')) and not list(tmp_path.glob('.audio-*'))


def test_audio_track_index_is_explicit_and_missing_audio_does_not_become_silence(tmp_path):
    av = AV([Frame(8000)], audio_tracks=2)
    state, events, _ = run(tmp_path, [], av=av, audio_stream_index=1)
    assert state == 'completed' and av.input.selected is av.tracks[1]
    other = tmp_path / 'absent'; other.mkdir()
    state, events, _ = run(other, [], av=AV([], audio_tracks=0))
    assert state == 'failed' and events[-1]['error_code'] == 'audio_absent' and not saved(events)
    assert not list(other.iterdir())


def test_missing_pts_can_save_samples_but_never_claim_continuity(tmp_path):
    state, events, _ = run(tmp_path, [Frame(4000, pts=None), Frame(4000, pts=None)])
    assert state == 'completed'
    value = saved(events)[0]
    assert value['sample_count'] == 8000 and value['has_missing_pts']
    assert value['continuity_verified'] is False and value['continuous_with_previous'] is False
    assert value['source_start_seconds'] is None and value['source_end_seconds'] is None
    gaps = [e for e in events if e['kind'] == 'gap']
    assert len(gaps) == 1 and gaps[0]['code'] == 'source_pts_missing' and gaps[0]['source_delta_seconds'] is None


@pytest.mark.parametrize('first,second,code', [(0, 8000, 'source_gap'), (8000, 4000, 'source_pts_regression'),
    (0, 2000, 'source_overlap')])
def test_gap_regression_and_overlap_stop_before_new_samples_and_keep_marked_tail(tmp_path, first, second, code):
    first_frame = Frame(4000, pts=first); second_frame = Frame(4000, pts=second)
    state, events, _ = run(tmp_path, [first_frame, second_frame], max_segments=2)
    assert state == 'failed' and events[-1]['error_code'] == code
    rows = saved(events); assert len(rows) == 1 and rows[0]['complete'] is False and rows[0]['tail_reason'] == code
    assert rows[0]['sample_count'] == 4000 and read_wav(tmp_path / rows[0]['file'])[-1] == first_frame.pcm
    gap = next(e for e in events if e['kind'] == 'gap')
    assert gap['code'] == code and gap['interval_status'] == 'not_filled' and gap['source_clock_utc'] == 'unknown'


def test_one_tick_quantization_is_explicitly_unverified_without_inserting_samples(tmp_path):
    frames = [Frame(4000, pts=0), Frame(4000, pts=4001)]
    state, events, _ = run(tmp_path, frames)
    assert state == 'completed' and saved(events)[0]['continuity_verified'] is False
    assert read_wav(tmp_path / saved(events)[0]['file'])[-1] == b''.join(f.pcm for f in frames)
    assert any(e['kind'] == 'gap' and e['code'] == 'timestamp_quantization_unverified' for e in events)


@pytest.mark.parametrize('samples,kept', [(1999, False), (2000, True)])
def test_only_identifiable_quarter_second_tail_is_saved_on_stream_end(tmp_path, samples, kept):
    state, events, _ = run(tmp_path, [Frame(samples)], max_segments=2)
    assert state == 'failed' and events[-1]['error_code'] == 'stream_ended'
    assert len(saved(events)) == int(kept)
    if kept: assert saved(events)[0]['complete'] is False and saved(events)[0]['sample_count'] == samples
    else: assert not list(tmp_path.iterdir()) and events[-1]['discarded_sample_count'] == samples


def test_byte_budget_stops_with_exact_partial_pcm_without_overrun(tmp_path):
    state, events, _ = run(tmp_path, [Frame(8000)], max_segment_bytes=44 + 4000 * 4)
    assert state == 'failed' and events[-1]['error_code'] == 'segment_byte_limit'
    row = saved(events)[0]; assert row['sample_count'] == 4000 and row['bytes'] == 16044
    assert row['complete'] is False and row['tail_reason'] == 'segment_byte_limit'


def test_format_change_keeps_old_format_tail_and_never_converts_rate_or_channels(tmp_path):
    state, events, av = run(tmp_path, [Frame(4000), Frame(4000, rate=16000, pts=4000)], max_segments=2)
    assert state == 'failed' and events[-1]['error_code'] == 'source_format_changed'
    assert saved(events)[0]['sample_rate'] == 8000 and saved(events)[0]['sample_count'] == 4000
    assert av.converters == [{'format': 's16', 'layout': 'stereo', 'rate': 8000}]


def test_timeout_diagnostics_never_include_url_credentials_or_exception_text(tmp_path):
    secret = 'rtsp://user:synthetic-secret@127.0.0.1:8554/audio'
    av = AV([Frame(4000)], error=TimeoutError(secret))
    state, events, _ = run(tmp_path, [], av=av, max_segments=2)
    assert state == 'failed' and events[-1]['error_code'] == 'read_timeout'
    assert saved(events)[0]['tail_reason'] == 'read_timeout'
    report = json.dumps(events)
    assert secret not in report and 'synthetic-secret' not in report and '127.0.0.1' not in report


def test_runtime_deadline_saves_only_predeadline_tail(tmp_path):
    clock = [0.0]
    def hook(index): clock[0] = 0.0 if index == 0 else 2.0
    av = AV([Frame(4000), Frame(4000, pts=4000)], hook=hook)
    events = []; state = capture.capture(request(tmp_path, max_runtime_seconds=1, max_segments=2), av,
        events.append, monotonic=lambda: clock[0], wall_clock=lambda: 123.0)
    assert state == 'failed' and events[-1]['error_code'] == 'runtime_limit'
    assert saved(events)[0]['sample_count'] == 4000 and saved(events)[0]['tail_reason'] == 'runtime_limit'


def test_existing_file_is_rejected_before_network_and_never_overwritten(tmp_path):
    path = tmp_path / 'audio-000000.wav'; path.write_bytes(b'original-keep')
    state, events, av = run(tmp_path, [Frame(8000)])
    assert state == 'failed' and events[-1]['error_code'] == 'output_exists' and not av.calls
    assert path.read_bytes() == b'original-keep'


def test_final_name_race_is_atomic_no_replace_and_temp_is_removed(tmp_path):
    with capture.OutputDirectory(str(tmp_path.resolve()), 1) as directory:
        writer = capture.SegmentWriter(directory, 0, 8000, 2, 'stereo', 'pcm_s16le', 1, capture.MAX_SEGMENT_BYTES)
        timing = capture.AudioTimeline().frame(0, Fraction(1, 8000), 8000, 8000)
        writer.append(Frame(8000).pcm, timing, 0)
        (tmp_path / 'audio-000000.wav').write_bytes(b'other-completed-file')
        with pytest.raises(capture.CaptureError) as error: writer.finish(complete=True, reason=None)
        assert error.value.code == 'output_exists'
        writer.discard()
    assert (tmp_path / 'audio-000000.wav').read_bytes() == b'other-completed-file'
    assert not list(tmp_path.glob('.audio-*'))


def test_symlink_directory_is_rejected_without_reading_or_writing_target(tmp_path):
    target = tmp_path / 'target'; target.mkdir(); link = tmp_path / 'link'; link.symlink_to(target, target_is_directory=True)
    body = request(tmp_path, directory=str(link)); av = AV([Frame(8000)]); events = []
    assert capture.capture(body, av, events.append) == 'failed'
    assert events[-1]['error_code'] == 'invalid_directory' and not av.calls and not list(target.iterdir())



def test_pts_without_time_base_is_preserved_as_raw_but_not_claimed_as_verified_time(tmp_path):
    frame = Frame(8000, pts=123); frame.time_base = None
    state, events, _ = run(tmp_path, [frame])
    value = saved(events)[0]
    assert state == 'completed' and value['first_source_pts'] == value['last_source_pts'] == 123
    assert value['first_source_time_base'] is None and value['source_start_seconds'] is None
    assert value['has_missing_pts'] and not value['continuity_verified']


def test_explicit_guard_stop_preserves_only_pre_stop_identifiable_tail(tmp_path):
    stopped = [False]
    def hook(index): stopped[0] = index > 0
    def guard():
        if stopped[0]: raise capture.CaptureError('interrupted')
    av = AV([Frame(4000), Frame(4000, pts=4000)], hook=hook); events = []
    assert capture.capture(request(tmp_path, max_segments=2), av, events.append, guard=guard) == 'failed'
    assert events[-1]['error_code'] == 'interrupted' and saved(events)[0]['sample_count'] == 4000
    assert saved(events)[0]['tail_reason'] == 'interrupted' and saved(events)[0]['complete'] is False


def test_cli_invalid_stdin_has_only_safe_jsonl_and_no_stderr_or_credentials(tmp_path):
    import os
    import subprocess
    import sys
    body = request(tmp_path, url='https://user:synthetic-secret@127.0.0.1/private')
    result = subprocess.run([sys.executable, str(Path(capture.__file__).resolve())], input=json.dumps(body).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        env={'PATH': os.defpath, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 2 and result.stderr == b''
    lines = result.stdout.splitlines(); assert len(lines) == 1
    value = json.loads(lines[0]); assert value['kind'] == 'stream.failed' and value['error_code'] == 'invalid_request'
    assert b'synthetic-secret' not in result.stdout and b'private' not in result.stdout and b'127.0.0.1' not in result.stdout

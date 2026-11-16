"""Bounded RTSP audio-track capture to decoded-source PCM16 WAV.

The parent owns endpoint validation, network sandbox, authorization, budgets and
lease. This process does not infer, mix channels, resample for YAMNet, fill gaps,
or verify source UTC. Stdout is a closed JSONL protocol; diagnostics never carry
URLs, credentials or exception text. Importing this module does not import AV.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
from fractions import Fraction
from importlib.metadata import version
from urllib.parse import urlsplit
from uuid import uuid4
import wave

SCHEMA = 'audio-stream-capture-v1'
EVENT_SCHEMA = 'audio-stream-capture-event-v1'
MAX_REQUEST_BYTES = 16384
MAX_SEGMENT_BYTES = 20 * 1024 * 1024
MIN_RATE, MAX_RATE, MAX_CHANNELS = 8000, 96000, 8
REQUEST_KEYS = {'schema', 'url', 'directory', 'audio_stream_index', 'segment_seconds',
    'max_segments', 'max_runtime_seconds', 'max_segment_bytes'}
ERROR_CODES = frozenset({'invalid_request', 'invalid_directory', 'output_exists', 'output_failed',
    'runtime_unavailable', 'audio_absent', 'audio_stream_index_invalid', 'audio_format_unsupported',
    'source_format_changed', 'source_pts_regression', 'source_overlap', 'source_gap',
    'sample_conversion_failed', 'segment_byte_limit', 'read_timeout', 'connection_failed',
    'stream_ended', 'decode_failed', 'runtime_limit', 'interrupted', 'parent_lost'})


class CaptureError(ValueError):
    def __init__(self, code, *, gap=None):
        self.code = code if code in ERROR_CODES else 'decode_failed'
        self.gap = gap
        super().__init__(self.code)


def _need(value, code):
    if not value: raise CaptureError(code)


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result: raise CaptureError('invalid_request')
        result[key] = value
    return result


def _text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    try: value.encode('utf-8')
    except UnicodeError: return False
    return True


def parse_request(raw):
    try:
        if isinstance(raw, str): raw = raw.encode('utf-8')
        _need(type(raw) is bytes and len(raw) <= MAX_REQUEST_BYTES, 'invalid_request')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(CaptureError('invalid_request')))
        _need(type(value) is dict and set(value) == REQUEST_KEYS and value['schema'] == SCHEMA, 'invalid_request')
        for key, low, high in [('audio_stream_index', 0, 31), ('segment_seconds', 1, 60),
                ('max_segments', 1, 100), ('max_runtime_seconds', 1, 3600), ('max_segment_bytes', 45, MAX_SEGMENT_BYTES)]:
            _need(type(value[key]) is int and low <= value[key] <= high, 'invalid_request')
        _need(_text(value['url'], 4096) and _text(value['directory'], 4096), 'invalid_request')
        endpoint = urlsplit(value['url'])
        _need(endpoint.scheme.lower() == 'rtsp' and endpoint.hostname is not None and not endpoint.fragment
            and (endpoint.port is None or 1 <= endpoint.port <= 65535), 'invalid_request')
        path = value['directory']
        _need(path.startswith('/') and '..' not in path.split('/') and '.' not in path.split('/'), 'invalid_request')
        return value
    except CaptureError: raise
    except (UnicodeError, TypeError, ValueError, KeyError, OverflowError):
        raise CaptureError('invalid_request') from None


def _regular_name(index):
    return 'audio-' + str(index).zfill(6) + '.wav'


class OutputDirectory:
    """Keep an fd to a canonical non-symlink directory; never replace a file."""
    def __init__(self, directory, max_segments):
        self.fd = None
        try:
            _need(_text(directory, 4096) and directory.startswith('/')
                and not any(part in {'.', '..'} for part in directory.split('/')), 'invalid_directory')
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            current = os.open('/', flags)
            try:
                for part in directory.split('/'):
                    if not part: continue
                    following = os.open(part, flags, dir_fd=current)
                    os.close(current); current = following
                    info = os.fstat(current)
                    trusted_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
                    _need(info.st_uid in {0, os.geteuid()} and (not info.st_mode & 0o022 or trusted_sticky), 'invalid_directory')
                self.fd = current; current = None
            finally:
                if current is not None: os.close(current)
            for index in range(max_segments):
                try: os.stat(_regular_name(index), dir_fd=self.fd, follow_symlinks=False)
                except FileNotFoundError: continue
                raise CaptureError('output_exists')
        except CaptureError:
            self.close(); raise
        except OSError:
            self.close(); raise CaptureError('invalid_directory') from None

    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def rational(value):
    return None if value is None else str(value.numerator) + '/' + str(value.denominator)


class AudioTimeline:
    """Relative decoded-frame PTS only; unknown timestamps are never filled."""
    def __init__(self):
        self.last_start = None; self.last_end = None; self.basis = None
        self.reported_missing = False; self.reported_quantization = False

    def frame(self, pts, basis, samples, rate):
        _need(pts is None or type(pts) is int and -(2 ** 63) <= pts < 2 ** 63, 'audio_format_unsupported')
        if basis is not None:
            try: basis = Fraction(basis)
            except (ValueError, TypeError, ZeroDivisionError): raise CaptureError('audio_format_unsupported') from None
            _need(basis > 0 and basis.numerator <= 2 ** 31 - 1 and basis.denominator <= 2 ** 31 - 1, 'audio_format_unsupported')
            if self.basis is not None and basis != self.basis: raise CaptureError('source_format_changed')
            self.basis = basis
        if pts is None or basis is None:
            self.last_start = self.last_end = None
            gap = None
            if not self.reported_missing:
                self.reported_missing = True
                gap = {'code': 'source_pts_missing', 'expected_source_start': None,
                    'actual_source_start': None, 'source_delta_seconds': None}
            return {'pts': pts, 'basis': basis, 'start': None, 'verified': False, 'gap': gap}
        self.basis = basis; start = pts * basis; previous = self.last_end
        info = {'expected_source_start': rational(previous), 'actual_source_start': rational(start),
            'source_delta_seconds': rational(start - previous) if previous is not None else None}
        if self.last_start is not None and start < self.last_start:
            raise CaptureError('source_pts_regression', gap={'code': 'source_pts_regression', **info})
        delta = start - previous if previous is not None else Fraction(0)
        tolerance = max(basis, Fraction(1, rate))
        if previous is not None and delta > tolerance:
            raise CaptureError('source_gap', gap={'code': 'source_gap', **info})
        if previous is not None and delta < -tolerance:
            raise CaptureError('source_overlap', gap={'code': 'source_overlap', **info})
        gap = None
        if previous is not None and delta != 0 and not self.reported_quantization:
            self.reported_quantization = True
            gap = {'code': 'timestamp_quantization_unverified', **info}
        self.last_start = start; self.last_end = start + Fraction(samples, rate)
        return {'pts': pts, 'basis': basis, 'start': start, 'verified': delta == 0, 'gap': gap}


class SegmentWriter:
    def __init__(self, directory, index, rate, channels, layout, codec, requested_seconds, max_bytes):
        self.directory = directory; self.index = index; self.rate = rate; self.channels = channels
        self.layout = layout; self.codec = codec; self.requested_seconds = requested_seconds; self.max_bytes = max_bytes
        self.samples = 0; self.file = None; self.wav = None; self.published = False
        self.temporary = '.audio-' + uuid4().hex + '.tmp'; self.name = _regular_name(index)
        self.first = None; self.last = None; self.verified = True; self.has_missing_pts = False
        try:
            descriptor = os.open(self.temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory.fd)
            self.file = os.fdopen(descriptor, 'w+b')
            self.wav = wave.open(self.file, 'wb'); self.wav.setnchannels(channels)
            self.wav.setsampwidth(2); self.wav.setframerate(rate)
        except OSError:
            self.discard(); raise CaptureError('output_failed') from None

    @property
    def capacity(self): return max(0, (self.max_bytes - 44) // (self.channels * 2) - self.samples)

    def append(self, pcm, timing, offset):
        frame_width = self.channels * 2
        _need(type(pcm) is bytes and len(pcm) % frame_width == 0, 'sample_conversion_failed')
        count = len(pcm) // frame_width
        _need(count > 0 and count <= self.capacity, 'segment_byte_limit')
        start = timing['start'] + Fraction(offset, self.rate) if timing['start'] is not None else None
        end = timing['start'] + Fraction(offset + count, self.rate) if timing['start'] is not None else None
        point = {'pts': timing['pts'], 'basis': timing['basis'], 'offset': offset, 'start': start}
        if self.first is None: self.first = point
        self.last = {'pts': timing['pts'], 'basis': timing['basis'], 'end_offset': offset + count, 'end': end}
        self.verified = self.verified and timing['verified'] and start is not None
        self.has_missing_pts = self.has_missing_pts or timing['start'] is None
        try: self.wav.writeframesraw(pcm)
        except (OSError, wave.Error): raise CaptureError('output_failed') from None
        self.samples += count

    def finish(self, *, complete, reason, previous=None):
        _need(self.samples > 0 and (complete or self.samples * 4 >= self.rate), 'output_failed')
        try:
            self.wav.close(); self.wav = None
            self.file.flush(); os.fsync(self.file.fileno()); self.file.seek(0)
            signature = hashlib.sha256()
            for block in iter(lambda: self.file.read(1024 * 1024), b''): signature.update(block)
            size = self.file.tell()
            _need(size == 44 + self.samples * self.channels * 2 and size <= self.max_bytes, 'output_failed')
            self.file.close(); self.file = None
            # POSIX same-directory link publication is atomic and fails EEXIST.
            # No os.rename/os.replace can clobber a prior completed original.
            os.link(self.temporary, self.name, src_dir_fd=self.directory.fd, dst_dir_fd=self.directory.fd, follow_symlinks=False)
            self.published = True
            os.unlink(self.temporary, dir_fd=self.directory.fd); os.fsync(self.directory.fd)
        except FileExistsError:
            raise CaptureError('output_exists') from None
        except (OSError, wave.Error): raise CaptureError('output_failed') from None
        first = self.first; last = self.last
        continuity = bool(self.verified and not self.has_missing_pts)
        consecutive = bool(previous and previous['continuity_verified'] and continuity
            and previous['source_end_seconds'] == rational(first['start']))
        return {'kind': 'segment.saved', 'schema': EVENT_SCHEMA, 'index': self.index, 'file': self.name,
            'sha256': signature.hexdigest(), 'bytes': size, 'sample_count': self.samples,
            'sample_rate': self.rate, 'channels': self.channels, 'channel_layout': self.layout,
            'sample_format': 'pcm_s16le', 'sample_width_bytes': 2, 'source_codec': self.codec, 'codec': 'pcm_s16le', 'duration_seconds': self.samples / self.rate,
            'requested_segment_seconds': self.requested_seconds, 'complete': complete, 'tail_reason': reason,
            'first_source_pts': first['pts'], 'last_source_pts': last['pts'],
            'first_source_time_base': rational(first['basis']), 'last_source_time_base': rational(last['basis']),
            'first_frame_offset_samples': first['offset'], 'last_frame_end_offset_samples': last['end_offset'],
            'source_start_seconds': rational(first['start']), 'source_end_seconds': rational(last['end']),
            'continuity_verified': continuity, 'continuous_with_previous': consecutive, 'has_missing_pts': self.has_missing_pts,
            'capture_representation': 'decoded_source_pcm16', 'source_encoding_preserved': False,
            'sample_rate_conversion': False, 'channel_mixing': False, 'source_clock_utc': 'unknown',
            'channel_order': 'decoded_frame_order_not_spatially_verified'}

    def discard(self):
        if self.wav is not None:
            try: self.wav.close()
            except (OSError, wave.Error): pass
            self.wav = None
        if self.file is not None:
            try: self.file.close()
            except OSError: pass
            self.file = None
        if not self.published and self.directory.fd is not None:
            try: os.unlink(self.temporary, dir_fd=self.directory.fd)
            except OSError: pass


def _frame_format(frame):
    try:
        rate = frame.sample_rate; channels = len(frame.layout.channels); layout = frame.layout.name; sample_format = frame.format.name
        _need(type(rate) is int and MIN_RATE <= rate <= MAX_RATE and type(channels) is int and 1 <= channels <= MAX_CHANNELS,
            'audio_format_unsupported')
        _need(_text(layout, 80) and _text(sample_format, 40) and type(frame.samples) is int and frame.samples > 0
            and frame.samples * channels * 2 <= MAX_SEGMENT_BYTES, 'audio_format_unsupported')
        return rate, channels, layout, sample_format
    except CaptureError: raise
    except (AttributeError, TypeError, ValueError): raise CaptureError('audio_format_unsupported') from None


def _pcm16(resampler, frame, rate, channels, layout):
    try:
        converted = resampler.resample(frame); result = []
        _need(sum(value.samples for value in converted) == frame.samples, 'sample_conversion_failed')
        for value in converted:
            _need(value.sample_rate == rate and len(value.layout.channels) == channels and value.layout.name == layout
                and value.format.name == 's16' and value.format.is_planar is False, 'sample_conversion_failed')
            size = value.samples * channels * 2; _need(0 < size <= MAX_SEGMENT_BYTES, 'sample_conversion_failed')
            plane = value.planes[0]
            _need(type(plane.buffer_size) is int and size <= plane.buffer_size <= MAX_SEGMENT_BYTES + 4096, 'sample_conversion_failed')
            data = bytes(plane); _need(len(data) >= size, 'sample_conversion_failed')
            data = data[:size]
            if sys.byteorder != 'little': data = b''.join(data[index:index + 2][::-1] for index in range(0, size, 2))
            result.append(data)
        return b''.join(result)
    except CaptureError: raise
    except Exception: raise CaptureError('sample_conversion_failed') from None


def _error_code(error, expired=False):
    if isinstance(error, CaptureError): return error.code
    if expired: return 'runtime_limit'
    return {'TimeoutError': 'read_timeout', 'ExitError': 'read_timeout', 'EOFError': 'stream_ended',
        'ConnectionRefusedError': 'connection_failed', 'ConnectionError': 'connection_failed', 'StreamNotFoundError': 'audio_absent',
        'InvalidDataError': 'decode_failed'}.get(type(error).__name__, 'decode_failed')


def capture(request, av_module, emitter, *, monotonic=time.monotonic, wall_clock=time.time, guard=lambda: None):
    """Run one connection. av_module is explicit for isolated codec unit tests."""
    request = parse_request(json.dumps(request).encode())
    started = monotonic(); deadline = started + request['max_runtime_seconds']
    count = 0; total_samples = 0; writer = None; previous = None; timeline = AudioTimeline(); profile = None; codec = None
    directory = None; resampler = None
    def send(value): emitter(value)
    def gap(value):
        send({'kind': 'gap', 'schema': EVENT_SCHEMA, **value, 'saved_segments': count,
            'interval_status': 'not_filled', 'source_clock_utc': 'unknown'})
    def publish(complete, reason):
        nonlocal writer, previous, count, total_samples
        result = writer.finish(complete=complete, reason=reason, previous=previous)
        writer = None; previous = result; count += 1; total_samples += result['sample_count']; send(result)
    def check():
        guard()
        if monotonic() >= deadline: raise CaptureError('runtime_limit')
    try:
        directory = OutputDirectory(request['directory'], request['max_segments'])
        check()
        timeout = min(2.0, float(request['max_runtime_seconds']))
        with av_module.open(request['url'], format='rtsp', timeout=(timeout, timeout),
                options={'rtsp_transport': 'tcp', 'allowed_media_types': 'audio', 'protocol_whitelist': 'rtsp,tcp',
                         'rw_timeout': str(int(timeout * 1000000))}) as incoming:
            streams = list(incoming.streams.audio)
            _need(bool(streams), 'audio_absent')
            _need(request['audio_stream_index'] < len(streams), 'audio_stream_index_invalid')
            stream = streams[request['audio_stream_index']]
            codec = stream.codec_context.name
            _need(type(codec) is str and re.fullmatch(r'[A-Za-z0-9_]{1,64}', codec) is not None, 'audio_format_unsupported')
            for frame in incoming.decode(stream):
                check(); current = _frame_format(frame)
                if profile is None:
                    profile = current; rate, channels, layout, _ = profile
                    resampler = av_module.AudioResampler(format='s16', layout=layout, rate=rate)
                    send({'kind': 'started', 'schema': EVENT_SCHEMA, 'audio_stream_index': request['audio_stream_index'],
                        'source_codec': codec, 'codec': 'pcm_s16le', 'sample_rate': rate, 'channels': channels, 'channel_layout': layout,
                        'sample_format': 'pcm_s16le', 'capture_representation': 'decoded_source_pcm16',
                        'source_encoding_preserved': False, 'sample_rate_conversion': False, 'channel_mixing': False,
                        'source_clock_utc': 'unknown', 'received_start_unix': wall_clock(),
                        'capture_start': 'first_decoded_audio_sample_not_connection_start'})
                elif current != profile: raise CaptureError('source_format_changed')
                rate, channels, layout, _ = profile
                timing = timeline.frame(frame.pts, frame.time_base, frame.samples, rate)
                if timing['gap']: gap(timing['gap'])
                pcm = _pcm16(resampler, frame, rate, channels, layout)
                offset = 0; samples = len(pcm) // (channels * 2)
                while offset < samples:
                    check()
                    if writer is None:
                        writer = SegmentWriter(directory, count, rate, channels, layout, codec,
                            request['segment_seconds'], request['max_segment_bytes'])
                    remaining = rate * request['segment_seconds'] - writer.samples
                    take = min(samples - offset, remaining, writer.capacity)
                    if take <= 0: raise CaptureError('segment_byte_limit')
                    first = offset * channels * 2; last = (offset + take) * channels * 2
                    writer.append(pcm[first:last], timing, offset); offset += take
                    if writer.samples == rate * request['segment_seconds']:
                        publish(True, None)
                        if count >= request['max_segments']:
                            send({'kind': 'stream.completed', 'schema': EVENT_SCHEMA, 'reason': 'max_segments',
                                'segments': count, 'saved_sample_count': total_samples, 'source_clock_utc': 'unknown'})
                            return 'completed'
                    elif writer.capacity == 0: raise CaptureError('segment_byte_limit')
            # Same-rate format conversion may not conceal a delayed tail.
            if resampler is not None: _need(not resampler.resample(None), 'sample_conversion_failed')
            raise CaptureError('stream_ended')
    except Exception as error:
        code = _error_code(error, monotonic() >= deadline)
        discarded = 0
        if writer is not None:
            discarded = writer.samples
            if (writer.samples * 4 >= writer.rate and writer.wav is not None and writer.file is not None
                    and not writer.published and code not in {'output_exists', 'output_failed'}):
                try:
                    publish(False, code); discarded = 0
                except Exception as save_error:
                    code = _error_code(save_error)
            if writer is not None: writer.discard(); writer = None
        if isinstance(error, CaptureError) and error.gap is not None: gap(error.gap)
        send({'kind': 'stream.failed', 'schema': EVENT_SCHEMA, 'error_code': code, 'segments': count,
            'saved_sample_count': total_samples, 'discarded_sample_count': discarded, 'source_clock_utc': 'unknown',
            'unrecorded_interval': 'unknown_not_filled'})
        return 'failed'
    finally:
        if writer is not None: writer.discard()
        if directory is not None: directory.close()


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False), flush=True)


def main():
    # Native decoder diagnostics can include authenticated URLs. Suppress stderr
    # at fd level, rather than trusting exception redaction or FFmpeg log level.
    silent = os.open(os.devnull, os.O_WRONLY)
    if silent != 2:
        os.dup2(silent, 2); os.close(silent)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        request = parse_request(raw)
        _need(version('av') == '16.1.0', 'runtime_unavailable')
        import av
        av.logging.set_level(-8)
        stopped = [False]; parent = os.getppid()
        def stop(*_): stopped[0] = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        def guard():
            if stopped[0]: raise CaptureError('interrupted')
            if os.getppid() != parent: raise CaptureError('parent_lost')
        return 0 if capture(request, av, emit, guard=guard) == 'completed' else 2
    except Exception as error:
        code = error.code if isinstance(error, CaptureError) else 'runtime_unavailable'
        emit({'kind': 'stream.failed', 'schema': EVENT_SCHEMA, 'error_code': code, 'segments': 0,
            'saved_sample_count': 0, 'discarded_sample_count': 0, 'source_clock_utc': 'unknown',
            'unrecorded_interval': 'unknown_not_filled'})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

"""Pure interval provenance for the unchanged evidence-privacy-v1 filter.

Offsets are Python Unicode codepoints, not UTF-8 bytes or raw PDF offsets.
The trace contains masked text and hashes, never a second copy of raw text.
Only check_privacy_trace(trace, raw) proves that a trace belongs to actual raw
text. A trace hash alone is not authorization or proof of a genuine original.
"""
import hashlib
import re

from .contracts import digest

SCHEMA = 'evidence-privacy-trace-v1'
PRIVACY_VERSION = 'evidence-privacy-v1'
_KEYS = {'schema', 'privacy_version', 'text', 'segments', 'trace_hash', 'raw_text_hash'}
_SEGMENT_KEYS = {'start', 'end', 'raw_start', 'raw_end', 'redacted'}
_HASH = re.compile(r'^[a-f0-9]{64}$')
# Literal patterns, order and replacement strings from pipeline.scrub. Changing
# these requires a new privacy version; this helper does not change that filter.
_RULES = (
    (r'(?i)\b(?:sk-[a-z0-9_-]{10,}|bearer\s+[a-z0-9._-]{10,})', '[凭证已移除]'),
    (r'(?i)(?:api[_ -]?key|password|secret|token|密码|密钥)\s*[:=：]\s*[^\s,，;；]+', '[凭证已移除]'),
    (r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已移除]'),
    (r'(?<!\d)(?:1[3-9]\d{9}|\d{17}[0-9Xx])(?!\d)', '[个人号码已移除]'),
)
_MARKERS = {replacement for _, replacement in _RULES}


def _need(value, code):
    if not value:
        raise ValueError(code)


def _text_hash(value):
    _need(type(value) is str, 'privacy_text_type')
    try:
        return hashlib.sha256(value.encode('utf-8', 'strict')).hexdigest()
    except UnicodeError:
        raise ValueError('privacy_text_unicode') from None


def _append(segments, start, length, raw_start, raw_end, redacted):
    """Merge adjacent literal intervals, never independent redaction markers."""
    if not length:
        return start
    if (segments and not redacted and not segments[-1]['redacted']
            and segments[-1]['end'] == start and segments[-1]['raw_end'] == raw_start):
        segments[-1]['end'] = start + length
        segments[-1]['raw_end'] = raw_end
    else:
        segments.append({'start': start, 'end': start + length,
            'raw_start': raw_start, 'raw_end': raw_end, 'redacted': redacted})
    return start + length


def _sub(text, segments, pattern, replacement):
    """Sweep intervals once for this regex pass, composing previous redactions.

    A later match may absorb a prior replacement plus adjacent literal text.
    Its marker maps to the complete union of those original intervals. No
    per-codepoint mapping is allocated and matches do not rescan prior segments.
    """
    output, pieces = [], []
    cursor = segment_index = output_end = 0

    def consume(end, keep):
        nonlocal cursor, segment_index, output_end
        raw_start = raw_end = None
        while cursor < end:
            segment = segments[segment_index]
            stop = min(end, segment['end'])
            if segment['redacted']:
                left, right = segment['raw_start'], segment['raw_end']
            else:
                left = segment['raw_start'] + cursor - segment['start']
                right = left + stop - cursor
            raw_start = left if raw_start is None else min(raw_start, left)
            raw_end = right if raw_end is None else max(raw_end, right)
            if keep:
                output_end = _append(output, output_end, stop - cursor, left, right, segment['redacted'])
            cursor = stop
            if cursor == segment['end']:
                segment_index += 1
        return raw_start, raw_end

    matched = False
    for match in re.finditer(pattern, text):
        matched = True
        pieces.append(text[cursor:match.start()])
        consume(match.start(), True)
        raw_start, raw_end = consume(match.end(), False)
        pieces.append(replacement)
        output_end = _append(output, output_end, len(replacement), raw_start, raw_end, True)
    if not matched:
        return text, segments
    pieces.append(text[cursor:])
    consume(len(text), True)
    return ''.join(pieces), output


def build_privacy_trace(raw):
    raw_hash = _text_hash(raw)
    text = raw
    segments = [] if not raw else [{'start': 0, 'end': len(raw),
        'raw_start': 0, 'raw_end': len(raw), 'redacted': False}]
    for index, (pattern, replacement) in enumerate(_RULES):
        if index == 2 and '@' not in text:  # Same conditional as pipeline.scrub.
            continue
        text, segments = _sub(text, segments, pattern, replacement)
    value = {'schema': SCHEMA, 'privacy_version': PRIVACY_VERSION, 'text': text,
        'segments': segments, 'raw_text_hash': raw_hash}
    return {**value, 'trace_hash': digest(value)}


def _shape(trace):
    _need(type(trace) is dict and set(trace) == _KEYS and trace['schema'] == SCHEMA
        and trace['privacy_version'] == PRIVACY_VERSION, 'privacy_trace_shape')
    text = trace['text']; _text_hash(text)
    _need(all(type(trace[key]) is str and _HASH.fullmatch(trace[key])
        for key in ('trace_hash', 'raw_text_hash')), 'privacy_trace_hash_shape')
    _need(type(trace['segments']) is list, 'privacy_segments_shape')
    cursor = raw_cursor = 0
    for segment in trace['segments']:
        _need(type(segment) is dict and set(segment) == _SEGMENT_KEYS
            and all(type(segment[key]) is int for key in ('start', 'end', 'raw_start', 'raw_end'))
            and type(segment['redacted']) is bool, 'privacy_segment_shape')
        _need(segment['start'] == cursor and cursor < segment['end'] <= len(text)
            and segment['raw_start'] == raw_cursor and raw_cursor < segment['raw_end'], 'privacy_segment_range')
        if segment['redacted']:
            _need(text[cursor:segment['end']] in _MARKERS, 'privacy_segment_marker')
        else:
            _need(segment['end'] - cursor == segment['raw_end'] - raw_cursor, 'privacy_literal_length')
        cursor, raw_cursor = segment['end'], segment['raw_end']
    _need(cursor == len(text) and (bool(text) or trace['raw_text_hash'] == _text_hash('')), 'privacy_segment_coverage')
    _need(trace['trace_hash'] == digest({key: value for key, value in trace.items() if key != 'trace_hash'}), 'privacy_trace_hash')
    return text, trace['segments']


def check_privacy_trace(trace, raw):
    """Cold-rebuild every value; re-signing modified intervals is insufficient."""
    _shape(trace)
    _need(trace == build_privacy_trace(raw), 'privacy_trace_does_not_cold_rebuild')


def raw_interval(trace, start, end):
    """Conservatively locate a masked half-open slice after caller cold-check.

    This performs structural/hash checks only; it cannot authenticate raw text
    without the caller's actual original. A zero-width position inside a marker
    also has no exact raw position: return its whole raw interval and both flags.
    At literal positions or interval boundaries, an empty slice maps exactly.
    """
    text, segments = _shape(trace)
    _need(type(start) is int and type(end) is int and 0 <= start <= end <= len(text), 'privacy_slice_range')
    if start == end:
        for segment in segments:
            if start == segment['start']:
                point = segment['raw_start']
                break
            if segment['start'] < start < segment['end']:
                if segment['redacted']:
                    return {'raw_start': segment['raw_start'], 'raw_end': segment['raw_end'],
                        'contains_redaction': True, 'partial_redaction': True}
                point = segment['raw_start'] + start - segment['start']
                break
        else:
            point = segments[-1]['raw_end'] if segments else 0
        return {'raw_start': point, 'raw_end': point, 'contains_redaction': False, 'partial_redaction': False}
    left = right = None
    contains = partial = False
    for segment in segments:
        if segment['end'] <= start:
            continue
        if segment['start'] >= end:
            break
        a, b = max(start, segment['start']), min(end, segment['end'])
        if segment['redacted']:
            lo, hi = segment['raw_start'], segment['raw_end']
            contains = True
            partial |= a > segment['start'] or b < segment['end']
        else:
            lo = segment['raw_start'] + a - segment['start']; hi = lo + b - a
        left = lo if left is None else min(left, lo)
        right = hi if right is None else max(right, hi)
    return {'raw_start': left, 'raw_end': right, 'contains_redaction': contains, 'partial_redaction': partial}

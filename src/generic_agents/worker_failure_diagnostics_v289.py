"""Bounded exception locations, never formatted exceptions or traceback locals.

The worker binds this pure snapshot to its original failed attempt only in the
existing lease-fenced final transaction. Nothing here grants retry or success.
"""
import copy
import re
from collections import deque
from uuid import UUID

from fastapi import HTTPException

from .contracts import digest


SCHEMA = 'worker-exception-locations-v289'
RECORD = 'worker-failure-diagnostic-v289'
KEY = 'failure_diagnostic_v289'
ACTION = 'worker.failure_diagnostic_v289'
MAX_FRAMES = 16
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}\Z')
_SPECIAL_FUNCTIONS = {'<module>', '<lambda>', '<listcomp>', '<dictcomp>', '<setcomp>', '<genexpr>'}


def _safe_name(value, fallback):
    return value if type(value) is str and _NAME.fullmatch(value) else fallback


def capture_failure(failure):
    """Read only type, HTTP status and code locations on the actual traceback.

    Module names come from Python module identity, never co_filename, which may
    contain an absolute user path. No args, message, detail, headers, locals,
    cause/context, SQL text or parameters are read, formatted or retained.
    """
    frames = deque(maxlen=MAX_FRAMES)
    trace = failure.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        module = frame.f_globals.get('__name__')
        if (type(module) is str and module.startswith('generic_agents.')
                and all(_NAME.fullmatch(part) for part in module.split('.'))):
            function = frame.f_code.co_name
            if function not in _SPECIAL_FUNCTIONS:
                function = _safe_name(function, '<unknown>')
            frames.append({'module': module.rsplit('.', 1)[-1] + '.py',
                'function': function, 'line': trace.tb_lineno})
        trace = trace.tb_next
    status = None
    if isinstance(failure, HTTPException):
        candidate = object.__getattribute__(failure, '__dict__').get('status_code')
        if type(candidate) is int and 100 <= candidate <= 599:
            status = candidate
    return {'schema': SCHEMA, 'error_type': _safe_name(type(failure).__name__, 'Exception'),
        'http_status': status, 'frames': list(frames)}


def attempt_record(job_id, workspace_id, attempt, diagnostic):
    """Create one closed, immutable-value record for a real failed Job attempt."""
    if (type(job_id) is not str or str(UUID(job_id)) != job_id
            or type(workspace_id) is not str or str(UUID(workspace_id)) != workspace_id
            or type(attempt) is not int or attempt < 1):
        raise ValueError('Invalid original failed worker attempt')
    if (type(diagnostic) is not dict
            or set(diagnostic) != {'schema', 'error_type', 'http_status', 'frames'}
            or diagnostic['schema'] != SCHEMA
            or _safe_name(diagnostic['error_type'], None) is None
            or not (diagnostic['http_status'] is None or type(diagnostic['http_status']) is int
                and 100 <= diagnostic['http_status'] <= 599)
            or type(diagnostic['frames']) is not list or len(diagnostic['frames']) > MAX_FRAMES):
        raise ValueError('Invalid bounded worker diagnostic')
    for frame in diagnostic['frames']:
        if (type(frame) is not dict or set(frame) != {'module', 'function', 'line'}
                or type(frame['module']) is not str or not frame['module'].endswith('.py')
                or _safe_name(frame['module'][:-3], None) is None
                or not (_safe_name(frame['function'], None) is not None
                    or frame['function'] in _SPECIAL_FUNCTIONS or frame['function'] == '<unknown>')
                or type(frame['line']) is not int or frame['line'] < 1):
            raise ValueError('Invalid worker code location')
    value = {'schema': RECORD, 'job_id': job_id, 'workspace_id': workspace_id,
        'attempt': attempt, 'final_status': 'failed', 'diagnostic': copy.deepcopy(diagnostic)}
    return {**value, 'record_hash': digest(value)}

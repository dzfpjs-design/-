"""Pure privacy/size/attempt-binding tests, not actual worker or PG evidence.

Compiled synthetic frames deliberately carry a private-looking filename and
payload. They verify that neither is serialized, not that a real Job ran.
"""
import copy
import json
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import worker_failure_diagnostics_v289 as diagnostic


SECRET = 'never-persist-private-payload-v289'
JOB = str(UUID(int=1))
WORKSPACE = str(UUID(int=2))


class UnprintableFailure(ValueError):
    def __str__(self):
        raise AssertionError('Diagnostics must never stringify an exception')


def captured(failure, *, depth=0, module='generic_agents.synthetic_worker_failure_v289'):
    namespace = {'__name__': module, 'failure': failure, 'private_payload': SECRET}
    code = compile('def invoke(depth):\n'
        '    private_headers = {"Authorization": private_payload}\n'
        '    private_sql_parameters = private_payload\n'
        '    if depth:\n'
        '        return invoke(depth - 1)\n'
        '    raise failure\n', '/Users/private-account/private-report.docx', 'exec')
    exec(code, namespace)
    try:
        namespace['invoke'](depth)
    except Exception as actual:
        return diagnostic.capture_failure(actual)
    raise AssertionError('Synthetic failure did not raise')


def assert_private_payload_absent(value):
    raw = json.dumps(value, sort_keys=True)
    for private in (SECRET, '/Users/', 'private-account', 'private-report', 'Authorization',
            'private_headers', 'private_sql_parameters', 'detail', 'headers', 'locals'):
        assert private not in raw


def test_capture_reads_locations_without_formatting_message_filename_or_locals():
    value = captured(UnprintableFailure(SECRET))
    assert set(value) == {'schema', 'error_type', 'http_status', 'frames'}
    assert value['schema'] == diagnostic.SCHEMA and value['error_type'] == 'UnprintableFailure'
    assert value['http_status'] is None
    assert value['frames'] == [{'module': 'synthetic_worker_failure_v289.py',
        'function': 'invoke', 'line': 6}]
    assert_private_payload_absent(value)


def test_only_innermost_sixteen_actual_generic_agents_locations_are_retained():
    value = captured(UnprintableFailure(SECRET), depth=40)
    assert len(value['frames']) == diagnostic.MAX_FRAMES == 16
    assert all(frame['module'] == 'synthetic_worker_failure_v289.py'
        and frame['function'] == 'invoke' and type(frame['line']) is int
        for frame in value['frames'])
    assert value['frames'][-1]['line'] == 6
    assert_private_payload_absent(value)


@pytest.mark.parametrize('module', ['third_party.client', 'generic_agents_extra.private', 'generic_agents.private/path'])
def test_non_package_or_invalid_module_identity_cannot_supply_a_location(module):
    value = captured(UnprintableFailure(SECRET), module=module)
    assert value['frames'] == []
    assert_private_payload_absent(value)


def test_http_status_is_retained_without_response_detail_or_headers():
    value = captured(HTTPException(403, detail={'private': SECRET}, headers={'Authorization': SECRET}))
    assert value['error_type'] == 'HTTPException' and value['http_status'] == 403
    assert_private_payload_absent(value)


@pytest.mark.parametrize('status', [True, 99, 600, '403'])
def test_non_http_status_values_are_omitted(status):
    value = captured(HTTPException(status, detail=SECRET))
    assert value['http_status'] is None


def test_failed_attempt_snapshot_has_exact_identity_and_stable_independent_hash():
    value = captured(UnprintableFailure(SECRET))
    record = diagnostic.attempt_record(JOB, WORKSPACE, 2, value)
    assert set(record) == {'schema', 'job_id', 'workspace_id', 'attempt', 'final_status',
        'diagnostic', 'record_hash'}
    assert record['job_id'] == JOB and record['workspace_id'] == WORKSPACE
    assert record['attempt'] == 2 and record['final_status'] == 'failed'
    assert record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'})
    before = copy.deepcopy(record)
    value['frames'].clear()
    assert record == before
    assert_private_payload_absent(record)


@pytest.mark.parametrize('attempt', [0, -1, True, '1'])
def test_invalid_attempt_cannot_be_anchored_as_a_failed_original(attempt):
    with pytest.raises(ValueError):
        diagnostic.attempt_record(JOB, WORKSPACE, attempt, captured(ValueError(SECRET)))


@pytest.mark.parametrize('change', [
    lambda value: value.update(message=SECRET),
    lambda value: value['frames'][0].update(module='/Users/private.py'),
    lambda value: value['frames'][0].update(function='/Users/private'),
    lambda value: value['frames'][0].update(line=True),
    lambda value: value.update(frames=value['frames'] * 17),
])
def test_record_cannot_smuggle_additional_payload_or_path_as_a_location(change):
    value = captured(ValueError(SECRET)); change(value)
    with pytest.raises(ValueError):
        diagnostic.attempt_record(JOB, WORKSPACE, 1, value)

"""Root executes only pure controls from the explicitly supplied reviewed file.

The external release does not need to copy .runtime. Set
GA_EVIDENCE_RUNTIME_SOURCE to that public controller; optionally pin
GA_EVIDENCE_RUNTIME_SHA256 to the frozen digest. No main/worker/helper loader runs.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest


SOURCE = Path(os.environ.get('GA_EVIDENCE_RUNTIME_SOURCE', str(
    Path(__file__).parents[1] / '.runtime/section-evidence-reading/actual_evidence_runtime.py')))
assert SOURCE.is_file() and not SOURCE.is_symlink(), 'Root must provide the reviewed GA_EVIDENCE_RUNTIME_SOURCE'
if os.environ.get('GA_EVIDENCE_RUNTIME_SHA256'):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == os.environ['GA_EVIDENCE_RUNTIME_SHA256']
spec = importlib.util.spec_from_file_location('reviewed_evidence_runtime_controls', SOURCE)
controls = importlib.util.module_from_spec(spec); spec.loader.exec_module(controls)
IMPORT_HELPERS = controls._helpers


@pytest.fixture(autouse=True)
def never_execute_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure runtime controls must not start a process, model, service or connection')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(controls, '_helpers', forbidden)
    monkeypatch.setattr(controls, '_worker', forbidden)


def contract(mode='preflight'):
    return {'schema': controls.CONTRACT, 'name': 'synthetic-controls-' + mode,
        'pg_port': 61628 if mode == 'preflight' else 61528, 'api_port': 18161 if mode == 'preflight' else 18160,
        'paid_authorized': mode == 'run', 'actor_id': '10000000-0000-4000-8000-000000000001',
        'workspace_id': '10000000-0000-4000-8000-000000000002',
        'auth_locator': {'path': '/private/tmp/synthetic-auth.json', 'sha256': 'a' * 64},
        'baseline': {key: 'b' * 64 for key in ('restore_sha256', 'rows_sha256', 'terminal_sha256')},
        'source_transition': {'path': '/private/tmp/synthetic-transition.json', 'sha256': 'c' * 64},
        'map_preview': {'path': '/private/tmp/synthetic-preview.json', 'sha256': 'd' * 64},
        'reduce_request_id': 'fer_10000000-0000-4000-8000-000000000003',
        'budget': {'job_id': '333a5d04-ce01-431b-bd15-a5c6566c410a', 'day': '2026-10-17',
            'job_row_sha256': 'e' * 64, 'previous_calls': 2, 'previous_tokens': 439462,
            'map_calls': 14, 'map_reserved_tokens': 1033979, 'maximum_calls': 15,
            'maximum_reserved_tokens': 1943195, 'peak_input_usd_per_million': '1.32',
            'peak_output_usd_per_million': '3.96', 'previous_estimated_usd': '0.59985816',
            'maximum_cumulative_usd': '4'},
        'deadlines': {'runtime_seconds': 4200, 'worker_seconds': 3000, 'decision_seconds': 600, 'cleanup_seconds': 180}}


def baseline():
    value = contract(); scope = value['workspace_id']; identity = value['budget']['job_id']
    budget = {'id': identity, 'workspace_id': scope, 'kind': 'cloud_budget', 'status': 'internal', 'version': 1,
        'data': {'day': '2026-10-17', 'calls': 2, 'tokens': 439462}}
    opaque = json.dumps(budget)
    value['budget']['job_row_sha256'] = hashlib.sha256(opaque.encode()).hexdigest()
    rows = {'SyntheticTable' + str(n): {} for n in range(100)}
    rows.update(Job={identity: opaque}, CloudCall={'old-paid': 'OPAQUE_OLD_PAID_NOT_JSON'},
        SetupReportDraft={'old-private': 'OPAQUE_OLD_PRIVATE_NOT_JSON'}, Audit={},
        SetupDraft={'old-configuration': 'OPAQUE_OLD_FULL_CONFIGURATION_NOT_JSON'}, User={'old-user': 'OPAQUE_USER'})
    assert len(rows) == 106
    return value, rows


def test_preflight_is_zero_dispatch_with_the_same_frozen_nonzero_budget_and_exact_ports():
    value = contract()
    assert controls.validate_contract(value, 'preflight') == value
    assert controls.validate_contract(contract('run'), 'run')['budget'] == value['budget']
    with pytest.raises(controls.GateError, match='fixed_runtime_ports'):
        controls.validate_contract(value, 'run')
    value = contract('run'); value['paid_authorized'] = False
    with pytest.raises(controls.GateError, match='paid_authorization'):
        controls.validate_contract(value, 'run')


@pytest.mark.parametrize('loader_fails', [False, True])
def test_reused_helper_import_does_not_parse_controller_mode_as_port_and_restores_argv(monkeypatch, loader_fails):
    original = ['controller.py', 'contract.json', 'static', 'preflight']
    monkeypatch.setattr(controls.sys, 'argv', original)
    def execute(module):
        # Reproduce the reused checks module's legacy import-time expression.
        assert (int(controls.sys.argv[3]) if len(controls.sys.argv) > 3 else 0) == 0
        if loader_fails:
            raise RuntimeError('synthetic-loader-error')
    fake = SimpleNamespace(loader=SimpleNamespace(exec_module=execute))
    monkeypatch.setattr(controls.importlib.util, 'spec_from_file_location', lambda *args: fake)
    monkeypatch.setattr(controls.importlib.util, 'module_from_spec', lambda spec: SimpleNamespace())
    if loader_fails:
        with pytest.raises(RuntimeError, match='synthetic-loader-error'):
            IMPORT_HELPERS()
    else:
        IMPORT_HELPERS()
    assert controls.sys.argv is original


def test_cleanup_selects_app_tree_groups_and_preserves_postgres_auxiliary_groups():
    known = {100: {'ppid': 1, 'pgid': 100}, 101: {'ppid': 100, 'pgid': 101},
        102: {'ppid': 100, 'pgid': 102}, 200: {'ppid': 1, 'pgid': 200},
        201: {'ppid': 200, 'pgid': 201}, 202: {'ppid': 201, 'pgid': 200},
        300: {'ppid': 1, 'pgid': 300}, 900: {'ppid': 1, 'pgid': 900}}
    assert controls.service_process_groups(known, {200, 300}) == {200, 201, 300}
    assert controls.service_process_groups(known, set()) == set()


def test_peak_bound_includes_old_cost_and_the_fifteen_full_output_ceilings():
    value = contract(); budget = value['budget']
    result = controls.Decimal(budget['previous_estimated_usd']) + controls.cost_bound(
        budget['maximum_reserved_tokens'], budget['maximum_calls'], budget)
    assert result == controls.Decimal('3.48927876') < controls.Decimal('4')


def test_explicit_stop_or_expired_decision_prevents_dispatch_even_when_authorization_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(controls.time, 'monotonic', lambda: 10)
    controls.save(tmp_path / 'reduce-authorization.json', {'consent': True}, exclusive=True)
    controls.dispatch_gate(tmp_path, 11)
    (tmp_path / 'stop').touch()
    with pytest.raises(controls.GateError, match='explicit_stop'):
        controls.dispatch_gate(tmp_path, 11)
    (tmp_path / 'stop').unlink()
    with pytest.raises(controls.GateError, match='dispatch_deadline'):
        controls.dispatch_gate(tmp_path, 10)


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(api_port=18158),
    lambda value: value.update(unknown_permission=True),
    lambda value: value['budget'].update(maximum_calls=16),
    lambda value: value['budget'].update(map_calls=True),
    lambda value: value['budget'].update(previous_tokens=0),
    lambda value: value['budget'].update(maximum_reserved_tokens=3000000),
    lambda value: value['budget'].update(peak_input_usd_per_million='0.1'),
    lambda value: value['budget'].update(maximum_cumulative_usd='100'),
    lambda value: value['deadlines'].update(worker_seconds=3001),
    lambda value: value['deadlines'].update(cleanup_seconds=181),
])
def test_changed_scope_budget_price_or_unbounded_runtime_never_authorizes_a_call(mutate):
    value = contract(); mutate(value)
    with pytest.raises(controls.GateError):
        controls.validate_contract(value, 'preflight')


def test_all_old_rows_including_paid_and_private_payloads_remain_opaque():
    value, before = baseline()
    assert controls.validate_delta(before, copy.deepcopy(before), value) == {}


@pytest.mark.parametrize('table', ['CloudCall', 'SetupReportDraft', 'SetupDraft', 'User'])
def test_any_old_opaque_row_change_is_rejected_without_decoding_it(table):
    value, before = baseline(); after = copy.deepcopy(before)
    identity = next(iter(after[table])); after[table][identity] += 'changed'
    with pytest.raises(controls.GateError, match='old_row_changed_' + table):
        controls.validate_delta(before, after, value)


@pytest.mark.parametrize('mutation', ['counter_without_call', 'metadata', 'day', 'unexpected_table'])
def test_budget_allowance_does_not_allow_other_metadata_or_unsourced_accounting(mutation):
    value, before = baseline(); after = copy.deepcopy(before); identity = value['budget']['job_id']
    budget = json.loads(after['Job'][identity])
    if mutation == 'counter_without_call': budget['data']['calls'] += 1
    elif mutation == 'metadata': budget['version'] += 1
    elif mutation == 'day': budget['data']['day'] = '2026-10-18'
    else: after['SyntheticTable0']['new-object'] = '{}'
    after['Job'][identity] = json.dumps(budget)
    with pytest.raises(controls.GateError):
        controls.validate_delta(before, after, value)


def test_sixteenth_new_call_is_rejected_even_if_budget_rows_were_consistently_increased():
    value, before = baseline(); after = copy.deepcopy(before); identity = value['budget']['job_id']
    for number in range(16):
        after['CloudCall']['new-' + str(number)] = json.dumps({'reserved_tokens': 10000, 'charged_tokens': 10000})
    budget = json.loads(after['Job'][identity]); budget['data'].update(calls=18, tokens=599462)
    after['Job'][identity] = json.dumps(budget)
    with pytest.raises(controls.GateError, match='call_count_exceeded'):
        controls.validate_delta(before, after, value)


def test_private_json_and_exclusive_attempt_files_reject_aliases_duplicate_keys_and_replacement(tmp_path):
    target = tmp_path / 'attempt.json'; controls.save(target, {'safe': 1}, exclusive=True)
    assert target.stat().st_mode & 0o777 == 0o600
    assert controls.read_json(target, private=True) == {'safe': 1}
    with pytest.raises(FileExistsError): controls.save(target, {'safe': 2}, exclusive=True)
    assert controls.read_json(target, private=True) == {'safe': 1}
    alias = tmp_path / 'alias.json'; alias.symlink_to(target)
    with pytest.raises(controls.GateError): controls.read_json(alias, private=True)
    link = tmp_path / 'hardlink.json'; os.link(target, link)
    with pytest.raises(controls.GateError): controls.read_json(target, private=True)
    link.unlink()
    duplicate = tmp_path / 'duplicate.json'; duplicate.write_text('{"safe":1,"safe":2}'); duplicate.chmod(0o600)
    with pytest.raises(controls.GateError, match='duplicate_json_key'): controls.read_json(duplicate, private=True)
    with pytest.raises(controls.GateError, match='unsafe_input_path'): controls.read_json(tmp_path / '.env')

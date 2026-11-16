"""Actual OCR cursor contracts over three or more sequential pages.

No IO adapter, DB, API, OCR, service or process is needed. The unchanged
production units/versions callers both spread the decoded scope into their
next cursor, so the decoder must return validated payload without its seal.
Authored here for root's separate isolated pure run; not executed by author.
"""
import base64
import copy
import json

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr as o
from generic_agents.contracts import digest


def scope(kind):
    value = {
        'workspace_id': '20000000-0000-4000-8000-000000000001',
        'material_id': '20000000-0000-4000-8000-000000000002',
        'run_id': '20000000-0000-4000-8000-000000000003',
        'kind': kind,
    }
    if kind == 'units':
        value['generation'] = 3
    else:
        value['unit_id'] = '20000000-0000-4000-8000-000000000004'
    return value


def token(value):
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).decode().rstrip('=')


def decoded(token_value):
    return json.loads(base64.urlsafe_b64decode(token_value + '=' * (-len(token_value) % 4)))


@pytest.mark.parametrize('kind,total,limit,pages', [
    ('units', 235, 100, 3),
    ('versions', 97, 20, 5),
])
def test_actual_cursor_functions_read_every_row_across_at_least_three_pages(kind, total, limit, pages):
    expected_scope = scope(kind)
    frozen = {**expected_scope, 'upper': total, 'last': 0}
    seen = []
    after = None
    reads = 0
    while True:
        if after is not None:
            frozen = o.cursor_decode(after, expected_scope)
        # This is the existing two callers' frozen last/upper projection and
        # exact next-cursor command, with only the row lookup kept in memory.
        found = list(range(frozen['last'] + 1, frozen['upper'] + 1))[:limit + 1]
        seen.extend(found[:limit])
        reads += 1
        if len(found) <= limit:
            break
        after = o.cursor_encode({**frozen, 'last': found[limit - 1]})
        assert decoded(after)['upper'] == total
    assert seen == list(range(1, total + 1))
    assert reads == pages


@pytest.mark.parametrize('kind', ['units', 'versions'])
def test_existing_single_cursor_wire_schema_is_unchanged_and_seal_is_not_payload(kind):
    plain = {**scope(kind), 'upper': 235 if kind == 'units' else 97, 'last': 20}
    original = copy.deepcopy(plain)
    # The pre-fix wire contract is independently produced here. Old first-page
    # cursors remain readable; only an internal hash key stops propagating.
    legacy = token({**plain, 'hash': digest(plain)})
    assert o.cursor_encode(plain) == legacy
    assert o.cursor_decode(legacy, scope(kind)) == plain
    assert 'hash' not in o.cursor_decode(legacy, scope(kind))
    assert plain == original
    assert set(decoded(legacy)) == set(plain) | {'hash'}


@pytest.mark.parametrize('kind', ['units', 'versions'])
@pytest.mark.parametrize('field', ['workspace_id', 'material_id', 'run_id', 'kind', 'specific'])
def test_valid_cursor_for_changed_scope_still_rejects(kind, field):
    expected_scope = scope(kind)
    after = o.cursor_encode({**expected_scope, 'upper': 235, 'last': 100})
    changed = copy.deepcopy(expected_scope)
    key = ('generation' if kind == 'units' else 'unit_id') if field == 'specific' else field
    changed[key] = changed[key] + 1 if key == 'generation' else 'different-scope'
    with pytest.raises(HTTPException) as error:
        o.cursor_decode(after, changed)
    assert error.value.status_code == 409


@pytest.mark.parametrize('kind', ['units', 'versions'])
@pytest.mark.parametrize('damage', ['hash', 'upper', 'last', 'missing_hash'])
def test_cursor_tampering_does_not_acquire_a_valid_next_page(kind, damage):
    expected_scope = scope(kind)
    value = decoded(o.cursor_encode({**expected_scope, 'upper': 235, 'last': 100}))
    if damage == 'hash':
        value['hash'] = '0' * 64
    elif damage == 'missing_hash':
        value.pop('hash')
    else:
        value[damage] += 1
    with pytest.raises(HTTPException) as error:
        o.cursor_decode(token(value), expected_scope)
    assert error.value.status_code == 409

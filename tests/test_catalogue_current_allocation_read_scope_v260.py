"""Current entry-point regression over the real disposable PG fixture.

The reused fixture has explicit synthetic immutable-source and HTTP-response
boundaries. Native validation, Current prepare/check, IAM, configuration, Job,
reservation callback, Call and capture paths remain production implementations.
These tests neither prove the original report nor provider/business quality.
Only Root executes them. Spies observe and delegate; none returns fake success.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import catalogue_current_allocation_v260 as current
from generic_agents import gateway
from generic_agents import models as m
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_catalogue_allocation_read_scope_v213 as reuse
from generic_agents.contracts import digest
from generic_agents.report_evidence_strict_map import StrictMapError
from generic_agents.report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_catalogue_current_allocation_v260 import (
    BASE, allocation_scene, reuse_scene, quote, start, finish, advance, owned_inventory)


@pytest.fixture
def current_preview(allocation_scene):
    """Actual API quote plus actual private preparation, without consuming it."""
    s = allocation_scene
    body, public = quote(s)
    with Session(s.bound) as db:
        value = current.prepare(db, s.p, current.PreviewBody.model_validate(body))
    assert current.preview_view(value) == public
    assert not s.posts
    return s, body, public, value


@pytest.fixture
def full_checks(current_preview, monkeypatch):
    original = native._check_allocation_evidence_uncached
    calls = []

    def counted(value):
        entry = {'digest': digest(value), 'scope': reuse._CURRENT.get(), 'returned': False}
        calls.append(entry)
        result = original(value)
        entry['returned'] = True
        return result

    monkeypatch.setattr(native, '_check_allocation_evidence_uncached', counted)
    return calls


def spelling(value):
    return canonical_json(value).encode('utf-8'), digest(value)


def pure_inactive():
    state = reuse._CURRENT.get()
    return state is None or state.closed or not state.current()


def test_current_public_check_owns_fresh_scope_when_outer_eight_entries_are_full(
        current_preview, full_checks):
    s, _, public, value = current_preview
    before = spelling(value)
    inner_before = spelling(value['preparation']['native_preparation'])
    evidence_before = spelling(value['allocation_evidence'])
    assert reuse.MAX_ENTRIES == 8
    with reuse.allocation_read_scope(s.p):
        outer = reuse._CURRENT.get()
        assert outer is not None and outer.current()
        for index in range(8):
            old_evidence = copy.deepcopy(value['allocation_evidence'])
            old_evidence['note'] += '；outer inventory entry ' + str(index)
            assert native.check_allocation_evidence(old_evidence) == old_evidence
        assert len(outer.entries) == 8 and len(full_checks) == 8
        entries, retained = list(outer.entries), outer.retained_bytes
        result = current.check_preview(copy.deepcopy(value), s.p)
        assert len(full_checks) == 9, 'Current must reuse inside its own fresh check, not miss 18 times'
        fresh = full_checks[-1]['scope']
        assert fresh is not None and fresh is not outer
        assert fresh.closed and fresh.entries == [] and fresh.retained_bytes == 0
        assert reuse._CURRENT.get() is outer and outer.current()
        assert outer.entries == entries and outer.retained_bytes == retained
        assert spelling(result) == before
        assert spelling(result['preparation']['native_preparation']) == inner_before
        assert spelling(result['allocation_evidence']) == evidence_before
        assert current.preview_view(result) == public
    assert outer.closed and outer.entries == [] and reuse._CURRENT.get() is None
    assert spelling(value) == before


def reseal_changed_containers(value):
    """Refresh untrusted container hashes, never invent a valid source proof."""
    inner = value['preparation']['native_preparation']
    inner['allocation_evidence_hash'] = digest(inner['allocation_evidence'])
    inner['generation_contract']['allocation_evidence_hash'] = inner['allocation_evidence_hash']
    inner['generation_contract_hash'] = digest(inner['generation_contract'])
    inner['preparation_hash'] = digest({k: v for k, v in inner.items() if k != 'preparation_hash'})
    wrapper = value['preparation']
    wrapper['preparation_hash'] = digest({k: v for k, v in wrapper.items() if k != 'preparation_hash'})
    value['preview_hash'] = digest({k: v for k, v in value.items() if k != 'preview_hash'})


def test_current_resealed_actual_source_bytes_cannot_borrow_a_previous_evidence_success(
        current_preview, full_checks):
    s, _, _, value = current_preview
    original = copy.deepcopy(value)
    bad = copy.deepcopy(value)
    evidence = bad['preparation']['native_preparation']['allocation_evidence']
    directory = evidence['source_context']['catalog']['index']
    span = next(iter(directory))
    directory[span]['text'] += '；实际原文内容已改变，容器自签不能使它成立'
    bad['allocation_evidence'] = copy.deepcopy(evidence)
    bad['source_context'] = copy.deepcopy(evidence['source_context'])
    reseal_changed_containers(bad)
    assert bad['preview_hash'] == digest({k: v for k, v in bad.items() if k != 'preview_hash'})
    assert digest(evidence) != digest(value['allocation_evidence'])
    with reuse.allocation_read_scope(s.p):
        assert native.check_allocation_evidence(value['allocation_evidence']) == value['allocation_evidence']
        assert len(full_checks) == 1
        for index in range(2):
            with pytest.raises((StrictMapError, HTTPException)):
                current.check_preview(copy.deepcopy(bad), s.p)
            assert len(full_checks) == index + 2
            checked = full_checks[-1]
            assert checked['digest'] == digest(evidence) and not checked['returned']
            assert checked['scope'].closed and checked['scope'].entries == []
        assert native.check_allocation_evidence(value['allocation_evidence']) == value['allocation_evidence']
        assert len(full_checks) == 3
    assert spelling(value) == spelling(original)


def test_each_current_public_check_revalidates_and_returned_local_edits_do_not_pollute_the_next(
        current_preview, full_checks):
    s, _, _, value = current_preview
    original = spelling(value)
    first = current.check_preview(copy.deepcopy(value), s.p)
    assert len(full_checks) == 1
    first_scope = full_checks[-1]['scope']
    assert first_scope.closed
    first['allocation_evidence']['document']['businesses'][0]['purpose'] += '；仅调用者本地修改'
    first['preparation']['native_preparation']['allocation_evidence']['note'] += '；另一本地副本'
    second = current.check_preview(copy.deepcopy(value), s.p)
    assert len(full_checks) == 2
    second_scope = full_checks[-1]['scope']
    assert second_scope is not first_scope and second_scope.closed
    assert spelling(second) == original == spelling(value)
    assert second['allocation_evidence']['document'] is not first['allocation_evidence']['document']
    assert second['preparation']['native_preparation'] is not first['preparation']['native_preparation']
    assert reuse._CURRENT.get() is None


@pytest.mark.parametrize('fault', ['source-unavailable', 'configuration-advance', 'membership-viewer'])
def test_fresh_current_preview_still_rechecks_source_configuration_and_real_membership(
        current_preview, full_checks, fault):
    s, body, _, value = current_preview
    fresh_body = {**body, 'activity_id': str(uuid4())}
    # There is no Consumer/grant occupying either quote. A valid control must
    # succeed, so a later refusal cannot merely be an old-grant duplicate.
    assert s.client.post(BASE + '/preview-allocation', json=fresh_body).status_code == 200
    with reuse.allocation_read_scope(s.p):
        native.check_allocation_evidence(value['allocation_evidence'])
        if fault == 'source-unavailable':
            s.source_unavailable = True  # Explicit negative of the fixture's labelled source boundary.
            expected = 409
        elif fault == 'configuration-advance':
            changed = advance(s, 'read-scope回归真实前进配置')
            assert changed['revision'] > s.binding['target_configuration']['revision']
            expected = 409
        else:
            with Session(s.bound) as db:
                member = db.scalar(select(m.Membership).where(
                    m.Membership.workspace_id == s.p.workspace_id,
                    m.Membership.user_id == s.p.user_id))
                assert member is not None and member.role in {'admin', 'operator'}
                member.role = 'viewer'
                db.commit()
            assert s.client.get('/api/me').json()['role'] == 'viewer'
            expected = 403
        before = owned_inventory(s)
        reads = len(s.source_reads)
        response = s.client.post(BASE + '/preview-allocation', json=fresh_body)
        assert response.status_code == expected
        assert owned_inventory(s) == before and not s.posts
        if fault == 'source-unavailable':
            assert len(s.source_reads) > reads
        assert reuse._CURRENT.get().current(), 'The earlier pure scope is not execution authority.'
    assert reuse._CURRENT.get() is None


def test_current_dispatch_closes_pure_scope_before_real_gateway_callback_and_transport(
        current_preview, full_checks, monkeypatch):
    s, body, public, _ = current_preview
    request, _, created = start(s, body, public)
    real_cloud = gateway.cloud_json
    real_stream = gateway.httpx.Client.stream  # The existing fixture's explicit synthetic HTTP boundary.
    gateway_entries, callbacks, transports = [], [], []

    def assert_pure_checks_closed():
        assert pure_inactive()
        assert all(entry['scope'] is None or entry['scope'].closed
                   or not entry['scope'].current() for entry in full_checks)

    def observed_cloud(*args, **kwargs):
        assert_pure_checks_closed()
        assert current._DISPATCH.get() is not None, 'The genuine stored Current dispatch proof is still required.'
        gateway_entries.append(None)
        original_reserved = kwargs['on_reserved']

        def observed_reserved(call_id):
            assert_pure_checks_closed()
            before = len(s.source_reads)
            result = original_reserved(call_id)  # Real reservation/lease/source/IAM callback, unchanged result.
            assert_pure_checks_closed()
            fresh_reads = s.source_reads[before:]
            assert 'origin' in fresh_reads and 'source' in fresh_reads
            callbacks.append({'call_id': call_id, 'reads': tuple(fresh_reads)})
            return result

        return real_cloud(*args, **{**kwargs, 'on_reserved': observed_reserved})

    def observed_stream(self, *args, **kwargs):
        assert_pure_checks_closed()
        assert len(callbacks) == 1, 'Fresh validation must finish before HTTP transport is entered.'
        transports.append(None)
        return real_stream(self, *args, **kwargs)

    monkeypatch.setattr(gateway, 'cloud_json', observed_cloud)
    monkeypatch.setattr(gateway.httpx.Client, 'stream', observed_stream)
    view = finish(s, created, public)
    assert view['status'] in {'review', 'needs_clarification'}
    assert len(gateway_entries) == len(callbacks) == len(transports) == len(s.posts) == 1
    with Session(s.bound) as db:
        row = db.get(Consumer, created['id'])
        call = db.get(m.CloudCall, callbacks[0]['call_id'])
        assert row.reserved_call_id == call.id and row.data['dispatch_started'] is True
        actual = call.data['gateway_dispatch']
        assert actual['identity']['task_id'] == current.TASK_PREFIX + request['current_scope_hash']
        assert actual['reservation_request_hash'] == request['request_hash']
    assert_pure_checks_closed()
    assert reuse._CURRENT.get() is None

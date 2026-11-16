"""Explicit source dispatch and existing human-confirmation origin continuity.

All data are public synthetic. Selected-source/current material IO is an explicit
dependency seam; real source selection/capture/IAM admission is tested elsewhere.
Actual V166 inventory, value/confirmation, V169 Audit/origin and design functions
run here. No real report/provider/PG or quality authority is claimed. Root runs.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import report_object_revision_source_v166 as source
from generic_agents import report_object_revision_store_v166 as revisions
from generic_agents import report_object_adoption_source_v169 as adoption
from generic_agents import report_object_adoption_v169 as adoption_store
from generic_agents import report_object_configuration_v169 as design
from generic_agents import report_saved_map_review_source_v178 as selected
from generic_agents.contracts import digest
from generic_agents.models import SetupReportDraft
from tests import test_report_object_adoption_source_v169 as fixture


def selected_basis(old):
    return {**copy.deepcopy(old),'source_kind':'saved-map-design-v178',
        'consumer_id':fixture.uid(601),'selection_id':fixture.uid(602),'selection_hash':'f'*64}


def bundle():
    """Author a separate valid new-basis human history, not relabel old rows."""
    data=fixture.bundle();basis=selected_basis(data.current['basis']);previous=None
    for command in data.commands:
        snapshot=command.data['record_snapshot']
        snapshot['data']['basis']=copy.deepcopy(basis);snapshot['basis_hash']=digest(basis)
        body=command.data['body'];body['basis_hash']=digest(basis)
        raw=json.dumps(body,ensure_ascii=False,indent=1).encode('utf-8')
        command.data['wire']=raw.decode('utf-8');command.wire_sha256=hashlib.sha256(raw).hexdigest()
        command.body_hash=digest(body);command.data['previous_hash']=previous
        command.data['result']=revisions._result(command)
        command.command_hash=digest(revisions._command_pin(command))
        next(event for event in data.audits if event.object_id==command.id).data=revisions._audit_pin(command)
        previous=digest(snapshot)
    last=data.commands[-1].data['record_snapshot']
    data.row.basis_hash=digest(basis);data.row.data=copy.deepcopy(last['data']);data.row.record_hash=previous
    data.current['basis']=copy.deepcopy(basis);data.current['basis_hash']=digest(basis)
    return data


@pytest.mark.parametrize('new',[False,True])
def test_basis_has_only_two_complete_closed_families_and_returns_a_detached_value(new):
    basis=fixture.bundle().current['basis']
    if new:basis=selected_basis(basis)
    before=copy.deepcopy(basis);checked=revisions._basis(basis,fixture.REPORT)
    assert checked==before and checked is not basis
    checked['model_result_hash']='0'*64
    assert basis==before
    assert len(basis)==(11 if new else 7)
    if new:assert digest(basis)!=digest({key:basis[key] for key in revisions.BASIS_KEYS})


@pytest.mark.parametrize('fault',['missing_kind','missing_consumer','missing_selection','missing_hash',
    'wrong_kind','kind_as_hash','bad_consumer','bad_selection','bad_hash','bool_revision','extra_private_note'])
def test_partial_or_mixed_new_basis_never_silently_becomes_old_or_treats_source_kind_as_a_hash(fault):
    basis=selected_basis(fixture.bundle().current['basis'])
    if fault.startswith('missing_'):basis.pop({'missing_kind':'source_kind','missing_consumer':'consumer_id',
        'missing_selection':'selection_id','missing_hash':'selection_hash'}[fault])
    elif fault=='wrong_kind':basis['source_kind']='saved-map-design-v176'
    elif fault=='kind_as_hash':basis['source_kind']='a'*64
    elif fault=='bad_consumer':basis['consumer_id']='a'*64
    elif fault=='bad_selection':basis['selection_id']=True
    elif fault=='bad_hash':basis['selection_hash']=fixture.uid(602)
    elif fault=='bool_revision':basis['report_revision']=True
    else:basis['private_note']='not a public source field'
    before=copy.deepcopy(basis)
    with pytest.raises(HTTPException) as caught:revisions._basis(basis,fixture.REPORT)
    assert caught.value.status_code==409 and basis==before


@pytest.mark.parametrize('lock',[False,True])
def test_selected_loader_dispatches_before_legacy_machine_decode_and_preserves_lock_mode(lock,monkeypatch):
    data=bundle();db=object();calls=[]
    def loaded(actual_db,p,report_id,*,lock=False):
        assert actual_db is db and p is fixture.P and report_id==fixture.REPORT
        calls.append(lock);return data.current
    def forbidden(*args,**kwargs):raise AssertionError('A selected new source must not re-enter the failed old producer decoder')
    monkeypatch.setattr(selected,'load_selected_source',loaded)
    monkeypatch.setattr(source.pipeline,'check_row',forbidden)
    assert source.load_source(db,fixture.P,fixture.REPORT,lock=lock) is data.current
    assert calls==[lock]


def test_only_true_no_selected_trace_continues_the_complete_legacy_owner_lookup(monkeypatch):
    calls=[]
    monkeypatch.setattr(selected,'load_selected_source',lambda *args,**kwargs:None)
    class EmptyOwnerIO:
        def scalar(self,statement):
            expected=select(SetupReportDraft).where(SetupReportDraft.id==fixture.REPORT,
                SetupReportDraft.workspace_id==fixture.WS,SetupReportDraft.actor_id==fixture.ACTOR)
            left,right=statement.compile(),expected.compile()
            assert str(left)==str(right) and left.params==right.params and statement._for_update_arg is None
            calls.append('legacy-owner');return None
    with pytest.raises(HTTPException) as caught:source.load_source(EmptyOwnerIO(),fixture.P,fixture.REPORT)
    assert caught.value.status_code==404 and calls==['legacy-owner']


def test_selected_inventory_or_source_failure_propagates_without_legacy_fallback(monkeypatch):
    failure=HTTPException(409,'selected source independent ledger failure')
    def broken(*args,**kwargs):raise failure
    monkeypatch.setattr(selected,'load_selected_source',broken)
    with pytest.raises(HTTPException) as caught:source.load_source(object(),fixture.P,fixture.REPORT)
    assert caught.value is failure


@pytest.mark.parametrize('lock',[False,True])
def test_latest_real_human_confirmation_and_adoption_design_keep_the_full_new_basis_hash(lock,monkeypatch):
    data=bundle();db=fixture.ReadIO(data);fixture.current_io(monkeypatch,data,db)
    saved=adoption.load_confirmed(db,fixture.P,fixture.REPORT,lock=lock)
    assert saved['basis']==data.current['basis'] and set(saved['basis'])==revisions.SELECTED_BASIS_KEYS
    assert saved['origin']['basis_hash']==digest(saved['basis'])
    assert saved['origin']==fixture.expected_origin(data)
    assert saved['result']==data.row.data['value']['result']
    assert 'note' not in saved and '独立私人笔记' not in json.dumps(saved,ensure_ascii=False)
    material=design.build_design(saved['result'],saved['object_identity_confirmations'],saved['origin'],
        **adoption_store.context(saved['origin'],saved['basis'],fixture.uid(610)))
    assert material['review_origin']['basis_hash']==digest(data.current['basis'])
    assert design.check_design(material)==material
    assert material['model_result_hash']==data.current['basis']['model_result_hash']
    assert ('current-source-io',lock) in db.calls and ('material-io',lock) in db.calls


def test_shared_historical_origin_keeps_selected_confirmation_without_current_source_or_author_permissions(monkeypatch):
    data=bundle();expected=fixture.expected_origin(data);db=fixture.ReadIO(data,audit_lock=False)
    def forbidden(*args,**kwargs):raise AssertionError('Saved origin history cannot renew or substitute current source/author authorization')
    monkeypatch.setattr(source,'load_source',forbidden)
    from generic_agents import relations
    monkeypatch.setattr(relations,'lock_current_actor',forbidden)
    reader=NS(user_id=fixture.uid(650),workspace_id=fixture.WS,role='viewer')
    saved=adoption.check_saved_origin(db,reader,expected)
    assert saved['basis']==data.current['basis'] and saved['origin']==expected
    assert saved['origin']['basis_hash']==digest(saved['basis']) and 'note' not in saved


def test_source_failure_keeps_saved_selected_human_history_readable_but_blocks_new_writes(monkeypatch):
    data=bundle();db=fixture.ReadIO(data)
    def unavailable(*args,**kwargs):raise HTTPException(409,'selected source no longer current')
    monkeypatch.setattr(source,'load_source',unavailable)
    view=revisions.detail(db,fixture.P,fixture.REPORT)
    assert view['basis']==data.current['basis'] and view['basis_hash']==digest(data.current['basis'])
    assert view['value']==data.row.data['value'] and len(view['draft_history'])==2
    assert view['source_available'] is False and view['can_save'] is False and view['can_confirm'] is False
    assert view['source_issue'] and view['value']['note']==data.row.data['value']['note']


def test_detail_actor_shared_mutex_precedes_all_report_shared_locks_and_the_selected_source_reader(monkeypatch):
    data=bundle();db=fixture.ReadIO(data);seen=[]
    def unavailable(actual_db,p,report_id,lock=False):
        assert actual_db is db and p is fixture.P and report_id==fixture.REPORT and lock is False
        acquired=[item[1] for item in db.calls if item[0]=='lock']
        assert acquired[0]=='actor' and acquired.index('actor')<acquired.index('report')
        seen.append('selected-reader-after-actor-report')
        raise HTTPException(409,'independent current source is unavailable')
    monkeypatch.setattr(source,'load_source',unavailable)
    result=revisions.detail(db,fixture.P,fixture.REPORT)
    assert seen==['selected-reader-after-actor-report']
    assert result['value']==data.row.data['value'] and result['can_save'] is False

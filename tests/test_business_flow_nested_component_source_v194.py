"""Explicit component recovery from a failed original; public I/O boundaries.

Actual V193/194 source and diagnostic checks run. No real capture, IAM, PG,
provider success or execution qualification is claimed by these fixtures.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_review_source as source
from generic_agents import business_flow_review_api as api
from generic_agents import business_flow_review_store as store
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_business_flow_review_api_pure import no_io
from tests.test_business_flow_nested_source_api_v193 import nested_case
from tests.test_report_business_flow_protocol_v182 import response


def failed_case(monkeypatch):
    c=nested_case(monkeypatch)
    c.value['causality']['input_bindings'][0]['contract']['unit']='m'
    c.row.status='failed';c.row.data['projection']=None
    c.saved['body']=response(canonical_json(c.value))
    return c


def test_original_failed_causality_is_not_relabelled_success_when_only_plan_is_recovered(monkeypatch):
    c=failed_case(monkeypatch);before=copy.deepcopy((c.row.data,c.saved))
    loaded=source.load(c.db,c.p,c.row.id);binding=loaded['source_binding']
    assert binding['schema']=='business-flow-nested-component-review-source-v194'
    assert binding['generation_profile']=='flow-v193' and binding['value_schema']=='report-business-flow-nested-plan-v192'
    assert binding['component_path']=='/plan' and binding['original_protocol_valid'] is False
    assert store._source(binding,c.row.id)==binding
    assert loaded['baseline_value']==c.value['plan'] and '_nested_causal_projection' not in loaded
    recovery=loaded['generation_recovery']
    assert recovery['component_only'] is True and recovery['original_protocol_valid'] is False
    assert recovery['component_validation_hash']==binding['component_validation_hash']
    assert recovery['issues'] and all(x[k] is False for x in [recovery] for k in source.FALSE_FLAGS)
    diagnostic=source.diagnose(loaded,loaded['baseline_value'])
    assert diagnostic['available'] and diagnostic['structural_valid'] and diagnostic['source_valid']
    assert (c.row.data,c.saved)==before and c.row.status=='failed' and c.row.data['projection'] is None


@pytest.mark.parametrize('fault',['protocol_flag','component_path','missing_marker','old_profile','hash','extra'])
def test_component_source_is_closed_and_never_authorizes_an_original_success(monkeypatch,fault):
    c=failed_case(monkeypatch);binding=source.load(c.db,c.p,c.row.id)['source_binding']
    if fault=='protocol_flag':binding['original_protocol_valid']=True
    if fault=='component_path':binding['component_path']='/causality'
    if fault=='missing_marker':binding['schema']='business-flow-nested-review-source-v193'
    if fault=='old_profile':binding['generation_profile']='flow-v190'
    if fault=='hash':binding['component_validation_hash']='invalid'
    if fault=='extra':binding['permission']=True
    with pytest.raises(HTTPException):store._source(binding,c.row.id)


def test_real_component_view_preserves_failure_warning_after_a_saved_manual_edit(monkeypatch):
    c=failed_case(monkeypatch);loaded=source.load(c.db,c.p,c.row.id)
    value=copy.deepcopy(loaded['baseline_value']);value['business']['flows'][0]['name']='修订流程名称'
    saved={'schema':store.VIEW,'attempt_id':c.row.id,'version':2,'value':value,
        'baseline_value':copy.deepcopy(loaded['baseline_value']),'source_binding':copy.deepcopy(loaded['source_binding']),
        'note':'人的工作必须保留','history':[],'status':'incomplete',**source.FALSE_FLAGS}
    monkeypatch.setattr(store,'get',lambda *_:copy.deepcopy(saved))
    detail=api.detail(c.db,c.p,c.row.id)
    assert detail['value']==value and detail['version']==2 and detail['note']==saved['note']
    assert 'generation_causality' not in detail
    assert detail['generation_recovery']['original_protocol_valid'] is False
    assert detail['source_available'] and all(detail[k] is False for k in source.FALSE_FLAGS)
    with pytest.raises(HTTPException) as error:source.current_review_source(c.db,c.p,c.row.id,saved)
    assert error.value.status_code==422


def test_bad_original_source_or_a_successful_whole_answer_cannot_use_component_recovery(monkeypatch):
    c=failed_case(monkeypatch)
    c.value['plan']['business']['flows'][0]['completion']['attribution']['citations']=['w:p99999/s1']
    c.saved['body']=response(canonical_json(c.value))
    with pytest.raises(HTTPException) as error:source.load(c.db,c.p,c.row.id)
    assert error.value.status_code==409 and c.row.data['projection'] is None
    c=nested_case(monkeypatch);c.row.status='failed';c.row.data['projection']=None
    with pytest.raises(HTTPException) as error:source.load(c.db,c.p,c.row.id)
    assert error.value.status_code==409

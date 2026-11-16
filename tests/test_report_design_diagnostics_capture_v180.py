"""Actual local capture/native-inspection/diagnostic integration, no provider/DB."""
import json
import pytest
from generic_agents import report_saved_map_design_consumer_v176 as app
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.contracts import digest
from tests.test_report_saved_map_design_consumer_v178 import scene as original_scene, capture_context, identity, paid_response
from tests.test_report_saved_map_design_consumer_v179 import scene as compatible_scene, paid_response as compatible_response


@pytest.mark.parametrize('scene',[original_scene,compatible_scene])
@pytest.mark.parametrize('malformed',[False,True])
def test_original_capture_reports_safe_counts_or_unavailable_without_mutation(tmp_path,monkeypatch,scene,malformed):
    _,_,_,source,row,*_=scene();row.reserved_call_id=identity(188)
    response=compatible_response if scene is compatible_scene else paid_response
    body=json.loads(response(row)['body']);wire=json.loads(body['choices'][0]['message']['content'])
    wire['reconciliation_groups']=wire['reconciliation_groups'][:1]
    if malformed:wire['result']['extra_private_content']='MUST_NOT_LEAK_AS_DIAGNOSTIC'
    body['choices'][0]['message']['content']=json.dumps(wire,ensure_ascii=False)
    raw=json.dumps(body,ensure_ascii=False).encode();root=tmp_path/'capture';monkeypatch.setattr(bridge,'root',lambda:root);ctx=capture_context(row)
    with capture.prepare(root,ctx) as writer:
        writer.mark_request_started();writer.begin_response(200,None,'diagnostic-fixture');writer.append(raw);writer.finish(eof=True)
    before=digest(row.data);result=app._capture_summary(row);check=result['content_check']
    assert result['http_status']==200 and result['response_complete'] and result['finish_reason']=='stop'
    assert check['expected_count']==source['snapshot']['claim_count']
    assert check['semantic_quality_verified'] is False and check['can_apply'] is False
    if malformed:
        assert check['available'] is False and check['covered_count'] is None and check['problem']=='invalid_wire_shape'
    else:
        assert check['available'] and check['covered_count']==1 and check['missing_count']==check['expected_count']-1
    assert 'MUST_NOT_LEAK' not in json.dumps(result) and wire['result']['domain'] not in json.dumps(result)
    assert digest(row.data)==before
    state=capture.load(root,ctx);assert capture.read_body(root,ctx,state['capture_hash'])==raw


def test_legacy_capture_summary_contract_is_unchanged(tmp_path,monkeypatch):
    _,_,_,_,row,*_=original_scene(indexed=False);row.reserved_call_id=identity(188)
    raw=paid_response(row)['body'];root=tmp_path/'legacy';monkeypatch.setattr(bridge,'root',lambda:root);ctx=capture_context(row)
    with capture.prepare(root,ctx) as writer:
        writer.mark_request_started();writer.begin_response(200,None,'legacy-fixture');writer.append(raw);writer.finish(eof=True)
    result=app._capture_summary(row)
    assert result['finish_reason']=='stop' and 'content_check' not in result

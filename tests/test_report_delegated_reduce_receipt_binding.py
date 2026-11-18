"""Receipt binding uses synthetic hashes; it never contains source text."""
from types import SimpleNamespace
import copy
import pytest
from generic_agents.contracts import digest
from generic_agents import report_evidence_delegated_reduce_preflight as subject


def make_values():
    principal=SimpleNamespace(user_id='actor', workspace_id='workspace')
    command=SimpleNamespace(revision=3,request_id='synthetic-request')
    row=SimpleNamespace(id='draft',data={'preview':{'map_preparation':{'plan':{'plan_hash':'a'*64}}}})
    reduction={'map_inputs_hash':'b'*64,'map_plan_hash':'a'*64,'payload':'synthetic-only'}
    reduction['preparation_hash']=digest(reduction)
    prepared={'preview_hash':'c'*64,'reduction_preparation':reduction,
        'body':{'revision':command.revision,'request_id':command.request_id}}
    base={'schema':'delegated-reduce-readonly-preflight-v1','draft_id':row.id,'actor_id':principal.user_id,
        'workspace_id':principal.workspace_id,'revision':command.revision,'reduction_preview_hash':prepared['preview_hash'],
        'reduction_preparation_hash':digest(reduction),'map_plan_hash':'d'*64}
    value={**base,'preflight_hash':digest(base)}
    return value,prepared,row,principal,command


def test_receipt_binding_uses_true_plan_and_input_hashes_without_source():
    value,prepared,row,p,command=make_values()
    binding=subject._receipt_binding(value,prepared,row,p,command)
    assert binding['map_plan_hash']=='a'*64 and binding['map_grant_manifest_hash']=='d'*64
    assert binding['map_inputs_hash']=='b'*64 and binding['request_id']==command.request_id
    assert binding['reduction_preparation_hash']==prepared['reduction_preparation']['preparation_hash']
    assert binding['binding_hash']==digest({k:v for k,v in binding.items() if k!='binding_hash'})
    assert 'payload' not in str(binding) and 'synthetic-only' not in str(binding)


@pytest.mark.parametrize('change', ['plan','inputs','preparation','preflight','identity','revision','request'])
def test_receipt_binding_rejects_changed_inputs(change):
    value,prepared,row,p,command=make_values()
    if change=='plan': row.data['preview']['map_preparation']['plan']['plan_hash']='f'*64
    elif change=='inputs': prepared['reduction_preparation']['map_inputs_hash']='f'*64
    elif change=='preparation': prepared['reduction_preparation']['preparation_hash']='f'*64
    elif change=='preflight': value['preflight_hash']='f'*64
    elif change=='identity': value['actor_id']='other'
    elif change=='revision': value['revision']=4
    elif change=='request': command.request_id='different-request'
    with pytest.raises(subject.ReducePreflightDenied):subject._receipt_binding(value,prepared,row,p,command)

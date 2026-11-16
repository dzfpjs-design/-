"""Actual parent/child finalization kernel; no worker, database or HTTP claims."""
import copy
from types import SimpleNamespace as N
import pytest
from fastapi import HTTPException
from generic_agents import simulation_loop_v176 as module
from generic_agents.models import Job
from generic_agents.simulation_models_v176 import SimulationSampleV176


def fixture():
    parent=N(id='parent',workspace_id='scope',kind=module.KIND,status='running',data={'sample_id':'sample'})
    child=N(id='child',workspace_id='scope',kind=module.PROCESS,status='waiting_parent',data={'observation_id':'observation'})
    sample=N(id='sample',workspace_id='scope',status='received',observation_id='observation',data={'job_id':'parent','processing_job_id':'child'})
    db=N(get=lambda model,key:{(SimulationSampleV176,'sample'):sample,(Job,'child'):child}.get((model,key)))
    return db,parent,child,sample


def test_activates_saved_child_only_when_original_parent_is_finalizing():
    db,parent,child,sample=fixture();before=copy.deepcopy(sample.data)
    module.finalize_sample(db,parent)
    assert child.status=='queued' and sample.data==before and parent.status=='running'


@pytest.mark.parametrize('fault',['workspace','parent_key','wrong_kind','already_running','observation','sample_status'])
def test_conflicting_or_already_running_child_never_activates(fault):
    db,parent,child,sample=fixture()
    if fault=='workspace':child.workspace_id='another'
    elif fault=='parent_key':sample.data['job_id']='other'
    elif fault=='wrong_kind':child.kind='unrelated'
    elif fault=='already_running':child.status='running'
    elif fault=='observation':child.data['observation_id']='other'
    else:sample.status='awaiting_receipt'
    before=child.status
    with pytest.raises(HTTPException):module.finalize_sample(db,parent)
    assert child.status==before


def test_unknown_receipt_without_observation_cannot_enable_processing():
    db,parent,child,sample=fixture();sample.observation_id=None;sample.status='awaiting_receipt'
    module.finalize_sample(db,parent)
    assert child.status=='waiting_parent'

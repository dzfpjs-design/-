"""Managed image origin metadata must not silently downgrade into generic input."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import local_observations as local, instance_api, instance_domain
from generic_agents.contracts import digest
from generic_agents.models import Capability

_spec=importlib.util.spec_from_file_location('_local_image_definition_fixture',Path(__file__).with_name('test_entity_release_compat_pure.py'))
_fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixture)
forbid_runtime=_fixture.forbid_runtime


@pytest.fixture
def managed_image(monkeypatch):
    w=_fixture.World(monkeypatch)
    source_data={'specification':{'managed_instance':w.instance.id},'execution':{'adapter':'upload'},
        'data_policy':{'cloud_allowed':False,'external_allowed':False}}
    source=NS(id='local-source',workspace_id=w.p.workspace_id,kind='source',status='published',data=source_data,
        content_hash=digest(source_data),validated_hash=digest(source_data))
    w.db.rows[Capability][source.id]=source
    cap=NS(id='image-model',data={'execution':{'adapter':'local_specialist'}})
    image=NS(id='saved-image',sha256='a'*64)
    request={'source_id':source.id,'source_record_id':'owned-image-request','processing_version':'1.0.0',
        'entity_id':w.entity.id,'business_day':'2026-10-16','observed_at':None,'extractor_id':cap.id,
        'attributes':{},'evidence_ids':[image.id]}
    row=NS(id='local-observation',source_id=source.id,source_record_id=request['source_record_id'],
        processing_version=request['processing_version'],entity_id=w.entity.id,business_day=request['business_day'],
        observed_at=None,data={'candidate_only':True,'original_request':request,'input_hash':digest(request),
            'attributes':{},'evidence_ids':[image.id],'local_capability_hash':digest(cap.data),
            'local_source_hash':digest(source_data),'local_original_sha256':image.sha256})
    # Model and file byte checks are independent seams; the real published source,
    # instance activation and complete definition proof logic remain enabled.
    monkeypatch.setattr(local,'checked_cap',lambda db,p,key:cap)
    monkeypatch.setattr(local,'file_inputs',lambda db,p,selected,ids,attributes:image)
    monkeypatch.setattr(local,'published',w.db.published);monkeypatch.setattr(local,'scoped',w.db.scoped)
    monkeypatch.setattr(instance_api,'scoped',w.db.scoped);monkeypatch.setattr(instance_api,'published',w.db.published)
    monkeypatch.setattr(instance_domain,'scoped',w.db.scoped)
    return w,row,source,cap,image


@pytest.mark.parametrize('context',['absent',None,{}])
def test_managed_source_cannot_skip_frozen_destination_when_context_is_missing(managed_image,context):
    w,row,source,cap,image=managed_image
    if context!='absent':row.data['local_instance']=context
    w.instance.status='paused'
    original_hash=row.data['input_hash'];before=copy.deepcopy(row.data)
    with pytest.raises(HTTPException):local.verify(w.db,w.p,row,cap.id,execution=True)
    assert row.data==before and digest(row.data['original_request'])==original_hash


def test_unmanaged_legacy_image_retains_generic_path(managed_image):
    w,row,source,cap,image=managed_image
    source.data['specification']={};source.content_hash=source.validated_hash=digest(source.data)
    row.data['local_source_hash']=digest(source.data);w.instance.status='paused'
    assert local.verify(w.db,w.p,row,cap.id,execution=True) is image

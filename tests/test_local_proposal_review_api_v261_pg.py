"""Actual authenticated routes and PG; the source fixture's provider is an explicit HTTP double."""
import copy
from uuid import uuid4
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from tests.test_local_proposal_review_drafts_v261_pg import review_scene,open_body


def test_routes_preserve_private_work_and_bind_original_source_hashes(review_scene):
    s=review_scene;client=s.client;body=open_body(s)
    opened=client.post('/api/setup/understanding/'+s.source_id+'/local-review-drafts',json=body)
    assert opened.status_code==201,opened.text
    row=opened.json();base='/api/setup/local-review-drafts/'+row['id']
    response=client.get(base+'/source');assert response.status_code==200,response.text
    source=response.json();assert source['analysis_id']==s.source_id and source['raw_hash']==s.raw_hash
    assert source['raw_candidate']==s.raw['value']
    assert source['item_hashes']['/businesses/0']==digest(s.raw['value']['businesses'][0])
    assert source['citation_hashes']['/businesses/0/citations/0']==digest(s.raw['value']['businesses'][0]['citations'][0])
    assert source['source_blocks'] and all(set(b)=={'material_id','block_id','material_name','ordinal','text'} for b in source['source_blocks'])
    value=copy.deepcopy(row['value']);value['edits']=[{'op':'replace_citation','path':'/businesses/0/citations/0',
        'material_id':None,'block_id':None,'start_text':'','end_text':'','reason':'尚在核对原文的私人理由🌲'}]
    request={'version':row['version'],'request_id':str(uuid4()),'value':value}
    saved=client.put(base,json=request);assert saved.status_code==200,saved.text
    view=saved.json()['current_draft'];assert view['value']==value and saved.json()['current_matches']
    assert client.get(base).json()==view
    assert client.put(base,json=request).json()==saved.json()
    reopened=client.post('/api/setup/understanding/'+s.source_id+'/local-review-drafts',json=body)
    assert reopened.status_code==201 and reopened.json()==view
    assert client.get('/api/setup/understanding/'+s.source_id+'/local-review-drafts').json()==[view]
    with Session(engine()) as db:
        original=db.get(m.SetupAnalysis,s.source_id);assert original.data['raw_response']==s.raw


def test_broken_source_blocks_only_source_view_and_new_save_not_private_recovery(review_scene):
    s=review_scene;client=s.client
    response=client.post('/api/setup/understanding/'+s.source_id+'/local-review-drafts',json=open_body(s));assert response.status_code==201
    row=response.json();base='/api/setup/local-review-drafts/'+row['id']
    with Session(engine()) as db:
        original=db.get(m.SetupAnalysis,s.source_id);data=copy.deepcopy(original.data)
        data['raw_response']['hash']='0'*64;original.data=data;db.commit()
    assert client.get(base).json()==row
    blocked=client.get(base+'/source');assert blocked.status_code==409
    write=client.put(base,json={'version':row['version'],'request_id':str(uuid4()),'value':row['value']})
    assert write.status_code==409 and client.get(base).json()==row

"""Real pinned ASR plus private durable correction; test-only PostgreSQL."""
import io,json,wave
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.local_audio import call,validate
from generic_agents.models import AudioRun,Memory,Evidence
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.worker import process_once

def wav(channels=1,seconds=.5):
    b=io.BytesIO()
    with wave.open(b,'wb') as f:f.setnchannels(channels);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0'*(int(16000*seconds)*2*channels))
    return b.getvalue()

def upload(client,content=None,identity=None):
    installs=client.get('/api/specialists/installations').json()
    if not any(r['pack_id']=='sensevoice-small-v1' and r['status']=='ready' for r in installs):
        r=client.post('/api/specialists/installations',json={'pack_id':'sensevoice-small-v1'});assert r.status_code==201,r.text;assert process_once(r.json()['job_id'],deliver=False)
    return client.post('/api/audio',files={'file':('公开样例.mp3',content or Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes(),'audio/mpeg')},data={'configuration':json.dumps({'channel':'mono','language':'zh'}),'request_id':identity or str(uuid4())})

def test_real_local_audio_review_keeps_original_and_source_result(client):
    identity=str(uuid4());response=upload(client,identity=identity);assert response.status_code==201,response.text;row=response.json()
    assert upload(client,identity=identity).json()['id']==row['id']
    assert process_once(row['job_id'],deliver=False)
    path='/api/audio/'+row['id'];row=client.get(path).json();assert row['status']=='succeeded',row.get('error')
    assert '9点' in row['result']['transcription']['text'] and '5点' in row['result']['transcription']['text']
    assert row['result']['cloud_calls']==0 and not row['result']['transcription']['word_timestamps']
    assert client.get('/api/audio/requests/'+identity,params={'expected_hash':row['request_hash']}).json()['id']==row['id']
    body={'version':row['version'],'text':'核对：开放时间早上9点至下午5点。','confirmed':True}
    response=client.put(path+'/review',json=body);assert response.status_code==200,response.text;reviewed=response.json()
    assert reviewed['result']==row['result'] and client.put(path+'/review',json=body).status_code==409
    assert client.get(path+'/export').json()['record']['review']['text']==body['text']
    assert client.get(path+'/original').content==Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes()
    with Session(engine()) as db:
        m=db.get(Memory,reviewed['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated']
        ev=db.get(Evidence,row['evidence_id']);p=settings().storage_dir/ev.storage_key;raw=p.read_bytes();p.write_bytes(b'broken')
    assert client.get(path+'/export').status_code==409
    p.write_bytes(raw)
    result=row['result'];result['transcription']['word_timestamps']=True
    with pytest.raises(ValueError):validate(result,raw,{'channel':'mono','language':'zh'})

def test_silence_never_becomes_generated_speech():
    result=call(wav(),{'channel':'mono','language':'auto'})
    assert result['audio']['silence_detected'] and result['transcription']['text']=='' and not result['transcription']['inference_performed']
    assert result['transcription']['skip_reason']=='near_silence'

def test_decode_refuses_implicit_channel_long_audio_and_fake_header():
    with pytest.raises(ValueError,match='多个通道'):call(wav(channels=2),{'channel':'mono','language':'auto'})
    with pytest.raises(ValueError,match='60秒'):call(wav(seconds=61),{'channel':'mono','language':'auto'})

def test_cancel_and_original_request_identity_reject_replacement(client):
    identity=str(uuid4());row=upload(client,identity=identity).json();path='/api/audio/'+row['id']
    assert upload(client,content=wav(),identity=identity).status_code==409
    response=client.post(path+'/cancel',json={'version':row['version']});assert response.status_code==200
    assert process_once(row['job_id'],deliver=False)
    row=client.get(path).json();assert row['status']=='cancelled' and row['result'] is None
    assert client.get('/api/audio/requests/'+identity,params={'expected_hash':'wrong'}).status_code==409
    assert client.post('/api/audio',files={'file':('fake.mp3',b'hello','audio/mpeg')},data={'request_id':str(uuid4()),'configuration':'{}'}).status_code==415

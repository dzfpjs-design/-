"""Closed raw requests for authorized audio-track recording, not video consent."""
from typing import Literal
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator
from .contracts import Contract

UUID = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'


class Configuration(Contract):
    channel: Literal['mono', 'mix', '1', '2', '3', '4', '5', '6', '7', '8']
    threshold: StrictFloat = Field(gt=0, lt=1)


class Start(Contract):
    schema: Literal['audio-stream-start-v255']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    channel_version: StrictInt = Field(ge=1)
    instance_id: StrictStr = Field(pattern=UUID)
    release_id: StrictStr = Field(pattern=UUID)
    business_id: StrictStr = Field(min_length=1, max_length=200)
    entity_id: StrictStr = Field(pattern=UUID)
    model_capability_id: StrictStr = Field(pattern=UUID)
    track_index: StrictInt = Field(ge=0, le=31)
    configuration: Configuration
    segment_seconds: StrictInt = Field(ge=1, le=60)
    max_segments: StrictInt = Field(ge=1, le=100)
    max_runtime_seconds: StrictInt = Field(ge=1, le=3600)
    max_connections: StrictInt = Field(ge=1, le=4)
    confirm_audio_capture: StrictBool

    @field_validator('confirm_audio_capture')
    @classmethod
    def confirmed(cls, value):
        if value is not True: raise ValueError('请明确确认从所选通道接收音轨')
        return value


class Command(Contract):
    schema: Literal['audio-stream-command-v255']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)
    action: Literal['pause', 'stop', 'resume']



class SourceRegister(Contract):
    schema: Literal['audio-source-register-v255']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    channel_version: StrictInt = Field(ge=1)
    gateway_version: StrictInt = Field(ge=1)


class ModelRegister(Contract):
    installation_id: StrictStr = Field(pattern=UUID)
    version: StrictInt = Field(ge=1)


# Full paths: workspace_api mounts this router through its unprefixed aggregate.
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .db import session
from .security import authenticated, Principal
from .snapshot_authentication_v141 import snapshot_authenticated
from .signal_dual_read_session import read_session
from . import audio_streams as store

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
router = APIRouter()


def _lookup(request_id, raw_sha256):
    store.need(store._safe_json(request_id) and 12 <= len(request_id) <= 100
        and len(raw_sha256) == 64 and all(c in '0123456789abcdef' for c in raw_sha256), code=422)


async def _raw(request):
    content = await request.body()
    store.need(0 < len(content) <= store.MAX_RAW, '申请正文为空或超过上限', 413)
    return content


@router.get('/api/audio-streams/catalog')
def catalog(p: ReadP, db: ReadDB):
    return store.catalog(db, p)


@router.get('/api/media/channels/{channel_id}/audio-source/options')
def source_options(channel_id: str, p: ReadP, db: ReadDB):
    return store.source_options(db, p, channel_id)


@router.get('/api/media/channels/{channel_id}/audio-source/requests')
def source_recovery(channel_id: str, p: ReadP, db: ReadDB, request_id: str, raw_sha256: str):
    _lookup(request_id, raw_sha256)
    return store.recover_source(db, p, channel_id, request_id, raw_sha256)


@router.post('/api/media/channels/{channel_id}/audio-source')
async def source_register(channel_id: str, request: Request, p: P, db: DB):
    result = store.register_source(db, p, channel_id, await _raw(request)); db.commit()
    return result


@router.get('/api/audio-streams/models/registrations')
def model_recovery(p: ReadP, db: ReadDB, installation_id: str, expected_version: int = Query(ge=1)):
    value = store.model_registration(db, p, installation_id, expected_version)
    return {'schema': 'audio-model-registration-lookup-v255', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'installation_id': installation_id, 'expected_version': expected_version, 'found': value is not None,
        'receipt': value, 'reason': None if value else '尚未找到原版本的登记回执；查不到不证明原登记没有发生'}


@router.post('/api/audio-streams/models/register')
def model_register(body: ModelRegister, p: P, db: DB):
    result = store.register_model(db, p, body.model_dump(mode='json')); db.commit()
    return {'schema': 'audio-model-registration-result-v255', 'receipt': result}


@router.get('/api/workspace/instances/{instance_id}/audio-streams/options')
def stream_options(instance_id: str, p: ReadP, db: ReadDB, channel_id: str | None = None):
    return store.options(db, p, instance_id, channel_id=channel_id)


@router.get('/api/workspace/instances/{instance_id}/audio-streams/requests')
def start_recovery(instance_id: str, p: ReadP, db: ReadDB, request_id: str, raw_sha256: str, channel_id: str | None = None):
    _lookup(request_id, raw_sha256)
    return store.recovery(db, p, instance_id, request_id, raw_sha256, channel_id=channel_id)


@router.get('/api/workspace/instances/{instance_id}/audio-streams')
def streams(instance_id: str, p: ReadP, db: ReadDB, limit: int = Query(20, ge=1, le=50), before_id: str | None = None):
    return store.list_streams(db, p, instance_id, limit=limit, before_id=before_id)


@router.post('/api/workspace/instances/{instance_id}/audio-streams/channels/{channel_id}')
async def start(instance_id: str, channel_id: str, request: Request, p: P, db: DB):
    result = store.start_stream(db, p, instance_id, channel_id, await _raw(request)); db.commit()
    return result


@router.get('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}/requests')
def command_recovery(instance_id: str, stream_id: str, p: ReadP, db: ReadDB, request_id: str, raw_sha256: str):
    _lookup(request_id, raw_sha256)
    return store.recovery(db, p, instance_id, request_id, raw_sha256, stream_id=stream_id)


@router.post('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}/commands')
async def command(instance_id: str, stream_id: str, request: Request, p: P, db: DB):
    result = store.command(db, p, instance_id, stream_id, await _raw(request)); db.commit()
    return result


@router.get('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}/captures')
def captures(instance_id: str, stream_id: str, p: ReadP, db: ReadDB, limit: int = Query(20, ge=1, le=50), before_id: str | None = None):
    stream = store._read_stream(db, p, instance_id, stream_id)
    return store.capture_list(db, p, stream, limit=limit, before_id=before_id)


@router.get('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}/captures/{observation_id}/content')
def content(instance_id: str, stream_id: str, observation_id: str, request: Request, p: ReadP, db: ReadDB):
    from .sound_api import audio_response
    stream = store._read_stream(db, p, instance_id, stream_id)
    row, context = store._selected_capture(db, p, stream, observation_id)
    return audio_response(request, context['evidence'], store._read_evidence(db, p, context['evidence']), context['capture']['segment']['file'])


@router.get('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}/captures/{observation_id}')
def capture(instance_id: str, stream_id: str, observation_id: str, p: ReadP, db: ReadDB):
    stream = store._read_stream(db, p, instance_id, stream_id)
    return store.capture_view(db, p, stream, observation_id)


@router.get('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}/daily')
def daily(instance_id: str, stream_id: str, p: ReadP, db: ReadDB):
    from .audio_stream_delivery import summary
    return summary(db, p, store._read_stream(db, p, instance_id, stream_id))


@router.get('/api/workspace/instances/{instance_id}/audio-streams/{stream_id}')
def detail(instance_id: str, stream_id: str, p: ReadP, db: ReadDB):
    return store.stream_view(db, p, store._read_stream(db, p, instance_id, stream_id))

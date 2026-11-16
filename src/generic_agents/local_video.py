"""Durable local video jobs, immutable frame evidence and transactional receivers."""
import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from .config import settings
from .contracts import digest
from .evidence import evidence_bytes, save_evidence
from .local_specialists import ROOT, RECIPE_HASH, ready_detection_install as ready_install, weights_dir, node_lock, guard_record, validate_output
from .models import VideoRun, VideoFrame, VideoAlert, Event, Memory, now
from .services import scoped, audit, enqueue_event

PIPELINE = json.loads((ROOT / 'model-packs/video-rtdetr-bytetrack.json').read_text())
PIPELINE_HASH = digest({'manifest': PIPELINE, 'model_recipe': RECIPE_HASH, 'code': {
    name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    for name in ['local_video_decode.py', 'local_video_runner.py', 'local_video.py', 'video_api.py','video_domain.py','video_domain_api.py','instance_domain.py']}})


def child(script, request, temporary, guard, receive, timeout_seconds=300):
    env = {k: v for k, v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYTORCH_ENABLE_MPS_FALLBACK='0')
    python = settings().specialist_python.absolute()
    request_file = temporary / 'request.json'; request_file.write_text(json.dumps(request))
    with (temporary / (script + '.stderr')).open('w+') as err:
        proc = subprocess.Popen([str(python), str(Path(__file__).with_name(script)), '--request', str(request_file)],
                                stdout=subprocess.PIPE, stderr=err, cwd=temporary, env=env, start_new_session=True,
                                pass_fds=(request['lock_fd'],) if request.get('lock_fd') is not None else ())
        selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
        started = time.monotonic(); buffer = b''
        try:
            eof = False
            while not eof:
                guard()
                if time.monotonic() - started > timeout_seconds: raise ValueError('本地视频处理超时；原件和已保存帧保留，可重试')
                for key, _ in selector.select(.2):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data: eof = True; break
                    buffer += data
                    if len(buffer) > 512 * 1024: raise ValueError('视频服务输出超出单帧限额')
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        receive(json.loads(line), temporary)
            guard(); proc.wait(timeout=5)
            if proc.returncode or buffer:
                err.seek(0); tail = err.read()[-1000:]
                if 'exceeds 60 seconds' in tail: reason = '视频超过60秒，请截取需要核对的片段后重新上传'
                elif 'PTS' in tail: reason = '视频时间信息缺失或不连续，无法可靠建立轨迹'
                elif 'MPS is unavailable' in tail: reason = '本机MPS不可用；须明确选择可用后端，不自动降级'
                else: reason = '本地视频解码或模型处理失败；原件和已保存帧保留，未切换云端'
                raise ValueError(reason)
        finally:
            selector.close(); proc.stdout.close()
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)


def execute_video(content, backend, configuration, checkpoints, guard, receive, lock_fd=None):
    with tempfile.TemporaryDirectory(prefix='ga-local-video-') as directory:
        temp = Path(directory); (temp / 'video.mp4').write_bytes(content)
        request = {'input': str(temp / 'video.mp4'), 'output_dir': str(temp), 'decode_manifest': str(temp / 'decode.json'),
                   'weights': str(weights_dir().resolve()), 'backend': backend,
                   'configuration': configuration, 'checkpoints': checkpoints, 'parent_pid':os.getpid(), 'lock_fd':lock_fd}
        child('local_video_decode.py', request, temp, guard, lambda v, _: receive(v, temp))
        child('local_video_runner.py', request, temp, guard, receive)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_frame(value, raw, backend):
    if value.get('file') != f'frame-{value.get("sample_index", -1):04d}.jpg' or not 0 <= value.get('sample_index', -1) < 120:
        raise ValueError('视频帧序号或文件名无效')
    if not isinstance(value.get('pts'), int) or isinstance(value['pts'], bool): raise ValueError('视频PTS无效')
    try: basis = Fraction(value['time_base'])
    except (KeyError, ValueError, ZeroDivisionError): raise ValueError('视频时间基准无效')
    if basis <= 0 or not finite(value.get('seconds')) or not 0 <= value['seconds'] <= 60.1:
        raise ValueError('视频帧时间无效')
    detection = {'schema':'local-detection-v1', 'backend':backend, 'cloud_calls':0, 'threshold':.1,
                 'image_sha256':value.get('frame_sha256'), 'width':value.get('width'), 'height':value.get('height'),
                 'detections':value.get('detections')}
    validate_output(detection, backend, .1, raw)
    tracks = value.get('tracks')
    if not isinstance(tracks, list) or len(tracks) > 300: raise ValueError('视频轨迹数量无效')
    for item in tracks:
        validate_output({**detection, 'detections':[item]}, backend, .1, raw)
        if not re.fullmatch(r'[1-9]\d*:[1-9]\d*', item.get('track_ref','')): raise ValueError('视频轨迹身份无效')
    if len({x['track_ref'] for x in tracks}) != len(tracks): raise ValueError('重复的会话轨迹')
    if not isinstance(value.get('events'), list) or len(value['events']) > 300: raise ValueError('视频线索数量无效')


def receive_video_events(db, p, run_id):
    """Only the selected video's outbox is delivered, never unrelated legacy monitoring."""
    events = db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,
        Event.kind.in_(['video.data','video.presence']), Event.status=='pending_delivery',
        Event.data['run_id'].as_string()==run_id).order_by(Event.created_at).with_for_update()).all()
    for event in events:
        run = scoped(db, VideoRun, run_id, p)
        frame = scoped(db, VideoFrame, event.data['frame_id'], p)
        if frame.run_id != run.id: raise ValueError('视频送达证据不属于此会话')
        if event.kind == 'video.data':
            from .video_domain import observation
            domain = observation(db,p,run,frame)
            db.add(Memory(workspace_id=p.workspace_id, status='candidate', text=f'视频“{run.data["name"]}”第{frame.data["seconds"]:.1f}秒的轨迹与候选状态',
                data={**event.data, **domain, 'event_id':event.id, 'type':'video_observation', 'not_authoritative':True,
                      'tracks':frame.data['tracks'], 'evidence_ids':[frame.evidence_id], 'deployment':'local','source_context':frame.data.get('source_context')}))
        else:
            candidate = event.data['candidate']
            alert = db.scalar(select(VideoAlert).where(VideoAlert.run_id==run_id, VideoAlert.event_key==candidate['event_key']))
            if not alert:
                db.add(VideoAlert(workspace_id=p.workspace_id, run_id=run_id, event_key=candidate['event_key'],
                    data={**candidate, 'event_id':event.id, 'frame_id':frame.id, 'evidence_id':frame.evidence_id,
                          'receiver':'business', 'not_authoritative':True, 'review':None}))
                db.flush()
                alert=db.scalar(select(VideoAlert).where(VideoAlert.run_id==run_id,VideoAlert.event_key==candidate['event_key']))
            from .video_domain import business_draft
            alert.data={**alert.data,**business_draft(db,p,run,frame,alert)}
        event.status='received'; event.received_at=now(); event.attempts += 1
    return len(events)


def process_video(db, p, job):
    row = scoped(db, VideoRun, job.data['run_id'], p, True)
    if row.status in {'succeeded','cancelled'}: return
    generation = job.data['generation']; run_id = row.id
    try:
        if row.data['pipeline_hash'] != PIPELINE_HASH: raise ValueError('视频处理版本已变化，请保留原记录并重新处理')
        installation = ready_install(db,p,row.installation_id)
        if row.data['installation_generation'] != installation.data['generation'] or row.data['backend'] != installation.data['backend']:
            raise ValueError('本地检测模型版本或后端已变化，原记录保留')
        original, content = evidence_bytes(db,p,row.evidence_id)
        if original.sha256 != row.data['input_sha256'] or original.data['modality']!='video': raise ValueError('视频原件不一致')
        frames = db.scalars(select(VideoFrame).where(VideoFrame.run_id==run_id).order_by(VideoFrame.sample_index)).all()
        checkpoints = [{'sample_index':f.sample_index, **{k:f.data[k] for k in ['frame_sha256','pts','time_base','detections']}} for f in frames]
        row.status='running'; row.version+=1; row.data={**row.data,'stage':'decoding','recovery_count':row.data.get('recovery_count',0)+int(bool(frames))}
        db.commit()
        def guard():
            current=guard_record(db,p,VideoRun,run_id,generation)
            if current.data.get('source_context'):
                from .media import context_guard
                _,_,session=context_guard(db,p,current.data['source_context'])
                if session.video_run_id!=current.id:raise ValueError('来源视频处理已被新记录替代')
            return current
        completion = None
        def receive(value, temporary):
            nonlocal completion
            current = guard()
            if value.get('kind')=='decoded':
                current.data={**current.data,'stage':'tracking','expected_samples':value['samples']};current.version+=1;db.commit();return
            if value.get('kind')=='complete':
                if value.get('schema')!='local-video-v1' or value.get('cloud_calls')!=0 or value.get('backend')!=current.data['backend']:
                    raise ValueError('视频处理完成回执不匹配')
                completion=value;return
            if value.get('kind')!='frame': raise ValueError('视频服务消息无效')
            index=value.get('sample_index')
            if not isinstance(index,int) or not 0<=index<120:raise ValueError('视频帧序号无效')
            if value.get('file')!=f'frame-{index:04d}.jpg':raise ValueError('视频帧路径无效')
            raw=(temporary/value['file']).read_bytes();validate_frame(value,raw,current.data['backend'])
            saved=db.scalar(select(VideoFrame).where(VideoFrame.run_id==run_id,VideoFrame.sample_index==index))
            stable={k:value[k] for k in ['frame_sha256','pts','time_base','seconds','width','height','segment','detections','tracks','events','gap_before']}
            if saved:
                if digest(stable)!=saved.data['receipt_hash']:raise ValueError('恢复后的轨迹与保存检查点不一致，未覆盖原结果')
            else:
                previous=db.scalar(select(VideoFrame).where(VideoFrame.run_id==run_id).order_by(VideoFrame.sample_index.desc()).limit(1))
                if index!=(previous.sample_index+1 if previous else 0):raise ValueError('视频帧保存顺序缺失')
                if previous and value['seconds']<=previous.data['seconds']:raise ValueError('视频帧时间没有递增')
                ev=save_evidence(db,p,raw,'image/jpeg')
                if (ev.data['width'],ev.data['height'])!=(value['width'],value['height']):raise ValueError('视频帧尺寸不一致')
                utc=None
                if current.data['configuration'].get('recorded_at'):
                    utc=(datetime.fromisoformat(current.data['configuration']['recorded_at'])+timedelta(seconds=value['seconds'])).isoformat()
                saved=VideoFrame(workspace_id=p.workspace_id,run_id=run_id,sample_index=index,evidence_id=ev.id,
                    data={**stable,'receipt_hash':digest(stable),'utc':utc,'utc_basis':'user_supplied_unverified' if utc else 'unknown',
                          'inference_ms':value['inference_ms'],'pipeline_hash':PIPELINE_HASH,'not_authoritative':True,
                          'source_context':current.data.get('source_context')})
                db.add(saved);db.flush()
                enqueue_event(db,p,'video.data',f'video-data:{run_id}:{index}',{'run_id':run_id,'frame_id':saved.id,'actor_id':p.user_id})
                rule=current.data['configuration'].get('presence_rule')
                for candidate in value['events']:
                    tracked=next((t for t in value['tracks'] if t['track_ref']==candidate.get('track_ref')),None)
                    if not rule or not tracked or tracked['label_id']!=rule['label_id'] or candidate.get('event_key')!='presence:'+tracked['track_ref']:
                        raise ValueError('视频线索不符合用户确认的规则')
                    start,end=candidate.get('from_seconds'),candidate.get('to_seconds')
                    if not finite(start) or not finite(end) or not 0<=start<=end==value['seconds'] or end-start+1e-6<rule['seconds']:
                        raise ValueError('视频线索时间无效')
                    enqueue_event(db,p,'video.presence',f'video-presence:{run_id}:{candidate["event_key"]}',
                        {'run_id':run_id,'frame_id':saved.id,'candidate':candidate,'actor_id':p.user_id})
            current.data={**current.data,'stage':'tracking','saved_samples':max(index+1,current.data.get('saved_samples',0))};current.version+=1
            db.commit()  # Results/outbox first; receiver acknowledgement in its next transaction.
            receive_video_events(db,p,run_id);db.commit()
        with node_lock() as lock_fd:
            execute_video(content,row.data['backend'],row.data['configuration'],checkpoints,guard,receive,lock_fd=lock_fd)
        current=guard();ready_install(db,p,current.installation_id);evidence_bytes(db,p,current.evidence_id)
        total=db.scalar(select(VideoFrame).where(VideoFrame.run_id==run_id).order_by(VideoFrame.sample_index.desc()).limit(1))
        if completion is None or not total or completion['sample_count']!=total.sample_index+1:raise ValueError('视频完成回执与已保存帧不符')
        current.status='succeeded';current.version+=1
        current.data={**current.data,'stage':'complete','result':completion,'result_hash':digest(completion),'error':None}
        if current.data.get('source_context') and not current.data['source_context']['partial']:
            from .media import context_guard,CAPTURE_HASH
            channel,gateway,session=context_guard(db,p,current.data['source_context'])
            if abs(completion['duration_seconds']-current.data['source_context']['captured_duration_seconds'])>.5:
                raise ValueError('保存视频的可解码时长与读取回执不一致；原件和已保存画面保留，请重新验证')
            channel.status='validated';channel.version+=1
            channel.data={**channel.data,'validation':{'session_id':session.id,'video_run_id':current.id,
                'pipeline_hash':PIPELINE_HASH,'capture_hash':CAPTURE_HASH,'channel_generation':channel.data['generation'],
                'gateway_generation':gateway.data['generation'],'checked_at':now().isoformat()}}
        audit(db,p,'video.completed',run_id,{'samples':completion['sample_count'],'cloud_calls':0});db.commit()
    except (ValueError,HTTPException) as error:
        db.rollback();current=scoped(db,VideoRun,run_id,p,True)
        if current.status not in {'cancelled','succeeded'}:
            current.status='failed';current.version+=1;current.data={**current.data,'error':str(error.detail if isinstance(error,HTTPException) else error)};db.commit()
        raise

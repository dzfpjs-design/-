"""Offline MP4 -> pinned RT-DETR -> actual ByteTrack, one model load per video.

JSON-lines IPC; replay saved detections to reconstruct the tracker after recovery.
Only parent-owned files are read. No camera/network URL is accepted here.
"""
import argparse
import hashlib
import json
import os
import time
import threading
from importlib.metadata import version
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    def watch_parent():
        while os.getppid() == request['parent_pid']: time.sleep(.5)
        os._exit(70)
    threading.Thread(target=watch_parent, daemon=True).start()
    root = Path(__file__).resolve().parents[2]
    recipe = json.loads((root / 'model-packs/rtdetr-v2-r18.json').read_text())
    pipeline = json.loads((root / 'model-packs/video-rtdetr-bytetrack.json').read_text())
    env = {**recipe['environment'], **pipeline['environment']}
    versions = {k: version(k) for k in env}
    if versions != env:
        raise ValueError('isolated video runtime differs from pinned dependencies')
    import numpy as np
    import supervision as sv
    import torch
    from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection
    torch.set_num_threads(4)
    backend = request['backend']
    if backend == 'mps' and not torch.backends.mps.is_available():
        raise ValueError('MPS is unavailable')
    weights = request['weights']
    started = time.monotonic()
    processor = RTDetrImageProcessor.from_pretrained(weights, local_files_only=True)
    model = RTDetrV2ForObjectDetection.from_pretrained(weights, local_files_only=True,
        use_safetensors=True, trust_remote_code=False, disable_custom_kernels=True).eval().to(backend)
    loaded_ms = round((time.monotonic() - started) * 1000)
    cfg = request['configuration']; hz = cfg['sample_hz']
    def tracker():
        # Buffer represents two seconds at the *sampled* frame rate.
        return sv.ByteTrack(track_activation_threshold=cfg['threshold'], frame_rate=hz,
                            lost_track_buffer=60, minimum_consecutive_frames=2)
    track = tracker()
    cache = {x['sample_index']: x for x in request['checkpoints']}
    tracks = {}; streaks = {}; event_keys = set(); gaps = []
    manifest = json.loads(Path(request['decode_manifest']).read_text())
    width, height = manifest['width'], manifest['height']
    for index, meta in enumerate(manifest['frames']):
        relative, segment = meta['seconds'], meta['segment']
        if meta['gap_before']:
            track = tracker(); streaks = {}
        file = Path(request['output_dir']) / meta['file']
        from PIL import Image
        with Image.open(file) as original: image = original.convert('RGB')
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        if sha != meta['frame_sha256']: raise ValueError('decoded frame changed')
        old = cache.get(index)
        replayed = old is not None
        inference_ms = 0
        if old:
            if old['frame_sha256'] != sha or old['pts'] != meta['pts'] or old['time_base'] != meta['time_base']:
                raise ValueError('checkpoint does not match decoded original')
            rows = old['detections']
            detections = sv.Detections(xyxy=np.array([x['bbox'] for x in rows], dtype=np.float32).reshape(-1, 4),
                confidence=np.array([x['score'] for x in rows], dtype=np.float32),
                class_id=np.array([x['label_id'] for x in rows], dtype=int))
        else:
            started = time.monotonic()
            inputs = processor(images=image, return_tensors='pt').to(backend)
            with torch.inference_mode():
                output = model(**inputs)
            if backend == 'mps': torch.mps.synchronize()
            result = processor.post_process_object_detection(output,
                target_sizes=torch.tensor([(height, width)], device=backend), threshold=.1)[0]
            boxes = result['boxes'].detach().cpu().numpy()
            boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
            boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
            scores = result['scores'].detach().cpu().numpy()
            classes = result['labels'].detach().cpu().numpy()
            valid = (boxes[:, 0] < boxes[:, 2]) & (boxes[:, 1] < boxes[:, 3])
            detections = sv.Detections(xyxy=boxes[valid], confidence=scores[valid], class_id=classes[valid]).with_nms(threshold=.6)
            rows = [{'bbox': [float(v) for v in box], 'score': float(score), 'label_id': int(cls),
                     'label': model.config.id2label[int(cls)]}
                    for box, score, cls in zip(detections.xyxy, detections.confidence, detections.class_id)]
            inference_ms = round((time.monotonic() - started) * 1000)
        tracked = track.update_with_detections(detections)
        active = []
        for box, score, cls, identity in zip(tracked.xyxy, tracked.confidence, tracked.class_id, tracked.tracker_id):
            ref = f'{segment}:{int(identity)}'
            item = {'track_ref': ref, 'label_id': int(cls), 'label': model.config.id2label[int(cls)],
                    'score': float(score), 'bbox': [float(v) for v in box]}
            active.append(item)
            tracks[ref] = {**item, 'last_seconds': relative,
                'first_seconds': tracks.get(ref, {}).get('first_seconds', relative)}
        events = []
        rule = cfg.get('presence_rule')
        eligible = set()
        for item in active:
            if not rule or item['label_id'] != rule['label_id'] or item['score'] < cfg['threshold']:
                continue
            ref = item['track_ref']; eligible.add(ref)
            streaks.setdefault(ref, relative)
            event_key = 'presence:' + ref
            if relative - streaks[ref] + 1e-6 >= rule['seconds'] and event_key not in event_keys:
                event_keys.add(event_key)
                events.append({'event_key': event_key, 'track_ref': ref, 'label': item['label'],
                    'from_seconds': streaks[ref], 'to_seconds': relative, 'kind': 'presence_candidate'})
        streaks = {k: v for k, v in streaks.items() if k in eligible}
        emit({'kind': 'frame', 'sample_index': index, 'file': file.name, 'frame_sha256': sha,
            'pts': meta['pts'], 'time_base': meta['time_base'], 'seconds': round(relative, 6),
            'width': width, 'height': height, 'segment': segment, 'detections': rows,
            'tracks': active, 'events': events, 'gap_before': meta['gap_before'],
            'inference_ms': inference_ms, 'replayed': replayed})
    count = len(manifest['frames'])
    if len(cache) > count:
        raise ValueError('checkpoint extends beyond the decoded original')
    emit({'kind': 'complete', 'schema': 'local-video-v1', 'cloud_calls': 0,
        'backend': backend, 'sample_count': count, 'decoded_frames': manifest['decoded_frames'],
        'duration_seconds': manifest['duration_seconds'], 'width': width, 'height': height,
        'tracks': list(tracks.values()), 'gap_intervals': manifest['gap_intervals'], 'model_load_ms': loaded_ms,
        'dependency_versions': versions, 'tracking_identity': 'session_and_segment_only',
        'audio_processed': False, 'sample_hz': hz})


if __name__ == '__main__':
    main()

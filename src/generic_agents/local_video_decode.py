"""PyAV in its own child: do not load OpenCV's second FFmpeg into this process."""
import argparse
import hashlib
import json
import os
import threading
import time
from importlib.metadata import version
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    def watch_parent():
        while os.getppid() == request['parent_pid']: time.sleep(.5)
        os._exit(70)
    threading.Thread(target=watch_parent, daemon=True).start()
    if version('av') != '16.1.0':
        raise ValueError('video decoder dependency changed')
    import av
    hz = request['configuration']['sample_hz']
    origin = previous = None; next_sample = 0.; decoded = 0; frames = []; gaps = []
    width = height = None; segment = 1; pending_gap = False
    with av.open(request['input'], format='mov', options={'protocol_whitelist': 'file',
                 'enable_drefs': '0', 'use_absolute_path': '0'}) as container:
        if not container.streams.video:
            raise ValueError('video stream absent')
        stream = container.streams.video[0]; stream.codec_context.thread_count = 2
        if not 0 < stream.width * stream.height <= 16000000:
            raise ValueError('video dimensions exceed limits')
        if stream.duration is not None and float(stream.duration * stream.time_base) > 60.1:
            raise ValueError('video exceeds 60 seconds')
        for frame in container.decode(stream):
            decoded += 1
            if decoded > 3600: raise ValueError('decoded frame budget exceeded')
            if frame.pts is None or frame.time_base is None:
                raise ValueError('video PTS absent; no fabricated timestamp')
            pts = float(frame.pts * frame.time_base)
            if previous is not None and pts <= previous:
                raise ValueError('video PTS is not strictly increasing')
            if origin is None: origin = pts; width = frame.width; height = frame.height
            relative = pts - origin
            if relative > 60.1 or (frame.width, frame.height) != (width, height):
                raise ValueError('duration or dimensions changed outside validated limits')
            gap = previous is not None and pts - previous > max(1., 1.5 / hz)
            if gap:
                segment += 1; pending_gap = True
                gaps.append({'from_seconds': round(previous - origin, 6), 'to_seconds': round(relative, 6)})
            previous = pts
            if relative + 1e-6 < next_sample: continue
            next_sample = relative + 1 / hz
            if len(frames) >= 120: raise ValueError('sample budget exceeded')
            file = Path(request['output_dir']) / f'frame-{len(frames):04d}.jpg'
            image = frame.to_image().convert('RGB'); image.save(file, 'JPEG', quality=90, subsampling=0)
            frames.append({'file': file.name, 'frame_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                'pts': frame.pts, 'time_base': str(frame.time_base), 'seconds': round(relative, 6),
                'width': width, 'height': height, 'segment': segment, 'gap_before': pending_gap})
            pending_gap = False
            if sum(x.stat().st_size for x in Path(request['output_dir']).glob('frame-*.jpg')) > 300 * 1024 * 1024:
                raise ValueError('decoded evidence disk budget exceeded')
    if not frames: raise ValueError('video has no decodable frames')
    manifest = {'frames': frames, 'decoded_frames': decoded, 'duration_seconds': round(previous - origin, 6),
                'width': width, 'height': height, 'gap_intervals': gaps, 'decoder_version': version('av')}
    Path(request['decode_manifest']).write_text(json.dumps(manifest))
    print(json.dumps({'kind': 'decoded', 'samples': len(frames)}), flush=True)


if __name__ == '__main__': main()

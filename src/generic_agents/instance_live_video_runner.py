"""Isolated, bounded RTSP decoder; receives URL on stdin, emits framed JPEGs."""
import io
import json
import os
import struct
import sys
import threading
import time


def main():
    request = json.loads(sys.stdin.buffer.readline(8192))
    def watch_parent():
        while os.getppid() == request['parent_pid']: time.sleep(.5)
        os._exit(70)
    threading.Thread(target=watch_parent, daemon=True).start()
    from importlib.metadata import version
    if version('av') != '16.1.0': raise ValueError('unexpected decoder')
    import av
    started = time.monotonic()
    previous = 0.
    with av.open(request['url'], format='rtsp', timeout=(5., 5.), options={
        'rtsp_transport': 'tcp', 'allowed_media_types': 'video',
        'protocol_whitelist': 'rtsp,tcp', 'rw_timeout': '5000000'}) as incoming:
        if not incoming.streams.video or incoming.streams.video[0].codec_context.name != 'h264':
            raise ValueError('unsupported codec')
        for frame in incoming.decode(incoming.streams.video[0]):
            clock = time.monotonic()
            if clock - started > 75: break
            if clock - previous < .5: continue
            if frame.width <= 0 or frame.height <= 0 or frame.width * frame.height > 16000000:
                raise ValueError('invalid dimensions')
            image = frame.to_image(); image.thumbnail((1280, 720))
            buffer = io.BytesIO(); image.save(buffer, format='JPEG', quality=72, optimize=False)
            data = buffer.getvalue()
            if not 0 < len(data) <= 1024 * 1024: raise ValueError('frame size')
            sys.stdout.buffer.write(struct.pack('>I', len(data)) + data)
            sys.stdout.buffer.flush()
            previous = clock


if __name__ == '__main__': main()

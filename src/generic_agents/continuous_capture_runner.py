"""One RTSP connection, independently decodable MP4 segments. No AI/OpenCV/cloud."""
import argparse
import json
import os
import threading
import time
from pathlib import Path
from importlib.metadata import version


def emit(value): print(json.dumps(value,ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
    request=json.loads(Path(args.request).read_text())
    def parent_watch():
        while os.getppid()==request['parent_pid']:time.sleep(.5)
        os._exit(70)
    threading.Thread(target=parent_watch,daemon=True).start()
    if version('av')!='16.1.0':raise ValueError('runtime mismatch')
    import av
    seconds=request['seconds'];limit=request['segments'];started=time.monotonic()
    index=0;output=None;packets=0;first=None;last=None;basis=None;shift=None;size=0;derived=0;received=None;previous_dts=None
    def close(partial=False,error=None):
        nonlocal output
        if output is None:return
        output.close();output=None
        if not packets:return
        emit({'kind':'segment.complete','schema':'rtsp-capture-v1','file':f'segment-{index:04d}.mp4','index':index,
            'packets':packets,'requested_seconds':seconds,'duration_seconds':round(float((last-first)*basis),6),
            'partial':partial,'error':error,'first_source_pts':first,'last_source_pts':last,'source_time_base':str(basis),
            'received_start_unix':received,'received_end_unix':time.time(),'timestamp_shift_source_ticks':shift,
            'source_clock_utc':'unknown','codec':'h264','audio_captured':False,'decoder_version':version('av'),
            'probe_buffer_replayed':False,'derived_dts_packets':derived,'boundary_frame_overlap':index>0,'cloud_calls':0})
    try:
        with av.open(request['url'],format='rtsp',timeout=(5.,5.),options={'rtsp_transport':'tcp',
            'allowed_media_types':'video','protocol_whitelist':'rtsp,tcp','fflags':'+nobuffer','rw_timeout':'5000000'}) as incoming:
            if not incoming.streams.video:raise ValueError('video absent')
            source=incoming.streams.video[0]
            if source.codec_context.name!='h264' or not 0<source.width*source.height<=16000000:raise ValueError('codec/dimensions unsupported')
            if source.codec_context.has_b_frames:raise ValueError('reordered H.264 timeline unsupported')
            def write(packet):
                nonlocal packets,size,last,previous_dts
                # Boundary keyframe is intentionally present in both adjacent originals.
                copy=av.Packet(bytes(packet));copy.pts=packet.pts-shift;copy.dts=packet.dts-shift
                copy.is_keyframe=packet.is_keyframe
                copy.time_base=packet.time_base;copy.duration=packet.duration;copy.stream=dest
                if previous_dts is not None and copy.dts<previous_dts:raise ValueError('DTS regression')
                size+=copy.size
                if size>18*1024*1024 or packets>=100000:raise ValueError('segment budget exceeded')
                output.mux(copy);previous_dts=copy.dts;last=packet.pts;packets+=1
            for packet in incoming.demux(source):
                if time.monotonic()-started>request['runtime_seconds']:raise ValueError('runtime deadline')
                if not packet.size:continue
                if packet.pts is None:
                    if first is None:continue
                    raise ValueError('PTS missing')
                if packet.dts is None:
                    if source.codec_context.has_b_frames:raise ValueError('DTS missing for B-frame codec')
                    packet.dts=packet.pts;derived+=1
                if first is not None:
                    if packet.time_base!=basis or packet.pts<last:raise ValueError('source timeline changed')
                    elapsed=float((packet.pts-first)*basis)
                    if elapsed>seconds+5:raise ValueError('keyframe rotation deadline')
                    if elapsed>=seconds and packet.is_keyframe:
                        write(packet);close()
                        index+=1;first=None
                        if index>=limit:emit({'kind':'stream.complete','segments':index});return
                if first is None:
                    if not packet.is_keyframe:continue
                    first=packet.pts;last=first;basis=packet.time_base;shift=min(packet.pts,packet.dts)
                    received=time.time();packets=0;size=0;derived=0;previous_dts=None
                    path=Path(request['output_dir'])/f'segment-{index:04d}.mp4'
                    output=av.open(str(path),'w',format='mp4',options={'movflags':'+faststart'})
                    dest=output.add_stream_from_template(source)
                    emit({'kind':'segment.open','index':index,'first_source_pts':first,'source_time_base':str(basis)})
                write(packet)
                if packets%20==0:emit({'kind':'segment.progress','index':index,'seconds':round(float((last-first)*basis),3)})
            raise ValueError('stream ended')
    except Exception as error:
        try:close(True,'视频流中断、时间戳异常或读取限额；已保存片段保留')
        except Exception:pass
        # Only our finite codes leave this process. AV exception messages may
        # contain the authenticated URL and must never become user diagnostics.
        codes={'video absent':'video_absent','codec/dimensions unsupported':'codec_unsupported',
            'reordered H.264 timeline unsupported':'reordered_codec','runtime deadline':'runtime_limit',
            'PTS missing':'pts_missing','DTS missing for B-frame codec':'dts_missing','DTS regression':'dts_regression',
            'source timeline changed':'timeline_changed','keyframe rotation deadline':'keyframe_timeout',
            'segment budget exceeded':'segment_limit','stream ended':'stream_ended'}
        names={'TimeoutError':'read_timeout','EOFError':'stream_ended','ConnectionRefusedError':'connection_refused',
            'InvalidDataError':'invalid_stream','ExitError':'read_timeout'}
        code=codes.get(str(error),'connection_or_codec_error') if isinstance(error,ValueError) else names.get(type(error).__name__,'connection_or_codec_error')
        emit({'kind':'stream.failed','error_code':code})


if __name__=='__main__':main()

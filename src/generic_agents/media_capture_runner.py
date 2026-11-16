"""Isolated RTSP/TCP -> bounded original H264 MP4. Never loads model/OpenCV."""
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
    if version('av')!='16.1.0':raise ValueError('capture runtime version differs')
    import av
    output=Path(request['output_dir'])/'stream.mp4';requested=request['seconds']
    first=None;maximum=0.;packets=0;size=0;started=time.monotonic();error=None;error_code=None;derived_dts=0;leading_discarded=0
    try:
        with av.open(request['url'],format='rtsp',timeout=(5.,5.),options={'rtsp_transport':'tcp',
                     'allowed_media_types':'video','protocol_whitelist':'rtsp,tcp','fflags':'+nobuffer','rw_timeout':'5000000'}) as incoming:
            if not incoming.streams.video:raise ValueError('no video')
            source=incoming.streams.video[0]
            if source.codec_context.name!='h264':
                emit({'kind':'capture.failed','reason':'当前读取验证仅支持H.264视频；其他编码需适配器验证'});return
            if not 0<source.width*source.height<=16000000:raise ValueError('dimensions outside limits')
            with av.open(str(output),'w',format='mp4',options={'movflags':'+faststart'}) as container:
                dest=container.add_stream_from_template(source)
                for packet in incoming.demux(source):
                    if time.monotonic()-started>requested+15:raise TimeoutError('capture deadline')
                    if not packet.size:continue
                    if packet.pts is None:
                        if first is None:leading_discarded+=1;continue
                        raise ValueError('source PTS missing after recording began')
                    # RTP supplies presentation timestamps. FFmpeg may infer DTS only
                    # for its probe buffer; later packets legitimately have no DTS.
                    # PTS can stand in for DTS only when the codec has no B frames.
                    if packet.dts is None:
                        if source.codec_context.has_b_frames:raise ValueError('B-frame source without DTS')
                        packet.dts=packet.pts;derived_dts+=1
                    if first is None:
                        if not packet.is_keyframe:continue
                        first=packet.pts;basis=packet.time_base;shift=min(packet.pts,packet.dts);received_start=time.time()
                        emit({'kind':'capture.progress','seconds':0.,'packets':0})
                    elapsed=float((packet.pts-first)*packet.time_base)
                    maximum=max(maximum,elapsed);size+=packet.size
                    if size>18*1024*1024:raise OverflowError('size budget')
                    if packet.time_base!=basis:raise ValueError('source time base changed')
                    packet.pts-=shift;packet.dts-=shift
                    packet.stream=dest;container.mux(packet);packets+=1
                    if packets%10==0:emit({'kind':'capture.progress','seconds':round(maximum,3),'packets':packets})
                    if maximum>=requested:break
    except OverflowError:
        emit({'kind':'capture.failed','reason':'读取内容超过大小限额，请缩短验证时长或降低来源码率'});return
    except Exception as failure:
        known={'source PTS missing after recording began':'source_pts_missing','B-frame source without DTS':'source_dts_missing',
               'source time base changed':'source_time_base_changed','capture deadline':'capture_deadline'}
        error_code=known.get(str(failure),'rtsp_'+type(failure).__name__.lower())
        error='视频中断或读取超时；已收到的内容与时间缺口保留'
    if not packets or not output.is_file() or output.stat().st_size>20*1024*1024:
        emit({'kind':'capture.failed','reason':'未读取到可保存的视频，请检查通道在线状态、编码和读取凭据','error_code':error_code});return
    partial=maximum<requested-.1 or error is not None
    emit({'kind':'capture.complete','schema':'rtsp-capture-v1','file':'stream.mp4','packets':packets,
          'duration_seconds':round(maximum,6),'requested_seconds':requested,'partial':partial,'error':error,'error_code':error_code,
          'first_source_pts':first,'source_time_base':str(basis),'received_start_unix':received_start,
          'timestamp_shift_source_ticks':shift,
          'received_end_unix':time.time(),'source_clock_utc':'unknown','codec':'h264','audio_captured':False,
          'decoder_version':version('av'),'probe_buffer_replayed':False,'leading_packets_without_pts_discarded':leading_discarded,
          'derived_dts_packets':derived_dts,'dts_policy':'PTS only when codec has no B frames','cloud_calls':0})


if __name__=='__main__':main()

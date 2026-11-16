"""Pinned MediaPipe YAMNet CPU inference; complete audio, no VAD or ASR."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import socket
import struct
import sys
import time
import zipfile
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_METADATA = {'author': 'MediaPipe', 'name': 'yamnet/classification', 'version': '1',
                  'license': 'Apache License. Version 2.0 http://www.apache.org/licenses/LICENSE-2.0.'}
CHANNELS = {'mono', 'mix', *map(str, range(1, 9))}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_file(path, maximum, expected_sha=None, expected_size=None):
    import os
    import stat
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValueError('本地声音文件不是允许大小的普通文件')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError('读取时声音文件发生变化')
    if expected_size is not None and len(raw) != expected_size:
        raise ValueError('本地声音模型文件长度不符')
    if expected_sha is not None and hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('本地声音文件摘要不符')
    return raw


def configuration(value):
    if not isinstance(value, dict) or set(value) != {'channel', 'threshold'}:
        raise ValueError('请选择明确声道和候选分数门槛')
    threshold = value['threshold']
    if (value['channel'] not in CHANNELS or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
            or not 0 < threshold < 1):
        raise ValueError('声道或候选分数门槛无效')
    return value


def decoder(recipe):
    path = Path(__file__).with_name('local_audio_runner.py')
    read_file(path, 256 * 1024, recipe['decoder_sha256'])
    spec = importlib.util.spec_from_file_location('_bounded_sound_decoder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.decode


def decode_audio(path, channel, recipe):
    if channel not in CHANNELS:
        raise ValueError('请明确选择原音声道或合并声道')
    if version('av') != recipe['environment']['av']:
        raise ValueError('声音解码环境与固定配方不一致')
    # The reused decoder imports only PyAV/NumPy. recognize()/SenseVoice is never called.
    wave, metadata = decoder(recipe)(path, channel)
    return wave, metadata


def resample(wave, source_rate):
    import av
    import numpy as np
    if not np.isfinite(wave).all() or np.max(np.abs(wave)) > 1:
        raise ValueError('声音幅度超出[-1,1]，保留原音，不自动归一化或截幅')
    if source_rate == 16000:
        return np.ascontiguousarray(wave, dtype=np.float32), 'original_16000_no_resample'
    frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(wave[None, :], dtype=np.float32),
                                     format='fltp', layout='mono')
    frame.sample_rate = source_rate
    converter = av.AudioResampler(format='fltp', layout='mono', rate=16000)
    frames = [*converter.resample(frame), *converter.resample(None)]
    if not frames:
        raise ValueError('声音重采样没有产生完整数据')
    values = np.concatenate([item.to_ndarray().reshape(-1) for item in frames]).astype(np.float32)
    if (not len(values) or len(values) > 960000 or not np.isfinite(values).all()
            or np.max(np.abs(values)) > 1
            or abs(len(values) / 16000 - len(wave) / source_rate) > 2 / 16000):
        raise ValueError('声音重采样长度或数值不一致，不能截断后继续')
    return np.ascontiguousarray(values), 'pyav_audioresampler_fltp_16000'


def window_positions(samples):
    if type(samples) is not int or not 1 <= samples <= 960000:
        raise ValueError('分析采样数超过60秒或为空')
    start = 0
    while True:
        yield start, min(samples, start + 15600)
        if start + 15600 >= samples:
            break
        start += 7680


def inspect_model(weights, recipe):
    import numpy as np
    from ai_edge_litert import schema_py_generated as schema
    from flatbuffers.table import Table
    blobs = {}
    for item in recipe['files']:
        blobs[item['name']] = read_file(weights / item['name'], item['bytes'], item['sha256'], item['bytes'])
    raw = blobs['yamnet-task-v1.tflite']
    if raw[4:8] != b'TFL3':
        raise ValueError('声音模型不是固定TFLite文件')
    label_raw = blobs['yamnet-label-list.txt']
    labels = label_raw.decode('utf8').splitlines()
    if len(labels) != 521 or len(set(labels)) != 521 or any(not x or len(x) > 300 for x in labels):
        raise ValueError('声音类别数量、顺序或唯一性无效')
    with zipfile.ZipFile(weights / 'yamnet-task-v1.tflite') as archive:
        rows = archive.infolist()
        if (len(rows) != 1 or rows[0].filename != 'yamnet_label_list.txt'
                or rows[0].file_size != 6230 or archive.read(rows[0]) != label_raw):
            raise ValueError('模型内嵌类别与固定标签不一致')
    model = schema.Model.GetRootAsModel(raw, 0)
    if model.SubgraphsLength() != 1:
        raise ValueError('声音模型子图数量不符')
    graph = model.Subgraphs(0)
    if graph.InputsLength() != 1 or graph.OutputsLength() != 1:
        raise ValueError('声音模型输入或输出数量不符')
    native = []
    for index, shape in [(int(graph.Inputs(0)), [15600]), (int(graph.Outputs(0)), [1, 521])]:
        tensor = graph.Tensors(index)
        actual = [int(x) for x in tensor.ShapeAsNumpy()]
        if actual != shape or tensor.Type() != schema.TensorType.FLOAT32:
            raise ValueError('声音模型不是float32[15600]到float32[1,521]')
        native.append({'name': tensor.Name().decode(), 'shape': actual, 'dtype': 'float32'})
    metadata = None
    for index in range(model.MetadataLength()):
        row = model.Metadata(index)
        if row.Name() == b'TFLITE_METADATA':
            if metadata is not None:
                raise ValueError('声音模型元数据重复')
            data = model.Buffers(row.Buffer()).DataAsNumpy().tobytes()
            if len(data) < 8 or data[4:8] != b'M001':
                raise ValueError('声音模型元数据类型无效')
            table = Table(bytearray(data), struct.unpack_from('<I', data, 0)[0])
            metadata = {}
            for name, field in [('name', 0), ('version', 2), ('author', 4), ('license', 5)]:
                offset = table.Offset((field + 2) * 2)
                metadata[name] = table.String(offset + table.Pos).decode('utf8') if offset else None
    if metadata != MODEL_METADATA:
        raise ValueError('声音模型作者、版本或内嵌许可声明不符')
    custom = [model.OperatorCodes(i).CustomCode() for i in range(model.OperatorCodesLength())
              if model.OperatorCodes(i).CustomCode() is not None]
    quantized = sum(graph.Tensors(i).Quantization() is not None
                    and graph.Tensors(i).Quantization().ScaleLength() > 0
                    for i in range(graph.TensorsLength()))
    if custom or quantized != 89:
        raise ValueError('声音模型算子或内部量化结构不符')
    return labels, {'pack_id': recipe['pack_id'], 'model_sha256': recipe['files'][0]['sha256'],
                    'labels_sha256': recipe['files'][1]['sha256'], 'labels_count': 521, 'labels': labels,
                    'native_input': native[0], 'native_output': native[1], 'metadata': metadata,
                    'quantized_tensors': quantized, 'weight_precision': 'internally_quantized_not_all_fp32',
                    'runtime_versions': {name: version(name) for name in (*recipe['environment'], 'flatbuffers')}}


def classify(waveform, labels, model_path, threshold):
    import numpy as np
    from ai_edge_litert.compiled_model import CompiledModel, HardwareAccelerator
    model = CompiledModel.from_file(str(model_path), hardware_accel=HardwareAccelerator.CPU)
    inputs = model.create_input_buffers(0)
    outputs = model.create_output_buffers(0)
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError('声音模型运行缓冲区数量不符')
    windows = []
    grouped = {}
    for index, (start, end) in enumerate(window_positions(len(waveform))):
        block = np.zeros(15600, dtype=np.float32)
        block[:end - start] = waveform[start:end]
        inputs[0].write(block)
        model.run_by_index(0, inputs, outputs)
        scores = outputs[0].read(521, np.float32).reshape(521)
        if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
            raise ValueError('声音模型输出含非有限或超范围分数')
        order = np.argsort(-scores, kind='stable')[:5]
        top = [{'class_index': int(i), 'label': labels[int(i)], 'score': float(scores[int(i)])} for i in order]
        windows.append({'index': index, 'start_sample': start, 'end_sample': end,
                        'padded_samples': 15600 - (end - start), 'start_seconds': start / 16000,
                        'end_seconds': end / 16000, 'scores': [float(x) for x in scores], 'top_classes': top})
        for i in np.flatnonzero(scores.astype(np.float64) >= threshold):
            entry = grouped.setdefault(int(i), {'class_index': int(i), 'label': labels[int(i)],
                                               'max_score': float(scores[int(i)]), 'window_indices': []})
            entry['max_score'] = max(entry['max_score'], float(scores[int(i)]))
            entry['window_indices'].append(index)
    return {'sample_rate': 16000, 'window_samples': 15600, 'hop_samples': 7680,
            'threshold': threshold, 'top_k': 5, 'score_semantics': 'uncalibrated_classification_score',
            'timing': 'analysis_windows_not_event_boundaries', 'inference_performed': True,
            'all_samples_covered': True, 'vad_used': False, 'asr_used': False,
            'domain_quality': 'not_verified', 'windows': windows,
            'candidates': [grouped[i] for i in sorted(grouped)]}


def deny_network(*args, **kwargs):
    raise RuntimeError('本地声音进程禁止网络访问')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['metadata', 'classify'])
    parser.add_argument('--weights')
    parser.add_argument('--audio', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    recipe = json.loads(read_file(ROOT / 'model-packs/yamnet-mediapipe-v1.json', 128 * 1024))
    request = json.loads(read_file(args.request, 4096))
    selected = configuration(request)
    content = read_file(args.audio, recipe['limits']['max_bytes'])
    # OS sandboxing is enforced by the parent. These Python guards add defense in depth.
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    start = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        for name in ['numpy', 'av'] if args.mode == 'metadata' else recipe['environment']:
            if version(name) != recipe['environment'][name]:
                raise ValueError('本地声音依赖与固定版本不一致：' + name)
        wave, audio = decode_audio(Path(args.audio), selected['channel'], recipe)
        if args.mode == 'metadata':
            result = {'schema': 'local-sound-metadata-v1', 'input_sha256': hashlib.sha256(content).hexdigest(), 'audio': audio}
        else:
            if not args.weights:
                raise ValueError('缺少固定声音模型目录')
            labels, model_trace = inspect_model(Path(args.weights), recipe)
            values, policy = resample(wave, audio['sample_rate'])
            result = {'schema': 'local-environment-sound-v1', 'deployment': 'local', 'backend': 'cpu',
                      'cloud_calls': 0, 'input_sha256': hashlib.sha256(content).hexdigest(),
                      'recipe_hash': digest(recipe), 'model': model_trace,
                      'audio': {**audio, 'analysis_sample_rate': 16000, 'analysis_samples': len(values), 'resampling': policy},
                      'classification': classify(values, labels, Path(args.weights) / 'yamnet-task-v1.tflite', selected['threshold']),
                      'elapsed_seconds': time.monotonic() - start}
        if hashlib.sha256(read_file(args.audio, recipe['limits']['max_bytes'])).hexdigest() != hashlib.sha256(content).hexdigest():
            raise ValueError('分析期间原音发生变化')
        if args.mode == 'classify':
            # Re-check on-disk original assets after native execution, never trust cached labels alone.
            for item in recipe['files']:
                read_file(Path(args.weights) / item['name'], item['bytes'], item['sha256'], item['bytes'])
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()

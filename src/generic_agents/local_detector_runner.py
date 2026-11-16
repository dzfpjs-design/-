"""Pinned local RT-DETRv2 inference. Run only in the isolated specialist venv.

No cloud SDK, network fetch, remote code, or automatic device fallback.
"""
import argparse
import hashlib
import json
import os
import time
from importlib.metadata import version
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', required=True)
    parser.add_argument('--image')
    parser.add_argument('--backend', choices=['cpu', 'mps'], default='cpu')
    parser.add_argument('--threshold', type=float, default=.5)
    args = parser.parse_args()
    if not 0.01 <= args.threshold <= .99:
        raise ValueError('threshold outside supported range')
    recipe = json.loads((Path(__file__).resolve().parents[2] / 'model-packs/rtdetr-v2-r18.json').read_text())
    dependency_versions = {name: version(name) for name in recipe['environment']}
    if dependency_versions != recipe['environment']:
        raise ValueError('local specialist environment differs from the pinned recipe')
    import torch
    import transformers
    from PIL import Image
    from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection
    torch.set_num_threads(4)
    if args.backend == 'mps' and not torch.backends.mps.is_available():
        raise ValueError('MPS is unavailable; no implicit CPU fallback')
    started = time.monotonic()
    processor = RTDetrImageProcessor.from_pretrained(args.weights, local_files_only=True)
    model = RTDetrV2ForObjectDetection.from_pretrained(args.weights, local_files_only=True,
        use_safetensors=True, trust_remote_code=False, disable_custom_kernels=True).eval().to(args.backend)
    loaded_ms = round((time.monotonic() - started) * 1000)
    if args.image:
        raw = Path(args.image).read_bytes()
        with Image.open(args.image) as original:
            # Match decoded original pixel coordinates; EXIF orientation is explicit metadata.
            orientation = original.getexif().get(274, 1)
            image = original.convert('RGB')
        sha = hashlib.sha256(raw).hexdigest()
    else:
        image = Image.new('RGB', (640, 480), 'white')
        sha = None
        orientation = 1
    inputs = processor(images=image, return_tensors='pt').to(args.backend)
    started = time.monotonic()
    with torch.inference_mode():
        output = model(**inputs)
    if args.backend == 'mps':
        torch.mps.synchronize()
    inference_ms = round((time.monotonic() - started) * 1000)
    result = processor.post_process_object_detection(output,
        target_sizes=torch.tensor([(image.height, image.width)], device=args.backend),
        threshold=args.threshold)[0]
    detections = []
    for score, label, box in zip(result['scores'], result['labels'], result['boxes']):
        x1, y1, x2, y2 = box.detach().cpu().tolist()
        bbox = [max(0, min(image.width, x1)), max(0, min(image.height, y1)),
                max(0, min(image.width, x2)), max(0, min(image.height, y2))]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        bbox = [round(v, 2) for v in bbox]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        detections.append({'label_id': int(label), 'label': model.config.id2label[int(label)],
                           'score': float(score), 'bbox': bbox})
    print(json.dumps({'schema': 'local-detection-v1', 'backend': args.backend,
        'width': image.width, 'height': image.height, 'image_sha256': sha,
        'exif_orientation': orientation, 'coordinates': 'decoded_original_pixels_xyxy',
        'threshold': args.threshold, 'detections': detections,
        'model_load_ms': loaded_ms, 'inference_ms': inference_ms,
        'torch_version': torch.__version__, 'transformers_version': transformers.__version__,
        'dependency_versions': dependency_versions,
        'mps_available': torch.backends.mps.is_available(), 'cloud_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()

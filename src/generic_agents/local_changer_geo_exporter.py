"""Verify learned artifacts, then retain exact probability/mask georeferencing."""
import argparse
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path


def export(aligned, learned, request, out, recipe):
    import numpy as np
    import rasterio
    from affine import Affine
    from PIL import Image
    from pyproj import network
    network.set_network_enabled(False)
    for name, pin in recipe['environment'].items():
        if version(name) != pin:
            raise ValueError('local geospatial dependencies differ from the fixed recipe')
    width, height = request['width'], request['height']
    if type(width) is not int or type(height) is not int or not 0 < width * height <= recipe['max_inference_pixels']:
        raise ValueError('learned raster dimensions exceed local bounds')
    transform = request['transform']
    if len(transform) != 6 or any(type(v) not in {int, float} or not math.isfinite(v) for v in transform):
        raise ValueError('learned raster transform is invalid')
    matrix = Affine(*transform)
    if matrix.determinant == 0:
        raise ValueError('learned raster transform is singular')
    def image(path, mode):
        with Image.open(path) as im:
            if im.format != 'PNG' or im.mode != mode or im.size != (width, height):
                raise ValueError('learned artifacts do not match the aligned pixel grid')
            return np.array(im)
    for name in ('before', 'after', 'valid'):
        content = (Path(aligned) / (name + '.png')).read_bytes()
        if hashlib.sha256(content).hexdigest() != request['aligned_input_sha256'][name]:
            raise ValueError('aligned artifact fingerprint differs from the model input')
    valid_values = image(Path(aligned) / 'valid.png', 'L')
    if not bool(((valid_values == 0) | (valid_values == 255)).all()):
        raise ValueError('common-valid mask is not binary')
    valid = valid_values > 0
    path = Path(learned) / 'probability.npy'
    if path.stat().st_size > width * height * 4 + 1024:
        raise ValueError('learned probability array exceeds the byte budget')
    probabilities = np.load(path, mmap_mode='r', allow_pickle=False)
    if not isinstance(probabilities, np.ndarray) or probabilities.dtype != np.float32 or probabilities.shape != (height, width):
        raise ValueError('learned probability array format is invalid')
    if (not bool(np.isfinite(probabilities[valid]).all())
            or not bool(np.isnan(probabilities[~valid]).all())
            or bool(((probabilities[valid] < 0) | (probabilities[valid] > 1)).any())):
        raise ValueError('unknown pixels must stay NaN and valid probabilities must be finite')
    threshold = request['probability_threshold']
    if type(threshold) not in {int, float} or not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError('learned probability threshold is invalid')
    candidate = valid & (probabilities >= threshold)
    overlay = image(Path(learned) / 'candidate.png', 'RGBA')
    expected_overlay = np.zeros((height, width, 4), dtype=np.uint8)
    expected_overlay[candidate] = [240, 100, 39, 190]
    quantized = image(Path(learned) / 'probability.png', 'I;16')
    expected_quantized = np.zeros((height, width), dtype=np.uint16)
    expected_quantized[valid] = np.rint(probabilities[valid] * 65534 + 1).astype(np.uint16)
    if not np.array_equal(overlay, expected_overlay) or not np.array_equal(quantized, expected_quantized):
        raise ValueError('saved probability and candidate images do not match the exact learned array')
    counts = {'total_pixels': width * height, 'common_valid_pixels': int(valid.sum()),
              'unknown_pixels': int((~valid).sum()), 'candidate_pixels': int(candidate.sum())}
    if any(type(request.get(key)) is not int or request[key] != value for key, value in counts.items()):
        raise ValueError('learned raster counts do not match the saved evidence')
    if not bool(valid.any()):
        raise ValueError('no common-valid pixels; no no-change conclusion accepted')
    out = Path(out)
    if not out.is_dir() or any((out / name).exists() for name in ('probability.tif', 'change-mask.tif')):
        raise ValueError('geospatial output must not overwrite a saved result')
    profile = {'driver': 'GTiff', 'height': height, 'width': width, 'count': 1,
               'crs': request['crs_wkt'], 'transform': matrix, 'compress': 'deflate'}
    labels = np.full((height, width), 255, dtype=np.uint8)
    labels[valid] = candidate[valid].astype(np.uint8)
    with rasterio.Env(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO'):
        with rasterio.open(out / 'probability.tif', 'w', dtype='float32', nodata=float('nan'), **profile) as dst:
            dst.write(probabilities, 1)
            dst.update_tags(result_kind='learned_binary_change_candidate', domain_quality='not_verified',
                            weights_sha256=request['weights_sha256'])
        with rasterio.open(out / 'change-mask.tif', 'w', dtype='uint8', nodata=255, **profile) as dst:
            dst.write(labels, 1)
            dst.update_tags(labels='0=below_threshold,1=candidate,255=unknown',
                            probability_threshold=str(threshold), domain_quality='not_verified')
        with rasterio.open(out / 'probability.tif') as src:
            restored = src.read(1)
            if (src.transform != matrix or src.crs != rasterio.crs.CRS.from_wkt(request['crs_wkt'])
                    or not np.array_equal(restored, probabilities, equal_nan=True)):
                raise ValueError('geospatial probability readback differs from the exact learned result')
        with rasterio.open(out / 'change-mask.tif') as src:
            if not np.array_equal(src.read(1), labels) or src.nodata != 255:
                raise ValueError('geospatial candidate mask readback differs from the learned result')
    return {'schema': 'local-changer-geospatial-export-v1', **counts,
            'exact_probability_readback': True, 'mask_readback': True,
            'unknown_probability': 'NaN', 'unknown_label': 255,
            'cloud_calls': 0, 'transform': transform, 'domain_quality_verified': False}


def main():
    parser = argparse.ArgumentParser()
    for name in ('aligned', 'learned', 'request', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    recipe = json.loads((root / 'runtime-packs/geo-rgb-v1.json').read_text())
    print(json.dumps(export(args.aligned, args.learned, json.loads(Path(args.request).read_text()), args.out, recipe), allow_nan=False))


if __name__ == '__main__':
    main()

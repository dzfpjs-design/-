"""Real local GeoTIFF -> trained model -> exact georeferenced candidate evidence.

Uses only self-generated engineering fixtures, never claims remote-sensing
quality or writes app data. Each completed case is checkpointed on disk.
"""
import hashlib
import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.runtime/changer-pair-validation'
EVIDENCE = ROOT / 'docs/evidence/changer-preparation-v06'


def save(name, value):
    path = OUT / name
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main():
    from generic_agents.changer_pair_codec import call, RECIPE_HASH
    from generic_agents.config import settings
    if OUT.exists():
        raise ValueError('validation directory already exists; retain earlier proof instead of overwriting')
    OUT.mkdir(mode=0o700)
    save('status.json', {'stage': 'preparing_self_generated_GeoTIFF', 'cloud_calls': 0})
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'TMPDIR', 'LANG'}}
    env.update(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO')
    generation = r'''
import numpy as np,rasterio
from rasterio.transform import from_origin
from pathlib import Path
root=Path(__import__('sys').argv[1]);height,width=540,620
yy,xx=np.indices((height,width))
a=np.stack([40+(xx//20%5)*30,50+(yy//20%5)*30,60+((xx+yy)//30%5)*25],axis=0).astype('uint8')
b=a.copy();b[:,100:190,200:310]=np.array([220,210,180],dtype='uint8')[:,None,None]
a[:,200:250,260:300]=0;b[:,260:300,300:340]=0
for name,values,dx in [('before',a,0),('after',b,0),('after-shifted',b,.5)]:
 profile={'driver':'GTiff','height':height,'width':width,'count':3,'dtype':'uint8','crs':'EPSG:32650','transform':from_origin(500000+dx,2680000+dx,.5,.5),'nodata':0,'compress':'deflate'}
 with rasterio.open(root/(name+'.tif'),'w',**profile) as dst:
  dst.write(values);dst.update_tags(source='self_generated_engineering_fixture_not_remote_sensing_sample')
 (root/(name+'.tif')).chmod(0o600)
'''
    subprocess.run([str(settings().geo_python.absolute()), '-c', generation, str(OUT)],
                   env=env, check=True, timeout=30, capture_output=True)
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in OUT.glob('*.tif')}
    request = {'roi': [30, 10, 600, 530], 'alignment': 'same_grid', 'probability_threshold': .5,
               'before_configuration': {'rgb_bands': [1, 2, 3], 'ranges': None},
               'after_configuration': {'rgb_bands': [1, 2, 3], 'ranges': None},
               'after_allowed_window': [40, 20, 610, 530]}
    results = {}
    for case, after, alignment in [('same-grid', 'after.tif', 'same_grid'),
                                  ('explicit-reprojection', 'after-shifted.tif', 'reproject_after_to_before')]:
        save('status.json', {'stage': case, 'completed_cases': list(results), 'cloud_calls': 0})
        folder = OUT / case; folder.mkdir(mode=0o700)
        result = call(OUT / 'before.tif', OUT / after, {**request, 'alignment': alignment}, folder)
        if (result['runtime_hash'] != RECIPE_HASH or result['learned_model'] is not True
                or result['cloud_calls'] != 0 or not result['geospatial_export']['exact_probability_readback']
                or result['unknown_pixels'] <= 0 or result['tiles_inferred'] <= 1):
            raise ValueError('local paired-model evidence is incomplete')
        results[case] = result
        save(case + '.json', result)
    folder = OUT / 'wrong-grid-rejected';folder.mkdir(mode=0o700)
    try:
        call(OUT / 'before.tif', OUT / 'after-shifted.tif', request, folder)
    except ValueError as error:
        if any(folder.iterdir()):
            raise ValueError('rejected grid mismatch produced accepted output')
        rejected = {'error': str(error), 'no_output': True, 'automatic_reprojection_used': False}
    else:
        raise ValueError('grid mismatch was silently accepted')
    if source_hashes != {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in OUT.glob('*.tif')}:
        raise ValueError('original engineering fixtures changed')
    proof = {'passed': True, 'input_kind': 'self_generated_engineering_GeoTIFF_not_remote_sensing_quality_sample',
             'local_cpu': True, 'cloud_calls': 0, 'app_data_written': False,
             'old_recipe_or_record_changed': False, 'source_sha256': source_hashes,
             'runtime_hash': RECIPE_HASH, 'cases': results, 'wrong_grid_rejected': rejected,
             'domain_quality_verified': False, 'browser_flow_verified': False,
             'business_capability_enabled': False}
    save('verification.json', proof)
    save('status.json', {'stage': 'completed', 'cases': list(results), 'passed': True, 'cloud_calls': 0})
    (EVIDENCE / 'paired-native.json').write_text(json.dumps(proof, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'passed': True, 'cases': len(results), 'wrong_grid_rejected': True,
                      'cloud_calls': 0, 'business_capability_enabled': False}))


def threshold_sensitivity():
    """An explicit engineering threshold, never a recommended business threshold."""
    from generic_agents.changer_pair_codec import call, RECIPE_HASH
    from generic_agents.geo_codec import file_digest
    baseline = json.loads((OUT / 'verification.json').read_text())
    if baseline['runtime_hash'] != RECIPE_HASH:
        raise ValueError('prepared validation recipe changed; retain earlier evidence')
    for name, expected in baseline['source_sha256'].items():
        if file_digest(OUT / name) != expected:
            raise ValueError('prepared validation original changed')
    previous = baseline['cases']['same-grid']
    for name, artifact in previous['artifacts'].items():
        if file_digest(OUT / 'same-grid' / name) != artifact['sha256']:
            raise ValueError('earlier learned result changed')
    folder = OUT / 'threshold-sensitivity'
    if folder.exists():
        raise ValueError('retain existing threshold evidence; no overwrite')
    folder.mkdir(mode=0o700)
    request = {'roi': [30, 10, 600, 530], 'alignment': 'same_grid', 'probability_threshold': .05,
               'before_configuration': {'rgb_bands': [1, 2, 3], 'ranges': None},
               'after_configuration': {'rgb_bands': [1, 2, 3], 'ranges': None},
               'after_allowed_window': [40, 20, 610, 530]}
    save('status.json', {'stage': 'engineering_threshold_sensitivity', 'cloud_calls': 0})
    result = call(OUT / 'before.tif', OUT / 'after.tif', request, folder)
    if (result['artifacts']['probability.npy']['sha256'] != previous['artifacts']['probability.npy']['sha256']
            or result['artifacts']['probability.png']['sha256'] != previous['artifacts']['probability.png']['sha256']
            or result['candidate_pixels'] < previous['candidate_pixels']):
        raise ValueError('threshold unexpectedly changed learned probabilities or removed higher-threshold candidates')
    if result['geometry'] is not None and result['geometry']['pixel_area'] != result['candidate_pixels']:
        raise ValueError('candidate geometry does not match thresholded pixels')
    proof = {'passed': True, 'engineering_threshold_only_not_product_recommendation': True,
             'same_probabilities_at_both_thresholds': True, 'result': result,
             'previous_threshold': previous['probability_threshold'],
             'previous_candidate_pixels': previous['candidate_pixels'],
             'cloud_calls': 0, 'domain_quality_verified': False, 'business_capability_enabled': False}
    save('threshold-native.json', proof)
    (EVIDENCE / 'threshold-native.json').write_text(json.dumps(proof, indent=2, allow_nan=False) + '\n')
    save('status.json', {'stage': 'completed', 'passed': True, 'cloud_calls': 0,
                         'threshold_sensitivity_verified': True})
    print(json.dumps({'passed': True, 'same_probabilities': True,
                      'candidate_pixels': result['candidate_pixels'], 'cloud_calls': 0,
                      'engineering_threshold_only': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold-sensitivity', action='store_true')
    args = parser.parse_args()
    threshold_sensitivity() if args.threshold_sensitivity else main()

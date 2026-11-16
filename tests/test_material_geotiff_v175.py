"""Pure metadata and adapter-boundary tests; not real GDAL/API/PG evidence.

The tiny source bytes below only exercise copy/hash/temp/lease boundaries.
The codec is an explicit boundary spy; Root separately runs actual GeoTIFFs.
"""
import ast
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from generic_agents import material_geotiff_v175 as module
from generic_agents.document_parsers import ParseProblem, parse_document
from generic_agents.leases import LeaseLost, ExecutionUnauthorized


def metadata():
    return {'schema': 'local-raster-v1', 'width': 256, 'height': 256, 'bands': 4,
        'dtypes': ['uint16'] * 4, 'color_interpretation': ['gray', 'undefined', 'undefined', 'undefined'],
        'suggested_rgb_bands': None, 'nodata': [0.0] * 4, 'nodata_is_nan': [False] * 4,
        'block_shapes': [[128, 128]] * 4, 'metadata_time_text': None,
        'observed_at': None, 'cloud_calls': 0, 'crs': 'EPSG:32650',
        'crs_wkt': 'synthetic-WKT-for-pure-test',
        'transform': [10.0, 0.0, 540000.0, 0.0, -10.0, 2745000.0],
        'axis_units': ['metre', 'metre'], 'georeference_status': 'affine_available',
        'ground_accuracy': 'not_verified', 'pixel_scale_native': [10.0, 10.0],
        'footprint_native': {'type': 'Polygon', 'coordinates': [[[540000.0, 2745000.0],
            [542560.0, 2745000.0], [542560.0, 2742440.0], [540000.0, 2742440.0], [540000.0, 2745000.0]]]}}


def result(value=None):
    return module.metadata_result(value or metadata(), source_sha256='a' * 64,
                                  source_size_bytes=512, evidence_id='source-evidence')


def test_metadata_is_a_resource_with_no_business_body_or_ocr_claim():
    original = metadata(); before = copy.deepcopy(original)
    value = result(original)
    assert original == before
    assert value['status'] == 'resource_ready' and value['blocks'] == []
    assert value['engine'] == 'geotiff_metadata_v175'
    assert value['resource_kind'] == 'geospatial_raster'
    assert value['source_kind'] == value['engine']
    assert value['ocr_coverage'] is None
    assert value['resource_coverage']['status'] == 'not_applicable'
    geo = value['geospatial_metadata']
    for key in ('crs', 'crs_wkt', 'transform', 'width', 'height', 'bands', 'dtypes', 'nodata'):
        assert geo[key] == original[key]
    assert geo['source_sha256'] == 'a' * 64 and geo['source_size_bytes'] == 512
    assert geo['observed_at'] is None and geo['source_time_verified'] is False
    assert geo['analysis_performed'] is False and geo['business_requirement_source'] is False
    assert geo['cloud_calls'] == 0


def test_absent_nodata_nan_flag_and_unverified_time_are_preserved_without_guessing():
    value = metadata(); value['nodata'] = [None, None, 0, -9999]
    value['nodata_is_nan'] = [False, True, False, False]
    value['metadata_time_text'] = '2024:01:18 02:50:58'
    geo = result(value)['geospatial_metadata']
    assert geo['nodata'] == [None, None, 0, -9999]
    assert geo['nodata_is_nan'] == [False, True, False, False]
    assert geo['metadata_time_text'] == value['metadata_time_text']
    assert geo['observed_at'] is None


def test_missing_map_reference_is_retained_as_unknown_not_invented():
    value = metadata(); value.update(crs=None, crs_wkt=None, axis_units=None,
        georeference_status='missing_crs', footprint_native=None)
    geo = result(value)['geospatial_metadata']
    assert geo['crs'] is None and geo['footprint_native'] is None
    assert geo['georeference_status'] == 'missing_crs'


@pytest.mark.parametrize('key,value', [
    ('width', True), ('height', 0), ('bands', 65), ('schema', 'other-family'),
    ('cloud_calls', False), ('cloud_calls', 1), ('observed_at', '2024-01-18'),
    ('dtypes', ['uint16']), ('dtypes', ['complex64'] * 4),
    ('nodata', [float('nan')] * 4), ('nodata_is_nan', [0] * 4),
    ('transform', [1, 0, 0, 0, float('inf'), 0]),
    ('suggested_rgb_bands', [1, 1, 2]), ('metadata_time_text', 'x' * 1025),
    ('footprint_native', {'type': 'Polygon', 'coordinates': [['invented']]}),
])
def test_malformed_or_guessed_metadata_is_rejected(key, value):
    item = metadata(); item[key] = value
    with pytest.raises(ParseProblem):
        result(item)


def test_extra_text_fields_and_oversized_metadata_are_not_silently_accepted():
    item = metadata(); item['business_requirements'] = 'invented requirements'
    with pytest.raises(ParseProblem):
        result(item)
    item = metadata(); item['crs_wkt'] = 'x' * 32769
    with pytest.raises(ParseProblem):
        result(item)


@pytest.mark.parametrize('field', ['transform', 'nodata', 'pixel_scale_native', 'footprint_native'])
def test_huge_integer_is_rejected_as_metadata_problem_without_numeric_overflow(field):
    giant = 10 ** 10000
    assert module.number(giant) is False
    assert module.number(-giant) is False
    item = metadata()
    if field == 'footprint_native':
        item[field]['coordinates'][0][0][0] = giant
    else:
        item[field][0] = giant
    with pytest.raises(ParseProblem):
        result(item)


@pytest.mark.parametrize('field,value', [
    ('suggested_rgb_bands', [[1], 2, 3]),
    ('georeference_status', ['affine_available']),
])
def test_malformed_unhashable_values_do_not_escape_as_type_errors(field, value):
    item = metadata(); item[field] = value
    with pytest.raises(ParseProblem):
        result(item)


def adapter(tmp_path, monkeypatch):
    content = b'II*\x00' + b'bounded synthetic bytes; not a real GDAL image' * 8
    source = tmp_path / 'server-owned-original.bin'; source.write_bytes(content)
    checksum = hashlib.sha256(content).hexdigest()
    principal = SimpleNamespace(user_id='actor', workspace_id='workspace', role='operator')
    material = SimpleNamespace(id='material', evidence_id='evidence', sha256=checksum,
        name='public-input.tif', status='parsing', data={'parse_generation': 1})
    evidence = SimpleNamespace(id='evidence', sha256=checksum,
        media_type='image/tiff', size_bytes=len(content))
    job = SimpleNamespace(kind='parse_setup_material', workspace_id='workspace',
        data={'material_id': 'material', 'generation': 1, 'actor_id': 'actor'})
    db = object(); seen = {'checkpoints': 0, 'paths': [], 'codec': []}
    def scoped(actual_db, cls, identity, p, lock):
        assert actual_db is db and cls is module.SetupMaterial and identity == material.id
        assert p is principal and lock is True
        return material
    def path_for(actual_db, p, identity):
        assert actual_db is db and p is principal and identity == evidence.id
        if source.stat().st_size != evidence.size_bytes or hashlib.sha256(source.read_bytes()).hexdigest() != evidence.sha256:
            raise ParseProblem('cold source SHA mismatch')
        return evidence, source
    def checkpoint(actual_db):
        assert actual_db is db; seen['checkpoints'] += 1
    def call(mode, path, *, guard):
        assert mode == 'inspect' and path != source and path.suffix == '.tif'
        assert path.read_bytes() == content and path.stat().st_mode & 0o777 == 0o600
        seen['paths'].append(path); seen['codec'].append(mode); guard()
        return metadata()
    monkeypatch.setattr(module, 'scoped', scoped)
    monkeypatch.setattr(module, 'path_for', path_for)
    monkeypatch.setattr(module, 'checkpoint', checkpoint)
    monkeypatch.setattr(module, 'call', call)
    return db, principal, material, job, evidence, source, content, seen


def test_adapter_uses_private_temp_and_cold_source_without_modifying_original(tmp_path, monkeypatch):
    db, p, material, job, evidence, source, content, seen = adapter(tmp_path, monkeypatch)
    before = source.stat()
    value = module.parse_material(db, p, material, job)
    assert value['geospatial_metadata']['source_sha256'] == evidence.sha256
    assert source.read_bytes() == content
    assert (source.stat().st_mtime_ns, source.stat().st_ino) == (before.st_mtime_ns, before.st_ino)
    assert seen['codec'] == ['inspect'] and seen['checkpoints'] >= 4
    assert all(not path.exists() and not path.parent.exists() for path in seen['paths'])


@pytest.mark.parametrize('exception', [LeaseLost, ExecutionUnauthorized])
def test_lease_and_authorization_loss_propagate_and_cleanup(tmp_path, monkeypatch, exception):
    db, p, material, job, _, source, content, seen = adapter(tmp_path, monkeypatch)
    def failing_codec(mode, path, *, guard):
        seen['paths'].append(path)
        def lost(_db):
            raise exception('synthetic lease or actor loss')
        monkeypatch.setattr(module, 'checkpoint', lost)
        guard()
    monkeypatch.setattr(module, 'call', failing_codec)
    with pytest.raises(exception):
        module.parse_material(db, p, material, job)
    assert source.read_bytes() == content
    assert all(not path.exists() and not path.parent.exists() for path in seen['paths'])


@pytest.mark.parametrize('change', ['cancel', 'generation', 'source'])
def test_changed_inputs_never_produce_resource_ready(tmp_path, monkeypatch, change):
    db, p, material, job, _, source, _, seen = adapter(tmp_path, monkeypatch)
    def changed(mode, path, *, guard):
        seen['paths'].append(path)
        if change == 'cancel':
            material.status = 'cancelled'
        elif change == 'generation':
            material.data['parse_generation'] += 1
        else:
            source.write_bytes(source.read_bytes() + b'changed')
        guard()
        return metadata()
    monkeypatch.setattr(module, 'call', changed)
    with pytest.raises((LeaseLost, ParseProblem)):
        module.parse_material(db, p, material, job)
    assert all(not path.exists() and not path.parent.exists() for path in seen['paths'])


def test_plain_document_parser_contract_still_does_not_manufacture_tiff_body():
    value = parse_document(b'II*\x00synthetic', 'sample.tif')
    assert value['status'] == 'needs_input' and value['engine'] == 'not_supported'
    assert value['blocks'] == []


def test_setup_dispatch_and_business_source_gates_remain_explicit_in_public_source():
    root = Path(__file__).resolve().parents[1] / 'src/generic_agents'
    tree = ast.parse((root / 'setup_api.py').read_text())
    process = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'process_material')
    main_try = next(n for n in process.body if isinstance(n, ast.Try))
    first = main_try.handlers[0]
    assert isinstance(first.type, ast.Tuple)
    assert {n.id for n in first.type.elts} == {'LeaseLost', 'ExecutionUnauthorized'}
    assert len(first.body) == 1 and isinstance(first.body[0], ast.Raise)
    dispatch = main_try.body[0]
    assert isinstance(dispatch, ast.If) and isinstance(dispatch.test, ast.Name) and dispatch.test.id == 'geotiff'
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'parse_material'
               for n in ast.walk(dispatch))
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'parse_document'
               for branch in dispatch.orelse for n in ast.walk(branch))
    understanding = ast.parse((root / 'setup_understanding.py').read_text())
    status_guards = [n for n in ast.walk(understanding) if isinstance(n, ast.If)
        and "m.status != 'parsed'" in ast.unparse(n.test)
        and any(isinstance(child, ast.Raise) for child in n.body)]
    assert status_guards, 'resource_ready must not satisfy the business source status gate'
    full = (root / 'report_full_reading_source.py').read_text()
    assert "row.status not in {'parsed', 'needs_input'}" in full
    assert 'PurePath(row.name).suffix.lower() != suffix' in full

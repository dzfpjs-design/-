"""Bounded raster-resource metadata, never document text or business evidence."""
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

from .document_parsers import ParseProblem
from .geo_codec import call, RECIPE, RECIPE_HASH, file_digest
from .geo_storage import path_for
from .leases import checkpoint, LeaseLost, ExecutionUnauthorized
from .models import SetupMaterial
from .services import require, scoped

ENGINE = 'geotiff_metadata_v175'
SCHEMA = 'material-geotiff-metadata-v175'
RESOURCE = 'geospatial_raster'
MAX_METADATA_BYTES = 65536
NOTICE = '这是观测资源，不能作为业务需求正文；后续通过遥感资源接入分析。'
DTYPES = {'uint8', 'int8', 'uint16', 'int16', 'uint32', 'int32',
          'uint64', 'int64', 'float32', 'float64'}
METADATA_KEYS = {'schema', 'width', 'height', 'bands', 'dtypes',
    'color_interpretation', 'suggested_rgb_bands', 'nodata', 'nodata_is_nan',
    'block_shapes', 'metadata_time_text', 'observed_at', 'cloud_calls',
    'crs', 'crs_wkt', 'transform', 'axis_units', 'georeference_status',
    'ground_accuracy', 'pixel_scale_native', 'footprint_native'}


def need(value, message='影像元数据或原件来源无法完整核对，原件保留'):
    if not value:
        raise ParseProblem(message)


def integer(value):
    return type(value) is int and value > 0


def number(value):
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def metadata_result(metadata, *, source_sha256, source_size_bytes, evidence_id):
    """Pure checked result. No pixel inference, invented acquisition date or text."""
    need(type(metadata) is dict and set(metadata) == METADATA_KEYS
         and metadata.get('schema') == 'local-raster-v1')
    need(type(metadata.get('cloud_calls')) is int and metadata['cloud_calls'] == 0)
    need('observed_at' in metadata and metadata['observed_at'] is None)
    need(metadata.get('ground_accuracy') == 'not_verified')
    width, height, bands = [metadata.get(k) for k in ('width', 'height', 'bands')]
    need(all(integer(v) for v in (width, height, bands))
         and width * height <= RECIPE['max_pixels'] and bands <= RECIPE['max_bands'])
    need(type(metadata.get('dtypes')) is list and len(metadata['dtypes']) == bands
         and all(type(v) is str and v in DTYPES for v in metadata['dtypes']))
    need(type(metadata.get('transform')) is list and len(metadata['transform']) == 6
         and all(number(v) for v in metadata['transform']))
    for key in ('nodata', 'nodata_is_nan', 'block_shapes', 'color_interpretation'):
        need(type(metadata.get(key)) is list and len(metadata[key]) == bands)
    need(all(v is None or number(v) for v in metadata['nodata']))
    need(all(type(v) is bool for v in metadata['nodata_is_nan']))
    need(all(not is_nan or value is None for value, is_nan
             in zip(metadata['nodata'], metadata['nodata_is_nan'])))
    need(all(type(v) is list and len(v) == 2 and all(integer(n) for n in v)
             for v in metadata['block_shapes']))
    need(all(type(v) is str and len(v) <= 64 for v in metadata['color_interpretation']))
    need(type(metadata.get('georeference_status')) is str and metadata['georeference_status'] in {
        'affine_available', 'unsupported_gcps_or_rpc', 'missing_crs', 'missing_transform'})
    for key, maximum in (('crs', 4096), ('crs_wkt', 32768), ('metadata_time_text', 1024)):
        need(key in metadata and (metadata[key] is None
             or type(metadata[key]) is str and len(metadata[key]) <= maximum))
    need((metadata['crs'] is None) == (metadata['crs_wkt'] is None))
    need(type(metadata.get('pixel_scale_native')) is list
         and len(metadata['pixel_scale_native']) == 2
         and all(number(v) and v >= 0 for v in metadata['pixel_scale_native']))
    units = metadata.get('axis_units')
    need('axis_units' in metadata and (units is None or type(units) is list
         and len(units) <= 4 and all(type(v) is str and len(v) <= 100 for v in units)))
    rgb = metadata.get('suggested_rgb_bands')
    need('suggested_rgb_bands' in metadata and (rgb is None or type(rgb) is list
         and len(rgb) == 3 and all(integer(v) and v <= bands for v in rgb)
         and len(set(rgb)) == 3))
    need('footprint_native' in metadata and (metadata['footprint_native'] is None
         or type(metadata['footprint_native']) is dict))
    footprint = metadata['footprint_native']
    if footprint is not None:
        need(set(footprint) == {'type', 'coordinates'} and footprint['type'] == 'Polygon'
             and type(footprint['coordinates']) is list and len(footprint['coordinates']) == 1)
        ring = footprint['coordinates'][0]
        need(type(ring) is list and len(ring) == 5
             and all(type(point) is list and len(point) == 2 and all(number(v) for v in point)
                     for point in ring) and ring[0] == ring[-1])
    need((metadata['georeference_status'] == 'affine_available') == (footprint is not None))
    need(type(source_sha256) is str and re.fullmatch(r'[a-f0-9]{64}', source_sha256))
    need(integer(source_size_bytes) and source_size_bytes <= RECIPE['max_upload_bytes'])
    need(type(evidence_id) is str and 0 < len(evidence_id) <= 100)
    try:
        encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode('utf-8', 'strict')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ParseProblem('影像元数据含不支持的值；原件保留') from None
    need(len(encoded) <= MAX_METADATA_BYTES, '影像元数据超过有界读取额度；原件保留')
    # Preserve actual native codec fields, including absent nodata versus NaN.
    inspected = copy.deepcopy(metadata)
    inspected['inspection_schema'] = inspected.pop('schema')
    geospatial = {**inspected, 'schema': SCHEMA, 'source_sha256': source_sha256,
        'source_size_bytes': source_size_bytes, 'source_evidence_id': evidence_id,
        'runtime_recipe_hash': RECIPE_HASH, 'source_time_verified': False,
        'analysis_performed': False, 'business_requirement_source': False}
    return {'status': 'resource_ready', 'engine': ENGINE,
        'resource_kind': RESOURCE, 'source_kind': ENGINE,
        'blocks': [], 'geospatial_metadata': geospatial, 'error': None,
        'warnings': [NOTICE], 'ocr_coverage': None,
        'resource_coverage': {'status': 'not_applicable',
            'reason': '影像资源没有文档正文；文档/OCR覆盖核对不适用，未据此确认任何业务需求。'}}


def parse_material(db, p, material, job):
    """Inspect one saved raster under the original Job lease and generation."""
    require(p, 'admin', 'operator')
    need(Path(material.name).suffix.lower() in {'.tif', '.tiff'})
    generation = job.data.get('generation')
    need(integer(generation) and job.kind == 'parse_setup_material'
         and job.workspace_id == p.workspace_id
         and job.data.get('material_id') == material.id
         and job.data.get('actor_id') == p.user_id)
    identity, evidence_id, expected_sha, name = (
        material.id, material.evidence_id, material.sha256, material.name)

    def guard():
        checkpoint(db)  # Lease and live actor checks execute on the owned commit.
        current = scoped(db, SetupMaterial, identity, p, True)
        if current.data.get('parse_generation') != generation or current.status == 'cancelled':
            raise LeaseLost('影像资料解析已取消或更新代次，旧结果未保存')
        need(current.evidence_id == evidence_id and current.sha256 == expected_sha
             and current.name == name, '影像资料原件身份已变化，旧结果未保存')
        return current

    guard()
    evidence, source = path_for(db, p, evidence_id)
    need(evidence.sha256 == expected_sha and evidence.media_type == 'image/tiff'
         and integer(evidence.size_bytes)
         and evidence.size_bytes <= RECIPE['max_upload_bytes'])
    size = evidence.size_bytes
    try:
        with tempfile.TemporaryDirectory(prefix='ga-material-geotiff-v175-') as directory:
            target = Path(directory) / 'source.tif'
            checksum, copied = hashlib.sha256(), 0
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as dest, source.open('rb') as original:
                while chunk := original.read(1024 * 1024):
                    guard()
                    if copied == 0:
                        need(chunk[:4] in {b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'},
                             '原件不是受支持的TIFF文件，未打开外部数据源')
                    copied += len(chunk)
                    need(copied <= size, '影像原件体积改变；未接受元数据')
                    checksum.update(chunk); dest.write(chunk)
                dest.flush(); os.fsync(dest.fileno())
            need(copied == size and checksum.hexdigest() == expected_sha
                 and file_digest(target) == expected_sha)
            guard()
            metadata = call('inspect', target, guard=guard)
            result = metadata_result(metadata, source_sha256=expected_sha,
                source_size_bytes=size, evidence_id=evidence_id)
            guard()
        # Recheck the original through the authoritative blob path after inspect.
        current_evidence, _ = path_for(db, p, evidence_id)
        need(current_evidence.sha256 == expected_sha and current_evidence.size_bytes == size)
        guard()
        return result
    except (LeaseLost, ExecutionUnauthorized):
        raise
    except ParseProblem:
        raise
    except (ValueError, OSError) as error:
        raise ParseProblem('本地影像元数据读取未完成：' + str(error)) from None

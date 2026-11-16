"""Coordinate-aware WMTS 1.0 matrix helpers, without I/O or network access.

Coordinates supplied by the user are longitude/latitude in degrees. Only
known CRS identifiers are accepted: guessing axis order for an unknown CRS
would select a geographically wrong tile. WMTS scale denominators use the
standard 0.28 mm pixel; geographic CRS use the OGC metres-per-degree factor.
"""
from copy import deepcopy
import math
import re

PIXEL_METRES = 0.00028
METRES_PER_DEGREE = 111319.49079327358
EARTH_RADIUS = 6378137.0
WEB_MERCATOR_MAX_LAT = 85.0511287798066


def _children(element, local):
    return [child for child in element if child.tag.rsplit('}', 1)[-1] == local]


def _text(element, local):
    values = _children(element, local)
    return (values[0].text or '').strip() if len(values) == 1 else ''


def supported_crs(value):
    """Return canonical CRS or None; never infer CRS from an ambiguous suffix."""
    text = str(value).strip().lower()
    if text in {'epsg:3857', 'urn:ogc:def:crs:epsg::3857', 'http://www.opengis.net/def/crs/epsg/0/3857', 'https://www.opengis.net/def/crs/epsg/0/3857'}:
        return 'EPSG:3857'
    if text in {'epsg:4326', 'urn:ogc:def:crs:epsg::4326', 'http://www.opengis.net/def/crs/epsg/0/4326', 'https://www.opengis.net/def/crs/epsg/0/4326'}:
        return 'EPSG:4326'
    if text in {'crs:84', 'ogc:crs84', 'urn:ogc:def:crs:ogc:1.3:crs84', 'http://www.opengis.net/def/crs/ogc/1.3/crs84', 'https://www.opengis.net/def/crs/ogc/1.3/crs84'}:
        return 'CRS:84'
    return None


def matrix_geometry(element, crs):
    """Return safe normalized geometry or None when position selection cannot be used.

    A missing/malformed geometry does not invalidate manual tile selection.
    In EPSG:4326 the WMTS TopLeftCorner follows the latitude, longitude axis
    order; in CRS:84 it follows longitude, latitude.
    """
    canonical = supported_crs(crs)
    if canonical is None:
        return None
    try:
        parts = _text(element, 'TopLeftCorner').split()
        if len(parts) != 2:
            return None
        first, second = map(float, parts)
        scale = float(_text(element, 'ScaleDenominator'))
        if not all(math.isfinite(v) for v in (first, second, scale)) or not 0 < scale <= 1e18:
            return None
        x, y = (second, first) if canonical == 'EPSG:4326' else (first, second)
        if abs(x) > 1e10 or abs(y) > 1e10:
            return None
        unit = 1.0 if canonical == 'EPSG:3857' else METRES_PER_DEGREE
        resolution = scale * PIXEL_METRES / unit
        if not math.isfinite(resolution) or not 1e-16 <= resolution <= 1e9:
            return None
        return {'crs': canonical, 'origin_x': x, 'origin_y': y, 'resolution': resolution}
    except (TypeError, ValueError, OverflowError):
        return None


def apply_matrix_limits(link, matrix_set):
    """Copy a layer's matrix set while enforcing its own advertised limits.

    A link without TileMatrixSetLimits has full matrix bounds. If a link has
    limits, only explicitly listed matrices are exposed. Any malformed,
    duplicated or out-of-bounds limit fails closed with ValueError; never
    silently turn a restricted layer into an unrestricted layer.
    """
    result = deepcopy(matrix_set)
    containers = _children(link, 'TileMatrixSetLimits')
    if not containers:
        return result
    if len(containers) != 1:
        raise ValueError('WMTS 瓦片矩阵限制无效')
    matrices = {matrix['id']: matrix for matrix in result['matrices']}
    limits = {}
    for item in _children(containers[0], 'TileMatrixLimits'):
        identity = _text(item, 'TileMatrix')
        if identity not in matrices or identity in limits:
            raise ValueError('WMTS 矩阵限制引用无效或重复')
        matrix = matrices[identity]
        parsed = {}
        for source, target in [('MinTileRow','min_row'),('MaxTileRow','max_row'),('MinTileCol','min_col'),('MaxTileCol','max_col')]:
            raw = _text(item, source)
            if not re.fullmatch(r'[0-9]{1,9}', raw):
                raise ValueError('WMTS 瓦片限制必须是非负整数')
            parsed[target] = int(raw)
        if not (0 <= parsed['min_row'] <= parsed['max_row'] < matrix['height'] and
                0 <= parsed['min_col'] <= parsed['max_col'] < matrix['width']):
            raise ValueError('WMTS 瓦片限制超出矩阵范围')
        limits[identity] = parsed
    if not limits:
        raise ValueError('WMTS 瓦片限制为空')
    result['matrices'] = [{**matrix, 'limits': limits[matrix['id']]} for matrix in result['matrices'] if matrix['id'] in limits]
    return result


def _xy(lon, lat, crs):
    if type(lon) not in (float, int) or type(lat) not in (float, int) or not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError('请输入有效的经度和纬度')
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError('经纬度超出允许范围')
    if crs == 'EPSG:3857':
        if abs(lat) > WEB_MERCATOR_MAX_LAT:
            raise ValueError('纬度超出 Web Mercator 瓦片的支持范围')
        return EARTH_RADIUS * math.radians(lon), EARTH_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    if crs in {'EPSG:4326','CRS:84'}:
        return float(lon), float(lat)
    raise ValueError('此瓦片矩阵的坐标系暂不支持按位置选择')


def _locate_xy(matrix, x, y, exclusive_southeast=False):
    geometry = matrix.get('geometry')
    if not isinstance(geometry, dict) or geometry.get('crs') not in {'EPSG:3857','EPSG:4326','CRS:84'}:
        raise ValueError('服务未提供可用的矩阵原点和比例尺，无法按位置选择')
    try:
        sx = geometry['resolution'] * matrix['tile_width']
        sy = geometry['resolution'] * matrix['tile_height']
        if not all(math.isfinite(v) for v in (sx, sy, x, y, geometry['origin_x'], geometry['origin_y'])) or sx <= 0 or sy <= 0:
            raise ValueError()
        col_index = (x - geometry['origin_x']) / sx
        row_index = (geometry['origin_y'] - y) / sy
        # Move the normalized index, not the original coordinate: subtracting
        # a large origin can otherwise round a nextafter-adjusted edge back to
        # the same whole-number cell boundary.
        if exclusive_southeast:
            col_index = math.nextafter(col_index, -math.inf)
            row_index = math.nextafter(row_index, -math.inf)
        col = math.floor(col_index)
        row = math.floor(row_index)
        limits = matrix.get('limits') or {'min_row':0,'max_row':matrix['height']-1,'min_col':0,'max_col':matrix['width']-1}
        if not (0 <= row < matrix['height'] and 0 <= col < matrix['width'] and
                limits['min_row'] <= row <= limits['max_row'] and limits['min_col'] <= col <= limits['max_col']):
            raise ValueError('位置不在此图层允许读取的瓦片范围内')
        return {'row':row, 'col':col}
    except (KeyError, TypeError, OverflowError, ZeroDivisionError):
        raise ValueError('矩阵几何信息无效，请重新读取服务目录') from None


def locate_point(matrix, lon, lat):
    """Locate one longitude/latitude point using the selected saved matrix."""
    geom = matrix.get('geometry') or {}
    if not geom:
        raise ValueError('服务未提供可用的矩阵原点和比例尺，请重新读取服务目录或手动选择')
    return _locate_xy(matrix, *_xy(lon, lat, geom.get('crs')))


def locate_bounds(matrix, west, south, east, north):
    """Return inclusive tile index bounds; no mosaic or network read is performed.

    East and south edges are exclusive. Antimeridian-crossing rectangles must
    be split explicitly. The caller must not describe one tile as the complete
    requested extent when tile_count exceeds one.
    """
    if any(type(v) not in (float,int) or not math.isfinite(v) for v in (west,south,east,north)) or west >= east or south >= north:
        raise ValueError('范围需按西、南、东、北填写，且西小于东、南小于北')
    crs = (matrix.get('geometry') or {}).get('crs')
    if not crs:
        raise ValueError('服务未提供可用的矩阵原点和比例尺，请重新读取服务目录或手动选择')
    left, top = _xy(west, north, crs)
    right, bottom = _xy(east, south, crs)
    nw = _locate_xy(matrix, left, top)
    se = _locate_xy(matrix, right, bottom, exclusive_southeast=True)
    rows, cols = se['row']-nw['row']+1, se['col']-nw['col']+1
    if rows < 1 or cols < 1:
        raise ValueError('范围不在矩阵的可用区域')
    return {'min_row':nw['row'], 'max_row':se['row'], 'min_col':nw['col'], 'max_col':se['col'], 'tile_count':rows*cols}

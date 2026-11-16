"""Closed pixel annotation requests and bounded, deterministic mask encoding.

Coordinates are half-open original-image pixels. Regions are applied in their
listed order. Unmarked/invalid pixels stay unknown; this module makes no model,
accuracy, georeference, Task, permission or persistence claim. The caller must
cold-check the real PixelChangeRun/version/result and any superseded ownership.
"""
from __future__ import annotations

import hashlib
import struct
from uuid import UUID
import zlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

MAX_PIXELS = 16_000_000
MAX_DRAWN_AREA = 64_000_000
MAX_REGIONS = 256
MAX_COORD = 16_000_000
LABELS = {'unchanged': 0, 'change': 1, 'unknown': 255}
_KNOWN_TABLE = bytes(1 if value in (0, 1) else 0 for value in range(256))


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


def _text(value: str) -> str:
    # PostgreSQL text/JSONB cannot persist NUL; invalid Unicode cannot be bound.
    if '\x00' in value:
        raise ValueError('text must not contain NUL')
    try: value.encode('utf-8')
    except UnicodeError: raise ValueError('text must be valid Unicode') from None
    return value


class Rectangle(_Closed):
    x0: StrictInt = Field(ge=0, le=MAX_COORD)
    y0: StrictInt = Field(ge=0, le=MAX_COORD)
    x1: StrictInt = Field(ge=0, le=MAX_COORD)
    y1: StrictInt = Field(ge=0, le=MAX_COORD)

    @model_validator(mode='after')
    def nonempty(self):
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError('rectangle must be nonempty and half-open')
        return self


class Region(_Closed):
    label: Literal['change', 'unchanged', 'unknown']
    rectangle: Rectangle


class SaveAnnotation(_Closed):
    schema: Literal['pixel-annotation-save-v256']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    expected_run_version: StrictInt = Field(ge=1)
    result_hash: StrictStr = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    supersedes_annotation_id: StrictStr | None
    note: StrictStr = Field(min_length=1, max_length=2000)
    regions: list[Region] = Field(max_length=MAX_REGIONS)

    @field_validator('request_id', 'note')
    @classmethod
    def valid_text(cls, value):
        return _text(value)

    @field_validator('note')
    @classmethod
    def meaningful_note(cls, value):
        if not value.strip(): raise ValueError('annotation note must not be blank')
        return value  # Preserve the original text, including surrounding spaces.

    @field_validator('supersedes_annotation_id')
    @classmethod
    def canonical_uuid(cls, value):
        if value is not None:
            try:
                if str(UUID(value)) != value: raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise ValueError('superseded annotation must be a canonical UUID') from None
        return value

    @model_validator(mode='after')
    def bounded_area(self):
        area = sum((r.rectangle.x1 - r.rectangle.x0) * (r.rectangle.y1 - r.rectangle.y0) for r in self.regions)
        if area > MAX_DRAWN_AREA: raise ValueError('cumulative rectangle area exceeds limit')
        return self


def _dimensions(width, height):
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError('mask dimensions must be positive strict integers')
    size = width * height
    if size > MAX_PIXELS: raise ValueError('mask exceeds 16000000 pixels')
    return size


def _regions(width, height, regions):
    if type(regions) is not list or len(regions) > MAX_REGIONS:
        raise ValueError('regions must be a bounded plain list')
    result = []; area = 0
    for region in regions:
        if type(region) is not dict or set(region) != {'label', 'rectangle'}:
            raise ValueError('region fields differ from the closed contract')
        label = region['label']; rectangle = region['rectangle']
        if type(label) is not str or label not in LABELS or type(rectangle) is not dict or set(rectangle) != {'x0', 'y0', 'x1', 'y1'}:
            raise ValueError('region label/rectangle is invalid')
        x0, y0, x1, y1 = (rectangle[key] for key in ('x0', 'y0', 'x1', 'y1'))
        if any(type(value) is not int or not 0 <= value <= MAX_COORD for value in (x0, y0, x1, y1)):
            raise ValueError('coordinates must be bounded strict integers')
        if not (x0 < x1 <= width and y0 < y1 <= height):
            raise ValueError('rectangle is empty or outside the original image')
        area += (x1 - x0) * (y1 - y0)
        if area > MAX_DRAWN_AREA: raise ValueError('cumulative rectangle area exceeds limit')
        result.append((LABELS[label], x0, y0, x1, y1))
    return result, area


def rasterize(width, height, regions, *, valid_mask=None):
    """Return row-major gray8 labels and a 0/1 known-valid mask, with hashes.

    Pass plain region dictionaries (request.model_dump(mode='json')['regions']).
    valid_mask must be immutable bytes, one byte per pixel, exclusively 0/1.
    None explicitly represents an all-valid source. Original validity still
    requires the caller's verified Run artifact; this function grants no scope.
    All geometry/work limits are checked before allocating output buffers.
    """
    size = _dimensions(width, height)
    checked, area = _regions(width, height, regions)
    if valid_mask is not None:
        if type(valid_mask) is not bytes or len(valid_mask) != size or valid_mask.count(b'\x00') + valid_mask.count(b'\x01') != size:
            raise ValueError('source valid mask must contain exactly one immutable 0/1 byte per pixel')
        source_valid = valid_mask.count(b'\x01')
    else:
        source_valid = size
    labels = bytearray(b'\xff') * size
    for value, x0, y0, x1, y1 in checked:
        rw, rh = x1 - x0, y1 - y0
        if x0 == 0 and x1 == width:
            labels[y0 * width:y1 * width] = bytes([value]) * (rw * rh)
        elif rh <= rw:
            fill = bytes([value]) * rw
            for y in range(y0, y1): labels[y * width + x0:y * width + x1] = fill
        else:
            fill = bytes([value]) * rh
            for x in range(x0, x1): labels[y0 * width + x:y1 * width + x:width] = fill
    if source_valid != size:
        for index, present in enumerate(valid_mask):
            if present == 0: labels[index] = 255
    labels = bytes(labels)
    known = labels.translate(_KNOWN_TABLE)
    change = labels.count(b'\x01'); unchanged = labels.count(b'\x00'); unknown = labels.count(b'\xff')
    return {'labels': labels, 'known_valid_mask': known,
        'counts': {'changed': change, 'unchanged': unchanged, 'unknown': unknown,
            'known_valid': change + unchanged, 'total': size},
        'labels_sha256': hashlib.sha256(labels).hexdigest(), 'known_mask_sha256': hashlib.sha256(known).hexdigest()}



def _chunk(kind, content):
    return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content) & 0xffffffff)


def mask_png(width, height, pixels):
    """Encode exact annotation/known-mask bytes as PNG gray8, with no palette.

    Allowed values are 0, 1 and 255. Filter 0 on every row makes readback exact;
    no rescaling, metadata, probabilities or filesystem access is involved.
    """
    size = _dimensions(width, height)
    if type(pixels) is not bytes or len(pixels) != size or set(pixels) - {0, 1, 255}:
        raise ValueError('PNG mask must be immutable exact-size 0/1/255 bytes')
    scanlines = bytearray(size + height)  # First byte of every row stays filter 0.
    if height <= width:
        for y in range(height): scanlines[y * (width + 1) + 1:(y + 1) * (width + 1)] = pixels[y * width:(y + 1) * width]
    else:
        for x in range(width): scanlines[x + 1::width + 1] = pixels[x::width]
    header = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', zlib.compress(scanlines, 6)) + _chunk(b'IEND', b'')

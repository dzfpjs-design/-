"""Bounded, read-only comparison of one PDF text layer and its original OCR.

This is a review aid, not a quality score, correction, confirmation or source
of business evidence. All input identity checks are the existing OCR checks.
"""
from pathlib import Path

from . import material_ocr as ocr

MAX_PAGE_CHARS = 20000


def compare_text(native, recognized):
    if type(native) is not str or type(recognized) is not str:
        raise ValueError('invalid text')
    if not recognized.strip():
        return 'ocr_unavailable'
    if not native.strip():
        return 'native_unavailable'
    if len(native) > MAX_PAGE_CHARS or len(recognized) > MAX_PAGE_CHARS:
        return 'too_large'
    # Deliberately ignore whitespace only. Never normalize numbers, units,
    # punctuation, signs or characters to manufacture an apparent match.
    normalize = lambda value: ''.join(c for c in value if not c.isspace())
    return 'no_difference' if normalize(native) == normalize(recognized) else 'different'


def comparison(db, principal, material, run, unit):
    """Inputs must be produced by the existing scoped OCR unit checks."""
    versions = ocr.checked_versions(db, principal, run, unit)
    raw = versions[0] if versions else None
    descriptor = unit.data['descriptor']
    page = descriptor['page']
    scope = {'material_id':material.id, 'material_version':material.version,
             'source_sha256':material.sha256, 'run_id':run.id, 'generation':run.generation,
             'unit_id':unit.id, 'unit_version':unit.version, 'page':page,
             'raw_revision_id':raw.id if raw else None,
             'raw_revision_hash':raw.content_hash if raw else None}
    result = {'schema':'ocr-native-comparison-v1', 'scope':scope,
              'status':'unsupported', 'native_text':None, 'ocr_text':None}
    if descriptor['kind'] != 'pdf_page' or Path(material.name).suffix.lower() != '.pdf':
        return result
    if type(page) is not int or page < 1:
        ocr.fail('PDF 页来源无法核对')
    if raw is None:
        result['status']='ocr_unavailable'
        return result
    blocks = run.data['native_blocks']
    if type(blocks) is not list:
        ocr.fail('PDF 原生文字来源无法核对')
    matches = []
    for block in blocks:
        if type(block) is not dict or type(block.get('location')) is not dict:
            ocr.fail('PDF 原生文字来源无法核对')
        if block['location'].get('page') == page:
            if block.get('kind') != 'page_text' or type(block.get('text')) is not str:
                ocr.fail('PDF 原生文字来源无法核对')
            matches.append(block['text'])
    if len(matches) > 1:
        ocr.fail('PDF 页原生文字不唯一，未进行对照')
    native = matches[0] if matches else ''
    recognized = raw.data['plain_text']
    status = compare_text(native, recognized)
    result['status'] = status
    if status in {'different','no_difference'}:
        result['native_text'],result['ocr_text'] = native,recognized
    return result

"""Resolve a proposed quotation against one already-authorized material snapshot.

This module only locates text. It does not read a file, establish workspace
access, authenticate a document digest, prove parse completeness, or approve
a business flow. The caller must obtain the material through the current
workspace authorization, verify that the stored digest belongs to the actual
original, and pin the proposal to that digest and material parse version.
The returned binding must be rechecked at save/confirmation time.
"""
import hmac
import re


_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_MAX_BLOCKS = 12000
_MAX_CHARACTERS = 2000000
_MAX_QUOTE = 250


def _pending(reason):
    return {'status': 'needs_review', 'reason': reason}


def _text(value, *, maximum, nonblank=True):
    if type(value) is not str or len(value) > maximum or '\x00' in value:
        return False
    if nonblank and not value.strip():
        return False
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        return False
    return True


def _occurrences(text, phrase):
    """Distinguish zero, one or multiple occurrences, including overlaps."""
    first = text.find(phrase)
    if first < 0:
        return 0
    return 2 if text.find(phrase, first + 1) >= 0 else 1


def resolve_material_citation(*, material, expected_file_hash,
        expected_material_version, expected_engine, source_text, quote):
    """Locate exact source text and a unique quote, or return a review reason.

    ``material`` is an authorized current material view containing its ``id``,
    ``hash``, ``version``, ``engine`` and actual ``blocks``. The expected hash
    identifies the proposal's original document; expected version and engine
    pin the target parser snapshot. All three must come from trusted provenance,
    not merely from an untrusted proposal. Quotes and text are matched exactly,
    without Unicode normalization, whitespace rewriting or fuzzy fallback.
    """
    if (not _text(quote, maximum=_MAX_QUOTE)
            or not _text(source_text, maximum=_MAX_CHARACTERS)
            or quote not in source_text):
        return _pending('invalid_quote')
    if (type(material) is not dict
            or not _text(material.get('id'), maximum=256)
            or type(material.get('version')) is not int or material['version'] < 1
            or not _text(material.get('engine'), maximum=256)
            or type(material.get('hash')) is not str
            or not _SHA256.fullmatch(material['hash'])
            or type(material.get('blocks')) is not list
            or len(material['blocks']) > _MAX_BLOCKS):
        return _pending('invalid_material')
    if (type(expected_file_hash) is not str or not _SHA256.fullmatch(expected_file_hash)
            or type(expected_material_version) is not int or expected_material_version < 1
            or not _text(expected_engine, maximum=256)
            or not hmac.compare_digest(material['hash'], expected_file_hash)
            or material['version'] != expected_material_version
            or material['engine'] != expected_engine):
        return _pending('material_changed')

    seen = set()
    total = 0
    selected = None
    ambiguous = False
    for block in material['blocks']:
        if (type(block) is not dict or not _text(block.get('id'), maximum=256)
                or not _text(block.get('text'), maximum=_MAX_CHARACTERS, nonblank=False)
                or block['id'] in seen):
            return _pending('invalid_material')
        seen.add(block['id'])
        text = block['text']
        total += len(text)
        if total > _MAX_CHARACTERS:
            return _pending('invalid_material')
        count = _occurrences(text, source_text)
        if count:
            if selected is not None or count > 1:
                ambiguous = True
            else:
                selected = block

    if ambiguous:
        return _pending('ambiguous')
    if selected is None:
        return _pending('not_found')
    if _occurrences(selected['text'], quote) != 1:
        return _pending('ambiguous')
    return {'status': 'matched',
            'citation': {'material_id': material['id'], 'block_id': selected['id'], 'quote': quote},
            'binding': {'material_id': material['id'], 'hash': material['hash'],
                        'version': material['version'], 'engine': material['engine']}}

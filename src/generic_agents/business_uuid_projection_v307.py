"""Pure, operation-scoped UUID aliases; never identity or permission grants."""
import copy
import re
from uuid import UUID, uuid5

from .contracts import digest
from .typed_payload_codec_v131 import canonical_json

UUID_TEXT = re.compile(r'(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
LOGICAL_REFS = frozenset({'candidate_ref', 'source_ref', 'fact_ref', 'requirement_ref',
    'capability_ref', 'claim_ref', 'port_ref', 'conflict_ref'})
MAX_BYTES = 24 * 1024 * 1024


class ProjectionError(ValueError):
    pass


def need(value):
    if not value: raise ProjectionError('UUID projection input, aliases or logical references do not match')


def _json(value):
    need(len(canonical_json(value).encode('utf-8', errors='strict')) <= MAX_BYTES)


def _context(namespace_id, salt):
    need(type(namespace_id) is str and UUID_TEXT.fullmatch(namespace_id) is not None)
    _json(salt)
    return UUID(namespace_id), digest(salt)


def project_text(text, *, namespace_id, salt):
    """Replace every UUID spelling with the same exact 36-character alias."""
    need(type(text) is str); _json(text)
    namespace, salted = _context(namespace_id, salt)
    return UUID_TEXT.sub(lambda match: str(uuid5(namespace, salted + ':' + match.group().lower())), text)


def _collect(value, internal, logical, field=None):
    if type(value) is str:
        if field in LOGICAL_REFS: logical.add(value)
        else: internal.update(match.group().lower() for match in UUID_TEXT.finditer(value))
    elif type(value) is dict:
        for key, child in value.items():
            internal.update(match.group().lower() for match in UUID_TEXT.finditer(key))
            _collect(child, internal, logical, key)
    elif type(value) is list:
        singular = field[:-1] if field and field.endswith('_refs') else field
        for child in value: _collect(child, internal, logical, singular)


def _logical_collision(internal, logical):
    # Embedded UUIDs are also refused if preserving their logical spelling
    # would expose an identifier that must be anonymous elsewhere.
    return any(ref.lower() in internal or any(match.group().lower() in internal
        for match in UUID_TEXT.finditer(ref)) for ref in logical)


def derive_aliases(value, *, namespace_id, salt):
    """Inventory keys, values and refs before creating a detached local map."""
    _json(value); namespace, salted = _context(namespace_id, salt)
    internal, logical = set(), set(); _collect(value, internal, logical)
    need(not _logical_collision(internal, logical))
    aliases = {identity: str(uuid5(namespace, salted + ':' + identity)) for identity in sorted(internal)}
    need(len(set(aliases.values())) == len(aliases) and not set(aliases.values()).intersection(internal))
    return aliases


def project(value, *, aliases):
    """Project exact JSON; unknown nonlogical UUIDs and key collisions fail.

    Reserved logical reference values are retained byte for byte. The map is
    local caller state, never metadata automatically inserted into this value.
    """
    _json(value); _json(aliases)
    need(type(aliases) is dict and all(type(key) is type(alias) is str
        and UUID_TEXT.fullmatch(key) is not None and str(UUID(key)) == key
        and UUID_TEXT.fullmatch(alias) is not None and str(UUID(alias)) == alias and UUID(alias).version == 5
        for key, alias in aliases.items()))
    need(len(set(aliases.values())) == len(aliases) and not set(aliases.values()).intersection(aliases))
    internal, logical = set(), set(); _collect(value, internal, logical)
    need(internal <= set(aliases) and not _logical_collision(set(aliases), logical))
    def text(raw):
        return UUID_TEXT.sub(lambda match: aliases[match.group().lower()], raw)
    def visit(item, field=None):
        if type(item) is str: return item if field in LOGICAL_REFS else text(item)
        if type(item) is list:
            singular = field[:-1] if field and field.endswith('_refs') else field
            return [visit(child, singular) for child in item]
        if type(item) is dict:
            result = {}
            for key, child in item.items():
                projected = text(key); need(projected not in result)
                result[projected] = visit(child, key)
            return result
        return copy.deepcopy(item)
    return visit(value)

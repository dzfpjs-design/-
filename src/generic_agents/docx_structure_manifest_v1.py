"""Independent explicit DOCX outline over the unchanged accepted reading view.

No heading is inferred from typography, numbering, style names or model text.
Callers must cold-check this manifest against real bytes before selecting new
context.  Neither its hashes nor section_context grant source/dispatch rights.
"""
import copy
import hashlib
import re
from collections import Counter

from . import native_report_reading as native
from .contracts import digest
from .document_copy_policy import normalize_docx, POLICY
from .document_parsers import NS, ParseProblem, checked_zip
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'docx-structure-manifest-v1'
CONTEXT = 'docx-section-context-v1'
MAX_PARAGRAPHS = 400000  # Same bounded XML ceiling; empty paragraphs are retained.
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_CONTEXT_BYTES = 8 * 1024 * 1024
_MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
_FLAGS = {'source_authority_verified': False, 'semantic_quality_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False}
_TOP = set(('schema source_sha256 normalized_sha256 revision_policy hidden_content_policy '
    'reading_hash styles_sha256 namespaces paragraphs diagnostics coordinate_space original_page_mapping '
    'hierarchy_policy source_authority_verified semantic_quality_verified external_dispatch_allowed '
    'whole_report_verified manifest_hash').split())
_RECORD = set(('ref legacy_ref kind location accepted_xml_path scope outline context_issues text '
    'text_sha256 ancestor_refs parent_ref parent_status').split())
_OUTLINE = {'status', 'outline_value', 'heading_level', 'origin', 'style_id', 'based_on_chain', 'evidence', 'issues'}


def _need(ok):
    if not ok:
        raise ParseProblem('Word结构、旧段落坐标或冻结摘要不一致；未猜测、截断或修改原读取结果')


def _bytes(value):
    return canonical_json(value).encode('utf-8', errors='strict')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8', errors='strict')).hexdigest()


class _Unknown(ValueError):
    pass


def _one(node, tag):
    values = [] if node is None else node.findall(NS + tag)
    if len(values) > 1:
        raise _Unknown('duplicate_' + tag)
    return values[0] if values else None


def _outline(ppr):
    value = _one(ppr, 'outlineLvl')
    if value is None:
        return None
    token = value.get(NS + 'val')
    if type(token) is not str or re.fullmatch(r'[0-9]', token) is None:
        raise _Unknown('invalid_outline_value')
    return int(token)


class _Styles:
    def __init__(self, root):
        self.root, self.by_id, self.defaults, self.diagnostics = root, {}, [], []
        if root is None:
            return
        for index, node in enumerate(root.findall(NS + 'style'), 1):
            identity = node.get(NS + 'styleId')
            if not identity:
                self.diagnostics.append({'code': 'style_id_missing', 'style_id': None})
                continue
            self.by_id.setdefault(identity, []).append((node, '/w:styles[1]/w:style[' + str(index) + ']'))
            if node.get(NS + 'type') == 'paragraph':
                default = node.get(NS + 'default')
                if default in {'1', 'true', 'on'}:
                    self.defaults.append(identity)
                elif default not in {None, '0', 'false', 'off'}:
                    self.defaults.append(None)
        for identity, values in self.by_id.items():
            if len(values) != 1:
                self.diagnostics.append({'code': 'duplicate_style_id', 'style_id': identity})

    def resolve(self, paragraph):
        style_id, chain, evidence = None, [], []
        try:
            ppr = _one(paragraph, 'pPr')
            style = _one(ppr, 'pStyle')
            if style is not None:
                style_id = style.get(NS + 'val')
                if not style_id:
                    raise _Unknown('style_reference_missing')
            direct = _outline(ppr)
            if direct is not None:
                value, origin = direct, 'paragraph_outline'
                evidence.append({'part': 'word/document.xml', 'property': 'pPr/outlineLvl', 'style_id': None})
            else:
                if style_id is None:
                    if len(self.defaults) > 1 or None in self.defaults:
                        raise _Unknown('default_style_conflict')
                    style_id = self.defaults[0] if self.defaults else None
                identity, seen, declared = style_id, set(), []
                while identity is not None:
                    if identity in seen:
                        raise _Unknown('style_inheritance_cycle')
                    seen.add(identity)
                    values = self.by_id.get(identity, [])
                    if len(values) != 1:
                        raise _Unknown('unknown_or_duplicate_style')
                    node, path = values[0]
                    if node.get(NS + 'type') != 'paragraph':
                        raise _Unknown('style_type_not_paragraph')
                    chain.append(identity)
                    level = _outline(_one(node, 'pPr'))
                    evidence.append({'part': 'word/styles.xml', 'property': path, 'style_id': identity})
                    if level is not None:
                        declared.append(level)
                    parent = _one(node, 'basedOn')
                    identity = parent.get(NS + 'val') if parent is not None else None
                    if parent is not None and not identity:
                        raise _Unknown('style_parent_missing')
                if declared:
                    value, origin = declared[0], 'paragraph_style_chain'
                else:
                    defaults = _one(self.root, 'docDefaults')
                    pdefault = _one(defaults, 'pPrDefault')
                    value = _outline(_one(pdefault, 'pPr'))
                    origin = 'doc_defaults' if value is not None else 'not_declared'
                    if value is not None:
                        evidence.append({'part': 'word/styles.xml', 'property': 'docDefaults/pPrDefault/pPr/outlineLvl', 'style_id': None})
            return {'status': 'not_declared' if value is None else 'body' if value == 9 else 'heading',
                'outline_value': value, 'heading_level': value + 1 if value is not None and value < 9 else None,
                'origin': origin, 'style_id': style_id, 'based_on_chain': chain, 'evidence': evidence, 'issues': []}
        except _Unknown as issue:
            return {'status': 'unknown', 'outline_value': None, 'heading_level': None, 'origin': 'unresolved',
                'style_id': style_id, 'based_on_chain': chain, 'evidence': evidence, 'issues': [str(issue)]}


def _namespaces(roots):
    uris = {node.tag[1:].split('}', 1)[0] for root in roots if root is not None
        for node in root.iter() if type(node.tag) is str and node.tag.startswith('{')}
    result = {'w': NS[1:-1], 'mc': _MC[1:-1]}
    for index, uri in enumerate(sorted(uris - set(result.values())), 1):
        result['n' + str(index)] = uri
    return result


def _paragraphs(main, body, styles, namespaces):
    aliases = {uri: prefix for prefix, uri in namespaces.items()}
    def tag_name(tag):
        if tag.startswith('{'):
            uri, local = tag[1:].split('}', 1)
            return aliases[uri] + ':' + local
        return tag
    def children(node, path):
        counts = Counter()
        for child in node:
            counts[child.tag] += 1
            yield child, path + '/' + tag_name(child.tag) + '[' + str(counts[child.tag]) + ']'
    main_path = '/' + tag_name(main.tag) + '[1]'
    body_path = next(path for child, path in children(main, main_path) if child is body)
    records, rows, cells = [], Counter(), Counter()
    table_number = 0
    def walk(node, path, body_index, owner=None, tables=(), textbox_depth=0, branch=None, parent=None, scope=()):
        nonlocal table_number
        tag = node.tag
        if tag == NS + 'tbl':
            table_number += 1
            tables = (*tables, {'table_index': table_number, 'row_index': 0, 'cell_index': 0})
        elif tag == NS + 'tr' and tables:
            table = tables[-1]['table_index']; rows[table] += 1
            tables = (*tables[:-1], {'table_index': table, 'row_index': rows[table], 'cell_index': 0})
        elif tag == NS + 'tc' and tables:
            table, row = tables[-1]['table_index'], tables[-1]['row_index']; cells[table, row] += 1
            tables = (*tables[:-1], {'table_index': table, 'row_index': row, 'cell_index': cells[table, row]})
            scope = (*scope, ('table_cell', path))
        if tag == NS + 'txbxContent':
            textbox_depth += 1; scope = (*scope, ('textbox', path))
        if tag in {_MC + 'Choice', _MC + 'Fallback'}:
            branch = 'choice' if tag == _MC + 'Choice' else 'fallback'
            scope = (*scope, (branch, path))
        if tag == NS + 'p':
            _need(owner is None or textbox_depth > 0)
            location = {'view': POLICY, 'body_index': body_index, 'paragraph_index': len(records) + 1}
            if tables:
                location.update(tables[-1]); location['table_depth'] = len(tables)
            if branch:
                location['compatibility_branch'] = branch
            _need(len(records) < MAX_PARAGRAPHS)
            owner = {'ref': 'ds:p' + str(len(records) + 1), 'pieces': [], 'legacy_ref': None,
                'kind': 'textbox_paragraph' if textbox_depth else 'table_paragraph' if tables else 'paragraph',
                'location': location, 'accepted_xml_path': path,
                'scope': [{'kind': kind, 'path': origin} for kind, origin in scope],
                'outline': styles.resolve(node), 'context_issues': []}
            if any(True for _ in node.iter(_MC + 'AlternateContent')):
                owner['context_issues'].append('mixed_compatibility_content')
            records.append(owner)
        if tag == NS + 't':
            _need(not len(node))
            text = node.text or ''
            if owner is None:
                _need(not text.strip())
            else:
                owner['pieces'].append(text)
        elif tag in {NS + 'tab', NS + 'br', NS + 'cr'} and owner is not None and parent == NS + 'r':
            owner['pieces'].append('\t' if tag == NS + 'tab' else '\n')
        for child, child_path in children(node, path):
            walk(child, child_path, body_index, owner, tables, textbox_depth, branch, tag, scope)
    allowed = {NS + name for name in ('p', 'tbl', 'sdt', 'customXml', 'sectPr')} | {_MC + 'AlternateContent'}
    for index, (child, path) in enumerate(children(body, body_path), 1):
        scope = () if child.tag in allowed else (('unclassified_body_container', path),)
        walk(child, path, index, scope=scope)
    return records


def _hierarchy(records):
    stacks, uncertain = {}, {}
    for record in records:
        scope = tuple((item['kind'], item['path']) for item in record['scope'])
        stack = stacks.setdefault(scope, []); outline = record['outline']
        unknown = outline['status'] == 'unknown' or bool(record['context_issues'])
        if outline['status'] == 'heading' and not unknown:
            level = outline['heading_level']
            while stack and stack[-1]['outline']['heading_level'] >= level:
                stack.pop()
            if level == 1:
                uncertain[scope] = False
        if unknown:
            uncertain[scope] = True
        record['ancestor_refs'] = [item['ref'] for item in stack]
        record['parent_ref'] = stack[-1]['ref'] if stack else None
        record['parent_status'] = 'unknown' if uncertain.get(scope, False) else 'explicit' if stack else 'none_declared_in_scope'
        if outline['status'] == 'heading' and not unknown:
            stack.append(record)


def build_manifest(content: bytes, *, reading: dict):
    """Recompute real old reading and require complete bidirectional agreement."""
    _need(type(content) is bytes and type(reading) is dict)
    actual = native.read_docx(content)
    _need(_bytes(actual) == _bytes(reading))
    normalized, receipt = normalize_docx(content, POLICY)
    _need(receipt['source_sha256'] == actual['source_sha256']
        and receipt['normalized_sha256'] == actual['normalized_sha256']
        and receipt['hidden_content_policy'] == actual['hidden_content_policy'])
    parts, body = native._package(normalized)
    main, root_styles = parts['word/document.xml'], parts.get('word/styles.xml')
    styles = _Styles(root_styles); namespaces = _namespaces((main, root_styles))
    records = _paragraphs(main, body, styles, namespaces)
    cursor = 0
    for record in records:
        record['text'] = ''.join(record.pop('pieces')).strip()
        record['text_sha256'] = _sha(record['text'])
        if not record['text'] and record['outline']['status'] == 'heading':
            record['context_issues'].append('empty_heading_text')
        if record['text']:
            _need(cursor < len(actual['blocks']))
            block = actual['blocks'][cursor]; cursor += 1
            _need(_bytes({'text': record['text'], 'kind': record['kind'], 'location': record['location']})
                == _bytes({key: block[key] for key in ('text', 'kind', 'location')}))
            record['legacy_ref'] = 'w:' + block['id']
    _need(cursor == len(actual['blocks']) and len(records) == actual['coverage']['counts']['paragraphs_total'])
    _hierarchy(records)
    with checked_zip(normalized) as archive:
        style_hash = hashlib.sha256(archive.read('word/styles.xml')).hexdigest() if root_styles is not None else None
    value = {'schema': SCHEMA, 'source_sha256': actual['source_sha256'],
        'normalized_sha256': actual['normalized_sha256'], 'revision_policy': POLICY,
        'hidden_content_policy': actual['hidden_content_policy'], 'reading_hash': digest(actual),
        'styles_sha256': style_hash, 'namespaces': namespaces, 'paragraphs': records,
        'diagnostics': copy.deepcopy(styles.diagnostics), 'coordinate_space': 'accepted_final_word_body',
        'original_page_mapping': 'unavailable', 'hierarchy_policy': 'explicit_outline_same_container_branch_only', **_FLAGS}
    value['manifest_hash'] = digest(value)
    if len(_bytes(value)) > MAX_MANIFEST_BYTES:
        raise ParseProblem('完整Word结构清单超过容量；未截断或签发部分映射')
    return value


def check_manifest(value, content: bytes, *, reading: dict):
    expected = build_manifest(content, reading=reading)
    _need(type(value) is dict and _bytes(value) == _bytes(expected))
    return expected


def section_context(manifest, legacy_refs, *, max_bytes):
    """Only nearest explicit parent candidates; no sampling or authorization.

    Caller must first check_manifest with actual source bytes.  A self-hash here
    is merely an internal-consistency check and cannot replace that cold check.
    Subspan refs are not guessed into block refs; the caller resolves real Map
    references to their original block using its separately frozen catalog.
    """
    _need(type(manifest) is dict and set(manifest) == _TOP and manifest['schema'] == SCHEMA
        and manifest['manifest_hash'] == digest({key: value for key, value in manifest.items() if key != 'manifest_hash'})
        and all(manifest[key] is False for key in _FLAGS))
    _need(type(max_bytes) is int and 1 <= max_bytes <= MAX_CONTEXT_BYTES
        and type(legacy_refs) is list and len(legacy_refs) <= native.MAX_BLOCKS
        and all(type(ref) is str and 1 <= len(ref) <= 128 for ref in legacy_refs))
    records = manifest['paragraphs']; _need(type(records) is list and len(records) <= MAX_PARAGRAPHS)
    index, legacy = {}, {}
    for record in records:
        _need(type(record) is dict and set(record) == _RECORD and type(record['ref']) is str
            and record['ref'] not in index and type(record['text']) is str
            and record['text_sha256'] == _sha(record['text'])
            and type(record['outline']) is dict and set(record['outline']) == _OUTLINE
            and type(record['scope']) is list and all(type(item) is dict and set(item) == {'kind', 'path'}
                and type(item['kind']) is str and type(item['path']) is str for item in record['scope']))
        index[record['ref']] = record
        if record['legacy_ref'] is not None:
            _need(record['legacy_ref'] not in legacy); legacy[record['legacy_ref']] = record
    rebuilt = copy.deepcopy(records); _hierarchy(rebuilt)
    _need(_bytes(rebuilt) == _bytes(records))
    contexts, matches, unknown, seen = [], [], [], set()
    for ref in legacy_refs:
        record = legacy.get(ref)
        if record is None:
            unknown.append({'legacy_ref': ref, 'reason': 'legacy_reference_not_in_manifest'})
            continue
        parent = index.get(record['parent_ref'])
        matches.append({'legacy_ref': ref, 'paragraph_ref': record['ref'],
            'parent_ref': record['parent_ref'], 'parent_status': record['parent_status']})
        if record['parent_status'] != 'explicit' or parent is None:
            unknown.append({'legacy_ref': ref, 'reason': record['parent_status']})
            continue
        _need(parent['outline']['status'] == 'heading' and parent['scope'] == record['scope'])
        if parent['ref'] not in seen:
            contexts.append(copy.deepcopy(parent)); seen.add(parent['ref'])
    result = {'schema': CONTEXT, 'manifest_hash': manifest['manifest_hash'],
        'source_sha256': manifest['source_sha256'], 'normalized_sha256': manifest['normalized_sha256'],
        'requested_legacy_refs': copy.deepcopy(legacy_refs), 'matches': matches,
        'contexts': contexts, 'unknown': unknown, **_FLAGS}
    if len(_bytes(result)) > max_bytes:
        raise ParseProblem('完整父章节上下文超过本次容量；未截断、取前若干项或扩大发送范围')
    return result

"""Inert accepted-view body text for a human-review draft, never full publication.

Original DOCX and existing parser/OCR gates stay unchanged. Every main-body w:t
belongs to its nearest w:p once, including wrapped, table and textbox paragraphs.
Unknown visual meaning and display branches remain explicit coverage gaps.
"""
from collections import Counter
import hashlib

from defusedxml import ElementTree as ET
from .document_copy_policy import normalize_docx, POLICY
from .document_parsers import checked_zip, ParseProblem, NS
from .material_ocr_coverage import coverage as original_coverage
from .report_text_parse_scope import read_docx_cached

MAX_BLOCKS = 12000
MAX_CHARACTERS = 2000000
_MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
_NON_BODY = ('word/header', 'word/footer', 'word/footnotes', 'word/endnotes', 'word/comments')
_HIDDEN = {'vanish', 'webHidden', 'specVanish'}


def _package(content):
    """All XML has already passed the existing normalizer's bounded preflight."""
    with checked_zip(content) as archive:
        parts = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                 if name.startswith('word/') and name.endswith('.xml')}
    main = parts.get('word/document.xml')
    body = main.find(NS + 'body') if main is not None else None
    if body is None:
        raise ParseProblem('Word 缺少可界定的主正文')
    return parts, body


def _gap_types(gaps):
    types = Counter()
    for gap in gaps:
        kind = ('revision' if '修订' in gap else 'textbox' if '文本框' in gap
                else 'non_body' if any(word in gap for word in ('页眉', '页脚', '脚注'))
                else 'image_or_visual')
        types[kind] += 1
    return dict(sorted(types.items()))


def read_docx(content: bytes) -> dict:
    """Read every supported main-body paragraph without executing source content.

    The accepted-final name is a revision-display policy, not approval of source
    changes. Locations address that derived XML view, never original page numbers.
    Limits reject the entire result. No text is sampled or truncated to fit a model.
    """
    return read_docx_cached(content, policy=POLICY, max_blocks=MAX_BLOCKS,
        max_characters=MAX_CHARACTERS, parse=_read_docx_uncached)


def _read_docx_uncached(content: bytes) -> dict:
    """The original parser, with every normalization and coverage gate intact."""
    return _read_docx_version(content, preserve_run_hyphens=False)


def read_docx_visible_v2(content: bytes) -> dict:
    """Build an independent text view; never replace a frozen v1 reading.

    OOXML's non-breaking and discretionary hyphens have different semantics.
    Preserve them as U+2011 and U+00AD respectively instead of guessing that a
    non-breaking hyphen is a numeric minus or that a soft hyphen was displayed.
    """
    return read_docx_cached(content, policy=POLICY + ':run-hyphens-v2',
        max_blocks=MAX_BLOCKS, max_characters=MAX_CHARACTERS,
        parse=_read_docx_visible_v2_uncached)


def _read_docx_visible_v2_uncached(content: bytes) -> dict:
    return _read_docx_version(content, preserve_run_hyphens=True)


def _read_docx_version(content: bytes, *, preserve_run_hyphens: bool) -> dict:
    normalized, receipt = normalize_docx(content, POLICY)
    source_hash = hashlib.sha256(content).hexdigest()
    normalized_hash = hashlib.sha256(normalized).hexdigest()
    if (receipt.get('source_sha256') != source_hash or receipt.get('normalized_sha256') != normalized_hash
            or receipt.get('policy') != POLICY or receipt.get('hidden_content_policy') != 'reveal_retained_text_v1'):
        raise ParseProblem('正文修订策略或来源摘要无法核对')
    original_parts, _ = _package(content)
    parts, body = _package(normalized)
    old_coverage = original_coverage(content, 'source.docx')
    if any(True for _ in body.iter(NS + 'delText')):
        raise ParseProblem('接受修订后仍有无法界定的删除文字，未生成部分正文')
    raw_tags = Counter(node.tag.rsplit('}', 1)[-1] for part in original_parts.values() for node in part.iter())
    tags = Counter(node.tag.rsplit('}', 1)[-1] for node in body.iter())
    paragraphs = []
    owners = {}
    table_number = 0
    rows = Counter()
    cells = Counter()
    text_nodes = 0
    unknown_direct = 0

    def walk(node, body_index, owner=None, tables=(), textbox_depth=0, branch=None, parent=None):
        nonlocal table_number, text_nodes
        tag = node.tag
        if tag == NS + 'tbl':
            table_number += 1
            tables = (*tables, {'table_index': table_number, 'row_index': 0, 'cell_index': 0})
        elif tag == NS + 'tr' and tables:
            table = tables[-1]['table_index']
            rows[table] += 1
            tables = (*tables[:-1], {'table_index': table, 'row_index': rows[table], 'cell_index': 0})
        elif tag == NS + 'tc' and tables:
            table, row = tables[-1]['table_index'], tables[-1]['row_index']
            cells[table, row] += 1
            tables = (*tables[:-1], {'table_index': table, 'row_index': row, 'cell_index': cells[table, row]})
        if tag == NS + 'txbxContent':
            textbox_depth += 1
        if tag in {_MC + 'Choice', _MC + 'Fallback'}:
            branch = 'choice' if tag == _MC + 'Choice' else 'fallback'
        if tag == NS + 'p':
            if owner is not None and not textbox_depth:
                raise ParseProblem('Word 含无法界定的段落嵌套，未生成部分正文')
            location = {'view': POLICY, 'body_index': body_index, 'paragraph_index': len(paragraphs) + 1}
            if tables:
                location.update(tables[-1])
                location['table_depth'] = len(tables)
            if branch:
                location['compatibility_branch'] = branch
            item = {'pieces': [], 'kind': 'textbox_paragraph' if textbox_depth else 'table_paragraph' if tables else 'paragraph',
                    'location': location}
            paragraphs.append(item)
            owner = item
        if tag == NS + 't':
            if len(node):
                raise ParseProblem('Word 文字节点含无法界定的嵌套结构')
            text = node.text or ''
            if owner is None:
                if text.strip():
                    raise ParseProblem('Word 主正文文字不属于可界定段落，未生成部分正文')
            else:
                text_nodes += 1
                owner['pieces'].append(text)
                owners[id(node)] = id(owner)
        elif tag in {NS + 'tab', NS + 'br', NS + 'cr'} and owner is not None and parent == NS + 'r':
            owner['pieces'].append('\t' if tag == NS + 'tab' else '\n')
        elif preserve_run_hyphens and tag in {NS + 'noBreakHyphen', NS + 'softHyphen'}:
            if owner is None or parent != NS + 'r' or len(node) or (node.text or '').strip():
                raise ParseProblem('Word 特殊连字符的位置无法核对，未生成新文字视图')
            owner['pieces'].append('\u2011' if tag == NS + 'noBreakHyphen' else '\u00ad')
        for child in node:
            walk(child, body_index, owner, tables, textbox_depth, branch, tag)

    for body_index, child in enumerate(body, 1):
        if child.tag not in {NS + 'p', NS + 'tbl', NS + 'sdt', NS + 'customXml', NS + 'sectPr', _MC + 'AlternateContent'}:
            unknown_direct += 1
        walk(child, body_index)
    blocks = []
    chars = 0
    empty = 0
    for paragraph in paragraphs:
        text = ''.join(paragraph['pieces']).strip()
        if not text:
            empty += 1
            continue
        chars += len(text)
        if len(blocks) >= MAX_BLOCKS or chars > MAX_CHARACTERS:
            raise ParseProblem('正文段落或文字超过读取上限，未截断或生成部分草案')
        prefix = 'v2:p' if preserve_run_hyphens else 'p'
        blocks.append({'id': prefix + str(len(blocks) + 1), 'text': text, 'kind': paragraph['kind'], 'location': paragraph['location']})
    if not blocks:
        raise ParseProblem('未找到可读取正文，不能生成待核业务草案')
    # Counts are XML occurrences, not claims about unique physical figures/pages.
    non_body = [part for name, part in parts.items() if name.startswith(_NON_BODY)]
    visual = sum(tags[name] for name in ('drawing', 'pict', 'chart', 'oMath', 'oMathPara', 'OLEObject'))
    unread_drawing_text = [node for node in body.iter() if node.tag != NS + 't' and node.tag.rsplit('}', 1)[-1] == 't' and (node.text or '').strip()]
    gaps = []
    for kind, count in (
        ('embedded_visual_semantics', visual),
        ('drawing_text_outside_word_paragraphs', len(unread_drawing_text)),
        ('textbox_layout_or_reading_order', tags['txbxContent']),
        ('table_layout_or_merged_cells', tags['tbl']),
        ('compatibility_display_branch_unresolved', tags['AlternateContent']),
        ('non_body_parts', len(non_body)),
        ('unclassified_body_container', unknown_direct),
        ('font_encoded_symbols', tags['sym']),
    ):
        if count:
            gaps.append({'kind': kind, 'count': count})
    counts = {
        'source_bytes': len(content), 'normalized_bytes': len(normalized),
        'paragraphs_total': len(paragraphs), 'paragraphs_read': len(blocks), 'paragraphs_empty': empty,
        'characters': chars, 'utf8_text_bytes': sum(len(block['text'].encode()) for block in blocks),
        'text_nodes_read': text_nodes, 'text_nodes_unique_owners': len(owners),
        'tables': tags['tbl'], 'table_paragraphs': sum(block['kind'] == 'table_paragraph' for block in blocks),
        'textboxes': tags['txbxContent'], 'textbox_paragraphs': sum(block['kind'] == 'textbox_paragraph' for block in blocks),
        'drawings': tags['drawing'], 'image_references': tags['blip'] + tags['imagedata'],
        'unread_drawing_text_nodes': len(unread_drawing_text),
        'unread_drawing_text_characters': sum(len(node.text or '') for node in unread_drawing_text),
        'chart_references': tags['chart'], 'chart_xml_parts': sum(name.startswith('word/charts/') for name in parts),
        'non_body_parts': len(non_body),
        'non_body_text_characters': sum(len(node.text or '') for part in non_body for node in part.iter(NS + 't')),
        'source_revision_insertions': raw_tags['ins'], 'source_revision_deletions': raw_tags['del'],
        'source_move_from': raw_tags['moveFrom'], 'source_move_to': raw_tags['moveTo'],
        'source_hidden_markers': sum(raw_tags[tag] for tag in _HIDDEN),
        'compatibility_alternate_content': tags['AlternateContent'],
    }
    result = {
        'schema': 'report-body-draft-source-v1', 'source_sha256': source_hash,
        'normalized_sha256': normalized_hash, 'revision_policy': POLICY,
        'hidden_content_policy': receipt['hidden_content_policy'], 'blocks': blocks,
        'coverage': {'scope': 'accepted_view_main_body_text', 'status': 'body_text_only', 'counts': counts,
            'gaps': gaps, 'policy_changes': dict(receipt['changes']),
            'existing_full_publication_status': old_coverage['status'],
            'existing_full_publication_gap_types': _gap_types(old_coverage['gaps']),
            'existing_full_publication_image_units': old_coverage['unit_count']},
        'warnings': [
            '仅基于已读取正文生成待核业务草案；未理解全部图表、图示、版式或非正文内容。',
            '最终修订视图采用 accepted_final_v1，不代表修订已被人工接受为正式终稿。',
            '保留的隐藏文字按 reveal_retained_text_v1 显示，外发前必须明确核对。',
            '动态字段、目录和页码仅保留原缓存文字，未更新或验证。',
            '位置仅指接受修订后的正文视图，不是原件页码；表格合并和文本框阅读顺序仍待人工核对。',
            '兼容分支中的候选文字全部保留，未判定最终显示分支；不得据此当作重复业务。',
            '完整OCR、资料出版、应用和设备执行门未改变；此结果不授予任何权限。',
        ],
        'full_report_confirmed': False, 'can_apply': False,
    }
    if preserve_run_hyphens:
        result['schema'] = 'report-body-draft-source-v2'
        result['text_policy'] = 'preserve-ooxml-run-hyphens-v2'
        result['warnings'].append('新版本保留非断行连字符与可选连字符；其数值含义及实际显示仍需核对，旧视图不变。')
    return result

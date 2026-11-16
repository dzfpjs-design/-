"""Compile saved private review intentions into the two explicit policy ops.

``source`` must come from the server's owner-gated source_for_draft, never a
client-provided source object. This pure compiler checks consistency, not DB
ownership or the complete raw-response hash preimage (absent from the DTO).
Display blocks are used only to select exact Unicode code points. Their policy
trace is discarded: submission must rerun the policy against original frozen
request.chunks, then run the unchanged complete business validator.
"""
import re
from uuid import UUID

from .contracts import digest
from .local_proposal_review_policy_v261 import LocalReviewInvalid, apply_local_review

_PINS = frozenset({'analysis_id', 'source_version', 'raw_hash', 'contract_version'})
_SOURCE = _PINS | {'raw_candidate', 'source_blocks', 'item_hashes', 'citation_hashes'}
_I = r'(?:0|[1-9][0-9]*)'
_DISCARD = re.compile(r'/(?:businesses|objects|relationships)/' + _I)
_CITATION = re.compile(r'/(?:businesses/' + _I + r'(?:/steps/' + _I
    + r')?|objects/' + _I + r'(?:/fields/' + _I + r')?|relationships/' + _I
    + r')/citations/' + _I)
_POSITION = re.compile(r'[1-9][0-9]*')
_SHA = re.compile(r'[0-9a-f]{64}')


def _need(ok, message):
    if not ok:
        raise LocalReviewInvalid(message)


def _text(value):
    return (type(value) is str and '\x00' not in value
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def _pins(value):
    _need(type(value) is dict and set(value) == _PINS, '私人稿来源包装不符')
    identity = value['analysis_id']
    try:
        valid = type(identity) is str and str(UUID(identity)) == identity
    except ValueError:
        valid = False
    _need(valid and type(value['source_version']) is int and value['source_version'] >= 1
        and type(value['raw_hash']) is str and _SHA.fullmatch(value['raw_hash']) is not None
        and type(value['contract_version']) is int and value['contract_version'] in {4, 5},
        '私人稿来源标识或版本不符')


def _hash(value):
    try:
        return digest(value)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise LocalReviewInvalid('原候选无法形成可核对摘要') from None


def _maps(raw, version):
    """Recompute the complete display index using original array positions."""
    _need(type(raw) is dict, '原机器候选不是对象')
    items = {}; citations = {}

    def collect(item, path):
        _need(type(item) is dict, '原候选条目类型不符')
        references = item.get('citations', [])
        _need(type(references) is list, '原引用容器类型不符')
        for index, citation in enumerate(references):
            _need(type(citation) is dict, '原引用类型不符')
            citations[path + '/citations/' + str(index)] = _hash(citation)

    for collection in ('businesses', 'objects', 'relationships'):
        if collection == 'relationships' and version == 4:
            continue
        rows = raw.get(collection, [])
        _need(type(rows) is list, '原候选列表类型不符')
        for index, item in enumerate(rows):
            path = '/' + collection + '/' + str(index)
            _need(type(item) is dict, '原候选条目类型不符')
            items[path] = _hash(item); collect(item, path)
            nested = 'steps' if collection == 'businesses' else 'fields' if collection == 'objects' else None
            if nested:
                children = item.get(nested, [])
                _need(type(children) is list, '原步骤或字段列表类型不符')
                for child_index, child in enumerate(children):
                    collect(child, path + '/' + nested + '/' + str(child_index))
    return items, citations


def _position(value, text):
    _need(type(value) is str and _POSITION.fullmatch(value) is not None,
        '引用起止位置必须为ASCII正整数，按Unicode码点一起算')
    # Reject absurd decimal input before int conversion; never round/coerce it.
    _need(len(value) <= len(str(len(text))), '引用位置超过原文长度')
    result = int(value)
    _need(1 <= result <= len(text), '引用位置超过原文长度')
    return result


def compile_saved_review(value, source):
    """Return only exact discard_item / replace_citation operations.

    Start/end are 1-based inclusive Unicode code-point indices, not UTF-16
    offsets, bytes, or grapheme clusters. Unknown agents and all unrelated raw
    fields remain for the original downstream validator; none are repaired.
    """
    _need(type(value) is dict and set(value) == {'source', 'edits'}, '私人稿仅可包含来源和编辑意图')
    _pins(value['source'])
    _need(type(source) is dict and set(source) == _SOURCE, '服务端来源DTO字段不符')
    pins = {key: source[key] for key in _PINS}; _pins(pins)
    _need(value['source'] == pins, '私人稿与原机器来源已经不同，不能换绑提交')
    edits = value['edits']
    _need(type(edits) is list and 1 <= len(edits) <= 100, '提交需明确1至100项完整编辑意图')
    raw = source['raw_candidate']; items, citations = _maps(raw, pins['contract_version'])
    for provided, calculated in ((source['item_hashes'], items), (source['citation_hashes'], citations)):
        _need(type(provided) is dict and all(type(key) is str and type(item) is str
            and _SHA.fullmatch(item) is not None for key, item in provided.items())
            and provided == calculated, '显示摘要表与原始候选指针不符，未信任摘要表')
    _need(type(source['source_blocks']) is list, '授权原文块列表不符')
    blocks = {}
    for block in source['source_blocks']:
        _need(type(block) is dict and all(_text(block.get(key)) for key in ('material_id', 'block_id', 'text'))
            and bool(block['material_id']) and bool(block['block_id']), '授权原文块结构不符')
        key = (block['material_id'], block['block_id'])
        _need(key not in blocks, '授权原文块标识重复，不能任取其中一份')
        blocks[key] = block

    operations = []
    for edit in edits:
        _need(type(edit) is dict and type(edit.get('op')) is str
            and edit['op'] in {'discard_item', 'replace_citation'}, '不支持此编辑意图')
        kind = edit['op']; keys = {'op', 'path', 'reason'}
        if kind == 'replace_citation':
            keys |= {'material_id', 'block_id', 'start_text', 'end_text'}
        _need(set(edit) == keys and _text(edit['reason']), '编辑意图缺字段或含未授权字段')
        path = edit['path']; pattern = _DISCARD if kind == 'discard_item' else _CITATION
        _need(type(path) is str and pattern.fullmatch(path) is not None
            and (pins['contract_version'] == 5 or not path.startswith('/relationships/')),
            '编辑意图不在本版本允许的精确路径内')
        computed = items if kind == 'discard_item' else citations
        _need(path in computed, '原机器候选中不存在此条目或引用')
        operation = {'op': kind, 'path': path, 'expected_hash': computed[path], 'reason': edit['reason']}
        if kind == 'replace_citation':
            _need(_text(edit['material_id']) and bool(edit['material_id'])
                and _text(edit['block_id']) and bool(edit['block_id']), '请完成原文块选择后提交')
            block = blocks.get((edit['material_id'], edit['block_id']))
            _need(block is not None, '所选原文块不在本次冻结来源中')
            text = block['text']; start = _position(edit['start_text'], text); end = _position(edit['end_text'], text)
            _need(start <= end and end - start + 1 <= 250, '引用位置逆序或超过250码点')
            quote = text[start - 1:end]
            _need(bool(quote.strip()), '引用不能只包含空白字符')
            operation['replacement'] = {'material_id': edit['material_id'], 'block_id': edit['block_id'], 'quote': quote}
        operations.append(operation)
    # Only structure, overlap, original hashes, exact source strings and reasons
    # are checked here. Discard the derived copy/trace; never fake final validity.
    apply_local_review(raw, operations, source['source_blocks'], contract_version=pins['contract_version'])
    return operations

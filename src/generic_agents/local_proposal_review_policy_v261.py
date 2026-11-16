"""Two explicit, local edits to a copied V4/V5 machine proposal.

The caller must supply the actual original parsed answer and authorized source
blocks, persist the original wire separately, and run the unchanged complete
proposal validator on the returned copy. This policy grants no role, capability,
relationship, or execution authority and makes no claim of proposal validity.
Every pointer and expected hash addresses the ORIGINAL answer, never a partly
edited result. Trace entries preserve the user's operation order.
"""
import copy
import re

from .contracts import digest

POLICY = 'local-proposal-review-v261'
_INDEX = r'(?:0|[1-9][0-9]*)'
_DISCARD = re.compile(r'/(?:businesses|objects|relationships)/' + _INDEX)
_CITATION = re.compile(r'/(?:businesses/' + _INDEX + r'(?:/steps/' + _INDEX
    + r')?|objects/' + _INDEX + r'(?:/fields/' + _INDEX + r')?|relationships/'
    + _INDEX + r')/citations/' + _INDEX)
_SHA = re.compile(r'[0-9a-f]{64}')
_CITATION_KEYS = frozenset({'material_id', 'block_id', 'quote'})


class LocalReviewInvalid(ValueError):
    """The requested local edits cannot be proved against the original data."""


def _need(ok, message):
    if not ok:
        raise LocalReviewInvalid(message)


def _text(value):
    return (type(value) is str and '\x00' not in value
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def _hash(value):
    try:
        return digest(value)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise LocalReviewInvalid('原条目或原文块无法形成可核对摘要') from None


def _index(token, values):
    # A huge decimal pointer is out of range without constructing a huge int.
    _need(type(values) is list and bool(values), '原指针所选数组不存在或为空')
    _need(len(token) <= len(str(len(values) - 1)), '原指针索引越界')
    index = int(token)
    _need(index < len(values), '原指针索引越界')
    return index


def _resolve(value, tokens):
    selected = value
    for token in tokens:
        if token.isdecimal():
            selected = selected[_index(token, selected)]
        elif type(selected) is dict:
            _need(token in selected, '原指针所选字段不存在')
            selected = selected[token]
        else:
            raise LocalReviewInvalid('原指针容器类型不符')
    return selected


def _citation(value, *, replacement=False):
    _need(type(value) is dict and set(value) == _CITATION_KEYS
        and all(_text(value[key]) for key in _CITATION_KEYS), '引用必须为原有的三个文本字段')
    if replacement:
        _need(bool(value['material_id']) and bool(value['block_id'])
            and 1 <= len(value['quote']) <= 250 and bool(value['quote'].strip()),
            '新引用需明确原文块和1至250字符的非空白原串')


def _replace(value, tokens, replacement):
    """Copy each changed ancestor so Python aliases cannot widen the edit."""
    if not tokens:
        return copy.deepcopy(replacement)
    result = value.copy()
    key = _index(tokens[0], value) if type(value) is list else tokens[0]
    result[key] = _replace(value[key], tokens[1:], replacement)
    return result


def apply_local_review(value, operations, blocks, *, contract_version):
    """Return ``(deep-copied corrected proposal, auditable trace)`` atomically.

    Only item discard and existing-citation replacement are possible. Block
    metadata may include location information; its complete server-provided
    mapping is hashed, without stripping or rewriting its text. An old citation
    needs exactly the three textual citation keys; replacing it may correct an
    empty or overlong old quote, leaving final validation to the caller.
    """
    _need(type(contract_version) is int and contract_version in {4, 5}, '本政策仅适用于明确的V4或V5建议')
    _need(type(value) is dict, '原建议必须是对象')
    _need(type(operations) is list and 1 <= len(operations) <= 100, '本次必须明确1至100项本地修改')
    _need(type(blocks) is list, '原文块必须为服务端已授权列表')
    source = {}
    for block in blocks:
        _need(type(block) is dict and all(key in block and _text(block[key])
            for key in ('material_id', 'block_id', 'text'))
            and bool(block['material_id']) and bool(block['block_id']), '原文块结构不符')
        key = (block['material_id'], block['block_id'])
        _need(key not in source, '原文块标识重复，不能选择其中一份作为依据')
        source[key] = block

    validated = []
    paths = []
    for operation in operations:
        _need(type(operation) is dict, '每项修改必须为封闭对象')
        kind = operation.get('op')
        _need(type(kind) is str and kind in {'discard_item', 'replace_citation'}, '不支持此本地修改操作')
        keys = {'op', 'path', 'expected_hash', 'reason'}
        if kind == 'replace_citation':
            keys.add('replacement')
        _need(set(operation) == keys, '本地修改字段缺失或包含未授权字段')
        path = operation['path']; expected = operation['expected_hash']; reason = operation['reason']
        _need(type(path) is str and (_DISCARD if kind == 'discard_item' else _CITATION).fullmatch(path)
            is not None, '修改指针不在允许的精确路径内')
        tokens = tuple(path[1:].split('/'))
        _need(contract_version == 5 or tokens[0] != 'relationships', 'V4不支持关系修改')
        _need(type(expected) is str and _SHA.fullmatch(expected) is not None, '原条目摘要格式不符')
        _need(_text(reason) and 6 <= len(reason) <= 1000 and bool(reason.strip()), '修改理由需为6至1000字符且不能全为空白')
        for previous in paths:
            _need(tokens[:len(previous)] != previous and previous[:len(tokens)] != tokens,
                '修改指针重复或存在父子交叠')
        paths.append(tokens)
        original = _resolve(value, tokens)
        _need(type(original) is dict, '原条目类型不符')
        original_hash = _hash(original)
        _need(original_hash == expected, '原条目摘要已变化，未应用任何本地修改')
        replacement_hash = source_block_hash = None
        replacement = None
        if kind == 'replace_citation':
            _citation(original)
            replacement = operation['replacement']; _citation(replacement, replacement=True)
            block = source.get((replacement['material_id'], replacement['block_id']))
            _need(block is not None and replacement['quote'] in block['text'], '新引用不是唯一原文块中的连续原串')
            replacement_hash = _hash(replacement); source_block_hash = _hash(block)
        validated.append({'op': kind, 'path': path, 'tokens': tokens,
            'original_hash': original_hash, 'replacement': replacement,
            'replacement_hash': replacement_hash, 'source_block_hash': source_block_hash,
            'reason': reason})

    corrected = copy.deepcopy(value)
    for operation in validated:
        if operation['op'] == 'replace_citation':
            corrected = _replace(corrected, operation['tokens'], operation['replacement'])
    discarded = {}
    for operation in validated:
        if operation['op'] == 'discard_item':
            discarded.setdefault(operation['tokens'][0], []).append(int(operation['tokens'][1]))
    for collection, indices in discarded.items():
        items = list(corrected[collection])
        for index in sorted(indices, reverse=True):
            del items[index]
        corrected[collection] = items
    trace = [{'policy': POLICY, **{key: operation[key] for key in
        ('op', 'path', 'original_hash', 'replacement_hash', 'source_block_hash', 'reason')}}
        for operation in validated]
    return corrected, trace

"""Read-only coverage audit for native DOCX parsing; never rewrites old blocks.

Candidate text is returned only by explicit request and is never logged or saved.
Locations use a separate namespace and are not assertions of business truth.
"""
from collections import Counter
import io
import zlib
from zipfile import BadZipFile

from defusedxml import ElementTree as ET

from .document_parsers import ParseProblem, checked_zip

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
_KNOWN_NAMESPACES = {
    'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
}
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_ELEMENTS = 500000
MAX_DEPTH = 192
MAX_NAMESPACES = 256
MAX_CANDIDATES = 12000
MAX_CANDIDATE_CHARS = 2000000
MAX_EXTRACTION_VISITS = 2000000


def audit_docx_structure(content: bytes, *, include_candidates: bool = False) -> dict:
    """Audit coverage of one DOCX without modifying source or v1 parse results.

    Candidate paths index the original XML tree (one-based); discarded
    AlternateContent branches are never mixed with the selected branch. When
    a choice's namespace cannot be proven, its fallback is selected, if any.
    All extracted text requires review, especially in documents with revisions.
    """
    if type(content) is not bytes or type(include_candidates) is not bool:
        raise ValueError('invalid audit input')
    with checked_zip(content) as archive:
        try:
            entry = archive.getinfo('word/document.xml')
            if entry.file_size > MAX_XML_BYTES:
                raise ParseProblem('DOCX 正文超过结构核查上限')
            xml = archive.read(entry)
        except KeyError:
            raise ParseProblem('DOCX缺少正文') from None
        except (BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError):
            raise ParseProblem('DOCX正文压缩条目损坏或不受支持') from None
        names = set(archive.namelist())
    try:
        parser = ET.iterparse(io.BytesIO(xml), events=('start', 'end', 'start-ns'))
        namespace_scope, pending_namespaces, namespace_stack, choice_supported = {}, [], [], {}
        depth = count = 0
        for event, value in parser:
            if event == 'start-ns':
                prefix, uri = value
                pending_namespaces.append((prefix, uri))
            elif event == 'start':
                depth += 1; count += 1
                if depth > MAX_DEPTH or count > MAX_ELEMENTS:
                    raise ParseProblem('DOCX结构过深或节点过多')
                undo = []
                for prefix, uri in pending_namespaces:
                    undo.append((prefix, prefix in namespace_scope, namespace_scope.get(prefix)))
                    namespace_scope[prefix] = uri
                pending_namespaces.clear()
                if len(namespace_scope) > MAX_NAMESPACES:
                    raise ParseProblem('DOCX命名空间超过核查上限')
                namespace_stack.append(undo)
                if value.tag == MC + 'Choice':
                    required = (value.get('Requires') or '').split()
                    choice_supported[id(value)] = bool(required) and all(
                        p in _KNOWN_NAMESPACES and namespace_scope.get(p) == _KNOWN_NAMESPACES[p]
                        for p in required)
            else:
                for prefix, existed, old in reversed(namespace_stack.pop()):
                    if existed: namespace_scope[prefix] = old
                    else: namespace_scope.pop(prefix, None)
                depth -= 1
        root = parser.root
    except ParseProblem:
        raise
    except Exception:
        raise ParseProblem('DOCX正文损坏或含不安全XML') from None
    bodies = root.findall(W + 'body') if root.tag == W + 'document' else []
    if len(bodies) != 1:
        raise ParseProblem('DOCX正文结构异常')
    body = bodies[0]

    def selected(node):
        for child in node:
            if child.tag == MC + 'Choice' and choice_supported.get(id(child), False):
                return child
        return next((child for child in node if child.tag == MC + 'Fallback'), None)

    def visible_children(node):
        if node.tag in (W + 'del', W + 'moveFrom'):
            return ()
        if node.tag == MC + 'AlternateContent':
            branch = selected(node)
            return (branch,) if branch is not None else ()
        return node

    extraction_visits = 0
    def visible_text(node):
        nonlocal extraction_visits
        result = []
        todo = [node]
        while todo:
            extraction_visits += 1
            if extraction_visits > MAX_EXTRACTION_VISITS:
                raise ParseProblem('DOCX候选遍历超过核查上限')
            item = todo.pop()
            # A nested box has its own independently located candidate.
            if item is not node and item.tag == W + 'txbxContent':
                continue
            if item.tag == W + 't':
                result.append(item.text or '')
            elif item.tag == W + 'noBreakHyphen':
                result.append('\u2011')
            elif item.tag == W + 'softHyphen':
                result.append('\u00ad')
            elif item.tag == W + 'tab':
                result.append('\t')
            elif item.tag in (W + 'br', W + 'cr'):
                result.append('\n')
            else:
                todo.extend(reversed(tuple(visible_children(item))))
        return ''.join(result).strip()

    def is_toc(node):
        properties = node.find(W + 'sdtPr')
        part = properties.find(W + 'docPartObj') if properties is not None else None
        gallery = part.find(W + 'docPartGallery') if part is not None else None
        return gallery is not None and (gallery.get(W + 'val') or '').casefold() == 'table of contents'

    all_counts = Counter()
    for node in body.iter():
        if node.tag in (W + 'sdt', W + 'txbxContent', W + 'tbl', W + 'drawing', W + 'pict',
                        W + 'ins', W + 'del', W + 'moveFrom', W + 'moveTo', MC + 'AlternateContent'):
            all_counts[node.tag] += 1
    top_toc = sum(child.tag == W + 'sdt' and is_toc(child) for child in body)
    top_other = sum(child.tag == W + 'sdt' and not is_toc(child) for child in body)
    skipped_top_level_text = sum(
        any((t.text or '').strip() for t in child.iter(W + 't'))
        for child in body if child.tag not in (W + 'p', W + 'tbl', W + 'sdt'))
    nested_tables = 0
    todo = [(body, False)]
    while todo:
        node, in_table = todo.pop()
        if node.tag == W + 'tbl':
            nested_tables += int(in_table)
            in_table = True
        todo.extend((child, in_table) for child in node)
    alt_with_no_supported_branch = sum(selected(x) is None for x in body.iter(MC + 'AlternateContent'))
    counts = {'top_level_directories': top_toc, 'top_level_other_content_controls': top_other,
              'other_skipped_top_level_with_text': skipped_top_level_text,
              'content_controls': all_counts[W+'sdt'], 'textboxes_all_branches': all_counts[W+'txbxContent'],
              'alternate_content': all_counts[MC+'AlternateContent'], 'alternate_without_supported_branch': alt_with_no_supported_branch,
              'tables': all_counts[W+'tbl'], 'nested_tables': nested_tables,
              'drawings': all_counts[W+'drawing'], 'legacy_pictures': all_counts[W+'pict'],
              'insertions': all_counts[W+'ins'], 'deletions': all_counts[W+'del'],
              'moved_from': all_counts[W+'moveFrom'], 'moved_to': all_counts[W+'moveTo'],
              'footnotes_present': 'word/footnotes.xml' in names, 'endnotes_present': 'word/endnotes.xml' in names}
    issues = []
    if top_toc: issues.append({'code':'directory_not_in_native_blocks','message':'正文解析未收录顶层目录；目录仅供导航，需与正文核对。'})
    if top_other: issues.append({'code':'content_control_not_in_native_blocks','message':'顶层内容控件未纳入原正文块，请核对是否包含正文。'})
    if skipped_top_level_text: issues.append({'code':'other_top_level_text_not_in_native_blocks','message':'其他顶层结构含文字，未纳入原正文块，请核对。'})
    if nested_tables: issues.append({'code':'nested_table_requires_review','message':'存在嵌套表格，原行文本可能混入嵌套内容，请逐层核对。'})
    if all_counts[MC+'AlternateContent']: issues.append({'code':'alternate_content_may_duplicate','message':'兼容分支可能使原正文块重复或混入文本框内容；候选仅选择一个可识别分支。'})
    if all_counts[W+'ins'] or all_counts[W+'del'] or all_counts[W+'moveFrom'] or all_counts[W+'moveTo']:
        issues.append({'code':'tracked_changes_require_review','message':'文档包含修订，候选内容和修订视图需要核对。'})
    if alt_with_no_supported_branch: issues.append({'code':'alternate_branch_unavailable','message':'部分兼容内容没有可识别的分支，不能认定已完整提取。'})
    if all_counts[W+'drawing'] or all_counts[W+'pict']: issues.append({'code':'visual_content_requires_review','message':'图片和图形内容需另行核对，不能仅凭文字判断完整性。'})
    if counts['footnotes_present'] or counts['endnotes_present']:
        issues.append({'code':'notes_parts_require_review','message':'文件含脚注或尾注部件，需核对其中是否有文字；当前候选不包含这些部件。'})
    result = {'schema':'docx-structure-audit-v1', 'status':'review_required' if issues else 'no_detected_issues',
              'counts':counts, 'issues':issues}
    if not include_candidates:
        return result

    candidates = []
    candidate_chars = 0
    def add(kind, node, body_index, path):
        nonlocal candidate_chars
        value = visible_text(node)
        if not value: return
        candidate_chars += len(value)
        if len(candidates) >= MAX_CANDIDATES or candidate_chars > MAX_CANDIDATE_CHARS:
            raise ParseProblem('DOCX候选内容超过核查上限')
        candidates.append({'kind':kind, 'text':value, 'review_required':True,
                           'location':{'scheme':'docx-audit-v1','part':'word/document.xml',
                                       'body_index':body_index,'child_path':list(path)}})
    for index, top in enumerate(body, 1):
        directory = top.tag == W+'sdt' and is_toc(top)
        todo = [(top, (), False)]
        while todo:
            node, path, in_textbox = todo.pop()
            if node.tag == W+'txbxContent':
                add('textbox', node, index, path)
                in_textbox = True
            elif directory and node.tag == W+'p' and not in_textbox:
                add('directory_entry', node, index, path)
            allowed = {id(child) for child in visible_children(node)}
            children = [(c, path+(i,), in_textbox) for i,c in enumerate(node,1) if id(c) in allowed]
            todo.extend(reversed(children))
    result['candidates'] = candidates
    return result

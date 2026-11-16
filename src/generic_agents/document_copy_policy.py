"""Bounded, inert DOCX copy with an explicit accepted-final revision policy.

Only an ephemeral copy is normalized. The uploaded source is never overwritten.
Unsupported revision structures stop conversion; layout still requires page review.
"""
from __future__ import annotations
import hashlib
import io
import posixpath
import re
import zipfile
from collections import Counter
from pathlib import PurePosixPath
from urllib.parse import unquote
from xml.dom import Node
from defusedxml import minidom, sax
from xml.sax.handler import ContentHandler
from .document_parsers import checked_zip, ParseProblem

POLICY = 'accepted_final_v1'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_MAX_XML_BYTES = 20 * 1024 * 1024
_MAX_NODES = 400000
_CHANGES = {'pPrChange','rPrChange','sectPrChange','tblPrChange','tblPrExChange','tblGridChange','trPrChange','tcPrChange','numberingChange'}
_DANGEROUS = {'altChunk','object','oleObject','OLEObject','control','subDoc','contentPart','mailMerge'}
_RANGE_REVISIONS = {'moveFromRangeStart','moveFromRangeEnd','moveToRangeStart','moveToRangeEnd','customXmlInsRangeStart','customXmlInsRangeEnd','customXmlDelRangeStart','customXmlDelRangeEnd','customXmlMoveFromRangeStart','customXmlMoveFromRangeEnd','customXmlMoveToRangeStart','customXmlMoveToRangeEnd'}


def _elements(node):
    return list(node.getElementsByTagName('*'))


def _remove(node):
    if node.parentNode is not None:
        node.parentNode.removeChild(node)


def _unwrap(node):
    parent = node.parentNode
    if parent is None: return
    for child in list(node.childNodes): parent.insertBefore(child, node)
    parent.removeChild(node)


def _tag(node, local):
    return node.nodeType == Node.ELEMENT_NODE and node.namespaceURI == W and node.localName == local


class _ShapeBound(ContentHandler):
    def __init__(self, consumed):
        self.nodes = consumed; self.depth = 0
    def startElement(self, name, attrs):
        self.nodes += 1; self.depth += 1
        if self.nodes > _MAX_NODES or self.depth > 96 or len(attrs) > 256:
            raise ParseProblem('Word XML 节点、嵌套或属性超过转换上限')
    def endElement(self, name):
        self.depth -= 1


def normalize_docx(content: bytes, revision_policy: str) -> tuple[bytes, dict]:
    if revision_policy != POLICY: raise ParseProblem('请选择并核对受支持的最终修订视图')
    if not isinstance(content, bytes) or not 0 < len(content) <= 20 * 1024 * 1024:
        raise ParseProblem('Word 大小超过本地转换上限')
    counters = Counter(); parts = {}; xmls = {}; xml_bytes = nodes = 0
    with checked_zip(content) as archive:
        for item in archive.infolist():
            name = item.filename
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or '\x00' in name or ':' in name:
                raise ParseProblem('Word 内部文件路径异常')
            if item.is_dir(): continue
            lowered = name.lower()
            if any(x in lowered for x in ('vbaproject','activex/','embeddings/','word/fonts/')):
                raise ParseProblem('Word 包含宏、嵌入对象、控件或嵌入字体，请先另存不含这些内容的副本')
            raw = archive.read(item)
            parts[name] = raw
            if lowered.endswith(('.xml','.rels')):
                xml_bytes += len(raw)
                if xml_bytes > _MAX_XML_BYTES: raise ParseProblem('Word XML 超过安全转换上限')
                try:
                    shape = _ShapeBound(nodes)
                    sax.parseString(raw, shape, forbid_dtd=True, forbid_entities=True, forbid_external=True)
                    nodes = shape.nodes
                    document = minidom.parseString(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
                except Exception as exc: raise ParseProblem('Word XML 损坏或包含外部定义') from exc
                xmls[name] = document
        if 'word/document.xml' not in xmls or '[Content_Types].xml' not in xmls:
            raise ParseProblem('Word 缺少正文或类型声明')
        main = xmls['word/document.xml'].documentElement
        if main.namespaceURI != W or main.localName != 'document':
            raise ParseProblem('只支持标准过渡版 DOCX；Strict 或其他 Word 命名空间需先另存')
        # Fail closed for XML disguised by extension and XML media. All XML
        # renderer inputs must have passed both streaming and DOM preflight.
        defaults = {}; overrides = {}
        for node in _elements(xmls['[Content_Types].xml']):
            typ = node.getAttribute('ContentType').lower()
            if node.localName == 'Default':
                key = node.getAttribute('Extension').lower()
                if not key or key in defaults: raise ParseProblem('Word 默认内容类型重复或异常')
                defaults[key] = typ
            elif node.localName == 'Override':
                key = node.getAttribute('PartName')
                if not key.startswith('/') or key in overrides: raise ParseProblem('Word 内容类型覆盖重复或异常')
                overrides[key] = typ
        for part in parts:
            typ = overrides.get('/'+part, defaults.get(PurePosixPath(part).suffix[1:].lower(), ''))
            if 'svg' in typ or part.lower().endswith('.svg'):
                raise ParseProblem('Word 含 SVG 图片，请先在 Word 中转为嵌入 PNG 后另存')
            if typ.endswith(('+xml','/xml')) and part not in xmls:
                raise ParseProblem('Word 含非标准扩展名 XML 内容，请先另存标准 DOCX')
        root_rels = xmls.get('_rels/.rels')
        root_office = [n for n in _elements(root_rels) if n.getAttribute('Type').rsplit('/',1)[-1] == 'officeDocument'] if root_rels else []
        if len(root_office) != 1 or root_office[0].getAttribute('Target') != 'word/document.xml' or root_office[0].getAttribute('TargetMode'):
            raise ParseProblem('Word 主文档关系缺失、不唯一或不指向固定正文')
        main_types = [n.getAttribute('ContentType') for n in _elements(xmls['[Content_Types].xml']) if n.getAttribute('PartName') == '/word/document.xml']
        if main_types != ['application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml']:
            raise ParseProblem('Word 主文档类型不受支持或包含宏')
        for name, document in xmls.items():
            for node in _elements(document):
                local = node.localName or node.tagName
                if node.namespaceURI and node.namespaceURI.startswith('http://purl.oclc.org/ooxml/wordprocessingml'):
                    raise ParseProblem('Strict Word 内容尚不支持安全修订转换，请先另存普通 DOCX')
                content_type = node.getAttribute('ContentType').lower()
                if any(x in content_type for x in ('macroenabled','vbaproject','activex','oleobject')):
                    raise ParseProblem('Word 声明了宏、嵌入对象或控件内容')
                if node.namespaceURI == W and local in {'comment','commentReference','commentRangeStart','commentRangeEnd'}:
                    raise ParseProblem('Word 含批注正文；当前副本不能完整呈现批注，请先导出批注供单独核对')
                if node.getAttribute('hidden') in {'1','true','on'} or re.search(r'(?:visibility\s*:\s*hidden|display\s*:\s*none)',node.getAttribute('style'),re.I):
                    raise ParseProblem('Word 含未支持的隐藏图形或对象，请先显示后另存')
                if 'conflict' in local.lower() or local in _DANGEROUS:
                    raise ParseProblem('Word 含外部或嵌入内容，无法保证本地完整呈现')
                for index in range(node.attributes.length):
                    attr = node.attributes.item(index)
                    if attr.localName in {'src','href'} or (attr.namespaceURI == R and attr.localName == 'link') or attr.name == 'xml:base':
                        raise ParseProblem('Word 含未受支持的直接外部引用，请先解除链接')
            if name.lower().endswith('.rels'):
                if document.documentElement.namespaceURI != REL:
                    raise ParseProblem('Word 关系类型声明不受支持')
                relation_ids = set()
                parent = posixpath.dirname(posixpath.dirname(name))
                for relation in list(document.documentElement.childNodes):
                    if relation.nodeType != Node.ELEMENT_NODE: continue
                    rid = relation.getAttribute('Id')
                    if not rid or rid in relation_ids: raise ParseProblem('Word 关系标识缺失或重复')
                    relation_ids.add(rid)
                    typ = relation.getAttribute('Type').rsplit('/',1)[-1]
                    if typ.lower() in {'vbaproject','oleobject','package','control','attachedtemplate'} or 'activex' in typ.lower():
                        raise ParseProblem('Word 含宏、嵌入对象或活动模板关系')
                    if typ == 'hyperlink':
                        _remove(relation); counters['disabled_hyperlink_relationships'] += 1; continue
                    if relation.getAttribute('TargetMode').lower() == 'external':
                        raise ParseProblem('Word 引用了外部文件或服务，请先嵌入所需内容')
                    target = unquote(relation.getAttribute('Target'))
                    if not target or any(x in target for x in ('\\','\x00',':','?','#')) or target.startswith('/'):
                        raise ParseProblem('Word 内部引用异常')
                    resolved = posixpath.normpath(posixpath.join(parent, target))
                    if resolved.startswith('../') or resolved not in parts:
                        raise ParseProblem('Word 内部引用缺失或越界')
                    if typ not in {'image','thumbnail','printerSettings','font','fontKey'} and resolved not in xmls:
                        raise ParseProblem('Word 结构关系引用了非标准 XML 内容')
        for name, document in xmls.items():
            if not name.lower().endswith('.xml'): continue
            # Historical formatting subtrees can contain old revision markers.
            # Remove them before interpreting current content changes.
            for node in _elements(document):
                if node.namespaceURI == W and node.localName in _CHANGES:
                    if node.parentNode is not None:
                        counters['accepted_'+node.localName] += 1; _remove(node)
            for node in _elements(document):
                if node.namespaceURI != W: continue
                local = node.localName
                if local in _RANGE_REVISIONS or local in {'cellDel','cellMerge','conflictIns','conflictDel'} or (local.endswith('Change') and local not in _CHANGES):
                    raise ParseProblem('Word 含尚未支持的范围、单元格或冲突修订，请先在 Word 中处理后另存')
            # Accepted row deletion removes its entire row; row insertion stays.
            for row in list(document.getElementsByTagNameNS(W, 'tr')):
                props = next((n for n in row.childNodes if _tag(n,'trPr')),None)
                if props and any(_tag(n,'del') for n in props.childNodes):
                    counters['deleted_rows'] += 1; _remove(row)
            # A deleted paragraph mark joins only adjacent sibling paragraphs.
            for paragraph in list(document.getElementsByTagNameNS(W,'p')):
                if paragraph.parentNode is None: continue
                while True:
                    props = next((n for n in paragraph.childNodes if _tag(n,'pPr')),None)
                    rprops = next((n for n in props.childNodes if _tag(n,'rPr')),None) if props else None
                    marker = next((n for n in rprops.childNodes if _tag(n,'del') or _tag(n,'moveFrom')),None) if rprops else None
                    if marker is None: break
                    nxt = paragraph.nextSibling
                    while nxt is not None and nxt.nodeType in {Node.TEXT_NODE,Node.COMMENT_NODE}: nxt = nxt.nextSibling
                    if nxt is None or not _tag(nxt,'p'):
                        raise ParseProblem('Word 的段落边界修订无法安全合并，请先在 Word 中处理后另存')
                    _remove(marker)
                    # Preserve the following paragraph's final end-marker only.
                    next_props = next((n for n in nxt.childNodes if _tag(n,'pPr')),None)
                    next_rprops = next((n for n in next_props.childNodes if _tag(n,'rPr')),None) if next_props else None
                    if next_rprops and any(_tag(n,'del') or _tag(n,'moveFrom') for n in next_rprops.childNodes):
                        if rprops is not None:
                            for n in list(next_rprops.childNodes):
                                if _tag(n,'del') or _tag(n,'moveFrom'): rprops.appendChild(n.cloneNode(True))
                    for child in list(nxt.childNodes):
                        if not _tag(child,'pPr'): paragraph.appendChild(child)
                    _remove(nxt); counters['joined_paragraph_marks'] += 1
            for node in _elements(document):
                if node.namespaceURI != W or node.parentNode is None: continue
                local = node.localName; parent = node.parentNode
                parent_local = parent.localName if parent.nodeType == Node.ELEMENT_NODE else ''
                if local in {'ins','moveTo'}:
                    if parent_local in {'rPr','trPr','numPr'}: _remove(node)
                    elif parent_local in {'p','hyperlink','ins','del','moveFrom','moveTo','sdtContent'}: _unwrap(node)
                    else: raise ParseProblem('Word 的插入或移入修订位置尚未支持')
                    counters['accepted_'+local] += 1
                elif local in {'del','moveFrom'}:
                    if parent_local in {'rPr','trPr'}: _remove(node)
                    elif parent_local in {'p','hyperlink','ins','del','moveFrom','moveTo','sdtContent'}: _remove(node)
                    else: raise ParseProblem('Word 的删除或移出修订位置尚未支持')
                    counters['accepted_'+local] += 1
                elif local == 'cellIns': _remove(node); counters['accepted_cellIns'] += 1
            # Freeze cached displayed results, including TOC/page numbers/date;
            # never evaluate source instructions or update external data fields.
            for node in _elements(document):
                if node.namespaceURI != W or node.parentNode is None: continue
                if node.localName in {'instrText','delInstrText','fldChar'}:
                    counters['frozen_field_markers'] += 1; _remove(node)
                elif node.localName in {'fldSimple','hyperlink'}:
                    counters['disabled_'+node.localName] += 1; _unwrap(node)
                elif node.localName in {'vanish','webHidden','specVanish'}:
                    counters['revealed_hidden_text_markers'] += 1; _remove(node)
                elif node.localName in {'trackRevisions','doNotTrackMoves','revisionView','updateFields'}:
                    _remove(node)
            # Renderer must not accept an active field/revision that survived.
            for node in _elements(document):
                if node.namespaceURI == W and node.localName in {'ins','del','moveFrom','moveTo','fldChar','instrText','delInstrText','fldSimple','hyperlink'}:
                    raise ParseProblem('Word 修订或动态字段未能完整固定，已停止转换')
        chinese=set()
        for name, document in xmls.items():
            for node in document.getElementsByTagNameNS(W,'t'):
                for child in node.childNodes:
                    if child.nodeType in {Node.TEXT_NODE,Node.CDATA_SECTION_NODE}: chinese.update(c for c in child.data if '\u4e00'<=c<='\u9fff')
            parts[name] = document.toxml(encoding='UTF-8')
        out = io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as result:
            for name in sorted(parts):
                item = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED
                result.writestr(item,parts[name])
    normalized = out.getvalue()
    return normalized, {'policy':POLICY,'source_sha256':hashlib.sha256(content).hexdigest(),'normalized_sha256':hashlib.sha256(normalized).hexdigest(),'changes':dict(sorted(counters.items())),'hidden_content_policy':'reveal_retained_text_v1','source_chinese_unique':len(chinese),
        'warnings':['已选择显示保留的隐藏文字和隐藏脚注；原 Word 不改动，外发前须核对。','本地呈现引擎可能替换字体或调整排版，须逐页核对原 Word 与 PDF 副本。','动态字段、目录和页码保留原缓存文字；不自动更新。','活动链接已解除，保留原显示文字；PDF 不替代原件。']}

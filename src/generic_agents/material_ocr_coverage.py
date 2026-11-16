"""从原件声明全部覆盖范围；不执行文内指令、不下载外部关系。"""
import hashlib
import io
import posixpath
from pathlib import PurePath
from defusedxml import ElementTree as ET
from .contracts import digest
from .document_parsers import checked_zip, ParseProblem, NS

REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'
DRAW = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
VML = '{urn:schemas-microsoft-com:vml}'


def coverage(content, name, *, profile=None):
    if profile is not None and (not isinstance(profile, dict) or set(profile) != {'kind', 'provenance_hash'}
        or profile.get('kind') != 'document_copy_full_pages_v1' or not isinstance(profile.get('provenance_hash'), str)
        or len(profile['provenance_hash']) != 64 or any(c not in 'abcdef0123456789' for c in profile['provenance_hash'])
        or PurePath(name).suffix.lower() != '.pdf'):
        raise ParseProblem('本地副本全页覆盖策略无法核对')
    units, gaps = [], []
    suffix = PurePath(name).suffix.lower()
    if suffix == '.pdf':
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(content))
        if pdf.is_encrypted or len(pdf.pages) > 500 or not pdf.pages:
            raise ParseProblem('PDF加密、为空或超过500页')
        needs = profile is not None
        work = [0]
        from pypdf.generic import ContentStream
        def inline(stream):
            # ContentStream继承空字典，bool(stream)可为False但含真实图像。
            if stream is None:return False
            content=stream if isinstance(stream,ContentStream) else ContentStream(stream,pdf)
            if len(content.get_data())>80*1024*1024:raise ParseProblem('PDF单页内容超过本地覆盖预算')
            return any(operator==b'INLINE IMAGE' for _,operator in content.operations)
        def images(resources, visited, depth=0):
            if depth > 8: raise ParseProblem('PDF资源嵌套过深，覆盖尚不能核对')
            resources = resources.get_object() if hasattr(resources, 'get_object') else resources
            if not resources: return False
            objects = resources.get('/XObject', {})
            objects = objects.get_object() if hasattr(objects, 'get_object') else objects
            found = False
            for obj in objects.values():
                work[0] += 1
                if work[0] > 20000: raise ParseProblem('PDF图像资源超过覆盖检查上限')
                key = (getattr(obj, 'idnum', None), getattr(obj, 'generation', None))
                if key != (None, None) and key in visited: continue
                visited.add(key)
                obj = obj.get_object()
                if obj.get('/F'): gaps.append('PDF包含外部文件资源，未读取网络内容')
                if obj.get('/Subtype') == '/Image': found = True
                if obj.get('/Subtype') == '/Form':
                    nested=images(obj.get('/Resources'),visited,depth+1)
                    found=inline(obj) or nested or found
            return found
        pages = []
        for n, page in enumerate(pdf.pages, 1):
            has_text = bool((page.extract_text() or '').strip())
            referenced=images(page.get('/Resources'),set())
            has_image = inline(page.get_contents()) or referenced
            has_forms = bool(page.get('/Annots'))
            if has_forms:gaps.append(f'第{n}页含尚未验证渲染覆盖的批注或表单，请提供无批注的完整副本')
            needs = needs or not has_text or has_image or has_forms
            pages.append({'index': n, 'kind': 'pdf_page', 'page': n, 'body_index': None,
                          'has_native_text': has_text, 'has_image': has_image, 'has_annotations': has_forms})
        # 只要存在混合/扫描范围，就逐页核对，不能以其他页文字代替。
        if needs: units = pages
        catalog=pdf.trailer['/Root'].get_object()
        if catalog.get('/AcroForm'):gaps.append('PDF含尚未验证的交互表单范围')
        if catalog.get('/OCProperties'):gaps.append('PDF含尚未验证的可选或隐藏图层范围')
    elif suffix == '.docx':
        with checked_zip(content) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
            body = root.find(NS + 'body')
            if body is None: raise ParseProblem('DOCX缺少正文')
            rels = {}
            if 'word/_rels/document.xml.rels' in archive.namelist():
                relations = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
                for r in relations:
                    key = r.get('Id')
                    if key in rels: raise ParseProblem('DOCX关系标识重复')
                    rels[key] = {'target': r.get('Target', ''), 'external': r.get('TargetMode') == 'External',
                                 'type': r.get('Type', '')}
            used = set()
            non_body_parts={name for name in archive.namelist() if name.startswith(('word/header','word/footer','word/footnotes','word/endnotes')) and name.endswith('.xml')}
            for relation in rels.values():
                if relation['type'].rsplit('/',1)[-1] not in {'header','footer','footnotes','endnotes'}:continue
                target=posixpath.normpath(posixpath.join('word',relation['target']))
                if relation['external'] or not target.startswith('word/') or target not in archive.namelist():
                    gaps.append('页眉、页脚或脚注关系缺失或为外部内容，覆盖待核')
                else:non_body_parts.add(target)
            for node in body.iter():
                if node.tag in {NS+'headerReference',NS+'footerReference'} and node.get(REL+'id') not in rels:
                    gaps.append('页眉或页脚引用缺少原件关系，覆盖待核')
            for body_index, item in enumerate(body, 1):
                for tag, label in [(NS+'txbxContent', '文本框'), (NS+'ins', '修订'), (NS+'del', '修订')]:
                    if any(True for _ in item.iter(tag)): gaps.append(f'正文第{body_index}处包含尚未覆盖的{label}')
                for node in item.iter():
                    local=node.tag.rsplit('}',1)[-1]
                    if node.tag in {NS+'altChunk',NS+'object',NS+'sdt',NS+'customXml'} or local in {'OLEObject','oMath','oMathPara','chart','relIds'}:
                        gaps.append(f'正文第{body_index}处含尚未覆盖的图表、嵌入对象或公式')
                    if node.tag==DRAW+'graphicData' and node.get('uri','')!='http://schemas.openxmlformats.org/drawingml/2006/picture':
                        gaps.append(f'正文第{body_index}处含尚未覆盖的图形或SmartArt')
                    if node.tag==DRAW+'srcRect' and any(value not in {'0','0.0',''} for value in node.attrib.values()):
                        gaps.append(f'正文第{body_index}处图片含未支持的裁剪，原嵌图不等于显示范围')
                    if node.tag==DRAW+'xfrm' and (node.get('rot','0') not in {'0','0.0'} or node.get('flipH','false').lower() not in {'false','0'} or node.get('flipV','false').lower() not in {'false','0'}):
                        gaps.append(f'正文第{body_index}处图片含未支持的旋转或翻转')
                    if node.tag.startswith(VML):
                        transforms={key.strip().lower():value.strip().lower() for part in node.get('style','').split(';') if ':' in part for key,value in [part.split(':',1)]}
                        if any(transforms.get(key,'0') not in {'','0','0.0','0f','none','false'} for key in {'rotation','flip'}):
                            gaps.append(f'正文第{body_index}处VML图片含未支持的旋转或翻转')
                        if any(key.rsplit('}',1)[-1].lower().startswith('crop') and value.strip().lower() not in {'','0','0.0','0f','0%'} for key,value in node.attrib.items()):
                            gaps.append(f'正文第{body_index}处VML图片含未支持的裁剪，原嵌图不等于显示范围')
                    if node.tag not in {DRAW+'blip', VML+'imagedata'}: continue
                    key = node.get(REL+'embed') or node.get(REL+'id') or node.get(REL+'link')
                    relation = rels.get(key)
                    if not relation or relation['external']:
                        gaps.append(f'正文第{body_index}处图片关系缺失或为外部关系，未下载'); continue
                    target = posixpath.normpath(posixpath.join('word', relation['target']))
                    if not target.startswith('word/') or target not in archive.namelist():
                        gaps.append(f'正文第{body_index}处图片原件缺失'); continue
                    used.add(target)
                    units.append({'index': len(units)+1, 'kind': 'docx_image', 'page': None,
                                  'body_index': body_index, 'relationship_id': key, 'archive_name': target,
                                  'embedded_sha256': hashlib.sha256(archive.read(target)).hexdigest()})
            for name in archive.namelist():
                if name.startswith('word/media/') and not name.endswith('/') and name not in used:
                    gaps.append('存在未定位到正文的内嵌图片，覆盖待核')
                if name in non_body_parts:
                    part = ET.fromstring(archive.read(name))
                    if any((n.text or '').strip() for n in part.iter(NS+'t')) or any(n.tag in {DRAW+'blip',VML+'imagedata'} or n.tag.rsplit('}',1)[-1] in {'oMath','OLEObject','chart'} for n in part.iter()):
                        gaps.append('页眉、页脚或脚注内容尚未完整覆盖')
    if len(units) > 500: gaps.append('需要识别的图片超过500个，请拆分资料')
    result = {'contract_version': 1, 'source_sha256': hashlib.sha256(content).hexdigest(),
              'status': 'blocked' if gaps else 'needs_ocr' if units else 'complete',
              'unit_count': len(units), 'units': units, 'gaps': list(dict.fromkeys(gaps)),
              'reason': '；'.join(dict.fromkeys(gaps)) if gaps else None}
    if profile is not None:
        result = {**result, 'contract_version': 2, 'profile': dict(profile), 'force_all_pages': True}
    return {**result, 'hash': digest(result)}


def verify_coverage(value):
    if not isinstance(value, dict) or value.get('contract_version') not in {1, 2} or value.get('hash') != digest({k:v for k,v in value.items() if k != 'hash'}):
        raise ParseProblem('资料覆盖清单完整性无法核对')
    if value.get('contract_version') == 2:
        profile = value.get('profile', {})
        if (not isinstance(profile, dict) or set(profile) != {'kind', 'provenance_hash'}
            or profile.get('kind') != 'document_copy_full_pages_v1' or not isinstance(profile.get('provenance_hash'), str)
            or len(profile['provenance_hash']) != 64 or any(c not in 'abcdef0123456789' for c in profile['provenance_hash'])
            or value.get('force_all_pages') is not True or not value.get('units')
            or value.get('unit_count') != len(value['units'])
            or any(u.get('kind') != 'pdf_page' or u.get('index') != i or u.get('page') != i for i,u in enumerate(value['units'],1))):
            raise ParseProblem('本地副本必须逐页核对全部页面')
    return value

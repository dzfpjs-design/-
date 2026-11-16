"""Remove only verified, inert intra-document navigation from a local PDF.

No page content, graphics, attachments or action annotations are discarded to
make coverage pass. Unsupported annotations stop the derivative workflow.
"""
import hashlib
import io
import math
import re
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, NullObject, NumberObject, FloatObject, TextStringObject

_ALLOWED = {'/Type','/Subtype','/Border','/Rect','/Contents','/Dest','/H','/F','/C','/P'}
_FITS = {'/XYZ':3,'/Fit':0,'/FitH':1,'/FitV':1,'/FitR':4,'/FitB':0,'/FitBH':1,'/FitBV':1}

def _error(): raise ValueError('转换 PDF 含尚不支持的注释、链接或活动内容；原件保留，请先处理后重试')
def _number(n): return isinstance(n,(NumberObject,FloatObject,int,float)) and not isinstance(n,bool) and math.isfinite(float(n))
def _resolve(obj): return obj.get_object() if hasattr(obj,'get_object') else obj

def strip_internal_navigation(content):
    reader=PdfReader(io.BytesIO(content),strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 500: _error()
    catalog=reader.trailer['/Root'];names=_resolve(catalog.get('/Names')) or {}
    if reader.attachments or names.get('/JavaScript') or any(k in catalog for k in ('/OpenAction','/AA','/AcroForm')): _error()
    pages={(p.indirect_reference.idnum,p.indirect_reference.generation) for p in reader.pages if p.indirect_reference}
    def page_ref(value): return isinstance(value,IndirectObject) and value.pdf is reader and (value.idnum,value.generation) in pages
    count=0
    for page in reader.pages:
        if '/AA' in page: _error()
        annots=_resolve(page.get('/Annots'))
        if annots is None: continue
        if not isinstance(annots,ArrayObject) or len(annots)>5000: _error()
        for ref in annots:
            a=_resolve(ref)
            if not isinstance(a,DictionaryObject) or set(a)-_ALLOWED or a.get('/Subtype')!='/Link' or a.get('/Type','/Annot')!='/Annot': _error()
            dest=_resolve(a.get('/Dest'));rect=_resolve(a.get('/Rect'))
            if not isinstance(dest,ArrayObject) or len(dest)<2 or not page_ref(dest[0]): _error()
            fit=dest[1]
            if fit not in _FITS or len(dest)!=2+_FITS[fit] or any(not(_number(n) or isinstance(n,NullObject)) for n in dest[2:]): _error()
            if not isinstance(rect,ArrayObject) or len(rect)!=4 or not all(_number(n) for n in rect): _error()
            if '/P' in a and not page_ref(a.raw_get('/P')): _error()
            if '/H' in a and a['/H'] not in {'/N','/I','/O','/P'}: _error()
            if '/F' in a and (not isinstance(a['/F'],NumberObject) or not 0 <= int(a['/F']) <= 1023): _error()
            border=_resolve(a.get('/Border'))
            if not isinstance(border,ArrayObject) or len(border)!=3 or any(not _number(v) or float(v)!=0 for v in border): _error()
            for key in ('/C',):
                if key in a:
                    values=_resolve(a[key])
                    if not isinstance(values,ArrayObject) or len(values)>4 or any(not _number(v) for v in values): _error()
            if '/Contents' in a:
                label=a['/Contents']
                # LO's footnote labels are numeric; do not discard substantive
                # comment text merely because its annotation subtype says Link.
                if not isinstance(label,TextStringObject) or not re.fullmatch(r'[\d\s\[\]()（）.、*†‡\-]{0,64}',str(label)): _error()
            count+=1
    if not count: return content,{'internal_navigation_links_removed':0,'renderer_pdf_sha256':hashlib.sha256(content).hexdigest()}
    writer=PdfWriter();writer.clone_document_from_reader(reader)
    for page in writer.pages:
        if '/Annots' in page: del page['/Annots']
    out=io.BytesIO();writer.write(out);result=out.getvalue()
    checked=PdfReader(io.BytesIO(result),strict=True)
    if len(checked.pages)!=len(reader.pages) or any(p.get('/Annots') for p in checked.pages): _error()
    # Prove content stream bytes did not change during inert-link removal.
    def stream_bytes(p):
        value=p.get_contents()
        return value.get_data() if value else b''
    if any(stream_bytes(a)!=stream_bytes(b) for a,b in zip(reader.pages,checked.pages)): _error()
    return result,{'internal_navigation_links_removed':count,'renderer_pdf_sha256':hashlib.sha256(content).hexdigest(),'page_content_streams_unchanged':True}


def chinese_text_health(content, source_unique):
    reader=PdfReader(io.BytesIO(content),strict=True);characters=set();total=0
    for page in reader.pages:
        text=page.extract_text() or '';total+=len(text)
        if total>2000000: raise ValueError('PDF 文字超过本地核对上限，请拆分资料')
        characters.update(c for c in text if '\u4e00'<=c<='\u9fff')
    # A conservative failure signal, never a claim of full content coverage.
    if source_unique>=10 and len(characters)<max(5,int(source_unique*.5)):
        raise ValueError('本地 PDF 中文字体呈现异常，未发布副本；请修复字体后按原记录核对')
    return {'check':'chinese_alphabet_collapse_v1','source_unique':source_unique,'pdf_unique':len(characters),'passed':True,'full_coverage_proven':False}

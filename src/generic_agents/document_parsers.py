"""Bounded native parsing. No macros, network fetches, or document instructions run."""
import csv
import io
import json
import zipfile
from pathlib import PurePath
from defusedxml import ElementTree as ET

MAX_EXPANDED = 80 * 1024 * 1024
MAX_BLOCKS = 12000
MAX_CHARACTERS = 2000000
NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class ParseProblem(ValueError):
    pass


def checked_zip(content):
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 10000:
            raise ParseProblem('压缩文件目录异常或条目过多')
        total = 0
        for entry in archive.infolist():
            total += entry.file_size
            if entry.flag_bits & 1 or total > MAX_EXPANDED or entry.file_size > MAX_EXPANDED:
                raise ParseProblem('文件加密或解压体积超过上限')
            if entry.file_size > 1000000 and entry.file_size > max(entry.compress_size, 1) * 300:
                raise ParseProblem('文件压缩比异常，请拆分后重新上传')
        return archive
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParseProblem('Office文件损坏或格式不匹配') from exc


def parse_document(content, name):
    suffix = PurePath(name).suffix.lower()
    blocks, warnings = [], []
    chars = 0

    def add(text, kind, location):
        nonlocal chars
        text = text.strip()
        if not text:
            return
        chars += len(text)
        if chars > MAX_CHARACTERS or len(blocks) >= MAX_BLOCKS:
            raise ParseProblem('正文或表格超过解析上限，请拆分资料；原件已保留')
        blocks.append({'id': 'b' + str(len(blocks) + 1), 'text': text, 'kind': kind, 'location': location})

    if suffix in {'.txt', '.md', '.csv', '.json', '.geojson'}:
        try:
            text = content.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ParseProblem('文本不是UTF-8，请转换编码后重新上传') from exc
        if '\x00' in text:
            raise ParseProblem('文本包含二进制内容')
        if suffix in {'.json', '.geojson'}:
            try:
                json.loads(text)
            except (ValueError, RecursionError) as exc:
                raise ParseProblem('JSON资料格式不完整') from exc
        if suffix == '.csv':
            csv.field_size_limit(MAX_CHARACTERS)
            for n, row in enumerate(csv.reader(io.StringIO(text)), 1):
                add(' | '.join(row), 'table_row', {'row': n})
        else:
            for n, line in enumerate(text.splitlines(), 1):
                add(line, 'paragraph', {'line': n})
        engine = 'native-text-v1'
    elif suffix == '.docx':
        with checked_zip(content) as archive:
            try:
                root = ET.fromstring(archive.read('word/document.xml'))
            except Exception as exc:
                raise ParseProblem('DOCX正文损坏或含不安全XML') from exc
            body = root.find(NS + 'body')
            if body is None:
                raise ParseProblem('DOCX缺少正文')
            for n, item in enumerate(body, 1):
                if item.tag == NS + 'p':
                    add(''.join(t.text or '' for t in item.iter(NS + 't')), 'paragraph', {'body_index': n})
                elif item.tag == NS + 'tbl':
                    for rn, row in enumerate(item.findall(NS + 'tr'), 1):
                        cells = [''.join(t.text or '' for t in c.iter(NS + 't')) for c in row.findall(NS + 'tc')]
                        add(' | '.join(cells), 'table_row', {'body_index': n, 'row': rn})
            warnings.append('已读取正文与表格；图片、文本框、脚注、修订及精确页码需增强解析核对')
        engine = 'native-docx-v1'
    elif suffix == '.xlsx':
        from openpyxl import load_workbook
        with checked_zip(content):
            pass
        try:
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
            for sheet in book:
                if sheet.max_row and sheet.max_row > MAX_BLOCKS:
                    raise ParseProblem('工作表行数超过上限，请拆分资料')
                if sheet.max_column and sheet.max_column > 500:
                    raise ParseProblem('工作表列数超过上限')
                for rn, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    add(' | '.join('' if x is None else str(x) for x in row), 'table_row', {'sheet': sheet.title, 'row': rn})
            book.close()
        except ParseProblem:
            raise
        except Exception as exc:
            raise ParseProblem('XLSX文件损坏或包含不支持结构') from exc
        warnings.append('公式保留为原公式，未执行计算；图片和复杂合并结构需核对')
        engine = 'native-xlsx-v1'
    elif suffix == '.pdf':
        from pypdf import PdfReader
        if not content.startswith(b'%PDF-'):
            raise ParseProblem('PDF格式不匹配')
        try:
            pdf = PdfReader(io.BytesIO(content))
            if pdf.is_encrypted:
                raise ParseProblem('PDF已加密，请上传可读取副本')
            if len(pdf.pages) > 500:
                raise ParseProblem('PDF超过500页，请拆分资料')
            empty = []
            for n, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ''
                if text.strip():
                    add(text, 'page_text', {'page': n})
                else:
                    empty.append(n)
            if empty:
                warnings.append('以下页没有可读取文字，需要OCR：' + ','.join(map(str, empty)))
        except ParseProblem:
            raise
        except Exception as exc:
            raise ParseProblem('PDF损坏或无法读取') from exc
        engine = 'native-pdf-v1'
    else:
        return {'status': 'needs_input', 'blocks': [], 'engine': 'not_supported',
                'warnings': ['原件已保存，此格式需要影像或专业解析处理器，尚未接入']}
    return {'status': 'needs_input' if not blocks or (suffix == '.pdf' and empty) else 'parsed',
            'blocks': blocks, 'engine': engine, 'warnings': warnings or ([] if blocks else ['未找到可读取正文'])}

"""Bounded dataset readers and a DNS-pinned, read-only HTTP transport.

Parsing preserves source values; it neither maps business fields nor grants
authority. The transport uses httpcore's public backend interface and dials
numeric, checked sockaddr values, while TLS authenticates the original host.
"""
import csv
import io
import ipaddress
import json
import math
import queue
import re
import socket
import ssl
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timezone
from urllib.parse import urlsplit

import httpcore
from defusedxml import ElementTree as ET

MAX_ORIGINAL_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_RECORDS = 5000
MAX_PARSED_BYTES = 2 * 1024 * 1024
MAX_HTTP_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30
MAX_FIELDS = 512
MAX_RECORD_SETS = 32
MAX_DEPTH = 32
MAX_ZIP_ENTRIES = 10000
_CSV_LOCK = threading.Lock()
_DNS_SLOTS = threading.BoundedSemaphore(2)
_TYPES = ('null', 'boolean', 'integer', 'number', 'string', 'object', 'array')


class ReaderError(ValueError):
    """Sanitized, human-readable failure; the caller retains original bytes."""


def _need(ok, message):
    if not ok:
        raise ReaderError(message)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def _escape(value):
    return value.replace('~', '~0').replace('/', '~1')


def _type(value):
    if value is None: return 'null'
    if type(value) is bool: return 'boolean'
    if type(value) is int: return 'integer'
    if type(value) is float:
        _need(math.isfinite(value), '数据含非有限数值，原件保留，请修正后重新上传')
        return 'number'
    if type(value) is str: return 'string'
    if type(value) is dict: return 'object'
    if type(value) is list: return 'array'
    raise ReaderError('数据包含不支持的值类型，原件保留')


def _validate_tree(value, depth=0):
    _need(depth <= MAX_DEPTH, '数据嵌套层级超过上限，请拆分原件')
    kind = _type(value)
    if kind == 'object':
        for key, child in value.items():
            _need(type(key) is str, 'JSON字段名称必须是文字')
            _validate_tree(child, depth + 1)
    elif kind == 'array':
        for child in value: _validate_tree(child, depth + 1)


def _record_set(key, label, rows, headers=()):
    records, content_size = [], 0
    descriptors = {'/' + _escape(name): {'label': name, 'types': set()} for name in headers}
    def describe(value, path, labels):
        kind = _type(value)
        seen = descriptors.setdefault(path, {'label': ' / '.join(labels), 'types': set()})
        seen['types'].add(kind)
        shapes = seen['types'] - {'null'}
        _need(not (('object' in shapes or 'array' in shapes) and len(shapes) > 1),
            '同一字段同时出现不兼容的结构，请拆分或统一来源结构')
        _need(len(descriptors) <= MAX_FIELDS, '单份数据的字段数量超过上限')
        if kind == 'object':
            for name, child in value.items():
                describe(child, path + '/' + _escape(name), [*labels, name])
    for index, location, value in rows:
        _need(len(records) < MAX_RECORDS, '单个数据集超过5000条，未截取前部分，请缩小来源范围')
        _need(type(value) is dict, '记录必须是对象，不能混用对象与数值或文字')
        _validate_tree(value)
        content_size += len(_canonical(value))
        _need(content_size <= MAX_PARSED_BYTES, '解析内容超过2MiB，未截断，请拆分资料')
        for name, child in value.items(): describe(child, '/' + _escape(name), [name])
        records.append({'index': index, 'location': location, 'value': value})
    return {'key': key, 'label': label, 'count': len(records),
        'fields': [{'path': path, 'label': item['label'],
            'types': [kind for kind in _TYPES if kind in item['types']]}
            for path, item in sorted(descriptors.items())], 'records': records}


def _json_sets(content):
    def pairs(items):
        value = {}
        for key, child in items:
            _need(key not in value, 'JSON存在重复字段，未采用最后一个值覆盖前值')
            value[key] = child
        return value
    def constant(_value): raise ReaderError('JSON包含非有限数值，原件保留')
    value = json.loads(content.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)
    _validate_tree(value)
    found = []
    def discover(node, path):
        if type(node) is list:
            _need(not node or all(type(item) is dict for item in node),
                '记录数组混有非对象值，请选择结构一致的数据集')
            _need(len(found) < MAX_RECORD_SETS, 'JSON数据集数量超过上限')
            found.append(_record_set(path, path or '根数据集',
                ((n, {'pointer': path + '/' + str(n)}, item) for n, item in enumerate(node))))
        elif type(node) is dict:
            for key, child in node.items():
                pointer = path + '/' + _escape(key)
                if type(child) is dict: discover(child, pointer)
                elif type(child) is list and (not child or any(type(item) is dict for item in child)):
                    discover(child, pointer)
    _need(type(value) in {dict, list}, 'JSON顶层必须为对象或记录数组')
    discover(value, '')
    return found or [_record_set('', '根对象', [(0, {'pointer': ''}, value)])]


def _headers(values):
    _need(values and len(values) <= MAX_FIELDS, '表格缺少表头或列数超过上限')
    _need(all(type(value) is str and value.strip() for value in values), '表头必须是非空文字，不能自动补列名')
    _need(len(values) == len(set(values)), '表格存在重复表头，不能覆盖同名列')
    return values


def _csv_sets(content):
    text = content.decode('utf-8-sig')
    _need('\x00' not in text, 'CSV包含二进制内容，无法按文字表格读取')
    with _CSV_LOCK:
        previous = csv.field_size_limit()
        try:
            csv.field_size_limit(MAX_PARSED_BYTES)
            reader = csv.reader(io.StringIO(text, newline=''), strict=True)
            headers = _headers(next(reader, []))
            def rows():
                prior_line = reader.line_num
                for index, row in enumerate(reader):
                    start = prior_line + 1; prior_line = reader.line_num
                    _need(len(row) == len(headers), 'CSV行列数量与表头不一致，未填补或丢弃单元格')
                    yield index, {'row': start, 'end_row': reader.line_num}, dict(zip(headers, row))
            return [_record_set('csv', 'CSV表格', rows(), headers)]
        finally:
            csv.field_size_limit(previous)


def _inspect_xml(content, name, warnings):
    """Validate XML without building a second expanded workbook in memory."""
    namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    worksheet = name.startswith('xl/worksheets/') and name.endswith('.xml')
    previous_row = previous_column = 0
    current_row = None
    stack = []
    for event, node in ET.iterparse(io.BytesIO(content), events=('start', 'end'),
            forbid_dtd=True, forbid_entities=True, forbid_external=True):
        if event == 'start':
            stack.append(node)
            _need(len(stack) <= 128, '工作簿XML层级超过上限')
            if name == '[content_types].xml':
                _need(not any('macroenabled' in value.lower() for value in node.attrib.values()),
                    '带宏的工作簿不属于当前XLSX读取范围')
            if name.endswith('.rels') and node.attrib.get('TargetMode', '').lower() == 'external':
                warnings.append('工作簿外部链接仅保留在原件，未读取链接内容')
            if worksheet and node.tag == namespace + 'row':
                number = int(node.attrib.get('r', '0'))
                _need(previous_row < number <= MAX_RECORDS + 1,
                    '工作表行重复、乱序或超出5000条记录范围，未覆盖或截断读取')
                previous_row = current_row = number; previous_column = 0
            if worksheet and node.tag == namespace + 'c':
                match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', node.attrib.get('r', ''))
                _need(match is not None and current_row is not None and int(match[2]) == current_row,
                    '工作表单元格位置与所在行不一致')
                column = 0
                for letter in match[1]: column = column * 26 + ord(letter) - 64
                _need(previous_column < column <= MAX_FIELDS,
                    '工作表单元格重复、乱序或列位置超过字段上限')
                previous_column = column
        else:
            if worksheet and node.tag == namespace + 'row': current_row = None
            stack.pop()
            if stack: stack[-1].remove(node)
            node.clear()


def _xlsx_sets(content):
    # Inspect actual expansion and active content before handing bytes to the
    # read-only parser. No archive paths are extracted to the filesystem.
    warnings = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        items = archive.infolist(); names = [item.filename for item in items]
        _need(len(items) <= MAX_ZIP_ENTRIES and len(names) == len(set(names)), 'XLSX压缩目录重复或数量超限')
        total = 0
        for item in items:
            name = item.filename.lower()
            _need(not item.flag_bits & 1 and not name.startswith('/') and '..' not in name.split('/'), 'XLSX压缩目录或加密方式不受支持')
            _need('vbaproject' not in name and not name.startswith('xl/embeddings/'), '当前仅接收无宏、无嵌入可执行对象的XLSX文件')
            _need(item.file_size <= MAX_EXPANDED_BYTES and total + item.file_size <= MAX_EXPANDED_BYTES, 'XLSX展开超过80MiB，原件保留')
            chunks = bytearray()
            with archive.open(item) as stream:
                while chunk := stream.read(65536):
                    total += len(chunk)
                    _need(total <= MAX_EXPANDED_BYTES, 'XLSX实际展开超过80MiB，原件保留')
                    if name.endswith('.xml') or name.endswith('.rels'): chunks.extend(chunk)
            if name.endswith('.xml') or name.endswith('.rels'): _inspect_xml(chunks, name, warnings)
    from openpyxl import load_workbook
    from openpyxl.styles.numbers import is_datetime
    book = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
    sets = []
    try:
        _need(0 < len(book.worksheets) <= MAX_RECORD_SETS, '工作表数量超过上限或没有可读取工作表')
        for sheet in book.worksheets:
            sheet.reset_dimensions()  # Never trust a padded/malicious dimension.
            iterator = sheet.iter_rows()
            first = next(iterator, ())
            if not first:
                sets.append(_record_set('sheet:' + sheet.title, sheet.title, [])); continue
            headers = _headers([cell.value for cell in first])
            def rows():
                for index, cells in enumerate(iterator):
                    _need(len(cells) <= len(headers), '工作表数据列超出表头，未丢弃额外单元格')
                    values = []
                    for cell in cells:
                        value = cell.value
                        if cell.data_type == 'f':
                            warnings.append('工作簿公式仅按原公式文字保留，未执行；公式不能作为数值导入')
                        if isinstance(value, (datetime, date, clock_time)):
                            # Excel stores Python dates as midnight datetimes.
                            # Only a date-only format plus a zero time proves
                            # that projecting to a date discards no time value.
                            if (isinstance(value, datetime) and value.time() == clock_time(0)
                                    and is_datetime(cell.number_format) == 'date'):
                                value = value.date()
                            value = value.isoformat()
                            warnings.append('日期或时间单元格保留为ISO文字，未补充时区')
                        values.append(value)
                    values.extend([None] * (len(headers) - len(values)))
                    yield index, {'sheet': sheet.title, 'row': index + 2}, dict(zip(headers, values))
            sets.append(_record_set('sheet:' + sheet.title, sheet.title, rows(), headers))
    finally:
        book.close()
    return sets, list(dict.fromkeys(warnings))


def parse_dataset(content: bytes, filename: str, kind: str) -> dict:
    """Parse bounded original bytes, preserving typed values and provenance."""
    _need(type(content) is bytes and len(content) <= MAX_ORIGINAL_BYTES, '资料必须是原始文件，且不超过20MiB')
    _need(type(filename) is str and type(kind) is str and kind in {'json', 'csv', 'xlsx'}, '请选择JSON、CSV或XLSX资料类型')
    try:
        warnings = []
        if kind == 'json': sets = _json_sets(content)
        elif kind == 'csv': sets = _csv_sets(content)
        else: sets, warnings = _xlsx_sets(content)
        engine = 'native-xlsx-v204-2' if kind == 'xlsx' else 'native-' + kind + '-v204'
        result = {'schema': 'business-data-parsed-v204', 'engine': engine,
            'warnings': warnings, 'record_sets': sets}
        _need(len(_canonical(result)) <= MAX_PARSED_BYTES, '解析内容连同来源位置超过2MiB，未截断，请拆分资料')
        return result
    except ReaderError: raise
    except Exception:
        raise ReaderError('文件格式、编码或表格结构无法核对，原件保留，请修正后重试') from None


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    media_type: str
    headers: dict
    received_at: str


def _remaining(deadline, timeout=None):
    left = deadline - time.monotonic()
    if left <= 0: raise httpcore.ReadTimeout('business data deadline')
    return min(left, timeout) if timeout is not None else left


class _PinnedStream(httpcore.NetworkStream):
    def __init__(self, sock, host, deadline):
        self.sock, self.host, self.deadline = sock, host, deadline

    def read(self, max_bytes, timeout=None):
        self.sock.settimeout(_remaining(self.deadline, timeout))
        return self.sock.recv(max_bytes)

    def write(self, buffer, timeout=None):
        while buffer:
            self.sock.settimeout(_remaining(self.deadline, timeout))
            sent = self.sock.send(buffer)
            if not sent: raise httpcore.WriteError('business data stream closed')
            buffer = buffer[sent:]

    def close(self): self.sock.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        if server_hostname != self.host or not ssl_context.check_hostname or ssl_context.verify_mode != ssl.CERT_REQUIRED:
            self.close(); raise httpcore.ConnectError('business data TLS identity')
        try:
            self.sock.settimeout(_remaining(self.deadline, timeout))
            wrapped = ssl_context.wrap_socket(self.sock, server_hostname=self.host)
            return _PinnedStream(wrapped, self.host, self.deadline)
        except BaseException:
            self.close(); raise

    def get_extra_info(self, info):
        if info == 'server_addr': return self.sock.getpeername()
        if info == 'client_addr': return self.sock.getsockname()
        if info == 'socket': return self.sock
        if info == 'ssl_object' and isinstance(self.sock, ssl.SSLSocket): return self.sock
        return None


class _PinnedBackend(httpcore.NetworkBackend):
    def __init__(self, host, port, addresses, deadline, before_request=lambda: None):
        self.host, self.port, self.addresses, self.deadline = host, port, addresses, deadline
        self.before_request = before_request

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.host or port != self.port or local_address is not None:
            raise httpcore.ConnectError('business data origin changed')
        for family, sockaddr in self.addresses:
            self.before_request()  # Recheck after DNS, immediately before socket I/O.
            _remaining(self.deadline)
            sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            try:
                sock.settimeout(_remaining(self.deadline, min(timeout or 5, 5)))
                for option in socket_options or (): sock.setsockopt(*option)
                sock.connect(sockaddr)  # numeric address; no hostname resolution here.
                if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(sockaddr[0]):
                    raise httpcore.ConnectError('business data peer changed')
                return _PinnedStream(sock, self.host, self.deadline)
            except (OSError, httpcore.NetworkError): sock.close()
            except BaseException:
                sock.close(); raise
        raise httpcore.ConnectError('business data connect failed')

    def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError('business data UNIX sockets forbidden')


def _resolve(execution, deadline):
    """Bound DNS/legacy endpoint validation without an unbounded worker queue."""
    _need(_DNS_SLOTS.acquire(blocking=False), '地址解析暂忙，请稍后按原请求重试')
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            from .gateway import validate_endpoint
            endpoint = validate_endpoint(execution)
            parsed = urlsplit(endpoint); host = parsed.hostname; port = parsed.port or 443
            allow_loopback = host == '127.0.0.1'  # Only exact dev allowlist can pass validator.
            addresses = []
            for family, socktype, protocol, _canon, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
                address = ipaddress.ip_address(sockaddr[0])
                _need(family in {socket.AF_INET, socket.AF_INET6} and socktype == socket.SOCK_STREAM
                    and (address.is_global or allow_loopback and str(address) == '127.0.0.1'), '来源地址未通过网络访问范围核对')
                pair = (family, sockaddr)
                if pair not in addresses: addresses.append(pair)
            _need(bool(addresses), '来源地址无法解析')
            result.put((endpoint, host, port, addresses))
        except Exception:
            result.put(None)
        finally: _DNS_SLOTS.release()
    try: threading.Thread(target=resolve, name='business-data-dns', daemon=True).start()
    except BaseException:
        _DNS_SLOTS.release(); raise
    try: answer = result.get(timeout=_remaining(deadline))
    except queue.Empty: raise ReaderError('来源地址解析超时，未发送HTTP请求') from None
    _need(answer is not None, '来源地址未通过访问范围核对，未发送HTTP请求')
    return answer


def fetch_json(execution, *, before_request=lambda: None, on_response=None) -> FetchResult:
    _need(type(execution) is dict and set(execution) <= {'endpoint', 'secret_ref'}
        and type(execution.get('endpoint')) is str
        and all(0x21 <= ord(char) <= 0x7e for char in execution['endpoint']), '业务数据连接配置无效')
    reference = execution.get('secret_ref')
    _need(reference is None or type(reference) is str and re.fullmatch(r'GA_CONNECTOR_SECRET_[A-Za-z0-9_]{1,100}', reference),
        '凭据只能使用服务端已登记的密钥引用，不能填写密码或任意请求头')
    deadline = time.monotonic() + HTTP_TIMEOUT_SECONDS
    before_request()  # Caller checks current authority before any DNS or HTTP I/O.
    try:
        _remaining(deadline)
        endpoint, host, port, addresses = _resolve(execution, deadline)
        from .connectors import headers
        request_headers = {**headers(execution), 'Accept': 'application/json, application/geo+json',
            'Accept-Encoding': 'identity', 'User-Agent': 'generic-agents-business-data/204'}
        backend = _PinnedBackend(host, port, addresses, deadline, before_request)
        with httpcore.ConnectionPool(network_backend=backend, ssl_context=ssl.create_default_context(),
                max_connections=1, max_keepalive_connections=0, retries=0, http2=False) as pool:
            with pool.stream('GET', endpoint, headers=request_headers,
                    extensions={'timeout': {name: _remaining(deadline) for name in ('connect', 'read', 'write', 'pool')}}) as response:
                _need(response.status == 200, '来源未返回完整成功响应，未将跳转、分页或空响应记为成功')
                values = {}
                for name, value in response.headers:
                    key = name.decode('ascii').lower()
                    values.setdefault(key, []).append(value.decode('latin-1'))
                _need(len(values.get('content-type', [])) == 1, '来源响应缺少唯一内容类型')
                media_type = values['content-type'][0].split(';', 1)[0].strip().lower()
                _need(media_type in {'application/json', 'application/geo+json'}, '来源不是JSON或GeoJSON，原响应未作为业务数据采用')
                _need(not values.get('content-encoding') or values['content-encoding'] == ['identity'], '来源响应使用未授权压缩编码，未展开读取')
                _need(len(values.get('content-length', [])) <= 1, '来源响应长度声明不一致')
                declared = values.get('content-length', [None])[0]
                if declared is not None:
                    _need(declared.isdigit() and int(declared) <= MAX_HTTP_BYTES, '来源响应超过1MiB或长度声明无效')
                content = bytearray()
                for chunk in response.iter_stream():
                    _remaining(deadline)
                    _need(len(content) + len(chunk) <= MAX_HTTP_BYTES, '来源响应超过1MiB，未保存截断快照')
                    content.extend(chunk)
                _remaining(deadline)
                _need(declared is None or int(declared) == len(content), '来源响应未完整接收，未保存部分快照')
                safe = {'status_code': response.status, 'content_type': media_type}
                for key in ('etag', 'last-modified'):
                    entries = values.get(key, [])
                    if len(entries) == 1 and len(entries[0]) <= 1024 and all(ord(c) >= 0x20 and ord(c) != 0x7f for c in entries[0]):
                        safe[key] = entries[0]
        fetched = FetchResult(bytes(content), media_type, safe, datetime.now(timezone.utc).isoformat())
    except ReaderError: raise
    except Exception:
        raise ReaderError('业务数据读取未完成，请核对来源与访问配置后按原请求重试') from None
    if on_response is not None: on_response(fetched.content, dict(fetched.headers))
    return fetched

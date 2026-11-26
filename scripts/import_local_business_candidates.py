#!/usr/bin/env python3
"""Append a private candidate bundle to an authenticated local workspace.

Credentials arrive on an already-open file descriptor, never in argv or the
environment. The importer does not upload a document or call a model.
"""
import argparse
import copy
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

MAX_FILE = 2 * 1024 * 1024
MAX_REPLY = 8 * 1024 * 1024
MAX_CREDENTIALS = 16 * 1024
MAX_MATERIAL_PAGES = 20
PAGE_SIZE = 500


class ImportStopped(Exception):
    pass


class UncertainWrite(Exception):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _json(data):
    return json.loads(data, object_pairs_hook=_pairs,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError('invalid number')))


def _origin(value):
    try:
        if (not isinstance(value, str) or not value.isascii()
                or any(ord(ch) <= 32 or ord(ch) == 127 for ch in value)):
            raise ValueError('invalid origin')
        parts = urlsplit(value)
        port = parts.port
        if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost')
                or parts.username is not None or parts.password is not None
                or not port or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError('invalid origin')
        # Never resolve a hostname or use proxy environment variables.
        return port
    except (TypeError, ValueError):
        raise ImportStopped('服务地址必须是明确端口的本机 HTTP 地址。') from None


def _read_private(path):
    value = Path(path)
    if not value.is_absolute() or value.suffix.lower() != '.json':
        raise ImportStopped('请选择绝对路径的私有候选文件。')
    try:
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > MAX_FILE):
                raise ValueError('unsafe file')
            with os.fdopen(descriptor, 'rb', closefd=False) as file:
                data = file.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ValueError('file too large')
            value = _json(data)
        finally:
            os.close(descriptor)
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ImportStopped('候选文件无法安全读取，请核对文件权限和格式。') from None
    if (not isinstance(value, dict) or value.get('schema') != 'setup-business-candidates-v1'
            or value.get('origin') != 'local_summary' or not isinstance(value.get('material'), dict)
            or not isinstance(value.get('candidates'), list) or not value['candidates']):
        raise ImportStopped('候选文件格式不完整，未进行导入。')
    material = value['material']
    if (material.get('id') is not None or material.get('version') is not None
            or material.get('status') != 'needs_binding'
            or not isinstance(material.get('hash'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', material['hash'])):
        raise ImportStopped('候选资料绑定状态无法核对，未进行导入。')
    return value


def _credentials(fd):
    try:
        info = os.fstat(fd)
        if (info.st_uid != os.getuid() or not (stat.S_ISREG(info.st_mode) or stat.S_ISFIFO(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) & 0o077)):
            raise ValueError('unsafe descriptor')
        data = bytearray()
        while len(data) <= MAX_CREDENTIALS:
            piece = os.read(fd, min(4096, MAX_CREDENTIALS + 1 - len(data)))
            if not piece:
                break
            data.extend(piece)
        if len(data) > MAX_CREDENTIALS:
            raise ValueError('credentials too large')
        value = _json(bytes(data))
        if not isinstance(value, dict):
            raise ValueError('invalid credentials')
        for key in ('cookie', 'csrf_token', 'workspace_id', 'user_id'):
            item = value.get(key)
            if (not isinstance(item, str) or not item or len(item) > 4096
                    or not item.isascii() or any(ord(ch) < 32 or ord(ch) == 127 for ch in item)):
                raise ValueError('invalid credentials')
        for key in ('workspace_id', 'user_id'):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value[key]):
                raise ValueError('invalid identity')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ImportStopped('会话信息不可用，请重新建立安全的本机会话。') from None


class LocalAPI:
    def __init__(self, port, credentials, timeout=30):
        self.port, self.credentials, self.timeout = port, credentials, timeout

    def request(self, method, path, body=None):
        headers = {'Cookie': self.credentials['cookie'], 'X-CSRF-Token': self.credentials['csrf_token'],
                   'X-Workspace-ID': self.credentials['workspace_id'], 'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_REPLY + 1)
            if len(raw) > MAX_REPLY:
                raise ValueError('oversized response')
            if not 200 <= response.status < 300:
                return response.status, None
            return response.status, _json(raw)
        finally:
            connection.close()


def _require_ok(api, method, path, body=None):
    try:
        status, value = api.request(method, path, body)
        if not 200 <= status < 300:
            raise ImportStopped('服务暂不可用或权限不足，未提交候选。')
        return value
    except (OSError, ValueError, TypeError, http.client.HTTPException, RecursionError):
        raise ImportStopped('无法核对本地服务，请确认连接后再操作。') from None


def _identity(api, credentials):
    row = _require_ok(api, 'GET', '/api/me')
    if (not isinstance(row, dict) or row.get('workspace_id') != credentials['workspace_id']
            or row.get('user_id') != credentials['user_id'] or row.get('role') not in ('admin', 'operator')):
        raise ImportStopped('当前账号或工作区与会话不一致，未提交候选。')


def _find_material(api, material):
    matches = []
    seen = set()
    for page in range(MAX_MATERIAL_PAGES):
        rows = _require_ok(api, 'GET', f'/api/setup/materials?offset={page * PAGE_SIZE}&limit={PAGE_SIZE}')
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ImportStopped('资料清单无法核对，未提交候选。')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in seen:
                raise ImportStopped('资料清单发生变化，请重新核对。')
            seen.add(row['id'])
            if row.get('hash') == material['hash'] and row.get('archived') is False:
                matches.append(row)
        if len(rows) < PAGE_SIZE:
            break
    else:
        raise ImportStopped('资料清单过多，暂无法完整核对。')
    if len(matches) != 1:
        raise ImportStopped('未找到唯一匹配且未归档的报告，请先核对资料。')
    match = matches[0]
    if (type(match.get('version')) is not int or match['version'] < 1
            or match.get('status') not in ('parsed', 'needs_input')
            or match.get('engine') != material.get('engine')):
        raise ImportStopped('报告版本或解析状态暂不可用，未提交候选。')
    return {key: match[key] for key in ('id', 'version', 'hash', 'engine', 'status')}


def _valid_draft(value):
    return (isinstance(value, dict) and type(value.get('revision')) is int and value['revision'] >= 1
            and isinstance(value.get('configuration'), dict))


def import_candidates(api, credentials, candidate):
    _identity(api, credentials)
    bundle = copy.deepcopy(candidate)
    if (not bundle['candidates'] or any(not isinstance(item, dict) or not isinstance(item.get('key'), str)
            or not item['key'] for item in bundle['candidates'])
            or len({item['key'] for item in bundle['candidates']}) != len(bundle['candidates'])):
        raise ImportStopped('候选业务无法核对，未提交候选。')
    bundle['material'] = _find_material(api, bundle['material'])
    preview = _require_ok(api, 'POST', '/api/setup/candidates/preview', bundle)
    if (not isinstance(preview, dict) or preview.get('material') != bundle['material']
            or not isinstance(preview.get('candidates'), list)
            or len(preview['candidates']) != len(bundle['candidates'])
            or any(not isinstance(item, dict) for item in preview['candidates'])
            or [item.get('key') for item in preview['candidates']] != [item['key'] for item in bundle['candidates']]):
        raise ImportStopped('候选预览结果无法核对，未提交候选。')
    _identity(api, credentials)
    draft = _require_ok(api, 'GET', '/api/setup/draft')
    if not _valid_draft(draft):
        raise ImportStopped('工作区草稿无法核对，未提交候选。')
    if draft['configuration'].get('businessCandidates') is not None:
        raise ImportStopped('已有业务候选，请先在配置中心核对。')
    try:
        status, saved = api.request('POST', '/api/setup/candidates',
                                    {'revision': draft['revision'], 'bundle': bundle})
        if status in (401, 403, 409, 422):
            raise ImportStopped('草稿或资料已变化，未覆盖现有内容；请刷新核对。')
        if not 200 <= status < 300 or not _valid_draft(saved):
            raise UncertainWrite()
        if (saved['revision'] != draft['revision'] + 1
                or saved['configuration'].get('businessCandidates') != bundle):
            raise UncertainWrite()
        previous = dict(draft['configuration']); previous.pop('businessCandidates', None)
        current = dict(saved['configuration']); current.pop('businessCandidates', None)
        initial = {'version': 1, 'resources': [], 'connections': [], 'materials': [],
                   'instanceProfile': {}, 'businesses': [], 'flows': {}}
        initialized_blank = previous == {} and current == initial
        if not initialized_blank and json.dumps(previous, ensure_ascii=False, sort_keys=True, allow_nan=False) != json.dumps(
                current, ensure_ascii=False, sort_keys=True, allow_nan=False):
            raise UncertainWrite()
        return '候选已保存到当前工作区，等待确认。'
    except ImportStopped:
        raise
    except (OSError, ValueError, TypeError, http.client.HTTPException, RecursionError, UncertainWrite):
        # A matching readback is useful to a human but cannot establish which
        # request wrote it; never replay or claim success from this observation.
        note = '暂无法从工作区核对。'
        try:
            observed = _require_ok(api, 'GET', '/api/setup/draft')
            if _valid_draft(observed):
                if (observed['revision'] > draft['revision']
                        and observed['configuration'].get('businessCandidates') == bundle):
                    note = '工作区已显示相同候选，但无法确认是否由本次请求保存。'
                else:
                    note = '工作区当前状态未能证明本次保存。'
        except ImportStopped:
            pass
        raise ImportStopped('保存结果尚未确认，未重复提交。' + note + '请在配置中心核对。') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='将本机业务候选保存为工作区待确认草案')
    parser.add_argument('--origin', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--credentials-fd', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        port = _origin(args.origin)
        candidate = _read_private(args.candidate)
        credentials = _credentials(args.credentials_fd)
        print(import_candidates(LocalAPI(port, credentials), credentials, candidate))
        return 0
    except ImportStopped as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        # Do not echo request data, file paths, credentials or server details.
        print('处理未完成，请在配置中心核对原候选；未重复提交。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

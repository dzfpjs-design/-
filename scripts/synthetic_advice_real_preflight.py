"""Prepare-only envelope for a future isolated synthetic DeepSeek test.

This module cannot dispatch, resolve DNS, read credentials or open a database.
It is not a production authorization, an accounting ledger, or evidence that
data is synthetic. Future integration must validate those facts separately.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

PROVIDER_HOST='api.deepseek.com'
SCHEMA='synthetic-deepseek-preflight-v1'


def _uuid(value):
    if type(value) is not str or str(UUID(value))!=value:
        raise ValueError('invalid synthetic identifier')
    return value


def _hash(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('invalid immutable hash')
    return value


def _public_ipv4(value):
    address=ipaddress.ip_address(value)
    if address.version!=4 or not address.is_global or address.is_multicast:
        raise ValueError('egress destination must be public IPv4')
    return str(address)


def verify_dns_answers(answers, approved_ips):
    """Check supplied answers; no DNS query is performed here."""
    if not answers or not approved_ips: raise ValueError('empty provider address set')
    approved={_public_ipv4(value) for value in approved_ips}
    resolved={_public_ipv4(value) for value in answers}
    if not resolved <= approved: raise ValueError('provider resolved outside approved egress')
    return tuple(sorted(resolved))


def sandbox_profile(pg_port, approved_ips, *, live=False):
    """Offline mock policy; live destination filtering needs another runtime.

    macOS sandbox-exec rejects external IP literals for network addresses, so
    generating a policy that purports to pin one would be misleading.
    """
    if type(pg_port) is not int or not 1 <= pg_port <= 65535: raise ValueError('invalid test PG port')
    ips=sorted({_public_ipv4(value) for value in approved_ips})
    if not ips: raise ValueError('empty provider address set')
    if live:raise RuntimeError('live fixed-IP egress unavailable with macOS sandbox-exec')
    return (f'(version 1) (allow default) (deny network*) '
        f'(allow network-outbound (remote ip "localhost:{pg_port}"))')


def _root(path):
    if not isinstance(path,Path) or not path.is_absolute() or path.is_symlink(): raise ValueError('invalid root')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    info=os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        os.close(fd);raise ValueError('root must be private')
    return fd


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def _write_new(root,name,value):
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=root)
    try:
        with os.fdopen(fd,'wb',closefd=False) as out:
            out.write(_json(value));out.flush();os.fsync(fd)
    finally:os.close(fd)
    os.fsync(root)


def _read(root,name):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=root)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>8192:
            raise ValueError('invalid preflight record')
        with os.fdopen(fd,'rb',closefd=False) as inp:return json.load(inp)
    finally:os.close(fd)


def _validate(value):
    fields={'schema','run_id','workspace_id','task_id','plan_id','call_id','request_id',
        'source_hash','model_hash','model','provider_host','approved_ips'}
    if type(value) is not dict or set(value)!=fields or value['schema']!=SCHEMA:
        raise ValueError('invalid preflight schema')
    for key in ('run_id','workspace_id','task_id','plan_id','call_id','request_id'):_uuid(value[key])
    for key in ('source_hash','model_hash'):_hash(value[key])
    if value['provider_host']!=PROVIDER_HOST or type(value['model']) is not str or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',value['model']):
        raise ValueError('invalid model or provider')
    ips=value['approved_ips']
    if type(ips) is not list or not ips or len(ips)>16 or sorted(set(_public_ipv4(i) for i in ips))!=ips:
        raise ValueError('invalid approved destinations')
    return value


def prepare(root_path, record):
    """Persist immutable metadata without credentials, source content or send."""
    record=_validate(record);root=_root(root_path)
    try:_write_new(root,'preflight.json',record)
    finally:os.close(root)
    return {'prepared':True,'dispatch_authorized':False}


def claim_attempt(root_path, request_id):
    """Fail closed after one claim, including when no send can be proved.

    This file is an extra local fence, not a replacement for the real DB's
    original lease, authorization, unique intent, permit or usage records.
    """
    _uuid(request_id);root=_root(root_path)
    try:
        record=_validate(_read(root,'preflight.json'))
        if record['request_id']!=request_id:raise ValueError('original request does not match')
        _write_new(root,'one-attempt.json',{'schema':SCHEMA,'request_id':request_id,
            'preflight_sha256':hashlib.sha256(_json(record)).hexdigest(),'outcome':'unknown'})
    finally:os.close(root)
    return {'attempt_claimed':True,'dispatch_authorized':False,'retry_authorized':False}

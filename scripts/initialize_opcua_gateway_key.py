"""Initialize the separate OPC UA vault key only before any saved trust exists."""
from pathlib import Path
import hashlib,os
from cryptography.fernet import Fernet
from sqlalchemy import select,text
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.managed_opcua_grants import _KIND,_KEY_MARKER


def main():
    raw=os.getenv('GA_OPCUA_VAULT_KEY_FILE','')
    if not raw or not Path(raw).is_absolute():raise RuntimeError('请设置绝对路径 GA_OPCUA_VAULT_KEY_FILE')
    path=Path(raw);parent=path.parent.resolve(strict=True)
    if parent.is_relative_to(Path(settings().storage_dir).resolve()):raise RuntimeError('密钥须位于证据存储目录之外')
    if path.exists() or path.is_symlink():raise RuntimeError('密钥路径已有文件；不会覆盖')
    with engine().begin() as db:
        if db.dialect.name!='postgresql':raise RuntimeError('密钥初始化需要 PostgreSQL 事务锁')
        number=int.from_bytes(hashlib.sha256(b'managed-opcua-key-identity').digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':number})
        if db.scalar(select(Job.id).where(Job.kind.in_([_KIND,_KEY_MARKER])).limit(1)):
            raise RuntimeError('已有加密授权或密钥身份；请恢复原密钥，不得重新初始化')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as output:output.write(Fernet.generate_key());output.flush();os.fsync(output.fileno())
        dfd=os.open(parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    print('已初始化独立 OPC UA 密钥；请按运维策略备份。')

if __name__=='__main__':main()

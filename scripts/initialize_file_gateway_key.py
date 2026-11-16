"""Initialize a new file-gateway key only before any encrypted grants exist.

Run as the backend service account with the normal database and storage
settings and GA_FILE_GATEWAY_KEY_FILE set to an absolute path outside storage.
Never use this to recover a lost or replaced key; restore the original backup.
"""
from pathlib import Path
import os,stat
from cryptography.fernet import Fernet
from sqlalchemy import select,text
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.managed_file_grants import SHARED_KEY_ENCRYPTED_KINDS,KEY_MARKER


def main():
    value=os.getenv('GA_FILE_GATEWAY_KEY_FILE','')
    if not value or not Path(value).is_absolute():raise RuntimeError('必须提供绝对密钥路径')
    path=Path(value);parent=path.parent.resolve(strict=True)
    if parent.is_relative_to(Path(settings().storage_dir).resolve()):raise RuntimeError('密钥路径必须位于资料存储目录之外')
    if path.exists() or path.is_symlink():raise RuntimeError('密钥路径已有文件；不会覆盖')
    with engine().begin() as db:
        if db.dialect.name!='postgresql':raise RuntimeError('密钥初始化需要 PostgreSQL 事务锁')
        import hashlib
        n=int.from_bytes(hashlib.sha256(b'managed-file-key-identity').digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:n)'),{'n':n})
        if db.scalar(select(Job.id).where(Job.kind.in_([*SHARED_KEY_ENCRYPTED_KINDS,KEY_MARKER])).limit(1)):
            raise RuntimeError('已有加密授权或密钥身份；请恢复原密钥，不得重新初始化')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as output:
            output.write(Fernet.generate_key());output.flush();os.fsync(output.fileno())
        dfd=os.open(parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    print('已初始化独立文件网关密钥；请按运维策略备份该文件。')


if __name__=='__main__':main()

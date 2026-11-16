"""Shared administrator creation inside a caller-owned transaction."""
import base64
import binascii
import re
from uuid import UUID

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from sqlalchemy import select

from .models import Membership, User, Workspace
from .security import hasher


def _name(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label}不能为空且不能超过{limit}个字符")
    return value.strip()


def _optional_uuid(value, label):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{label}须为标准小写 UUID") from None
    return value


def _encoded_password(value):
    """Validate PHC structure without guessing or verifying any cleartext.

    check_needs_rehash parses Argon2 parameters but does not validate the base64
    payload or positive parameter bounds. Check those too, without running an
    attacker-selected memory/time cost. A valid older cost remains acceptable.
    """
    try:
        if not isinstance(value, str):
            raise ValueError
        hasher.check_needs_rehash(value)
        parameters = extract_parameters(value)
        parts = value.split('$')
        if (len(parts) != 6 or parts[0] or parts[1] not in {'argon2id', 'argon2i', 'argon2d'}
                or not re.fullmatch(r'v=\d+', parts[2])
                or not re.fullmatch(r'm=\d+,t=\d+,p=\d+', parts[3])
                or parameters.version not in {16, 19}
                or not 1 <= parameters.time_cost <= 2**32 - 1
                or not 1 <= parameters.parallelism <= 2**24 - 1
                or not 8 * parameters.parallelism <= parameters.memory_cost <= 2**32 - 1):
            raise ValueError
        for encoded, minimum in ((parts[4], 8), (parts[5], 4)):
            decoded = base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True)
            if (len(decoded) < minimum or len(decoded) > 2**32 - 1
                    or base64.b64encode(decoded).decode('ascii').rstrip('=') != encoded):
                raise ValueError
    except (InvalidHashError, ValueError, TypeError, UnicodeError, binascii.Error):
        raise ValueError("密码必须是有效的 Argon2 编码摘要，不能传入明文") from None
    return value


def create_administrator(db, *, username, workspace_name, encoded_password,
                         user_id=None, workspace_id=None):
    """Stage one new administrator, workspace and admin membership; never commit.

    The caller owns commit/rollback, including a concurrent unique-key failure.
    This function performs no schema changes and never overwrites an account.
    """
    username = _name(username, '管理员用户名', 100)
    workspace_name = _name(workspace_name, '工作区名称', 200)
    user_id = _optional_uuid(user_id, '管理员编号')
    workspace_id = _optional_uuid(workspace_id, '工作区编号')
    encoded_password = _encoded_password(encoded_password)
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise ValueError("账户已存在，不覆盖")
    user = User(username=username, password_hash=encoded_password)
    workspace = Workspace(name=workspace_name)
    if user_id is not None:
        user.id = user_id
    if workspace_id is not None:
        workspace.id = workspace_id
    db.add_all([user, workspace])
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role='admin'))
    db.flush()
    return {'user_id': user.id, 'workspace_id': workspace.id}

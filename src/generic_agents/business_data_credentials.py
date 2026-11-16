"""Server-owned credential destinations; workspace users never grant themselves access."""
import json
import re
from fastapi import HTTPException
from .config import settings


def require_grant(workspace_id, endpoint, secret_ref):
    if secret_ref is None:
        return
    message = '管理员尚未授权当前工作区对此接口使用该凭证，请核对服务器认证设置'
    try:
        raw = settings().business_data_access_grants
        if not isinstance(raw, str) or len(raw) > 128 * 1024:
            raise ValueError()
        def unique(items):
            value = {}
            for key, item in items:
                if key in value: raise ValueError()
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique)
        if (type(value) is not dict or set(value) != {'schema', 'grants'}
                or value['schema'] != 'business-data-access-grants-v204'
                or type(value['grants']) is not list or len(value['grants']) > 128):
            raise ValueError()
        for grant in value['grants']:
            if (type(grant) is not dict or set(grant) != {'workspace_id', 'endpoint', 'secret_ref'}
                    or any(type(grant[key]) is not str for key in grant)
                    or not re.fullmatch(r'GA_CONNECTOR_SECRET_[A-Z0-9_]{1,100}', grant['secret_ref'])):
                raise ValueError()
        if {'workspace_id': workspace_id, 'endpoint': endpoint, 'secret_ref': secret_ref} in value['grants']:
            return
    except (ValueError, TypeError, KeyError):
        pass
    raise HTTPException(409, message)

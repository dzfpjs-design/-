"""Bounded HTTP/MCP transport. Tool content never creates permissions."""
import json
import os
import time
from uuid import uuid4
from urllib.parse import urlparse
import httpx
from fastapi import HTTPException
from .contracts import digest

LIMIT = 1024 * 1024


def headers(execution):
    from .device_gateway_http_v269 import reject_legacy
    reject_legacy(execution)
    from .device_gateway_source_http_v270 import reject_legacy as reject_source
    reject_source(execution)
    result = {}
    reference = execution.get('secret_ref')
    if reference:
        if not isinstance(reference, str) or not reference.startswith('GA_CONNECTOR_SECRET_'):
            raise HTTPException(422, '工具凭证只能引用GA_CONNECTOR_SECRET_环境变量')
        value = os.getenv(reference)
        if not value:
            raise HTTPException(409, '工具服务凭证未配置')
        result['Authorization'] = 'Bearer ' + value
    return result


def bounded_response(client, method, url, *, rpc_id=None, on_response=None, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        started=time.monotonic()
        response.raise_for_status()
        metadata = dict(response.headers)
        if response.status_code in {202, 204}:
            return None, metadata
        content_type = response.headers.get('content-type', '').split(';')[0]
        if content_type == 'text/event-stream':
            pending = []; total = 0; buffer=bytearray()
            for chunk in response.iter_bytes():
                total += len(chunk);buffer.extend(chunk)
                if total > LIMIT or time.monotonic()-started > 30:
                    raise ValueError('MCP stream exceeds bounds')
                while b'\n' in buffer:
                    raw,_,remaining=buffer.partition(b'\n');buffer=bytearray(remaining)
                    line=raw.rstrip(b'\r').decode('utf-8')
                    if line.startswith('data:'):pending.append(line[5:].lstrip())
                    elif not line and pending:
                        payload='\n'.join(pending);pending=[]
                        if not payload.strip():continue
                        value=json.loads(payload)
                        if value.get('id')==rpc_id:return value,metadata
                        if value.get('method') and 'id' in value:
                            raise ValueError('Server initiated requests are not supported by this connector')
            raise ValueError('MCP response missing')
        if content_type != 'application/json':
            raise ValueError('Connector must return JSON or MCP SSE')
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > LIMIT or time.monotonic()-started>30:
                raise ValueError('Connector response exceeds bounds')
        if on_response is not None:
            on_response(bytes(content), {'status_code':response.status_code,
                'content_type':content_type})
        return json.loads(content), metadata


class MCPClient:
    def __init__(self, execution):
        from .gateway import validate_endpoint
        self.url = validate_endpoint(execution)
        self.execution = execution
        self.client = httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False)
        self.headers = {**headers(execution), 'Accept':'application/json, text/event-stream'}
        self.session = None

    def __enter__(self):
        try:
            value = self.rpc('initialize', {'protocolVersion':'2025-11-25', 'capabilities':{},
                'clientInfo':{'name':'generic-agents', 'version':'0.1.0'}})
            version = value.get('protocolVersion')
            if version not in {'2025-03-26','2025-06-18','2025-11-25'}:
                raise ValueError('Unsupported MCP protocol version')
            self.headers['MCP-Protocol-Version'] = version
            self.rpc('notifications/initialized', notification=True)
            return self
        except Exception:
            self.client.close(); raise

    def rpc(self, method, params=None, notification=False):
        request={'jsonrpc':'2.0','method':method}
        identifier=None if notification else uuid4().hex
        if identifier:request['id']=identifier
        if params is not None:request['params']=params
        value, response_headers=bounded_response(self.client,'POST',self.url,rpc_id=identifier,
            headers=self.headers,json=request)
        if method=='initialize' and response_headers.get('mcp-session-id'):
            session=response_headers['mcp-session-id']
            if len(session)>512 or any(not 0x21 <= ord(c) <= 0x7e for c in session):
                raise ValueError('Invalid MCP session')
            self.session=session;self.headers['MCP-Session-Id']=session
        if notification:return None
        if not isinstance(value,dict) or value.get('jsonrpc')!='2.0' or value.get('id')!=identifier or 'error' in value:
            raise ValueError('Invalid or failed MCP response')
        if not isinstance(value.get('result'),dict):raise ValueError('Invalid MCP result')
        return value['result']

    def check_tool(self, manifest):
        name=self.execution.get('tool_name');cursor=None
        for page in range(10):
            value=self.rpc('tools/list', {'cursor':cursor} if cursor else {})
            for tool in value.get('tools',[]):
                if tool.get('name')==name:
                    if digest(tool.get('inputSchema'))!=digest(manifest['input_schema']):
                        raise HTTPException(409,'MCP工具输入契约已变化，需要注册新版本')
                    if tool.get('outputSchema') and digest(tool['outputSchema'])!=digest(manifest['output_schema']):
                        raise HTTPException(409,'MCP工具输出契约与登记版本不一致')
                    return
            cursor=value.get('nextCursor')
            if not cursor:break
        raise HTTPException(409,'MCP服务未提供登记工具')

    def call(self, manifest, inputs, *, before_call=None):
        self.check_tool(manifest)
        if before_call is not None:before_call()
        result=self.rpc('tools/call',{'name':self.execution['tool_name'],'arguments':inputs})
        if result.get('isError'):
            raise ValueError('MCP tool reports failure')
        if isinstance(result.get('structuredContent'),dict):return result['structuredContent']
        texts=[c['text'] for c in result.get('content',[]) if c.get('type')=='text']
        if len(texts)!=1:raise ValueError('MCP output lacks structured JSON')
        return json.loads(texts[0])

    def __exit__(self,*args):
        if self.session:
            try:self.client.delete(self.url,headers=self.headers)
            except httpx.HTTPError:pass
        self.client.close()


def call_http(execution, inputs, key, *, before_request=None):
    from .device_gateway_http_v269 import reject_legacy
    reject_legacy(execution)
    from .device_gateway_source_http_v270 import reject_legacy as reject_source
    reject_source(execution)
    from .gateway import validate_endpoint
    url=validate_endpoint(execution)
    if len(json.dumps(inputs).encode())>LIMIT:raise HTTPException(422,'工具输入超过单次上限')
    with httpx.Client(timeout=20,follow_redirects=False,trust_env=False) as client:
        request_headers={**headers(execution),'Idempotency-Key':key}
        if before_request is not None:before_request()
        value,_=bounded_response(client,'POST',url,headers=request_headers,json=inputs)
        return value


def query_receipt(execution,key,*,on_response=None):
    from .device_gateway_http_v269 import reject_legacy
    reject_legacy(execution)
    from .device_gateway_source_http_v270 import reject_legacy as reject_source
    reject_source(execution)
    from .gateway import validate_endpoint
    url=validate_endpoint({'endpoint':execution.get('receipt_endpoint','')})
    with httpx.Client(timeout=15,follow_redirects=False,trust_env=False) as client:
        value,_=bounded_response(client,'GET',url,on_response=on_response,
            headers={**headers(execution),'Idempotency-Key':key})
        return value

"""Opt-in real-provider HTTP proof for the disposable M42 browser fixture.

Never saves headers or keys. Validate a real request before it leaves; capturing
an allowed request does not replace either provider execution or UI acceptance.
"""
import json
import os
from pathlib import Path
import time

import httpx

from generic_agents.contracts import digest


def install():
    out = Path(os.environ['GA_NATIVE_SUGGESTION_PROBE_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    forbidden = json.loads(os.environ['GA_NATIVE_SUGGESTION_FORBIDDEN_IDS'])
    limit = int(os.getenv('GA_NATIVE_SUGGESTION_HTTP_LIMIT', '3'))
    assert 1 <= limit <= 3, 'Disposable HTTP limit must be explicitly bounded'
    original = httpx.Client.send
    endpoints = {'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                 'https://api.deepseek.com/chat/completions'}

    def send(client, request, *args, **kwargs):
        if request.method != 'POST' or str(request.url) not in endpoints:
            return original(client, request, *args, **kwargs)
        content = request.content.decode('utf-8')
        assert 'NMS_PRIVATE_' not in content, 'Private source marker blocked before cloud'
        assert all(item not in content for item in forbidden), 'Source identity blocked before cloud'
        value = json.loads(content)
        # Only body is captured. Authorization, cookies and database/key values
        # never enter any evidence artifact.
        assert set(value) <= {'model', 'messages', 'response_format', 'max_tokens',
                              'temperature', 'stream', 'enable_thinking', 'thinking'}
        count = len(list(out.glob('request-*.json')))
        assert count < limit, 'Disposable verification reached the frozen real-call limit'
        target = out / ('request-' + str(count + 1) + '.json')
        proof = {'request_hash': digest(value), 'request_body': value,
                 'private_source_markers_absent': True,
                 'actual_source_ids_absent': True, 'headers_not_saved': True,
                 'provider': 'qwen' if 'aliyuncs' in str(request.url) else 'deepseek',
                 'sent_to_real_provider': False}
        target.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
        started = time.monotonic()
        try:
            response = original(client, request, *args, **kwargs)
            proof['sent_to_real_provider'] = True
            proof['http_status'] = response.status_code
            # Usage is a receipt, not settled monetary cost or quality evidence.
            if response.is_success:
                data = response.json()
                proof['provider_usage'] = data.get('usage')
                if os.getenv('GA_NATIVE_SUGGESTION_CAPTURE_RESPONSE') == '1':
                    # Capture bounded final content only for the v2 comparison;
                    # never headers, credentials, or reasoning_content.
                    from hashlib import sha256
                    choices = data.get('choices', [])
                    choice = choices[0] if choices else {}
                    final_content = choice.get('message', {}).get('content')
                    proof['reported_model'] = data.get('model')
                    proof['finish_reason'] = choice.get('finish_reason')
                    if isinstance(final_content, str):
                        proof['response_content_sha256'] = sha256(final_content.encode()).hexdigest()
                        if len(final_content.encode()) <= 20000:
                            proof['response_final_content'] = final_content
                        else:
                            proof['response_content_not_saved_reason'] = 'exceeds_evidence_limit'
            return response
        except Exception as error:
            proof['transport_error_type'] = type(error).__name__
            proof['response_unknown'] = True
            raise
        finally:
            proof['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            target.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')

    httpx.Client.send = send


if __name__ == '__main__':
    assert os.getenv('GA_NATIVE_SUGGESTION_BROWSER') == '1'
    install()
    from generic_agents.cli import main
    main()

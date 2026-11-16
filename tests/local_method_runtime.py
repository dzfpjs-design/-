"""Validate the isolated runtime's effective no-cloud configuration, no mocks."""
import json
import os
from pathlib import Path
import sys
from generic_agents.config import settings


if __name__ == '__main__':
    assert not Path('.env').exists(), 'Isolated child must not load repository dotenv'
    config = settings()
    assert config.cloud_daily_call_limit == 0
    for secret in (config.dashscope_api_key, config.deepseek_api_key):
        assert secret is None or secret.get_secret_value() == ''
    out = Path(os.environ['GA_LOCAL_METHOD_PROOF_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    (out / ('runtime-config-' + sys.argv[1] + '-' + str(os.getpid()) + '.json')).write_text(
        json.dumps({'repository_dotenv_not_loaded': True,
                    'effective_provider_keys_empty': True,
                    'effective_cloud_daily_call_limit': 0,
                    'command': sys.argv[1]}, indent=2) + '\n')
    from generic_agents.cli import main
    main()

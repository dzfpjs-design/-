"""Preflight-only archives must never enter the maturity replay path."""
import os
import tempfile
from pathlib import Path

import pytest

from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db


def test_preflight_marker_rejected_before_database_access():
    with tempfile.TemporaryDirectory(prefix='advice-fresh-mature-checkpoint-') as directory:
        root = Path(directory).resolve()
        os.chmod(root, 0o700)
        (root / 'PREFLIGHT_ONLY').write_text('synthetic preflight')
        with pytest.raises(RuntimeError, match='synthetic checkpoint isolation check failed'):
            restore_to_disposable_test_db(str(root))

"""Run native candidate checks and preserve evidence only after every check passes."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_changer_checks', ROOT / 'tests/native/test_changer.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(checks))
if not result.wasSuccessful() or result.skipped or not hasattr(checks.GraphBoundary, 'proof'):
    print(json.dumps({'passed': False, 'tests': result.testsRun, 'skipped': len(result.skipped)}))
    sys.exit(1)
folder = ROOT / 'docs/evidence/changer-preparation-v06'
conversion = json.loads((ROOT / '.runtime/changer-candidate/conversion.json').read_text())
proof = checks.GraphBoundary.proof
if conversion['safetensors_sha256'] != proof['weights_sha256'] or not conversion['all_tensors_exact_readback']:
    raise ValueError('conversion and native graph evidence do not match')
for name, value in [('conversion.json', conversion), ('native-graph.json', proof),
                    ('native-checks.json', {'passed': True, 'tests': result.testsRun, 'skipped': 0,
                       'cloud_calls': 0, 'platform_enabled': False,
                       'graph_sha256': proof['graph_sha256']})]:
    (folder / name).write_text(json.dumps(value, indent=2) + '\n')
print(json.dumps({'passed': True, 'tests': result.testsRun, 'backend': 'cpu',
                  'strict_parameter_match': True, 'cloud_calls': 0, 'platform_enabled': False}))

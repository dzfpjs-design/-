"""Reuse the read-only disposable evidence collector with v2-only paths."""
from pathlib import Path
from tests import native_suggestion_evidence_snapshot as collector

ROOT=Path(__file__).resolve().parents[1]
collector.ACCESS=ROOT/'.runtime/native-suggestion-v2-browser-access.json'
collector.OUT=ROOT/'docs/evidence/native-suggestion-quality-v12/browser'

if __name__=='__main__':
    import sys
    collector.snapshot(sys.argv[1])

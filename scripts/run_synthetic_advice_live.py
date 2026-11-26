#!/usr/bin/env python3
"""Legacy entry point restricted to the offline in-process mock runner.

Caller-supplied checkpoint hashes prove integrity, not synthetic provenance.
Live dispatch must remain disabled until that provenance has an independent
trust boundary. Historical live records and their no-send recovery remain.
"""
import argparse
import sys

from scripts import run_synthetic_advice_mock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--pg-bin', required=True)
    parser.add_argument('--mode', choices=('fake', 'live'), default='fake')
    parser.add_argument('--credential-fd', type=int)
    args = parser.parse_args()
    if args.mode == 'live':
        raise RuntimeError('live mode disabled: synthetic checkpoint provenance is not independently verified')
    if args.credential_fd is not None:
        raise RuntimeError('fake mode accepts no credential')
    original = sys.argv
    try:
        sys.argv = [original[0], '--checkpoint', args.checkpoint, '--pg-bin', args.pg_bin]
        return run_synthetic_advice_mock.main()
    finally:
        sys.argv = original


if __name__ == '__main__':
    sys.exit(main())

"""Retired live flow: source provenance is not independently verifiable."""


def run_synthetic_mature_flow(*_args, **_kwargs):
    raise RuntimeError('synthetic live flow is disabled; use the in-process mock harness')


if __name__ == '__main__':
    run_synthetic_mature_flow()

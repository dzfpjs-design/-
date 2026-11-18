"""Versioned report jobs invisible to older generic worker claim queries.

Legacy 44c07f2 workers require available_at <= database time for every claim,
including unfiltered global workers. New delegated jobs carry a far-future
sentinel through queueing, execution, failure and explicit handoff. Only the
new explicitly job-id-scoped claim path may ignore the ordinary due-time rule.
The invariant assumes ordinary application writes preserve the sentinel; it
is not a substitute for a DB trigger against arbitrary external DB writers.
"""
from datetime import datetime, timezone

KIND = 'read_report_scoped_v1'
NOT_AVAILABLE = datetime(9999, 1, 1, tzinfo=timezone.utc)


def isolated(job):
    return bool(job is not None and job.kind == KIND and job.available_at == NOT_AVAILABLE)


def kind_for(row):
    from .report_reading_drafts import KIND as LEGACY_KIND
    return KIND if isinstance(row.request_id,str) and row.request_id.startswith('fl_') else LEGACY_KIND

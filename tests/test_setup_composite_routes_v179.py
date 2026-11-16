"""Actual assembled ASGI routes; no DB, document, model, or browser substitute."""
import pytest
from generic_agents.api import app


@pytest.mark.parametrize('path,method', [
    ('/api/setup/understanding/models', 'GET'),
    ('/api/setup/understanding/preview', 'POST'),
    ('/api/setup/draft/restore/preview', 'POST'),
    ('/api/setup/report-saved-map-designs/preview-indexed', 'POST'),
    ('/api/setup/report-saved-map-designs/start-indexed', 'POST'),
    ('/api/setup/report-saved-map-designs/{consumer_id}/review-source', 'POST'),
    ('/api/workspace/report-evidence-readings/{report_id}/object-revisions-v166', 'GET'),
    ('/api/workspace/report-evidence-readings/{report_id}/object-adoption-v169/commit', 'POST'),
])
def test_actual_late_registration_and_independent_routes_have_canonical_paths(path, method):
    # FastAPI includes nested router contexts lazily; its public OpenAPI
    # assembler resolves effective prefixes instead of inspecting raw branches.
    paths = app.openapi()['paths']
    assert path in paths and method.lower() in paths[path]


def test_late_routes_never_escape_the_prefix_and_child_prefix_is_not_duplicated():
    paths = set(app.openapi()['paths'])
    assert '/understanding/models' not in paths
    assert '/draft/restore/preview' not in paths
    assert not any('/api/setup/api/setup/' in path for path in paths)
    # Authentication and actual HTTP are independently checked in the owned
    # runtime, not replaced with an auth/DB spy inside this route test.

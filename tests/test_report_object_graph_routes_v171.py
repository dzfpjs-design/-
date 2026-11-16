"""Root isolated import/route contracts, with no lifespan, SQL or network.

Registration is necessary for a user journey, not source or execution authority.
Root executes with its own conftest and all network blocked.
"""
from generic_agents.api import app


def routes():
    # FastAPI may retain nested included routers instead of flattening app.routes.
    # Its public schema resolves those children and validates their contracts.
    return {(path, method.upper()) for path, value in app.openapi()['paths'].items()
        for method in value if method in {'get', 'post', 'put', 'patch', 'delete'}}


def test_new_direct_graph_routes_are_registered_without_replacing_old_routes():
    actual = routes()
    root = '/api/setup/full-semantic-object-graphs-v171/{identity}'
    for suffix, method in (('/preview', 'POST'), ('/adopt', 'POST'),
            ('/requests/{request_id}', 'GET'), ('/revisions/preview', 'POST'),
            ('/revisions/adopt', 'POST'), ('/revisions/requests/{request_id}', 'GET')):
        assert (root + suffix, method) in actual
    assert ('/api/setup/full-semantic-graphs-v4/{identity}/preview', 'POST') in actual
    assert ('/api/setup/full-semantic-graphs/{identity}/preview', 'POST') in actual


def test_new_wire_routes_use_the_same_handoff_first_order_and_explicit_family():
    actual = routes()
    root = '/api/setup/full-semantic-graph-wire/{identity}'
    for suffix, method in (('/editor-context', 'GET'), ('/preview', 'POST'),
            ('/adopt', 'POST'), ('/requests/{request_id}', 'GET'), ('/command-hash', 'POST'),
            ('/configuration', 'GET'), ('/revisions/preview', 'POST'),
            ('/revisions/adopt', 'POST'), ('/revisions/requests/{request_id}', 'GET')):
        assert (root + '/objects-v171' + suffix, method) in actual
        assert (root + '/v4' + suffix, method) in actual

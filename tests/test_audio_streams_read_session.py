"""GET uses the existing single READ ONLY RR snapshot; writers stay unchanged.

The actual concurrent native capture/read experiment is run separately by Root.
"""
from generic_agents import audio_stream_api as api
from generic_agents.db import session
from generic_agents.security import authenticated
from generic_agents.signal_dual_read_session import read_session
from generic_agents.snapshot_authentication_v141 import snapshot_authenticated


def test_all_audio_get_routes_share_read_only_consistent_snapshot_and_snapshot_authentication():
    routes = [route for route in api.router.routes if route.methods == {'GET'}]
    assert len(routes) == 13
    for route in routes:
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert snapshot_authenticated in calls and read_session in calls, route.path
        assert session not in calls and authenticated not in calls, route.path


def test_audio_posts_retain_normal_writer_session_and_current_authenticated_actor():
    routes = [route for route in api.router.routes if route.methods == {'POST'}]
    assert len(routes) == 4
    for route in routes:
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert authenticated in calls and session in calls, route.path
        assert read_session not in calls and snapshot_authenticated not in calls, route.path

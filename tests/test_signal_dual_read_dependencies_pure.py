"""Run the actual production dependency assertions without a database fixture."""
import socket
import subprocess

from sqlalchemy.engine import Engine

from tests import test_signal_dual_read_consistency as dependency_checks


def test_production_read_and_write_dependencies(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('dependency inspection must not use SQL, network or processes')
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    dependency_checks.test_route_dependencies_keep_posts_writable_and_only_task_detail_changes()

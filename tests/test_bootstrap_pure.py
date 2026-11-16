"""Shared bootstrap/CLI contracts, using only a fake caller transaction.

Run with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and --noconftest. No database,
subprocess, network, installer, or real CLI bootstrap is executed.
"""
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

from argon2 import PasswordHasher
import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from generic_agents import bootstrap, cli, db as database
from generic_agents.models import Membership, User, Workspace
from generic_agents.security import password_hash


ROOT = Path(__file__).resolve().parents[1]
USER_ID = '5d03245b-133d-4e76-83aa-019a7ce0b8ce'
WORKSPACE_ID = 'ad6158d5-cb14-417a-a5af-e98065df1fde'


@pytest.fixture(autouse=True)
def prohibit_real_runtime(monkeypatch):
    assert Path(bootstrap.__file__).resolve() == ROOT / 'src/generic_agents/bootstrap.py'
    assert Path(cli.__file__).resolve() == ROOT / 'src/generic_agents/cli.py'

    def forbidden(*args, **kwargs):
        pytest.fail('Real database/network/subprocess action is forbidden')

    monkeypatch.setattr(database, 'create_engine', forbidden)
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(cli, 'engine', forbidden)
    monkeypatch.setattr(cli, 'Session', forbidden)
    monkeypatch.setattr(cli, 'migrate', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)


@pytest.fixture(scope='module')
def encoded():
    # Synthetic CPU-only hash; no real account or service credential.
    return password_hash('synthetic-bootstrap-test-password')


class FakeDB:
    def __init__(self, *, duplicate=None, fail_flush=None, fail_commit=False):
        self.duplicate = duplicate
        self.fail_flush = fail_flush
        self.fail_commit = fail_commit
        self.pending = []
        self.committed = []
        self.events = []
        self.statements = []
        self.flushes = self.commits = self.rollbacks = 0

    def scalar(self, statement):
        self.statements.append(statement)
        self.events.append('lookup')
        return self.duplicate

    def add_all(self, rows):
        self.events.append('add_user_and_workspace')
        self.pending.extend(rows)

    def add(self, row):
        self.events.append('add_membership')
        self.pending.append(row)

    def flush(self):
        self.flushes += 1
        self.events.append('flush')
        if self.flushes == self.fail_flush:
            raise RuntimeError('synthetic flush failure')
        for row in self.pending:
            if row.id is None:
                row.id = str(uuid4())

    def commit(self):
        self.events.append('commit')
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError('synthetic commit failure')
        self.committed.extend(self.pending)
        self.pending.clear()

    def rollback(self):
        self.events.append('rollback')
        self.rollbacks += 1
        self.pending.clear()

    def __enter__(self):
        self.events.append('enter')
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None or self.pending:
            self.rollback()
        self.events.append('exit')
        return False


def create(db, encoded, **patch):
    return bootstrap.create_administrator(db, **{
        'username': '管理员甲', 'workspace_name': '资料核查工作区',
        'encoded_password': encoded, **patch,
    })


def test_helper_stages_exact_three_rows_and_caller_alone_commits(encoded):
    db = FakeDB()
    result = create(db, encoded, username='  管理员甲  ', workspace_name='  资料核查工作区  ')
    assert set(result) == {'user_id', 'workspace_id'}
    assert all(str(UUID(value)) == value for value in result.values())
    user = next(row for row in db.pending if isinstance(row, User))
    workspace = next(row for row in db.pending if isinstance(row, Workspace))
    membership = next(row for row in db.pending if isinstance(row, Membership))
    assert user.username == '管理员甲' and user.password_hash == encoded
    assert workspace.name == '资料核查工作区'
    assert (membership.user_id, membership.workspace_id, membership.role) == (
        result['user_id'], result['workspace_id'], 'admin')
    assert db.flushes == 2 and db.commits == db.rollbacks == 0
    assert not db.committed
    assert db.statements[0].compile().params == {'username_1': '管理员甲'}
    db.commit()
    assert len(db.committed) == 3 and not db.pending


def test_explicit_ids_are_preserved_not_regenerated(encoded):
    db = FakeDB()
    assert create(db, encoded, user_id=USER_ID, workspace_id=WORKSPACE_ID) == {
        'user_id': USER_ID, 'workspace_id': WORKSPACE_ID,
    }
    membership = next(row for row in db.pending if isinstance(row, Membership))
    assert membership.user_id == USER_ID and membership.workspace_id == WORKSPACE_ID


@pytest.mark.parametrize('patch', [
    {'username': ''}, {'username': ' \t\n'}, {'username': None}, {'username': '甲' * 101},
    {'workspace_name': ''}, {'workspace_name': '  \n'}, {'workspace_name': 123}, {'workspace_name': '乙' * 201},
    {'user_id': USER_ID.upper()}, {'user_id': USER_ID.replace('-', '')}, {'user_id': ' ' + USER_ID},
    {'user_id': UUID(USER_ID)}, {'workspace_id': ''}, {'workspace_id': False}, {'workspace_id': 'not-a-uuid'},
])
def test_invalid_names_and_ids_fail_before_any_database_operation(encoded, patch):
    db = FakeDB()
    with pytest.raises(ValueError):
        create(db, encoded, **patch)
    assert db.events == [] and db.pending == []


def test_trimmed_unicode_name_boundaries_are_accepted(encoded):
    db = FakeDB()
    create(db, encoded, username=' ' + '甲' * 100 + ' ', workspace_name=' ' + '乙' * 200 + ' ')
    assert next(row for row in db.pending if isinstance(row, User)).username == '甲' * 100
    assert next(row for row in db.pending if isinstance(row, Workspace)).name == '乙' * 200


@pytest.mark.parametrize('duplicate', ['existing-user', ''])
def test_existing_username_never_overwrites_or_adds_rows(encoded, duplicate):
    db = FakeDB(duplicate=duplicate)
    with pytest.raises(ValueError, match='已存在'):
        create(db, encoded)
    assert db.events == ['lookup'] and not db.pending and db.commits == 0


@pytest.mark.parametrize('value', [None, 123, b'$argon2id$not-a-string', '', 'plain-long-password',
    '$argon2id$v=19$m=65536,t=3,p=4$bad%chars$bad%chars',
    '$argon2id$v=19$m=65536,t=3,p=4$$AAAA',
    '$argon2id$v=19$m=65536,t=3,p=4$YWJj$YWJj',
    '$argon2id$v=42$m=65536,t=3,p=4$YWJjZGVmZ2g$YWJjZA',
    '$argon2id$v=19$m=0,t=3,p=4$YWJjZGVmZ2g$YWJjZA',
    '$argon2id$v=19$m=8,t=0,p=1$YWJjZGVmZ2g$YWJjZA',
    '$argon2id$v=19$m=8,t=1,p=0$YWJjZGVmZ2g$YWJjZA',
    '$argon2id$v=19$m=8,t=1,p=4$YWJjZGVmZ2g$YWJjZA',
    '$argon2id$v=19$m=8,t=1,p=1,t=2$YWJjZGVmZ2g$YWJjZA',
])
def test_plaintext_or_malformed_encoded_hash_fails_before_database(value):
    db = FakeDB()
    with pytest.raises(ValueError, match='Argon2'):
        create(db, value)
    assert not db.events


def test_uses_existing_hasher_and_accepts_valid_different_cost_without_rehash(monkeypatch):
    value = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash('synthetic-valid-older-cost')
    original = bootstrap.hasher
    calls = []

    def check(encoded):
        calls.append(encoded)
        assert original.check_needs_rehash(encoded) is True
        return True

    monkeypatch.setattr(bootstrap, 'hasher', SimpleNamespace(check_needs_rehash=check))
    db = FakeDB()
    create(db, value)
    assert calls == [value]
    assert next(row for row in db.pending if isinstance(row, User)).password_hash == value


def test_flush_failure_does_not_commit_or_rollback_callers_transaction(encoded):
    db = FakeDB(fail_flush=2)
    with pytest.raises(RuntimeError, match='flush failure'):
        create(db, encoded)
    assert db.commits == db.rollbacks == 0
    assert len(db.pending) == 3
    db.rollback()
    assert not db.pending and not db.committed


def install_cli_fakes(monkeypatch, db, encoded, *, confirmation='synthetic-cli-password'):
    inputs = iter([' 管理员乙 ', ' 业务资料 '])
    passwords = iter(['synthetic-cli-password', confirmation])
    calls = []
    monkeypatch.setattr(sys, 'argv', ['ga', 'bootstrap'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(inputs))
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: next(passwords))
    monkeypatch.setattr(cli, 'migrate', lambda: calls.append('migrate'))
    monkeypatch.setattr(cli, 'engine', lambda: calls.append('engine') or 'fake-engine')
    monkeypatch.setattr(cli, 'Session', lambda bind: db if bind == 'fake-engine' else pytest.fail('foreign bind'))
    monkeypatch.setattr(cli, 'password_hash', lambda password: calls.append(('hash', password)) or encoded)
    return calls


def test_cli_keeps_migration_confirmation_and_shared_helper_then_prints_committed_workspace(monkeypatch, capsys, encoded):
    db = FakeDB()
    calls = install_cli_fakes(monkeypatch, db, encoded)
    cli.main()
    output = capsys.readouterr().out
    workspace = next(row for row in db.committed if isinstance(row, Workspace))
    assert calls == ['migrate', ('hash', 'synthetic-cli-password'), 'engine']
    assert db.commits == 1 and db.rollbacks == 0
    assert workspace.name == '业务资料' and workspace.id in output
    assert 'synthetic-cli-password' not in output and encoded not in output


def test_cli_mismatched_confirmation_never_hashes_or_opens_session(monkeypatch, capsys, encoded):
    db = FakeDB()
    calls = install_cli_fakes(monkeypatch, db, encoded, confirmation='different-confirmation')
    with pytest.raises(SystemExit, match='密码不一致'):
        cli.main()
    assert calls == ['migrate'] and not db.events
    assert capsys.readouterr().out == ''


def test_cli_duplicate_rolls_back_context_without_success_output(monkeypatch, capsys, encoded):
    db = FakeDB(duplicate=USER_ID)
    install_cli_fakes(monkeypatch, db, encoded)
    with pytest.raises(SystemExit, match='账户已存在'):
        cli.main()
    assert db.commits == 0 and db.rollbacks == 1 and not db.pending
    assert capsys.readouterr().out == ''


def test_cli_commit_failure_prints_no_workspace_or_success(monkeypatch, capsys, encoded):
    db = FakeDB(fail_commit=True)
    install_cli_fakes(monkeypatch, db, encoded)
    with pytest.raises(RuntimeError, match='commit failure'):
        cli.main()
    assert db.rollbacks == 1 and not db.committed
    assert capsys.readouterr().out == ''


def test_cli_database_error_does_not_reveal_encoded_hash_or_bound_parameters(monkeypatch, capsys, encoded):
    db = FakeDB()
    install_cli_fakes(monkeypatch, db, encoded)

    def rejected():
        raise IntegrityError('synthetic insert', {'password_hash': encoded}, Exception('synthetic duplicate'))

    monkeypatch.setattr(db, 'flush', rejected)
    with pytest.raises(SystemExit, match='状态未确认') as failure:
        cli.main()
    output = capsys.readouterr()
    assert encoded not in str(failure.value) + output.out + output.err
    assert 'password_hash' not in str(failure.value) + output.out + output.err
    assert db.rollbacks == 1 and not db.committed

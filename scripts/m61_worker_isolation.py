"""M61-only isolation contracts; importing this module starts no runtime.

The caller owns cluster creation, role grants, process lifetime and real sandbox
probes. These profiles are deny-default candidates, not evidence that macOS has
accepted or enforced them. M60's fixed restore identity remains unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).absolute().parents[1]
_spec = importlib.util.spec_from_file_location(
    "m61_immutable_m60_check", ROOT / "scripts/m60_local_release_check.py")
check = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check
_spec.loader.exec_module(check)


class Code(str, Enum):
    CONTRACT = "m61_isolation_contract_rejected"
    PATH = "m61_owned_path_rejected"
    ENVIRONMENT = "m61_inherited_environment_rejected"
    IDENTITY = "m61_cluster_identity_rejected"
    DATABASE = "m61_readonly_snapshot_failed"
    FILES = "m61_original_files_changed"
    PROFILE = "m61_sandbox_profile_rejected"


class IsolationError(RuntimeError):
    def __init__(self, code):
        self.code = code if isinstance(code, Code) else Code.CONTRACT
        super().__init__(self.code.value)


def _fail(code):
    raise IsolationError(code) from None


def validate_environment(inherited=None):
    """No libpq redirects, proxy routes or inherited model credentials."""
    inherited = os.environ if inherited is None else inherited
    if not isinstance(inherited, Mapping):
        _fail(Code.ENVIRONMENT)
    secrets = {"DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"}
    for key, value in inherited.items():
        if not isinstance(key, str):
            _fail(Code.ENVIRONMENT)
        upper = key.upper()
        if value not in (None, "") and (
                upper.startswith("PG") or upper.endswith("_PROXY")
                or upper in secrets):
            _fail(Code.ENVIRONMENT)


def _absolute(path):
    if (not isinstance(path, Path) or not path.is_absolute()
            or ".." in path.parts or any(c in str(path) for c in "\x00\n\r")):
        _fail(Code.PATH)
    return path


def _directory(path, *, private=False):
    _absolute(path)
    try:
        fd = check._directory_fd(path)
        try:
            info = os.fstat(fd)
            if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                _fail(Code.PATH)
        finally:
            os.close(fd)
    except (OSError, check.CheckError):
        _fail(Code.PATH)
    return path


def _run_root(run_root, run_id=None):
    _absolute(run_root)
    match = re.fullmatch(r"m61-run-([0-9a-f]{32})", run_root.name)
    if (match is None or run_root.parent != ROOT / ".runtime"
            or run_id is not None and match[1] != run_id):
        _fail(Code.PATH)
    return _directory(run_root, private=True)


def _port(port):
    if type(port) is not int or not 1024 <= port <= 65535 or port == 55432:
        _fail(Code.CONTRACT)
    return port


@dataclass(frozen=True, repr=False)
class OwnedCluster:
    run_id: str = field(repr=False)
    run_root: Path = field(repr=False)
    port: int = field(repr=False)
    system_identifier: str = field(repr=False)
    database_name: str = field(repr=False)
    oid: int = field(repr=False)
    marker: str = field(repr=False)
    app_url: str = field(repr=False)

    def __post_init__(self):
        try:
            if (not isinstance(self.run_id, str)
                    or re.fullmatch(r"[0-9a-f]{32}", self.run_id) is None
                    or not isinstance(self.system_identifier, str)
                    or re.fullmatch(r"[1-9][0-9]{0,19}", self.system_identifier) is None
                    or int(self.system_identifier) > 2**64 - 1
                    or type(self.oid) is not int or self.oid <= 0
                    or self.database_name != "ga_m61_" + self.run_id
                    or self.marker != "m61-workers-owned:" + self.run_id
                    or not isinstance(self.app_url, str)):
                _fail(Code.CONTRACT)
            _run_root(self.run_root, self.run_id)
            _port(self.port)
            url = make_url(self.app_url)
            if (url.drivername not in {"postgresql+psycopg", "postgresql+psycopg2", "postgresql"}
                    or (url.host, url.port, url.database) != (
                        "127.0.0.1", self.port, self.database_name)
                    or url.username != "m61_app_" + self.run_id[:12]
                    or not url.password or url.query):
                _fail(Code.CONTRACT)
        except IsolationError:
            raise
        except Exception:
            _fail(Code.CONTRACT)

    def __repr__(self):
        return "OwnedCluster(<private>)"


def _control_record(cluster):
    return {"format": "m61-owned-cluster-v1", "state": "ready",
        "run_id": cluster.run_id, "run_root": str(cluster.run_root),
        "port": cluster.port, "system_identifier": cluster.system_identifier,
        "database_name": cluster.database_name, "oid": cluster.oid,
        "marker": cluster.marker}


def _private_read(root, relative, maximum):
    try:
        fd = check._open_at(root, relative)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                _fail(Code.PATH)
            return check._read_fd(fd, maximum=maximum)
        finally:
            os.close(fd)
    except (OSError, check.CheckError):
        _fail(Code.PATH)


def _verify_control(cluster):
    if type(cluster) is not OwnedCluster:
        _fail(Code.CONTRACT)
    # Revalidate even a frozen instance: its filesystem may have changed.
    cluster.__post_init__()
    _directory(cluster.run_root / "control", private=True)
    _directory(cluster.run_root / "cluster", private=True)
    if _private_read(cluster.run_root, "cluster/PG_VERSION", 16) not in {b"17", b"17\n"}:
        _fail(Code.IDENTITY)
    try:
        value = json.loads(_private_read(cluster.run_root,
            "control/cluster-identity.json", 8192), object_pairs_hook=check._unique_object)
        if check._digest(value) != check._digest(_control_record(cluster)):
            _fail(Code.IDENTITY)
    except (ValueError, TypeError, check.CheckError):
        _fail(Code.IDENTITY)


def _storage_inventory(storage):
    """Private copies must not share writable inodes with historical originals."""
    files = check.file_inventory(storage)
    for relative, expected in files.items():
        fd = check._open_at(storage, relative)
        try:
            info = os.fstat(fd)
            if info.st_nlink != 1 or check._file_metadata(fd) != expected:
                _fail(Code.FILES)
        finally:
            os.close(fd)
    return files


def snapshot_owned(cluster, storage):
    """Read only this owned cluster; authenticate identity before table access.

    The caller grants only EXECUTE on pg_catalog.pg_control_system() to the
    non-superuser app role. No admin URL or shared-cluster fallback is accepted.
    Only storage and its explicit originals child are valid inventory roots.
    """
    validate_environment()
    _verify_control(cluster)
    if not isinstance(storage, Path) or storage not in {
            cluster.run_root / "storage", cluster.run_root / "storage/originals"}:
        _fail(Code.PATH)
    _directory(storage, private=True)
    database = None
    try:
        files = _storage_inventory(storage)
        database = create_engine(cluster.app_url, isolation_level="REPEATABLE READ",
            connect_args={"hostaddr": "127.0.0.1", "port": cluster.port,
                "options": "-c default_transaction_read_only=on", "connect_timeout": 5})
        with database.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                observed = connection.execute(text("""
                    SELECT pg_catalog.current_database() AS database_name, d.oid,
                      pg_catalog.shobj_description(d.oid, 'pg_database') AS marker,
                      pg_catalog.current_setting('transaction_read_only') AS transaction_read_only,
                      control.system_identifier::text AS system_identifier,
                      pg_catalog.host(pg_catalog.inet_server_addr()) AS server_address,
                      pg_catalog.inet_server_port() AS server_port,
                      current_user AS role_name,
                      r.rolsuper AS superuser, r.rolcreatedb AS createdb,
                      r.rolcreaterole AS createrole, r.rolreplication AS replication,
                      r.rolbypassrls AS bypassrls,
                      pg_catalog.pg_has_role(current_user, 'pg_read_server_files', 'MEMBER') AS read_server_files,
                      pg_catalog.pg_has_role(current_user, 'pg_write_server_files', 'MEMBER') AS write_server_files,
                      pg_catalog.pg_has_role(current_user, 'pg_execute_server_program', 'MEMBER') AS execute_server_program,
                      EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members membership
                        WHERE membership.member = r.oid) AS has_role_memberships
                    FROM pg_catalog.pg_database d
                    CROSS JOIN pg_catalog.pg_control_system() control
                    JOIN pg_catalog.pg_roles r ON r.rolname = current_user
                    WHERE d.datname = pg_catalog.current_database()
                """)).mappings().one()
                expected = {"database_name": cluster.database_name, "oid": cluster.oid,
                    "marker": cluster.marker, "transaction_read_only": "on",
                    "system_identifier": cluster.system_identifier,
                    "server_address": "127.0.0.1", "server_port": cluster.port,
                    "role_name": "m61_app_" + cluster.run_id[:12],
                    **{key: False for key in ("superuser", "createdb", "createrole",
                        "replication", "bypassrls", "read_server_files",
                        "write_server_files", "execute_server_program", "has_role_memberships")}}
                if check._digest(observed) != check._digest(expected):
                    _fail(Code.IDENTITY)
                result = check._collect_inventory(connection, files)
        if _storage_inventory(storage) != files:
            _fail(Code.FILES)
        _verify_control(cluster)
        return result
    except IsolationError:
        raise
    except Exception:
        _fail(Code.DATABASE)
    finally:
        if database is not None:
            try:
                database.dispose()
            except Exception:
                _fail(Code.DATABASE)


def _quoted(value):
    return json.dumps(str(value), ensure_ascii=False)


def _binary(path):
    _absolute(path)
    try:
        target = path.resolve(strict=True)
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            _fail(Code.PROFILE)
        # Both literals identify the caller-selected executable, not a bin tree.
        return sorted({str(path), str(target)})
    except (OSError, RuntimeError):
        _fail(Code.PROFILE)


def _profile(run_root, port, binary, read_roots, *, postgres):
    _run_root(run_root)
    _port(port)
    binaries = _binary(binary)
    if not isinstance(read_roots, (list, tuple)) or not read_roots:
        _fail(Code.PROFILE)
    writes = ("cluster", "pg-tmp", "pg-logs") if postgres else ("storage", "tmp", "logs")
    write_paths = [_directory(run_root / name, private=True) for name in writes]
    if any(Path(binary).is_relative_to(path) for binary in binaries for path in write_paths):
        _fail(Code.PROFILE)
    reads = set(write_paths)
    for path in read_roots:
        _directory(path)
        if (path in {Path("/"), Path.home(), ROOT, ROOT / ".runtime", run_root}
                or ROOT.is_relative_to(path)
                or path.is_relative_to(ROOT / ".runtime/evidence")
                or path.is_relative_to(run_root) and path not in write_paths):
            _fail(Code.PROFILE)
        reads.add(path)
    sysctls = ("hw.ncpu", "hw.activecpu", "hw.logicalcpu", "hw.physicalcpu",
        "hw.memsize", "hw.pagesize", "hw.cachelinesize", "hw.optional.arm64",
        "kern.ostype", "kern.osrelease", "kern.osversion", "kern.argmax",
        "sysctl.proc_cputype", "security.mac.lockdown_mode_state", "kern.bootargs",
        "kern.hostname", "kern.version", "hw.machine")
    lines = ["(version 1)", "(deny default)", "(allow sysctl-read " + " ".join(
        "(sysctl-name " + _quoted(name) + ")" for name in sysctls) + ")"]
    # dyld CacheFinder reads the root directory itself during ignition. This
    # literal grants no descendants and does not broaden the read-root list.
    lines.append('(allow file-read-data (literal "/"))')
    # Ancestor metadata is needed for pathname traversal, without reading files.
    ancestors = {parent for path in reads for parent in path.parents}
    lines.append("(allow file-read-metadata " + " ".join(
        "(literal " + _quoted(path) + ")" for path in sorted(ancestors)) + ")")
    lines.append("(allow file-read* " + " ".join(
        "(subpath " + _quoted(path) + ")" for path in sorted(reads)) + ")")
    executable_reads = set(read_roots) - set(write_paths)
    lines.append("(allow file-map-executable " + " ".join(
        ["(subpath " + _quoted(path) + ")" for path in sorted(executable_reads)]
        + ["(literal " + _quoted(path) + ")" for path in binaries]) + ")")
    lines.append("(allow file-read* " + " ".join(
        "(literal " + _quoted(path) + ")" for path in binaries) + ")")
    lines.append("(allow process-exec " + " ".join(
        "(literal " + _quoted(path) + ")" for path in binaries) + ")")
    lines.append("(allow file-write* " + " ".join(
        "(subpath " + _quoted(path) + ")" for path in write_paths) + ")")
    # SBPL accepts only '*' or 'localhost' as an address host. The app URL and
    # observed SQL endpoint remain independently pinned to IPv4 127.0.0.1.
    endpoint = _quoted("localhost:" + str(port))
    if postgres:
        lines.extend(["(allow process-fork)",
            "(allow signal (target same-sandbox))",
            "(allow network-bind (local tcp " + endpoint + "))",
            "(allow network-inbound (local tcp " + endpoint + "))",
            # This host's PG17 startup needs SysV shared memory and semaphores.
            # No locally verified key filter is available: this PG-only grant
            # is not segment isolation. The caller checks owned IPC on exit.
            "(allow ipc-sysv-shm)",
            "(allow ipc-sysv-sem)",
            '(allow ipc-posix-shm* (ipc-posix-name-regex #"^/?PostgreSQL[.][0-9]+$"))'])
    else:
        lines.append("(allow network-outbound (remote tcp " + endpoint + "))")
    return "\n".join(lines) + "\n"


def worker_profile(run_root, port, python_binary, read_roots):
    return _profile(run_root, port, python_binary, read_roots, postgres=False)


def postgres_profile(run_root, port, postgres_binary, read_roots):
    return _profile(run_root, port, postgres_binary, read_roots, postgres=True)

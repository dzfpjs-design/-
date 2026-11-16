"""Server-owned UAV adapter catalogue and fail-closed physical boundary.

This is a code-level extension contract, not the production registration API.
Only explicitly allowlisted Python adapter classes can be registered as test
simulators. A physical vendor can be recorded as a candidate, but cannot be
resolved for dispatch until a separate audited integration is implemented.

The catalogue itself does not authenticate users or approve missions. Actual
execution must go through the existing gateway's workspace/device grants,
frozen inputs, human approval, durable request and receipt recovery.
"""
import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

from .device_simulator_v175 import DeviceLedger

_ID = re.compile(r'^[a-z][a-z0-9_.-]{0,63}$')
_VERSION = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+$')
_FEATURES = frozenset({'read_status', 'prepare_mission', 'start_mission', 'read_receipt', 'return_to_dock'})


class AdapterUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class AdapterManifest:
    adapter_id: str
    version: str
    vendor: str
    protocol: str
    environment: Literal['simulation', 'physical']
    capabilities: tuple[str, ...]

    def validate(self):
        if (not _ID.fullmatch(self.adapter_id) or not _VERSION.fullmatch(self.version)
                or not isinstance(self.vendor, str) or not 1 <= len(self.vendor.strip()) <= 120
                or not isinstance(self.protocol, str) or not _ID.fullmatch(self.protocol)
                or self.environment not in ('simulation', 'physical')
                or type(self.capabilities) is not tuple or not self.capabilities
                or len(set(self.capabilities)) != len(self.capabilities)
                or not set(self.capabilities) <= _FEATURES):
            raise ValueError('无人机适配器声明无效')
        return self

    def fingerprint(self):
        self.validate()
        wire=json.dumps(asdict(self),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        return hashlib.sha256(wire.encode()).hexdigest()


class ProcurementSimulatorAdapter:
    """The only built-in executable adapter; it wraps the isolated simulator."""
    def __init__(self, ledger):
        if type(ledger) is not DeviceLedger:
            raise TypeError('仅允许采购设备模拟器台账')
        self.ledger=ledger


class AdapterRegistry:
    """Immutable identities; executable plugins must be allowlisted by code.

    ``trusted_simulator_types`` is a server-side deployment choice, not input
    from a web page. An untrusted Python plugin is executable code: declaring
    "simulation" alone never establishes that it is safe.
    """
    def __init__(self, *, trusted_simulator_types=(ProcurementSimulatorAdapter,)):
        if type(trusted_simulator_types) is not tuple or not all(isinstance(t,type) for t in trusted_simulator_types):
            raise TypeError('仿真适配器类型必须由服务端显式允许')
        self._trusted=trusted_simulator_types
        self._entries={}

    def register(self, manifest, implementation=None):
        if type(manifest) is not AdapterManifest:
            raise TypeError('需要完整的适配器声明')
        manifest.validate()
        if manifest.adapter_id in self._entries:
            raise ValueError('适配器标识已登记；不能覆盖原版本')
        if manifest.environment == 'physical':
            if implementation is not None:
                raise AdapterUnavailable('真实厂商控制尚未接通，不能注册可执行驱动')
        elif type(implementation) not in self._trusted:
            raise AdapterUnavailable('模拟适配器不在服务端可信类型清单中')
        self._entries[manifest.adapter_id]=(manifest,implementation)
        return self.descriptor(manifest.adapter_id)

    def descriptor(self, adapter_id):
        manifest, implementation = self._entries[adapter_id]
        return {**copy.deepcopy(asdict(manifest)), 'fingerprint':manifest.fingerprint(),
                'executable':manifest.environment=='simulation' and implementation is not None,
                'release_boundary':'仅设备模拟器；没有真实无人机控制权限' if manifest.environment=='simulation'
                    else '真实设备适配、机型校验、空域和安全条件尚未完成'}

    def resolve_simulation(self, adapter_id, fingerprint):
        if adapter_id not in self._entries:
            raise AdapterUnavailable('适配器不存在')
        manifest, implementation = self._entries[adapter_id]
        if manifest.fingerprint()!=fingerprint:
            raise AdapterUnavailable('适配器版本或绑定已变化，请重新核对')
        if manifest.environment!='simulation' or implementation is None:
            raise AdapterUnavailable('真实无人机适配器尚不可执行')
        return implementation


def procurement_simulator_manifest():
    return AdapterManifest(adapter_id='ga.procurement-simulator',version='1.0.0',vendor='平台模拟器',
        protocol='procurement-simulator-v1',environment='simulation',capabilities=tuple(sorted(_FEATURES)))

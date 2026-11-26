"""Sample this isolated macOS test process; never inspect other processes."""
import ctypes
import sys
import threading


class _TimeValue(ctypes.Structure):
    _fields_ = [('seconds', ctypes.c_int32), ('microseconds', ctypes.c_int32)]


class _TaskInfo(ctypes.Structure):
    _fields_ = [('virtual_size', ctypes.c_uint64), ('resident_size', ctypes.c_uint64),
                ('resident_size_max', ctypes.c_uint64), ('user_time', _TimeValue),
                ('system_time', _TimeValue), ('policy', ctypes.c_int32), ('suspend_count', ctypes.c_int32)]


def _reader():
    if sys.platform != 'darwin':
        raise RuntimeError('isolated RSS sampler requires macOS')
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    port = ctypes.c_uint.in_dll(lib, 'mach_task_self_').value
    info = lib.task_info
    info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint)]
    info.restype = ctypes.c_int
    def read():
        data = _TaskInfo()
        count = ctypes.c_uint(ctypes.sizeof(data) // ctypes.sizeof(ctypes.c_int))
        result = info(port, 20, ctypes.cast(ctypes.byref(data), ctypes.POINTER(ctypes.c_int)), ctypes.byref(count))
        if result != 0 or count.value != ctypes.sizeof(data) // ctypes.sizeof(ctypes.c_int) or data.resident_size == 0:
            raise RuntimeError('isolated RSS measurement unavailable')
        return data.resident_size
    return read


class SyntheticRssSampler:
    def __init__(self, interval_seconds=0.01):
        self._read = _reader()
        self.interval_seconds = interval_seconds
        self.start_bytes = self.peak_bytes = self._read()
        self._stop = threading.Event()
        self._failed = False
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self._stop.wait(self.interval_seconds):
            try:
                self.peak_bytes = max(self.peak_bytes, self._read())
            except Exception:
                self._failed = True
                return

    def start(self):
        self._thread.start()

    def finish(self):
        self._stop.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive() or self._failed:
            raise RuntimeError('isolated RSS measurement incomplete')
        end = self._read()
        self.peak_bytes = max(self.peak_bytes, end)
        return {'start_bytes': self.start_bytes, 'sampled_peak_bytes': self.peak_bytes,
                'end_bytes': end, 'interval_ms': round(self.interval_seconds * 1000)}

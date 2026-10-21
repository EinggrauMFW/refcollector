"""Stop a browser worker and its children when its parent releases ownership.

The worker must report its own PID and wait for attachment before it starts
any child processes. Windows venv launcher PIDs are not worker PIDs.
POSIX callers must spawn the worker with ``start_new_session=True``.
"""

from __future__ import annotations

import ctypes
import os
import signal
from ctypes import wintypes

from refcollector.errors import IntegrationUnavailable


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ProcessTree:
    """Own one process tree; close is idempotent and stops all attached children."""

    def __init__(self) -> None:
        self._pid: int | None = None
        self._job: int | None = None
        self._closed = False
        self._kernel = None
        if os.name != "nt":
            return
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError as exc:
            raise IntegrationUnavailable("Could not load Windows process containment") from exc
        self._kernel = kernel
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        try:
            self._job = kernel.CreateJobObjectW(None, None)
            if not self._job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError as exc:
            self.close()
            raise IntegrationUnavailable("Could not create browser process containment") from exc

    def attach(self, pid: int) -> None:
        """Attach the worker before it is permitted to create browser children."""
        if self._closed or self._pid is not None:
            raise IntegrationUnavailable("Browser process containment is already used or closed")
        if self._kernel is None:
            self._pid = pid
            return
        process = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            self.close()
            raise IntegrationUnavailable("Could not open browser worker for containment")
        try:
            if not self._kernel.AssignProcessToJobObject(self._job, process):
                raise IntegrationUnavailable("Could not attach browser worker to containment")
            self._pid = pid
        except IntegrationUnavailable:
            self.close()
            raise
        finally:
            self._kernel.CloseHandle(process)

    def close(self) -> None:
        """Stop children even when the original worker has already exited."""
        if self._closed:
            return
        self._closed = True
        if self._kernel is not None:
            if self._job:
                self._kernel.CloseHandle(self._job)
                self._job = None
        elif self._pid is not None:
            try:
                os.killpg(self._pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._pid = None

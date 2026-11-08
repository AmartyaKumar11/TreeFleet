"""Keep worker descendants on the same lifetime as the orchestrator.

Windows: one job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. The handle lives in the
orchestrator process. Closing it (process exit, including taskkill /F) kills every process
in the job. A child spawned after its parent is in the job joins the job. A child that
already exists does not, so each pid is assigned explicitly.

POSIX: the worker is started in its own session. On Linux the child calls prctl(PR_SET_PDEATHSIG)
so the direct child dies when the orchestrator dies. That signal does not kill grandchildren,
and it does not exist on macOS. See docs/SESSIONS.md.
"""

from __future__ import annotations

import sys
import threading

import psutil

_JOB_LOCK = threading.Lock()
_JOB: int | None = None
_KILL_ON_CLOSE = 0x2000
_JOB_EXTENDED = 9
_ASSIGN_ACCESS = 0x0100 | 0x0001


def _job_handle() -> int:
    global _JOB
    with _JOB_LOCK:
        if _JOB:
            return _JOB
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel.CreateJobObjectW.restype = ctypes.c_void_p
        kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel.SetInformationJobObject.restype = ctypes.c_int
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_CLOSE
        if not kernel.SetInformationJobObject(job, _JOB_EXTENDED, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        _JOB = int(job)
        return _JOB


def _assign(job: int, pid: int) -> bool:
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(_ASSIGN_ACCESS, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel.AssignProcessToJobObject(job, handle))
    finally:
        kernel.CloseHandle(handle)


def resume_process(pid: int) -> None:
    """Resume a worker started with CREATE_SUSPENDED. No-op off Windows."""
    if sys.platform != "win32" or pid <= 0:
        return
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    handle = kernel.OpenProcess(0x0800, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess for resume failed")
    try:
        status = int(ntdll.NtResumeProcess(handle))
        if status != 0:
            raise OSError(status, "NtResumeProcess failed")
    finally:
        kernel.CloseHandle(handle)


def _job_pids(job: int) -> set[int]:
    import ctypes

    class Listing(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", ctypes.c_uint32),
            ("NumberOfProcessIdsInList", ctypes.c_uint32),
            ("ProcessIdList", ctypes.c_size_t * 256),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    info = Listing()
    if not kernel.QueryInformationJobObject(job, 3, ctypes.byref(info), ctypes.sizeof(info), None):
        return set()
    count = min(int(info.NumberOfProcessIdsInList), 256)
    return {int(info.ProcessIdList[index]) for index in range(count)}


def bind_worker(pid: int) -> None:
    """Put this process and the children it already has into the kill-on-close job."""
    if sys.platform != "win32" or pid <= 0:
        return
    job = _job_handle()
    pids = [pid]
    try:
        pids.extend(int(child.pid) for child in psutil.Process(pid).children(recursive=True))
    except psutil.Error:
        pass
    if any(_assign(job, item) for item in pids):
        return
    if pid in _job_pids(job):
        return
    raise RuntimeError(f"could not assign {pid} to the kill-on-close job")


def _linux_pdeathsig() -> None:
    import ctypes
    import os

    libc = ctypes.CDLL("libc.so.6")
    libc.prctl(1, 9)
    if os.getppid() == 1:
        os.kill(os.getpid(), 9)

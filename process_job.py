"""子プロセスを本体の終了に連動して終了させる (Windows Job Object)。

コンソールを閉じた・タスクマネージャーで終了した等、atexit が走らない強制終了でも、
本体プロセスが消えた時点で OS がジョブのハンドルを閉じ、登録した子プロセスを終了させる。
"""
import ctypes
import os
import threading
from ctypes import wintypes
from logging import getLogger

import constants

_log = getLogger(f"{constants.LOG_PREFIX}.process_job")

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_lock = threading.Lock()
# ジョブのハンドルは本体の終了まで閉じない（閉じると登録済みの子プロセスが終了する）
_job_handle = None
_job_failed = False


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _get_job_locked(k32):
    global _job_handle, _job_failed
    if _job_handle or _job_failed:
        return _job_handle
    job = k32.CreateJobObjectW(None, None)
    if not job:
        _job_failed = True
        _log.warning("CreateJobObject failed: winerror=%s", ctypes.get_last_error())
        return None
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    ):
        _job_failed = True
        _log.warning("SetInformationJobObject failed: winerror=%s", ctypes.get_last_error())
        k32.CloseHandle(job)
        return None
    _job_handle = job
    return job


def bind_to_current_process(proc):
    """subprocess.Popen で起動した子プロセスを、本体の終了時に自動で終了させる。成功時 True。"""
    if os.name != "nt" or proc is None:
        return False
    try:
        k32 = _kernel32()
        with _lock:
            job = _get_job_locked(k32)
        if not job:
            return False
        handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, proc.pid)
        if not handle:
            _log.warning("OpenProcess failed pid=%s: winerror=%s", proc.pid, ctypes.get_last_error())
            return False
        try:
            if not k32.AssignProcessToJobObject(job, handle):
                _log.warning("AssignProcessToJobObject failed pid=%s: winerror=%s", proc.pid, ctypes.get_last_error())
                return False
        finally:
            k32.CloseHandle(handle)
        return True
    except Exception as e:
        _log.warning("bind_to_current_process failed: %s", e)
        return False

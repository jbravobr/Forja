import sys

if sys.platform == "win32":
    import ctypes
    import os
    import signal
    from contextlib import suppress
    from ctypes import wintypes
    from typing import Any

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _nt = ctypes.WinDLL("ntdll")

    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _CREATE_SUSPENDED = 0x00000004
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_ALL_ACCESS = 0x1F0FFF
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x00000020
    _IDLE_PRIORITY_CLASS = 0x00000040
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _STILL_ACTIVE = 259
    _TH32CS_SNAPPROCESS = 0x00000002

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
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

    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    _k32.Process32FirstW.restype = wintypes.BOOL
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    _k32.Process32NextW.restype = wintypes.BOOL
    _nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _nt.NtResumeProcess.restype = ctypes.c_long

    def _limit(job: Any, flags: int, priority_class: int | None = None) -> None:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = flags
        if priority_class is not None:
            limits.BasicLimitInformation.PriorityClass = priority_class
        ok = _k32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    class WindowsJobGroup:
        """Kill-on-close Job Object: the whole tree dies with the handle or on ``kill``.

        The child is created suspended and resumed only after joining the job, so no
        descendant can start outside it.
        """

        def __init__(self) -> None:
            job = _k32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                _limit(job, _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            except OSError:
                _k32.CloseHandle(job)
                raise
            self._job: Any = job
            self._pid: int | None = None

        def spawn_kwargs(self) -> dict[str, Any]:
            return {"creationflags": _CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED}

        def lower_priority(self) -> None:
            """The job gives the idle class to every process in it, and none of them can leave
            it: the tree runs only when nothing else on the machine wants the processor."""
            _limit(
                self._job,
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_PRIORITY_CLASS,
                _IDLE_PRIORITY_CLASS,
            )

        def attach(self, pid: int) -> None:
            handle = _k32.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not _k32.AssignProcessToJobObject(self._job, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                status = _nt.NtResumeProcess(handle)
                if status != 0:
                    raise OSError(f"NtResumeProcess failed with status {status:#x}")
            finally:
                _k32.CloseHandle(handle)
            self._pid = pid

        def interrupt(self) -> None:
            """Ask politely (Ctrl+Break to the process group); needs a console, else a no-op."""
            if self._pid is not None:
                with suppress(OSError):
                    os.kill(self._pid, signal.CTRL_BREAK_EVENT)

        def kill(self) -> None:
            if self._job is not None:
                _k32.TerminateJobObject(self._job, 1)

        def peak_memory(self) -> int | None:
            """``PeakJobMemoryUsed``: the most the processes of the job held at the same time.

            Measured with children that allocate known sizes: it is the concurrent sum across
            the tree (150 MB parent + 200 MB child read about 380 MB), and it stays readable
            after ``TerminateJobObject``. ``PeakProcessMemoryUsed`` is only the largest single
            process, so it misses a tree that spreads its memory over several.
            """
            if self._job is None:
                return None
            limits = _ExtendedLimits()
            if not _k32.QueryInformationJobObject(
                self._job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
                None,
            ):
                return None
            return int(limits.PeakJobMemoryUsed) or None

        def close(self) -> None:
            if self._job is not None:
                _k32.CloseHandle(self._job)
                self._job = None

    def creation_token(pid: int) -> int | None:
        handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            if code.value != _STILL_ACTIVE:
                return None
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _k32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            _k32.CloseHandle(handle)

    def kill_process(pid: int) -> None:
        handle = _k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if handle:
            try:
                _k32.TerminateProcess(handle, 1)
            finally:
                _k32.CloseHandle(handle)

    def running_process_names() -> set[str]:
        """Lower-case executable names of every process on the machine."""
        snapshot = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snapshot in (None, wintypes.HANDLE(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        names: set[str] = set()
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                names.add(entry.szExeFile.lower())
                more = _k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            _k32.CloseHandle(snapshot)
        return names

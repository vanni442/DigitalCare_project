"""Identify this Windows project's services before reusing their local ports."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket


def port_available(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def free_port(host, preferred, excluded=()):
    for port in range(preferred, min(preferred + 200, 65536)):
        if port not in excluded and port_available(host, port):
            return port
    raise RuntimeError("No free local port near the configured port. Close an unused server and retry.")


def listener_pids(host, port):
    """Read IPv4 listener ownership, including services returning HTTP errors."""
    if os.name != "nt":
        return set()
    class Row(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in
                    ("state", "address", "port", "remote_address", "remote_port", "pid")]
    size = wintypes.DWORD()
    table = ctypes.windll.iphlpapi.GetExtendedTcpTable
    table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                      wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    table.restype = wintypes.DWORD
    if table(None, ctypes.byref(size), False, socket.AF_INET, 3, 0) not in (0, 122):
        return set()
    # The listener table can grow between the sizing and reading calls.
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = table(buffer, ctypes.byref(size), False, socket.AF_INET, 3, 0)
        if result != 122:
            break
    if result:
        return set()
    count = wintypes.DWORD.from_buffer(buffer).value
    address = socket.inet_aton(socket.gethostbyname(host))
    owners = set()
    for index in range(count):
        row = Row.from_buffer(buffer, ctypes.sizeof(wintypes.DWORD) + index * ctypes.sizeof(Row))
        local_address = int(row.address).to_bytes(4, 'little')
        if socket.ntohs(row.port & 0xffff) == port and local_address in (address, b'\0' * 4):
            owners.add(int(row.pid))
    return owners


def owned_process(root, name, command):
    """A copied/stale PID file must never authorize reuse of another project."""
    if os.name != "nt":
        return None  # The offline distribution targets Windows 10/11.
    try:
        entry = json.loads((Path(root) / 'data/processes.json').read_text('utf-8'))[name]
        if (Path(entry['project']).resolve() != Path(root).resolve()
                or entry['command'] != [str(item) for item in command]
                or not entry.get('createdFileTime')):
            return None
        pid = int(entry['pid'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        created, ended, user, cpu = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended),
                                      ctypes.byref(user), ctypes.byref(cpu)):
            return None
        birth = str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            return None
        if birth != entry['createdFileTime'] or Path(image.value).resolve() != Path(command[0]).resolve():
            return None
        return pid
    finally:
        kernel.CloseHandle(handle)


def owns_listener(root, name, command, host, port):
    pid = owned_process(root, name, command)
    return bool(pid and pid in listener_pids(host, port))

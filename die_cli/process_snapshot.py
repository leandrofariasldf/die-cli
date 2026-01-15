import os
import time
import ctypes
import csv
import io
import subprocess
from ctypes import wintypes

import psutil

SNAPSHOT_INTERVAL = 1.0
TASKLIST_REFRESH = 15.0
TASKLIST_TTL = 60.0
SYSTEM_PROCESS_NAMES = {
    "system",
    "system idle process",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "registry",
    "secure system",
    "msmpeng.exe",
    "trustedinstaller.exe",
}
_WELL_KNOWN_SIDS = {
    "SYSTEM": "S-1-5-18",
    "LOCAL_SERVICE": "S-1-5-19",
    "NETWORK_SERVICE": "S-1-5-20",
}
_WELL_KNOWN_CACHE = {}


def _prime_cpu_percent():
    for proc in psutil.process_iter():
        try:
            proc.cpu_percent(None)
        except Exception:
            pass


def _enable_debug_privilege():
    if os.name != "nt":
        return False
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcessToken = advapi32.OpenProcessToken
        OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        OpenProcessToken.restype = wintypes.BOOL

        LookupPrivilegeValueW = advapi32.LookupPrivilegeValueW
        LookupPrivilegeValueW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LUID),
        ]
        LookupPrivilegeValueW.restype = wintypes.BOOL

        AdjustTokenPrivileges = advapi32.AdjustTokenPrivileges
        AdjustTokenPrivileges.argtypes = [
            wintypes.HANDLE,
            wintypes.BOOL,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        AdjustTokenPrivileges.restype = wintypes.BOOL

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        TOKEN_QUERY = 0x0008
        TOKEN_ADJUST_PRIVILEGES = 0x0020
        SE_PRIVILEGE_ENABLED = 0x00000002

        token = wintypes.HANDLE()
        if not OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES,
            ctypes.byref(token),
        ):
            return False

        luid = wintypes.LUID()
        if not LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            CloseHandle(token)
            return False

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", wintypes.LUID), ("Attributes", wintypes.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES)]

        tp = TOKEN_PRIVILEGES(
            PrivilegeCount=1,
            Privileges=LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED),
        )
        AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        CloseHandle(token)
        return True
    except Exception:
        return False


def _build_service_user_map():
    mapping = {}
    try:
        for svc in psutil.win_service_iter():
            try:
                info = svc.as_dict()
                pid = info.get("pid")
                user = info.get("username")
                if pid and user:
                    mapping[pid] = user
            except Exception:
                continue
    except Exception:
        pass
    return mapping


def _lookup_account_sid(sid):
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        LookupAccountSidW = advapi32.LookupAccountSidW
        LookupAccountSidW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPVOID,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        LookupAccountSidW.restype = wintypes.BOOL

        name_size = wintypes.DWORD(0)
        domain_size = wintypes.DWORD(0)
        sid_type = wintypes.DWORD(0)
        LookupAccountSidW(
            None,
            sid,
            None,
            ctypes.byref(name_size),
            None,
            ctypes.byref(domain_size),
            ctypes.byref(sid_type),
        )
        if not name_size.value:
            return None

        name_buf = ctypes.create_unicode_buffer(name_size.value)
        domain_buf = ctypes.create_unicode_buffer(domain_size.value)
        if not LookupAccountSidW(
            None,
            sid,
            name_buf,
            ctypes.byref(name_size),
            domain_buf,
            ctypes.byref(domain_size),
            ctypes.byref(sid_type),
        ):
            return None

        name = name_buf.value
        domain = domain_buf.value
        if domain:
            return f"{domain}\\{name}"
        return name
    except Exception:
        return None


def _win_session_id(pid):
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ProcessIdToSessionId = kernel32.ProcessIdToSessionId
        ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        ProcessIdToSessionId.restype = wintypes.BOOL
        session_id = wintypes.DWORD(0)
        if not ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            return None
        return int(session_id.value)
    except Exception:
        return None


def _lookup_account_from_sid_str(sid_str):
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        ConvertStringSidToSidW = advapi32.ConvertStringSidToSidW
        ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID)]
        ConvertStringSidToSidW.restype = wintypes.BOOL

        LocalFree = kernel32.LocalFree
        LocalFree.argtypes = [wintypes.HLOCAL]
        LocalFree.restype = wintypes.HLOCAL

        sid = wintypes.LPVOID()
        if not ConvertStringSidToSidW(sid_str, ctypes.byref(sid)):
            return None
        try:
            return _lookup_account_sid(sid)
        finally:
            if sid:
                LocalFree(sid)
    except Exception:
        return None


def _well_known_account_name(kind):
    if kind in _WELL_KNOWN_CACHE:
        return _WELL_KNOWN_CACHE[kind]
    sid = _WELL_KNOWN_SIDS.get(kind)
    name = _lookup_account_from_sid_str(sid) if sid else None
    _WELL_KNOWN_CACHE[kind] = name or kind
    return _WELL_KNOWN_CACHE[kind]


def _win_username_from_pid(pid):
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        OpenProcessToken = advapi32.OpenProcessToken
        OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        OpenProcessToken.restype = wintypes.BOOL

        GetTokenInformation = advapi32.GetTokenInformation
        GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        GetTokenInformation.restype = wintypes.BOOL

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        TOKEN_QUERY = 0x0008
        TokenUser = 1

        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

        class TOKEN_USER(ctypes.Structure):
            _fields_ = [("User", SID_AND_ATTRIBUTES)]

        proc_handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not proc_handle:
            return None

        token_handle = wintypes.HANDLE()
        if not OpenProcessToken(proc_handle, TOKEN_QUERY, ctypes.byref(token_handle)):
            CloseHandle(proc_handle)
            return None

        needed = wintypes.DWORD(0)
        GetTokenInformation(token_handle, TokenUser, None, 0, ctypes.byref(needed))
        if not needed.value:
            CloseHandle(token_handle)
            CloseHandle(proc_handle)
            return None

        buf = ctypes.create_string_buffer(needed.value)
        if not GetTokenInformation(
            token_handle, TokenUser, buf, needed, ctypes.byref(needed)
        ):
            CloseHandle(token_handle)
            CloseHandle(proc_handle)
            return None

        token_user = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents
        sid = token_user.User.Sid

        CloseHandle(token_handle)
        CloseHandle(proc_handle)
        return _lookup_account_sid(sid)
    except Exception:
        return None


def _win_session_username(pid):
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

        ProcessIdToSessionId = kernel32.ProcessIdToSessionId
        ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        ProcessIdToSessionId.restype = wintypes.BOOL

        WTSQuerySessionInformationW = wtsapi32.WTSQuerySessionInformationW
        WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        ]
        WTSQuerySessionInformationW.restype = wintypes.BOOL

        WTSFreeMemory = wtsapi32.WTSFreeMemory
        WTSFreeMemory.argtypes = [wintypes.LPVOID]
        WTSFreeMemory.restype = None

        WTS_CURRENT_SERVER_HANDLE = wintypes.HANDLE(0)
        WTSUserName = 5
        WTSDomainName = 7

        session_id = _win_session_id(pid)
        if session_id is None:
            return None

        def _query(info_class):
            buf = wintypes.LPWSTR()
            size = wintypes.DWORD(0)
            if not WTSQuerySessionInformationW(
                WTS_CURRENT_SERVER_HANDLE,
                session_id,
                info_class,
                ctypes.byref(buf),
                ctypes.byref(size),
            ):
                return ""
            try:
                return buf.value or ""
            finally:
                WTSFreeMemory(buf)

        user = _query(WTSUserName)
        domain = _query(WTSDomainName)
        if not user:
            return None
        if domain:
            return f"{domain}\\{user}"
        return user
    except Exception:
        return None


def _win_owner_from_pid(pid):
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        GetSecurityInfo = advapi32.GetSecurityInfo
        GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
        ]
        GetSecurityInfo.restype = wintypes.DWORD

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        LocalFree = kernel32.LocalFree
        LocalFree.argtypes = [wintypes.HLOCAL]
        LocalFree.restype = wintypes.HLOCAL

        READ_CONTROL = 0x00020000
        SE_KERNEL_OBJECT = 6
        OWNER_SECURITY_INFORMATION = 0x00000001

        proc_handle = OpenProcess(READ_CONTROL, False, pid)
        if not proc_handle:
            return None

        owner_sid = wintypes.LPVOID()
        security_desc = wintypes.LPVOID()
        status = GetSecurityInfo(
            proc_handle,
            SE_KERNEL_OBJECT,
            OWNER_SECURITY_INFORMATION,
            ctypes.byref(owner_sid),
            None,
            None,
            None,
            ctypes.byref(security_desc),
        )

        user = None
        if status == 0 and owner_sid:
            user = _lookup_account_sid(owner_sid)

        if security_desc:
            LocalFree(security_desc)
        CloseHandle(proc_handle)
        return user
    except Exception:
        return None


def _resolve_username(pid, name, raw_user, cache, service_users):
    if raw_user:
        return raw_user
    cached = cache.get(pid)
    if cached and cached.get("name") == name:
        return cached.get("user")
    if service_users and pid in service_users:
        user = service_users.get(pid)
        cache[pid] = {"name": name, "user": user}
        return user
    user = _win_username_from_pid(pid)
    if not user:
        user = _win_owner_from_pid(pid)
    if not user:
        user = _win_session_username(pid)
    if not user and name and name.lower() == "dwm.exe":
        session_id = _win_session_id(pid)
        if session_id is not None:
            user = f"DWM-{session_id}"
    if not user:
        if pid == 0:
            user = _well_known_account_name("SYSTEM")
        elif pid == 4 or (name and name.lower() == "system"):
            user = _well_known_account_name("SYSTEM")
        elif name and name.lower() in SYSTEM_PROCESS_NAMES:
            user = _well_known_account_name("SYSTEM")
        else:
            user = "UNKNOWN"
    cache[pid] = {"name": name, "user": user}
    return user


def _query_tasklist_usernames():
    if os.name != "nt":
        return {}
    result = {}
    cmd = ["tasklist", "/V", "/FO", "CSV"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return result
    output = proc.stdout.strip()
    if not output:
        return result
    try:
        reader = csv.reader(io.StringIO(output))
        header = next(reader, None)
        for row in reader:
            if len(row) < 7:
                continue
            try:
                pid = int(row[1])
            except Exception:
                pid = 0
            user = (row[6] or "").strip()
            if not pid or not user:
                continue
            if user.upper() in {"N/A", "N/D"}:
                continue
            result[pid] = user
    except Exception:
        return result
    return result


def collect_snapshot(state):
    cpu_count = psutil.cpu_count(logical=True) or 1
    _prime_cpu_percent()
    _enable_debug_privilege()
    last_net = psutil.net_io_counters()
    last_net_time = time.time()
    system_drive = os.getenv("SystemDrive", "C:") + "\\"
    boot_time = psutil.boot_time()
    user_cache = {}
    tasklist_cache = {}
    tasklist_last_query = 0.0

    while state.running:
        t0 = time.time()
        rows = []
        service_users = _build_service_user_map()
        unknown_pids = []

        for proc in psutil.process_iter(
            attrs=["pid", "name", "username", "memory_info"]
        ):
            try:
                pid = proc.info["pid"]
                name = proc.info.get("name") or "?"
                user = _resolve_username(
                    pid, name, proc.info.get("username"), user_cache, service_users
                )
                if "\\" in user:
                    user = user.split("\\")[-1]
                mem = proc.info["memory_info"].rss // (1024 * 1024)

                if pid == 0:
                    cpu = 0.0
                else:
                    cpu = proc.cpu_percent(None) / cpu_count
                    if cpu < 0:
                        cpu = 0.0
                    elif cpu > 100:
                        cpu = 100.0

                rows.append(
                    {
                        "pid": pid,
                        "name": name,
                        "user": user,
                        "cpu": cpu,
                        "mem": mem,
                    }
                )
                if user == "UNKNOWN":
                    unknown_pids.append(pid)
            except Exception:
                continue

        now = time.time()
        if unknown_pids and (now - tasklist_last_query) >= TASKLIST_REFRESH:
            tasklist_last_query = now
            task_users = _query_tasklist_usernames()
            if task_users:
                for pid, user in task_users.items():
                    tasklist_cache[pid] = {"user": user, "ts": now}

        if unknown_pids:
            for row in rows:
                if row.get("user") != "UNKNOWN":
                    continue
                pid = row.get("pid")
                cached = tasklist_cache.get(pid)
                if not cached:
                    continue
                if (now - cached.get("ts", 0)) > TASKLIST_TTL:
                    continue
                user = cached.get("user")
                if not user:
                    continue
                row["user"] = user
                user_cache[pid] = {"name": row.get("name"), "user": user}

        rows.sort(key=lambda r: r["cpu"], reverse=True)

        try:
            sys_cpu = psutil.cpu_percent(None)
        except Exception:
            sys_cpu = 0.0

        try:
            vm = psutil.virtual_memory()
            mem_total_mb = vm.total // (1024 * 1024)
            mem_used_mb = (vm.total - vm.available) // (1024 * 1024)
            mem_used_gb = mem_used_mb / 1024.0
            mem_percent = vm.percent
        except Exception:
            mem_total_mb = 0
            mem_used_mb = 0
            mem_used_gb = 0.0
            mem_percent = 0.0

        try:
            du = psutil.disk_usage(system_drive)
            disk_total_gb = du.total / (1024 * 1024 * 1024)
            disk_used_gb = (du.total - du.free) / (1024 * 1024 * 1024)
            disk_percent = du.percent
        except Exception:
            disk_total_gb = 0.0
            disk_used_gb = 0.0
            disk_percent = 0.0

        try:
            net_now = psutil.net_io_counters()
            dt = max(0.1, now - last_net_time)
            down_bps = (net_now.bytes_recv - last_net.bytes_recv) / dt
            up_bps = (net_now.bytes_sent - last_net.bytes_sent) / dt
            last_net = net_now
            last_net_time = now
        except Exception:
            down_bps = 0.0
            up_bps = 0.0

        with state.lock:
            state.rows = rows
            state.system = {
                "cpu_percent": sys_cpu,
                "mem_total_mb": mem_total_mb,
                "mem_used_mb": mem_used_mb,
                "mem_used_gb": mem_used_gb,
                "mem_percent": mem_percent,
                "disk_total_gb": disk_total_gb,
                "disk_used_gb": disk_used_gb,
                "disk_percent": disk_percent,
                "net_down_bps": down_bps,
                "net_up_bps": up_bps,
                "system_drive": system_drive,
                "uptime_seconds": max(0, int(now - boot_time)),
            }
            if state.selected_pid is None and state.rows:
                state.selected_pid = state.rows[0]["pid"]
        state.ui_event.set()

        elapsed = time.time() - t0
        timeout = max(0, SNAPSHOT_INTERVAL - elapsed)
        if state.refresh_event.wait(timeout):
            state.refresh_event.clear()
            continue

import os
import time

import psutil

SNAPSHOT_INTERVAL = 1.0


def _prime_cpu_percent():
    for proc in psutil.process_iter():
        try:
            proc.cpu_percent(None)
        except Exception:
            pass


def collect_snapshot(state):
    cpu_count = psutil.cpu_count(logical=True) or 1
    _prime_cpu_percent()
    last_net = psutil.net_io_counters()
    last_net_time = time.time()
    system_drive = os.getenv("SystemDrive", "C:") + "\\"
    boot_time = psutil.boot_time()

    while state.running:
        t0 = time.time()
        rows = []

        for proc in psutil.process_iter(
            attrs=["pid", "name", "username", "memory_info"]
        ):
            try:
                pid = proc.info["pid"]
                name = proc.info.get("name") or "?"
                user = proc.info.get("username") or "?"
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
            except Exception:
                continue

        rows.sort(key=lambda r: r["cpu"], reverse=True)

        now = time.time()
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

        elapsed = time.time() - t0
        timeout = max(0, SNAPSHOT_INTERVAL - elapsed)
        if state.refresh_event.wait(timeout):
            state.refresh_event.clear()
            continue

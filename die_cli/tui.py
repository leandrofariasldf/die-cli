import curses
import ctypes
import msvcrt
import os
import sys
import threading
import time
from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import beeps
from .actions import action_worker
from .process_snapshot import collect_snapshot

REFRESH_UI_HZ = 30
POLL_INTERVAL_WT = 0.05
POLL_INTERVAL_CONHOST = 0.12
NET_BAR_CAPS = {"KBPS": 2000.0, "MBPS": 200.0, "GBPS": 2.0}
LOGO_DIE_BASE = [
    "██████╗ ██╗███████╗",
    "██╔══██╗██║██╔════╝",
    "██║  ██║██║█████╗  ",
    "██║  ██║██║██╔══╝  ",
    "██████╔╝██║███████╗",
    "╚═════╝ ╚═╝╚══════╝",
]
LOGO_SKULL = []
LOGO_HEIGHT = len(LOGO_DIE_BASE)
SKULL_STYLE = "grey35"
SKULL_FILE = "skull ascii.txt"


def _load_skull_lines():
    path = Path(__file__).resolve().parent.parent / SKULL_FILE
    for enc in ("utf-8", "cp1252"):
        try:
            text = path.read_text(encoding=enc)
            lines = [line.rstrip("\r\n") for line in text.splitlines()]
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            if lines:
                return lines
        except Exception:
            pass
    return list(LOGO_SKULL)


def _get_skull_lines():
    if not hasattr(_get_skull_lines, "_cache"):
        _get_skull_lines._cache = _load_skull_lines()
    return _get_skull_lines._cache


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []
        self.status = "READY"
        self.filter_text = ""
        self.filter_mode = False
        self.filter_input = ""
        self.selected_idx = 0
        self.selected_pid = None
        self.scroll = 0
        self.action_queue = []
        self.beep_queue = []
        self.running = True
        self.refresh_event = threading.Event()
        self.ui_event = threading.Event()
        self.system = {}


def _queue_action(state, job):
    with state.lock:
        state.action_queue.append(job)


def _queue_beep(state, pattern):
    with state.lock:
        state.beep_queue.append(pattern)


def _apply_filter(rows, filter_text):
    if not filter_text:
        return rows
    needle = filter_text.lower()
    filtered = []
    for row in rows:
        name = str(row.get("name", "")).lower()
        user = str(row.get("user", "")).lower()
        pid = str(row.get("pid", ""))
        if needle in name or needle in user or needle in pid:
            filtered.append(row)
    return filtered


def _build_view(state, max_rows):
    with state.lock:
        rows = list(state.rows)
        status = state.status
        filter_text = state.filter_text
        filter_mode = state.filter_mode
        filter_input = state.filter_input
        selected_pid = state.selected_pid
        scroll = state.scroll
        system = dict(state.system)

    rows = _apply_filter(rows, filter_text)
    selected_idx = 0

    if rows:
        if selected_pid is None:
            selected_pid = rows[0]["pid"]
        for i, row in enumerate(rows):
            if row["pid"] == selected_pid:
                selected_idx = i
                break
        else:
            selected_pid = rows[0]["pid"]
            selected_idx = 0
    else:
        selected_pid = None
        selected_idx = 0
        scroll = 0

    max_scroll = max(0, len(rows) - max_rows)
    scroll = max(0, min(scroll, max_scroll))
    if rows:
        if selected_idx < scroll:
            scroll = selected_idx
        elif selected_idx >= scroll + max_rows:
            scroll = selected_idx - max_rows + 1

    visible = rows[scroll: scroll + max_rows]

    with state.lock:
        state.selected_pid = selected_pid
        state.selected_idx = selected_idx
        state.scroll = scroll

    return {
        "rows": rows,
        "visible": visible,
        "status": status,
        "filter_text": filter_text,
        "filter_mode": filter_mode,
        "filter_input": filter_input,
        "selected_idx": selected_idx,
        "scroll": scroll,
        "system": system,
    }


def _format_uptime(seconds):
    seconds = max(0, int(seconds))
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02d}:{mins:02d}:{sec:02d}"


def _scale_vertical(lines, target_height):
    if target_height <= 0:
        return []
    if not lines:
        return [""] * target_height
    src_h = len(lines)
    return [lines[int(i * src_h / target_height)] for i in range(target_height)]


def _normalize_skull(lines):
    return list(lines)


def _trim_art(lines):
    if not lines:
        return []
    src_h = len(lines)
    src_w = max(len(line) for line in lines)
    padded = [line.ljust(src_w) for line in lines]
    rows = [i for i, line in enumerate(padded) if any(ch != " " for ch in line)]
    if not rows:
        return []
    cols = [
        j
        for j in range(src_w)
        if any(padded[i][j] != " " for i in range(src_h))
    ]
    if not cols:
        return []
    r0, r1 = rows[0], rows[-1]
    c0, c1 = cols[0], cols[-1]
    return [line[c0 : c1 + 1] for line in padded[r0 : r1 + 1]]


def _scale_art_majority(lines, target_height, target_width):
    if target_height <= 0 or target_width <= 0:
        return []
    if not lines:
        return [" " * target_width for _ in range(target_height)]
    src_h = len(lines)
    src_w = max(len(line) for line in lines)
    padded = [line.ljust(src_w) for line in lines]
    out = []
    for y in range(target_height):
        sy = int((y + 0.5) * src_h / target_height)
        if sy >= src_h:
            sy = src_h - 1
        row = []
        for x in range(target_width):
            sx = int((x + 0.5) * src_w / target_width)
            if sx >= src_w:
                sx = src_w - 1
            row.append(padded[sy][sx])
        out.append("".join(row))
    return out


def _logo_text():
    die_lines = list(LOGO_DIE_BASE)
    die_height = len(die_lines)
    die_width = max(len(line) for line in die_lines)
    skull_width = 0
    text = Text()
    for i in range(die_height):
        die_line = die_lines[i] if i < len(die_lines) else ""
        die_line = die_line.ljust(die_width)
        skull_line = ""
        text.append(die_line, style="bold red")
        text.append("  ")
        if i < die_height - 1:
            text.append("\n")
    text.append("\n")
    text.append("Die, Die, Die My Darling", style="bold white")
    return text


def _read_key():
    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        ch2 = msvcrt.getwch()
        if ch2 == "H":
            return "UP"
        if ch2 == "P":
            return "DOWN"
        return None
    if ch == "\r":
        return "ENTER"
    if ch == "\x1b":
        return "ESC"
    if ch == "\x08":
        return "BACKSPACE"
    if ch == "\x7f":
        return "CTRL_BACKSPACE"
    return ch


def _handle_filter_input(key, state):
    if key == "ESC":
        with state.lock:
            state.filter_mode = False
            state.filter_input = ""
            state.status = "FILTER CANCELED"
        state.ui_event.set()
        return

    if key == "ENTER":
        with state.lock:
            state.filter_text = state.filter_input
            state.filter_mode = False
            if state.filter_text:
                state.status = f"FILTER ON: {state.filter_text}"
            else:
                state.status = "FILTER CLEARED"
        state.ui_event.set()
        return

    if key == "BACKSPACE":
        with state.lock:
            state.filter_input = state.filter_input[:-1]
        state.ui_event.set()
        return

    if key == "CTRL_BACKSPACE":
        with state.lock:
            state.filter_input = ""
        state.ui_event.set()
        return

    if isinstance(key, str) and len(key) == 1:
        code = ord(key)
        if 32 <= code <= 126:
            with state.lock:
                state.filter_input += key
            state.ui_event.set()


def _handle_normal_input(key, state, rows, selected_idx):
    if key in ("q", "Q"):
        state.running = False
        return

    if key == "ESC":
        with state.lock:
            if state.filter_text:
                state.filter_text = ""
                state.filter_input = ""
                state.status = "FILTER CLEARED"
        return

    if key == "/":
        with state.lock:
            state.filter_mode = True
            state.filter_input = state.filter_text
        state.ui_event.set()
        return

    if key in ("k", "K") and rows:
        row = rows[selected_idx]
        with state.lock:
            state.status = f"KILLING {row['pid']} {row['name']}"
        _queue_action(
            state, {"kind": "KILL", "pid": row["pid"], "name": row["name"]}
        )
        _queue_beep(state, "short3")
        state.ui_event.set()
        return

    if key in ("t", "T") and rows:
        row = rows[selected_idx]
        with state.lock:
            state.status = f"KILLING TREE {row['pid']} {row['name']}"
        _queue_action(
            state, {"kind": "KILL_TREE", "pid": row["pid"], "name": row["name"]}
        )
        _queue_beep(state, "long")
        state.ui_event.set()
        return

    if key in ("r", "R"):
        with state.lock:
            state.status = "REFRESH"
        state.refresh_event.set()
        state.ui_event.set()
        return

    if key == "UP" and rows:
        new_idx = max(0, selected_idx - 1)
        with state.lock:
            state.selected_idx = new_idx
            state.selected_pid = rows[new_idx]["pid"]
        state.ui_event.set()
        return

    if key == "DOWN" and rows:
        new_idx = min(len(rows) - 1, selected_idx + 1)
        with state.lock:
            state.selected_idx = new_idx
            state.selected_pid = rows[new_idx]["pid"]
        state.ui_event.set()


def _build_table(view):
    table = Table(
        expand=True,
        show_header=True,
        header_style="bold white",
        box=None,
        pad_edge=False,
        row_styles=["none", "dim"],
    )
    table.add_column("PID", justify="right", width=6, no_wrap=True, overflow="crop")
    table.add_column("USER", justify="left", width=10, no_wrap=True, overflow="crop")
    table.add_column("CPU%", justify="right", width=5, no_wrap=True, overflow="crop")
    table.add_column("MEM USAGE", justify="right", width=9, no_wrap=True, overflow="crop")
    table.add_column("COMMAND", justify="left", overflow="crop")

    selected = view["selected_idx"]
    scroll = view["scroll"]
    for i, row in enumerate(view["visible"]):
        real_i = scroll + i
        style = "bold white on red" if real_i == selected else None
        mem_str = f"{row.get('mem', 0)} MB"
        table.add_row(
            str(row.get("pid", "?")),
            str(row.get("user", "?")),
            f"{row.get('cpu', 0.0):.1f}",
            mem_str,
            str(row.get("name", "?")),
            style=style,
        )

    return table


def _keys_line():
    line = Text()
    line.append("[UP/DN] ", style="bold magenta")
    line.append("Navigate  ", style="white")
    line.append("[K] ", style="bold red")
    line.append("Kill  ", style="white")
    line.append("[T] ", style="bold blue")
    line.append("Kill Tree  ", style="white")
    line.append("[/] ", style="bold green")
    line.append("Filter  ", style="white")
    line.append("[R] ", style="bold cyan")
    line.append("Refresh  ", style="white")
    line.append("[Q] ", style="bold magenta")
    line.append("Quit", style="white")
    return line


def _bar_text(pct, height, colors, bar_width=2, empty_color="#202020"):
    pct = max(0.0, min(100.0, float(pct)))
    height = max(1, int(height))
    bar_width = max(1, int(bar_width))
    filled = int(round((pct / 100.0) * height))
    text = Text()
    for i in range(height):
        idx = int(i * (len(colors) - 1) / max(1, height - 1))
        color = colors[idx]
        if i >= height - filled:
            ch = "█" * bar_width
            style = f"bold {color}"
        else:
            ch = "░" * bar_width
            style = empty_color
        text.append(ch, style=style)
        if i < height - 1:
            text.append("\n")
    return text


def _pad_text(text, current_height, target_height):
    for _ in range(max(0, target_height - current_height)):
        text.append("\n")
        text.append(" ")


def _format_rate(rate_bps):
    bits_per_sec = max(0.0, float(rate_bps)) * 8.0
    if bits_per_sec < 1_000_000.0:
        unit = "KBPS"
        value = bits_per_sec / 1_000.0
        scale = NET_BAR_CAPS["KBPS"]
    elif bits_per_sec < 1_000_000_000.0:
        unit = "MBPS"
        value = bits_per_sec / 1_000_000.0
        scale = NET_BAR_CAPS["MBPS"]
    else:
        unit = "GBPS"
        value = bits_per_sec / 1_000_000_000.0
        scale = NET_BAR_CAPS["GBPS"]
    percent = 0.0 if scale == 0 else min(100.0, (value / scale) * 100.0)

    if value >= 100:
        value_str = f"{value:>4.0f}"
    else:
        value_str = f"{value:>4.1f}"
    return f"{value_str} {unit}", percent


def _dual_bar_block(
    name_a,
    value_a,
    pct_a,
    name_b,
    value_b,
    pct_b,
    colors,
    bar_height,
    bar_width=2,
    gap_width=4,
):
    label_a = Text(f"{name_a}\n{value_a}", style="bold bright_white")
    label_b = Text(f"{name_b}\n{value_b}", style="bold bright_white")
    label_width = max(len(name_a), len(value_a), len(name_b), len(value_b))
    col_width = label_width + 2
    bar_a = _bar_text(pct_a, bar_height, colors, bar_width=bar_width)
    bar_b = _bar_text(pct_b, bar_height, colors, bar_width=bar_width)
    spacer = Text(" " * gap_width)

    block = Table.grid(padding=(0, 0))
    block.add_column(justify="center", width=col_width, no_wrap=True, overflow="crop")
    block.add_column(justify="center", width=gap_width)
    block.add_column(justify="center", width=col_width, no_wrap=True, overflow="crop")
    block.add_row(Align.center(bar_a), spacer, Align.center(bar_b))
    block.add_row(Align.center(label_a), spacer, Align.center(label_b))
    return block, (col_width * 2 + gap_width)


def _render_ui(view):
    system = view.get("system", {})
    cpu = system.get("cpu_percent", 0.0)
    mem_gb = system.get("mem_used_gb", 0.0)
    mem_pct = system.get("mem_percent", 0.0)
    down_bps = system.get("net_down_bps", 0.0)
    up_bps = system.get("net_up_bps", 0.0)
    uptime = _format_uptime(system.get("uptime_seconds", 0))

    header_height = LOGO_HEIGHT + 1
    bar_height = LOGO_HEIGHT - 1
    colors = ["#3a0c0c", "#5a1212", "#7a1717", "#9a1d1d", "#bc2222", "#ff2a2a"]
    left = _logo_text()

    up_label, up_pct = _format_rate(up_bps)
    down_label, down_pct = _format_rate(down_bps)
    net_block, net_width = _dual_bar_block(
        "UP",
        up_label,
        up_pct,
        "DOWN",
        down_label,
        down_pct,
        colors,
        bar_height,
        bar_width=2,
        gap_width=4,
    )

    cpu_value = f"{cpu:>4.1f}%"
    mem_value = f"{mem_gb:>4.1f} GB"
    cpu_block, cpu_width = _dual_bar_block(
        "CPU",
        cpu_value,
        cpu,
        "RAM",
        mem_value,
        mem_pct,
        colors,
        bar_height,
        bar_width=2,
        gap_width=4,
    )

    right = Text(
        f"UPTIME: {uptime}",
        style="bold bright_white",
    )
    _pad_text(right, 1, header_height)
    header_grid = Table.grid(expand=True)
    header_grid.add_column(justify="left")
    header_grid.add_column(justify="center", width=net_width)
    header_grid.add_column(justify="center", width=cpu_width)
    header_grid.add_column(justify="right")
    header_grid.add_row(left, net_block, cpu_block, Align.right(right))

    if view["filter_mode"]:
        filter_line = Text(f"FILTER: {view['filter_input']}", style="dim")
    elif view["filter_text"]:
        filter_line = Text(f"FILTER: {view['filter_text']}", style="dim")
    else:
        filter_line = Text("")

    status_line = Text(f"STATUS: {view['status']}", style="dim")
    group = Group(
        header_grid,
        Rule(style="grey37"),
        filter_line,
        Rule(style="grey37"),
        _build_table(view),
        Rule(style="grey37"),
        status_line,
        _keys_line(),
    )

    return Panel(
        group,
        box=box.SQUARE,
        padding=(0, 1),
        border_style="grey37",
    )


def beep_worker(state):
    while state.running:
        pattern = None
        with state.lock:
            if state.beep_queue:
                pattern = state.beep_queue.pop(0)

        if pattern is None:
            time.sleep(0.01)
            continue

        try:
            if pattern == "short":
                beeps.beep_short()
            elif pattern == "short3":
                beeps.beep_short_triplet()
            elif pattern == "long":
                beeps.beep_long()
        except Exception:
            pass


def _calc_max_rows(height):
    reserved = LOGO_HEIGHT + 8
    return max(1, height - reserved)

def _enable_vt_mode():
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        DISABLE_NEWLINE_AUTO_RETURN = 0x0008

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING | DISABLE_NEWLINE_AUTO_RETURN
        if not kernel32.SetConsoleMode(handle, new_mode):
            return False
        return True
    except Exception:
        return False


def _get_terminal_size():
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 80, 24


def _truncate_ansi(line, max_width):
    if max_width <= 0:
        return ""
    out = []
    count = 0
    i = 0
    while i < len(line) and count < max_width:
        ch = line[i]
        if ch == "\x1b" and i + 1 < len(line) and line[i + 1] == "[":
            end = line.find("m", i)
            if end == -1:
                break
            out.append(line[i : end + 1])
            i = end + 1
            continue
        if ch in ("\r", "\n"):
            break
        out.append(ch)
        count += 1
        i += 1
    out.append("\x1b[0m")
    return "".join(out)


def _render_ansi_lines(console, view, width, height):
    with console.capture() as capture:
        console.print(_render_ui(view))
    text = capture.get()
    lines = text.splitlines()
    if len(lines) < height:
        lines.extend([""] * (height - len(lines)))
    else:
        lines = lines[:height]
    return [_truncate_ansi(line, width) for line in lines]


def _ui_loop_conhost(state):
    _enable_vt_mode()
    prev_lines = []
    dirty = True
    console = None
    console_size = (0, 0)
    poll_interval = POLL_INTERVAL_CONHOST

    sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H")
    sys.stdout.flush()
    try:
        while state.running:
            width, height = _get_terminal_size()
            max_rows = _calc_max_rows(height)

            if (width, height) != console_size:
                console = Console(
                    color_system="truecolor",
                    force_terminal=True,
                    width=width,
                    height=height,
                )
                console_size = (width, height)
                prev_lines = [""] * height
                dirty = True

            key = _read_key()
            if key is not None:
                view = _build_view(state, max_rows)
                dirty = True
                if view["filter_mode"]:
                    _handle_filter_input(key, state)
                else:
                    _handle_normal_input(key, state, view["rows"], view["selected_idx"])

            if state.ui_event.is_set():
                state.ui_event.clear()
                dirty = True

            if dirty and console:
                view = _build_view(state, max_rows)
                lines = _render_ansi_lines(console, view, width, height)
                out = []
                for i in range(height):
                    line = lines[i]
                    if i >= len(prev_lines) or line != prev_lines[i]:
                        out.append(f"\x1b[{i + 1};1H{line}\x1b[0K")
                if out:
                    sys.stdout.write("".join(out))
                    sys.stdout.flush()
                prev_lines = lines
                dirty = False

            if state.ui_event.wait(poll_interval):
                state.ui_event.clear()
                dirty = True
    finally:
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()


def _ui_loop_rich(state):
    console = Console(color_system="truecolor", force_terminal=True)
    poll_interval = POLL_INTERVAL_WT
    with Live(
        console=console,
        screen=True,
        auto_refresh=False,
    ) as live:
        dirty = True
        while state.running:
            height = console.size.height
            max_rows = _calc_max_rows(height)
            key = _read_key()
            if key is not None:
                view = _build_view(state, max_rows)
                dirty = True
                if view["filter_mode"]:
                    _handle_filter_input(key, state)
                else:
                    _handle_normal_input(key, state, view["rows"], view["selected_idx"])

            if state.ui_event.is_set():
                state.ui_event.clear()
                dirty = True

            if dirty:
                view = _build_view(state, max_rows)
                live.update(_render_ui(view), refresh=True)
                dirty = False
                continue

            if state.ui_event.wait(poll_interval):
                state.ui_event.clear()
                dirty = True


def ui_loop(state):
    if os.getenv("WT_SESSION"):
        _ui_loop_rich(state)
    else:
        _ui_loop_conhost(state)


def _main():
    state = SharedState()
    threading.Thread(target=collect_snapshot, args=(state,), daemon=True).start()
    threading.Thread(target=action_worker, args=(state,), daemon=True).start()
    threading.Thread(target=beep_worker, args=(state,), daemon=True).start()
    ui_loop(state)


def run():
    _main()

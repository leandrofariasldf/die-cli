import os
import sys
import shutil
import subprocess


def _print_help_and_exit():
    from die_cli.version import __version__

    text = (
        "die-cli - Windows process exterminator\n"
        "Usage: die-cli [--version|-v] [--help|-h]\n"
        "Run the TUI (admin required): die-cli"
    )
    print(text)
    sys.exit(0)


def _print_version_and_exit():
    from die_cli.version import __version__

    print(__version__)
    sys.exit(0)


def _handle_cli_flags():
    args = sys.argv[1:]
    if any(arg in ("-h", "--help") for arg in args):
        _print_help_and_exit()
    if any(arg in ("-v", "--version") for arg in args):
        _print_version_and_exit()


def _is_admin():
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin():
    gsudo = shutil.which("gsudo")
    if not gsudo:
        print("RUN AS ADMIN OR GO HOME")
        sys.exit(1)

    exe = sys.executable
    script = os.path.abspath(__file__)
    cmd = [exe, script, *sys.argv[1:]]
    try:
        rc = subprocess.call([gsudo, "--preserve-env", "--", *cmd])
    except Exception:
        print("RUN AS ADMIN OR GO HOME")
        sys.exit(1)
    sys.exit(rc if isinstance(rc, int) else 0)


_handle_cli_flags()

if os.name == "nt" and not _is_admin():
    _relaunch_as_admin()

from die_cli.tui import run


if __name__ == "__main__":
    run()

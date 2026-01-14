# die-cli

ASCII TUI process exterminator for Windows (PowerShell, cmd, Windows Terminal, Server Core).
Dangerous by design: no confirmations.

## Run

```
py -m pip install -r requirements.txt
py -m die_cli
```

Best look: Windows Terminal with a monospaced font (Cascadia Mono/Consolas).

## Keys (quick test)

- UP/DOWN: move selection
- K: kill selected (3 short beeps)
- T: kill tree (long beep)
- /: filter by PID/USER/NAME (type, Enter apply, Esc cancel, Backspace delete, Ctrl+Backspace clear)
- R: manual refresh
- Q: quit

## Build (PyInstaller, single exe)

```
py -m pip install pyinstaller
py -m PyInstaller --onefile --name die-cli --clean -m die_cli
```

## Install (winget)

After publishing a release and submitting the manifest:

```
winget install leandrofariasldf.die
```

## Install (PowerShell fallback)

```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

If your repo name is not `leandrofariasldf/die-cli`, update `$repo` inside `install.ps1`.

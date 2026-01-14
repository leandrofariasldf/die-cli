# 💀 die-cli — process exterminator (Windows)

`die-cli` is a Windows CLI/TUI inspired by *htop*, built to **kill processes fast and brutally**.

No moral judgement. No safety rails. No “are you sure?”.  
Press **K**: the process is **dead**. Press **T**: the whole tree gets **erased**. 😈

> ⚠️ **DANGEROUS BY DESIGN**  
> This is not a “friendly” tool.  
> It’s for operators who already have permissions… and accept consequences.

---

## 🥷 Who this is for

For you who **live in the terminal**, dodge the mouse like it’s lava, and think a trackpad is just a *stress test* for your patience.

If your natural habitat is:
- PowerShell / cmd / Windows Terminal
- SSH sessions at 2AM
- keyboard shortcuts burned into muscle memory

…welcome home, terminal ninja. 🥷⌨️

---

## ✅ What it is (and what it isn’t)

### ✅ It is
- **Fast and responsive** (htop-like input feel)
- **Pure ASCII** (no Unicode box drawing)
- Works on:
  - Windows 11
  - Windows Server (GUI)
  - Windows Server Core (terminal only)
- **No confirmations**
- **No dry-run**
- **No safe mode**
- **Permadeath**, with one tiny technicality:
  - The process stays dead **until something restarts it**  
    *(service manager, watchdog, scheduled task, or you resurrecting it on purpose)*

### ❌ It is not
- A pretty Task Manager
- A “safe for end users” support tool
- A tool designed to protect you from yourself

---

## ☠️ Warning (the painfully honest version)

By using `die-cli`, you accept that:

- You **understand** what killing processes means on Windows.
- You **accept** that terminating the wrong thing can break apps, services, sessions, and your day.
- `die-cli` **does not ask**, **does not confirm**, **does not forgive**.
- If you kill something important, it might not come back  
  *(unless it’s auto-respawned or you manually restart the service)*.
- If you kill a critical service in production and take down something expensive…  
  **you may receive a friendly invitation to HR.** 🫡💼

**No moral judgement. Just execution.**

---

## 🎮 Keybindings

- `↑ / ↓` — navigate  
- `K` — **kill** selected process (no confirmation)
- `T` — **kill tree** (parent + all children recursively, children first)
- `/` — filter by name
- `R` — manual refresh
- `Q` — quit

Bottom bar shows `STATUS` for your most recent act of violence.

---

## 📦 Requirements

- **Python 3.12+**
- `psutil`
- `windows-curses` (required on Windows)

---

## 🚀 Run (dev)

Install dependencies:

```powershell
py -m pip install -r requirements.txt

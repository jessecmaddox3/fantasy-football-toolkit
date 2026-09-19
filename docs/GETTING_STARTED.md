# Your first run

> **TL;DR:** Install Python, make a folder, paste three commands, and run the fictional demo. You need no fantasy account, GitHub account, Git or AI subscription to try it.

This toolkit runs in a terminal: a window where you paste a command and press Enter. It prints tables and suggestions. You will still manage your team on Sleeper or ESPN yourself.

## 1. Get Python

Use **Python 3.11 or newer**. Python 3.13 is a good choice for this release: it is included in our test matrix. Download it from [python.org](https://www.python.org/downloads/) and follow the installer. Avoid preview releases. Linux users can use their distribution's Python and `venv` packages.

On a Mac, open **Terminal** from Applications > Utilities. On Windows, search the Start menu for **PowerShell**. On Linux, open your terminal application. Run one of these to check:

```bash
# Mac or Linux
python3 --version
```

```powershell
# Windows
py --version
```

You should see `Python 3.11`, `3.12`, `3.13` or a newer stable version. If it says command not found, finish installing Python and reopen the terminal. Python 3.11 and 3.13 on Linux and 3.12 on Windows are tested in CI; other versions are not all verified.

## 2. Make a place for it

Create a folder called `FantasyToolkit` inside your home folder. These commands create it if needed and move the terminal into it. If it already exists, use it or choose a different folder name.

Mac or Linux:

```bash
mkdir -p ~/FantasyToolkit
cd ~/FantasyToolkit
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\FantasyToolkit" | Out-Null
Set-Location "$HOME\FantasyToolkit"
```

Keep using this folder. Future league settings and live output are saved relative to it.

## 3. Install and try the example

Copy the block for your computer and press Enter. The first line creates an isolated Python environment in `.venv`; the second downloads this version and its dependencies. The last runs the demo. You do not need to activate the environment or change PowerShell's execution policy.

Mac or Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install "https://github.com/jessecmaddox3/fantasy-football-toolkit/releases/download/v0.2.0/fantasy_football_toolkit-0.2.0-py3-none-any.whl"
.venv/bin/python -m ff.cli demo
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install "https://github.com/jessecmaddox3/fantasy-football-toolkit/releases/download/v0.2.0/fantasy_football_toolkit-0.2.0-py3-none-any.whl"
.\.venv\Scripts\python.exe -m ff.cli demo
```

Installation needs internet. The demo itself works offline. It should print `Fantasy Football Toolkit: offline demo`, a draft board, and this invented example:

```text
projected: current 47.0 | optimal 67.0 | delta +20.0
```

Those are example projections, not a claim about your team's results. [See all expected output](../examples/demo-output.txt).

The wheel URL is the installable package. If you downloaded that `.whl` file manually from [the release](https://github.com/jessecmaddox3/fantasy-football-toolkit/releases/tag/v0.2.0), move it into `FantasyToolkit` and substitute its filename for the quoted URL. Dependency installation still needs internet. Do not double-click the wheel or run it as a script.

## 4. Come back later

Open your terminal again, change into `FantasyToolkit` as in step 2, and run just the last demo command. No reinstall is needed. To see every command, replace `demo` with `--help`.

When you're ready, follow [Connect your own leagues](../README.md#connect-your-own-leagues). That part uses live services and your private league information. The beginner commands use a full Python path so they work without shell activation. Wherever the README says `ff`, use `.venv/bin/python -m ff.cli` on Mac/Linux or `.\.venv\Scripts\python.exe -m ff.cli` on Windows.

## If you get stuck

**Python is too old:** install a supported version, then make a new folder/environment with that Python. An existing `.venv` keeps the interpreter it was created with.

**`venv` or `ensurepip` is unavailable on Linux:** install your distribution's `python3-venv` or equivalent package. [Python's environment documentation](https://docs.python.org/3/library/venv.html) explains the mechanism.

**The package cannot be downloaded:** check your internet connection and the release URL. Installation downloads Python dependencies as well as the toolkit. A managed work computer may restrict them; don't disable its protections.

**`No module named ff`:** run the exact install command for the same `.venv` Python, from the same folder. The toolkit is not published on PyPI, so `pip install fantasy-football-toolkit` is not this installation route.

**Live rankings or projections are unavailable:** try the offline demo to separate installation from provider availability. A live provider failure does not mean installation failed. Don't reuse old consensus ranks as current advice.

To ask for help, [open an issue](https://github.com/jessecmaddox3/fantasy-football-toolkit/issues) with your OS, Python version, command and a redacted error. Do not attach cookies, `.env`, league IDs, real league reports or cache files.

## Want to modify the code?

Download the release's **Source code (zip)**, extract it, and open a terminal in the extracted folder containing `pyproject.toml`. Create `.venv` as above, then use `.venv/bin/python -m pip install '.[dev]'` on Mac/Linux or `.\.venv\Scripts\python.exe -m pip install '.[dev]'` on Windows. Run that Python with `-m pytest tests`. See [CONTRIBUTING.md](../CONTRIBUTING.md).

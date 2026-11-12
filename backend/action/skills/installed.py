"""Lists programs from the Start Menu shortcuts so the console can offer them for the open_app whitelist."""

import json
import subprocess
from pathlib import Path

SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sh = New-Object -ComObject WScript.Shell
$dirs = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
Get-ChildItem -Path $dirs -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
  $l = $sh.CreateShortcut($_.FullName)
  [pscustomobject]@{ name = $_.BaseName; target = $l.TargetPath }
} | ConvertTo-Json -Compress
"""
SKIP_WORDS = ("uninstall", "卸载", "unins", "readme", "帮助", "help")
TIMEOUT_S = 30


def parse_shortcuts(raw: str) -> list[dict[str, str]]:
    """Programs (name, exe path) from the script's JSON; deduplicated by exe, sorted by name."""
    raw = raw.strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):  # ConvertTo-Json unwraps a single item
        data = [data]
    found: dict[str, dict[str, str]] = {}
    for item in data:
        name, target = str(item.get("name") or "").strip(), str(item.get("target") or "").strip()
        if not name or not target.lower().endswith(".exe"):
            continue
        if any(w in name.lower() or w in Path(target).name.lower() for w in SKIP_WORDS):
            continue
        found.setdefault(target.lower(), {"name": name, "exe": target})
    return sorted(found.values(), key=lambda p: p["name"].lower())


def scan_start_menu() -> list[dict[str, str]]:
    proc = subprocess.run(  # noqa: S603 - fixed script, no user input
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
        capture_output=True,
        timeout=TIMEOUT_S,
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )
    if proc.returncode != 0:
        raise RuntimeError("扫描开始菜单失败")
    return parse_shortcuts(proc.stdout.decode("utf-8", errors="replace"))

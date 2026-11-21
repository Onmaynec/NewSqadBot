"""Fail CI when tracked files contain common production credentials.

This is a conservative repository scan, not a replacement for secret scanning
in GitHub or rotation of credentials already exposed in commit history.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json", "service-account.json"}
BLOCKED_SUFFIXES = {".p12", ".pfx", ".pem", ".key"}
PATTERNS = {
    "GitHub access token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    "GitHub fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    "Discord bot token": re.compile(r"\b(?:[MN][A-Za-z0-9_-]{23}|[A-Za-z0-9_-]{24})\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
    "Discord webhook token": re.compile(
        r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d{12,}/[A-Za-z0-9_-]{30,}"
    ),
    "Private key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
}


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    findings = []
    for raw in tracked:
        if not raw:
            continue
        path = Path(raw.decode("utf-8", errors="replace"))
        if path.name.lower() in BLOCKED_NAMES or path.suffix.lower() in BLOCKED_SUFFIXES:
            findings.append(f"{path}: credential-like file is tracked")
            continue
        absolute = ROOT / path
        if not absolute.is_file() or absolute.stat().st_size > 2_000_000:
            continue
        try:
            value = absolute.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(value):
                findings.append(f"{path}: possible {label} (value redacted)")
    if findings:
        print("Secret scan failed:\n" + "\n".join(findings))
        return 1
    print(f"Secret scan passed: {len(tracked) - 1} tracked files checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Secret, identity and publication-boundary scan for this repository.

Runs in CI on every push and pull request (job `security-scan`) and in the
nightly data job before it pushes. Standard library only, no network.

    python .github/scripts/security_scan.py             # tracked files
    python .github/scripts/security_scan.py --history   # + every commit, diff and message

What fails the scan
  paths     a secret-bearing file name (.env, .dev.vars, keys, credentials, ...),
            anything under data/ outside data/published/, a non-JSON/CSV file
            in data/published/, the generated site/public/, a file over 5 MB
  content   credential formats (GitHub, Cloudflare, AWS, Slack, Stripe, npm,
            Google, private keys, JWTs, bearer tokens, credentials in URLs,
            long literal secrets assigned to secret-like names), a Cloudflare
            account id, a Firebase project other than the published one,
            absolute home-directory paths, internal decision/request ids
  identity  any token whose salted SHA-256 is listed in
            .github/security/forbidden-token-hashes.txt. The identifiers
            themselves are never committed; only their hashes are.

A finding prints the file, the line and the rule. It never prints the matched
value, so a leaked secret is not copied into a public CI log.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
HASH_FILE = ROOT / ".github" / "security" / "forbidden-token-hashes.txt"
ALLOW_FILE = ROOT / ".github" / "security" / "allowed-token-hashes.txt"
HASH_SALT = "deltakura-security-scan-v1:"
MAX_FILE_BYTES = 5 * 1024 * 1024
ALLOWED_FIREBASE_PROJECTS = {"deltakura-signals"}

# --------------------------------------------------------------------- paths

SECRET_NAME_PATTERNS = [
    r"\.env", r"\.env\..*", r".*\.env", r"\.dev\.vars", r".*\.pem", r".*\.key",
    r".*\.p12", r".*\.pfx", r".*\.jks", r".*\.keystore", r".*\.token",
    r"id_(rsa|dsa|ecdsa|ed25519)(\.pub)?", r"credentials.*", r"secrets?(\..*)?",
    r"\.npmrc", r"\.pypirc", r"\.netrc", r".*\.tfstate(\..*)?", r"service[-_]?account.*\.json",
]
SECRET_NAME_ALLOW = {".dev.vars.example"}
_SECRET_NAME = re.compile("^(" + "|".join(SECRET_NAME_PATTERNS) + ")$", re.IGNORECASE)


def path_findings(path: str, size: int) -> list[str]:
    out = []
    p = PurePosixPath(path)
    if p.name not in SECRET_NAME_ALLOW and _SECRET_NAME.match(p.name):
        out.append("secret-bearing file name")
    parts = p.parts
    if parts and parts[0] == "data":
        if len(parts) < 3 or parts[1] != "published":
            out.append("file under data/ outside data/published/")
        elif p.suffix.lower() not in {".json", ".csv"}:
            out.append("data/published/ holds only .json and .csv")
    if path.startswith("site/public/"):
        out.append("generated site/public/ must not be committed")
    if size > MAX_FILE_BYTES:
        out.append(f"file larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    return out


# ------------------------------------------------------------------- content
# Each pattern is written so that it does not match its own source text.

CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("GitHub token", re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("Cloudflare token", re.compile(r"\bcfut_[A-Za-z0-9_\-]{16,}")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI/Anthropic-style key", re.compile(r"\bsk-(ant-)?[A-Za-z0-9_\-]{20,}")),
    ("Stripe key", re.compile(r"\b(sk|pk|rk)_(live|test)_[0-9A-Za-z]{10,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("Slack/Discord webhook", re.compile(r"hooks\.slack\.com/services/T[0-9A-Z]+/|discord(app)?\.com/api/webhooks/[0-9]+/")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("GitLab token", re.compile(r"\bglpat-[0-9A-Za-z_\-]{20}\b")),
    ("private key block", re.compile(r"-----BEGIN ([A-Z]+ )*PRIVATE KEY( BLOCK)?-----")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("bearer token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=\-]{20,}")),
    ("credentials in a URL", re.compile(r"\bhttps?://[^\s/:@'\"<>()]+:[^\s/@'\"<>()]{6,}@[A-Za-z0-9.\-]+")),
    ("literal secret assignment", re.compile(
        r"(?i)\b[\w\-]*(api[_\-]?key|secret|passw(or)?d|access[_\-]?token|auth[_\-]?token|private[_\-]?key|salt)[\"']?"
        r"\s*[:=]\s*[\"'][A-Za-z0-9/+_\-.=]{24,}[\"']")),
    ("Cloudflare account id", re.compile(r"(?i)\baccount_id\s*[=:]\s*[\"']?[0-9a-f]{32}\b")),
    ("absolute home path (Windows)", re.compile(r"(?i)\b[a-z]:[\\/]+users[\\/]+[^\\/\s\"']+")),
    ("absolute home path (POSIX)", re.compile(r"(?<![\w.])(/home/[a-z_][a-z0-9_\-]*|/Users/[A-Za-z][^/\s\"'\[]*|/[a-z]/U[s]ers/[^/\s\"'\[]+)/")),
    ("per-user profile folder", re.compile(r"(?i)\bapp[d]ata[\\/]|\bone[d]rive\b")),
    ("internal decision/request id", re.compile(r"\b(DEC|REQ)-0\d{2}\b")),
]

_FIREBASE_HOST = re.compile(r"\b([a-z0-9][a-z0-9\-]{2,})\.(web\.app|firebaseapp\.com)\b")
_FIREBASE_RC_DEFAULT = re.compile(r'"default"\s*:\s*"([^"]+)"')


def firebase_findings(path: str, text: str) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _FIREBASE_HOST.finditer(line):
            if m.group(1) not in ALLOWED_FIREBASE_PROJECTS:
                out.append((i, "Firebase project other than the published one"))
    if PurePosixPath(path).name == ".firebaserc":
        for m in _FIREBASE_RC_DEFAULT.finditer(text):
            if m.group(1) not in ALLOWED_FIREBASE_PROJECTS and not m.group(1).startswith("REPLACE"):
                out.append((1, "Firebase project other than the published one"))
    return out


# ------------------------------------------------------------------ identity

_WORDS = re.compile(r"[a-z0-9]+")
_COMPOUND = re.compile(r"[a-z0-9][a-z0-9._%+@\-]*[a-z0-9]")


def token_hash(token: str) -> str:
    return hashlib.sha256((HASH_SALT + token.lower()).encode("utf-8")).hexdigest()


def load_hashes(path: Path) -> dict[str, str]:
    table: dict[str, str] = {}
    if not path.exists():
        return table
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        table[fields[0].lower()] = " ".join(fields[1:])
    return table


def load_allow(path: Path) -> set[tuple[str, str]]:
    """`<hash> <path>`: that forbidden token is tolerated in that one file."""
    allow: set[tuple[str, str]] = set()
    if not path.exists():
        return allow
    for raw in path.read_text(encoding="utf-8").splitlines():
        fields = raw.split("#", 1)[0].split()
        if len(fields) >= 2:
            allow.add((fields[0].lower(), fields[1]))
    return allow


def identity_findings(path: str, text: str, forbidden: dict[str, str],
                      allow: set[tuple[str, str]]) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(text.lower().splitlines(), 1):
        tokens = set(_WORDS.findall(line)) | set(_COMPOUND.findall(line))
        for tok in tokens:
            h = token_hash(tok)
            if h in forbidden and (h, path) not in allow:
                out.append((i, f"forbidden identifier [{forbidden[h] or 'unlabelled'}]"))
    return out


# ---------------------------------------------------------------------- scan

# A literal that is obviously a placeholder is not a secret.
_PLACEHOLDER = re.compile(r"(?i)change[-_]?me|example|placeholder|replace|dummy|not[-_]?a[-_]?secret|fake|local[-_]development")


def scan_text(path: str, text: str, forbidden, allow) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        for name, rx in CONTENT_RULES:
            m = rx.search(line)
            if not m:
                continue
            if name == "literal secret assignment" and _PLACEHOLDER.search(m.group(0)):
                continue
            findings.append((i, name))
    findings += firebase_findings(path, text)
    findings += identity_findings(path, text, forbidden, allow)
    return findings


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def tracked_files() -> list[str]:
    return [p for p in git("ls-files", "-z").decode("utf-8").split("\0") if p]


def scan_tree(forbidden, allow) -> list[str]:
    report = []
    files = tracked_files()
    for path in files:
        fp = ROOT / path
        if not fp.is_file():
            continue
        data = fp.read_bytes()
        for why in path_findings(path, len(data)):
            report.append(f"{path}: {why}")
        text = data.decode("utf-8", errors="ignore")
        for line, why in scan_text(path, text, forbidden, allow):
            report.append(f"{path}:{line}: {why}")
    print(f"security-scan: {len(files)} tracked file(s) scanned")
    return report


def scan_history(forbidden, allow) -> list[str]:
    report = []
    commits = git("rev-list", "--all").decode().split()
    meta = git("log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x01").decode("utf-8", errors="ignore")
    for record in meta.split("\x01"):
        record = record.strip("\n")
        if not record:
            continue
        sha, *fields = record.split("\x00")
        text = "\n".join(fields)
        for line, why in scan_text("<commit>", text, forbidden, allow):
            report.append(f"commit {sha[:10]} metadata/message line {line}: {why}")
    # Every path ever added, and every added line, in every commit.
    names = git("log", "--all", "--diff-filter=A", "--name-only", "--format=").decode("utf-8", errors="ignore")
    for path in sorted({n for n in names.splitlines() if n}):
        for why in path_findings(path, 0):
            report.append(f"history: {path}: {why}")
    patch = git("log", "--all", "-p", "--no-color", "--format=commit %H").decode("utf-8", errors="ignore")
    current_sha, current_path = "", ""
    added: list[str] = []

    def flush() -> None:
        if added:
            for line, why in scan_text(current_path, "\n".join(added), forbidden, allow):
                report.append(f"history {current_sha[:10]} {current_path} (+line {line}): {why}")
            added.clear()

    for line in patch.splitlines():
        if line.startswith("commit "):
            flush(); current_sha = line[7:].strip()
        elif line.startswith("diff --git "):
            flush(); current_path = line.split(" b/", 1)[-1]
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    flush()
    print(f"security-scan: {len(commits)} commit(s) of history scanned")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--history", action="store_true", help="also scan every commit, diff and message")
    args = ap.parse_args(argv)

    forbidden = load_hashes(HASH_FILE)
    allow = load_allow(ALLOW_FILE)
    if not forbidden:
        print(f"security-scan: {HASH_FILE.relative_to(ROOT)} is missing or empty", file=sys.stderr)
        return 2

    report = scan_tree(forbidden, allow)
    if args.history:
        report += scan_history(forbidden, allow)

    if report:
        print(f"security-scan: FAIL - {len(report)} finding(s). Values are not printed.")
        for item in report:
            print(f"  {item}")
        return 1
    print("security-scan: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

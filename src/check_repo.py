"""Final repository check before hand-in.

Checks the things a reviewer sees first:

1. Required files exist.
2. Every relative link and image in the Markdown files points to a file that
   exists, with the exact upper/lower case (GitHub is case sensitive, Windows is not).
3. Nothing that should stay out of Git is tracked (caches, secrets files, stray
   files such as ``$null``, very large files, generated reports).
4. No password-like text in tracked files.
5. The commit history is clean: enough commits, no one-word or junk messages.

Usage (from the repository root)::

    python src/check_repo.py

Exit code 0 means no problems; 1 means at least one problem was found.
Warnings do not change the exit code.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_FILES = [
    "README.md",
    "requirements.txt",
    ".gitignore",
    "docs/metric_dictionary.md",
    "docs/data_dictionary.md",
    "docs/architecture/architecture.png",
    "reports/test_results_summary.md",
]

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Paths that must never be tracked. Matched against the start of the path or any part.
FORBIDDEN_PARTS = ("__pycache__", ".venv", "venv", ".pytest_cache", ".ipynb_checkpoints", ".vscode", ".idea")
FORBIDDEN_NAMES = ("$null", ".env", ".DS_Store", "Thumbs.db")
FORBIDDEN_PREFIXES = ("reports/outbox/",)
FORBIDDEN_PATTERNS = (
    re.compile(r"^reports/board_report_.*\.pdf$"),
    re.compile(r"^reports/latest_report\.json$"),
    re.compile(r"^reports/refresh_log\.csv$"),
    re.compile(r"^reports/pytest_results\.xml$"),
    re.compile(r".*\.(key|pem)$"),
)

MAX_FILE_MB = 25.0
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".csv", ".sql", ".yml", ".yaml", ".ini", ".cfg", ".toml", ".dax"}
SECRET_RE = re.compile(
    r"(?i)\b(?:password|passwd|app[_ ]?password|smtp[_ ]?pass(?:word)?|secret|api[_ ]?key)\b\s*[:=]\s*['\"]?"
    r"(?!\s|<|your|xxx|changeme|\*|\$|%|None|null|os\.|environ|input|getpass)([A-Za-z0-9!@#$%^&*_\-]{6,})"
)
JUNK_MESSAGES = {"update", "updates", "fix", "fixes", "wip", "commit", "changes", "stuff", "test", "tests", "misc", "final", "asdf", "."}


def run_git(root: Path, *args: str) -> list[str] | None:
    """Return the output lines of a git command, or None if git or the repo is missing."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line for line in out.stdout.splitlines() if line.strip()]


def tracked_files(root: Path) -> tuple[list[str], bool]:
    """Files that count as part of the repo. The bool says whether Git was used."""
    listed = run_git(root, "ls-files")
    if listed is not None:
        return listed, True
    skip = set(FORBIDDEN_PARTS) | {".git"}
    found = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not (set(p.relative_to(root).parts) & skip)
    ]
    return found, False


def exact_case_exists(root: Path, rel: str) -> bool:
    """True only if every part of the path matches the real file name, case included."""
    cur = root
    for part in Path(rel).parts:
        if part in ("", "."):
            continue
        try:
            names = {p.name for p in cur.iterdir()}
        except (OSError, NotADirectoryError):
            return False
        if part not in names:
            return False
        cur = cur / part
    return True


def check_required(root: Path) -> list[str]:
    return [f"Required file is missing: {r}" for r in REQUIRED_FILES if not exact_case_exists(root, r)]


def check_links(root: Path, files: list[str]) -> list[str]:
    problems: list[str] = []
    for rel in files:
        if not rel.lower().endswith(".md"):
            continue
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#")[0].split("?")[0]
            if not target:
                continue
            # Do not use Path.resolve() here: on Windows it rewrites the letter case to match
            # the disk, which would hide exactly the mistake this check exists to find.
            joined = os.path.normpath(os.path.join(path.parent, target))
            rel_target = os.path.relpath(joined, root).replace(os.sep, "/")
            if rel_target.startswith(".."):
                problems.append(f"{rel}: link leaves the repo: {m.group(1)}")
                continue
            if not exact_case_exists(root, rel_target):
                problems.append(f"{rel}: broken link or wrong letter case: {m.group(1)}")
    return problems


def check_forbidden(root: Path, files: list[str]) -> list[str]:
    problems: list[str] = []
    for rel in files:
        parts = rel.split("/")
        name = parts[-1]
        if set(parts) & set(FORBIDDEN_PARTS):
            problems.append(f"Should not be in Git (cache or editor folder): {rel}")
        elif name in FORBIDDEN_NAMES:
            problems.append(f"Should not be in Git (stray or secret file): {rel}")
        elif rel.startswith(FORBIDDEN_PREFIXES) or any(p.match(rel) for p in FORBIDDEN_PATTERNS):
            problems.append(f"Should not be in Git (generated or secret file): {rel}")
        path = root / rel
        if path.is_file() and path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            problems.append(f"File is larger than {MAX_FILE_MB:.0f} MB: {rel}")
    return problems


def check_secrets(root: Path, files: list[str]) -> list[str]:
    problems: list[str] = []
    for rel in files:
        path = root / rel
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "pragma: allowlist secret" in line:
                continue
            if SECRET_RE.search(line):
                problems.append(f"Possible secret at {rel}:{n} (check it, then remove it if real)")
    return problems


def check_history(root: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    log = run_git(root, "log", "--format=%h\t%s")
    if log is None:
        notes.append("Git history not checked: this folder is not a Git repository here.")
        return problems, notes
    if len(log) < 10:
        problems.append(f"Only {len(log)} commits; a clean history usually shows steady progress (10 or more).")
    for line in log:
        sha, _, subject = line.partition("\t")
        s = subject.strip()
        if s.lower().rstrip(".!") in JUNK_MESSAGES or len(s) < 8:
            problems.append(f"Unclear commit message: {sha} '{s}'")
    notes.append(f"{len(log)} commits found.")
    status = run_git(root, "status", "--short")
    if status:
        problems.append(f"{len(status)} files have uncommitted changes or are untracked (run 'git status').")
    return problems, notes


def run(root: Path) -> tuple[list[str], list[str]]:
    files, used_git = tracked_files(root)
    problems = []
    problems += check_required(root)
    problems += check_links(root, files)
    problems += check_forbidden(root, files)
    problems += check_secrets(root, files)
    hist_problems, notes = check_history(root)
    problems += hist_problems
    if not used_git:
        notes.append("Tracked-file checks used the folder contents, because Git was not available.")
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0] if argv else ".").resolve()
    problems, notes = run(root)
    for n in notes:
        print(f"NOTE  {n}")
    for p in problems:
        print(f"FIX   {p}")
    if problems:
        print(f"\n{len(problems)} problem(s) found.")
        return 1
    print("\nRepo check passed: no problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Tests for the final repository check (src/check_repo.py).

Each test builds a tiny throw-away folder so the checker's rules are proven:
links must resolve with exact letter case, stray files and secrets are caught,
and junk commit messages are flagged.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import check_repo as cr  # noqa: E402


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_links_found_and_missing(tmp_path):
    _write(tmp_path, "docs/a.md", "ok")
    _write(tmp_path, "README.md", "[good](docs/a.md) [bad](docs/missing.md) [web](https://x.org) ![i](docs/a.md#top)")
    problems = cr.check_links(tmp_path, ["README.md"])
    assert len(problems) == 1 and "missing.md" in problems[0]


def test_link_letter_case_is_checked(tmp_path):
    _write(tmp_path, "docs/screenshots/a.png")
    _write(tmp_path, "README.md", "![x](docs/Screenshots/a.png)")
    assert cr.check_links(tmp_path, ["README.md"])


def test_links_inside_code_blocks_ignored(tmp_path):
    _write(tmp_path, "README.md", "```\n[x](nope.md)\n```")
    assert cr.check_links(tmp_path, ["README.md"]) == []


def test_forbidden_files_flagged(tmp_path):
    files = ["src/__pycache__/a.pyc", "$null", ".env", "reports/board_report_2025-12.pdf", "src/ok.py"]
    problems = cr.check_forbidden(tmp_path, files)
    assert len(problems) == 4
    assert not any("ok.py" in p for p in problems)


def test_secret_detection(tmp_path):
    bad = "smtp_pass" + 'word = "abcd1234efgh"'      # built in pieces so this test file is not itself flagged
    _write(tmp_path, "a.py", bad + "\n")
    _write(tmp_path, "c.py", bad + "  # pragma: allowlist secret\n")
    _write(tmp_path, "b.py", 'password = os.environ["X"]\npassword = "<your password>"\n')
    assert len(cr.check_secrets(tmp_path, ["a.py", "b.py", "c.py"])) == 1


def test_required_files(tmp_path):
    assert len(cr.check_required(tmp_path)) == len(cr.REQUIRED_FILES)


def test_history_flags_junk_messages(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    for i, msg in enumerate(["update", "Add data preparation scripts"]):
        _write(tmp_path, f"f{i}.txt")
        git("add", ".")
        git("commit", "-q", "-m", msg)
    problems, notes = cr.check_history(tmp_path)
    assert any("update" in p for p in problems)
    assert any("Only 2 commits" in p for p in problems)
    assert not any("data preparation" in p for p in problems)
    assert "2 commits" in notes[0]

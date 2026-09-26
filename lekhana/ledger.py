"""Git as the record.

Each project is one git repository under ``settings.LEKHANA_REPO_ROOT``:
``main.tex``, ``sahalekhana.sty``, and ``.sahalekhana/vimarsha/<ask>.json`` — the
full thread behind every ask, rejected candidates included. Every commit's
message ends in ``Act:`` trailers, one per act it records, so ``git log`` alone
answers who did what, in which role, typed by which device.

The database (``lekhana.models.Act``) is only an index of this record.

Anchoring: :func:`anchor_root` folds every project's HEAD into one SHA-256 that
``manage.py anchor_lekhana`` stores and, with ``--ots``, stamps on a public
append-only log. Only the hash leaves the server; content never does.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

PORTAL_NAME = "Sahalekhana portal"
PORTAL_EMAIL = "portal@heritagesemantics.org"
RECORD_DIR = ".sahalekhana"

_F, _R = "\x1f", "\x1e"


class LedgerError(RuntimeError):
    """A git operation failed. The message is safe to show the writer."""


def repo_path(slug: str) -> Path:
    return Path(settings.LEKHANA_REPO_ROOT) / slug


def _git(path: Path, *args: str, env: dict | None = None) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, text=True,
            env={**os.environ, **(env or {})},
        ).stdout
    except FileNotFoundError as exc:
        raise LedgerError("git is not installed on this server.") from exc
    except subprocess.CalledProcessError as exc:
        raise LedgerError((exc.stderr or str(exc)).strip()) from exc


def init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    _git(path, "init", "-q", "-b", "main")


@contextmanager
def lock(path: Path):
    """Serialise read-modify-commit on one repository across workers."""
    with open(Path(path) / ".git" / "sahalekhana.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def head(path: Path) -> str | None:
    if not (Path(path) / ".git").exists():
        return None
    try:
        return _git(path, "rev-parse", "--verify", "-q", "HEAD").strip() or None
    except LedgerError:
        return None


def read(path: Path, name: str = "main.tex") -> str:
    target = Path(path) / name
    return target.read_text(encoding="utf-8") if target.exists() else ""


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def commit(path: Path, *, files: dict[str, str], message: str,
           trailers: list[tuple[str, str]], author_name: str, author_email: str) -> str:
    """Write ``files``, then commit with ``trailers``. Commits even when no file
    changed (``--allow-empty``): a rejection alone is still a recorded act."""
    for rel, text in files.items():
        target = Path(path) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    body = "\n".join(f"{key}: {_one_line(value)}" for key, value in trailers)
    env = {"GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
           "GIT_COMMITTER_NAME": PORTAL_NAME, "GIT_COMMITTER_EMAIL": PORTAL_EMAIL}
    _git(path, "commit", "-q", "--allow-empty", "-m", _one_line(message), "-m", body, env=env)
    return head(path)


def log(path: Path, limit: int = 300) -> list[dict]:
    if head(path) is None:
        return []
    out = _git(path, "log", f"-n{limit}",
               f"--format=%H{_F}%an{_F}%aI{_F}%s{_F}%(trailers:only,unfold){_R}")
    entries = []
    for record in out.split(_R):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, author, date, subject, trailer_text = record.split(_F, 4)
        trailers = [tuple(part.strip() for part in line.split(":", 1))
                    for line in trailer_text.splitlines() if ":" in line]
        entries.append({"sha": sha, "author": author, "date": date,
                        "subject": subject, "trailers": trailers,
                        "acts": [parse_act(v) for k, v in trailers if k == "Act"]})
    return entries


def file_history(path: Path, name: str = "main.tex", limit: int = 200) -> list[dict]:
    """Every commit that touched ``name`` (newest first), with the file's text at it."""
    if head(path) is None:
        return []
    out = _git(path, "log", f"-n{limit}", f"--format=%H{_F}%an{_F}%aI{_F}%s{_R}", "--", name)
    entries = []
    for record in out.split(_R):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, author, date, subject = record.split(_F, 3)
        entries.append({"sha": sha, "author": author, "date": date, "subject": subject,
                        "text": _git(path, "show", f"{sha}:{name}")})
    return entries


def file_changed_since(path: Path, base_sha: str, name: str = "main.tex") -> bool:
    """Did ``name`` change between ``base_sha`` and HEAD? An unknown base counts as changed."""
    if not base_sha:
        return head(path) is not None
    try:
        _git(path, "cat-file", "-e", f"{base_sha}^{{commit}}")
        _git(path, "diff", "--quiet", base_sha, "HEAD", "--", name)
    except LedgerError:
        return True
    return False


def parse_act(value: str) -> dict:
    """``"3 affirm agent=human:ram role=sakshin yukti=upamana"`` -> a dict."""
    parts = value.split()
    fields = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
    return {"seq": parts[0] if parts else "", "kind": parts[1] if len(parts) > 1 else "",
            "is_ai": fields.get("agent", "").startswith("ai:"), **fields}


def anchor_root(heads: list[tuple[str, str]]) -> str:
    """One SHA-256 over every (project, HEAD) pair, order-independent."""
    lines = "\n".join(f"{slug} {sha}" for slug, sha in sorted(heads))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()

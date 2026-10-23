"""Config directory: OAuth client secret, token and upload ledger. Never inside a git repository."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "YT_UPLOAD_HOME"
DEFAULT_HOME = "~/.youtube-upload"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Home:
    root: Path

    @property
    def client_secret(self) -> Path:
        return self.root / "client_secret.json"

    @property
    def token(self) -> Path:
        return self.root / "token.json"

    @property
    def ledger(self) -> Path:
        return self.root / "uploads.json"


def enclosing_git_repo(path: Path) -> Path | None:
    """Return the first ancestor (or the path itself) that contains a .git entry."""
    p = path.resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_home(cli_value: str | None = None) -> Home:
    raw = cli_value or os.environ.get(ENV_HOME) or DEFAULT_HOME
    root = Path(raw).expanduser()
    repo = enclosing_git_repo(root)
    if repo is not None:
        raise ConfigError(
            f"config directory {root} is inside a git repository ({repo}); "
            f"put it outside any repository (default {DEFAULT_HOME})"
        )
    return Home(root=root)


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def harden(home: Home) -> None:
    """Tighten permissions on whatever already exists in the config directory."""
    if home.root.exists():
        home.root.chmod(0o700)
    for f in (home.client_secret, home.token, home.ledger):
        if f.exists():
            f.chmod(0o600)


def write_private(path: Path, text: str) -> None:
    """Atomically write a 0600 file inside a 0700 directory."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)

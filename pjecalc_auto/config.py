"""Resolução de caminhos e configuração do projeto PJeCalcAutonomous."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


class ProjectPaths:
    """Localiza os diretórios canônicos do projeto."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.vendor = Path(
            os.environ.get("PJECALC_VENDOR_DIR", "")
        ).expanduser().resolve() if os.environ.get("PJECALC_VENDOR_DIR") else (
            self.root / "vendor" / "pjecalc" / "2.16.0"
        )
        self.seed = self.vendor / "seed"
        self.src_truth = root / "src_truth"
        self.jobs = root / ".jobs"
        self.fixtures = root / "tests" / "fixtures"
        self.skills = root / "skills"

    def seed_database(self) -> Path:
        return self.seed / "database"

    def ensure(self) -> None:
        for d in (self.jobs, self.fixtures):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def runtime_manifest(self) -> Path:
        return self.vendor / "runtime-manifest.json"

    @property
    def auditor_dir(self) -> Path:
        return self.root / "third_party" / "auditor-processual"


def _looks_like_project(path: Path) -> bool:
    return (path / "pyproject.toml").exists() or (path / "vendor" / "pjecalc").exists()


def _persisted_root() -> Optional[Path]:
    config_file = Path(
        os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    ) / "pjecalc-autonomous" / "config.json"
    if not config_file.is_file():
        return None
    try:
        value = json.loads(config_file.read_text(encoding="utf-8")).get("home")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if isinstance(value, str) and value:
        return Path(value).expanduser().resolve()
    return None


def project_root() -> Path:
    """Resolve a raiz sem depender implicitamente do CWD.

    Ordem: `PJECALC_AUTONOMOUS_HOME`, configuração persistida, diretório do
    pacote (quando contém `pyproject.toml`/vendor), e por fim CWD como fallback
    consciente para instalações antigas.
    """

    env = os.environ.get("PJECALC_AUTONOMOUS_HOME")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"PJECALC_AUTONOMOUS_HOME não existe: {root}")
        return root
    persisted = _persisted_root()
    if persisted and persisted.exists():
        return persisted
    package_root = Path(__file__).resolve().parent.parent
    if _looks_like_project(package_root):
        return package_root
    cwd = Path.cwd().resolve()
    return cwd

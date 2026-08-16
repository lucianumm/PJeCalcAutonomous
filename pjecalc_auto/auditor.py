"""Integração com o AuditorProcessual (dependência oficial).

Dependência: https://github.com/lucianum7/AuditorProcessual (skill:
`skills/legal-process-parser/SKILL.md`). O repositório é clonado em
`third_party/auditor-processual` (vendor).

Fluxo:
    processo judicial (PDF/TXT/MD)
    -> AuditorProcessual (ingest)
    -> manifest.json (schema process_manifest) + processo_estruturado.md
    -> ProcessCorpus (este projeto)

Este módulo NÃO reimplementa o parser processual: apenas orquestra o script
oficial `ingest_document.py`, preservando o princípio de não inventar fatos.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class AuditorProcessual:
    def __init__(self, auditor_dir: Path, python: Optional[str] = None):
        self.auditor_dir = auditor_dir
        self.python = python or sys.executable
        self.script = (
            auditor_dir
            / "skills"
            / "legal-process-parser"
            / "scripts"
            / "ingest_document.py"
        )

    def _cmd(self, input_path: Path, output_dir: Path, task: str,
             extra: Optional[List[str]] = None) -> List[str]:
        cmd = [
            self.python,
            str(self.script),
            str(input_path),
            "--output",
            str(output_dir),
            "--task",
            task,
        ]
        cmd.extend(extra or [])
        return cmd

    def ingest(self, input_path: Path, output_dir: Path,
               task: str = "ingest",
               extra: Optional[List[str]] = None) -> subprocess.CompletedProcess:
        """Executa a ingestão do processo no diretório de saída do job."""
        output_dir.mkdir(parents=True, exist_ok=True)
        if Path(input_path).stat().st_size > 100 * 1024 * 1024:
            raise ValueError("entrada do AuditorProcessual excede 100 MiB")
        cmd = self._cmd(input_path, output_dir, task, extra)
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                cmd, 124,
                stdout=(exc.stdout or "")[-4000:],
                stderr=(exc.stderr or "")[-4000:] + "\nAUDITOR_TIMEOUT",
            )

    def validate_output(self, output_dir: Path) -> dict:
        """Valida o contrato mínimo de saída antes de construir a spec."""
        output_dir = Path(output_dir)
        errors: list[str] = []
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.is_file():
            errors.append("manifest.json ausente")
            return {"valid": False, "errors": errors}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {"valid": False, "errors": [f"manifest inválido: {exc}"]}
        if not isinstance(manifest, dict):
            errors.append("manifest deve ser objeto JSON")
        for key in ("process_id", "source"):
            if isinstance(manifest, dict) and key not in manifest:
                errors.append(f"manifest sem campo {key}")
        markdown = [p for p in output_dir.glob("*.md") if p.is_file()]
        if not markdown:
            errors.append("markdown estruturado ausente")
        pieces = output_dir / "pecas"
        index_candidates = [
            output_dir / "index.json",
            output_dir / "pecas.json",
            pieces / "index.json",
        ]
        if not any(p.is_file() for p in index_candidates):
            # AuditorProcessual 1.2 emits the piece index as JSON Lines.  It
            # is equivalent to the historical JSON index as long as at least
            # one record parses successfully; accepting only ``index.json``
            # incorrectly rejected valid PDF ingestions.
            jsonl = output_dir / "index.jsonl"
            if not _valid_jsonl_index(jsonl):
                errors.append("índice de peças ausente ou inválido")
        return {"valid": not errors, "errors": errors,
                "manifest": manifest if not errors else None}
    def manifest_path(self, output_dir: Path) -> Path:
        """Caminho do manifest.json produzido pela ingestão."""
        return output_dir / "manifest.json"

    def revision(self) -> str | None:
        """Return the checked-out AuditorProcessual commit when available."""

        git_dir = self.auditor_dir / ".git"
        if not git_dir.exists():
            return None
        try:
            return subprocess.check_output(
                ["git", "-C", str(self.auditor_dir), "rev-parse", "HEAD"],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            ).strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def source_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def _valid_jsonl_index(path: Path) -> bool:
    """Return whether a JSONL piece index contains valid records."""

    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as stream:
            found = False
            for line in stream:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return False
                found = True
            return found
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False

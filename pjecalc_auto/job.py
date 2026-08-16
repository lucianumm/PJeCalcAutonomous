"""Persistência segura e isolada de jobs do PJe-Calc.

Um job é uma unidade de execução persistente. A criação (`create_job`) é
intencionalmente diferente do carregamento (`load_job`): carregar um job nunca
recria diretórios, nunca substitui o banco e nunca apaga o estado anterior.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from .constants import DB_DIR, DB_FILE, PRODUCT_NAME, PRODUCT_VERSION

JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
STATE_SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 100 * 1024 * 1024
SUBDIRS = (
    "input", "corpus", "calculation", "database", "pjecalc", "output",
    "audit", "artifacts", "logs", "logs/runtime", "logs/browser", "tmp",
)

_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


class InvalidJobId(ValueError):
    """Entrada externa não pode ser usada como caminho de job."""


class StateCorruptError(RuntimeError):
    """state.json existe, mas não pode ser lido com segurança."""


StateMutator = Callable[[dict], dict | None]


def validate_job_id(job_id: str) -> str:
    """Valida um ID simples, sem separadores, NUL ou traversal."""

    if not isinstance(job_id, str) or not job_id:
        raise InvalidJobId("job_id deve ser uma string não vazia")
    if "\x00" in job_id or not JOB_ID_RE.fullmatch(job_id):
        raise InvalidJobId("job_id inválido; use somente [A-Za-z0-9._-]")
    if Path(job_id).is_absolute() or "/" in job_id or "\\" in job_id:
        raise InvalidJobId("job_id não pode ser um caminho")
    if job_id in {".", ".."} or ".." in Path(job_id).parts:
        raise InvalidJobId("job_id não pode conter traversal")
    return job_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _state_lock(path: Path) -> Iterator[None]:
    with _lock_for(path):
        lock_path = path.with_name(path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - exercised on Linux CI, not Windows
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _default_state(job_id: str) -> dict:
    now = _now()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "job_id": job_id,
        "created_at": now,
        "updated_at": now,
        "current_stage": "PENDING",
        "stage": "PENDING",  # alias de compatibilidade
        "mode": None,
        "engine": PRODUCT_NAME,
        "engine_version": PRODUCT_VERSION,
        "database_initialized": False,
        "database_seed_sha256": None,
        "database_sha256": None,
        "input_hashes": {},
        "artifacts": {},
        "failure": None,
        "steps": {},
    }


class Job:
    """Diretório exclusivo `.jobs/<job_id>` com defesa contra traversal."""

    def __init__(self, root: Path, job_id: str):
        self.root = Path(root).expanduser().resolve()
        self.job_id = validate_job_id(job_id)
        self.jobs_root = (self.root / ".jobs").resolve()
        self.path = (self.jobs_root / self.job_id).resolve()
        try:
            self.path.relative_to(self.jobs_root)
        except ValueError as exc:
            raise InvalidJobId("caminho do job saiu de .jobs") from exc

    @property
    def state_path(self) -> Path:
        return self.path / "state.json"

    @property
    def database_dir(self) -> Path:
        return self.path / DB_DIR

    @property
    def database_path(self) -> Path:
        return self.database_dir / DB_FILE

    @property
    def workdir(self) -> Path:
        return self.path

    def ensure_dirs(self) -> None:
        for subdir in SUBDIRS:
            (self.path / subdir).mkdir(parents=True, exist_ok=True)

    def read_state(self) -> dict:
        if not self.state_path.exists():
            return _default_state(self.job_id)
        return self._read_state_unlocked()

    def _read_state_unlocked(self) -> dict:
        """Read and migrate state; caller may already hold the state lock."""
        if not self.state_path.exists():
            return _default_state(self.job_id)
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateCorruptError(f"state.json inválido: {self.state_path}") from exc
        if not isinstance(state, dict):
            raise StateCorruptError("state.json deve conter um objeto JSON")
        try:
            return _migrate_state(state, self.job_id)
        except (TypeError, ValueError, KeyError) as exc:
            raise StateCorruptError(f"state.json incompatível: {self.state_path}") from exc

    def write_state(self, state: dict) -> None:
        """Persiste com fsync + os.replace no mesmo volume."""

        if not isinstance(state, dict):
            raise TypeError("state deve ser um objeto dict")
        self.path.mkdir(parents=True, exist_ok=True)
        with _state_lock(self.state_path):
            self._write_state_unlocked(state)

    def update_state(self, mutator: StateMutator) -> dict:
        """Atomically read, modify and persist ``state.json``.

        ``read_state(); mutate; write_state()`` is safe against torn writes but
        not against lost updates when two MCP/CLI calls race.  This helper
        holds the per-job inter-process lock across the complete transaction;
        callers should use it whenever a state change depends on the previous
        state (steps, artifacts, failures, mode, and hashes).
        """

        if not callable(mutator):
            raise TypeError("mutator deve ser chamável")
        self.path.mkdir(parents=True, exist_ok=True)
        with _state_lock(self.state_path):
            state = self._read_state_unlocked()
            updated = mutator(state)
            if updated is not None:
                if not isinstance(updated, dict):
                    raise TypeError("mutator deve retornar dict ou None")
                state = updated
            self._write_state_unlocked(state)
            return self._read_state_unlocked()

    def _write_state_unlocked(self, state: dict) -> None:
        """Write a normalized state while the caller owns the state lock."""

        normalized = _migrate_state(dict(state), self.job_id)
        normalized["updated_at"] = _now()
        normalized["stage"] = normalized.get("current_stage", normalized.get("stage"))
        temp = self.state_path.with_name(
            f"state.json.tmp.{os.getpid()}.{threading.get_ident()}"
        )
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(normalized, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.state_path)
        finally:
            temp.unlink(missing_ok=True)

    def initialize_database(self, seed: Path, *, reset: bool = False,
                            confirm: bool = False) -> Path:
        """Copia o seed uma única vez; reset exige confirmação explícita."""

        seed_dir = Path(seed).expanduser().resolve()
        source = seed_dir / DB_FILE
        if not source.is_file():
            raise FileNotFoundError(f"Seed H2 não encontrado: {source}")
        self.ensure_dirs()
        self.database_dir.mkdir(parents=True, exist_ok=True)
        destination = self.database_path
        if destination.is_symlink():
            raise StateCorruptError("banco H2 via symlink não é aceito")
        if destination.exists() and not reset:
            actual_hash = _sha256(destination)
            seed_hash = _sha256(source)

            def mark_existing(state: dict) -> None:
                if not state.get("database_initialized"):
                    state["database_initialized"] = True
                    state["database_seed_sha256"] = state.get("database_seed_sha256") or seed_hash
                    state["database_sha256"] = actual_hash

            self.update_state(mark_existing)
            return destination
        if reset and not confirm:
            raise PermissionError("reset do banco exige confirm=True")
        if destination.exists():
            backup = self.database_dir / (
                f"{DB_FILE}.backup.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
            shutil.move(str(destination), str(backup))
        shutil.copy2(source, destination)
        seed_hash = _sha256(source)
        database_hash = _sha256(destination)
        self.update_state(lambda state: state.update({
            "database_initialized": True,
            "database_seed_sha256": seed_hash,
            "database_sha256": database_hash,
        }))
        return destination

    def copy_seed_database(self, seed: Path) -> Path:
        """Compatibilidade: inicialização sem reset."""

        return self.initialize_database(seed)

    def register_input(self, source: Path) -> tuple[Path, str]:
        """Copia entrada isolada e registra SHA-256 no estado."""

        source_arg = Path(source).expanduser()
        if source_arg.is_symlink():
            raise ValueError("entrada via symlink não é aceita")
        source = source_arg.resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Entrada não encontrada: {source}")
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("entrada excede o limite de 100 MiB")
        self.ensure_dirs()
        destination = self.path / "input" / source.name
        if destination.is_symlink():
            raise ValueError("destino de entrada via symlink não é aceito")
        if source != destination:
            digest_source = _sha256(source)
            if destination.exists() and _sha256(destination) != digest_source:
                raise FileExistsError(
                    f"entrada já registrada com conteúdo diferente: {destination.name}"
                )
            if not destination.exists():
                temp = destination.with_name(
                    f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                try:
                    with source.open("rb") as src, temp.open("xb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                        dst.flush()
                        os.fsync(dst.fileno())
                    os.replace(temp, destination)
                finally:
                    temp.unlink(missing_ok=True)
        digest = _sha256(destination)
        relative = str(destination.relative_to(self.path)).replace("\\", "/")
        self.update_state(lambda state: state.setdefault("input_hashes", {}).update({
            relative: digest,
        }))
        return destination, digest


def _migrate_state(state: dict, job_id: str) -> dict:
    defaults = _default_state(job_id)
    defaults.update(state)
    defaults["schema_version"] = max(int(defaults.get("schema_version") or 1), STATE_SCHEMA_VERSION)
    defaults["job_id"] = job_id
    if "current_stage" not in state and "stage" in state:
        defaults["current_stage"] = state["stage"]
    if "stage" not in state:
        defaults["stage"] = defaults["current_stage"]
    for key in ("steps", "input_hashes", "artifacts"):
        if not isinstance(defaults.get(key), dict):
            defaults[key] = {}
    return defaults


def create_job(root: Path, job_id: Optional[str] = None) -> Job:
    """Cria um job novo; ID existente causa erro explícito."""

    jid = validate_job_id(job_id) if job_id is not None else uuid.uuid4().hex[:12]
    job = Job(root, jid)
    job.jobs_root.mkdir(parents=True, exist_ok=True)
    try:
        # mkdir sem pré-checagem é a operação atômica que arbitra dois
        # criadores concorrentes para o mesmo ID.
        job.path.mkdir()
    except FileExistsError as exc:
        raise FileExistsError(f"Job já existe: {jid}; use load_job()") from exc
    job.ensure_dirs()
    job.write_state(_default_state(jid))
    return job


def load_job(root: Path, job_id: str) -> Job:
    """Carrega job existente sem criar, copiar seed ou apagar estado."""

    job = Job(root, job_id)
    if not job.path.is_dir():
        raise FileNotFoundError(f"Job não encontrado: {job.job_id}")
    return job

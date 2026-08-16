"""RuntimePJeCalc — inicia/para o PJe-Calc 2.16.0 real.

Princípio: o motor de cálculo REAL é o do PJe-Calc (Calculo.liquidar()). Este
módulo apenas gerencia o processo que executa esse motor. Ele NÃO reimplementa
cálculo em Python.

Estratégia de boot (baseada em `docs/02-bootstrap/STARTUP_FLOW.md`, CONFIRMED):

- O `Lancador` (Windows) faz, na parte Java efetiva:
    1. `System.setProperty("caminho.instalacao", <cwd>)`
    2. valida `.dados/pjecalc.h2.db`
    3. `TomCat("tomcat")` -> `setCatalinaHome("tomcat")`
    4. `org.apache.catalina.startup.Bootstrap` (init + start)
    5. (somente Windows) SystemTray/Janela Swing + FirefoxPortable

- A parte GUI (tray/janela/FirefoxPortable) é específica de Windows e não é
  necessária para o cálculo. Em Linux, iniciamos o MESMO Tomcat embutido via
  `org.apache.catalina.startup.Bootstrap`, reproduzindo os passos 1-4.

- O webapp usa DataSource `jdbc:h2:.dados/pjecalc` relativo ao CWD do processo
  Tomcat, portanto o processo roda com CWD = diretório isolado do job.

- O processo é sempre bindado em 127.0.0.1 (nunca 0.0.0.0), com porta HTTP 9257.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .constants import (
    BIND_ADDRESS,
    DB_DIR,
    DB_FILE,
    FIRST_PARTY_JARS,
    HTTP_PORT,
    runtime_jvm_args,
    WEBAPP_CONTEXT,
)

BOOTSTRAP_MAIN = "org.apache.catalina.startup.Bootstrap"
SHUTDOWN_CMD = "SHUTDOWN"
SHUTDOWN_PORT = 9256

# Tempo máximo para o healthcheck responder (segundos).
START_TIMEOUT = 120
HEALTH_URL = f"http://{BIND_ADDRESS}:{HTTP_PORT}/{WEBAPP_CONTEXT}"


class JavaNotFoundError(RuntimeError):
    pass


class RuntimeAlreadyRunning(RuntimeError):
    pass


class RuntimeConfigurationError(RuntimeError):
    pass


class PortConflict(RuntimeError):
    pass


class RuntimeBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeProbe:
    status: str
    http_status: Optional[int] = None
    url: str = HEALTH_URL
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.status == "PJECALC_HEALTHY"


class PJeCalcRuntime:
    """Gerencia uma instância isolada do PJe-Calc."""

    def __init__(self, project_root: Path, vendor_dir: Path):
        self.project_root = project_root
        self.vendor_dir = vendor_dir
        self.proc: Optional[subprocess.Popen] = None
        self._java_home: Optional[Path] = None
        self._workdir: Optional[Path] = None
        self._stdout = None
        self._lock_path: Optional[Path] = None
        self._shutdown_token: Optional[str] = None

    # -- Java ----------------------------------------------------------------
    def detect_java(self) -> Path:
        """Retorna o home do Java 8.

        A instalação Windows do PJe-Calc já traz uma JRE em ``bin/jre``.
        Depois de consultar a configuração explícita e o PATH, procuramos
        essa JRE ao lado do checkout para que a execução local não dependa de
        uma variável de ambiente frágil.
        """
        env_home = os.environ.get("PJECALC_JAVA_HOME")
        if env_home:
            p = Path(env_home)
            if (p / "bin" / "java").exists() or (p / "bin" / "java.exe").exists():
                self._java_home = p
                return p

        # Prefer the installer-bundled Java 8 over a newer global Java.  A
        # Java 17+ on PATH is common on developer machines but is not a valid
        # runtime for the legacy PJe-Calc bytecode.
        bundled_candidates = (
            self.project_root / "bin" / "jre",
            self.project_root.parent / "bin" / "jre",
        )
        for candidate in bundled_candidates:
            if any((candidate / "bin" / name).is_file()
                   for name in ("java", "java.exe")):
                self._java_home = candidate.resolve()
                return self._java_home

        # java global (validated by java_version() at boot)
        home = shutil.which("java")
        if home:
            resolved = Path(home).resolve()
            # sobe dois níveis: .../bin/java -> .../
            self._java_home = resolved.parent.parent
            return self._java_home

        raise JavaNotFoundError(
            "Java não encontrado. Defina PJECALC_JAVA_HOME apontando para um JDK/JRE 8."
        )

    def java_version(self) -> str:
        """Retorna a versão reportada pelo executável e exige Java 8."""
        java = self._java_bin()
        proc = subprocess.run(
            [str(java), "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        output = (proc.stderr or proc.stdout).strip()
        if proc.returncode != 0:
            raise RuntimeConfigurationError(f"java -version falhou: {output}")
        first = output.splitlines()[0] if output else ""
        if '"1.8.' not in first and '"8' not in first:
            raise RuntimeConfigurationError(
                f"JAVA_VERSION_UNSUPPORTED: PJe-Calc 2.16.0 requer Java 8; {first}"
            )
        return first

    def _java_bin(self) -> Path:
        if self._java_home is None:
            self._java_home = self.detect_java()
        for name in ("java", "java.exe"):
            p = self._java_home / "bin" / name
            if p.exists():
                return p
        raise JavaNotFoundError(f"Binário java não encontrado em {self._java_home}")

    # -- Classpath -----------------------------------------------------------
    def classpath(self) -> str:
        lib = self.vendor_dir / "lib"
        jars = sorted(glob.glob(str(lib / "*.jar")))
        if not jars:
            raise FileNotFoundError(f"Nenhum jar em {lib}")
        return os.pathsep.join(jars)

    # -- Porta ---------------------------------------------------------------
    def probe_runtime(self) -> RuntimeProbe:
        """Distingue porta aberta, PJe-Calc saudável e serviço estrangeiro."""
        try:
            response = requests.get(HEALTH_URL, timeout=2, allow_redirects=True)
        except requests.RequestException as exc:
            if _port_open(BIND_ADDRESS, HTTP_PORT):
                return RuntimeProbe("FOREIGN_SERVICE", detail=repr(exc))
            return RuntimeProbe("PORT_CLOSED", detail=repr(exc))
        body = response.text[:20000].lower()
        expected_path = f"/{WEBAPP_CONTEXT}"
        is_expected_endpoint = response.url.startswith(
            f"http://{BIND_ADDRESS}:{HTTP_PORT}{expected_path}"
        )
        has_login_contract = "usuario us" in body or (
            "usuarious" in body and "senhaus" in body
        )
        if response.status_code < 500 and is_expected_endpoint and (
            "pjecalc" in body or "pje-calc" in body or has_login_contract
        ):
            return RuntimeProbe("PJECALC_HEALTHY", response.status_code, response.url)
        return RuntimeProbe("FOREIGN_SERVICE", response.status_code, response.url)

    def is_running(self) -> bool:
        return self.probe_runtime().healthy

    def _acquire_lock(self, workdir: Path) -> None:
        lock_dir = self.project_root / ".runtime"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "pjecalc.lock"
        payload = {
            "pid": os.getpid(),
            "workdir": str(workdir),
            "created_at": time.time(),
            "shutdown_token": self._shutdown_token,
        }
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            # Recupera lock de um launcher que morreu sem deixar um processo
            # Java vivo; nunca remove lock cujo PID ainda está ativo.
            try:
                current = json.loads(lock_path.read_text(encoding="utf-8"))
                owner = int(current.get("process_pid") or current.get("pid"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                owner = None
            if owner is not None and not _pid_exists(owner):
                lock_path.unlink(missing_ok=True)
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                raise RuntimeBusy(f"runtime ocupado; lock={lock_path}") from exc
        try:
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)
        self._lock_path = lock_path

    def _release_lock(self) -> None:
        if self._lock_path is None:
            return
        try:
            data = json.loads(self._lock_path.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self._lock_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            # Não removemos lock que não conseguimos provar ser nosso.
            pass
        self._lock_path = None

    # -- Boot ----------------------------------------------------------------
    def start(self, workdir: Path,
              catalina_home: Optional[Path] = None) -> subprocess.Popen:
        """Inicia o Tomcat embutido com CWD isolado em `workdir`.

        `workdir` (CWD do processo) contém `.dados/pjecalc.h2.db` (DataSource
        relativo). `catalina.home` aponta para os arquivos empacotados e
        `catalina.base` é uma cópia por job, evitando logs/temp/work
        compartilhados entre execuções.
        """
        probe = self.probe_runtime()
        if probe.status == "PJECALC_HEALTHY":
            raise RuntimeAlreadyRunning(
                f"Porta {HTTP_PORT} já em uso. Encerre a instância existente primeiro."
            )
        if probe.status == "FOREIGN_SERVICE":
            raise PortConflict(f"PORT_CONFLICT na porta {HTTP_PORT}: {probe.detail}")

        manifest_ok, manifest_detail = verify_runtime_manifest(
            self.vendor_dir, self.vendor_dir / "runtime-manifest.json"
        )
        if not manifest_ok:
            raise RuntimeConfigurationError(
                f"runtime-manifest.json não confere com o vendor: {manifest_detail}"
            )

        self._acquire_lock(workdir)

        (workdir / "logs" / "runtime").mkdir(parents=True, exist_ok=True)
        try:
            java = self._java_bin()
            self.java_version()
            jvm_args = runtime_jvm_args()
            catalina_home = catalina_home or (self.vendor_dir / "tomcat")
            catalina_base = workdir / "runtime" / "tomcat-base"
            if not catalina_base.exists():
                catalina_base.parent.mkdir(parents=True, exist_ok=True)
                # Cópia explícita: links/junctions variam entre Windows e Linux.
                shutil.copytree(catalina_home, catalina_base)
            self._shutdown_token = secrets.token_hex(24)
            _configure_shutdown_token(catalina_base, self._shutdown_token)
        except Exception:
            self._release_lock()
            raise

        cmd = [
            str(java),
            *jvm_args,
            f"-Dcatalina.home={catalina_home}",
            f"-Dcatalina.base={catalina_base}",
            f"-Dcaminho.instalacao={workdir}",
            "-cp",
            self.classpath(),
            BOOTSTRAP_MAIN,
            "start",
        ]

        self._workdir = workdir
        try:
            self._stdout = open(workdir / "logs" / "runtime" / "tomcat.out.log", "ab")
            self.proc = subprocess.Popen(
                cmd, cwd=str(workdir), stdout=self._stdout,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        except Exception:
            if self._stdout is not None:
                self._stdout.close()
            self._stdout = None
            self._release_lock()
            raise
        try:
            payload = {
                "pid": os.getpid(),
                "process_pid": self.proc.pid,
                "workdir": str(workdir),
                "created_at": time.time(),
                "shutdown_token": self._shutdown_token,
            }
            self._lock_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
            self._release_lock()
            raise
        return self.proc

    def wait_healthy(self, timeout: int = START_TIMEOUT) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False
            if self.probe_runtime().healthy:
                return True
            time.sleep(1)
        return False

    def stop(self) -> None:
        """Envia SHUTDOWN ao Tomcat e, em último caso, mata o processo."""
        # Um launcher CLI pode ter terminado depois de deixar o Java vivo. Só
        # enviamos SHUTDOWN nesse caso se o lock contiver o PID do processo
        # filho criado por nós; uma porta estrangeira sem esse vínculo nunca é
        # tocada.
        if self.proc is None:
            if not self._external_lock_owns_live_process():
                return
        # O server.xml da cópia por job recebe um token aleatório. Nunca
        # envie o valor histórico ``SHUTDOWN`` para uma porta loopback, pois
        # qualquer processo local poderia derrubar a instância.
        if self._shutdown_token:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((BIND_ADDRESS, SHUTDOWN_PORT))
                s.sendall(self._shutdown_token.encode("ascii"))
                s.close()
            except OSError:
                pass

        if self.proc is not None:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._kill_process_group()
            self.proc = None
        if self._stdout is not None:
            self._stdout.close()
            self._stdout = None
        # aguarda liberar a porta
        deadline = time.time() + 15
        while self.probe_runtime().status == "PJECALC_HEALTHY" and time.time() < deadline:
            time.sleep(0.5)
        if self.proc is None:
            if self.probe_runtime().status != "PJECALC_HEALTHY" and self._lock_path:
                self._lock_path.unlink(missing_ok=True)
                self._lock_path = None
        else:
            self._release_lock()

    def _external_lock_owns_live_process(self) -> bool:
        if self._lock_path is None:
            self._lock_path = self.project_root / ".runtime" / "pjecalc.lock"
        try:
            payload = json.loads(self._lock_path.read_text(encoding="utf-8"))
            process_pid = int(payload.get("process_pid"))
            self._shutdown_token = payload.get("shutdown_token") or None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        if not _pid_exists(process_pid):
            if self.probe_runtime().status == "PORT_CLOSED":
                self._lock_path.unlink(missing_ok=True)
            return False
        return self.probe_runtime().status == "PJECALC_HEALTHY"

    def _kill_process_group(self) -> None:
        if self.proc is None:
            return
        try:
            if os.name == "nt":
                self.proc.kill()
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass


def _port_open(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _configure_shutdown_token(catalina_base: Path, token: str) -> None:
    """Set a per-job Tomcat shutdown command in the mutable base copy."""

    server_xml = catalina_base / "conf" / "server.xml"
    try:
        content = server_xml.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeConfigurationError(
            f"server.xml do catalina.base não pôde ser lido: {server_xml}"
        ) from exc
    updated, count = re.subn(
        r'(<Server\b[^>]*\bshutdown=")([^"]*)(")',
        rf"\g<1>{token}\g<3>",
        content,
        count=1,
    )
    if count != 1:
        raise RuntimeConfigurationError(
            "server.xml não contém exatamente um atributo de shutdown configurável"
        )
    try:
        server_xml.write_text(updated, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise RuntimeConfigurationError(
            f"server.xml do catalina.base não pôde ser atualizado: {server_xml}"
        ) from exc


def verify_runtime_manifest(vendor: Path, manifest_path: Path) -> tuple[bool, str]:
    """Confere hashes e contenção de todos os arquivos antes de executar Java."""

    try:
        external_anchor = os.environ.get("PJECALC_RUNTIME_MANIFEST_SHA256")
        if external_anchor:
            actual_manifest_hash = _sha256_file(manifest_path)
            if actual_manifest_hash.casefold() != external_anchor.strip().casefold():
                return False, "runtime manifest não confere com âncora externa"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files")
        if not isinstance(files, dict):
            return False, "manifest sem mapa de arquivos"
        mismatches: list[str] = []
        listed = set(files)
        vendor_root = vendor.resolve()
        for rel, entry in files.items():
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts or not isinstance(entry, dict):
                mismatches.append(f"entrada inválida:{rel}")
                continue
            target = (vendor_root / rel_path).resolve()
            try:
                target.relative_to(vendor_root)
            except ValueError:
                mismatches.append(f"fora do vendor:{rel}")
                continue
            if not target.is_file() or _hash_file(target) != entry.get("sha256"):
                mismatches.append(rel)
        actual = {
            str(path.relative_to(vendor_root)).replace("\\", "/")
            for path in vendor_root.rglob("*")
            if path.is_file() and path.name != manifest_path.name
        }
        mismatches.extend(f"não listado:{name}" for name in sorted(actual - listed)[:20])
        if mismatches:
            return False, "; ".join(mismatches[:20])
        return True, f"{len(files)} arquivos verificados"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return False, "manifest ilegível"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True

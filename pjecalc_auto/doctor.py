"""Doctor estático/runtime com estados honestos."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from .config import ProjectPaths
from .constants import BIND_ADDRESS, DB_FILE, HTTP_PORT, PRODUCT_VERSION
from .runtime import PJeCalcRuntime


def _check(name: str, ok: bool | None, detail: str = "") -> Dict[str, Any]:
    status = "NOT_TESTED" if ok is None else ("PASS" if ok else "FAIL")
    return {"check": name, "status": status, "detail": detail}


def run_doctor(paths: ProjectPaths, full: bool = False) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = [
        _check("os", True, platform.system()),
        _check("architecture", platform.machine().lower() in {"amd64", "x86_64", "aarch64", "arm64"}, platform.machine()),
        _check("python", sys.version_info >= (3, 10), platform.python_version()),
    ]
    runtime = PJeCalcRuntime(paths.root, paths.vendor)
    try:
        runtime.detect_java()
        version = runtime.java_version()
        checks.append(_check("java", True, version))
    except Exception as exc:
        checks.append(_check("java", False, repr(exc)))

    vendor = paths.vendor
    jars_ok = vendor.joinpath("lib").is_dir() and any(vendor.joinpath("lib").glob("*.jar"))
    version_ok = _manifest_version(paths.runtime_manifest)
    checks.append(_check("pjecalc_runtime", jars_ok, str(vendor)))
    checks.append(_check("pjecalc_version", version_ok, PRODUCT_VERSION))
    checks.append(_check("runtime_manifest", paths.runtime_manifest.is_file(), str(paths.runtime_manifest)))
    if full:
        manifest_ok, manifest_detail = _verify_runtime_manifest(paths.vendor, paths.runtime_manifest)
        checks.append(_check("runtime_manifest_integrity", manifest_ok, manifest_detail))
        anchor = os.environ.get("PJECALC_RUNTIME_MANIFEST_SHA256")
        if anchor:
            try:
                anchor_ok = _hash_file(paths.runtime_manifest).casefold() == anchor.strip().casefold()
                anchor_detail = "âncora externa conferida" if anchor_ok else "âncora externa divergente"
            except OSError as exc:
                anchor_ok = False
                anchor_detail = repr(exc)
            checks.append(_check("runtime_manifest_anchor", anchor_ok, anchor_detail))
        else:
            checks.append(_check(
                "runtime_manifest_anchor", None,
                "não configurada; defina PJECALC_RUNTIME_MANIFEST_SHA256 fora do repositório",
            ))
    seed_db = paths.seed_database() / DB_FILE
    checks.append(_check("h2_seed", seed_db.is_file() and seed_db.stat().st_size > 0, str(seed_db)))
    checks.append(_check("seed_schema", _seed_schema_present(vendor), "persistence.xml"))
    try:
        from .selector_audit import audit_selectors
        selector_report = audit_selectors(vendor)
        checks.append(_check("selector_static_audit", selector_report["ok"],
                             f"{selector_report['matched']}/{selector_report['total']} static matches"))
    except Exception as exc:
        selector_report = {"ok": False, "error": repr(exc)}
        checks.append(_check("selector_static_audit", False, repr(exc)))
    probe = runtime.probe_runtime()
    checks.append(_check("port_9257", probe.status == "PORT_CLOSED", probe.status))
    checks.append(_check("pjecalc_health", None if probe.status == "PORT_CLOSED" else probe.healthy, probe.detail))
    auditor_script = paths.auditor_dir / "skills" / "legal-process-parser" / "scripts" / "ingest_document.py"
    auditor_revision = _git_revision(paths.auditor_dir)
    checks.append(_check(
        "auditor_processual",
        auditor_script.is_file() and auditor_revision is not None,
        f"{auditor_script}; commit={auditor_revision or 'desconhecido'}",
    ))
    try:
        import mcp  # noqa: F401
        from importlib.metadata import version as package_version
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        mcp_version = package_version("mcp")
        mcp_status = True
        mcp_detail = f"mcp {mcp_version}; FastMCP importável"
    except (ImportError, ModuleNotFoundError) as exc:
        mcp_status = False
        mcp_detail = (
            f"SDK MCP ausente ou incompatível: {exc}; "
            "instale mcp>=1.0,<2.0"
        )
    checks.append(_check("mcp_sdk", mcp_status, mcp_detail))
    try:
        import selenium  # noqa: F401
        selenium_status = True
        selenium_detail = "selenium importável"
    except ImportError as exc:
        selenium_status = False
        selenium_detail = f"não instalado: {exc}"
    checks.append(_check("browser_selenium", selenium_status, selenium_detail))
    try:
        import pypdf  # noqa: F401
        pdf_status = True
        pdf_detail = "pypdf importável"
    except ImportError as exc:
        pdf_status = False
        pdf_detail = f"não instalado: {exc}"
    checks.append(_check("pdf_reader", pdf_status, pdf_detail))
    try:
        import PIL  # noqa: F401
        pillow_status = True
        pillow_detail = "Pillow importável"
    except ImportError as exc:
        pillow_status = False
        pillow_detail = f"não instalado: {exc}"
    checks.append(_check("pdf_image_support", pillow_status, pillow_detail))
    firefox, firefox_detail = _find_executable(
        "PJECALC_FIREFOX_BIN",
        (
            paths.root.parent / "navegador" / "windows" / "App" / "Firefox64" / "firefox.exe",
            paths.root.parent / "navegador" / "windows" / "App" / "Firefox" / "firefox.exe",
        ),
        ("firefox", "firefox.exe"),
    )
    gecko, gecko_detail = _find_executable(
        "PJECALC_GECKODRIVER",
        (
            paths.root / ".tools" / "geckodriver" / "geckodriver.exe",
            paths.root.parent / "geckodriver.exe",
        ),
        ("geckodriver", "geckodriver.exe"),
    )
    checks.append(_check("firefox", firefox is not None, firefox_detail))
    checks.append(_check("geckodriver", gecko is not None, gecko_detail))
    try:
        free = shutil.disk_usage(str(paths.root)).free
        checks.append(_check("disk", free > 512 * 1024 * 1024, f"free={free // (1024 * 1024)} MB"))
    except OSError as exc:
        checks.append(_check("disk", None, repr(exc)))
    try:
        from .golden import GOLDEN_CASES
        golden_detail = f"{len(GOLDEN_CASES)} casos sintéticos embutidos"
        golden_ok = bool(GOLDEN_CASES)
    except Exception as exc:
        golden_ok = False
        golden_detail = repr(exc)
    checks.append(_check("golden_fixtures", golden_ok, golden_detail))

    if full:
        checks.extend([
            _check("credentials", bool(os.environ.get("PJECALC_USERNAME") and os.environ.get("PJECALC_PASSWORD")), "environment only"),
            _check("runtime_boot", None, "não executado automaticamente pelo doctor --full sem confirmação/licença"),
            _check("official_exports", None, "dependente de runtime + Firefox + sessão autenticada"),
        ])

    required_fail = any(item["status"] == "FAIL" for item in checks)
    overall = "FAIL" if required_fail else ("PARTIAL" if any(item["status"] == "NOT_TESTED" for item in checks) else "PASS")
    return {"product": "PJeCalcAutonomous", "pjecalc_version": PRODUCT_VERSION,
            "overall": overall, "checks": checks, "selector_audit": selector_report,
            "full": full}


def _manifest_version(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version") == PRODUCT_VERSION
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _find_executable(env_name: str, bundled: tuple[Path, ...],
                     path_names: tuple[str, ...]) -> tuple[Path | None, str]:
    """Resolve an executable from explicit env, bundled install, or PATH."""
    configured = os.environ.get(env_name)
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.is_file():
            return candidate, str(candidate)
        return None, f"{candidate} (configurado em {env_name}, mas ausente)"
    for candidate in bundled:
        if candidate.is_file():
            return candidate.resolve(), str(candidate.resolve())
    for name in path_names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve(), str(Path(found).resolve())
    return None, "não encontrado (env, instalação embutida ou PATH)"


def _git_revision(path: Path) -> str | None:
    """Read a dependency revision without shell interpolation."""

    git_dir = path / ".git"
    if not git_dir.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _verify_runtime_manifest(vendor: Path, path: Path) -> tuple[bool, str]:
    """Verifica todos os hashes do runtime vendorizado quando solicitado."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        files = manifest.get("files")
        if manifest.get("version") != PRODUCT_VERSION or not isinstance(files, dict):
            return False, "manifest schema/version inválido"
        mismatches: list[str] = []
        listed = set(files)
        for rel, entry in files.items():
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts or not isinstance(entry, dict):
                mismatches.append(f"entrada inválida:{rel}")
                continue
            target = (vendor / rel_path).resolve()
            try:
                target.relative_to(vendor.resolve())
            except ValueError:
                mismatches.append(f"fora do vendor:{rel}")
                continue
            if not target.is_file() or _hash_file(target) != entry.get("sha256"):
                mismatches.append(rel)
        actual = {
            str(p.relative_to(vendor)).replace("\\", "/")
            for p in vendor.rglob("*") if p.is_file() and p.name != path.name
        }
        missing_from_manifest = sorted(actual - listed)
        if missing_from_manifest:
            mismatches.extend(f"não listado:{item}" for item in missing_from_manifest[:20])
        if mismatches:
            return False, "; ".join(mismatches[:20])
        return True, f"{len(files)} arquivos verificados"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return False, "manifest ilegível"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seed_schema_present(vendor: Path) -> bool:
    meta = vendor / "tomcat" / "webapps" / "pjecalc" / "WEB-INF" / "classes" / "META-INF"
    return (meta / "persistence.xml").is_file() or (meta / "persistence.xml.tmp").is_file()


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

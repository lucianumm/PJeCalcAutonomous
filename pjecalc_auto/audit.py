"""Auditoria cruzada fail-closed de Spec, UI/H2, PJC e PDF."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from .config import ProjectPaths
from .job import load_job
from .validators import ImportPjcValidator, validate_pdf
from .external_spec import ExternalCalculationSpec
from .validators import ExternalSpecValidator


def audit_job(paths: ProjectPaths, job_id: str) -> Dict[str, Any]:
    job = load_job(paths.root, job_id)
    spec_path = job.path / "calculation" / "calculation_spec.json"
    artifacts_dir = job.path / "artifacts"
    output_dir = job.path / "output"  # compatibilidade com jobs antigos
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    spec: Dict[str, Any] = {}
    spec_present = spec_path.is_file()
    if spec_present:
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            spec_present = False

    unresolved = _collect_unresolved(spec)
    pjc = _first_artifact(artifacts_dir, output_dir, ("calculo.pjc", ".pjc"))
    pdf = _first_artifact(artifacts_dir, output_dir, ("calculo.pdf", ".pdf"))
    pjc_validation = ImportPjcValidator().validate(pjc) if pjc else None
    pdf_validation = validate_pdf(pdf) if pdf else None
    state = job.read_state()
    engine_execution_path = artifacts_dir / "engine_execution.json"
    engine_execution = _read_json(engine_execution_path)
    validation = _read_json(artifacts_dir / "validation.json")
    reconciliation = _read_json(artifacts_dir / "reconciliation.json")
    artifact_manifest = _read_json(artifacts_dir / "manifest.json")
    external_spec = _read_json(job.path / "calculation" / "external_spec.json")
    mode = state.get("mode")
    external_validation = None
    if external_spec is not None:
        try:
            external_validation = ExternalSpecValidator().validate(
                ExternalCalculationSpec.model_validate(external_spec)
            )
        except Exception:
            external_validation = None
    spec_sha256 = _sha256(spec_path) if spec_present else None
    state_input_hashes = state.get("input_hashes", {})
    engine_bound = bool(
        engine_execution
        and engine_execution.get("spec_sha256") == spec_sha256
        and engine_execution.get("source_input_hashes", {}) == state_input_hashes
    )
    manifest_bound = bool(
        artifact_manifest
        and artifact_manifest.get("job_id") == job_id
        and artifact_manifest.get("spec_sha256") == spec_sha256
        and artifact_manifest.get("source_input_hashes", {}) == state_input_hashes
        and _manifest_matches_files(artifact_manifest, artifacts_dir)
    )

    checks = {
        "source_consistent": bool(state.get("input_hashes")) or bool(spec.get("processo")),
        "spec_bound_to_source": spec.get("source_input_hashes", {}) == state_input_hashes,
        "spec_complete": (
            bool(external_validation and external_validation.valid)
            if mode == "EXTERNAL_UPDATE" else
            (bool(pjc_validation and pjc_validation.valid)
             if mode == "IMPORT_PJC" else spec_present and not unresolved)
        ),
        "validation_passed": bool(validation and validation.get("errors", 0) == 0 and validation.get("ok") is True),
        "engine_confirmed": bool(engine_execution and engine_execution.get("evidence")),
        "engine_bound_to_input": engine_bound,
        "artifact_manifest_integrity": manifest_bound,
        "pjc_valid": bool(pjc_validation and pjc_validation.valid),
        "pdf_valid": bool(pdf_validation and pdf_validation.valid),
        "h2_crosscheck_passed": bool(reconciliation and reconciliation.get("h2_verified") is True),
        "totals_crosscheck_passed": bool(reconciliation and reconciliation.get("totals_verified") is True),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    pre_audit = {
        "job_id": job_id, "stage": "PRE_AUDIT", "spec_present": spec_present,
        "unresolved_critical": unresolved, "fail_closed": bool(unresolved),
    }
    post_audit = {
        "job_id": job_id, "stage": "POST_AUDIT", "status": status,
        "checks": checks,
        "artifacts": {
            "pjc": _artifact_info(pjc, pjc_validation),
            "pdf": _artifact_info(pdf, pdf_validation),
            "reconciliation": reconciliation,
            "manifest": artifact_manifest,
        },
    }
    lineage = {
        "job_id": job_id,
        "edges": [
            {"from": "input", "to": "corpus"},
            {"from": "corpus", "to": "calculation_spec"},
            {"from": "calculation_spec", "to": "PJe-Calc"},
            {"from": "PJe-Calc", "to": "calculo.pjc"},
            {"from": "PJe-Calc", "to": "calculo.pdf"},
        ],
        "engine_confirmed": checks["engine_confirmed"],
    }
    audit = {"job_id": job_id, "status": status, "ok": status == "PASS",
             "checks": checks, "pre_audit": pre_audit, "post_audit": post_audit}

    _write_json(artifacts_dir / "pre_audit.json", pre_audit)
    _write_json(artifacts_dir / "post_audit.json", post_audit)
    _write_json(artifacts_dir / "lineage.json", lineage)
    _write_json(artifacts_dir / "audit.json", audit)
    # aliases esperados pelo layout antigo
    _write_json(job.path / "audit" / "pre_audit.json", pre_audit)
    _write_json(job.path / "audit" / "post_audit.json", post_audit)
    _write_json(job.path / "audit" / "calculation_lineage.json", lineage)
    final = job.path / "audit" / "final_report.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(_render_report(job_id, audit), encoding="utf-8")
    job.update_state(lambda state: state.update({
        "current_stage": "AUDIT",
        "artifacts": {
            **(state.get("artifacts") or {}),
            "audit": str(artifacts_dir / "audit.json"),
        },
        "failure": None if status == "PASS" else {
            "reason": "AUDIT_FAILED", "checks": checks,
        },
    }))

    return {"ok": status == "PASS", "status": status, "job_id": job_id,
            "checks": checks, "audit_path": str(artifacts_dir / "audit.json"),
            "final_report": str(final)}


def _collect_unresolved(spec: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    contract = spec.get("contract") or {}
    for key in ("salario", "jornada", "admissao", "demissao"):
        rv = contract.get(key)
        if not rv or rv.get("status") in {"UNRESOLVED", "CONFLICTING"} or rv.get("value") is None:
            problems.append(f"contract.{key}")
    for i, v in enumerate(spec.get("verbas") or []):
        for key in ("base", "divisor", "quantidade", "percentual", "valor"):
            if key not in v:
                continue
            rv = v.get(key)
            if rv is None or rv.get("status") in {"UNRESOLVED", "CONFLICTING"} or rv.get("value") is None:
                problems.append(f"verba[{i}].{key}")
    return problems


def _first_artifact(primary: Path, fallback: Path, names: tuple[str, ...]) -> Path | None:
    candidates: List[Path] = []
    for directory in (primary, fallback):
        for name in names:
            candidate = directory / name if "." in name else None
            if candidate and candidate.is_file():
                candidates.append(candidate)
        for suffix in names:
            candidates.extend(sorted(directory.glob(f"*{suffix}")))
    return candidates[0] if candidates else None


def _artifact_info(path: Path | None, validation: Any) -> dict:
    if path is None:
        return {"path": None, "valid": False}
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "valid": bool(validation and validation.valid),
            "details": validation.as_dict() if validation else None}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_matches_files(manifest: dict, artifacts_dir: Path) -> bool:
    entries = manifest.get("artifacts")
    if not isinstance(entries, dict):
        return False
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            return False
        path = artifacts_dir / name
        if not path.is_file() or _sha256(path) != entry.get("sha256"):
            return False
        if entry.get("size") != path.stat().st_size:
            return False
    return True


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _render_report(job_id: str, audit: dict) -> str:
    lines = ["# Relatório de Auditoria", "", f"- Job: `{job_id}`",
             f"- Status: `{audit['status']}`", "", "## Checks", ""]
    lines.extend(f"- {key}: `{value}`" for key, value in audit["checks"].items())
    lines += ["", "A auditoria só é PASS quando todas as evidências independentes são válidas.", ""]
    return "\n".join(lines)

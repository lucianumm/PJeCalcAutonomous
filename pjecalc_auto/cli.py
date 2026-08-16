"""CLI `pjecalc-auto` com operações explícitas e fail-closed."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import ProjectPaths, project_root


def _paths() -> ProjectPaths:
    paths = ProjectPaths(project_root())
    paths.ensure()
    return paths


def _print(result: dict) -> int:
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor
    return _print(run_doctor(_paths(), full=args.full))


def cmd_runtime(args: argparse.Namespace) -> int:
    from .runtime import PJeCalcRuntime
    from .job import create_job, load_job
    paths = _paths()
    runtime = PJeCalcRuntime(paths.root, paths.vendor)
    if args.action == "status":
        probe = runtime.probe_runtime()
        return _print({"ok": probe.healthy, "status": probe.status,
                       "detail": probe.detail})
    if args.action == "start":
        try:
            job = load_job(paths.root, "manual")
        except FileNotFoundError:
            job = create_job(paths.root, "manual")
        try:
            job.initialize_database(paths.seed_database())
            runtime.start(job.path)
            ok = runtime.wait_healthy()
            return _print({"ok": ok, "status": "PJECALC_HEALTHY" if ok else "RUNTIME_UNHEALTHY"})
        except Exception as exc:
            return _print({"ok": False, "reason": "RUNTIME_START_FAILED", "error": repr(exc)})
    runtime.stop()
    return _print({"ok": runtime.probe_runtime().status != "PJECALC_HEALTHY", "status": "STOPPED"})


def cmd_golden(args: argparse.Namespace) -> int:
    from .golden import run_golden_tests
    results = run_golden_tests(_paths())
    passed = sum(1 for item in results if item.get("status") == "PASS")
    total = len(results)
    return _print({"ok": passed == total and total > 0, "passed": passed,
                   "total": total, "tests": results})


def cmd_calculate(args: argparse.Namespace) -> int:
    from .ops import calculate_from_process
    return _print(calculate_from_process(_paths(), args.input, args.liquidation_date))


def cmd_analyze(args: argparse.Namespace) -> int:
    from .ops import analyze_process
    result = analyze_process(_paths(), args.input)
    result["operation"] = "analyze"
    return _print(result)


def cmd_audit(args: argparse.Namespace) -> int:
    from .audit import audit_job
    return _print(audit_job(_paths(), args.job_id))


def cmd_status(args: argparse.Namespace) -> int:
    from .job import load_job
    job = load_job(_paths().root, args.job_id)
    return _print({"ok": True, "job_id": args.job_id, "state": job.read_state()})


def cmd_resume(args: argparse.Namespace) -> int:
    from .job import load_job
    job = load_job(_paths().root, args.job_id)
    state = job.read_state()
    if state.get("current_stage") == "DONE" and state.get("failure") is None:
        return _print({"ok": True, "status": "ALREADY_COMPLETE", "job_id": args.job_id, "state": state})
    return _print({"ok": False, "status": "RESUME_REQUIRES_CANONICAL_PIPELINE",
                   "reason": "PIPELINE_NOT_EXECUTED", "job_id": args.job_id, "state": state})


def cmd_export(args: argparse.Namespace) -> int:
    from .job import load_job
    from .validators import ImportPjcValidator, validate_pdf
    job = load_job(_paths().root, args.job_id)
    artifact = job.path / "artifacts" / ("calculo.pjc" if args.kind == "pjc" else "calculo.pdf")
    validation = ImportPjcValidator().validate(artifact) if args.kind == "pjc" else validate_pdf(artifact)
    return _print({"ok": validation.valid, "job_id": args.job_id,
                   "artifact": str(artifact), "validation": validation.as_dict()})


def cmd_purge(args: argparse.Namespace) -> int:
    from .job import load_job
    paths = _paths()
    job = load_job(paths.root, args.job_id)
    if not args.confirm:
        return _print({"ok": False, "reason": "CONFIRM_REQUIRED", "message": "Use --confirm para purgar o job."})
    shutil.rmtree(job.path)
    return _print({"ok": True, "job_id": args.job_id, "status": "PURGED"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pjecalc-auto")
    sub = parser.add_subparsers(dest="command", required=True)
    p_doctor = sub.add_parser("doctor", help="Diagnóstico do ambiente")
    p_doctor.add_argument("--full", action="store_true")
    p_doctor.set_defaults(func=cmd_doctor)
    p_runtime = sub.add_parser("runtime", help="Gerencia o runtime")
    p_runtime.add_argument("action", choices=["start", "stop", "status"])
    p_runtime.set_defaults(func=cmd_runtime)
    p_golden = sub.add_parser("golden", help="Golden tests")
    p_golden.add_argument("action", choices=["test"])
    p_golden.set_defaults(func=cmd_golden)
    p_calc = sub.add_parser("calculate", help="Executa fluxo end-to-end")
    p_calc.add_argument("input")
    p_calc.add_argument("--liquidation-date", dest="liquidation_date")
    p_calc.set_defaults(func=cmd_calculate)
    p_analyze = sub.add_parser("analyze", help="Ingere e constrói spec")
    p_analyze.add_argument("input")
    p_analyze.set_defaults(func=cmd_analyze)
    p_audit = sub.add_parser("audit", help="Audita um job")
    p_audit.add_argument("job_id")
    p_audit.set_defaults(func=cmd_audit)
    p_status = sub.add_parser("status", help="Estado de um job")
    p_status.add_argument("job_id")
    p_status.set_defaults(func=cmd_status)
    p_resume = sub.add_parser("resume", help="Retoma um job")
    p_resume.add_argument("job_id")
    p_resume.set_defaults(func=cmd_resume)
    for kind in ("pjc", "pdf"):
        p_export = sub.add_parser(f"export-{kind}")
        p_export.add_argument("job_id")
        p_export.set_defaults(func=cmd_export, kind=kind)
    p_purge = sub.add_parser("purge-job")
    p_purge.add_argument("job_id")
    p_purge.add_argument("--confirm", action="store_true")
    p_purge.set_defaults(func=cmd_purge)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        return _print({"ok": False, "reason": "UNHANDLED_ERROR", "error": repr(exc)})


if __name__ == "__main__":
    sys.exit(main())

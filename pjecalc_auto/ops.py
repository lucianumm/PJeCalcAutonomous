"""Operações de orquestração do PJeCalcAutonomous.

Conecta CalculationSpec -> PJe-Calc (via Browser Driver) -> resultados.

PRINCÍPIO CENTRAL: o motor de cálculo é o do PJe-Calc (Calculo.liquidar()).
Nenhuma destas funções calcula valores em Python; elas preenchem a UI oficial
(JSF 1.2 + RichFaces 3.3.4) e acionam liquidação/exportação reais.

Fluxo real implementado (seletores derivados do XHTML empacotado):

    logon.jsf (usuário/senha)
      -> pages/principal.jsf  (Criar Novo Cálculo)
      -> pages/calculo/calculo.jsf (cadastro do processo/contrato + salvar)
      -> pages/calculo/liquidacao.jsf (data de liquidação + liquidar)
      -> pages/calculo/exportacao.jsf (exportar PJC)
      -> pages/calculo/relatorio/relatorio-calculo.jsf (imprimir PDF)

Cada passo é fail-closed: se o componente esperado não aparecer, a operação
aborta com diagnóstico (screenshot/DOM/URL em `.jobs/<JOB_ID>/logs/browser/`),
em vez de prosseguir com estado inconsistente ou inventar resultado.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .browser import login
from .calculation_spec import (
    CalculationSpec,
    ResolvedValue,
    SourceStatus,
    format_br_decimal,
    format_ui_date,
    parse_br_decimal,
)
from .config import ProjectPaths
from .session import Session
from .validators import ImportPjcValidator, validate_pdf


BASE = "http://127.0.0.1:9257/pjecalc"


def _record_step(session: Session, name: str, state: str,
                 data: Optional[Dict[str, Any]] = None,
                 error: Optional[str] = None) -> None:
    """Registra etapas documentais no mesmo formato do Pipeline."""
    now = datetime.now(timezone.utc).isoformat()

    def mutate(current: dict) -> None:
        previous = current.setdefault("steps", {}).get(name) or {}
        current["steps"][name] = {
            "state": state,
            "started_at": previous.get("started_at") or now,
            "finished_at": None if state == "RUNNING" else now,
            "data": data or previous.get("data") or {},
            "error": error,
        }
        if state != "RUNNING":
            current["current_stage"] = name
            current["stage"] = name
        if error:
            current["failure"] = {"step": name, "error": error}

    session.job.update_state(mutate)


def execute_session(session: Session) -> Dict[str, Any]:
    """Executor único para os caminhos CLI, MCP e processo->spec.

    O invólucro usa o mesmo executor persistente de etapas para que falhas,
    retomadas e ``SHUTDOWN`` tenham uma representação única em ``state.json``.
    """
    from .pipeline import Pipeline, PipelineContext, Step, StepResult, StepState

    box: Dict[str, Any] = {}
    context = PipelineContext(job=session.job, spec=session.spec)
    pipeline = Pipeline(
        session.job,
        required_steps=[
            "START_PJECALC", "POPULATE", "VALIDATE", "LIQUIDATE",
            "EXPORT_PJC", "EXPORT_PDF", "AUDIT", "SHUTDOWN",
        ],
    )

    def start(_ctx, _data):
        if not session.start_runtime():
            box.update({"ok": False, "job_id": session.job.job_id,
                        "reason": "RUNTIME_START_FAILED"})
            return StepResult(StepState.FAIL, error="runtime unhealthy")
        return StepResult(StepState.PASS, {"runtime": "PJECALC_HEALTHY"})

    def populate(_ctx, _data):
        driver = session.start_browser(headless=True)
        result = populate_liquidate_and_export(session, driver)
        box.update(result)
        if result.get("ok"):
            return StepResult(StepState.PASS, result)
        return StepResult(StepState.FAIL, result, result.get("reason"))

    def validate(_ctx, _data):
        payload = _read_json_file(session.job.path / "artifacts" / "validation.json")
        ok = bool(payload and payload.get("ok") is True and payload.get("errors", 0) == 0)
        return StepResult(StepState.PASS if ok else StepState.FAIL, payload or {},
                          None if ok else "official validation evidence missing")

    def liquidate(_ctx, _data):
        payload = _read_json_file(session.job.path / "artifacts" / "engine_execution.json")
        ok = bool(payload and payload.get("evidence"))
        return StepResult(StepState.PASS if ok else StepState.FAIL, payload or {},
                          None if ok else "engine execution evidence missing")

    def export_pjc(_ctx, _data):
        path = session.job.path / "artifacts" / "calculo.pjc"
        validation = ImportPjcValidator().validate(path)
        return StepResult(StepState.PASS if validation.valid else StepState.FAIL,
                          validation.as_dict(), None if validation.valid else "PJC invalid")

    def export_pdf(_ctx, _data):
        path = session.job.path / "artifacts" / "calculo.pdf"
        validation = validate_pdf(path)
        return StepResult(StepState.PASS if validation.valid else StepState.FAIL,
                          validation.as_dict(), None if validation.valid else "PDF invalid")

    def audit(_ctx, _data):
        from .audit import audit_job
        payload = audit_job(session.paths, session.job.job_id)
        box["audit"] = payload
        return StepResult(StepState.PASS if payload.get("ok") else StepState.FAIL,
                          payload, None if payload.get("ok") else "cross-audit failed")

    def shutdown(_ctx, _data):
        session.shutdown()
        return StepResult(StepState.PASS, {"shutdown": True})

    pipeline.register(Step("START_PJECALC", start))
    pipeline.register(Step("POPULATE", populate, ["START_PJECALC"]))
    pipeline.register(Step("VALIDATE", validate, ["POPULATE"]))
    pipeline.register(Step("LIQUIDATE", liquidate, ["VALIDATE"]))
    pipeline.register(Step("EXPORT_PJC", export_pjc, ["LIQUIDATE"]))
    pipeline.register(Step("EXPORT_PDF", export_pdf, ["EXPORT_PJC"]))
    pipeline.register(Step("AUDIT", audit, ["EXPORT_PDF"]))
    pipeline.register(Step("SHUTDOWN", shutdown))
    ok = pipeline.run(context)
    if box:
        box["ok"] = bool(ok and box.get("ok", True))
        box["pipeline_ok"] = ok
        return box
    return {"ok": False, "pipeline_ok": ok, "job_id": session.job.job_id,
            "reason": "EXECUTION_FAILED", "message": "Pipeline sem resultado."}


def calculate_from_spec(
    paths: ProjectPaths,
    case_id: str,
    contract: Dict[str, Any],
    verbas: List[Dict[str, Any]],
    processo: Optional[Dict[str, Any]] = None,
    data_liquidacao: Optional[str] = None,
) -> Dict[str, Any]:
    """Cria um job, monta o spec e liquida no PJe-Calc real.

    Retorna um dicionário com status. Nunca inventa resultados: se o runtime
    não subir ou a liquidação falhar, `ok=False` e `reason` explicam o quê.
    """
    try:
        session = Session.create(paths, job_id=case_id)
    except FileExistsError:
        return {
            "ok": False,
            "reason": "JOB_ALREADY_EXISTS",
            "job_id": case_id,
            "message": "O job já existe; use operações sobre o job existente.",
        }
    session.set_contract(**{k: v for k, v in contract.items() if v is not None})
    for raw in verbas:
        v = dict(raw)
        tipo = v.pop("tipo", "Principal")
        rest = {k: val for k, val in v.items() if val is not None}
        session.add_verba(tipo, **rest)
    if processo:
        session.set_process(**{k: v for k, v in processo.items() if v is not None})
    if data_liquidacao:
        session.set_data_liquidacao(data_liquidacao)
    session.save_spec()

    refusal = session.fail_closed_refusal(mode="STANDARD")
    if refusal:
        refusal["job_id"] = session.job.job_id
        refusal["spec_path"] = str(session.save_spec())
        return refusal

    result = execute_session(session)
    result["job_id"] = session.job.job_id
    result["spec_path"] = str(session.save_spec())
    return result


def calculate_from_resolved_spec(
    paths: ProjectPaths,
    resolved_spec_path: str,
    *,
    case_id: Optional[str] = None,
    target_date: Optional[str] = None,
    external_spec_path: Optional[str] = None,
    base_pjc_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Executa uma especificação documental já resolvida pelo motor oficial.

    ``calculate_from_process`` é deliberadamente conservador: um PDF grande
    pode conter fatos suficientes para um operador humano, mas o extrator
    automático não pode escolher premissas silenciosamente. Este caminho
    permite que uma ``calculation_spec_resolved.json`` revisada, com
    proveniência, seja entregue ao MCP e enviada à UI oficial do PJe-Calc.

    O arquivo não é um resultado de cálculo. Ele contém somente parâmetros;
    valores finais, PJC e PDF continuam sendo produzidos pelo PJe-Calc. Para
    uma atualização externa, o JSON deve conter (ou apontar para) uma
    ``ExternalCalculationSpec`` e o PJC-base continua obrigatório.
    """
    source = Path(resolved_spec_path).expanduser().resolve()
    if not source.is_file():
        return {
            "ok": False,
            "reason": "RESOLVED_SPEC_NOT_FOUND",
            "message": f"Especificação resolvida não encontrada: {source}",
        }
    if source.stat().st_size > 20 * 1024 * 1024:
        return {
            "ok": False,
            "reason": "RESOLVED_SPEC_SIZE_LIMIT",
            "message": "A especificação resolvida excede o limite de 20 MiB.",
        }
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "reason": "RESOLVED_SPEC_INVALID_JSON",
            "error": repr(exc),
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "reason": "RESOLVED_SPEC_OBJECT_REQUIRED",
            "message": "A especificação resolvida deve ser um objeto JSON.",
        }

    # Aceita tanto o formato canônico direto quanto um envelope que também
    # transporta a especificação opcional de atualização externa.
    payload = raw.get("calculation_spec", raw.get("spec", raw))
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "reason": "RESOLVED_SPEC_OBJECT_REQUIRED",
            "message": "O campo calculation_spec/spec deve ser um objeto JSON.",
        }
    try:
        spec = CalculationSpec.model_validate(payload)
    except Exception as exc:
        return {
            "ok": False,
            "reason": "RESOLVED_SPEC_VALIDATION_FAILED",
            "error": repr(exc),
        }

    selected_case_id = case_id or spec.case_id
    try:
        session = Session.create(paths, job_id=selected_case_id)
    except FileExistsError:
        return {
            "ok": False,
            "reason": "JOB_ALREADY_EXISTS",
            "job_id": selected_case_id,
            "message": "O job já existe; use pjecalc_status ou um novo case_id.",
        }
    except ValueError as exc:
        return {
            "ok": False,
            "reason": "JOB_ID_INVALID",
            "error": repr(exc),
        }

    try:
        registered, _digest = session.job.register_input(source)
        spec.case_id = session.job.job_id
        spec.source_input_hashes = session.job.read_state().get("input_hashes", {})
        session.spec = spec
        if target_date:
            session.set_data_liquidacao(target_date)

        # Um spec resolvido precisa ser estrito. Aceitar strict_mode=false
        # aqui transformaria a nova ferramenta em um caminho para estimativas.
        if not session.spec.strict_mode:
            return _resolved_spec_refusal(
                session, "RESOLVED_SPEC_MUST_BE_STRICT", [],
                "A especificação resolvida deve usar strict_mode=true.",
            )

        external_payload: Any = raw.get("external_update")
        if external_spec_path:
            ext_source = Path(external_spec_path).expanduser()
            if not ext_source.is_absolute():
                ext_source = source.parent / ext_source
            ext_source = ext_source.resolve()
            try:
                external_payload = json.loads(ext_source.read_text(encoding="utf-8"))
                session.job.register_input(ext_source)
                spec.source_input_hashes = session.job.read_state().get("input_hashes", {})
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                return _resolved_spec_refusal(
                    session, "EXTERNAL_SPEC_INVALID_JSON", [], repr(exc),
                )
            if not isinstance(external_payload, dict):
                return _resolved_spec_refusal(
                    session, "EXTERNAL_SPEC_OBJECT_REQUIRED", [],
                    "A especificação externa deve ser um objeto JSON.",
                )

        session.save_spec()
        if external_payload is not None:
            return _execute_resolved_external(
                session, external_payload, source.parent, base_pjc_path,
                source_path=registered,
            )
        if base_pjc_path:
            return _resolved_spec_refusal(
                session, "EXTERNAL_SPEC_REQUIRED", [],
                "base_pjc_path só pode ser usado junto com external_spec_path ou external_update.",
            )

        refusal = session.fail_closed_refusal(mode="STANDARD")
        if refusal:
            refusal.update({
                "job_id": session.job.job_id,
                "spec_path": str(session.job.path / "calculation" / "calculation_spec.json"),
                "stage": "REQUIRES_REVIEW",
            })
            _mark_requires_review(session, refusal.get("unresolved", []), "resolved_spec_incomplete")
            return refusal

        session.job.update_state(lambda state: state.update({"mode": "STANDARD"}))
        result = execute_session(session)
        result.update({
            "job_id": session.job.job_id,
            "spec_path": str(session.job.path / "calculation" / "calculation_spec.json"),
        })
        return result
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "job_id": session.job.job_id,
            "reason": "RESOLVED_SPEC_INPUT_FAILED",
            "error": repr(exc),
        }


def _resolved_spec_refusal(session: Session, reason: str,
                           unresolved: List[str], message: str) -> Dict[str, Any]:
    """Persist a deterministic refusal for resolved-spec inputs."""
    session.save_spec()
    _mark_requires_review(session, unresolved, reason)
    return {
        "ok": False,
        "job_id": session.job.job_id,
        "status": "REQUIRES_REVIEW",
        "reason": reason,
        "message": message,
        "unresolved": unresolved,
        "spec_path": str(session.job.path / "calculation" / "calculation_spec.json"),
        "stage": "REQUIRES_REVIEW",
    }


def _execute_resolved_external(session: Session, payload: Dict[str, Any],
                               base_dir: Path,
                               base_pjc_path: Optional[str],
                               *, source_path: Path) -> Dict[str, Any]:
    """Run an external spec supplied alongside a resolved calculation spec."""
    from .external_spec import ExternalCalculationSpec
    from .validators import ExternalSpecValidator

    data = dict(payload)
    data["case_id"] = session.job.job_id
    requested_base = base_pjc_path or data.get("base_pjc_path")
    if not requested_base:
        return _resolved_spec_refusal(
            session, "BASE_PJC_REQUIRED", [],
            "Atualização externa exige o PJC oficial-base; PDF/planilha não preserva o rateio dos pagamentos.",
        )
    base = Path(requested_base).expanduser()
    if not base.is_absolute():
        base = base_dir / base
    base = base.resolve()
    if not base.is_file():
        return _resolved_spec_refusal(
            session, "BASE_PJC_REQUIRED", [],
            f"PJC-base não encontrado: {base}",
        )
    try:
        registered, _digest = session.job.register_input(base)
        session.spec.source_input_hashes = session.job.read_state().get("input_hashes", {})
        data["base_pjc_path"] = str(registered)
        ext = ExternalCalculationSpec.model_validate(data)
    except (OSError, ValueError) as exc:
        return _resolved_spec_refusal(
            session, "EXTERNAL_SPEC_VALIDATION_FAILED", [], repr(exc),
        )
    validation = ExternalSpecValidator().validate(ext)
    if not validation.valid:
        return _resolved_spec_refusal(
            session, "FAIL_CLOSED", validation.errors,
            "A especificação externa contém parâmetros críticos ausentes ou incompatíveis.",
        ) | {"validation": validation.as_dict()}
    external_path = session.save_external_spec(ext)
    session.save_spec()
    session.job.update_state(lambda state: state.update({
        "mode": "EXTERNAL_UPDATE",
        "external_update_requested": True,
        "artifacts": {
            **(state.get("artifacts") or {}),
            "external_spec": str(external_path),
            "resolved_spec": str(source_path),
        },
    }))
    try:
        runtime_ok = session.start_runtime()
    except Exception as exc:
        return {
            "ok": False,
            "job_id": session.job.job_id,
            "reason": "RUNTIME_START_FAILED",
            "message": "Não foi possível iniciar o PJe-Calc.",
            "error": repr(exc),
        }
    if not runtime_ok:
        return {
            "ok": False,
            "job_id": session.job.job_id,
            "reason": "RUNTIME_START_FAILED",
            "message": "Não foi possível iniciar o PJe-Calc.",
        }
    try:
        try:
            driver = session.start_browser(headless=True)
        except Exception as exc:
            return {
                "ok": False,
                "job_id": session.job.job_id,
                "reason": "BROWSER_START_FAILED",
                "message": "Não foi possível iniciar Firefox/geckodriver.",
                "error": repr(exc),
            }

        def _fail(step: str, message: str) -> Dict[str, Any]:
            return {
                "ok": False,
                "job_id": session.job.job_id,
                "reason": "UI_INTERACTION_FAILED",
                "step": step,
                "message": message,
            }

        try:
            result = execute_external_update(session, driver, ext, registered, _fail)
        except Exception as exc:
            return {
                "ok": False,
                "job_id": session.job.job_id,
                "reason": "EXTERNAL_UPDATE_FAILED",
                "message": "A execução externa terminou com uma exceção não tratada.",
                "error": repr(exc),
            }
        result["external_spec_path"] = str(external_path)
        result["resolved_spec_path"] = str(source_path)
        return result
    finally:
        session.shutdown()


def _prepare_process(paths: ProjectPaths, process_input: str,
                     target_date: Optional[str] = None):
    """Executa somente ingestão/contrato documental e devolve uma sessão."""
    from .auditor import AuditorProcessual
    from .process_to_spec import build_spec_from_corpus

    auditor_dir = paths.auditor_dir
    auditor = AuditorProcessual(auditor_dir)
    if not auditor_dir.is_dir() or not auditor.script.is_file():
        return None, {
            "ok": False,
            "reason": "AUDITOR_NOT_VENDORED",
            "message": (
                "AuditorProcessual não está disponível. Execute "
                "scripts/fetch_auditor.sh (ou clone o repositório oficial em "
                "third_party/auditor-processual)."
            ),
        }

    session = Session.create(paths)
    try:
        input_copy, _input_hash = session.job.register_input(Path(process_input))
    except (OSError, ValueError) as exc:
        return None, {"ok": False, "job_id": session.job.job_id,
                      "reason": "INPUT_REGISTRATION_FAILED", "error": repr(exc)}
    _record_step(session, "INGEST", "RUNNING", {"input": str(input_copy)})
    corpus_dir = session.job.path / "corpus"
    _record_step(session, "AUDITORPROCESSUAL", "RUNNING")
    try:
        proc = auditor.ingest(input_copy, corpus_dir, task="ingest")
    except Exception as exc:
        _record_step(session, "AUDITORPROCESSUAL", "FAIL", error=repr(exc))
        _record_step(session, "INGEST", "FAIL", error=repr(exc))
        return None, {"ok": False, "job_id": session.job.job_id,
                      "reason": "AUDITOR_INGEST_FAILED", "error": repr(exc)}
    if proc.returncode != 0:
        _record_step(session, "AUDITORPROCESSUAL", "FAIL",
                     {"stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]},
                     "auditor returned non-zero")
        _record_step(session, "INGEST", "FAIL", error="auditor returned non-zero")
        return None, {
            "ok": False, "job_id": session.job.job_id,
            "reason": "AUDITOR_INGEST_FAILED",
            "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
        }
    valid_output = auditor.validate_output(corpus_dir)
    if not valid_output["valid"]:
        _record_step(session, "AUDITORPROCESSUAL", "FAIL", valid_output,
                     "auditor output invalid")
        _record_step(session, "BUILD_CORPUS", "FAIL", valid_output,
                     "corpus output invalid")
        return None, {"ok": False, "job_id": session.job.job_id,
                      "reason": "AUDITOR_OUTPUT_INVALID",
            "errors": valid_output["errors"]}
    auditor_revision = auditor.revision()
    session.job.update_state(
        lambda state: state.update({
            "auditor_processual": {
                "repository": "https://github.com/lucianum7/AuditorProcessual",
                "commit": auditor_revision,
                "script": str(auditor.script),
            }
        })
    )
    _record_step(session, "AUDITORPROCESSUAL", "PASS", valid_output)
    _record_step(session, "INGEST", "PASS", {"input": str(input_copy)})
    _record_step(session, "BUILD_CORPUS", "PASS", valid_output)
    _record_step(session, "BUILD_CALCULATIONSPEC", "RUNNING")

    try:
        spec = build_spec_from_corpus(corpus_dir, strict_mode=True)
    except Exception as exc:
        _record_step(session, "BUILD_CALCULATIONSPEC", "FAIL", error=repr(exc))
        session.job.update_state(lambda state: state.update({
            "current_stage": "BUILD_CALCULATIONSPEC",
            "failure": {"reason": "SPEC_BUILD_FAILED", "error": repr(exc)},
        }))
        return None, {"ok": False, "job_id": session.job.job_id,
                      "reason": "SPEC_BUILD_FAILED", "error": repr(exc)}
    spec.case_id = session.job.job_id
    spec.source_input_hashes = session.job.read_state().get("input_hashes", {})
    session.spec = spec
    if target_date:
        session.set_data_liquidacao(target_date)
    _collect_documentary_evidence(session, corpus_dir, input_copy)
    spec_path = session.save_spec()
    _record_step(session, "BUILD_CALCULATIONSPEC", "PASS", {"spec": str(spec_path)})
    meta = {
        "job_id": session.job.job_id,
        "stage": "SPEC_BUILT",
        "corpus_dir": str(corpus_dir),
        "spec_path": str(spec_path),
        "spec": json.loads(spec.model_dump_json()),
    }
    return session, meta


def _collect_documentary_evidence(session: Session, corpus_dir: Path,
                                  input_copy: Optional[Path] = None) -> None:
    """Persist prior-liquidation/title evidence before choosing a mode.

    The target liquidation date is not evidence of a prior calculation. Only
    named documentary candidates are parsed, and parser output remains a
    provenance-bearing record for later reconciliation.
    """
    from .prior_liquidation import parse_prior_liquidation
    from .title_analysis import TitleDocument, analyze_title

    candidates: List[Path] = []
    if input_copy is not None:
        candidates.append(Path(input_copy))
    if corpus_dir.is_dir():
        candidates.extend(p for p in corpus_dir.rglob("*") if p.is_file())
    prior_candidates = []
    seen: set[str] = set()
    for path in candidates:
        resolved = str(path.resolve())
        if resolved in seen or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        seen.add(resolved)
        # O PDF do processo pode ter nome genérico e ainda conter a última
        # planilha. O parser só é promovido a evidência quando encontra a
        # assinatura forte (número + data + total/pagamento), evitando que a
        # palavra "liquidação" em uma petição mude o modo por acidente.
        try:
            parsed_probe = parse_prior_liquidation(path)
        except (OSError, ValueError, RuntimeError):
            parsed_probe = None
        if parsed_probe is not None and parsed_probe.strong_signature:
            prior_candidates.append(path)

    prior_dir = session.job.path / "calculation" / "prior_liquidation"
    parsed_prior = []
    for path in prior_candidates[:16]:
        try:
            if path.stat().st_size > 25 * 1024 * 1024:
                continue
            parsed = parse_prior_liquidation(path)
            if not (parsed.liquidation_date or parsed.totals or parsed.payments):
                continue
            target = prior_dir / (path.stem + ".json")
            _atomic_json(target, parsed.model_dump(mode="json"))
            parsed_prior.append({"source": str(path), "artifact": str(target)})
        except (OSError, ValueError, RuntimeError):
            # A candidate that cannot be parsed is not silently used as a
            # calculation input; it is left for the explicit review report.
            continue

    title_docs: List[TitleDocument] = []
    title_tokens = ("senten", "acord", "decis", "embarg", "recurso", "agrav")
    for path in candidates:
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        if not any(token in path.name.casefold() for token in title_tokens):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            title_docs.append(TitleDocument(kind="unknown", source=str(path), text=text))
        except OSError:
            continue
    def update_evidence(state: dict) -> None:
        if parsed_prior:
            state["prior_liquidation_detected"] = True
            state["prior_liquidation_sources"] = parsed_prior
            state["external_update_recommended"] = True
        if title_docs:
            state["title_resolution_status"] = resolution.status
            state["title_reconciliation_required"] = bool(
                resolution.conflicts or resolution.status == "UNRESOLVED"
            )

    resolution = None
    if title_docs:
        resolution = analyze_title(title_docs)
        _atomic_json(
            session.job.path / "calculation" / "title_resolution.json",
            resolution.to_dict(),
        )
    session.job.update_state(update_evidence)


def analyze_process(paths: ProjectPaths, process_input: str,
                    target_date: Optional[str] = None) -> Dict[str, Any]:
    """Ingere e constrói a spec, sem iniciar runtime ou navegador."""
    session, result = _prepare_process(paths, process_input, target_date)
    if session is None:
        return result
    mode = _documentary_mode(session)
    _record_step(session, "SELECT_MODE", "PASS", mode.to_dict())
    unresolved = session.spec.critical_parameters(mode=mode.mode.value)
    result.update({
        "ok": not unresolved,
        "status": "SPEC_BUILT" if not unresolved else "REQUIRES_REVIEW",
        "reason": None if not unresolved else "FAIL_CLOSED",
        "mode": mode.to_dict(),
        "unresolved": unresolved,
    })
    return result


def calculate_from_process(paths: ProjectPaths, process_input: str,
                           target_date: Optional[str] = None) -> Dict[str, Any]:
    """Pipeline: processo -> AuditorProcessual -> spec -> PJe-Calc oficial."""
    session, result = _prepare_process(paths, process_input, target_date)
    if session is None:
        return result
    mode = _documentary_mode(session)
    _record_step(session, "SELECT_MODE", "PASS", mode.to_dict())
    unresolved = session.spec.critical_parameters(mode=mode.mode.value)
    result.update({"mode": mode.to_dict(), "unresolved": unresolved})
    if unresolved:
        _mark_requires_review(session, unresolved, "critical parameters unresolved")
        result.update({"ok": False, "status": "REQUIRES_REVIEW",
                       "reason": "FAIL_CLOSED", "stage": "REQUIRES_REVIEW"})
        return result
    session.job.update_state(lambda state: state.update({"mode": mode.mode.value}))
    if mode.mode.value != "STANDARD":
        _mark_requires_review(session, [], mode.mode.value)
        result.update({"ok": False, "status": "REQUIRES_REVIEW",
                       "reason": mode.mode.value, "stage": "REQUIRES_REVIEW"})
        return result
    execution = execute_session(session)
    result.update(execution)
    # `_prepare_process` starts with the documentary marker SPEC_BUILT.  Once
    # the canonical executor has run, expose the persisted terminal stage so
    # callers cannot mistake a completed/failed execution for spec-only mode.
    final_state = session.job.read_state()
    result["stage"] = final_state.get("current_stage", final_state.get("stage"))
    result["status"] = "DONE" if result.get("ok") else "FAILED"
    return result


def _mark_requires_review(session: Session, unresolved: list[str], reason: str) -> None:
    """Persist a terminal documentary refusal distinct from ``SPEC_BUILT``."""

    session.job.update_state(lambda state: state.update({
        "current_stage": "REQUIRES_REVIEW",
        "stage": "REQUIRES_REVIEW",
        "failure": {
            "reason": "FAIL_CLOSED",
            "detail": reason,
            "unresolved": list(unresolved),
        },
    }))


def import_pjc_official(session: Session, driver: Any, pjc_path: Path,
                        target_date: Optional[str] = None) -> Dict[str, Any]:
    """Importa um PJC pelo fluxo ``Importar Cálculo`` da UI oficial."""
    from . import selectors as sel

    pjc_path = Path(pjc_path).resolve()
    validation = ImportPjcValidator().validate(pjc_path, target_date=target_date)
    if not validation.valid:
        return {"ok": False, "reason": "PJC_INVALID", "validation": validation.as_dict()}
    logs_dir = session.job.path / "logs" / "browser"

    def fail(step: str, message: str) -> Dict[str, Any]:
        return _ui_failure(session, driver, step, logs_dir, message)

    if not login(driver):
        return fail("logon", "Login não concluiu antes da importação PJC.")
    driver.goto(f"{BASE}/pages/principal.jsf")
    if not driver.is_present("css", sel.PRINCIPAL_IMPORTAR_CSS):
        return fail("importacao", "A ação oficial 'Importar Cálculo' não apareceu.")
    driver.click_by_css(sel.PRINCIPAL_IMPORTAR_CSS)
    if not _wait_present(driver, "css", sel.IMPORT_FILE_CSS, timeout=30):
        return fail("importacao", "Upload oficial do PJC não apareceu.")
    driver.upload_file(pjc_path, sel.IMPORT_FILE_CSS)
    if not driver.is_present("id", sel.IMPORT_CONFIRMAR):
        return fail("importacao", "Botão oficial de confirmação do PJC não apareceu.")
    driver.click_by_id(sel.IMPORT_CONFIRMAR)
    if not driver.wait_ajax_idle(timeout=60):
        return fail("importacao", "Importação PJC não concluiu o Ajax oficial.")
    source = driver.page_source().casefold()
    confirmed = (
        "parâmetros do cálculo" in source
        or "parametros do calculo" in source
        or "dados do cálculo" in source
        or "dados do calculo" in source
    ) and "importação" not in source
    if not confirmed:
        return fail("importacao", "A UI não confirmou o cálculo PJC importado.")

    if target_date:
        driver.goto(f"{BASE}/pages/calculo/liquidacao.jsf")
        if not driver.is_present("id", sel.LIQUIDACAO_BUTTON):
            return fail("importacao_data", "Ação oficial de liquidação do PJC ausente.")
        cal_input = sel.calendar_input(sel.LIQUIDACAO_DATA)
        if not driver.is_present("css", cal_input):
            return fail("importacao_data", "Data-alvo oficial do PJC ausente.")
        expected = format_ui_date(target_date)
        driver.set_by_css(cal_input, expected)
        if _ui_normalize(driver.get_value("css", cal_input)) != _ui_normalize(expected):
            return fail("importacao_data", "Readback divergente da data-alvo do PJC.")
        driver.click_by_id(sel.LIQUIDACAO_BUTTON)
        if not driver.wait_ajax_idle(timeout=60):
            return fail("importacao_data", "Liquidação do PJC não concluiu.")
        evidence = _liquidation_evidence(driver)
        if not evidence.get("confirmed"):
            return fail("importacao_data", "Liquidação do PJC sem evidência oficial.")
    else:
        evidence = {"confirmed": True, "source": "import_ui", "url": driver.current_url()}

    destination = session.job.path / "artifacts" / "calculo.pjc"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.copy2(pjc_path, destination)
    artifact_validation = ImportPjcValidator().validate(destination, target_date=target_date)
    if not artifact_validation.valid:
        return fail("importacao_artefato", "Cópia do PJC importado falhou na validação.")
    session.job.update_state(lambda state: state.update({
        "mode": "IMPORT_PJC",
        "current_stage": "PJC_IMPORTED",
        "artifacts": {**(state.get("artifacts") or {}), "pjc": str(destination)},
    }))
    return {"ok": True, "status": "IMPORTED_UI", "job_id": session.job.job_id,
            "path": str(destination), "validation": artifact_validation.as_dict(),
            "evidence": evidence}


def execute_external_update(session: Session, driver: Any, ext: Any,
                            base_pjc_path: Path, fail_fn) -> Dict[str, Any]:
    """Executa o ciclo completo de atualização externa no cálculo-base.

    O cálculo anterior é importado primeiro. Só então os parâmetros de
    ``Cálculo Externo``, as parcelas, as exclusões e a data final são enviados à
    UI. Ao final, validação, PJC e PDF são obrigatórios. Nenhuma diferença de
    saldo é calculada nesta camada.
    """
    base_pjc_path = Path(base_pjc_path).expanduser().resolve()
    validation = ImportPjcValidator().validate(base_pjc_path)
    if not validation.valid:
        return fail_fn("base_pjc", f"PJC-base inválido: {validation.errors}")
    if ext.base_calculation_number:
        identifiers = validation.details.get("identifiers", {})
        if not any(str(ext.base_calculation_number) == str(value)
                   for value in identifiers.values()):
            return fail_fn(
                "base_pjc",
                "O PJC-base não contém o número de cálculo informado "
                f"({ext.base_calculation_number}).",
            )

    imported = import_pjc_official(session, driver, base_pjc_path, target_date=None)
    if not imported.get("ok"):
        return imported
    steps: List[Dict[str, Any]] = [{
        "step": "importar_pjc_base", "ok": True,
        "path": str(base_pjc_path),
        "validation": validation.as_dict(),
    }]

    updated = populate_external_update(session, driver, fail_fn, ext)
    if not updated.get("ok"):
        return updated
    steps.extend(updated.get("steps", []))
    evidence = updated.get("evidence")
    if not evidence or not evidence.get("confirmed"):
        return fail_fn("external_update", "Liquidação externa sem evidência oficial.")
    official_validation = validate_official_calculation(driver, session)
    if not official_validation.get("ok"):
        return official_validation
    steps.append({"step": "validar", "ok": True,
                  "evidence": official_validation})
    _write_engine_execution(session, evidence)

    logs_dir = session.job.path / "logs" / "browser"
    pjc = _export_pjc(driver, session, steps, fail_fn, logs_dir)
    if not pjc.get("ok"):
        return pjc
    pdf = _export_pdf(driver, session, steps, fail_fn, logs_dir)
    if not pdf.get("ok"):
        return pdf
    _write_artifact_manifest(session)
    session.job.update_state(lambda state: state.update({
        "mode": "EXTERNAL_UPDATE",
        "current_stage": "EXTERNAL_UPDATE_COMPLETED",
        "failure": None,
        "artifacts": {
            **(state.get("artifacts") or {}),
            "base_pjc": str(base_pjc_path),
            "external_pjc": str(session.job.path / "artifacts" / "calculo.pjc"),
            "external_pdf": str(session.job.path / "artifacts" / "calculo.pdf"),
        },
    }))
    return {
        "ok": True,
        "status": "OFFICIAL_EXTERNAL_UPDATE",
        "job_id": session.job.job_id,
        "steps": steps,
        "evidence": evidence,
        "pjc": str(session.job.path / "artifacts" / "calculo.pjc"),
        "pdf": str(session.job.path / "artifacts" / "calculo.pdf"),
        "message": "Atualização externa liquidada e exportada pelo PJe-Calc oficial.",
    }


def populate_liquidate_and_export(session: Session, driver: Any) -> Dict[str, Any]:
    """Fluxo completo: login, cadastro, liquidação e exportação.

    Retorna `ok=True` somente se liquidação + exportações forem confirmadas com
    sucesso na UI oficial. Cada passo é verificado; falha gera diagnóstico.
    """
    from . import selectors as sel
    logs_dir = session.job.path / "logs" / "browser"

    steps: List[Dict[str, Any]] = []

    def _fail(step: str, message: str) -> Dict[str, Any]:
        return _ui_failure(session, driver, step, logs_dir, message)

    # 1. login
    if not login(driver):
        return _fail("logon", "Login não concluiu com redirecionamento para a principal.")

    # 2. criar novo cálculo (principal -> calcular novo)
    driver.goto(f"{BASE}/pages/principal.jsf")
    if not driver.is_present("css", sel.PRINCIPAL_NOVO_CALCULO_CSS):
        return _fail("principal", "Botão 'Criar Novo Cálculo' não encontrado.")
    driver.click_by_css(sel.PRINCIPAL_NOVO_CALCULO_CSS)

    # aguarda o formulário de cálculo (processo)
    if not _wait_present(driver, "id", sel.ACTION_SALVAR, timeout=30):
        return _fail("calculo", "Formulário de cálculo (Salvar) não apareceu após 'novo cálculo'.")

    # 3. preencher identificação do processo. Quando a spec contém um valor,
    # a ausência do componente oficial é erro: nunca se descarta um fato
    # documental silenciosamente.
    _fill_if_present(driver, session.spec.processo, sel.CALC_NUMERO, "numero", "id")
    _fill_if_present(driver, session.spec.processo, sel.CALC_DIGITO, "digito", "id")
    _fill_if_present(driver, session.spec.processo, sel.CALC_ANO, "ano", "id")
    _assert_disabled_process_field(driver, session.spec.processo, sel.CALC_JUSTICA, "justica")
    _fill_if_present(driver, session.spec.processo, sel.CALC_REGIAO, "regiao", "id")
    _fill_if_present(driver, session.spec.processo, sel.CALC_VARA, "vara", "id")
    _select_if_present(driver, session.spec.processo, sel.CALC_ESTADO, "estado")
    _select_if_present(driver, session.spec.processo, sel.CALC_MUNICIPIO, "municipio")
    _fill_if_present(driver, session.spec.processo, sel.CALC_RECLAMANTE_NOME, "reclamante", "id")
    _fill_if_present(driver, session.spec.processo, sel.CALC_RECLAMADO_NOME, "reclamado", "id")
    _fill_process_dates(driver, session.spec.processo, sel)

    # 4. preencher parâmetros do contrato (datas e remuneração)
    _fill_contract(driver, session.spec, sel)

    # 5. salvar o cálculo
    driver.click_by_id(sel.ACTION_SALVAR)
    if not driver.wait_for_text("Parâmetros do Cálculo", timeout=30):
        return _fail("calculo_salvar", "Salvar não navegou/concluiu o cadastro do cálculo.")
    steps.append({"step": "salvar", "ok": True})

    # 5b. cadastrar verbas na UI (ERRO B corrigido)
    verba_res = populate_verbas(session, driver, _fail)
    if verba_res.get("ok") is False:
        return verba_res
    steps += verba_res.get("steps", [])

    # 6. validação oficial antes da liquidação
    validation = validate_official_calculation(driver, session)
    if not validation.get("ok"):
        return validation
    steps.append({"step": "validar", "ok": True, "evidence": validation})

    # 7. liquidação
    driver.goto(f"{BASE}/pages/calculo/liquidacao.jsf")
    if not driver.is_present("id", sel.LIQUIDACAO_BUTTON):
        return _fail("liquidacao", "Botão 'Liquidar' não encontrado.")

    data = session.spec.processo.get("data_liquidacao_atualizacao")
    if data:
        cal_input = sel.calendar_input(sel.LIQUIDACAO_DATA)
        if not driver.is_present("css", cal_input):
            return _fail("liquidacao_data", "Campo 'Data de Liquidação' não encontrado.")
        driver.set_by_css(cal_input, format_ui_date(data))
    driver.click_by_id(sel.LIQUIDACAO_BUTTON)
    if not driver.wait_ajax_idle(timeout=60):
        return _fail("liquidacao", "Ajax de liquidação não concluiu.")
    evidence = _liquidation_evidence(driver)
    if not evidence.get("confirmed"):
        return _fail("liquidacao", "Liquidação sem evidência oficial de conclusão.")
    _write_engine_execution(session, evidence)
    steps.append({"step": "liquidar", "ok": True, "evidence": evidence})

    # 7. exportação PJC
    pjc = _export_pjc(driver, session, steps, _fail, logs_dir)
    if not pjc.get("ok"):
        return pjc

    # 8. exportação PDF (relatório consolidado)
    pdf = _export_pdf(driver, session, steps, _fail, logs_dir)
    if not pdf.get("ok"):
        return pdf

    _write_artifact_manifest(session)

    return {
        "ok": True,
        "job_id": session.job.job_id,
        "steps": steps,
        "message": "Cadastro, liquidação e exportações confirmados na UI oficial.",
    }


def populate_verbas(session: Session, driver: Any, fail_fn) -> Dict[str, Any]:
    """Cadastra as verbas de `session.spec.verbas` na UI oficial (ERRO B).

    Abre a tela de verbas (`pages/calculo/verba/verba-calculo.xhtml`), preenche
    descrição/período/base/divisor/multiplicador/quantidade e salva. Cada verba
    deve constar na listagem; se o componente esperado não aparecer, fail-closed.
    """
    from . import selectors as sel

    steps: List[Dict[str, Any]] = []
    for i, v in enumerate(session.spec.verbas):
        driver.goto(f"{BASE}/pages/calculo/verba/verba-calculo.jsf")
        # A tela começa na listagem. O botão Manual/Incluir é a transição
        # oficial para o formulário; não se presume que o formulário já abriu.
        _wait_present(driver, "css", "input[value='Manual'], input[value='Incluir'], #formulario\\:incluir", timeout=30)
        if driver.is_present("id", "formulario:incluir"):
            driver.click_by_id("formulario:incluir")
        elif driver.is_present("css", "input[value='Manual']"):
            driver.click_by_css("input[value='Manual']")
        elif driver.is_present("css", "input[value='Incluir']"):
            driver.click_by_css("input[value='Incluir']")
        form_present = _wait_present(driver, "id", sel.VERBA_DESCRICAO, timeout=30)
        if not form_present:
            return fail_fn(
                f"verba_{i}", "Formulário de verba (descrição) não apareceu.")

        # descrição
        if v.descricao:
            driver.set_by_id(sel.VERBA_DESCRICAO, v.descricao)

        # Classificação obrigatória: os valores são escolhidos nos radios
        # oficiais, nunca apenas armazenados na spec.
        radio_fields = [
            ("tipoDeVerba", v.tipo if v.tipo.lower() not in {"informada", "informed"} else None),
            ("tipoVariacaoDaParcela", v.tipo_variacao_da_parcela),
            ("caracteristicaVerba", v.caracteristica),
            ("ocorrenciaPagto", v.ocorrencia_pagto),
            ("ocorrenciaAjuizamento", v.ocorrencia_ajuizamento),
        ]
        for component, value in radio_fields:
            if value:
                _select_radio(driver, component, str(value))

        # O assunto CNJ é um campo de leitura acompanhado por um hidden id;
        # ambos devem existir para o submit JSF considerar a seleção válida.
        if v.assunto_cnj:
            if not driver.is_present("id", sel.VERBA_ASSUNTO_CNJ):
                return fail_fn(f"verba_{i}", "Campo Assunto CNJ não apareceu.")
            driver.set_by_id(sel.VERBA_ASSUNTO_CNJ, v.assunto_cnj)
            if v.codigo_assuntos_cnj and driver.is_present("id", sel.VERBA_CODIGO_ASSUNTO_CNJ):
                driver.set_dom_value("id", sel.VERBA_CODIGO_ASSUNTO_CNJ,
                                     str(v.codigo_assuntos_cnj))

        # período (admissão/demissão do contrato, quando houver)
        c = session.spec.contract
        if c.admissao and c.admissao.value:
            _fill_calendar(driver, c.admissao, sel.VERBA_PERIODO_INICIAL)
        if c.demissao and c.demissao.value:
            _fill_calendar(driver, c.demissao, sel.VERBA_PERIODO_FINAL)

        # divisor / multiplicador / quantidade / percentual / valor informado
        _fill_resolved(driver, v.divisor, sel.VERBA_DIVISOR_OUTRO, "id")
        _fill_resolved(driver, v.multiplicador, sel.VERBA_MULTIPLICADOR, "id")
        if v.base_calculo and driver.is_present("id", sel.VERBA_BASE_TABELADA):
            _select_by_id(driver, sel.VERBA_BASE_TABELADA, v.base_calculo)
        if v.quantidade and v.quantidade.value is not None:
            _fill_resolved(driver, v.quantidade, sel.VERBA_QUANTIDADE, "id")
        if v.dobra and driver.is_present("id", sel.VERBA_DOBRA):
            _set_checkbox(driver, sel.VERBA_DOBRA, True)
        if v.tipo.lower() in {"informada", "informed"} and v.valor and v.valor.value is not None:
            driver.set_by_id(sel.VERBA_VALOR_INFORMADO, _fmt(v.valor.value))
        if v.valor_informado_quantidade and v.valor_informado_quantidade.value is not None:
            if driver.is_present("id", "formulario:valorInformadoDaQuantidade"):
                driver.set_by_id("formulario:valorInformadoDaQuantidade", _fmt(v.valor_informado_quantidade.value))

        # salvar
        previous_url = driver.current_url()
        driver.click_by_id(sel.VERBA_SALVAR)
        if not driver.wait_navigation_or_update(previous_url, expected=("id", sel.VERBA_DESCRICAO), timeout=30):
            # O retorno pode ser a listagem sem o campo de edição.
            if not driver.wait_for_text(v.descricao or "", timeout=5):
                return fail_fn(f"verba_{i}", "Salvar da verba não confirmou retorno/listagem.")
        if v.descricao and not driver.wait_for_text(v.descricao, timeout=10):
            return fail_fn(f"verba_{i}", "Verba não localizada na listagem após salvar.")
        steps.append({"step": f"verba_{i}", "ok": True})

    return {"ok": True, "steps": steps}


def _fmt(value: Any) -> str:
    """Formata número como moeda simples (sem inventar precisão)."""
    try:
        return format_br_decimal(value)
    except (TypeError, ValueError):
        return str(value)


def populate_external_update(session: Session, driver: Any, fail_fn,
                             ext) -> Dict[str, Any]:
    """Preenche o módulo oficial "Cálculo Externo" (EXTERNAL_UPDATE).

    Opera as telas reais `calculo-externo.xhtml` (parâmetros) e
    `parcelas-atualizaveis.xhtml` (créditos/descontos). Nenhuma conta é feita
    fora do PJe-Calc: apenas ativa checkboxes e informa valores da spec.
    """
    from . import selectors as sel

    steps: List[Dict[str, Any]] = []

    if ext.combinacoes_indices or ext.combinacoes_juros:
        return fail_fn(
            "calc_externo",
            "Combinações de índice/juros exigem o mapeamento DOM dinâmico oficial.",
        )

    # 1. parâmetros do cálculo externo
    try:
        driver.goto(f"{BASE}/pages/calculo/calculo-externo.jsf")
        if not _wait_present(driver, "id", sel.CALC_EXT_SALVAR, timeout=30):
            return fail_fn("calc_externo", "Formulário de Cálculo Externo não apareceu.")

        if ext.data_ultima_atualizacao:
            _fill_calendar(driver, ResolvedValue(value=ext.data_ultima_atualizacao,
                                                 status=SourceStatus.EXPLICIT),
                           sel.CALC_EXT_DATA_ULTIMA_ATUALIZACAO)
        if ext.indice_trabalhista:
            _select_by_id(driver, sel.CALC_EXT_INDICE_TRABALHISTA, ext.indice_trabalhista)
        if ext.juros:
            _select_by_id(driver, sel.CALC_EXT_JUROS, ext.juros)
        if ext.base_juros_verbas:
            _select_by_id(driver, sel.CALC_EXT_BASE_JUROS_VERBAS, ext.base_juros_verbas)
        if ext.ignorar_taxa_negativa is not None:
            _set_checkbox(driver, sel.CALC_EXT_IGNORAR_TAXA_NEGATIVA,
                          bool(ext.ignorar_taxa_negativa))
        if ext.fgts_destino:
            _select_radio(driver, sel.CALC_EXT_FGTS_TIPO.split(":")[-1], str(ext.fgts_destino))
        if ext.fgts_correcao:
            _select_radio(driver, sel.CALC_EXT_FGTS_CORRECAO.split(":")[-1],
                          str(ext.fgts_correcao))
        if ext.lei_11941 is not None:
            _set_checkbox(driver, sel.CALC_EXT_LEI_11941, bool(ext.lei_11941))
        if ext.irpf is not None:
            _set_checkbox(driver, sel.CALC_EXT_IRPF, bool(ext.irpf))
        if ext.custas is not None:
            _set_checkbox(driver, sel.CALC_EXT_CUSTAS, bool(ext.custas))
        # Esses dois campos históricos representam a correção previdenciária
        # dos salários devidos/pagos. Eles agora são acionados pelo id real
        # da tela; antes eram recusados mesmo quando o PJC trazia o parâmetro.
        if ext.contribuicao_social_salarios_devidos is not None:
            _set_checkbox(driver, sel.CALC_EXT_INSS_CORRECAO_PREVIDENCIARIA_DEVIDOS,
                          bool(ext.contribuicao_social_salarios_devidos))
        if ext.contribuicao_social_salarios_pagos is not None:
            _set_checkbox(driver, sel.CALC_EXT_INSS_CORRECAO_PREVIDENCIARIA_PAGOS,
                          bool(ext.contribuicao_social_salarios_pagos))
        driver.click_by_id(sel.CALC_EXT_SALVAR)
        if not driver.wait_ajax_idle(timeout=30):
            return fail_fn("calc_externo", "Salvar dos parâmetros externos não concluiu.")
        steps.append({"step": "calc_externo_params", "ok": True})

        # 2. Parcelas atualizáveis. O mapa é derivado do XHTML real e cobre
        # créditos, descontos, outros débitos e débitos do reclamante.
        driver.goto(f"{BASE}/pages/calculo/parcelas-atualizaveis.jsf")
        from .selectors import external_parcel_binding
        groups = (
            "creditos_reclamante", "descontos_reclamante",
            "outros_debitos_reclamado", "debitos_reclamante",
        )
        applied: list[dict] = []
        for group in groups:
            for parcel in getattr(ext, group):
                binding = external_parcel_binding(group, parcel.key)
                if binding is None:
                    raise RuntimeError(f"EXTERNAL_PARCEL_KEY_UNKNOWN:{group}:{parcel.key}")
                _apply_external_parcel(driver, group, parcel, binding)
                applied.append({"group": group, "key": parcel.key,
                                "active": bool(parcel.ativa)})
        if not _wait_present(driver, "id", sel.VERBA_SALVAR, timeout=30):
            return fail_fn("parcelas_atualizaveis", "Botão oficial de salvar parcelas não apareceu.")
        driver.click_by_id(sel.VERBA_SALVAR)
        if not driver.wait_ajax_idle(timeout=30):
            return fail_fn("parcelas_atualizaveis", "Salvar das parcelas não concluiu.")
        steps.append({"step": "parcelas_atualizaveis", "ok": True,
                      "applied": applied})

        # 3. Alterações do título que podem ser representadas pela tela
        # oficial. A alteração de parte é opt-in e exige readback exato.
        if ext.reclamado_remanescente:
            _set_remaining_defendant(driver, ext.reclamado_remanescente)
            steps.append({"step": "reclamado_remanescente", "ok": True,
                          "value": ext.reclamado_remanescente})

        # 4. Exclusão de verba pós-pagamento: feita no cálculo importado, por
        # descrição única, antes da liquidação. O motor oficial refaz a
        # imputação dos pagamentos; não há subtração manual de valores.
        for exclusion in ext.excluir_verbas:
            _exclude_verba_by_description(driver, exclusion.descricao)
            steps.append({"step": "excluir_verba", "ok": True,
                          "descricao": exclusion.descricao})
    except (RuntimeError, ValueError) as exc:
        return fail_fn("external_update_mapping", str(exc))

    # O formulário externo registra a data do saldo de origem. A data final
    # solicitada é aplicada no fluxo oficial de liquidação; sem este passo
    # seria apenas metadado decorativo.
    if ext.data_final_atualizacao:
        driver.goto(f"{BASE}/pages/calculo/liquidacao.jsf")
        if not driver.is_present("id", sel.LIQUIDACAO_BUTTON):
            return fail_fn(
                "external_data_final",
                "O PJe-Calc não expôs a ação oficial para a data final.",
            )
        cal_input = sel.calendar_input(sel.LIQUIDACAO_DATA)
        if not driver.is_present("css", cal_input):
            return fail_fn(
                "external_data_final",
                "Campo oficial da data final não apareceu.",
            )
        expected = format_ui_date(ext.data_final_atualizacao)
        driver.set_by_css(cal_input, expected)
        if _ui_normalize(driver.get_value("css", cal_input)) != _ui_normalize(expected):
            return fail_fn(
                "external_data_final",
                "Readback divergente da data final informada.",
            )
        driver.click_by_id(sel.LIQUIDACAO_BUTTON)
        if not driver.wait_ajax_idle(timeout=60):
            return fail_fn("external_data_final", "Atualização até a data final não concluiu.")
        evidence = _liquidation_evidence(driver)
        if not evidence.get("confirmed"):
            return fail_fn("external_data_final", "Resultado externo sem evidência oficial.")
        steps.append({"step": "external_data_final", "ok": True, "evidence": evidence})

    return {"ok": True, "steps": steps,
            "evidence": next((s.get("evidence") for s in steps
                               if s.get("step") == "external_data_final"), None)}


def _apply_external_parcel(driver: Any, group: str, parcel: Any,
                           binding: dict) -> None:
    """Aplica uma instrução de parcela e confirma cada campo escrito."""
    from .selectors import canonical_external_parcel_key

    checkbox = binding["checkbox"]
    if not driver.is_present("id", checkbox):
        raise RuntimeError(f"UI_SELECTOR_MISSING:{checkbox}")
    _set_checkbox(driver, checkbox, bool(parcel.ativa))
    driver.wait_ajax_idle(timeout=15)
    if not parcel.ativa:
        return
    values = binding.get("values") or {}
    if any(value is not None for value in (
        parcel.juros, parcel.indice, parcel.aliquota,
        parcel.aplicar_juros, parcel.data_juros_a_partir_de,
    )):
        raise RuntimeError(
            f"EXTERNAL_PARCEL_DETAIL_SELECTOR_UNMAPPED:{group}:{parcel.key}"
        )
    components = dict(parcel.componentes or {})
    if parcel.principal is not None:
        if "principal" not in values:
            raise RuntimeError(f"EXTERNAL_PARCEL_PRINCIPAL_UNMAPPED:{group}:{parcel.key}")
        # Para grupos com variante Lei 11.941, o campo total só é renderizado
        # quando a variante simples está ativa. Se não estiver presente, o
        # agente exige os dois componentes documentados, nunca escolhe um
        # campo silenciosamente.
        components.setdefault("principal", parcel.principal)
    if not components and binding.get("value_required", True):
        raise RuntimeError(f"EXTERNAL_PARCEL_VALUE_REQUIRED:{group}:{parcel.key}")
    canonical = canonical_external_parcel_key(group, parcel.key)
    for component, value in components.items():
        selector = values.get(component)
        if selector is None:
            raise RuntimeError(
                f"EXTERNAL_PARCEL_COMPONENT_UNMAPPED:{group}:{canonical}:{component}"
            )
        if not driver.is_present("id", selector):
            raise RuntimeError(f"UI_SELECTOR_MISSING:{selector}")
        rendered = _fmt(value)
        driver.set_by_id(selector, rendered)
        readback = parse_br_decimal(driver.get_value("id", selector))
        if readback != parse_br_decimal(value):
            raise RuntimeError(
                f"UI_READBACK_MISMATCH:{group}:{canonical}:{component}"
            )


def _set_remaining_defendant(driver: Any, name: str) -> None:
    """Atualiza o reclamado pela tela de processo, se o PJC permitir edição."""
    from . import selectors as sel

    driver.goto(f"{BASE}/pages/calculo/calculo.jsf")
    if not _wait_present(driver, "id", sel.CALC_RECLAMADO_NOME, timeout=30):
        raise RuntimeError("UI_SELECTOR_MISSING:formulario:reclamadoNome")
    field = driver.find("id", sel.CALC_RECLAMADO_NOME)
    if not field.is_enabled():
        # O PJC importado pode bloquear o cadastro. Tentar explicitamente o
        # modo manual mantém a operação oficial e deixa o readback decidir.
        try:
            _select_radio(driver, "processoInformadoManualmente", "Sim")
            driver.wait_ajax_idle(timeout=10)
        except RuntimeError as exc:
            raise RuntimeError("RECLAMADO_REMANESCENTE_UI_LOCKED") from exc
    field = driver.find("id", sel.CALC_RECLAMADO_NOME)
    if not field.is_enabled():
        raise RuntimeError("RECLAMADO_REMANESCENTE_UI_LOCKED")
    driver.set_by_id(sel.CALC_RECLAMADO_NOME, name)
    if _ui_normalize(driver.get_value("id", sel.CALC_RECLAMADO_NOME)) != _ui_normalize(name):
        raise RuntimeError("UI_READBACK_MISMATCH:formulario:reclamadoNome")
    if not driver.is_present("id", sel.ACTION_SALVAR):
        raise RuntimeError("UI_SELECTOR_MISSING:formulario:salvar")
    driver.click_by_id(sel.ACTION_SALVAR)
    if not driver.wait_ajax_idle(timeout=30):
        raise RuntimeError("RECLAMADO_REMANESCENTE_SAVE_FAILED")


def _exclude_verba_by_description(driver: Any, description: str) -> None:
    """Exclui exatamente uma verba existente no cálculo importado."""
    from . import selectors as sel

    driver.goto(f"{BASE}/pages/calculo/verba/verba-calculo.jsf")
    if not _wait_present(driver, "css", "a.linkExcluir", timeout=30):
        raise RuntimeError("EXCLUSION_VERBA_LIST_NOT_AVAILABLE")
    literal = _xpath_literal(description)
    # A linha da verba principal contém o texto de nome e o link de exclusão.
    # `find_all` garante que uma descrição ambígua nunca apague a primeira
    # ocorrência por acidente.
    row_xpath = (
        "//tr[.//*[contains(normalize-space(.), " + literal + ")]"
        " and .//a[contains(@class,'linkExcluir')]]"
    )
    rows = driver.find_all("xpath", row_xpath)
    if len(rows) != 1:
        raise RuntimeError(f"EXCLUSION_VERBA_MATCH_COUNT:{description}:{len(rows)}")
    link = rows[0].find_element("xpath", ".//a[contains(@class,'linkExcluir')]")
    link.click()
    # O XHTML usa window.confirm antes do Ajax. O alerta pode surgir depois
    # do click no Firefox legado.
    time.sleep(0.2)
    driver.accept_alert_if_present()
    if not driver.wait_ajax_idle(timeout=30):
        raise RuntimeError(f"EXCLUSION_VERBA_SAVE_FAILED:{description}")
    if driver.find_all("xpath", row_xpath):
        raise RuntimeError(f"EXCLUSION_VERBA_READBACK_PRESENT:{description}")


def _xpath_literal(value: str) -> str:
    """Cota uma string para XPath 1.0, inclusive quando contém aspas."""
    text = str(value)
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def _parcela(driver, chk_selector, val_selector, parcelas) -> bool:
    """Aplica uma parcela por chave exata e confirma estado do checkbox."""
    key = chk_selector.split(":")[-1]
    match = next((p for p in parcelas if p.key == key), None)
    if match is None:
        return False
    if not driver.is_present("id", chk_selector):
        raise RuntimeError(f"checkbox de parcela ausente: {chk_selector}")
    _set_checkbox(driver, chk_selector, bool(match.ativa))
    if match.ativa:
        if match.principal is None or not driver.is_present("id", val_selector):
            raise RuntimeError(f"valor de parcela ausente: {key}")
        driver.set_by_id(val_selector, _fmt(match.principal))
        if any(value is not None for value in (
            match.juros, match.indice, match.aliquota,
            match.aplicar_juros, match.data_juros_a_partir_de,
        )):
            # A tela oficial renderiza alguns desses inputs sem id estável;
            # nunca descartar uma instrução de juros/índice sem um mapeamento
            # DOM confirmado para aquela versão.
            raise RuntimeError(
                f"EXTERNAL_PARCEL_DETAIL_SELECTOR_UNMAPPED:{key}"
            )
    return True


def _select_by_id(driver, cid: str, value: str) -> None:
    """Seleciona uma opção em h:selectOneMenu pelo valor (via JS do JSF)."""
    from selenium.webdriver.support.ui import Select
    element = driver.find("id", cid)
    try:
        Select(element).select_by_visible_text(value)
    except Exception:
        try:
            Select(element).select_by_value(value)
        except Exception as second:
            raise RuntimeError(f"opção não encontrada em {cid}: {value}") from second


def _select_radio(driver, component_id: str, value: str) -> None:
    try:
        driver.select_radio_by_label(component_id, value)
        driver.wait_ajax_idle(timeout=10)
    except Exception as exc:
        raise RuntimeError(
            f"opção de radio não encontrada em formulario:{component_id}: {value}"
        ) from exc


def _set_checkbox(driver, cid: str, desired: bool) -> None:
    element = driver.find("id", cid)
    selected = bool(element.is_selected())
    if selected != desired:
        element.click()


def _export_pjc(driver, session, steps, fail_fn, logs_dir) -> Dict[str, Any]:
    from . import selectors as sel

    driver.goto(f"{BASE}/pages/calculo/exportacao.jsf")
    if not driver.is_present("id", sel.EXPORTACAO_BUTTON):
        return fail_fn("exportacao_pjc", "Botão 'Exportar' (PJC) não encontrado.")
    download_started = time.time()
    driver.click_by_id(sel.EXPORTACAO_BUTTON)
    if not driver.wait_ajax_idle(timeout=30):
        return fail_fn("exportacao_pjc", "Exportação PJC não terminou.")
    if not driver.is_present("id", sel.EXPORTACAO_DOWNLOAD):
        return fail_fn("exportacao_pjc", "Link de download PJC não apareceu.")
    driver.click_by_id(sel.EXPORTACAO_DOWNLOAD)
    path = driver.wait_download((".pjc", ".zip"), timeout=60,
                                not_before=download_started)
    if path is None:
        return fail_fn("exportacao_pjc", "Arquivo PJC não foi baixado.")
    validation = ImportPjcValidator().validate(path)
    if not validation.valid:
        return fail_fn("exportacao_pjc", f"PJC inválido: {validation.errors}")
    destination = session.job.path / "artifacts" / "calculo.pjc"
    if path != destination:
        path.replace(destination)
    steps.append({"step": "exportar_pjc", "ok": True, "path": str(destination), "validation": validation.as_dict()})
    return {"ok": True}


def _export_pdf(driver, session, steps, fail_fn, logs_dir) -> Dict[str, Any]:
    from . import selectors as sel

    driver.goto(f"{BASE}/pages/calculo/relatorio/relatorio-calculo.jsf")
    if not driver.is_present("id", sel.RELATORIO_IMPRIMIR):
        return fail_fn("exportacao_pdf", "Botão 'Imprimir' (PDF) não encontrado.")
    download_started = time.time()
    driver.click_by_id(sel.RELATORIO_IMPRIMIR)
    if not driver.wait_ajax_idle(timeout=30):
        return fail_fn("exportacao_pdf", "Geração de PDF não terminou.")
    if not driver.is_present("id", sel.RELATORIO_DOWNLOAD):
        return fail_fn("exportacao_pdf", "Link de download PDF não apareceu.")
    driver.click_by_id(sel.RELATORIO_DOWNLOAD)
    path = driver.wait_download((".pdf",), timeout=60,
                                not_before=download_started)
    if path is None:
        return fail_fn("exportacao_pdf", "Arquivo PDF não foi baixado.")
    expected_text = [
        str(session.spec.processo.get("numero"))
        if session.spec.processo.get("numero") else "",
        str(session.spec.processo.get("reclamante"))
        if session.spec.processo.get("reclamante") else "",
    ]
    validation = validate_pdf(path, expected_text=expected_text)
    if not validation.valid:
        return fail_fn("exportacao_pdf", f"PDF inválido: {validation.errors}")
    destination = session.job.path / "artifacts" / "calculo.pdf"
    if path != destination:
        path.replace(destination)
    steps.append({"step": "exportar_pdf", "ok": True, "path": str(destination), "validation": validation.as_dict()})
    return {"ok": True}


def _fill_if_present(driver, data: Dict[str, Any], selector: str,
                     key: str, by: str) -> None:
    """Preenche e confirma um campo oficial quando a spec o fornece."""
    value = data.get(key)
    if value is None:
        return
    if not driver.is_present(by, selector):
        raise RuntimeError(f"UI_SELECTOR_MISSING:{selector}")
    text = str(value)
    driver.set_input(by, selector, text)
    rendered = driver.get_value(by, selector)
    if rendered in (None, ""):
        raise RuntimeError(f"UI_READBACK_EMPTY:{selector}")
    if _ui_normalize(rendered) != _ui_normalize(text):
        raise RuntimeError(f"UI_READBACK_MISMATCH:{selector}")


def _fill_process_dates(driver: Any, data: Dict[str, Any], sel: Any) -> None:
    for key, selector in (
        ("data_inicio_calculo", sel.CALC_DATA_INICIO),
        ("data_termino_calculo", sel.CALC_DATA_TERMINO),
    ):
        value = data.get(key)
        if value is not None:
            _fill_calendar(
                driver,
                ResolvedValue(value=value, status=SourceStatus.EXPLICIT),
                selector,
            )


def _ui_normalize(value: Any) -> str:
    return "".join(ch for ch in str(value).strip().casefold() if ch.isalnum())


def _assert_disabled_process_field(driver: Any, data: Dict[str, Any],
                                   selector: str, key: str) -> None:
    value = data.get(key)
    if value is None:
        return
    if not driver.is_present("id", selector):
        raise RuntimeError(f"UI_SELECTOR_MISSING:{selector}")
    rendered = driver.get_value("id", selector)
    if not rendered or _ui_normalize(rendered) != _ui_normalize(value):
        raise RuntimeError(f"UI_READBACK_MISMATCH:{selector}")


def _select_if_present(driver: Any, data: Dict[str, Any], selector: str,
                       key: str) -> None:
    value = data.get(key)
    if value is None:
        return
    if not driver.is_present("id", selector):
        raise RuntimeError(f"UI_SELECTOR_MISSING:{selector}")
    _select_by_id(driver, selector, str(value))
    element = driver.find("id", selector)
    from selenium.webdriver.support.ui import Select
    selected = Select(element).first_selected_option
    observed = {
        selected.text,
        selected.get_attribute("value"),
        driver.get_value("id", selector),
    }
    if not any(_ui_normalize(item) == _ui_normalize(value)
               for item in observed if item):
        raise RuntimeError(f"UI_READBACK_MISMATCH:{selector}")


def _fill_contract(driver, spec: CalculationSpec, sel) -> None:
    """Preenche datas e remuneração nas abas do cálculo, quando presentes."""
    c = spec.contract

    # datas (rich:calendar -> input InputDate)
    _fill_calendar(driver, c.admissao, sel.CALC_DATA_ADMISSAO)
    _fill_calendar(driver, c.demissao, sel.CALC_DATA_DEMISSAO)
    ajuizamento = spec.processo.get("data_ajuizamento")
    if ajuizamento:
        _fill_calendar(driver, ResolvedValue(value=ajuizamento, status=SourceStatus.EXPLICIT), sel.CALC_DATA_AJUIZAMENTO)

    # remuneração / carga horária
    _fill_resolved(driver, c.salario, sel.CALC_MAIOR_REMUNERACAO, "id")
    _fill_resolved(driver, c.jornada, sel.CALC_CARGA_HORARIA_PADRAO, "id")


def _fill_calendar(driver, rv: Optional[ResolvedValue], selector: str) -> None:
    if rv is None or rv.value is None:
        return
    css = f"input[id$='{selector}InputDate']"
    if not driver.is_present("css", css):
        raise RuntimeError(f"UI_SELECTOR_MISSING:{css}")
    expected = format_ui_date(rv.value)
    driver.set_input("css", css, expected)
    rendered = driver.get_value("css", css)
    if not rendered or _ui_normalize(rendered) != _ui_normalize(expected):
        raise RuntimeError(f"UI_READBACK_MISMATCH:{selector}")


def _fill_resolved(driver, rv: Optional[ResolvedValue], selector: str, by: str) -> None:
    if rv is None or rv.value is None:
        return
    if not driver.is_present(by, selector):
        raise RuntimeError(f"UI_SELECTOR_MISSING:{selector}")
    expected = str(rv.value)
    driver.set_input(by, selector, expected)
    rendered = driver.get_value(by, selector)
    if not rendered:
        raise RuntimeError(f"UI_READBACK_EMPTY:{selector}")


def _documentary_mode(session: Session):
    """Resolve mode from explicit prior/PJC evidence, not the target date."""
    from .modes import CalculationMode, ModeDecision, resolve_calculation_mode

    input_dir = session.job.path / "input"
    pjc_available = any(
        p.is_file() and p.suffix.lower() in {".pjc", ".zip"}
        for p in input_dir.iterdir()
    ) if input_dir.is_dir() else False
    prior_dir = session.job.path / "calculation" / "prior_liquidation"
    prior_flag = bool(prior_dir.is_dir() and any(prior_dir.iterdir()))
    state = session.job.read_state()
    if state.get("title_reconciliation_required"):
        return ModeDecision(
            mode=CalculationMode.RECONCILIATION_REQUIRED,
            reason="title_timeline_has_unresolved_or_conflicting_adjustments",
            source="title_analysis",
            confidence=1.0,
        )
    prior_flag = prior_flag or bool(state.get("prior_liquidation_detected"))
    update_plan = bool(state.get("external_update_requested"))
    return resolve_calculation_mode(
        pjc_available=pjc_available,
        has_prior_liquidation=prior_flag,
        has_update_plan=update_plan,
        source="documentary_evidence",
    )


def _wait_present(driver, by: str, value: str, timeout: float) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.is_present(by, value):
            return True
        time.sleep(0.5)
    return False


def _ui_failure(session: Session, driver: Any, step: str,
                logs_dir: Path, message: str) -> Dict[str, Any]:
    from .browser import ArtifactContext

    ctx = ArtifactContext(out_dir=logs_dir, step=step)
    summary = driver.dump_failure(ctx)
    return {
        "ok": False,
        "job_id": session.job.job_id,
        "reason": "UI_INTERACTION_FAILED",
        "step": step,
        "message": message,
        "artifacts": summary,
    }


def validate_official_calculation(driver: Any, session: Session) -> Dict[str, Any]:
    """Lê a validação oficial; ausência de evidência bloqueia a liquidação."""
    from . import selectors as sel

    # Os totais impeditivos são renderizados na tela de liquidação
    # (liquidacao.xhtml); validacao.xhtml é apenas o histórico posterior.
    driver.goto(f"{BASE}/pages/calculo/liquidacao.jsf")
    if driver.is_present("id", sel.VALIDACAO_TOTAL_ERROS):
        raw_errors = driver.get_value("id", sel.VALIDACAO_TOTAL_ERROS)
        raw_alerts = driver.get_value("id", sel.VALIDACAO_TOTAL_ALERTAS) if driver.is_present("id", sel.VALIDACAO_TOTAL_ALERTAS) else "0"
        try:
            errors = int(raw_errors or 0)
            alerts = int(raw_alerts or 0)
        except ValueError:
            return {"ok": False, "reason": "PJECALC_VALIDATION_UNCONFIRMED",
                    "message": "Totais da validação oficial não são numéricos."}
    elif driver.is_present("css", sel.VALIDACAO_SUCESSO_CSS):
        # Quando ambos os totais são zero, a página renderiza somente o bloco
        # de sucesso e omite os inputs totalErros/totalAlertas.
        errors, alerts = 0, 0
    else:
        return {"ok": False, "reason": "PJECALC_VALIDATION_UNCONFIRMED",
                "message": "Resultado oficial de validação não foi confirmado."}
    messages = driver.get_jsf_messages()
    result = {"ok": errors == 0, "errors": errors, "alerts": alerts,
              "messages": messages, "path": "liquidacao.jsf",
              "spec_sha256": _file_sha256(
                  session.job.path / "calculation" / "calculation_spec.json"
              )}
    if errors:
        result.update(reason="PJECALC_VALIDATION_FAILED")
    _atomic_json(session.job.path / "artifacts" / "validation.json", result)
    return result


def _liquidation_evidence(driver: Any) -> Dict[str, Any]:
    """Exige estado textual/visual após o Ajax de liquidação."""
    source = driver.page_source().lower()
    markers = ("liquidado", "data de liquidação", "total devido", "resultado")
    found = [marker for marker in markers if marker in source]
    return {"confirmed": len(found) >= 2, "markers": found,
            "url": driver.current_url()}


def _write_engine_execution(session: Session, evidence: Dict[str, Any]) -> Path:
    """Persiste somente a evidência observada após a liquidação oficial."""
    from .constants import PRODUCT_NAME, PRODUCT_VERSION

    payload = {
        "engine": PRODUCT_NAME,
        "engine_version": PRODUCT_VERSION,
        "operation": "LIQUIDACAO",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "evidence": evidence,
        "totals": None,
        "spec_sha256": _file_sha256(
            session.job.path / "calculation" / "calculation_spec.json"
        ),
        "source_input_hashes": session.job.read_state().get("input_hashes", {}),
    }
    target = session.job.path / "artifacts" / "engine_execution.json"
    _atomic_json(target, payload)
    return target


def _write_artifact_manifest(session: Session) -> Path:
    """Registra hashes e origem dos artefatos, sem declarar reconciliação."""
    artifacts = session.job.path / "artifacts"
    state = session.job.read_state()
    entries: Dict[str, Any] = {}
    for path in sorted(artifacts.iterdir()):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        raw = path.read_bytes()
        entries[path.name] = {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    spec_file = session.job.path / "calculation" / "calculation_spec.json"
    manifest = {
        "job_id": session.job.job_id,
        "source_input_hashes": state.get("input_hashes", {}),
        "spec_sha256": hashlib.sha256(spec_file.read_bytes()).hexdigest()
        if spec_file.is_file() else None,
        "artifacts": entries,
        "reconciliation": {"confirmed": False, "reason": "NOT_PERFORMED"},
    }
    target = artifacts / "manifest.json"
    _atomic_json(target, manifest)
    session.job.update_state(lambda current: current.update({
        "artifacts": {
            **(current.get("artifacts") or {}),
            "manifest": str(target),
        }
    }))
    return target


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

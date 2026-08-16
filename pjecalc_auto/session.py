"""Sessão persistente de cálculo.

`Session.create()` cria um job novo; `Session.load()` abre um job existente.
Não existe mais um construtor que possa confundir essas duas operações.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .browser import BaseBrowserDriver, make_driver
from .calculation_spec import (
    CalculationSpec,
    JudicialAdjustment,
    PaymentEvent,
    ResolvedValue,
    SourceStatus,
    VerbaSpec,
    parse_br_decimal,
    parse_date,
)
from .config import ProjectPaths
from .job import Job, create_job, load_job
from .runtime import PJeCalcRuntime


class Session:
    def __init__(self, paths: ProjectPaths, job: Job,
                 spec: Optional[CalculationSpec] = None):
        if not isinstance(job, Job):
            raise TypeError("Session exige um Job; use Session.create() ou Session.load()")
        self.paths = paths
        self.job = job
        self.spec = spec or CalculationSpec(case_id=self.job.job_id, strict_mode=True)
        self.runtime: Optional[PJeCalcRuntime] = None
        self.browser: Optional[BaseBrowserDriver] = None
        self._started = False

    @classmethod
    def create(cls, paths: ProjectPaths, job_id: Optional[str] = None) -> "Session":
        return cls(paths, create_job(paths.root, job_id))

    @classmethod
    def load(cls, paths: ProjectPaths, job_id: str) -> "Session":
        job = load_job(paths.root, job_id)
        spec_path = job.path / "calculation" / "calculation_spec.json"
        if spec_path.exists():
            try:
                spec = CalculationSpec.model_validate_json(
                    spec_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise ValueError(f"CalculationSpec inválido: {spec_path}") from exc
        else:
            spec = CalculationSpec(case_id=job.job_id, strict_mode=True)
        return cls(paths, job, spec)

    def start_runtime(self) -> bool:
        """Abre o banco existente ou inicializa o seed apenas se necessário."""

        self.job.update_state(lambda state: state.update({
            "current_stage": "START_PJECALC",
        }))
        self.job.initialize_database(self.paths.seed_database())
        runtime = PJeCalcRuntime(self.paths.root, self.paths.vendor)
        try:
            runtime.start(self.job.path)
            healthy = runtime.wait_healthy()
        except Exception as exc:
            self.job.update_state(lambda state: state.update({
                "failure": {"reason": "RUNTIME_START_FAILED", "error": repr(exc)},
            }))
            raise
        if not healthy:
            runtime.stop()
            self.job.update_state(lambda state: state.update({
                "failure": {"reason": "RUNTIME_UNHEALTHY"},
            }))
            return False
        self.runtime = runtime
        self._started = True
        self.job.update_state(lambda state: state.update({
            "current_stage": "PJECALC_STARTED",
            "failure": None,
        }))
        return True

    def stop_runtime(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None
        self._started = False

    def start_browser(self, headless: bool = True) -> BaseBrowserDriver:
        if self.browser is None:
            self.browser = make_driver(
                "selenium", headless=headless,
                download_dir=self.job.path / "artifacts",
            )
            self.browser.start()
        return self.browser

    def close_browser(self) -> None:
        if self.browser is not None:
            self.browser.close()
            self.browser = None

    def shutdown(self) -> None:
        self.close_browser()
        self.stop_runtime()

    def reset_database(self, *, confirm: bool = False) -> Path:
        return self.job.initialize_database(
            self.paths.seed_database(), reset=True, confirm=confirm
        )

    # -- spec builders ------------------------------------------------------
    def set_contract(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if key in {
                "admissao", "demissao", "salario", "jornada", "cargo", "regime",
                "maior_remuneracao", "ultima_remuneracao", "aviso_previo",
                "projecao_aviso_previo",
            } and value is not None:
                normalized = value
                if key in {"admissao", "demissao"}:
                    normalized = parse_date(value)
                elif key in {
                    "salario", "jornada", "maior_remuneracao", "ultima_remuneracao",
                }:
                    normalized = parse_br_decimal(value)
                setattr(self.spec.contract, key,
                        ResolvedValue(value=normalized, status=SourceStatus.EXPLICIT))

    def add_verba(self, tipo: str, **kwargs: Any) -> None:
        v = VerbaSpec(tipo=tipo)
        resolved_fields = {
            "valor", "base", "divisor", "multiplicador", "percentual", "quantidade",
            "periodo_inicial", "periodo_final", "valor_informado_quantidade",
            "proporcionalidade", "valor_pago",
        }
        for key, value in kwargs.items():
            if value is None:
                continue
            if hasattr(v, key) and key not in {"tipo", "dobra"}:
                if key in resolved_fields:
                    # Transportes financeiros não passam por float: a camada
                    # de domínio normaliza texto BR/Decimal antes da UI.
                    normalized = parse_br_decimal(value) if key not in {
                        "periodo_inicial", "periodo_final"
                    } else parse_date(value)
                    setattr(v, key, ResolvedValue(
                        value=normalized, status=SourceStatus.EXPLICIT
                    ))
                else:
                    setattr(v, key, value)
            elif key == "dobra":
                v.dobra = bool(value)
            elif key == "descricao":
                v.descricao = str(value)
            else:
                raise ValueError(f"VERBA_FIELD_UNSUPPORTED: {key}")
        self.spec.verbas.append(v)

    def add_payment(self, payment: dict) -> None:
        self.spec.payments.append(PaymentEvent.model_validate(payment))

    def add_judicial_adjustment(self, adjustment: dict) -> None:
        self.spec.judicial_adjustments.append(
            JudicialAdjustment.model_validate(adjustment)
        )

    def set_process(self, **kwargs: Any) -> None:
        known = {
            "numero", "digito", "ano", "justica", "regiao", "vara", "estado",
            "municipio", "reclamante", "reclamado", "data_ajuizamento",
            "data_inicio_calculo", "data_termino_calculo", "data_liquidacao_atualizacao",
        }
        for key, value in kwargs.items():
            if key in known and value is not None:
                if key.startswith("data_"):
                    value = parse_date(value)
                self.spec.processo[key] = value

    def set_data_liquidacao(self, data: str) -> None:
        self.spec.processo["data_liquidacao_atualizacao"] = parse_date(data)

    def fail_closed_refusal(self, mode: str = "STANDARD") -> Optional[dict]:
        if not self.spec.strict_mode:
            return None
        problems = self.spec.critical_parameters(mode=mode)
        if problems:
            return {
                "ok": False,
                "status": "REQUIRES_REVIEW",
                "reason": "FAIL_CLOSED",
                "unresolved": problems,
                "message": "Liquidação bloqueada: parâmetros críticos não resolvidos.",
            }
        return None

    # -- state --------------------------------------------------------------
    def save_spec(self) -> Path:
        p = self.job.path / "calculation" / "calculation_spec.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with tmp.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(self.spec.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)
        return p

    def save_external_spec(self, external: Any) -> Path:
        """Persiste a especificação externa com a mesma garantia atômica."""
        p = self.job.path / "calculation" / "external_spec.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(external.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)
        return p

    def state_snapshot(self) -> Dict[str, Any]:
        state = self.job.read_state()
        return {
            "ok": True,
            "job_id": self.job.job_id,
            "stage": state.get("current_stage"),
            "runtime_started": self._started,
            "database_initialized": state.get("database_initialized", False),
            "spec": json.loads(self.spec.model_dump_json()),
        }

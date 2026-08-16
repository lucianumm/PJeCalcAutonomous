"""Executor canônico com estado persistente, dependências e resume."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .job import Job


class StepState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class PipelineConfigurationError(RuntimeError):
    pass


@dataclass
class StepRecord:
    state: StepState = StepState.PENDING
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def from_value(cls, value: Any) -> "StepRecord":
        if isinstance(value, dict):
            raw = dict(value)
            try:
                state = StepState(raw.get("state", StepState.PENDING.value))
            except ValueError:
                state = StepState.FAIL
            return cls(state, raw.get("started_at"), raw.get("finished_at"),
                       raw.get("data") or {}, raw.get("error"))
        if isinstance(value, str):
            try:
                return cls(state=StepState(value))
            except ValueError:
                return cls(state=StepState.FAIL, error=f"estado inválido: {value}")
        return cls()

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "data": self.data,
            "error": self.error,
        }


@dataclass
class StepResult:
    state: StepState
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


StepFn = Callable[["PipelineContext", Dict[str, Any]], StepResult]


@dataclass
class Step:
    name: str
    fn: StepFn
    depends_on: List[str] = field(default_factory=list)


@dataclass
class PipelineContext:
    job: Job
    runtime: Any = None
    browser: Any = None
    spec: Any = None


PIPELINE_STEPS: List[str] = [
    "INGEST", "AUDITORPROCESSUAL", "BUILD_CORPUS", "EXTRACT_CALCULATION_DATA",
    "BUILD_CALCULATIONSPEC", "PRE_AUDIT", "START_PJECALC", "POPULATE",
    "VALIDATE", "LIQUIDATE", "EXPORT_PJC", "EXPORT_PDF", "POST_AUDIT",
    "PACKAGE_RESULT", "SHUTDOWN",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Pipeline:
    def __init__(self, job: Job, required_steps: Optional[List[str]] = None):
        self.job = job
        self.required_steps = list(required_steps or PIPELINE_STEPS)
        self.steps: Dict[str, Step] = {}
        self.state = job.read_state()
        self.state.setdefault("steps", {})

    def register(self, step: Step) -> None:
        if step.name in self.steps:
            raise PipelineConfigurationError(f"step duplicado: {step.name}")
        self.steps[step.name] = step

    def _step_state(self, name: str) -> StepRecord:
        record = StepRecord.from_value(self.state.setdefault("steps", {}).get(name))
        # Um processo interrompido não pode ficar eternamente RUNNING.
        if record.state == StepState.RUNNING:
            record.state = StepState.PENDING
        self.state["steps"][name] = record.to_dict()
        return record

    def _mark(self, name: str, state: StepState, data: Optional[dict] = None,
              error: Optional[str] = None) -> None:
        now = _now()

        def mutate(current: dict) -> None:
            previous = StepRecord.from_value(
                current.setdefault("steps", {}).get(name)
            )
            record = StepRecord(
                state=state,
                started_at=previous.started_at or (now if state == StepState.RUNNING else None),
                finished_at=now if state != StepState.RUNNING else None,
                data=data if data is not None else previous.data,
                error=error,
            )
            current["steps"][name] = record.to_dict()
            if state == StepState.FAIL:
                current["failure"] = {"step": name, "error": error}
            if state != StepState.RUNNING:
                current["current_stage"] = name
                current["stage"] = name

        self.state = self.job.update_state(mutate)

    def _configuration_errors(self) -> List[str]:
        errors: List[str] = []
        for name in self.required_steps:
            if name not in self.steps:
                errors.append(f"step obrigatório não registrado: {name}")
        for step in self.steps.values():
            for dependency in step.depends_on:
                if dependency not in self.steps:
                    errors.append(f"dependência não registrada: {step.name}->{dependency}")
        return errors

    def run(self, context: PipelineContext, resume: bool = True,
            retry_failed: bool = True) -> bool:
        """Executa o fluxo; SHUTDOWN sempre roda em `finally`."""

        config_errors = self._configuration_errors()
        if config_errors:
            self.state = self.job.update_state(lambda state: state.update({
                "failure": {
                    "reason": "PIPELINE_CONFIGURATION_ERROR", "errors": config_errors,
                },
                "current_stage": "FAIL",
                "stage": "FAIL",
            }))
            raise PipelineConfigurationError("; ".join(config_errors))

        all_ok = True
        try:
            for name in self.required_steps:
                if name == "SHUTDOWN":
                    continue
                step = self.steps[name]
                previous = self._step_state(name)
                if resume and previous.state == StepState.PASS:
                    continue
                if previous.state == StepState.FAIL and not retry_failed:
                    all_ok = False
                    break
                if any(self._step_state(dep).state != StepState.PASS for dep in step.depends_on):
                    self._mark(name, StepState.SKIPPED, error="dependência não satisfeita")
                    all_ok = False
                    break
                self._mark(name, StepState.RUNNING)
                try:
                    result = step.fn(context, previous.data)
                    if not isinstance(result, StepResult):
                        raise TypeError("step deve retornar StepResult")
                except Exception as exc:  # registra e propaga a falha ao resultado
                    result = StepResult(StepState.FAIL, error=repr(exc))
                self._mark(name, result.state, result.data, result.error)
                if result.state != StepState.PASS:
                    all_ok = False
                    break
        finally:
            shutdown = self.steps.get("SHUTDOWN")
            if shutdown is not None:
                try:
                    self._mark("SHUTDOWN", StepState.RUNNING)
                    shutdown_data = StepRecord.from_value(
                        self.state.setdefault("steps", {}).get("SHUTDOWN")
                    ).data
                    result = shutdown.fn(context, shutdown_data)
                    if not isinstance(result, StepResult):
                        raise TypeError("SHUTDOWN deve retornar StepResult")
                    self._mark("SHUTDOWN", result.state, result.data, result.error)
                except Exception as exc:
                    self._mark("SHUTDOWN", StepState.FAIL, error=repr(exc))
                    all_ok = False
            else:
                all_ok = False
        terminal_stage = "DONE" if all_ok else "FAIL"
        self.state = self.job.update_state(lambda state: state.update({
            "current_stage": terminal_stage,
            "stage": terminal_stage,
        }))
        return all_ok

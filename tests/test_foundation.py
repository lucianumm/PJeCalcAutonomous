from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pjecalc_auto.audit import audit_job
from pjecalc_auto.calculation_spec import format_br_decimal, parse_br_decimal
from pjecalc_auto.config import ProjectPaths
from pjecalc_auto.job import InvalidJobId, create_job
from pjecalc_auto.pipeline import (
    Pipeline, PipelineConfigurationError, PipelineContext, Step, StepResult, StepState,
)
from pjecalc_auto.session import Session
from pjecalc_auto.runtime import _configure_shutdown_token, verify_runtime_manifest
from pjecalc_auto.validators import ImportPjcValidator


@pytest.mark.parametrize("job_id", ["../x", "a/b", r"a\\b", "", "..", "x\x00y", "/absolute"])
def test_job_id_is_safe(tmp_path: Path, job_id: str) -> None:
    with pytest.raises(InvalidJobId):
        create_job(tmp_path, job_id)


def test_seed_is_copied_once_and_existing_database_survives(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    seed_file = seed / "pjecalc.h2.db"
    seed_file.write_bytes(b"seed-v1")
    job = create_job(tmp_path, "case-1")
    destination = job.initialize_database(seed)
    destination.write_bytes(b"work-data")
    job.initialize_database(seed)
    assert destination.read_bytes() == b"work-data"
    state = job.read_state()
    assert state["database_initialized"] is True
    assert state["database_seed_sha256"]
    assert not job.state_path.with_name("state.json.tmp").exists()


def test_state_update_is_atomic_across_concurrent_mutators(tmp_path: Path) -> None:
    job = create_job(tmp_path, "state-race")

    def record(index: int) -> None:
        job.update_state(
            lambda state: state.setdefault("steps", {}).update({
                f"STEP_{index}": {"state": "PASS", "data": {"index": index}}
            })
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(24)))

    state = job.read_state()
    assert len(state["steps"]) == 24
    assert all(state["steps"][f"STEP_{i}"]["state"] == "PASS" for i in range(24))


def test_runtime_shutdown_command_is_per_job_token(tmp_path: Path) -> None:
    server = tmp_path / "tomcat" / "conf"
    server.mkdir(parents=True)
    config = server / "server.xml"
    config.write_text('<Server port="9256" shutdown="SHUTDOWN">\n</Server>', encoding="utf-8")
    _configure_shutdown_token(server.parent, "a" * 48)
    assert 'shutdown="' + ("a" * 48) + '"' in config.read_text(encoding="utf-8")


def test_runtime_manifest_can_use_external_anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    payload = vendor / "file.txt"
    payload.write_text("trusted", encoding="utf-8")
    import hashlib
    manifest = vendor / "runtime-manifest.json"
    manifest.write_text(
        '{"version":"2.16.0","files":{"file.txt":{"sha256":"'
        + hashlib.sha256(payload.read_bytes()).hexdigest()
        + '"}}}',
        encoding="utf-8",
    )
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    monkeypatch.setenv("PJECALC_RUNTIME_MANIFEST_SHA256", digest)
    assert verify_runtime_manifest(vendor, manifest)[0] is True
    monkeypatch.setenv("PJECALC_RUNTIME_MANIFEST_SHA256", "0" * 64)
    assert verify_runtime_manifest(vendor, manifest)[0] is False


def test_create_load_preserves_spec_and_verbas(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path)
    session = Session.create(paths, "case-2")
    session.set_process(numero="001", justica="JT", regiao="18", vara="1",
                         estado="GO", municipio="Goiania")
    session.set_contract(admissao="01/01/2020", demissao="01/01/2021",
                         salario="1.234,56", jornada="220")
    session.add_verba("Principal", descricao="A", base="100", divisor="30", quantidade="1", assunto_cnj="x")
    session.add_verba("Principal", descricao="B", base="200", divisor="30", quantidade="2", assunto_cnj="y")
    session.save_spec()
    loaded = Session.load(paths, "case-2")
    assert [item.descricao for item in loaded.spec.verbas] == ["A", "B"]
    assert loaded.spec.processo["numero"] == "001"
    from decimal import Decimal
    assert Decimal(loaded.spec.contract.salario.value) == Decimal("1234.56")


def test_decimal_br_parser() -> None:
    assert parse_br_decimal("1.234,56") == parse_br_decimal("1234.56")
    assert format_br_decimal("1234.5") == "1.234,50"


def test_pipeline_uses_one_step_schema_and_shutdown(tmp_path: Path) -> None:
    job = create_job(tmp_path, "pipeline")
    calls: list[str] = []
    pipeline = Pipeline(job, required_steps=["A", "B", "SHUTDOWN"])
    pipeline.register(Step("A", lambda _ctx, _data: (calls.append("A") or StepResult(StepState.PASS))))
    pipeline.register(Step("B", lambda _ctx, _data: (calls.append("B") or StepResult(StepState.PASS)), ["A"]))
    pipeline.register(Step("SHUTDOWN", lambda _ctx, _data: (calls.append("S") or StepResult(StepState.PASS))))
    assert pipeline.run(PipelineContext(job)) is True
    assert calls == ["A", "B", "S"]
    assert isinstance(job.read_state()["steps"]["A"], dict)


def test_pipeline_missing_required_step_fails_closed(tmp_path: Path) -> None:
    job = create_job(tmp_path, "pipeline-missing")
    pipeline = Pipeline(job, required_steps=["A", "SHUTDOWN"])
    pipeline.register(Step("SHUTDOWN", lambda _ctx, _data: StepResult(StepState.PASS)))
    with pytest.raises(PipelineConfigurationError):
        pipeline.run(PipelineContext(job))
    assert job.read_state()["failure"]["reason"] == "PIPELINE_CONFIGURATION_ERROR"


def test_pjc_validator_requires_zip_and_xml(tmp_path: Path) -> None:
    path = tmp_path / "calculo.pjc"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("calculo.xml", "<?xml version='1.0'?><calculo numeroProcesso='001' />")
    result = ImportPjcValidator().validate(path)
    assert result.valid is True


def test_audit_does_not_approve_presence_only(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path)
    create_job(tmp_path, "audit-case")
    result = audit_job(paths, "audit-case")
    assert result["ok"] is False
    assert result["status"] == "FAIL"

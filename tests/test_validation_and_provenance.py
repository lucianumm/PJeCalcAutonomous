from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pjecalc_auto import ops
from pjecalc_auto.auditor import AuditorProcessual
from pjecalc_auto.calculation_spec import CalculationSpec, SourceStatus
from pjecalc_auto.config import ProjectPaths
from pjecalc_auto.external_spec import ExternalCalculationSpec
from pjecalc_auto.job import StateCorruptError, create_job
from pjecalc_auto.prior_liquidation import parse_prior_liquidation
from pjecalc_auto.process_to_spec import build_spec_from_corpus
from pjecalc_auto.selector_audit import audit_selectors
from pjecalc_auto.session import Session
from pjecalc_auto.title_analysis import TitleDocument, analyze_title
from pjecalc_auto.validators import ExternalSpecValidator, ImportPjcValidator


def test_corrupt_state_is_not_silently_repaired(tmp_path: Path) -> None:
    job = create_job(tmp_path, "corrupt")
    job.state_path.write_text('{"schema_version": "bad"}', encoding="utf-8")
    with pytest.raises(StateCorruptError):
        job.read_state()


def test_external_zero_interest_is_valid() -> None:
    spec = ExternalCalculationSpec(
        data_ultima_atualizacao="01/01/2024",
        data_final_atualizacao="31/01/2024",
        indice_trabalhista="IPCA-E",
        creditos_reclamante=[{
            "key": "principal", "principal": "1.000,00",
            "ativa": True, "aplicar_juros": True, "juros": "0,00",
        }],
    )
    assert ExternalSpecValidator().validate(spec).valid


def test_external_update_accepts_inss_and_custas_groups() -> None:
    spec = ExternalCalculationSpec(
        data_ultima_atualizacao="09/03/2022",
        data_final_atualizacao="15/08/2026",
        indice_trabalhista="TR",
        descontos_reclamante=[{
            "key": "inss_reclamante", "principal": "754,39",
        }],
        outros_debitos_reclamado=[{
            "key": "inss_patronal_salarios_devidos", "principal": "38.883,78",
        }, {
            "key": "custas_reclamado", "ativa": True,
        }],
        excluir_verbas=[{"descricao": "MULTA EMBARGOS DECLARAÇÃO PROTELATÓRIOS"}],
        reclamado_remanescente="CENTRAL COMÉRCIO E CONSTRUÇÕES ELÉTRICAS LTDA.",
    )
    result = ExternalSpecValidator().validate(spec)
    assert result.valid, result.errors


def test_prior_liquidation_detects_generic_report_and_payments(tmp_path: Path) -> None:
    source = tmp_path / "processo.pdf.txt"
    source.write_text(
        "Cálculo nº 22726 elaborado em 09/03/2022\n"
        "Líquido devido ao reclamante: R$ 105.040,04\n"
        "Total devido pelo reclamado: R$ 147.504,21\n"
        "Pagamentos de R$ 13.000,00 em 26/08/2021 e R$ 19.500,00 em 13/12/2021\n",
        encoding="utf-8",
    )
    result = parse_prior_liquidation(source)
    assert result.strong_signature
    assert len(result.payments) == 2
    assert str(result.payments[0].amount) == "13000.00"


def test_title_conflict_requires_review() -> None:
    result = analyze_title([
        TitleDocument(kind="", source="sentenca", text="sentença deferida a verba"),
        TitleDocument(kind="", source="acordao", text="decisão posterior excluída a verba"),
    ])
    assert result.status == "UNRESOLVED"
    assert result.conflicts


def test_prior_liquidation_extracts_money_with_provenance(tmp_path: Path) -> None:
    source = tmp_path / "relatorio.txt"
    source.write_text("Data de liquidação: 31/01/2024\nPrincipal: R$ 1.234,56", encoding="utf-8")
    result = parse_prior_liquidation(source)
    assert result.liquidation_date is not None
    assert str(result.totals["principal"].value) == "1234.56"
    assert result.totals["principal"].provenance[0].file_sha256


def test_pjc_validator_rejects_multiple_xml(tmp_path: Path) -> None:
    path = tmp_path / "bad.pjc"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("one.xml", "<a numero='1'/>")
        archive.writestr("two.xml", "<b numero='2'/>")
    assert not ImportPjcValidator().validate(path).valid


def test_selector_audit_matches_packaged_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    report = audit_selectors(ProjectPaths(root).vendor)
    assert report["ok"]
    assert report["matched"] >= 78


def test_auditor_accepts_jsonl_piece_index(tmp_path: Path) -> None:
    output = tmp_path / "corpus"
    output.mkdir()
    (output / "manifest.json").write_text(
        '{"process_id":"001","source":{"name":"processo.pdf"}}',
        encoding="utf-8",
    )
    (output / "processo_estruturado.md").write_text("# Processo", encoding="utf-8")
    (output / "index.jsonl").write_text('{"id":"P001"}\n', encoding="utf-8")
    result = AuditorProcessual(tmp_path).validate_output(output)
    assert result["valid"] is True


def test_manifest_process_id_is_documented_without_source_metadata_pollution(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        '{"process_id":"0010604-34.2019.5.18.0129",'
        '"source":{"name":"processo.pdf","sha256":"abc"}}',
        encoding="utf-8",
    )
    (corpus / "processo_estruturado.md").write_text("# Processo", encoding="utf-8")
    spec = build_spec_from_corpus(corpus)
    assert spec.processo["numero"] == "0010604-34.2019.5.18.0129"
    assert "name" not in spec.processo
    assert spec.process_provenance["numero"][0].file_sha256 == "abc"
    assert spec.process_provenance["numero"][0].status.value == "DOCUMENTED"


def test_calculate_process_review_is_not_reported_as_spec_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ProjectPaths(tmp_path)
    session = Session.create(paths, "review-case")
    monkeypatch.setattr(
        ops, "_prepare_process", lambda *_args, **_kwargs: (
            session, {"stage": "SPEC_BUILT", "spec_path": "spec.json"}
        )
    )
    result = ops.calculate_from_process(paths, "input.pdf")
    assert result["ok"] is False
    assert result["status"] == "REQUIRES_REVIEW"
    assert result["stage"] == "REQUIRES_REVIEW"
    assert session.job.read_state()["current_stage"] == "REQUIRES_REVIEW"


def _resolved_spec_payload(case_id: str = "resolved-case") -> dict:
    def explicit(value: object) -> dict:
        return {"value": value, "status": SourceStatus.EXPLICIT.value}

    return {
        "case_id": case_id,
        "strict_mode": True,
        "contract": {
            "admissao": explicit("01/01/2020"),
            "demissao": explicit("31/12/2020"),
            "salario": explicit("2.000,00"),
            "jornada": explicit("220"),
        },
        "processo": {
            "numero": "001",
            "justica": "TRT",
            "regiao": "18",
            "vara": "1",
            "estado": "GO",
            "municipio": "GOIANIA",
            "data_liquidacao_atualizacao": "15/08/2026",
        },
        "verbas": [{
            "tipo": "Informada",
            "descricao": "VERBA TESTE",
            "assunto_cnj": "Horas extras",
            "valor": explicit("100,00"),
        }],
    }


def test_resolved_spec_uses_the_same_official_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "calculation_spec_resolved.json"
    source.write_text(json.dumps(_resolved_spec_payload()), encoding="utf-8")
    captured: dict = {}

    def fake_execute(session):
        captured["job_id"] = session.job.job_id
        captured["spec"] = session.spec
        return {"ok": True, "status": "OFFICIAL_EXECUTOR_TEST"}

    monkeypatch.setattr(ops, "execute_session", fake_execute)
    result = ops.calculate_from_resolved_spec(
        ProjectPaths(tmp_path), str(source)
    )
    assert result["ok"] is True
    assert result["status"] == "OFFICIAL_EXECUTOR_TEST"
    assert captured["job_id"] == "resolved-case"
    assert captured["spec"].source_input_hashes
    assert Path(result["spec_path"]).is_file()


def test_resolved_external_spec_never_falls_back_to_manual_math(tmp_path: Path) -> None:
    payload = _resolved_spec_payload("resolved-external")
    payload["external_update"] = {
        "data_ultima_atualizacao": "09/03/2022",
        "data_final_atualizacao": "15/08/2026",
        "indice_trabalhista": "TR",
        "excluir_verbas": [{"descricao": "MULTA EMBARGOS DECLARAÇÃO PROTELATÓRIOS"}],
    }
    source = tmp_path / "calculation_spec_resolved.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = ops.calculate_from_resolved_spec(ProjectPaths(tmp_path), str(source))
    assert result["ok"] is False
    assert result["reason"] == "BASE_PJC_REQUIRED"
    assert result["status"] == "REQUIRES_REVIEW"


def test_standard_spec_rejects_payment_that_ui_would_drop() -> None:
    spec = CalculationSpec.model_validate(_resolved_spec_payload())
    spec.payments.append({"date": "26/08/2021", "amount": "13.000,00"})
    problems = spec.critical_parameters()
    assert "unsupported_execution.payments" in problems


def test_mcp_registers_resolved_spec_tool() -> None:
    pytest.importorskip("mcp.server.fastmcp")
    from pjecalc_auto.mcp_server import build_server

    server = build_server()
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert "calculate_from_resolved_spec" in names

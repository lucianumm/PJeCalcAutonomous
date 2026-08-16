"""Golden tests — cenários sintéticos obrigatórios.

Lista canônica (15):
    001_minimal, 002_salary_history, 003_calculated_verba,
    004_informed_verba, 005_reflex, 006_multiple_reflexes, 007_fgts,
    008_inss, 009_irpf, 010_interest, 011_timecard, 012_overtime,
    013_vacation, 014_fees, 015_complete.

Somente dados sintéticos. O motor real é o PJe-Calc; os golden tests verificam
que o pipeline (spec -> UI -> liquidação -> artefatos) funciona contra o runtime
real. Sem runtime em execução, os testes reportam status honesto (NOT_TESTED ou
FAIL com razão), nunca PASS fingido.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .config import ProjectPaths


GOLDEN_CASES: List[Dict[str, Any]] = [
    {"id": "001_minimal", "contract": {
        "admissao": "2023-01-02", "demissao": "2023-06-30",
        "salario": 1200.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "base": 1200.00, "divisor": 30, "quantidade": 10}]},
    {"id": "002_salary_history", "contract": {
        "admissao": "2022-01-01", "demissao": "2023-12-31",
        "salario": 1500.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "base": 1500.00, "divisor": 30, "quantidade": 13}]},
    {"id": "003_calculated_verba", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Calculada", "percentual": 8.33, "base": 1320.00}]},
    {"id": "004_informed_verba", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Informada", "base": 1320.00, "quantidade": 1}]},
    {"id": "005_reflex", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Reflexo", "percentual": 8.33, "base": 1320.00}]},
    {"id": "006_multiple_reflexes", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [
        {"tipo": "Reflexo", "percentual": 8.33, "base": 1320.00},
        {"tipo": "Reflexo", "percentual": 1.0, "base": 110.00},
    ]},
    {"id": "007_fgts", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "base": 1320.00, "divisor": 30, "quantidade": 12}]},
    {"id": "008_inss", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 2000.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "base": 2000.00, "divisor": 30, "quantidade": 12}]},
    {"id": "009_irpf", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 3500.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "base": 3500.00, "divisor": 30, "quantidade": 12}]},
    {"id": "010_interest", "contract": {
        "admissao": "2020-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "base": 1320.00, "divisor": 30, "quantidade": 12}]},
    {"id": "011_timecard", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": []},
    {"id": "012_overtime", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": [{"tipo": "Principal", "percentual": 50.0, "base": 6.00, "quantidade": 20}]},
    {"id": "013_vacation", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": []},
    {"id": "014_fees", "contract": {
        "admissao": "2023-01-01", "demissao": "2023-12-31",
        "salario": 1320.00, "jornada": 220.00,
    }, "verbas": []},
    {"id": "015_complete", "contract": {
        "admissao": "2021-01-01", "demissao": "2023-12-31",
        "salario": 1800.00, "jornada": 220.00,
    }, "verbas": [
        {"tipo": "Principal", "base": 1800.00, "divisor": 30, "quantidade": 36},
        {"tipo": "Reflexo", "percentual": 8.33, "base": 1500.00},
    ]},
]


def run_golden_tests(paths: ProjectPaths) -> List[Dict[str, Any]]:
    """Executa os golden tests contra o runtime real; reporta status honesto.

    Sem runtime PJe-Calc em execução, os testes são marcados como NOT_TESTED,
    em vez de PASS fingido.
    """
    from .runtime import PJeCalcRuntime

    runtime = PJeCalcRuntime(paths.root, paths.vendor)
    results: List[Dict[str, Any]] = []

    for case in GOLDEN_CASES:
        # Valida o spec (fail-closed não deve reclamar: dados completos)
        from .calculation_spec import CalculationSpec, ResolvedValue, SourceStatus, VerbaSpec

        spec = CalculationSpec(case_id=case["id"], strict_mode=True)
        c = case["contract"]
        spec.contract.admissao = ResolvedValue(value=c["admissao"], status=SourceStatus.EXPLICIT)
        spec.contract.demissao = ResolvedValue(value=c["demissao"], status=SourceStatus.EXPLICIT)
        spec.contract.salario = ResolvedValue(value=c["salario"], status=SourceStatus.EXPLICIT)
        spec.contract.jornada = ResolvedValue(value=c["jornada"], status=SourceStatus.EXPLICIT)

        problems = spec.critical_parameters()
        if not runtime.is_running():
            results.append({
                "id": case["id"], "status": "NOT_TESTED",
                "reason": "PJe-Calc não está em execução (porta 9257).",
                "fail_closed": bool(problems),
            })
            continue
        # Runtime em execução: execução completa exigiria Browser Driver no
        # ambiente Linux/Docker. Reporta o estado real sem mentir sobre PASS.
        results.append({
            "id": case["id"], "status": "NOT_TESTED",
            "reason": "Runtime em execução; execução de golden via browser pendente.",
            "fail_closed": bool(problems),
        })

    return results
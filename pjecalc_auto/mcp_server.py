"""Servidor MCP (stdio) do PJeCalcAutonomous.

Exposição de ferramentas de baixo nível (operação do PJe-Calc) e de orquestração
(calculate_labor_case, calculate_from_process_corpus).

O servidor é stateless entre chamadas no que diz respeito ao PJe-Calc: cada
chamada cria/usa uma sessão isolada por job. Em produção, as ferramentas de
orquestração executam o pipeline determinístico completo.

Execução:
    python -m pjecalc_auto.mcp_server
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ProjectPaths, project_root


def build_server():
    """Constrói o servidor MCP usando o SDK `mcp` (FastMCP)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        # MCP SDK 2.x no longer ships ``mcp.server.fastmcp``.  Keep the
        # failure actionable instead of reporting the misleading "not
        # installed" message when a newer, incompatible SDK is present.
        try:
            import mcp  # type: ignore
        except ModuleNotFoundError:
            raise RuntimeError(
                "SDK `mcp` não instalado. Instale as dependências do projeto "
                "com: python -m pip install -e \".[mcp]\""
            ) from exc
        version = getattr(mcp, "__version__", "desconhecida")
        raise RuntimeError(
            "SDK MCP incompatível (versão " + str(version) + "). "
            "Este servidor usa FastMCP da série 1.x; reinstale com "
            "python -m pip install \"mcp>=1.0,<2.0\"."
        ) from exc
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "SDK `mcp` não pôde ser carregado. Reinstale com: "
            "python -m pip install \"mcp>=1.0,<2.0\""
        ) from exc

    mcp = FastMCP("pjecalc-autonomous")

    paths = ProjectPaths(project_root())
    paths.ensure()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    @mcp.tool()
    def pjecalc_health() -> dict:
        """Verifica a saúde do ambiente e do PJe-Calc (porta 9257)."""
        from .doctor import run_doctor

        report = run_doctor(paths)
        return report

    # ------------------------------------------------------------------
    # Ciclo de vida do cálculo (spec building)
    # ------------------------------------------------------------------
    @mcp.tool()
    def pjecalc_create_calculation(case_id: str | None = None) -> dict:
        """Cria uma nova sessão/job de cálculo. Retorna o job_id."""
        from .session import Session

        try:
            session = Session.create(paths, job_id=case_id)
        except FileExistsError:
            return {"ok": False, "reason": "JOB_ALREADY_EXISTS", "job_id": case_id}
        session.save_spec()
        return {"job_id": session.job.job_id, "ok": True}

    @mcp.tool()
    def pjecalc_set_contract(
        job_id: str,
        admissao: str | None = None,
        demissao: str | None = None,
        salario: str | int | float | None = None,
        jornada: str | int | float | None = None,
        cargo: str | None = None,
        regime: str | None = None,
        maior_remuneracao: str | int | float | None = None,
        ultima_remuneracao: str | int | float | None = None,
        aviso_previo: str | int | float | None = None,
        projecao_aviso_previo: str | int | float | None = None,
    ) -> dict:
        """Define os dados do contrato de trabalho do job."""
        from .session import Session
        from .job import load_job

        session = Session.load(paths, job_id)
        session.set_contract(
            admissao=admissao, demissao=demissao,
            salario=salario, jornada=jornada, cargo=cargo, regime=regime,
            maior_remuneracao=maior_remuneracao,
            ultima_remuneracao=ultima_remuneracao,
            aviso_previo=aviso_previo,
            projecao_aviso_previo=projecao_aviso_previo,
        )
        session.save_spec()
        return session.state_snapshot()

    @mcp.tool()
    def pjecalc_set_process(
        job_id: str,
        numero: str | None = None,
        digito: str | None = None,
        ano: str | None = None,
        justica: str | None = None,
        regiao: str | None = None,
        vara: str | None = None,
        estado: str | None = None,
        municipio: str | None = None,
        reclamante: str | None = None,
        reclamado: str | None = None,
        data_ajuizamento: str | None = None,
        data_inicio_calculo: str | None = None,
        data_termino_calculo: str | None = None,
        data_liquidacao: str | None = None,
    ) -> dict:
        """Define/edita os dados do processo e a data de liquidação do job.

        Permite alterar nomes das partes, número do processo e datas antes da
        liquidação — como numa geração real. Campos None não sobrescrevem.
        """
        from .session import Session

        session = Session.load(paths, job_id)
        session.set_process(
            numero=numero, digito=digito, ano=ano, justica=justica,
            regiao=regiao, vara=vara, estado=estado, municipio=municipio,
            reclamante=reclamante, reclamado=reclamado,
            data_ajuizamento=data_ajuizamento,
            data_inicio_calculo=data_inicio_calculo,
            data_termino_calculo=data_termino_calculo,
        )
        if data_liquidacao:
            session.set_data_liquidacao(data_liquidacao)
        session.save_spec()
        return session.state_snapshot()

    @mcp.tool()
    def pjecalc_add_verba(
        job_id: str,
        tipo: str,
        base: str | int | float | None = None,
        divisor: str | int | float | None = None,
        multiplicador: str | int | float | None = None,
        quantidade: str | int | float | None = None,
        percentual: str | int | float | None = None,
        dobra: bool = False,
    ) -> dict:
        """Adiciona uma verba de cálculo ao job (motor = PJe-Calc)."""
        from .session import Session
        from .job import load_job

        session = Session.load(paths, job_id)
        session.add_verba(
            tipo, base=base, divisor=divisor, multiplicador=multiplicador,
            quantidade=quantidade, percentual=percentual, dobra=dobra,
        )
        session.save_spec()
        return session.state_snapshot()

    @mcp.tool()
    def pjecalc_liquidate(job_id: str) -> dict:
        """Executa a liquidação no PJe-Calc real (fail-closed em strict mode).

        Bloqueia (FAIL_CLOSED) quando há parâmetro crítico UNRESOLVED.
        """
        from .session import Session
        from .job import load_job

        session = Session.load(paths, job_id)
        refusal = session.fail_closed_refusal()
        if refusal:
            return refusal
        # Todos os chamadores usam o mesmo executor oficial/fail-closed.
        from .ops import execute_session
        result = execute_session(session)
        result["job_id"] = session.job.job_id
        return result

    @mcp.tool()
    def pjecalc_export_pjc(job_id: str) -> dict:
        """Exporta o cálculo em .pjc via fluxo oficial do PJe-Calc.

        Requer um job já liquidado (a exportação acontece na tela oficial).
        """
        from .session import Session
        from .job import load_job

        session = Session.load(paths, job_id)
        if not session.start_runtime():
            return {"ok": False, "job_id": job_id,
                    "reason": "RUNTIME_START_FAILED",
                    "message": "Não foi possível iniciar o PJe-Calc."}
        try:
            driver = session.start_browser(headless=True)
            from .browser import login
            if not login(driver):
                return {"ok": False, "job_id": job_id,
                        "reason": "LOGIN_FAILED", "message": "Falha no login."}
            from .ops import _export_pjc
            steps: list[dict] = []
            result = _export_pjc(
                driver, session, steps,
                lambda step, message: {"ok": False, "job_id": job_id,
                                       "reason": "EXPORT_FAILED", "step": step,
                                       "message": message},
                session.job.path / "logs" / "browser",
            )
            result["job_id"] = job_id
            return result
        finally:
            session.shutdown()

    @mcp.tool()
    def pjecalc_export_pdf(job_id: str) -> dict:
        """Exporta o cálculo em PDF via fluxo oficial do PJe-Calc."""
        from .session import Session
        from .job import load_job

        session = Session.load(paths, job_id)
        if not session.start_runtime():
            return {"ok": False, "job_id": job_id,
                    "reason": "RUNTIME_START_FAILED",
                    "message": "Não foi possível iniciar o PJe-Calc."}
        try:
            driver = session.start_browser(headless=True)
            from .browser import login
            if not login(driver):
                return {"ok": False, "job_id": job_id,
                        "reason": "LOGIN_FAILED", "message": "Falha no login."}
            from .ops import _export_pdf
            steps: list[dict] = []
            result = _export_pdf(
                driver, session, steps,
                lambda step, message: {"ok": False, "job_id": job_id,
                                       "reason": "EXPORT_FAILED", "step": step,
                                       "message": message},
                session.job.path / "logs" / "browser",
            )
            result["job_id"] = job_id
            return result
        finally:
            session.shutdown()

    @mcp.tool()
    def pjecalc_update_external(
        job_id: str,
        data_ultima_atualizacao: str | None = None,
        data_final_atualizacao: str | None = None,
        indice_trabalhista: str | None = None,
        juros: str | None = None,
        base_juros_verbas: str | None = None,
        fgts_destino: str | None = None,
        fgts_correcao: str | None = None,
        creditos: list[dict] | None = None,
        base_pjc_path: str | None = None,
        base_calculation_number: str | None = None,
        descontos: list[dict] | None = None,
        outros_debitos: list[dict] | None = None,
        debitos: list[dict] | None = None,
        excluir_verbas: list[str | dict] | None = None,
        reclamado_remanescente: str | None = None,
        ignorar_taxa_negativa: bool | None = None,
        lei_11941: bool | None = None,
        irpf: bool | None = None,
        custas: bool | None = None,
        contribuicao_social_salarios_devidos: bool | None = None,
        contribuicao_social_salarios_pagos: bool | None = None,
    ) -> dict:
        """Atualiza o saldo pelo módulo oficial "Cálculo Externo" do PJe-Calc.

        Usado quando já existe liquidação anterior (EXTERNAL_UPDATE). Preenche
        `calculo-externo.xhtml` + `parcelas-atualizaveis.xhtml` com os valores
        fornecidos; nenhuma conta é feita fora do PJe-Calc. O cálculo-base
        (`.pjc`) é obrigatório: sem ele a operação não pode preservar
        pagamentos nem recalcular sua imputação.
        """
        from .session import Session
        from .external_spec import ExternalCalculationSpec
        from .validators import ExternalSpecValidator

        session = Session.load(paths, job_id)
        state = session.job.read_state()
        base = Path(base_pjc_path).expanduser().resolve() if base_pjc_path else None
        if base is None:
            saved_external = session.job.path / "calculation" / "external_spec.json"
            if saved_external.is_file():
                try:
                    saved_data = json.loads(saved_external.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    saved_data = {}
                saved_base = saved_data.get("base_pjc_path") if isinstance(saved_data, dict) else None
                if saved_base:
                    base = Path(saved_base).expanduser().resolve()
        if base is None:
            recorded = (state.get("artifacts") or {}).get("pjc_input")
            if recorded:
                base = Path(recorded).expanduser().resolve()
        if base is None:
            inputs = sorted(
                [*session.job.path.joinpath("input").glob("*.pjc"),
                 *session.job.path.joinpath("input").glob("*.zip")]
            )
            base = inputs[0].resolve() if len(inputs) == 1 else None
        if base is None or not base.is_file():
            return {"ok": False, "job_id": job_id,
                    "reason": "BASE_PJC_REQUIRED",
                    "message": "Informe o .pjc oficial do cálculo-base; planilha/PDF não preserva o rateio de pagamentos."}
        registered, _digest = session.job.register_input(base)
        session.spec.source_input_hashes = session.job.read_state().get("input_hashes", {})
        session.save_spec()
        def _exclusions(items):
            result = []
            for item in items or []:
                result.append({"descricao": item} if isinstance(item, str) else item)
            return result

        ext = ExternalCalculationSpec.model_validate({
            "case_id": job_id,
            "base_pjc_path": str(registered),
            "base_calculation_number": base_calculation_number,
            "data_ultima_atualizacao": data_ultima_atualizacao,
            "data_final_atualizacao": data_final_atualizacao,
            "indice_trabalhista": indice_trabalhista,
            "juros": juros,
            "base_juros_verbas": base_juros_verbas,
            "ignorar_taxa_negativa": ignorar_taxa_negativa,
            "fgts_destino": fgts_destino,
            "fgts_correcao": fgts_correcao,
            "creditos_reclamante": creditos or [],
            "descontos_reclamante": descontos or [],
            "outros_debitos_reclamado": outros_debitos or [],
            "debitos_reclamante": debitos or [],
            "excluir_verbas": _exclusions(excluir_verbas),
            "reclamado_remanescente": reclamado_remanescente,
            "lei_11941": lei_11941,
            "irpf": irpf,
            "custas": custas,
            "contribuicao_social_salarios_devidos": contribuicao_social_salarios_devidos,
            "contribuicao_social_salarios_pagos": contribuicao_social_salarios_pagos,
        })

        validation = ExternalSpecValidator().validate(ext)
        if not validation.valid:
            return {"ok": False, "job_id": job_id, "reason": "FAIL_CLOSED",
                    "validation": validation.as_dict()}
        external_path = session.save_external_spec(ext)
        session.job.update_state(lambda state: (
            state.update({
                "mode": "EXTERNAL_UPDATE",
                "external_update_requested": True,
                "artifacts": {
                    **(state.get("artifacts") or {}),
                    "external_spec": str(external_path),
                },
            })
        ))

        try:
            runtime_ok = session.start_runtime()
        except Exception as exc:
            return {"ok": False, "job_id": job_id,
                    "reason": "RUNTIME_START_FAILED",
                    "message": "Não foi possível iniciar o PJe-Calc.",
                    "error": repr(exc)}
        if not runtime_ok:
            return {"ok": False, "job_id": job_id,
                    "reason": "RUNTIME_START_FAILED",
                    "message": "Não foi possível iniciar o PJe-Calc."}
        try:
            try:
                driver = session.start_browser(headless=True)
            except Exception as exc:
                return {"ok": False, "job_id": job_id,
                        "reason": "BROWSER_START_FAILED",
                        "message": "Não foi possível iniciar Firefox/geckodriver.",
                        "error": repr(exc)}
            from .ops import execute_external_update

            def _fail(step, message):
                return {"ok": False, "job_id": job_id,
                        "reason": "UI_INTERACTION_FAILED",
                        "step": step, "message": message}

            result = execute_external_update(session, driver, ext, registered, _fail)
            result["job_id"] = job_id
            result["external_spec_path"] = str(external_path)
            return result
        finally:
            session.shutdown()

    @mcp.tool()
    def pjecalc_status(job_id: str) -> dict:
        """Lê estado persistido sem recriar o job."""
        from .job import load_job
        job = load_job(paths.root, job_id)
        return {"ok": True, "job_id": job_id, "state": job.read_state()}

    @mcp.tool()
    def pjecalc_validate(job_id: str) -> dict:
        """Valida a CalculationSpec localmente; não simula validação oficial."""
        from .session import Session
        from .validators import StandardSpecValidator
        session = Session.load(paths, job_id)
        result = StandardSpecValidator().validate(session.spec)
        return {"ok": result.valid, "job_id": job_id,
                "reason": None if result.valid else "FAIL_CLOSED",
                "validation": result.as_dict()}

    @mcp.tool()
    def pjecalc_add_payment(job_id: str, payment: dict) -> dict:
        from .session import Session
        session = Session.load(paths, job_id)
        session.add_payment(payment)
        session.save_spec()
        return session.state_snapshot()

    @mcp.tool()
    def pjecalc_add_judicial_adjustment(job_id: str, adjustment: dict) -> dict:
        from .session import Session
        session = Session.load(paths, job_id)
        session.add_judicial_adjustment(adjustment)
        session.save_spec()
        return session.state_snapshot()

    @mcp.tool()
    def pjecalc_set_external_calculation(job_id: str, external: dict) -> dict:
        from .session import Session
        from .external_spec import ExternalCalculationSpec
        from .validators import ExternalSpecValidator
        session = Session.load(paths, job_id)
        ext = ExternalCalculationSpec.model_validate({**external, "case_id": job_id})
        validation = ExternalSpecValidator().validate(ext)
        target = session.save_external_spec(ext)
        session.job.update_state(lambda state: state.update({
            "mode": "EXTERNAL_UPDATE",
            "external_update_requested": True,
            "artifacts": {
                **(state.get("artifacts") or {}),
                "external_spec": str(target),
            },
        }))
        return {"ok": validation.valid, "job_id": job_id,
                "status": "READY" if validation.valid else "REQUIRES_REVIEW",
                "reason": None if validation.valid else "FAIL_CLOSED",
                "validation": validation.as_dict(),
                "external_spec_path": str(target)}

    @mcp.tool()
    def pjecalc_add_external_parcel(job_id: str, group: str, parcel: dict) -> dict:
        from .session import Session
        from .external_spec import ExternalCalculationSpec, Parcela
        from .validators import ExternalSpecValidator
        session = Session.load(paths, job_id)
        target = session.job.path / "calculation" / "external_spec.json"
        ext = ExternalCalculationSpec.model_validate_json(target.read_text(encoding="utf-8")) if target.exists() else ExternalCalculationSpec(case_id=job_id)
        collection = getattr(ext, group, None)
        if not isinstance(collection, list):
            return {"ok": False, "reason": "UNKNOWN_EXTERNAL_GROUP", "group": group}
        collection.append(Parcela.model_validate(parcel))
        target = session.save_external_spec(ext)
        validation = ExternalSpecValidator().validate(ext)
        session.job.update_state(lambda state: state.update({
            "mode": "EXTERNAL_UPDATE",
            "external_update_requested": True,
        }))
        return {"ok": validation.valid, "job_id": job_id,
                "status": "READY" if validation.valid else "REQUIRES_REVIEW",
                "reason": None if validation.valid else "FAIL_CLOSED",
                "validation": validation.as_dict(),
                "external_spec_path": str(target)}

    @mcp.tool()
    def pjecalc_import_pjc(job_id: str, pjc_path: str, target_date: str | None = None) -> dict:
        from .session import Session
        from .validators import ImportPjcValidator
        session = Session.load(paths, job_id)
        if not target_date:
            return {"ok": False, "job_id": job_id,
                    "reason": "PJC_TARGET_DATE_REQUIRED"}
        validation = ImportPjcValidator().validate(Path(pjc_path), target_date=target_date)
        if not validation.valid:
            return {"ok": False, "reason": "PJC_INVALID", "validation": validation.as_dict()}
        source = Path(pjc_path).expanduser().resolve()
        copied, _digest = session.job.register_input(source)
        session.job.update_state(lambda state: state.update({
            "mode": "IMPORT_PJC",
            "current_stage": "PJC_VALIDATED",
            "artifacts": {
                **(state.get("artifacts") or {}),
                "pjc_input": str(copied),
            },
        }))
        if not session.start_runtime():
            return {"ok": False, "job_id": job_id,
                    "reason": "RUNTIME_START_FAILED"}
        try:
            driver = session.start_browser(headless=True)
            from .ops import import_pjc_official
            result = import_pjc_official(session, driver, copied, target_date)
            result.setdefault("job_id", job_id)
            return result
        finally:
            session.shutdown()

    @mcp.tool()
    def analyze_process(process_input: str) -> dict:
        from .ops import analyze_process as _analyze_process
        result = _analyze_process(paths, process_input)
        result["operation"] = "analyze"
        return result

    @mcp.tool()
    def pjecalc_audit(job_id: str) -> dict:
        """Gera o pacote de auditoria (pre/post audit + lineage)."""
        from .audit import audit_job

        return audit_job(paths, job_id)

    # ------------------------------------------------------------------
    # Ferramentas principais
    # ------------------------------------------------------------------
    @mcp.tool()
    def calculate_labor_case(
        case_id: str,
        contract: dict,
        verbas: list[dict],
        processo: dict | None = None,
        data_liquidacao: str | None = None,
    ) -> dict:
        """Calcula um caso trabalhista completo a partir de dados estruturados.

        `contract`: admissao, demissao, salario, jornada, cargo.
        `verbas`: lista de {tipo, base, divisor, multiplicador, quantidade,
        percentual, dobra}.
        `processo`: numero, digito, ano, justica, regiao, vara, reclamante,
        reclamado, data_ajuizamento.
        `data_liquidacao`: data de atualização/liquidação (dd/MM/yyyy).
        """
        from .ops import calculate_from_spec

        return calculate_from_spec(paths, case_id=case_id, contract=contract,
                                   verbas=verbas, processo=processo,
                                   data_liquidacao=data_liquidacao)

    @mcp.tool()
    def calculate_from_process_corpus(
        process_input: str,
        target_date: str | None = None,
        resolved_spec_path: str | None = None,
        external_spec_path: str | None = None,
        base_pjc_path: str | None = None,
    ) -> dict:
        """Calcula processo local e executa o pipeline oficial.

        ``process_input`` deve ser um caminho local para PDF/TXT/MD.
        ``target_date`` usa ``dd/MM/yyyy`` quando informado.

        Quando a extração documental foi revisada por um operador, informe
        ``resolved_spec_path`` para executar a ``calculation_spec_resolved.json``
        pela mesma UI oficial. Uma atualização externa adicional exige
        ``external_spec_path`` e o PJC-base; a ferramenta nunca calcula por
        fórmula Python.
        """
        from .ops import calculate_from_process, calculate_from_resolved_spec

        # Permite passar o próprio ``calculation_spec_resolved.json`` no
        # parâmetro histórico process_input, sem mudar o contrato de PDFs.
        if not resolved_spec_path:
            candidate = Path(process_input).expanduser()
            if candidate.suffix.lower() == ".json" and "calculation_spec" in candidate.name.casefold():
                resolved_spec_path = str(candidate)
        if resolved_spec_path:
            return calculate_from_resolved_spec(
                paths,
                resolved_spec_path,
                target_date=target_date,
                external_spec_path=external_spec_path,
                base_pjc_path=base_pjc_path,
            )

        return calculate_from_process(paths, process_input, target_date)

    @mcp.tool()
    def calculate_from_resolved_spec(
        resolved_spec_path: str,
        case_id: str | None = None,
        target_date: str | None = None,
        external_spec_path: str | None = None,
        base_pjc_path: str | None = None,
    ) -> dict:
        """Executa uma ``calculation_spec_resolved.json`` pela UI oficial.

        A especificação precisa estar estrita e conter proveniência suficiente;
        este método não aceita saldo, total ou pagamento inventado. Para o modo
        ``EXTERNAL_UPDATE`` o PJC oficial-base continua obrigatório.
        """
        from .ops import calculate_from_resolved_spec as _calculate

        return _calculate(
            paths,
            resolved_spec_path,
            case_id=case_id,
            target_date=target_date,
            external_spec_path=external_spec_path,
            base_pjc_path=base_pjc_path,
        )

    return mcp


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()

---
name: pjecalc-autonomous
description: Opera o PJe-Calc 2.16.0 oficial para cálculo trabalhista a partir de
  processo judicial ou dados estruturados. Use quando o objetivo for liquidar
  verbas trabalhistas, exportar PJC/PDF e auditar cálculos sem reimplementar o
  motor (o cálculo é executado pelo PJe-Calc). Nunca invente valores ausentes.
---

# PJeCalcAutonomous

Fluxo: processo → AuditorProcessual → ProcessCorpus → CalculationSpec → PJe-Calc
(Calculo.liquidar()) → PDF + PJC → auditoria.

## Ferramentas principais

- `calculate_from_process_corpus(process_input, target_date?)` — ingere
  PDF/TXT/MD via AuditorProcessual, extrai fatos documentais e, quando a
  especificação é completa e o modo é `STANDARD`, executa o pipeline oficial:
  runtime, navegador, liquidação, exportação PJC/PDF e auditoria. Parâmetro
  ausente vira `UNRESOLVED` (fail-closed) e retorna `REQUIRES_REVIEW`.
  `SPEC_BUILT` identifica somente a etapa documental de `analyze_process`, não
  uma liquidação concluída.
- `calculate_from_resolved_spec(resolved_spec_path, case_id?, target_date?,
  external_spec_path?, base_pjc_path?)` — recebe uma
  `calculation_spec_resolved.json` revisada e executa o mesmo caminho oficial.
  O arquivo transporta parâmetros e proveniência, nunca totais calculados.
  Para `EXTERNAL_UPDATE`, a especificação externa e o PJC-base são obrigatórios.
  Campos ainda não mapeados para a tela (como pagamentos ou reflexos) geram
  `REQUIRES_REVIEW`; nunca são ignorados.
- `calculate_labor_case(case_id, contract, verbas, processo?, data_liquidacao?)`
  — a partir de dados estruturados (cadastro + verbas + liquidação + PJC/PDF).
- `pjecalc_update_external(job_id, ...)` — modo EXTERNAL_UPDATE: importa o PJC
  oficial-base, atualiza saldo pelo módulo "Cálculo Externo"
  (`calculo-externo.xhtml` + `parcelas-atualizaveis.xhtml`), aplica exclusões
  de verba por descrição única, liquida novamente e exporta PJC/PDF. O PJC-base
  é obrigatório para preservar pagamentos e seu rateio.

## Modos de liquidação

- `STANDARD`: reconstruir a condenação pela origem e liquidar via
  `Calculo.liquidar()`.
- `EXTERNAL_UPDATE`: já há liquidação anterior; atualizar o saldo no PJe-Calc.
- `IMPORT_PJC`: há `.PJC` transportável; importar e reorganizar.

## Regras obrigatórias

1. Nunca reimplementar o motor em Python — o cálculo é do PJe-Calc
   (`Calculo.liquidar()`); a skill preenche a UI oficial e extrai os artefatos.
2. Nunca inventar parâmetros críticos ausentes (fail-closed → `UNRESOLVED`).
3. Cadastrar o cálculo, liquidar até a data, exportar PJC + PDF e auditar.
4. Dados sintéticos apenas nos testes; nunca commitar dados reais.

## CLI

```bash
pjecalc-auto doctor
pjecalc-auto calculate Processo.pdf
pjecalc-auto audit <job_id>
```

Consulte `README.md` e `PJECALC_REVERSE_ENGINEERING.md`.

# PJeCalc Agent

Agente local para operar o **PJe-Calc 2.16.0** oficial por CLI ou MCP.
O cálculo continua sendo executado pelo motor do PJe-Calc
(`Calculo.liquidar()`); este projeto somente prepara os dados, opera a UI
oficial e valida os artefatos produzidos.

> Status: pronto para execução local controlada. A liquidação oficial só é
> declarada quando houver dados críticos, credenciais, evidência da UI, PJC,
> PDF e reconciliação independentes. Ausências resultam em `REQUIRES_REVIEW`.

## Fluxo

```text
processo → AuditorProcessual → ProcessCorpus → CalculationSpec
         → PJe-Calc oficial → liquidação → PJC/PDF → auditoria
```

## O que está incluído

- CLI e servidor MCP com as mesmas operações do pipeline.
- Execução fail-closed: nenhum salário, data, verba ou total é inventado.
- Jobs isolados em `.jobs/<job_id>` com estado atômico, hashes e proveniência.
- Runtime PJe-Calc 2.16.0 com manifesto de integridade.
- Selenium compatível com a UI JSF/RichFaces legada.
- Validação de PJC/PDF, auditoria cruzada e diagnósticos redigidos.
- Testes unitários, CI e scripts determinísticos para dependências externas.

## Estrutura

```text
pjecalc_auto/                 pacote CLI/MCP, runtime, browser e auditoria
tests/                        testes automatizados
scripts/                      obtenção do Auditor/geckodriver e vendorização
vendor/pjecalc/2.16.0/        runtime e seed H2 do PJe-Calc
SKILL.md                      contrato operacional do agente
IMPLEMENTATION_REPORT.md      evidências e bloqueios atuais
PJECALC_REVERSE_ENGINEERING.md  fatos técnicos verificados do runtime
```

## Requisitos

- Python 3.10 ou superior.
- Java 8/JRE 8 (Java moderno não é aceito pelo PJe-Calc 2.16.0).
- Firefox e geckodriver compatíveis, com Selenium.
- MCP SDK 1.x (`mcp>=1.0,<2.0`), `pypdf` e Pillow.
- AuditorProcessual na revisão fixada pelo script.
- Credenciais e contexto de serviços fornecidos pelo ambiente de execução.

O projeto não assume que Java, Firefox ou geckodriver possam ser
redistribuídos. Confirme as licenças dos componentes e da instalação oficial
antes de publicar binários ou um runtime completo.

## Instalação

```bash
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[mcp,selenium,pdf,dev]"
python scripts/fetch_auditor.py --project .
```

O servidor usa a API `FastMCP` da série 1.x. A instalação do projeto fixa
`mcp<2`; se uma instalação antiga já tiver colocado o MCP 2.x no ambiente,
execute novamente `python -m pip install -e ".[mcp]"` para alinhar a versão.

No Windows, `scripts/fetch_geckodriver.py --project .` baixa a versão fixada
para o Firefox legado do instalador. No Linux, instale um geckodriver Linux
compatível e aponte `PJECALC_GECKODRIVER` para ele.

## Configuração

Copie `.env.example` para o mecanismo de configuração do seu ambiente. O
projeto não carrega `.env` automaticamente; injete as variáveis pelo processo
que inicia o agente:

```text
PJECALC_AUTONOMOUS_HOME=/path/to/PJeCalcAgent
PJECALC_JAVA_HOME=/path/to/java8
PJECALC_FIREFOX_BIN=/path/to/firefox
PJECALC_GECKODRIVER=/path/to/geckodriver
PJECALC_USERNAME=<usuario>
PJECALC_PASSWORD=<senha>
PJECALC_TOKEN_SERVICOS=<token>
PJECALC_PJE_CONTEXT=<contexto>
PJECALC_RUNTIME_MANIFEST_SHA256=<ancora-externa-opcional>
```

`PJECALC_ALLOW_DEV_PLACEHOLDERS=1` só deve ser usado em smoke tests locais;
nunca em produção.

## Verificação local

```bash
python -m pjecalc_auto.cli doctor --full
python -m pytest -q
python -m compileall -q pjecalc_auto scripts tests
```

O `doctor --full` deve ser interpretado junto com o relatório: boot,
exportações e âncora externa podem permanecer `NOT_TESTED` sem uma sessão
autenticada.

## CLI

```bash
python -m pjecalc_auto.cli doctor --full
python -m pjecalc_auto.cli analyze /caminho/processo.pdf
python -m pjecalc_auto.cli calculate /caminho/processo.pdf \
  --liquidation-date 15/08/2026
python -m pjecalc_auto.cli status <job_id>
python -m pjecalc_auto.cli audit <job_id>
```

`calculate` só retorna `ok=true` depois da execução oficial, validação da UI,
exportação de PJC/PDF e auditoria. Um processo documental incompleto retorna
`REQUIRES_REVIEW` e não gera um valor estimado.

## Especificação documental revisada

Um PDF pode conter todos os fatos necessários, mas o extrator automático não
tem autorização para escolher premissas quando a proveniência está ambígua.
Depois de uma revisão documental, entregue a especificação canônica ao MCP:

```text
calculate_from_resolved_spec(
  resolved_spec_path="/dados/calculation_spec_resolved.json"
)
```

Esse caminho apenas troca a etapa de extração. Ele ainda valida a
`CalculationSpec`, exige `strict_mode=true`, inicia o runtime, preenche a UI
oficial, liquida e exporta PJC/PDF. Não calcula totais por Python e não aceita
um JSON de resultado como se fosse evidência do PJe-Calc. Pagamentos já
imputados, exclusões posteriores e qualquer atualização externa continuam
exigindo `external_spec_path` e o PJC oficial-base.
Se o JSON contiver campos que o executor de verbas ainda não mapeia (por
exemplo, pagamentos, reflexos ou histórico salarial), o MCP retorna
`REQUIRES_REVIEW` em vez de descartar esses dados silenciosamente.

## Atualização externa de cálculo já liquidado

Quando o processo já possui uma liquidação oficial, use o PJC dessa conta como
base. O PDF ou a planilha não substituem o PJC: somente o arquivo `.pjc`
preserva as parcelas e o rateio dos pagamentos para que o PJe-Calc refaça a
imputação depois de uma exclusão de verba.

O MCP expõe `pjecalc_update_external` com:

- `base_pjc_path` e `base_calculation_number` para validar a origem;
- grupos `creditos`, `descontos`, `outros_debitos` e `debitos`, incluindo INSS
  segurado/patronal e custas;
- `excluir_verbas` por descrição única e `reclamado_remanescente`;
- liquidação até `data_final_atualizacao`, validação oficial e exportação do
  novo PJC e PDF.

Exemplo mínimo (valores devem vir da documentação/PJC, nunca de estimativa):

```json
{
  "job_id": "0010604-34.2019.5.18.0129",
  "base_pjc_path": "/dados/calculo-22726.pjc",
  "base_calculation_number": "22726",
  "data_ultima_atualizacao": "09/03/2022",
  "data_final_atualizacao": "15/08/2026",
  "indice_trabalhista": "TR",
  "descontos": [{"key": "inss_reclamante", "principal": "..."}],
  "outros_debitos": [{"key": "inss_patronal_salarios_devidos", "principal": "..."}],
  "excluir_verbas": [{"descricao": "MULTA EMBARGOS DECLARAÇÃO PROTELATÓRIOS"}],
  "reclamado_remanescente": "CENTRAL COMÉRCIO E CONSTRUÇÕES ELÉTRICAS LTDA."
}
```

Se o PJC-base, a descrição exata ou qualquer parâmetro crítico não estiver
disponível, a operação retorna `REQUIRES_REVIEW`/`FAIL_CLOSED`.

## MCP local

```bash
python -m pjecalc_auto.mcp_server
```

O servidor atual usa transporte **stdio** e deve ser iniciado pelo host do
agente. Para torná-lo um serviço público, é obrigatório adicionar transporte
HTTP, autenticação, autorização por usuário/job, isolamento de arquivos,
limites de recursos e rate limiting. Não exponha o processo stdio diretamente
à rede.

## Dados e segurança

- Não versione processos, CPF/CNPJ, PJC, PDF, banco H2, logs ou credenciais.
- Jobs e artefatos locais são ignorados pelo Git.
- O runtime usa shutdown por token aleatório e verifica o manifesto antes do
  boot.
- O agente falha fechado quando há fato ausente, conflito, seletor ausente ou
  evidência oficial insuficiente.
- Veja [`SECURITY.md`](SECURITY.md) e
  [`IMPLEMENTATION_REPORT.md`](IMPLEMENTATION_REPORT.md).

## Estado de validação

As correções atuais têm 78 testes automatizados passando, incluindo a
construção do servidor MCP e o caminho de especificação resolvida, além de
smoke tests locais de Java/Tomcat/Firefox. A execução oficial de um processo
específico ainda depende das informações jurídicas, do PJC-base quando for
atualização externa e das credenciais daquele caso; isso é intencional e não
é substituído por fórmula Python.

## Licenciamento

Este repositório contém código de integração e referências a um runtime
externo. A ausência de um arquivo `LICENSE` não concede licença implícita para
redistribuir o PJe-Calc, Java, Firefox, geckodriver ou dados de processos.
Verifique os termos aplicáveis antes de publicar qualquer binário.

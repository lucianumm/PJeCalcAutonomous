# Relatório de implementação e validação

Status atual: `INCOMPLETE` para liquidação oficial end-to-end.

As correções de engenharia foram aplicadas ao repositório correto
(`PJeCalcAutonomous`). O sistema agora é executável localmente e encerra com
`REQUIRES_REVIEW` quando a documentação não contém parâmetros críticos. Não
foi fabricado valor de liquidação, PJC ou PDF.

## Correções aplicadas

- Dependências declaradas e verificadas: MCP, Selenium, `pypdf` e Pillow.
- O SDK MCP deixou de ser uma dependência aberta (`mcp>=1.0`): o servidor usa
  `FastMCP` da série 1.x e agora instala/valida `mcp>=1.0,<2.0`. Antes disso,
  uma instalação atual do MCP 2.x fazia `python -m pjecalc_auto.mcp_server`
  falhar com `ModuleNotFoundError: mcp.server.fastmcp`.
- Java 8, runtime PJe-Calc 2.16.0, Firefox legado e geckodriver passaram a ser
  resolvidos de forma explícita ou pelo bundle. O script
  `scripts/fetch_geckodriver.py` fixa versão, URL e SHA-256.
- O driver Selenium foi compatibilizado com Firefox 55/geckodriver 0.19.x:
  usa preferências diretamente no `Options`, reserva uma porta concreta,
  remove apenas argumentos BiDi incompatíveis e guarda o log por job.
- O AuditorProcessual é obtido em revisão imutável
  `64b53871441c4abcfaa08ad8f414c860aa955651`; o validador aceita o
  `index.jsonl` produzido pela versão instalada e registra a revisão.
- O fluxo processo → corpus → `CalculationSpec` preserva hash/proveniência,
  registra também o status de cada proveniência, não copia metadados de origem
  para campos processuais e não inventa partes a partir de nomes genéricos de
  PDF.
- Falhas de parâmetros críticos agora persistem `REQUIRES_REVIEW` (e nunca
  `SPEC_BUILT` como se a execução oficial tivesse ocorrido).
- Foi adicionado o caminho MCP `calculate_from_resolved_spec`: uma
  `calculation_spec_resolved.json` revisada pode substituir somente a etapa de
  extração e é então enviada ao mesmo executor/UI oficial. O caminho continua
  estrito e não transforma um JSON documental em resultado financeiro.
- Atualizações de `state.json` usam read-modify-write atômico sob lock,
  evitando perda de eventos concorrentes.
- O shutdown do Tomcat usa token aleatório por job e `server.xml` isolado; o
  comando estático `SHUTDOWN` não é aceito.
- O `EXTERNAL_UPDATE` agora importa o PJC-base antes de operar, suporta os
  grupos de descontos/INSS/custas, permite remover uma verba posterior por
  descrição única e só termina após validação oficial e exportação de PJC/PDF.
- A detecção documental reconhece relatórios de liquidação mesmo quando o PDF
  tem nome genérico e registra pagamentos e candidatos a exclusão com
  proveniência.
- A integridade do runtime é verificada pelo manifesto interno e pode ser
  ancorada por SHA-256 externo (`PJECALC_RUNTIME_MANIFEST_SHA256`).
- Diagnósticos do navegador redigem senha/token; segredos permanecem somente
  em variáveis de ambiente. O README, `.env.example`, doctor e scripts foram
  atualizados.

## Evidências executadas

```text
python -m compileall -q pjecalc_auto scripts tests   PASS
python -m pytest -q                                  78 passed
git diff --check                                     PASS
doctor --full                                       runtime/dependências PASS;
                                                    FAIL sem credenciais;
                                                    boot/exports/âncora NOT_TESTED
runtime smoke (Java/Tomcat/health/shutdown)         PASS com placeholders de dev
Firefox/geckodriver data-URL smoke                  PASS
PJe-Calc UI smoke                                   PASS: redirect para principal.jsf
processo real 0010604-34.2019.5.18.0129             REQUIRES_REVIEW (fail-closed)
MCP FastMCP build + resolved-spec guards             PASS
```

O processo real de 1.234 páginas foi ingerido e gerou corpus/spec com hash do
PDF e número processual proveniente do manifesto. A execução parou
corretamente porque faltam, entre outros, salário, jornada, admissão,
demissão, vara, município e parâmetros da verba. Assim, não houve acesso à
UI autenticada, não foram baixados PJC/PDF oficiais e a reconciliação H2 não
foi declarada.

## Segurança e limites restantes

Corrigidos e cobertos por testes: corrida de estado, shutdown Tomcat sem
autorização, resolução/âncora opcional do runtime, redaction de diagnósticos,
proveniência básica e recusa fail-closed.

Ainda dependem de decisão/infraestrutura: política de autorização por
`job_id` no MCP stdio local, exposição inevitável de credenciais nos
argumentos/propriedades exigidos pelo PJe-Calc, confiança operacional no
AuditorProcessual externo e reconciliação independente dos totais H2. Esses
itens não são mascarados como resolvidos.

## Para concluir o end-to-end oficial

1. Configurar Java 8/runtime, Firefox/geckodriver e a revisão do Auditor.
2. Injetar `PJECALC_USERNAME`, `PJECALC_PASSWORD`,
   `PJECALC_TOKEN_SERVICOS` e `PJECALC_PJE_CONTEXT` por secret manager.
3. Fornecer ou revisar os parâmetros críticos ausentes do processo.
4. Executar a sessão autenticada, confirmar a validação oficial, baixar PJC e
   PDF e produzir a reconciliação independente antes de declarar `PASS`.

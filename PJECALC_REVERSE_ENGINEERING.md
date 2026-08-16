# PJe-Calc 2.16.0 — Engenharia Reversa Consolidada

Documento-fonte para futuras skills/agentes que precisem operar o **PJe-Calc
2.16.0** oficial. Todo o conteúdo abaixo foi obtido por evidência
(decompilação CFR 0.152, análise de bytecode, leitura de binários/XML reais e
consulta ao H2). Prioridade de confiança:

```
RUNTIME REAL > BYTECODE > CFR/DECOMPILAÇÃO > KNOWLEDGE DB > DOCUMENTAÇÃO > HIPÓTESE
```

---

## 1. Visão geral do produto

| Item | Valor |
|------|-------|
| Produto | PJe-Calc (TRT8) |
| Versão investigada | 2.16.0 |
| Main-Class do launcher | `br.jus.trt8.pjecalcsa.container.Lancador` |
| Tomcat embutido | 7.0.67 (`org.apache.catalina.startup.Bootstrap`) |
| Java | 8 (JRE empacotada 1.8.0_241 no Windows) |
| Banco | H2 1.3.154, arquivo `.dados/pjecalc.h2.db` |

Módulos **first-party** (bytecode próprio):

- `pjecalc.jar` → launcher (`br.jus.trt8.pjecalcsa.*`)
- `pjecalc-base-2.16.0.jar` (utilidades, JRAdapter, persistence base)
- `pjecalc-negocio-2.16.0.jar` (1147 classes: domínio + motor + serviços)
- `pjecalc-integracao-2.16.0.jar` (client REST / DTOs PJe)
- `web-classes` (`br.jus.trt8.pjecalc.web.*`: apresentadores Seam + controllers JSF)

Stack de terceiros (identificada em `WEB-INF/lib`):

Seam 2.2.0.GA · JSF 1.2_13 (Mojarra) · Facelets 1.1.15 · RichFaces 3.3.4 ·
Hibernate 3.3.2.GA · Drools 5.5.0.Final · JasperReports 3.7.6 · H2 1.3.154 ·
RESTEasy 2.2.0.GA · XStream 1.1.3/1.3.1 · Selenium 2.21.0 (embarcado).

---

## 2. Bootstrap / Inicialização

`iniciarPjeCalc.bat` → JRE empacotada → `java -jar bin/pjecalc.jar`.

Argumentos JVM reais:

```
-Xms1024m -Xmx2048m -XX:MaxPermSize=512m
-Duser.timezone=GMT-3
-Dfile.encoding=ISO-8859-1
-Dseguranca.pjecalc.tokenServicos=<token>
-Dseguranca.pjekz.servico.contexto="https://pje.trtXX.jus.br/pje-seguranca"
-splash:pjecalc_splash.gif
```

Fluxo REAL do `Lancador.main`:

```
Lancador.main
 ├─ System.setProperty("caminho.instalacao", <cwd absoluto>)
 ├─ configurarVariaveisBase():
 │    PORTA_HTTP_EM_USO = PORTA_HTTP = 9257 (fixa)
 │    SITE = "http://localhost:9257/pjecalc"
 ├─ validarPastaInstalador():
 │    se .dados/pjecalc.h2.db NÃO existe → erro + exit(1)
 │    se persistence.xml.tmp existe → reescreve persistence.xml (Ora4H2) e apaga .tmp
 ├─ iniciarAplicacao():
 │    new Socket("localhost", 9257)
 │      ├─ OK (porta ocupada) → diálogo "aplicação antiga ainda ativa"
 │      └─ falha (porta livre) → prossegue
 │    TomCat("tomcat").startTomcat()
 │      └─ Bootstrap: setCatalinaHome("tomcat"); init(); start()
 │    SystemTray/Desktop? → bandeja + abrirSite() ; senão → Janela Swing
 └─ fecha splash
```

`abrirSite()` (Windows) abre `navegador/windows/FirefoxPortable.exe http://localhost:9257/pjecalc`.
No Linux não há bandeja/Janela/FirefoxPortable — a parte Java efetiva (passos
de validação + Tomcat embutido) é o que importa para o cálculo.

### Portas (server.xml real)

| Finalidade | Porta | Bind |
|-----------|-------|------|
| HTTP | 9257 | 127.0.0.1 |
| Shutdown | 9256 | 127.0.0.1 |
| AJP | 9258 | 127.0.0.1 |

Nunca há bind em `0.0.0.0`. Contexto web: `/pjecalc`.

### Banco de dados

- URL: `jdbc:h2:.dados/pjecalc` (relativo ao CWD do processo Tomcat).
- Usuário `pjecalc`, senha `/pjecalc/` (DataSource em
  `tomcat/webapps/pjecalc/META-INF/context.xml`).
- `persistence-unit PJECALC_H2`, `hibernate.hbm2ddl.auto=validate`, dialeto
  customizado `DialetoH2`.
- Migração Oracle→H2 via `PersistenceParse` (Ora4H2) quando `persistence.xml.tmp`
  está presente.

> **Boot alternativo (sem `Lancador`)** — o que foi constatado em execução real:
> ao iniciar o Tomcat direto via `org.apache.catalina.startup.Bootstrap`, é
> obrigatório:
> 1. Ter `WEB-INF/classes/META-INF/persistence.xml` presente (o `Lancador` gera
>    a partir de `persistence.xml.tmp`; sem isso, Hibernate falha com
>    "No Persistence provider for EntityManager named PJECALC_H2").
> 2. Ter `tomcat/lib/` (referenciado pelo `common.loader` de `catalina.properties`).
> 3. Repassar as `-D` do `iniciarPjeCalc.bat` (`seguranca.pjecalc.tokenServicos`,
>    `seguranca.pjekz.servico.contexto`) — sem elas, `logon.xhtml` lança NPE em
>    `#{aplicacao.urlPJe}` (`Aplicacao.getUrlPJe()`).
> 4. Para logon por usuário/senha (automação), usar
>    `-Dconfiguracao.pjecalc.ambiente=DEV` (`Aplicacao.isAmbienteDesenvolvimento()`).
>    Em produção (`PRD`) o logon é único via PJe/certificado.

---

## 3. Arquitetura de camadas

```
XHTML/JSF (pages/*.xhtml)
   → Apresentador* (Seam @Name, @Scope)               [br.jus.trt8.pjecalc.web.apresentador]
   → ServicoDeCalculo / ServicoDeValidacao / ServicoDrools / ServicoDeRelatorio
   → DOMÍNIO (br.jus.trt8.pjecalc.negocio.dominio.*)
   → Calculo.liquidar()
   → MaquinaDeCalculo* (motor)
   → Hibernate/JPA → H2 (tabelas TB*)
   → JasperReports (.jrxml + JRAdapter) → PDF
   → Exportador/Importador (XMLExpScanProcessor/XMLImpScanProcessor) → .pjc
```

452 componentes Seam; padrão dominante: entidades `@Name` + `@Scope(CONVERSATION)`
+ um `apresentador*` por tela. `pages.xml` centraliza as regras de navegação
(`if-outcome`), `@Begin`/`@End` de conversações aninhadas.

Web: `state saving = server`, `numberOfViewsInSession=1`, sessão sem timeout
(`session-timeout -1`), `SeamFilter` com `maxRequestSize 20MB`.

**Não existe API REST de cálculo.** Os 9 endpoints JAX-RS são apenas
sincronização/atualização de tabelas (precatórios, SELIC, tabelas nacionais).
Operações de cálculo acontecem exclusivamente no fluxo JSF/Seam/RichFaces
(FacesServlet `*.jsf` + postback/Ajax).

---

## 4. Motor de cálculo (ponto central)

### Ordem REAL de liquidação (`Calculo.liquidar()`)

```
1. zerarOrdem()
2. validar verbas informadas sem constante (MSG0019)
3. validarDisponibilidadeDaMaiorRemuneracaoNaLiquidacao()
4. validarDisponibilidadeDaUltimaRemuneracaoNaLiquidacao()
5. validarUsoCorretoDoHistoricoSalarial()
6. validarVerbaPossuiQuantidade()
7. marcar todas verbas liquidado=false
8. TabelaDeCorrecaoMonetaria.carregarTabela(admissão .. dataLiquidacao)
9. para cada verba ativa: verba.liquidar()  (reflexos: precedência de bases)
10. salarioFamilia.liquidar()
11. seguroDesemprego.liquidar()
12. fgts.liquidar()
13. inss.liquidar(dataLiquidacao)
14. previdenciaPrivada.liquidar()
15. calcularJuros()
16. pensaoAlimenticia.liquidar() (se existir)
17. multa.liquidar() (para cada multa)
18. honorario.liquidar() (para cada honorário)
19. irpf.liquidar()
20. custasJudiciais.liquidar()
21. salvar()
22. versao++ ; hashCodeLiquidacao = calcularHashCodeDaLiquidacao()
```

### Fórmula central (por ocorrência)

`MaquinaDeCalculo.calcularValorDevidoDaOcorrencia()`:

```
base         = BaseVerba.resolverValor(parametro) (+ BaseTabelada, se houver)
divisor      = Divisor.resolverValor(parametro)
multiplicador= Multiplicador.resolverValor(parametro)
quantidade   = Quantidade.resolverValor(parametro)

devido = (base / divisor) * multiplicador * quantidade   // MathContext(38)
se dobra == true:  devido = devido * 2
devido = arredondarValorMonetario(devido)                // setScale(2, HALF_EVEN)
pago   = ValorPago.resolverValor(parametro) → arredondado a 2 casas
```

### Subclasses de `MaquinaDeCalculo<T extends VerbaDeCalculo>`

- `MaquinaDeCalculoDaVerbaCalculada` — fórmula completa.
- `MaquinaDeCalculoDaVerbaInformada` — valor constante (`FormulaInformada.constante`).
- `MaquinaDeCalculoDaVerbaReflexo` — base derivada de outra verba (precedência).
- `MaquinaDeCalculoDeCorrecaoMonetaria` — índices acumulados.

Duas fases (`ModoDeCalculoEnum`): `GERACAO_DE_OCORRENCIA` (quebra de período em
meses via `HelperDate.breakInMonths`) e `LIQUIDACAO`.

### Família completa de máquinas

`MaquinaDeCalculoDoFgts`, `DoInss`, `DeIrpf`, `DeCustas`, `DeHonorarios`,
`DeMulta`, `DePensaoAlimenticia`, `DePrevidenciaPrivada`, `DeSalarioFamilia`,
`DeSeguroDesemprego`, `DeCartaoDePonto`, `DeRateioDoPagamento`.

### Arredondamento (`Utils`)

- `CONTEXTO_MATEMATICO = MathContext(38)` (precisão intermediária).
- `arredondarValorMonetario(v) = v.setScale(2, HALF_EVEN)`.
- `arredondarValorRegraIRPF` = `setScale(4, HALF_EVEN)` (IR).
- Índices: `índiceAcumulado * valor` (ou `/` se negativo), arredondado a 2 casas.
- Arredondamento monetário acontece **no final** (`setDevido/setPago/setBase`).

### Rotinas auxiliares (`comum.rotinasdecalculo`)

`CalculadorDeIndices`, `CalculoDoIntegralizar`, `CalculoDoProporcionalizar`,
`CalculoDoPrazoDeFerias`, `CalculoDaQuantidadeApuradaDoPrazoAvisoPrevio`,
`CalculoDoSalarioEmFerias`, `RotinaDeCalculo`.

---

## 5. Domínio ↔ persistência (tabelas-chave)

| Conceito | Classe | Tabela |
|----------|--------|--------|
| Cálculo (raiz) | `Calculo` | TBCALCULO |
| Verba (catálogo) | `Verba` | TBVERBA |
| Verba no cálculo | `VerbaDeCalculo` | TBVERBACALCULO |
| Fórmula | `Formula` (+Calculada/Reflexo/Informada) | TBFORMULA |
| Termos | `Termo`:{Divisor, Quantidade, ValorPago, ItemBaseVerba} | TB* |
| Ocorrência mensal | `OcorrenciaDeVerba` | TBOCORRENCIAVERBA |
| Histórico salarial | `HistoricoSalarial` | TBHISTORICOSALARIAL |
| Férias / Falta | `Ferias` / `Falta` | TBFERIAS / TBFALTACALCULO |
| FGTS / INSS / IRPF | `Fgts` / `Inss` / `Irpf` | TBFGTS / TBINSS / TBIRPF |
| Juros | `ApuracaoDeJuros` | TBAPURACAOJUROSCALCULO |
| Atualização | `ParametrosDeAtualizacao` etc. | TBPARAMATUALIZACAOCALCULO e afins |
| Cartão de ponto | `CartaoDePonto` | TBCARTAODEPONTO |

156 entidades, 148 mapeadas verbatim; 151 tabelas, 138 sequences, 157 FKs, 307 índices.

---

## 6. UI / Seam / RichFaces

- FacesServlet em `*.jsf`; `SeamResourceServlet` em `/servico/*`.
- Filtros: `SeamFilter` (`/*`, max 20MB) + `TratamentoUrlRichFaces` (`/a4j/*`).
- Navegação central em `WEB-INF/pages.xml`:
  - `login-view-id=/logon.jsf`, `no-conversation-view-id=/pages/principal.xhtml`.
  - outcomes: `calculo`, `verbaDeCalculo`, `liquidacao`, `exportacao`,
    `validacao`, `relatorio`, `historicoSalarial`, `ferias`, `falta`, `inss`,
    `fgts`, `irpf`, `custasJudiciais`, `honorarios`, `pagamento`, etc.
- `ServicoDeCalculo.calculoAberto` guarda o cálculo ativo na conversação
  (single-user por sessão; `numberOfLogicalViews=1`).

### Identificadores reais de componentes (para automação)

O formulário principal de conteúdo usa `id="formulario"`; no DOM os ids ficam
prefixados: `formulario:<campo>`.

**Logon** (`logon-usuario-senha.xhtml`): o `<h:form>` **não tem id** e o botão
de login **não tem id** (apenas `styleClass="bt_logon"`). Seletores CSS reais:

- usuário: `input[id$='usuarioUS']`
- senha: `input[id$='senhaUS']`
- botão: `input.bt_logon`

**Principal** (`pages/principal.xhtml`):

- "Criar Novo Cálculo": `a[title='Criar Novo Cálculo']`
  (action `#{apresentadorCalculo.iniciarComNovo}`).

**Cálculo** (`pages/calculo/calculo.xhtml`) — form `formulario`:

- Processo: `formulario:numero`, `formulario:digito`, `formulario:ano`,
  `formulario:justica`, `formulario:regiao`, `formulario:vara`,
  `formulario:valorDaCausa`, `formulario:autuadoEm`,
  `formulario:reclamanteNome`, `formulario:reclamadoNome`.
- Parâmetros: `formulario:dataAdmissao`, `formulario:dataDemissao`,
  `formulario:dataAjuizamento`, `formulario:dataInicioCalculo`,
  `formulario:dataTerminoCalculo`, `formulario:valorMaiorRemuneracao`,
  `formulario:valorUltimaRemuneracao`, `formulario:valorCargaHorariaPadrao`,
  `formulario:tipoDaBaseTabelada`, `formulario:apuracaoPrazoDoAvisoPrevio`,
  checkboxes (`prescricaoQuinquenal`, `zeraValorNegativo`, `sabadoDiaUtil`, etc.).
- Ações: `formulario:buscar`, `formulario:limpar`, `formulario:salvar`,
  `formulario:cancelar`.

**Liquidação** (`pages/calculo/liquidacao.xhtml`):

- `formulario:dataDeLiquidacao` (`rich:calendar`; input renderizado com sufixo
  `InputDate` → usar `formulario:dataDeLiquidacaoInputDate` ou CSS `[id$='...InputDate']`).
- `formulario:indicesAcumulados` (radio).
- `formulario:liquidar` (action `#{apresentador.liquidar}`).
- Totais de pendências: `formulario:totalErros`, `formulario:totalAlertas`.

**Exportação PJC** (`pages/calculo/exportacao.xhtml`):

- `formulario:exportar` (`#{apresentador.exportar}`)
- `formulario:linkDownloadArquivo` (quando `downloadDisponivel`).

**Relatório PDF** (`pages/calculo/relatorio/relatorio-calculo.xhtml`):

- `formulario:formatoSaida` (radio), `formulario:tipoDeRelatorio` (checkboxes)
- `formulario:imprimirConsolidado` (`#{apresentador.gerarRelatorioConsolidado}`)
- `formulario:linkDownloadConsolidado`.

### RichFaces 3.3.4/JSF 1.2 — notas de automação

- `rich:calendar` com `enableManualInput="true"` renderiza o input editável com
  suffix `InputDate`.
- Botões `a4j:commandButton` com `id` explícito aparecem como
  `formulario:<id>` (input de submit).
- `h:selectOneRadio` vira vários `<input type="radio" name="formulario:<id>">`
  seguidos de `<label>`; selecione por contém do texto do label.

---

## 7. Drools / Validação

- Validação de cálculo: código Java (`*.ValidRule`) **e** Drools
  (`pjecalc.validacao` = 80 regras) via `ServicoDeValidacao`.
- Justificativas: Drools (`pjecalc.justificativa` = 39 regras).
- Regras de negócio de cálculo ficam no domínio (métodos), não em Drools.

---

## 8. Relatórios (PDF)

- JasperReports: 129 `.jrxml`, preenchidos via
  `JasperFillManager.fillReport(..., JREmptyDataSource(1))` com dados dos
  `*JRAdapter*`; saída PDF via `JasperExportManager`.
- Tabela `reports` na knowledge DB correlaciona `.jrxml` ↔ JRAdapter ↔ entidade.

---

## 9. Formato .pjc (import/export)

- **ZIP** (Apache Commons Compress, `application/zip`) contendo **1 entry XML
  ISO-8859-1**, gerado por `ScanProcessorEngine`/`XMLExpScanProcessor`
  (reflexão sobre `negocio.dominio`). **Não é XStream.**
- Exportador: `Exportador`; importador: `Importador` +
  `XMLImpScanProcessor` (DOM).
- Import: `ApresentadorImportacao.importarZip` → `Utils.unzip` →
  `ServicoDeCalculo.importarCalculo` → `Importador.importar`.

---

## 10. Superfície de automação (decisivo para MCP)

- **API REST de cálculo: NÃO existe.** Operações de cálculo vivem no fluxo
  JSF/Seam.
- Caminhos viáveis:
  1. **Browser automation** (Selenium/HtmlUnit — ambos já empacotados no
     webapp). Respeita toda a semântica oficial (validações, Drools, máquinas).
  2. Invocação interna dos serviços Seam (mesmo classloader/JVM) — exige
     integração Java dedicada; não validada como segura.
- Escrita direta no H2 é tecnicamente possível (schema mapeado), mas **não é
  suportada pelo fluxo da aplicação** e não é segura para automação de cálculo.

Implicações de estado/concorrência:

- `state saving = server` + `numberOfViewsInSession=1` → 1 view lógica por sessão.
- Sessão sem timeout; `ServicoDeCalculo.calculoAberto` é single-user por sessão.
- Porta 9257 fixa; uma instância por vez (detecção por socket).
- O MCP deve reutilizar uma sessão/contexto estável; paralelismo exige
  isolamento de sessão.

---

## 11. Pontos de entrada reais para uma skill (resumo operacional)

1. Iniciar runtime (Health): `http://127.0.0.1:9257/pjecalc`.
2. Login: `/pjecalc/logon.jsf` (usuário/senha; sem id no form).
3. Criar cálculo: `principal.jsf` → link `Criar Novo Cálculo`.
4. Cadastrar processo/contrato: `calculo.jsf` (form `formulario`) → `salvar`.
5. Adicionar verba: outcome `verbaDeCalculo` → `pages/calculo/verba/verba-calculo.xhtml`.
6. Validar: outcome `validacao` → `pages/calculo/validacao.xhtml`.
7. Liquidar: `pages/calculo/liquidacao.jsf` → `dataDeLiquidacao` + `liquidar`.
8. Exportar PJC: `pages/calculo/exportacao.jsf` → `exportar`.
9. Exportar PDF: `pages/calculo/relatorio/relatorio-calculo.jsf` → `imprimirConsolidado`.
10. Auditoria: leitura do H2 (read-only) + comparação Spec ↔ PJC ↔ PDF ↔ H2.

> Regra de ouro de qualquer skill: **o cálculo é executado pelo PJe-Calc
> (`Calculo.liquidar()`), nunca por motor paralelo.** A skill apenas alimenta a
> UI oficial e extrai os artefatos; parâmetros críticos ausentes devem resultar
> em `UNRESOLVED` (fail-closed), nunca em valor inventado.
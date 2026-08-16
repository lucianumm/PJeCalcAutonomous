"""Mapa de seletores do PJe-Calc 2.16.0, derivado do XHTML/JSF real.

Fonte de verdade: `tomcat/webapps/pjecalc/**/*.xhtml` (componentes JSF 1.2 +
RichFaces 3.3.4). Nenhum seletor é inventado: cada entrada reproduz um `id`,
`styleClass` ou `title` literal presente nos arquivos XHTML inspecionados.

Particularidades reais (confirmadas nos fontes):

- O formulário principal de conteúdo usa `id="formulario"` (o próprio XHTML
  referencia `formulario:campo`); no DOM final o id vira `formulario:campo`.
- O formulário de `logon-usuario-senha.xhtml` NÃO tem `id` explícito e o botão
  de login NÃO tem id (apenas `styleClass="bt_logon"`). Por isso o logon usa
  localizadores CSS por sufixo de id e por classe — nunca um prefixo inventado.
- Os botões de página (`liquidar`, `exportar`, `salvar`, etc.) são
  `a4j:commandButton` com `id` explícito, prefixados por `formulario:`.
"""

# Formulário principal (id real, referenciado no próprio XHTML)
FORM_CALCULO = "formulario"

# ---------------------------------------------------------------------------
# Logon (logon-usuario-senha.xhtml)
#   <h:inputText id="usuarioUS" .../>
#   <h:inputSecret id="senhaUS" .../>
#   <h:commandButton action="#{identity.login}" styleClass="bt_logon"/>
# ---------------------------------------------------------------------------
LOGIN_USER_CSS = "input[id$='usuarioUS']"
LOGIN_PASSWORD_CSS = "input[id$='senhaUS']"
LOGIN_SUBMIT_CSS = "input.bt_logon"

# ---------------------------------------------------------------------------
# Principal (pages/principal.xhtml)
#   <h:commandLink action="#{apresentadorCalculo.iniciarComNovo}" title="Criar Novo Cálculo"/>
# ---------------------------------------------------------------------------
PRINCIPAL_NOVO_CALCULO_CSS = "a[title='Criar Novo Cálculo']"
PRINCIPAL_BUSCAR_CALCULO_CSS = "a[title='Buscar Cálculo']"
PRINCIPAL_IMPORTAR_CSS = "a[title='Importar Cálculo']"
IMPORT_FILE_CSS = "input[type='file']"
IMPORT_CONFIRMAR = "formulario:confirmarImportacao"

# ---------------------------------------------------------------------------
# Cálculo: aba "Dados do Processo" (pages/calculo/calculo.xhtml)
# ---------------------------------------------------------------------------
CALC_NUMERO = "formulario:numero"
CALC_DIGITO = "formulario:digito"
CALC_ANO = "formulario:ano"
CALC_JUSTICA = "formulario:justica"
CALC_REGIAO = "formulario:regiao"
CALC_VARA = "formulario:vara"
CALC_VALOR_CAUSA = "formulario:valorDaCausa"
CALC_AUTUADO_EM = "formulario:autuadoEm"
CALC_RECLAMANTE_NOME = "formulario:reclamanteNome"
CALC_RECLAMADO_NOME = "formulario:reclamadoNome"

# ---------------------------------------------------------------------------
# Cálculo: aba "Parâmetros do Cálculo" (mesma página)
# ---------------------------------------------------------------------------
CALC_ESTADO = "formulario:estado"
CALC_MUNICIPIO = "formulario:municipio"
CALC_DATA_ADMISSAO = "formulario:dataAdmissao"
CALC_DATA_DEMISSAO = "formulario:dataDemissao"
CALC_DATA_AJUIZAMENTO = "formulario:dataAjuizamento"
CALC_DATA_INICIO = "formulario:dataInicioCalculo"
CALC_DATA_TERMINO = "formulario:dataTerminoCalculo"
CALC_MAIOR_REMUNERACAO = "formulario:valorMaiorRemuneracao"
CALC_ULTIMA_REMUNERACAO = "formulario:valorUltimaRemuneracao"
CALC_CARGA_HORARIA_PADRAO = "formulario:valorCargaHorariaPadrao"
CALC_REGIME_CONTRATO = "formulario:tipoDaBaseTabelada"
CALC_AVISO_PREVIO = "formulario:apuracaoPrazoDoAvisoPrevio"
CALC_PRESCRICAO_QUINQUENAL = "formulario:prescricaoQuinquenal"
CALC_PRESCRICAO_FGTS = "formulario:prescricaoFgts"
CALC_ZERA_VALOR_NEGATIVO = "formulario:zeraValorNegativo"
CALC_SABADO_DIA_UTIL = "formulario:sabadoDiaUtil"
CALC_CONSIDERA_FERIADO_ESTADUAL = "formulario:consideraFeriadoEstadual"
CALC_CONSIDERA_FERIADO_MUNICIPAL = "formulario:consideraFeriadoMunicipal"
CALC_COMENTARIOS = "formulario:comentarios"

# ---------------------------------------------------------------------------
# Ações (a4j:commandButton) — mesma página
# ---------------------------------------------------------------------------
ACTION_BUSCAR = "formulario:buscar"
ACTION_LIMPAR = "formulario:limpar"
ACTION_SALVAR = "formulario:salvar"
ACTION_CANCELAR = "formulario:cancelar"

# ---------------------------------------------------------------------------
# Liquidação (pages/calculo/liquidacao.xhtml)
#   <rich:calendar id="dataDeLiquidacao" .../>
#   <h:selectOneRadio id="indicesAcumulados" .../>
#   <a4j:commandButton id="liquidar" actionListener="#{apresentador.liquidar}"/>
# ---------------------------------------------------------------------------
LIQUIDACAO_DATA = "formulario:dataDeLiquidacao"
LIQUIDACAO_INDICES = "formulario:indicesAcumulados"
LIQUIDACAO_BUTTON = "formulario:liquidar"

# ---------------------------------------------------------------------------
# Validação (pages/calculo/validacao.xhtml — totais exibidos na liquidação)
# ---------------------------------------------------------------------------
VALIDACAO_TOTAL_ERROS = "formulario:totalErros"
VALIDACAO_TOTAL_ALERTAS = "formulario:totalAlertas"
VALIDACAO_SUCESSO_CSS = ".validacaoSucesso"

# ---------------------------------------------------------------------------
# Exportação PJC (pages/calculo/exportacao.xhtml)
#   <a4j:commandButton id="exportar" actionListener="#{apresentador.exportar}"/>
#   <h:commandLink id="linkDownloadArquivo" actionListener="#{apresentador.downloadArquivo}"/>
# ---------------------------------------------------------------------------
EXPORTACAO_BUTTON = "formulario:exportar"
EXPORTACAO_DOWNLOAD = "formulario:linkDownloadArquivo"

# ---------------------------------------------------------------------------
# Relatório consolidado / PDF (pages/calculo/relatorio/relatorio-calculo.xhtml)
#   <h:selectOneRadio id="formatoSaida" .../>
#   <h:selectManyCheckbox id="tipoDeRelatorio" .../>
#   <a4j:commandButton id="imprimirConsolidado" value="Imprimir" .../>
#   <h:commandLink id="linkDownloadConsolidado" .../>
# ---------------------------------------------------------------------------
RELATORIO_FORMATO = "formulario:formatoSaida"
RELATORIO_TIPOS = "formulario:tipoDeRelatorio"
RELATORIO_IMPRIMIR = "formulario:imprimirConsolidado"
RELATORIO_DOWNLOAD = "formulario:linkDownloadConsolidado"

# ---------------------------------------------------------------------------
# Verba (pages/calculo/verba/verba-calculo.xhtml) — component ids reais
# ---------------------------------------------------------------------------
VERBA_NOVO_CSS = "a[title='Criar Novo Cálculo'], a[title='Incluir Verba'], a[title='Adicionar']"
VERBA_DESCRICAO = "formulario:descricao"
VERBA_ASSUNTO_CNJ = "formulario:assuntosCnj"          # leitura (hidden codigoAssuntosCnj)
VERBA_CODIGO_ASSUNTO_CNJ = "formulario:codigoAssuntosCnj"
VERBA_PERIODO_INICIAL = "formulario:periodoInicial"     # rich:calendar
VERBA_PERIODO_FINAL = "formulario:periodoFinal"         # rich:calendar
VERBA_TIPO_VARIACAO = "formulario:tipoVariacaoDaParcela"
VERBA_TIPO = "formulario:tipoDeVerba"                   # radio (PRINCIPAL/REFLEXO/CALCULADA/INFORMADA)
VERBA_VALOR_TIPO = "formulario:valor"
VERBA_CARACTERISTICA = "formulario:caracteristicaVerba" # radio
VERBA_OCORRENCIA_PAGTO = "formulario:ocorrenciaPagto"   # radio
VERBA_OCORRENCIA_AJUIZAMENTO = "formulario:ocorrenciaAjuizamento"
VERBA_VALOR_INFORMADO = "formulario:valorInformadoDoDevido"
VERBA_DIVISOR_TIPO = "formulario:tipoDeDivisor"         # radio
VERBA_DIVISOR_OUTRO = "formulario:outroValorDoDivisor"
VERBA_MULTIPLICADOR = "formulario:outroValorDoMultiplicador"
VERBA_DOBRA = "formulario:dobraValorDevido"
VERBA_BASE_TABELADA = "formulario:tipoDaBaseTabelada"
VERBA_TIPO_QUANTIDADE = "formulario:tipoDaQuantidade"
VERBA_QUANTIDADE = "formulario:valorInformadoDaQuantidade"
VERBA_GERA_REFLEXO = "formulario:geraReflexo"
VERBA_GERAR_PRINCIPAL = "formulario:gerarPrincipal"
VERBA_SALVAR = "formulario:salvar"


# ---------------------------------------------------------------------------
# Cálculo Externo (pages/calculo/calculo-externo.xhtml) — ids reais
# ---------------------------------------------------------------------------
CALC_EXT_DATA_ULTIMA_ATUALIZACAO = "formulario:dataUltimaAtualizacao"
CALC_EXT_INDICE_TRABALHISTA = "formulario:indiceTrabalhista"
CALC_EXT_JUROS = "formulario:juros"
CALC_EXT_BASE_JUROS_VERBAS = "formulario:baseDeJurosDasVerbas"
CALC_EXT_FGTS_TIPO = "formulario:tipoDeVerba"
CALC_EXT_FGTS_CORRECAO = "formulario:indiceDeCorrecaoDoFGTS"
CALC_EXT_IGNORAR_TAXA_NEGATIVA = "formulario:ignorarTaxaNegativa"
CALC_EXT_LEI_11941 = "formulario:correcaoLei11941"
CALC_EXT_IRPF = "formulario:apurarImpostoRenda"
CALC_EXT_CUSTAS = "formulario:aplicarCorrecaoCustas"
CALC_EXT_INSS_CORRECAO_TRABALHISTA_DEVIDOS = "formulario:correcaoTrabalhistaDosSalariosDevidosDoINSS"
CALC_EXT_INSS_JUROS_TRABALHISTAS_DEVIDOS = "formulario:jurosTrabalhistasDosSalariosDevidosDoINSS"
CALC_EXT_INSS_CORRECAO_PREVIDENCIARIA_DEVIDOS = "formulario:correcaoPrevidenciariaDosSalariosDevidosDoINSS"
CALC_EXT_INSS_JUROS_PREVIDENCIARIOS_DEVIDOS = "formulario:jurosPrevidenciariosDosSalariosDevidosDoINSS"
CALC_EXT_INSS_CORRECAO_LEI_11941_PAGOS = "formulario:correcaoLei11941Pago"
CALC_EXT_INSS_CORRECAO_TRABALHISTA_PAGOS = "formulario:correcaoTrabalhistaDosSalariosPagosDoINSS"
CALC_EXT_INSS_JUROS_TRABALHISTAS_PAGOS = "formulario:jurosTrabalhistasDosSalariosPagosDoINSS"
CALC_EXT_INSS_CORRECAO_PREVIDENCIARIA_PAGOS = "formulario:correcaoPrevidenciariaDosSalariosPagosDoINSS"
CALC_EXT_INSS_JUROS_PREVIDENCIARIOS_PAGOS = "formulario:jurosPrevidenciariosDosSalariosPagosDoINSS"
CALC_EXT_SALVAR = "formulario:salvar"

# ---------------------------------------------------------------------------
# Parcelas Atualizáveis (pages/calculo/parcelas-atualizaveis.xhtml) — ids reais
# ---------------------------------------------------------------------------
PARC_VERBAS_TRIBUTAVEL = "formulario:verbasTributavel"
PARC_VALOR_VERBAS_TRIBUTAVEL = "formulario:valorParcelaCredReclamVerbasTributavel"
PARC_VERBAS_NAO_TRIBUTAVEL = "formulario:verbasNaoTributavel"
PARC_VALOR_VERBAS_NAO_TRIBUTAVEL = "formulario:valorParcelaCredReclamVerbasNaoTributavel"
PARC_FGTS = "formulario:fgts"
PARC_VALOR_FGTS = "formulario:valorParcelaCredReclamFgts"
PARC_MULTA_FGTS = "formulario:multaFgts"
PARC_VALOR_MULTA_FGTS = "formulario:valorParcelaCredReclamMultaFgts"

# Demais grupos da tela `parcelas-atualizaveis.xhtml`. O mapa é usado tanto
# pela validação quanto pela operação; manter os ids aqui evita que um agente
# invente localizadores DOM e permite auditar a versão vendorizada.
PARC_DESCONTO_CONTRIB_SOCIAL_SEGURADO = "formulario:contribSocialSegurado"
PARC_VALOR_DESCONTO_CONTRIB_SOCIAL_SEGURADO = "formulario:valorParcelaDescCredReclamContribSocialSegurado"
PARC_DESCONTO_PREVIDENCIA_PRIVADA = "formulario:previdenciaPrivada"
PARC_VALOR_DESCONTO_PREVIDENCIA_PRIVADA = "formulario:valorParcelaDescCredReclamPrevidenciaPrivada"
PARC_OUTROS_CONTRIB_SOCIAL_SEGURADO = "formulario:contribSocialSeguradoDevidos"
PARC_OUTROS_VALOR_CONTRIB_SOCIAL_SEGURADO = "formulario:valorParcelaContribSocialSeguradoOutrosDeb"
PARC_OUTROS_CONTRIB_SOCIAL_PATRONAL = "formulario:contribSocialPatronalDevidos"
PARC_OUTROS_VALOR_CONTRIB_SOCIAL_PATRONAL = "formulario:valorParcelaContribSocialPatronalOutrosDeb"
PARC_OUTROS_CONTRIB_SOCIAL_10 = "formulario:contribSocial10OutrosDeb"
PARC_OUTROS_VALOR_CONTRIB_SOCIAL_10 = "formulario:valorParcelaOutrosDebContribSocial10"
PARC_OUTROS_CONTRIB_SOCIAL_05 = "formulario:contribSocial05OutrosDeb"
PARC_OUTROS_VALOR_CONTRIB_SOCIAL_05 = "formulario:valorParcelaOutrosDebContribSocial05"
PARC_OUTROS_CUSTAS_CONHECIMENTO = "formulario:custasConhecimentoReclamadoOutrosDeb"
PARC_OUTROS_CUSTAS_LIQUIDACAO = "formulario:custasLiquidacaoOutrosDeb"
PARC_OUTROS_CUSTAS_EXECUCAO = "formulario:custasExecucaoOutrosDeb"
PARC_OUTROS_VALOR_CUSTAS_EXECUCAO = "formulario:valorParcelaOutrosDebCustasExecucao"
PARC_DEBITO_CUSTAS_RECLAMANTE = "formulario:custasReclamanteDebReclam"
PARC_DESCONTO_CUSTAS_RECLAMANTE = "formulario:custasReclamante"


_EXTERNAL_PARCEL_BINDINGS = {
    # group -> key -> {checkbox, values, value_required}
    "creditos_reclamante": {
        "verbasTributavel": {"checkbox": PARC_VERBAS_TRIBUTAVEL, "values": {"principal": PARC_VALOR_VERBAS_TRIBUTAVEL}},
        "verbasNaoTributavel": {"checkbox": PARC_VERBAS_NAO_TRIBUTAVEL, "values": {"principal": PARC_VALOR_VERBAS_NAO_TRIBUTAVEL}},
        "fgts": {"checkbox": PARC_FGTS, "values": {"principal": PARC_VALOR_FGTS}},
        "multaFgts": {"checkbox": PARC_MULTA_FGTS, "values": {"principal": PARC_VALOR_MULTA_FGTS}},
    },
    "descontos_reclamante": {
        "contribSocialSegurado": {"checkbox": PARC_DESCONTO_CONTRIB_SOCIAL_SEGURADO, "values": {"principal": PARC_VALOR_DESCONTO_CONTRIB_SOCIAL_SEGURADO}},
        "previdenciaPrivada": {"checkbox": PARC_DESCONTO_PREVIDENCIA_PRIVADA, "values": {"principal": PARC_VALOR_DESCONTO_PREVIDENCIA_PRIVADA}},
        "custasReclamante": {"checkbox": PARC_DESCONTO_CUSTAS_RECLAMANTE, "values": {}, "value_required": False},
    },
    "outros_debitos_reclamado": {
        "contribSocialSeguradoDevidos": {"checkbox": PARC_OUTROS_CONTRIB_SOCIAL_SEGURADO, "values": {"principal": PARC_OUTROS_VALOR_CONTRIB_SOCIAL_SEGURADO, "ate_fev_2009": "formulario:valorParcelasAteFev2009ContribSocialSeguradoOutrosDeb", "apos_fev_2009": "formulario:valorParcelasAposFev2009ContribSocialSeguradoOutrosDeb"}},
        "contribSocialPatronalDevidos": {"checkbox": PARC_OUTROS_CONTRIB_SOCIAL_PATRONAL, "values": {"principal": PARC_OUTROS_VALOR_CONTRIB_SOCIAL_PATRONAL, "ate_fev_2009": "formulario:valorParcelasAteFev2009ContribSocialPatronalOutrosDeb", "apos_fev_2009": "formulario:valorParcelasAposFev2009ContribSocialPatronalOutrosDeb"}},
        "contribSocial10OutrosDeb": {"checkbox": PARC_OUTROS_CONTRIB_SOCIAL_10, "values": {"principal": PARC_OUTROS_VALOR_CONTRIB_SOCIAL_10}},
        "contribSocial05OutrosDeb": {"checkbox": PARC_OUTROS_CONTRIB_SOCIAL_05, "values": {"principal": PARC_OUTROS_VALOR_CONTRIB_SOCIAL_05}},
        "custasConhecimentoReclamadoOutrosDeb": {"checkbox": PARC_OUTROS_CUSTAS_CONHECIMENTO, "values": {}, "value_required": False},
        "custasLiquidacaoOutrosDeb": {"checkbox": PARC_OUTROS_CUSTAS_LIQUIDACAO, "values": {}, "value_required": False},
        "custasExecucaoOutrosDeb": {"checkbox": PARC_OUTROS_CUSTAS_EXECUCAO, "values": {"principal": PARC_OUTROS_VALOR_CUSTAS_EXECUCAO}},
    },
    "debitos_reclamante": {
        "custasReclamanteDebReclam": {"checkbox": PARC_DEBITO_CUSTAS_RECLAMANTE, "values": {}, "value_required": False},
    },
}

_EXTERNAL_PARCEL_ALIASES = {
    "principal": ("creditos_reclamante", "verbasTributavel"),
    "inss_reclamante": ("descontos_reclamante", "contribSocialSegurado"),
    "inss_segurado_salarios_devidos": ("outros_debitos_reclamado", "contribSocialSeguradoDevidos"),
    "inss_patronal_salarios_devidos": ("outros_debitos_reclamado", "contribSocialPatronalDevidos"),
    "custas_reclamado": ("outros_debitos_reclamado", "custasConhecimentoReclamadoOutrosDeb"),
}


def external_parcel_binding(group: str, key: str) -> dict | None:
    """Retorna binding estático para uma parcela externa.

    Aliases são resolvidos somente para o grupo esperado; uma chave válida em
    outro grupo continua sendo recusada para impedir alteração cruzada.
    """
    group_map = _EXTERNAL_PARCEL_BINDINGS.get(group, {})
    if key in group_map:
        return group_map[key]
    alias = _EXTERNAL_PARCEL_ALIASES.get(key)
    if alias and alias[0] == group:
        return group_map.get(alias[1])
    return None


def canonical_external_parcel_key(group: str, key: str) -> str:
    alias = _EXTERNAL_PARCEL_ALIASES.get(key)
    return alias[1] if alias and alias[0] == group else key


def calendar_input(cid: str) -> str:
    """Retorna o seletor CSS do input editável de um rich:calendar.

    RichFaces 3.3.4 renderiza o input de um <rich:calendar id="X"> com o
    sufixo `InputDate` (`XInputDate`), mesmo com `enableManualInput="true"`.
    """
    return f"input[id$='{cid}InputDate']"


def jsf(cid: str) -> str:
    """Normaliza um id já prefixado para busca por id no DOM."""
    return cid


def jsf_name(cid: str) -> str:
    """JSF renderiza o atributo name igual ao id composto."""
    return cid

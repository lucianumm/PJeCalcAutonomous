"""Constantes derivadas da knowledge base comprovada do PJe-Calc 2.16.0.

Prioridade de confiança (definida na pesquisa):
    RUNTIME REAL > BYTECODE > CFR/DECOMPILAÇÃO > KNOWLEDGE DB > DOCUMENTAÇÃO > HIPÓTESE

Nenhum valor aqui é suposição: todos vêm de `pjecalc-research/knowledge/version.json`,
`src_truth/project.json`, das evidências coletadas por bytecode/forense e do
`server.xml` real empacotado.
"""

# ---------------------------------------------------------------------------
# Produto / versão
# ---------------------------------------------------------------------------
PRODUCT_NAME = "PJe-Calc"
PRODUCT_VERSION = "2.16.0"

# ---------------------------------------------------------------------------
# Componentes de runtime (evidência: knowledge/version.json)
# ---------------------------------------------------------------------------
LAUNCHER_MAIN_CLASS = "br.jus.trt8.pjecalcsa.container.Lancador"
EMBEDDED_TOMCAT = "7.0.67"
BUNDLED_JRE = "1.8.0_241"
H2_VERSION = "1.3.154"
SEAM = "2.2.0.GA"
JSF = "1.2_13"
RICHFACES = "3.3.4"
HIBERNATE = "3.3.2.GA"
DROOLS = "5.5.0.Final"
JASPERREPORTS = "3.7.6"

# ---------------------------------------------------------------------------
# Portas / bind (evidência: tomcat/conf/server.xml real)
# ---------------------------------------------------------------------------
HTTP_PORT = 9257
SHUTDOWN_PORT = 9256
AJP_PORT = 9258
BIND_ADDRESS = "127.0.0.1"  # Nunca 0.0.0.0 por padrão.

# ---------------------------------------------------------------------------
# Banco de dados (evidência: knowledge base + persistence.xml)
# ---------------------------------------------------------------------------
DB_URL = "jdbc:h2:.dados/pjecalc"
DB_USER = "pjecalc"
DB_FILE = "pjecalc.h2.db"  # H2 1.3.x nomeia o arquivo principal assim (sem -mv).
DB_DIR = ".dados"

# ---------------------------------------------------------------------------
# Contexto raiz web
# ---------------------------------------------------------------------------
WEBAPP_CONTEXT = "pjecalc"
HEALTH_PATH = "/pjecalc"
HEALTH_URL = f"http://{BIND_ADDRESS}:{HTTP_PORT}/{WEBAPP_CONTEXT}"

# ---------------------------------------------------------------------------
# JVM (argumentos sem segredos)
# ---------------------------------------------------------------------------
JVM_ARGS = [
    "-Duser.timezone=GMT-3",
    "-Dfile.encoding=ISO-8859-1",
    "-Dconfiguracao.pjecalc.ambiente=DEV",
]


def runtime_jvm_args() -> list[str]:
    """Monta propriedades do runtime a partir do ambiente, nunca do código.

    O PJe-Calc lê token/contexto durante a renderização do login. Em execução
    real ambos devem ser fornecidos por ambiente/secret manager. Placeholders
    só podem ser habilitados explicitamente para fixtures locais.
    """

    import os

    token = os.environ.get("PJECALC_TOKEN_SERVICOS")
    context = os.environ.get("PJECALC_PJE_CONTEXT")
    if not token or not context:
        if os.environ.get("PJECALC_ALLOW_DEV_PLACEHOLDERS") != "1":
            raise RuntimeError(
                "PJECALC_TOKEN_SERVICOS e PJECALC_PJE_CONTEXT são obrigatórios; "
                "use PJECALC_ALLOW_DEV_PLACEHOLDERS=1 somente em fixture local"
            )
        token = token or "dev-placeholder-token"
        context = context or "http://127.0.0.1/pje-seguranca"
    return [
        *JVM_ARGS,
        f"-Dseguranca.pjecalc.tokenServicos={token}",
        f"-Dseguranca.pjekz.servico.contexto={context}",
    ]

# ---------------------------------------------------------------------------
# First-party JARs (bytecode próprio do PJe-Calc 2.16.0)
# ---------------------------------------------------------------------------
FIRST_PARTY_JARS = [
    "pjecalc-base-2.16.0.jar",
    "pjecalc-negocio-2.16.0.jar",
    "pjecalc-integracao-2.16.0.jar",
    "pjecalc.jar",  # launcher
]

# ---------------------------------------------------------------------------
# Motor real (não reimplementar em Python)
# ---------------------------------------------------------------------------
ENGINE_LIQUIDATE = "Calculo.liquidar()"
ENGINE_FORMULA = "MaquinaDeCalculo.calcularValorDevidoDaOcorrencia"

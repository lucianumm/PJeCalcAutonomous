"""BrowserPJeCalcDriver — opera o PJe-Calc via automação de navegador.

O motor de cálculo REAL é o do PJe-Calc (Calculo.liquidar()). O driver apenas
preenche a UI oficial (JSF 1.2 + RichFaces 3.3.4) e clica nos comandos reais,
usando os seletores derivados do XHTML empacotado (`selectors.py`).

Implementação preferencial: Selenium (empíricamente mais compatível com
JSF/RichFaces antigos). A escolha de backend é feita em runtime; a dependência
é importada de forma lazy para não quebrar o boot quando ausente.

Em qualquer falha de automação, o driver salva em `.jobs/<JOB_ID>/logs/browser/`:
    screenshot, DOM, URL, console (quando disponível) e estado do driver.
"""

from __future__ import annotations

import json
import os
import re
import socket
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .constants import HEALTH_URL, WEBAPP_CONTEXT
from . import selectors as sel


class BrowserDriverError(RuntimeError):
    pass


@dataclass
class ArtifactContext:
    """Onde salvar artefatos de diagnóstico em falha."""

    out_dir: Path
    step: str


class BaseBrowserDriver(ABC):
    """Interface abstrata do driver de browser."""

    def __init__(self, base_url: str = HEALTH_URL, headless: bool = True,
                 download_dir: Optional[Path] = None):
        self.base_url = base_url
        self.headless = headless
        self.download_dir = Path(download_dir).resolve() if download_dir else None
        self._driver: Any = None

    # -- lifecycle ----------------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def goto(self, url: str) -> None:
        ...

    @abstractmethod
    def find(self, by: str, value: str) -> Any:
        ...

    def find_all(self, by: str, value: str) -> list[Any]:
        """Localiza todos os elementos sem expor o objeto Selenium ao fluxo."""
        if self._driver is None:
            raise BrowserDriverError("driver não iniciado")
        from selenium.webdriver.common.by import By
        strategy = {
            "id": By.ID,
            "css": By.CSS_SELECTOR,
            "xpath": By.XPATH,
            "name": By.NAME,
            "tag": By.TAG_NAME,
        }.get(by)
        if strategy is None:
            raise BrowserDriverError(f"localizador não suportado: {by}")
        return list(self._driver.find_elements(strategy, value))

    @abstractmethod
    def set_input(self, by: str, value: str, text: str) -> None:
        ...

    @abstractmethod
    def click(self, by: str, value: str) -> None:
        ...

    @abstractmethod
    def page_source(self) -> str:
        ...

    @abstractmethod
    def current_url(self) -> str:
        ...

    @abstractmethod
    def screenshot(self, path: Path) -> None:
        ...

    # -- helpers ------------------------------------------------------------
    def set_by_id(self, cid: str, text: str) -> None:
        self.set_input("id", cid, text)

    def click_by_id(self, cid: str) -> None:
        self.click("id", cid)

    def set_by_css(self, css: str, text: str) -> None:
        self.set_input("css", css, text)

    def click_by_css(self, css: str) -> None:
        self.click("css", css)

    def set_dom_value(self, by: str, value: str, text: str) -> None:
        """Define um campo renderizado pelo JSF e dispara eventos DOM."""
        if self._driver is None:
            raise BrowserDriverError("driver não iniciado")
        element = self.find(by, value)
        self._driver.execute_script(
            "arguments[0].value = arguments[1]; "
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            element, text,
        )

    def upload_file(self, path: Path, selector: str = "input[type='file']") -> None:
        """Envia um arquivo ao ``rich:fileUpload`` oficial."""
        element = self.find("css", selector)
        element.send_keys(str(Path(path).resolve()))

    def is_present(self, by: str, value: str) -> bool:
        try:
            self.find(by, value)
            return True
        except Exception:
            return False

    def get_value(self, by: str, value: str) -> str:
        el = self.find(by, value)
        return el.get_attribute("value") or ""

    def accept_alert_if_present(self) -> bool:
        """Confirma alertas JS usados pelos comandos de exclusão, se houver."""
        if self._driver is None:
            return False
        try:
            self._driver.switch_to.alert.accept()
            return True
        except Exception:
            return False

    def select_radio_by_label(self, component_id: str, label_substring: str) -> None:
        """Marca o radio cujo rótulo contém `label_substring`, dentro do
        componente `component_id` (id JSF sem prefixo de form).

        JSF renderiza cada item do h:selectOneRadio como
        `<input type="radio" name="formulario:<component_id>" .../>` seguido de
        um `<label>`. Localizamos o label pelo texto e clicamos o input que o
        precede via XPath (baseado no `name` real do componente).
        """
        name = f"formulario:{component_id}"
        label_xpath = _xpath_literal(label_substring)
        xpath = (
            f"//input[@type='radio' and @name='{name}']"
            f"/following-sibling::label[contains(., {label_xpath})]"
            f"/preceding-sibling::input[1]"
        )
        # O XPath acima pode variar; fallback: localizar o label e clicar o
        # input de mesmo name imediatamente anterior no DOM.
        try:
            self.find("xpath", xpath).click()
            return
        except Exception:
            pass
        rad_xpath = (
            f"//label[contains(., {label_xpath})]"
            f"/preceding-sibling::input[@type='radio' and @name='{name}'][1]"
        )
        self.find("xpath", rad_xpath).click()

    def wait_for_text(self, text: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text in self.page_source():
                return True
            time.sleep(0.5)
        return False

    def wait_ajax_idle(self, timeout: float = 30.0) -> bool:
        """Aguarda RichFaces/JSF terminar Ajax antes de ler o DOM."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                script = (
                    "return !(window.A4J && A4J.AJAX && A4J.AJAX._requestsQueue "
                    "&& A4J.AJAX._requestsQueue.length) && "
                    "!(window.RichFaces && RichFaces.queue && RichFaces.queue.isEmpty "
                    "&& !RichFaces.queue.isEmpty());"
                )
                if bool(self._driver.execute_script(script)):
                    return True
            except Exception:
                # Alguns estados não expõem a fila; o DOM ainda pode ser
                # confirmado por `wait_jsf_update`.
                return True
            time.sleep(0.2)
        return False

    def wait_jsf_update(self, expected: Optional[tuple[str, str]] = None,
                        timeout: float = 30.0) -> bool:
        if not self.wait_ajax_idle(timeout):
            return False
        if expected is None:
            return True
        return _wait_present(self, expected[0], expected[1], timeout)

    def wait_navigation_or_update(self, previous_url: Optional[str] = None,
                                  expected: Optional[tuple[str, str]] = None,
                                  timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if previous_url and self.current_url() != previous_url:
                return self.wait_ajax_idle(timeout=2)
            if expected and self.is_present(*expected):
                return self.wait_ajax_idle(timeout=2)
            time.sleep(0.2)
        return False

    def get_jsf_messages(self) -> list[str]:
        try:
            source = self.page_source()
        except Exception as exc:
            raise BrowserDriverError("não foi possível ler mensagens JSF") from exc
        # Mensagens são texto de usuário; removemos tags sem depender de parser
        # opcional e preservamos apenas trechos não vazios.
        text = re.sub(r"<[^>]+>", " ", source)
        text = re.sub(r"\s+", " ", text)
        return [m.strip() for m in re.findall(r"(?:erro|error|alerta|warn|sucesso|success)[^<]{0,240}", text, re.I)]

    def wait_file_stable(self, path: Path, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        previous = None
        while time.time() < deadline:
            if path.is_file():
                size = path.stat().st_size
                if size > 0 and size == previous:
                    return True
                previous = size
            time.sleep(0.25)
        return False

    def wait_download(self, suffixes: Iterable[str], timeout: float = 60.0,
                      not_before: Optional[float] = None) -> Optional[Path]:
        if self.download_dir is None:
            raise BrowserDriverError("download_dir não configurado para o job")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        suffixes = tuple(s.lower() for s in suffixes)
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = [
                p for p in self.download_dir.iterdir()
                if p.is_file() and p.suffix.lower() in suffixes
                and not p.name.endswith(('.part', '.tmp'))
                and (not_before is None or p.stat().st_mtime >= not_before)
            ]
            for candidate in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
                if self.wait_file_stable(candidate, timeout=2):
                    return candidate
            time.sleep(0.25)
        return None

    def dump_failure(self, ctx: ArtifactContext) -> dict:
        """Captura artefatos de diagnóstico e retorna um resumo."""
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        summary: dict = {"step": ctx.step}
        try:
            summary["url"] = self.current_url()
        except Exception:
            summary["url"] = None
        try:
            dom = self.page_source()
            dom = _redact_sensitive_dom(dom)[:5 * 1024 * 1024]
            (ctx.out_dir / f"{ctx.step}.dom.html").write_text(
                dom, encoding="utf-8", errors="replace"
            )
            summary["dom_saved"] = True
        except Exception:
            summary["dom_saved"] = False
        try:
            self.screenshot(ctx.out_dir / f"{ctx.step}.screenshot.png")
            summary["screenshot_saved"] = True
        except Exception:
            summary["screenshot_saved"] = False
        (ctx.out_dir / f"{ctx.step}.summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        return summary


def _redact_sensitive_dom(dom: str) -> str:
    """Remove password/token values before persisting a browser diagnostic."""
    dom = re.sub(
        r"(<input\b[^>]*(?:type\s*=\s*['\"]password['\"]|name\s*=\s*['\"][^'\"]*(?:senha|password|token)[^'\"]*['\"])[^>]*\bvalue\s*=\s*)['\"][^'\"]*['\"]",
        r"\1\"[REDACTED]\"",
        dom,
        flags=re.I,
    )
    dom = re.sub(
        r"((?:token|password|senha)[^=]{0,30}=\s*)[^&\"'\s<]+",
        r"\1[REDACTED]",
        dom,
        flags=re.I,
    )
    return dom


class SeleniumBrowserDriver(BaseBrowserDriver):
    """Implementação via Selenium WebDriver."""

    def start(self) -> None:
        try:
            from selenium import webdriver
            from selenium.webdriver.firefox.options import Options
            from selenium.webdriver.firefox.service import Service
        except ImportError as exc:  # pragma: no cover - ambiente sem selenium
            raise BrowserDriverError(
                "Selenium não instalado. Instale com: pip install selenium"
            ) from exc

        opts = Options()
        firefox_bin = _resolve_browser_binary(
            "PJECALC_FIREFOX_BIN",
            (
                "navegador/windows/App/Firefox64/firefox.exe",
                "navegador/windows/App/Firefox/firefox.exe",
            ),
        )
        if firefox_bin:
            firefox_path = Path(firefox_bin)
            if not firefox_path.is_file():
                raise BrowserDriverError(
                    f"PJECALC_FIREFOX_BIN não existe: {firefox_path}"
                )
            opts.binary_location = str(firefox_path)
        if self.headless:
            opts.add_argument("-headless")
        if self.download_dir is not None:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            opts.set_preference("browser.download.folderList", 2)
            opts.set_preference("browser.download.dir", str(self.download_dir))
            opts.set_preference("browser.download.useDownloadDir", True)
            opts.set_preference("browser.helperApps.neverAsk.saveToDisk", ",".join([
                "application/zip", "application/x-zip-compressed", "application/pdf",
                "application/octet-stream", "application/xml", "text/xml",
            ]))
            opts.set_preference("pdfjs.disabled", True)
        geckodriver = _resolve_browser_binary(
            "PJECALC_GECKODRIVER",
            (
                ".tools/geckodriver/geckodriver.exe",
                "geckodriver.exe",
            ),
        )
        service = None
        gecko_log: Path | None = None
        if geckodriver:
            gecko_path = Path(geckodriver)
            if not gecko_path.is_file():
                raise BrowserDriverError(
                    f"PJECALC_GECKODRIVER não existe: {gecko_path}"
                )
            # geckodriver 0.19.x (needed by the bundled Firefox 55) does not
            # accept Selenium's newer ``--port 0`` convention; reserve a
            # concrete loopback port before starting it.
            if self.download_dir is not None:
                gecko_log = self.download_dir.parent / "logs" / "browser" / "geckodriver.log"
                gecko_log.parent.mkdir(parents=True, exist_ok=True)
            service = Service(
                executable_path=str(gecko_path),
                port=_free_local_port(),
                log_output=str(gecko_log) if gecko_log else None,
            )
            # Selenium 4.47 adds a CDP ``--websocket-port`` argument that
            # geckodriver 0.19/0.20 (the only releases supporting Firefox 55)
            # rejects. Keep Selenium current while removing only that
            # incompatible optional argument.
            service_args = list(service.service_args)
            if "--websocket-port" in service_args:
                index = service_args.index("--websocket-port")
                del service_args[index:index + 2]
                service.service_args = service_args
            # Selenium 4 also advertises BiDi by default. The legacy
            # geckodriver rejects ``moz:debuggerAddress``; the PJe-Calc UI
            # uses ordinary WebDriver HTTP commands, so BiDi is unnecessary.
            opts._caps.pop("moz:debuggerAddress", None)
            opts._preferences.pop("remote.active-protocols", None)
        try:
            if service is None:
                self._driver = webdriver.Firefox(options=opts)
            else:
                self._driver = webdriver.Firefox(options=opts, service=service)
        except Exception as exc:
            detail = f"Falha ao iniciar Firefox/geckodriver: {exc}"
            if gecko_log:
                detail += f"; log={gecko_log}"
            raise BrowserDriverError(detail) from exc
    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None

    def goto(self, url: str) -> None:
        self._driver.get(url)

    def find(self, by: str, value: str):
        from selenium.webdriver.common.by import By

        by_map = {
            "id": By.ID,
            "css": By.CSS_SELECTOR,
            "css_selector": By.CSS_SELECTOR,
            "name": By.NAME,
            "xpath": By.XPATH,
        }
        strategy = by_map.get(by.lower())
        if strategy is None:
            strategy = getattr(By, by.upper(), By.ID)
        return self._driver.find_element(strategy, value)

    def set_input(self, by: str, value: str, text: str) -> None:
        el = self.find(by, value)
        el.clear()
        el.send_keys(text)

    def click(self, by: str, value: str) -> None:
        self.find(by, value).click()

    def page_source(self) -> str:
        return self._driver.page_source

    def current_url(self) -> str:
        return self._driver.current_url

    def screenshot(self, path: Path) -> None:
        self._driver.save_screenshot(str(path))


def _resolve_browser_binary(env_name: str, relative_candidates: tuple[str, ...]) -> str | None:
    """Resolve an explicit, bundled, or PATH browser executable."""
    configured = os.environ.get(env_name)
    if configured:
        return str(Path(configured).expanduser().resolve())

    roots: list[Path] = []
    configured_root = os.environ.get("PJECALC_AUTONOMOUS_HOME")
    if configured_root:
        roots.append(Path(configured_root).expanduser().resolve())
    try:
        from .config import project_root
        roots.append(project_root())
    except Exception:
        pass
    module_root = Path(__file__).resolve().parent.parent
    roots.append(module_root)

    seen: set[Path] = set()
    for root in roots:
        for base in (root, root.parent):
            for relative in relative_candidates:
                candidate = (base / relative).resolve()
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate.is_file():
                    return str(candidate)

    names = ("geckodriver", "geckodriver.exe") if "GECKODRIVER" in env_name else ("firefox", "firefox.exe")
    for name in names:
        found = shutil.which(name)
        if found:
            return str(Path(found).resolve())
    return None


def _free_local_port() -> int:
    """Return an available loopback TCP port for a legacy geckodriver."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_driver(backend: str = "selenium", headless: bool = True,
                download_dir: Optional[Path] = None) -> BaseBrowserDriver:
    """Fábrica de driver de browser.

    backend: "selenium" (preferencial). O backend "playwright" pode ser adicionado
    posteriormente; a interface BaseBrowserDriver permanece estável.
    """
    if backend == "selenium":
        return SeleniumBrowserDriver(headless=headless, download_dir=download_dir)
    if backend == "playwright":  # pragma: no cover - ainda não implementado
        raise BrowserDriverError("Backend playwright ainda não implementado.")
    raise BrowserDriverError(f"Backend desconhecido: {backend}")


def login(driver: BaseBrowserDriver) -> bool:
    """Preenche o logon usuário/senha e retorna True se a principal foi alcançada.

    O formulário de logon não possui id explícito e o botão não possui id;
    usamos seletores CSS derivados do XHTML real (sufixo de id + classe).
    Credenciais vêm de variáveis de ambiente (nunca hardcoded em segredo).
    """
    import os

    driver.goto(f"http://127.0.0.1:9257/{WEBAPP_CONTEXT}/logon.jsf")
    user = os.environ.get("PJECALC_USERNAME")
    password = os.environ.get("PJECALC_PASSWORD")
    if not user or not password:
        raise BrowserDriverError(
            "PJECALC_USERNAME e PJECALC_PASSWORD são obrigatórios; "
            "não há credenciais embutidas"
        )
    driver.set_by_css(sel.LOGIN_USER_CSS, user)
    driver.set_by_css(sel.LOGIN_PASSWORD_CSS, password)
    driver.click_by_css(sel.LOGIN_SUBMIT_CSS)
    # Se logado, o logon.xhtml redireciona via JS para pages/principal.jsf.
    return driver.wait_for_text("Criar Novo", timeout=30.0)


def _wait_present(driver: BaseBrowserDriver, by: str, value: str,
                  timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.is_present(by, value):
            return True
        time.sleep(0.2)
    return False


def _xpath_literal(value: str) -> str:
    """Representa texto arbitrário como literal XPath sem injeção."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

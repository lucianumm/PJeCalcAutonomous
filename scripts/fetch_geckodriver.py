"""Baixa o geckodriver compatível com o Firefox legado do instalador.

O instalador PJe-Calc contém Firefox 55.0.2. A matriz oficial do Mozilla
indica geckodriver 0.19.1 para Firefox 55--62; o arquivo é fixado por URL e
SHA-256 para evitar trocar silenciosamente o binário usado pela automação.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import urllib.request
import zipfile
from pathlib import Path


VERSION = "0.19.1"
URL = (
    "https://github.com/mozilla/geckodriver/releases/download/"
    f"v{VERSION}/geckodriver-v{VERSION}-win64.zip"
)
ZIP_SHA256 = "b1c180842aa127686b93b4bf8570790c26a13dcb4c703a073404e0918de42090"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    target_dir = root / ".tools" / "geckodriver"
    target = target_dir / "geckodriver.exe"
    if target.is_file() and not args.force:
        print(f"geckodriver já existe em {target}")
        return 0

    print(f"Baixando geckodriver {VERSION} de {URL}")
    with urllib.request.urlopen(URL, timeout=60) as response:
        archive = response.read()
    digest = _sha256(archive)
    if digest != ZIP_SHA256:
        raise SystemExit(
            f"SHA-256 inesperado para geckodriver: {digest}; "
            f"esperado {ZIP_SHA256}"
        )

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = [name for name in bundle.namelist()
                 if Path(name).name == "geckodriver.exe"]
        if names != ["geckodriver.exe"]:
            raise SystemExit(f"arquivo inesperado no ZIP: {names}")
        payload = bundle.read(names[0])

    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(payload)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(f"geckodriver instalado em {target}")
    print(f"zip_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

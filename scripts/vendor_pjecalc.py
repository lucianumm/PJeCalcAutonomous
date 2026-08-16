"""Vendoring do runtime PJe-Calc 2.16.0 para o repositório.

Copia, a partir de uma instalação oficial, os componentes necessários ao runtime
Linux (bytecode Java — independente de plataforma) para `vendor/pjecalc/2.16.0/`:

    lib/       <- bin/lib/*.jar (Tomcat embutido 7.0.67 + H2 + suporte)
    tomcat/    <- tomcat/conf + tomcat/webapps/pjecalc (webapp + WEB-INF/lib/classes)
    pjecalc.jar<- bin/pjecalc.jar (launcher, referência)
    seed/database/pjecalc.h2.db <- seed limpo (0 cálculos, 0 processos)

NÃO copia: bin/jre (JRE Windows), navegador/ (FirefoxPortable Windows).

Após copiar, grava `runtime-manifest.json` com SHA-256 de todos os arquivos.

Uso:
    python tools/vendor_pjecalc.py <dir_instalacao> [--seed <caminho_h2>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("install_dir", type=Path)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--seed", type=Path, default=None,
                        help="Caminho do pjecalc.h2.db limpo (senão usa db-copy da pesquisa)")
    parser.add_argument("--force", action="store_true",
                        help="Substitui vendor existente após criar backup")
    args = parser.parse_args(argv)

    install = args.install_dir.resolve()
    project = args.project.resolve()
    if not (install / "bin").is_dir() or not (install / "tomcat").is_dir():
        print(f"Instalação PJe-Calc inválida: {install}", file=sys.stderr)
        return 2
    vendor = project / "vendor" / "pjecalc" / "2.16.0"
    if vendor.exists():
        if not args.force:
            print(f"Destino já existe: {vendor}; use --force para backup + substituição", file=sys.stderr)
            return 2
        backup = vendor.with_name(vendor.name + ".backup." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        shutil.move(str(vendor), str(backup))

    # 1. lib
    copy_tree(install / "bin" / "lib", vendor / "lib")

    # 2. tomcat (conf + lib + webapps), sem logs/work temporários
    copy_tree(install / "tomcat" / "conf", vendor / "tomcat" / "conf")
    lib_src = install / "tomcat" / "lib"
    if lib_src.exists():
        copy_tree(lib_src, vendor / "tomcat" / "lib")
    copy_tree(install / "tomcat" / "webapps" / "pjecalc",
              vendor / "tomcat" / "webapps" / "pjecalc")

    # 2b. Garantir META-INF/persistence.xml (o Lancador gera a partir do .tmp
    #     no primeiro boot via PersistenceParse). Aqui reproduzimos o resultado:
    #     usa persistence.xml se já existir; senão copia persistence.xml.tmp.
    meta = vendor / "tomcat" / "webapps" / "pjecalc" / "WEB-INF" / "classes" / "META-INF"
    px = meta / "persistence.xml"
    ptmp = meta / "persistence.xml.tmp"
    if not px.exists() and ptmp.exists():
        shutil.copy2(ptmp, px)

    # 3. launcher jar (referência)
    launcher = install / "bin" / "pjecalc.jar"
    if launcher.exists():
        (vendor).mkdir(parents=True, exist_ok=True)
        shutil.copy2(launcher, vendor / "pjecalc.jar")

    # 4. seed H2 limpo
    seed_dir = vendor / "seed" / "database"
    seed_dir.mkdir(parents=True, exist_ok=True)
    if args.seed:
        seed_src = args.seed.resolve()
    else:
        candidates = [
            install / ".dados" / "pjecalc.h2.db",
            install / "pjecalc-research" / "workspace" / "db-copy" / "pjecalc.h2.db",
        ]
        seed_src = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    if not seed_src.exists():
        print(f"Seed H2 não encontrado: {seed_src}", file=sys.stderr)
        return 1
    if seed_src.stat().st_size == 0:
        print(f"Seed H2 vazio: {seed_src}", file=sys.stderr)
        return 1
    shutil.copy2(seed_src, seed_dir / "pjecalc.h2.db")

    # 5. runtime-manifest.json (SHA-256)
    manifest = {"product": "PJe-Calc", "version": "2.16.0", "files": {}}
    for p in sorted(vendor.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(vendor)).replace("\\", "/")
            if rel == "runtime-manifest.json":
                continue
            manifest["files"][rel] = {
                "sha256": sha256(p),
                "size": p.stat().st_size,
            }

    manifest_path = vendor / "runtime-manifest.json"
    temp = manifest_path.with_name("runtime-manifest.json.tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(manifest_path)
    print(f"Vendoring concluído em {vendor}")
    print(f"Arquivos: {len(manifest['files'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

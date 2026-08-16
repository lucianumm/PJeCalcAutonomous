"""Obtém AuditorProcessual de forma determinística (Windows/POSIX)."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


DEFAULT_REF = "64b53871441c4abcfaa08ad8f414c860aa955651"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--ref",
        default=os.environ.get("AUDITORPROCESSUAL_REF", DEFAULT_REF),
        help="commit/tag fixo; por padrão usa a revisão validada pelo projeto",
    )
    args = parser.parse_args()
    root = args.project.resolve()
    dest = root / "third_party" / "auditor-processual"
    repo = "https://github.com/lucianum7/AuditorProcessual.git"
    ref = args.ref
    if not dest.joinpath(".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", repo, str(dest)], check=True)
    if not ref:
        raise SystemExit("AUDITORPROCESSUAL_REF deve apontar para uma revisão explícita")
    # A pinned SHA may already be present in a filtered clone.  If it is not,
    # fetch that exact ref first; fetching only tags is insufficient when the
    # selected immutable revision is not itself tagged.
    resolved = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0:
        fetched = subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", ref],
            check=False,
        )
        if fetched.returncode != 0:
            subprocess.run(["git", "-C", str(dest), "fetch", "--tags", "origin"], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--detach", ref], check=True)
    revision = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    print(f"AuditorProcessual disponível em {dest}")
    print(f"auditor_commit={revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

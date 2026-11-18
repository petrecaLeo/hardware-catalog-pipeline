"""Linha de comando do pipeline: `.venv/bin/python cli.py <comando>`."""

import argparse
import sys
from pathlib import Path

from pipeline import db
from pipeline.config import DB_FILE
from pipeline.sheets import SETS, load_sheets, sheets_path


def read_sheets(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """O conjunto e as fichas `(id_origem, texto)` de um .jsonl ou de uma pasta de .txt.

    Só os arquivos com gabarito (data/dev e data/holdout) dão 'dev' e 'holdout': é lá que a
    avaliação procura o gabarito pelo id. Todo o resto é 'inbox'.
    """
    if path.is_dir():
        files = sorted(path.glob("*.txt"))
        return "inbox", [(file.name, file.read_text(encoding="utf-8")) for file in files]
    if not (path.is_file() and path.suffix == ".jsonl"):
        sys.exit(f"{path}: não é um arquivo .jsonl nem uma pasta de .txt")
    answer_key_sets = {sheets_path(name).resolve(): name for name in SETS}
    conjunto = answer_key_sets.get(path.resolve(), "inbox")
    return conjunto, [(sheet["id"], sheet["texto"]) for sheet in load_sheets(path)]


def ingest(conn, paths: list[Path]) -> None:
    total = new_total = 0
    for path in paths:
        conjunto, sheets = read_sheets(path)
        new = db.add_sheets(conn, conjunto, sheets)
        print(f"{path} ({conjunto}): {new} novas, {len(sheets) - new} já existiam")
        total += len(sheets)
        new_total += new
    print(f"total: {new_total} novas, {total - new_total} já existiam")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pipeline de entrada de catálogo de hardware.")
    parser.add_argument(
        "--db", type=Path, default=DB_FILE, help="arquivo do banco (padrão: data/catalog.db)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_parser = commands.add_parser("ingest", help="guarda as fichas no banco, sem repetir")
    ingest_parser.add_argument(
        "paths", nargs="+", type=Path, metavar="caminho", help="arquivo .jsonl ou pasta de .txt"
    )

    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        if args.command == "ingest":
            ingest(conn, args.paths)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

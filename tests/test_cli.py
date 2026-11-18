import subprocess
import sys

import cli
from pipeline import db
from pipeline.config import ROOT_DIR
from pipeline.sheets import SETS, load_sheets, sheets_path


def run_ingest(db_file, *paths):
    # De ponta a ponta, como no terminal. O subprocesso herda a chave falsa do conftest.
    result = subprocess.run(
        [sys.executable, "cli.py", "--db", str(db_file), "ingest", *paths],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_ingest_both_sets_then_again(tmp_path):
    db_file = tmp_path / "catalog.db"
    paths = ["data/dev/fichas.jsonl", "data/holdout/fichas.jsonl"]

    assert run_ingest(db_file, *paths) == [
        "data/dev/fichas.jsonl (dev): 53 novas, 0 já existiam",
        "data/holdout/fichas.jsonl (holdout): 30 novas, 0 já existiam",
        "total: 83 novas, 0 já existiam",
    ]
    assert run_ingest(db_file, *paths)[-1] == "total: 0 novas, 83 já existiam"

    # Cada ficha guarda o conjunto e o id do jsonl, que a avaliação usa para achar o gabarito.
    conn = db.connect(db_file)
    rows = conn.execute("SELECT conjunto, id_origem, status FROM fichas")
    stored = {tuple(row) for row in rows}
    expected = {
        (name, sheet["id"], "pendente") for name in SETS for sheet in load_sheets(sheets_path(name))
    }
    assert stored == expected


def test_copied_answer_key_file_is_inbox(tmp_path):
    # Só o próprio data/dev/fichas.jsonl dá 'dev': uma cópia dele não tem gabarito garantido.
    copy = tmp_path / "fichas.jsonl"
    copy.write_text(sheets_path("dev").read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.read_sheets(copy)[0] == "inbox"


def test_ingest_txt_folder(tmp_path, capsys):
    folder = tmp_path / "entrada"
    folder.mkdir()
    files = {
        "fonte.txt": "Fonte 650W 80 Plus Bronze R$ 399,90\n",
        "ssd.txt": "SSD NVMe 1TB PCIe 4.0, 7000 MB/s",
        # O mesmo texto com outros espaços nas pontas é a mesma ficha.
        "fonte_copia.txt": "\n  Fonte 650W 80 Plus Bronze R$ 399,90  ",
        "leia-me.md": "não é ficha",
    }
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    db_file = tmp_path / "catalog.db"

    cli.main(["--db", str(db_file), "ingest", str(folder)])

    assert capsys.readouterr().out.splitlines()[-1] == "total: 2 novas, 1 já existiam"
    rows = db.connect(db_file).execute("SELECT conjunto, id_origem, texto FROM fichas ORDER BY id")
    assert [tuple(row) for row in rows] == [
        ("inbox", "fonte.txt", "Fonte 650W 80 Plus Bronze R$ 399,90"),
        ("inbox", "ssd.txt", "SSD NVMe 1TB PCIe 4.0, 7000 MB/s"),
    ]

from pipeline import db


def test_connect_creates_the_tables(tmp_path):
    path = tmp_path / "pasta_nova" / "catalog.db"
    db.connect(path).close()
    conn = db.connect(path)  # abrir de novo não recria nem falha
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row["name"] for row in rows}
    assert tables == {"fichas", "rodadas", "tentativas", "produtos", "revisoes", "lotes"}
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

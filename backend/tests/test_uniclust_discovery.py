"""Riconoscimento dei database UniClust30/UniRef30 nel worker QMEAN (docker/qmean/uniclust.py)."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "docker" / "qmean" / "uniclust.py"
pytestmark = pytest.mark.skipif(not MODULE.exists(), reason="sorgenti del worker QMEAN non presenti")


@pytest.fixture
def uc(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("uniclust_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    src = tmp_path / "disco"
    src.mkdir()
    monkeypatch.setattr(module, "SOURCES", [str(src)])
    monkeypatch.setattr(module, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(module, "VIEWS", str(tmp_path / "views"))
    module.src = src
    return module


def ffindex(data: Path, index: Path, entries=("a", "b", "c"), truncate=0):
    """Coppia ffdata/ffindex coerente (o con il file dati troncato)."""
    blob, lines, offset = b"", [], 0
    for name in entries:
        chunk = (f">{name}\nMKTAYIAKQR\n").encode() + b"\0"
        lines.append(f"{name}\t{offset}\t{len(chunk)}")
        blob += chunk
        offset += len(chunk)
    data.write_bytes(blob[:len(blob) - truncate] if truncate else blob)
    index.write_text("\n".join(lines) + "\n")


def hh3(folder: Path, name: str, hhm=True):
    folder.mkdir(parents=True, exist_ok=True)
    kinds = ("a3m", "cs219", "hhm") if hhm else ("a3m", "cs219")
    for kind in kinds:
        ffindex(folder / f"{name}_{kind}.ffdata", folder / f"{name}_{kind}.ffindex")


def hh2(folder: Path, name: str):
    folder.mkdir(parents=True, exist_ok=True)
    for kind in ("a3m", "hhm"):
        ffindex(folder / f"{name}_{kind}_db", folder / f"{name}_{kind}_db.index")
    (folder / f"{name}.cs219").write_bytes(b"vecchio formato")
    (folder / f"{name}.cs219.sizes").write_text("3 30\n")


def by_name(result):
    return {d["name"]: d for d in result["databases"]}


def test_every_known_layout_is_recognised(uc):
    hh3(uc.src / "UniRef30_2023_02", "UniRef30_2023_02")
    hh3(uc.src / "UniRef30_2022_02", "UniRef30_2022_02", hhm=False)
    hh2(uc.src / "vecchi" / "uniclust30_2016_03", "uniclust30_2016_03")
    both = uc.src / "uniclust30_2018_08"
    hh3(both, "uniclust30_2018_08")
    hh2(both, "uniclust30_2018_08")  # la release 2018_08 contiene entrambi i formati
    dbs = by_name(uc.discover())
    assert set(dbs) == {"UniRef30_2023_02", "UniRef30_2022_02", "uniclust30_2016_03", "uniclust30_2018_08"}
    new = dbs["UniRef30_2023_02"]
    assert (new["state"], new["version"], new["family"], new["hhm"], new["format"]) == \
        ("ready", "2023-02", "UniRef30", True, "HH-suite 3")
    assert dbs["UniRef30_2022_02"]["state"] == "ready" and dbs["UniRef30_2022_02"]["hhm"] is False
    old = dbs["uniclust30_2016_03"]
    assert (old["state"], old["format"], old["legacy_cs219"], old["family"]) == \
        ("needs_conversion", "HH-suite 2", True, "UniClust30")
    assert dbs["uniclust30_2018_08"]["state"] == "ready" and dbs["uniclust30_2018_08"]["format"] == "HH-suite 3"
    assert "a3m" not in uc.public(new) and "dir" not in uc.public(new)  # niente percorsi interni nell'API


def test_choice_prefers_request_then_newest_ready(uc):
    hh3(uc.src / "a", "uniclust30_2018_08")
    hh3(uc.src / "b", "UniRef30_2023_02")
    hh2(uc.src / "c", "uniclust30_2017_10")
    dbs = uc.discover()["databases"]
    assert uc.choose(dbs)["name"] == "UniRef30_2023_02"
    assert uc.choose(dbs, "a/uniclust30_2018_08")["name"] == "uniclust30_2018_08"
    assert uc.choose(dbs, "uniclust30_2018_08")["name"] == "uniclust30_2018_08"  # anche per nome
    assert uc.choose(dbs, "c/uniclust30_2017_10") is None  # scelta ma da convertire: nessun ripiego silenzioso


def test_truncated_copy_archives_and_mmseqs_format(uc):
    folder = uc.src / "rotto"
    hh3(folder, "UniRef30_2021_03")
    ffindex(folder / "UniRef30_2021_03_a3m.ffdata", folder / "UniRef30_2021_03_a3m.ffindex", truncate=5)
    (uc.src / "UniRef30_2023_02_hhsuite.tar.gz").write_bytes(b"x" * 10)
    (uc.src / "uniref30_2302_db.dbtype").write_bytes(b"\0")
    result = uc.discover()
    broken = by_name(result)["UniRef30_2021_03"]
    assert broken["state"] == "incomplete" and "incompleto" in broken["problems"][0]
    assert result["archives"][0]["file"] == "UniRef30_2023_02_hhsuite.tar.gz"
    assert "MMseqs2" in result["unsupported"][0]["reason"]
    assert uc.choose(result["databases"]) is None


def test_converted_cs219_is_used_only_while_the_database_is_unchanged(uc):
    hh2(uc.src / "old", "uniclust30_2016_03")
    db = by_name(uc.discover())["uniclust30_2016_03"]
    plan = uc.conversion_plan(db)
    assert plan["command"][0] == "cstranslate" and plan["command"][plan["command"].index("-I") + 1] == "a3m"
    target = Path(plan["target_dir"])
    target.mkdir(parents=True)
    ffindex(target / "uniclust30_2016_03_cs219.ffdata", target / "uniclust30_2016_03_cs219.ffindex")
    (target / "stamp.json").write_text(json.dumps(plan["stamp"]))
    ready = by_name(uc.discover())["uniclust30_2016_03"]
    assert ready["state"] == "ready" and ready["cs219_source"] == "converted"
    index = uc.src / "old" / "uniclust30_2016_03_a3m_db.index"
    index.write_text(index.read_text() + "d\t0\t5\n")  # il database sul disco è cambiato
    assert by_name(uc.discover())["uniclust30_2016_03"]["state"] == "needs_conversion"


def test_depth_limit_and_hidden_folders(uc):
    hh3(uc.src / "1" / "2" / "3" / "4" / "5", "UniRef30_2020_06")  # troppo in profondità
    hh3(uc.src / ".Trashes", "UniRef30_2020_03")
    hh3(uc.src / "db", "UniRef30_2020_02")
    assert set(by_name(uc.discover())) == {"UniRef30_2020_02"}


@pytest.mark.skipif(os.name != "posix", reason="collegamenti simbolici")
def test_view_uses_standard_names_for_any_layout(uc):
    both = uc.src / "uniclust30_2016_09"
    hh2(both, "uniclust30_2016_09")
    ffindex(both / "uniclust30_2016_09_cs219.ffdata", both / "uniclust30_2016_09_cs219.ffindex")
    db = by_name(uc.discover())["uniclust30_2016_09"]
    prefix = uc.prepare_view(db)
    for kind in ("a3m", "cs219", "hhm"):
        assert os.path.realpath(prefix + f"_{kind}.ffindex").startswith(str(both))


def test_filesystem_of_mounts(uc, tmp_path, monkeypatch):
    mounts = tmp_path / "mounts"
    mounts.write_text("overlay / overlay rw 0 0\n"
                      r"E:\ /uniclust_src 9p ro,aname=drvfs 0 0" "\n"
                      r"/dev/sdb1 /mnt/my\040db ext4 rw 0 0" "\n")
    monkeypatch.setattr(uc, "MOUNTS", str(mounts))
    assert uc.filesystem("/uniclust_src/UniRef30_2020_06/UniRef30_2020_06") == "9p"
    assert uc.filesystem("/mnt/my db/x") == "ext4"
    assert uc.filesystem("/uniclust_srcX/y") == "overlay"  # prefisso senza separatore: non è quel mount
    monkeypatch.setattr(uc, "MOUNTS", str(tmp_path / "assente"))
    assert uc.filesystem("/uniclust_src") is None


def test_local_copy_is_used_only_while_it_matches_the_disk(uc):
    hh3(uc.src / "db", "UniRef30_2020_06")
    db = by_name(uc.discover())["UniRef30_2020_06"]
    names = {f["name"] for f in uc.local_plan(db)}
    assert names == {"UniRef30_2020_06_a3m.ffindex", "UniRef30_2020_06_hhm.ffindex",
                     "UniRef30_2020_06_cs219.ffindex", "UniRef30_2020_06_cs219.ffdata"}  # gli ffdata grandi restano sul disco
    assert db["local"]["state"] == "none" and db["local"]["size"] > 0
    assert "local_files" not in uc.public(db)

    seen = []
    state = uc.copy_local(db, report=lambda done, total, name: seen.append(name))
    assert state["state"] == "ready" and set(seen) == names
    db = by_name(uc.discover())["UniRef30_2020_06"]
    assert db["local"] == {"state": "ready", "size": state["size"], "used": state["size"]}
    if os.name == "posix":
        prefix = uc.prepare_view(db)
        assert os.path.realpath(prefix + "_cs219.ffdata").startswith(uc.CACHE)
        assert os.path.realpath(prefix + "_a3m.ffdata").startswith(str(uc.src))

    index = uc.src / "db" / "UniRef30_2020_06_a3m.ffindex"
    index.write_text(index.read_text() + "d\t0\t5\n")  # database aggiornato sul disco
    db = by_name(uc.discover())["UniRef30_2020_06"]
    assert db["local"]["state"] == "stale"
    assert "UniRef30_2020_06_a3m.ffindex" not in db["local_files"]  # l'indice vecchio non si usa più
    assert "UniRef30_2020_06_cs219.ffdata" in db["local_files"]
    seen.clear()
    assert uc.copy_local(db, report=lambda done, total, name: seen.append(name))["state"] == "ready"
    assert set(seen) == {"UniRef30_2020_06_a3m.ffindex"}  # ricopia solo ciò che è cambiato

    assert uc.remove_local(db["id"]) > 0
    assert by_name(uc.discover())["UniRef30_2020_06"]["local"]["state"] == "none"


def test_local_copy_cancel_space_and_orphans(uc, monkeypatch):
    hh3(uc.src / "db", "UniRef30_2022_02")
    db = by_name(uc.discover())["UniRef30_2022_02"]
    with pytest.raises(uc.Cancelled):
        uc.copy_local(db, cancelled=lambda: True)
    folder = Path(uc.CACHE) / uc.safe_id(db["id"]) / "local"
    assert not list(folder.glob("*.part")) and by_name(uc.discover())["UniRef30_2022_02"]["local"]["state"] == "none"

    with monkeypatch.context() as m:  # solo qui: undo() annullerebbe anche le cartelle della fixture
        m.setattr(uc.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1024})())
        with pytest.raises(RuntimeError, match="spazio insufficiente"):
            uc.copy_local(db)

    uc.copy_local(db)
    for f in (uc.src / "db").iterdir():
        f.unlink()  # disco con un'altra versione: la copia resta orfana
    assert uc.discover()["databases"] == []
    assert [c["id"] for c in uc.local_copies()] == [uc.safe_id(db["id"])]
    assert uc.safe_id("..") == "db" and uc.remove_local("..") == 0
    assert uc.remove_local(uc.safe_id(db["id"])) > 0 and uc.local_copies() == []
    assert not (Path(uc.CACHE) / uc.safe_id(db["id"])).exists()  # niente cartelle vuote nel volume


def test_memory_estimate_from_the_indexes(uc, tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        5954160 kB\nMemFree: 1 kB\nSwapTotal:       2097152 kB\n")
    limit = tmp_path / "memory.max"
    limit.write_text("max\n")
    monkeypatch.setattr(uc, "MEMINFO", str(meminfo))
    monkeypatch.setattr(uc, "CGROUP_LIMITS", (str(limit),))
    mem = uc.memory()
    assert mem == {"total": 5954160 * 1024, "swap": 2097152 * 1024}
    limit.write_text(str(4 * 1024 ** 3))  # limite del container più basso della memoria della macchina
    assert uc.memory()["total"] == 4 * 1024 ** 3

    hh3(uc.src / "db", "UniRef30_2020_06")
    db = by_name(uc.discover())["UniRef30_2020_06"]
    assert uc.memory_fit(db, None) is None
    assert uc.memory_fit(db, mem) == "ok" and uc.memory_message(db, mem) is None
    monkeypatch.setattr(uc, "HHBLITS_OVERHEAD", 5.5 * 1024 ** 3)  # come un database con indici da ~1,4 GB
    assert uc.memory_fit(db, mem) == "swap" and "swap" in uc.memory_message(db, mem)
    monkeypatch.setattr(uc, "HHBLITS_OVERHEAD", 9 * 1024 ** 3)
    assert uc.memory_fit(db, mem) == "insufficient" and "insufficiente" in uc.memory_message(db, mem)


def test_worker_uses_one_thread_when_memory_is_tight(monkeypatch):
    folder = str(MODULE.parent)
    monkeypatch.syspath_prepend(folder)
    spec = importlib.util.spec_from_file_location("qmean_worker_under_test", MODULE.parent / "qmean_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "CPU_ALL", 4)
    monkeypatch.setattr(worker, "CPU_FIXED", 0)
    assert worker.hhblits_cpu("ok") == 4 and worker.hhblits_cpu(None) == 4
    assert worker.hhblits_cpu("swap") == 1  # con 4 thread il cs219 veniva riletto di continuo
    monkeypatch.setattr(worker, "CPU_FIXED", 2)
    assert worker.hhblits_cpu("swap") == 2  # QMEAN_HHBLITS_CPU vince sempre

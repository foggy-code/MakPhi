"""Grafici PostScript di PROCHECK conservati secondo l'impostazione `procheck_plots` (tutti, solo il Ramachandran
complessivo, nessuno), con le stesse metriche in tutti e tre i casi; impostazione salvata e validata dall'API."""

from __future__ import annotations

import pytest

from makphi.config import UserSettings
from makphi.core.db import Database
from makphi.core.jobs import JobManager
from makphi.engines import procheck, tools

from .conftest import DATA
from .test_parallel_quality import run_job

TITLES = {"model_01.ps": "Ramachandran plot, page  1", "model_02.ps": "All-residue Ramachandran plots, page  1",
          "model_03.ps": "All-residue chi1-chi2 plots, page  1", "model_10.ps": "Distorted geometry, page  1"}


def _plot(path, title):
    path.write_text(f"%!PS-Adobe-3.0\n%%Creator: PROCHECK\n%%Title: {title}\n%%Pages: 1\nshowpage\n", encoding="latin-1")
    return path


@pytest.mark.parametrize("choice,kept", [("all", set(TITLES)), ("ramachandran", {"model_01.ps"}), ("none", set())])
def test_plots_kept_by_choice(tmp_path, choice, kept):
    assert {name for name, title in TITLES.items()
            if procheck._keep_plot(_plot(tmp_path / name, title), choice)} == kept


@pytest.fixture
def manager_with(tmp_path):
    made = []

    def make(choice: str) -> JobManager:
        db = Database(tmp_path / f"{choice}.db")
        mgr = JobManager(db, tmp_path / choice / "jobs", 2, lambda: UserSettings(procheck_plots=choice),
                         scratch_dir=tmp_path / choice / "scratch")
        made.append((mgr, db))
        return mgr

    yield make
    for mgr, db in made:
        mgr.shutdown()
        db.close()


@pytest.mark.skipif(not tools.find("procheck"), reason="PROCHECK non installato")
def test_real_procheck_keeps_the_chosen_plots_with_the_same_metrics(manager_with):
    results = {}
    for choice in ("all", "ramachandran", "none"):
        mgr = manager_with(choice)
        job = run_job(mgr, lambda ctx: procheck.run(DATA / "1UBQ.pdb", ctx, ctx.workdir / "procheck"))
        assert job["status"] == "completed", job["error"]
        results[choice] = job["result"]
    plots = {c: [f for f in r["files"] if f.endswith(".ps")] for c, r in results.items()}
    assert len(plots["all"]) >= 9 and plots["ramachandran"] == ["model_01.ps"] and plots["none"] == []
    assert all("model.sum" in r["files"] for r in results.values())
    assert [r["plots"] for r in results.values()] == ["all", "ramachandran", "none"]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("files", "plots")}  # noqa: E731
    assert strip(results["all"]) == strip(results["ramachandran"]) == strip(results["none"])
    assert results["none"]["ramachandran"]["core"] == 95.5  # come tests/data/procheck_1ubq.sum


def test_setting_is_saved_and_validated(client):
    assert client.get("/api/settings").json()["procheck_plots"] == "all"
    r = client.put("/api/settings", json={"procheck_plots": "ramachandran"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["procheck_plots"] == "ramachandran"
    r = client.put("/api/settings", json={"procheck_plots": "qualcuno"})
    assert r.status_code == 400 and "Grafici PostScript di PROCHECK" in r.json()["error"]["message"]

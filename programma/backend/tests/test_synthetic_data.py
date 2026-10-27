"""Le strutture sintetiche di tests/data (generate da make_synthetic.py) restano valide."""

from pathlib import Path

import numpy as np

from makphi.domain import structure

DATA = Path(__file__).parent / "data"


def _model(name: str):
    return structure.first_model(structure.load_structure(DATA / name))


def test_dimer_has_two_identical_chains_in_contact():
    model = _model("ubq_dimer.pdb")
    chains = structure.chain_summaries(model)
    assert [c["chain"] for c in chains] == ["A", "B"]
    assert chains[0]["sequence"] == chains[1]["sequence"] and chains[0]["residues"] == 76
    a = np.array([at.coord for at in model["A"].get_atoms()])
    b = np.array([at.coord for at in model["B"].get_atoms()])
    dist = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    assert 3.0 <= dist.min() < 4.0  # contatto senza scontri
    assert (dist < 4.5).sum() >= 10


def test_zinc_site_is_a_ligand_coordinated_by_his68():
    model = _model("ubq_zn.pdb")
    groups = structure.hetero_groups(model)
    assert [(g["resname"], g["chain"], g["resnum"]) for g in groups] == [("ZN", "A", "101")]
    zn = next(at for at in model.get_atoms() if at.element == "ZN")
    ne2 = model["A"][68]["NE2"]
    assert abs(float(np.linalg.norm(zn.coord - ne2.coord)) - 2.1) < 0.01

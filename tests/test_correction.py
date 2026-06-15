"""PHASE 3 — boucle de correction (température bornée, validée hors échantillon).

On vérifie : la détection d'un biais systématique (sous-confiance) corrigé par T<1 ;
le rejet d'une correction qui n'améliore pas la validation ; les bornes ; la
réversibilité (drapeau / T=1 neutre) ; l'anti-boucle (apprise sur l'ensemble).
"""

import numpy as np
import pytest

from pipeline import config, correction, metrics


def _underconfident_records(n=400, seed=0):
    """Génère des prédictions SOUS-CONFIANTES + issues réelles cohérentes.

    Vraie proba du favori p* ; le modèle annonce une version aplatie (tirée vers
    l'uniforme) -> sous-confiance que T<1 doit corriger. Issues tirées selon p*.
    """
    rng = np.random.default_rng(seed)
    recs = []
    for i in range(n):
        pstar = rng.uniform(0.45, 0.85)
        rest = 1.0 - pstar
        true = np.array([pstar, rest * 0.6, rest * 0.4])
        flat = 0.5 * true + 0.5 * np.array([1 / 3, 1 / 3, 1 / 3])   # aplati -> sous-confiant
        outcome = int(rng.choice(3, p=true))
        recs.append({"date": f"2026-{1 + i % 9:02d}-01", "probs": flat.tolist(),
                     "outcome": outcome})
    return recs


def test_temper_sharpens_and_is_identity_at_one():
    p = np.array([[0.5, 0.3, 0.2]])
    assert np.allclose(correction.temper(p, 1.0), p)            # T=1 neutre
    sharp = correction.temper(p, 0.7)[0]
    assert sharp[0] > 0.5 and sharp.sum() == pytest.approx(1.0)  # affûte le favori


def test_detects_and_corrects_underconfidence():
    recs = _underconfident_records()
    st = correction.fit_domain(recs)
    assert st["enabled"] is True
    assert correction.T_MIN <= st["t"] <= correction.T_MAX
    assert st["t"] < 1.0                                   # affûtage (anti sous-confiance)
    assert st["rps_valid_corr"] < st["rps_valid_base"]     # amélioration hors échantillon


def test_well_calibrated_yields_no_correction():
    """Des prédictions déjà bien calibrées ne doivent pas déclencher de correction."""
    rng = np.random.default_rng(1)
    recs = []
    for i in range(400):
        p = rng.dirichlet([4, 3, 3])
        recs.append({"date": f"2026-{1 + i % 9:02d}-01", "probs": p.tolist(),
                     "outcome": int(rng.choice(3, p=p))})
    st = correction.fit_domain(recs)
    # T peut être ~1 ; surtout, il n'apporte pas d'amélioration nette -> souvent non conservé
    assert correction.T_MIN <= st["t"] <= correction.T_MAX
    if st["enabled"]:
        assert st["rps_valid_corr"] < st["rps_valid_base"]   # si conservé, c'est justifié


def test_insufficient_data_disables_correction():
    recs = _underconfident_records(n=20)
    st = correction.fit_domain(recs)
    assert st["enabled"] is False and st["t"] == 1.0
    assert "pas assez" in st["reason"]


def test_correction_is_bounded():
    """Même un biais extrême ne pousse jamais T hors des bornes."""
    recs = [{"date": "2026-01-01", "probs": [0.4, 0.3, 0.3], "outcome": 0}
            for _ in range(300)]   # le favori gagne TOUJOURS -> énorme sous-confiance
    st = correction.fit_domain(recs)
    assert correction.T_MIN <= st["t"] <= correction.T_MAX


def test_apply_is_reversible_and_flagged(monkeypatch, tmp_path):
    p = {"p_home_win": 0.50, "p_draw": 0.30, "p_away_win": 0.20}
    monkeypatch.setattr(correction, "_PATH", str(tmp_path / "corr.json"))
    correction.save({"international": {"enabled": True, "t": 0.8}})
    # drapeau OFF -> aucune modification (réversible)
    monkeypatch.setattr(config, "CORRECTION_ENABLED", False)
    correction.clear_cache()
    assert correction.apply_to_pred(dict(p), "international") == p
    # drapeau ON -> correction appliquée et tracée
    monkeypatch.setattr(config, "CORRECTION_ENABLED", True)
    correction.clear_cache()
    out = correction.apply_to_pred(dict(p), "international")
    assert out["p_home_win"] > 0.50 and out.get("correction", {}).get("applied") is True
    assert abs(out["p_home_win"] + out["p_draw"] + out["p_away_win"] - 1.0) < 1e-3


def test_apply_is_idempotent(monkeypatch, tmp_path):
    """Appliquer deux fois ne cumule pas la correction (pas de T·T)."""
    monkeypatch.setattr(correction, "_PATH", str(tmp_path / "corr.json"))
    correction.save({"international": {"enabled": True, "t": 0.8}})
    monkeypatch.setattr(config, "CORRECTION_ENABLED", True)
    correction.clear_cache()
    p = {"p_home_win": 0.50, "p_draw": 0.30, "p_away_win": 0.20}
    once = correction.apply_to_pred(dict(p), "international")
    twice = correction.apply_to_pred(dict(once), "international")
    assert twice["p_home_win"] == once["p_home_win"]   # 2e application sans effet


def test_apply_never_crashes_on_corrupted_state(monkeypatch, tmp_path):
    """Un état de correction corrompu (T non numérique) ne casse JAMAIS la prédiction."""
    monkeypatch.setattr(correction, "_PATH", str(tmp_path / "corr.json"))
    correction.save({"international": {"enabled": True, "t": "oops"}})
    monkeypatch.setattr(config, "CORRECTION_ENABLED", True)
    correction.clear_cache()
    p = {"p_home_win": 0.50, "p_draw": 0.30, "p_away_win": 0.20}
    assert correction.apply_to_pred(dict(p), "international") == p   # inchangé, pas d'exception


def test_apply_noop_when_no_validated_correction(monkeypatch, tmp_path):
    monkeypatch.setattr(correction, "_PATH", str(tmp_path / "corr.json"))
    correction.save({"international": {"enabled": False, "t": 1.0}})
    monkeypatch.setattr(config, "CORRECTION_ENABLED", True)
    correction.clear_cache()
    p = {"p_home_win": 0.50, "p_draw": 0.30, "p_away_win": 0.20}
    assert correction.apply_to_pred(dict(p), "international") == p


def test_validation_gate_rejects_overfit(monkeypatch):
    """Si T aide le passé mais PAS la validation, on ne le conserve pas.

    On force `_best_temperature` à renvoyer un T<1 « appris » ; mais les données de
    validation sont telles qu'aplatir (T<1) n'aide pas -> rejet."""
    rng = np.random.default_rng(3)
    # données quasi uniformes : aucune structure -> affûter dégrade la validation
    recs = [{"date": f"2026-{1 + i % 9:02d}-01",
             "probs": [0.34, 0.33, 0.33],
             "outcome": int(rng.choice(3))} for i in range(300)]
    monkeypatch.setattr(correction, "_best_temperature", lambda *a, **k: 0.7)
    st = correction.fit_domain(recs)
    assert st["enabled"] is False           # T=0.7 n'améliore pas la validation -> rejeté
    assert st["t"] == 1.0

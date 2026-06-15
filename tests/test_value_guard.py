"""PHASE C — garde-fou anti « fausse value » (aucun réseau, aucun modèle chargé).

On vérifie que :
  - une value énorme sur un OUTSIDER où le modèle est en fort désaccord avec le
    marché est marquée « peu fiable » (value mais NON value_reliable) ;
  - une value saine sur un quasi-favori reste fiable ;
  - les probabilités de marché sont bien DÉVIGÉES avant comparaison.
"""

from pipeline import config, service


def test_reliability_flags_intl_outsider_disagreement():
    fair_outsider = 0.12
    model_optimistic = 0.30                      # >> 0.12 × ratio
    rel, reason = service._value_reliability(config.DOMAIN_INTL, model_optimistic, fair_outsider)
    assert rel == "low" and reason and "outsider" in reason


def test_reliability_ok_for_reasonable_favorite():
    rel, reason = service._value_reliability(config.DOMAIN_CLUB, 0.55, 0.50)
    assert rel == "ok" and reason is None


def test_reliability_ok_when_no_market():
    assert service._value_reliability(config.DOMAIN_INTL, 0.4, None) == ("ok", None)


def test_intl_stricter_than_club():
    """Mêmes chiffres : un cas limite est signalé en sélections, pas en clubs."""
    model_p, fair = 0.46, 0.34
    assert service._value_reliability(config.DOMAIN_INTL, model_p, fair)[0] == "low"
    assert service._value_reliability(config.DOMAIN_CLUB, model_p, fair)[0] == "ok"


def test_fair_market_probs_are_devigged():
    best = {"h2h": {"home": {"odds": 1.5}, "draw": {"odds": 4.0}, "away": {"odds": 7.0}},
            "totals": {}}
    fair = service._fair_market_probs(best)
    assert abs(sum(fair.values()) - 1.0) < 1e-6          # marge retirée -> somme = 1
    assert fair["home"] > fair["draw"] > fair["away"]    # ordre préservé
    assert fair["home"] < 1.0 / 1.5                       # dévigé < proba brute


def test_build_rows_flags_false_value_on_outsider():
    best = {
        "h2h": {"home": {"odds": 1.5, "book": "B"},
                "draw": {"odds": 4.0, "book": "B"},
                "away": {"odds": 7.0, "book": "B"}},
        "totals": {}, "n_books": 6,
    }
    # le modèle voit l'outsider (away) à 30 % alors que le marché le voit ~13 %
    model_probs = {"home": 0.50, "draw": 0.20, "away": 0.30, "over": None, "under": None}
    rows = service._build_rows(best, model_probs, "A", "B", domain=config.DOMAIN_INTL)
    away = next(r for r in rows if r["selection"] == "away")
    assert away["value"] is True                 # l'edge brut est énorme
    assert away["value_reliable"] is False        # …mais marqué peu fiable
    assert away["reliability"] == "low"
    assert away["reliability_note"]


def test_build_rows_keeps_sane_value_reliable():
    best = {
        "h2h": {"home": {"odds": 2.4, "book": "B"},
                "draw": {"odds": 3.4, "book": "B"},
                "away": {"odds": 3.0, "book": "B"}},
        "totals": {}, "n_books": 6,
    }
    # favori modéré : le modèle voit home à 50 % (marché ~41 %), edge raisonnable
    model_probs = {"home": 0.50, "draw": 0.25, "away": 0.25, "over": None, "under": None}
    rows = service._build_rows(best, model_probs, "A", "B", domain=config.DOMAIN_CLUB)
    home = next(r for r in rows if r["selection"] == "home")
    assert home["value"] is True and home["value_reliable"] is True
    assert home["reliability"] == "ok"

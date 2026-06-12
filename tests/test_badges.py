"""Tests Étape 1 : écussons d'équipe (drapeaux des sélections, pastilles des clubs).

Tout est pur et hors ligne. On vérifie :
  - le mapping nom → code ISO (exact, normalisé/accents, alias, sous-codes UK) ;
  - les emojis drapeaux (alpha-2 → indicateurs régionaux ; sous-codes UK) ;
  - les initiales propres (mots de liaison ignorés, un seul mot, vide) ;
  - la couleur déterministe et valide (hex) ;
  - l'objet badge : drapeau pour une sélection reconnue, pastille en repli — jamais
    de trou (clubs, sélections non mappées).
"""

from __future__ import annotations

import re

from pipeline import badges, config


# --- mapping nom → ISO -------------------------------------------------------
def test_iso_code_known_nations():
    assert badges.iso_code("France") == "fr"
    assert badges.iso_code("Brazil") == "br"
    assert badges.iso_code("Ivory Coast") == "ci"
    assert badges.iso_code("Bosnia and Herzegovina") == "ba"
    # sous-codes flagcdn pour les nations britanniques
    assert badges.iso_code("England") == "gb-eng"
    assert badges.iso_code("Scotland") == "gb-sct"
    assert badges.iso_code("Wales") == "gb-wls"
    assert badges.iso_code("Northern Ireland") == "gb-nir"


def test_iso_code_normalized_and_alias():
    assert badges.iso_code("france") == "fr"            # casse
    assert badges.iso_code("Curaçao") == "cw"           # accent / cédille
    assert badges.iso_code("Réunion") == "re"
    assert badges.iso_code("Côte d'Ivoire") == "ci"     # alias accentué
    assert badges.iso_code("USA") == "us"               # alias
    assert badges.iso_code("Korea Republic") == "kr"    # alias


def test_iso_code_unknown_is_none():
    assert badges.iso_code("Abkhazia") is None
    assert badges.iso_code("Catalonia") is None
    assert badges.iso_code("") is None
    assert badges.iso_code(None) is None


# --- emoji -------------------------------------------------------------------
def test_flag_emoji_from_alpha2():
    assert badges.flag_emoji("fr") == "🇫🇷"
    assert badges.flag_emoji("br") == "🇧🇷"
    assert badges.flag_emoji(None) is None


def test_flag_emoji_subdivisions():
    # Angleterre / Écosse / Pays de Galles ont un emoji (séquence de balises).
    assert badges.flag_emoji("gb-eng").startswith("\U0001F3F4")
    assert badges.flag_emoji("gb-sct").startswith("\U0001F3F4")
    # Irlande du Nord : pas d'emoji standard → None (l'image flagcdn reste dispo).
    assert badges.flag_emoji("gb-nir") is None


# --- initiales ---------------------------------------------------------------
def test_initials():
    assert badges.initials("Manchester United") == "MU"
    assert badges.initials("Real Madrid") == "RM"
    assert badges.initials("Arsenal") == "AR"          # un seul mot → 2 lettres
    assert badges.initials("Paris Saint-Germain") == "PS"
    # mots de liaison ignorés
    assert badges.initials("Borussia Dortmund") == "BD"
    assert badges.initials("") == "?"
    assert badges.initials(None) == "?"


# --- couleur -----------------------------------------------------------------
def test_color_is_deterministic_hex():
    c1 = badges.color("Arsenal")
    c2 = badges.color("Arsenal")
    assert c1 == c2                                     # déterministe
    assert re.fullmatch(r"#[0-9a-f]{6}", c1)            # hex valide
    assert badges.color("Arsenal") != badges.color("Chelsea")


# --- objet badge -------------------------------------------------------------
def test_badge_intl_flag():
    b = badges.badge("France", config.DOMAIN_INTL)
    assert b["kind"] == "flag"
    assert b["iso"] == "fr"
    assert b["emoji"] == "🇫🇷"
    assert b["label"] == "France"


def test_badge_intl_unmapped_falls_back_to_pill():
    b = badges.badge("Abkhazia", config.DOMAIN_INTL)
    assert b["kind"] == "initials"
    assert b["text"] == "AB"
    assert re.fullmatch(r"#[0-9a-f]{6}", b["color"])


def test_badge_club_is_always_pill():
    # même un nom de club homonyme d'un pays reste une pastille (domaine club).
    b = badges.badge("Georgia", config.DOMAIN_CLUB)
    assert b["kind"] == "initials"
    b2 = badges.badge("Real Madrid", config.DOMAIN_CLUB)
    assert b2["kind"] == "initials" and b2["text"] == "RM"

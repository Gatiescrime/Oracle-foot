"""Écussons d'équipe : drapeaux des sélections, pastilles d'initiales pour les clubs.

But : donner à CHAQUE équipe nommée dans l'UI un repère visuel, sans jamais laisser
de trou. Deux cas :

  * **Sélections** reconnues → code pays ISO 3166-1 alpha-2 (servi par flagcdn.com côté
    frontend) + l'emoji drapeau correspondant (pour les `<option>` natives, qui ne savent
    pas afficher d'image). Les nations britanniques utilisent les sous-codes flagcdn
    `gb-eng` / `gb-sct` / `gb-wls` / `gb-nir`.
  * **Tout le reste** (clubs, sélections non mappées : entités non-FIFA, historiques…) →
    **pastille ronde colorée** avec les initiales, couleur déterministe stable.

Le mapping est volontairement centralisé ici et **tolérant** : un nom inconnu retombe
proprement sur la pastille (aucun appel réseau, aucun crash). Toute la logique est pure
et testée hors ligne ; le frontend ne fait qu'afficher l'objet `badge`.
"""

from __future__ import annotations

import hashlib

from . import config
from .names import _norm

try:  # table d'écussons de clubs figée hors ligne (peut être absente avant génération)
    from .club_crests import CLUB_CRESTS
except Exception:  # noqa: BLE001
    CLUB_CRESTS: dict[str, str] = {}

# --- sélections → code ISO 3166-1 alpha-2 (minuscule, compatible flagcdn) ---------------
# Cas spéciaux : nations du Royaume-Uni via les sous-codes flagcdn.
_INTL_ISO: dict[str, str] = {
    "Afghanistan": "af", "Albania": "al", "Algeria": "dz", "Andorra": "ad",
    "Angola": "ao", "Antigua and Barbuda": "ag", "Argentina": "ar", "Armenia": "am",
    "Aruba": "aw", "Australia": "au", "Austria": "at", "Azerbaijan": "az",
    "Bahamas": "bs", "Bahrain": "bh", "Bangladesh": "bd", "Barbados": "bb",
    "Belarus": "by", "Belgium": "be", "Belize": "bz", "Benin": "bj", "Bermuda": "bm",
    "Bhutan": "bt", "Bolivia": "bo", "Bonaire": "bq", "Bosnia and Herzegovina": "ba",
    "Botswana": "bw", "Brazil": "br", "British Virgin Islands": "vg", "Brunei": "bn",
    "Bulgaria": "bg", "Burkina Faso": "bf", "Burundi": "bi", "Cambodia": "kh",
    "Cameroon": "cm", "Canada": "ca", "Cape Verde": "cv", "Cayman Islands": "ky",
    "Central African Republic": "cf", "Chad": "td", "Chile": "cl", "China": "cn",
    "Colombia": "co", "Comoros": "km", "Congo": "cg", "Cook Islands": "ck",
    "Costa Rica": "cr", "Croatia": "hr", "Cuba": "cu", "Curaçao": "cw", "Cyprus": "cy",
    "Czech Republic": "cz", "Denmark": "dk", "Djibouti": "dj", "Dominica": "dm",
    "Dominican Republic": "do", "DR Congo": "cd", "Ecuador": "ec", "Egypt": "eg",
    "El Salvador": "sv", "England": "gb-eng", "Equatorial Guinea": "gq",
    "Eritrea": "er", "Estonia": "ee", "Eswatini": "sz", "Ethiopia": "et",
    "Faroe Islands": "fo", "Fiji": "fj", "Finland": "fi", "France": "fr",
    "French Guiana": "gf", "Gabon": "ga", "Gambia": "gm", "Georgia": "ge",
    "Germany": "de", "Ghana": "gh", "Gibraltar": "gi", "Greece": "gr",
    "Greenland": "gl", "Grenada": "gd", "Guadeloupe": "gp", "Guam": "gu",
    "Guatemala": "gt", "Guinea": "gn", "Guinea-Bissau": "gw", "Guyana": "gy",
    "Haiti": "ht", "Honduras": "hn", "Hong Kong": "hk", "Hungary": "hu",
    "Iceland": "is", "India": "in", "Indonesia": "id", "Iran": "ir", "Iraq": "iq",
    "Israel": "il", "Italy": "it", "Ivory Coast": "ci", "Jamaica": "jm", "Japan": "jp",
    "Jordan": "jo", "Kazakhstan": "kz", "Kenya": "ke", "Kosovo": "xk", "Kuwait": "kw",
    "Kyrgyzstan": "kg", "Laos": "la", "Latvia": "lv", "Lebanon": "lb", "Lesotho": "ls",
    "Liberia": "lr", "Libya": "ly", "Liechtenstein": "li", "Lithuania": "lt",
    "Luxembourg": "lu", "Macau": "mo", "Madagascar": "mg", "Malawi": "mw",
    "Malaysia": "my", "Maldives": "mv", "Mali": "ml", "Malta": "mt",
    "Martinique": "mq", "Mauritania": "mr", "Mauritius": "mu", "Mayotte": "yt",
    "Mexico": "mx", "Moldova": "md", "Monaco": "mc", "Mongolia": "mn",
    "Montenegro": "me", "Montserrat": "ms", "Morocco": "ma", "Mozambique": "mz",
    "Myanmar": "mm", "Namibia": "na", "Nepal": "np", "Netherlands": "nl",
    "New Caledonia": "nc", "New Zealand": "nz", "Nicaragua": "ni", "Niger": "ne",
    "Nigeria": "ng", "North Korea": "kp", "North Macedonia": "mk",
    "Northern Ireland": "gb-nir", "Norway": "no", "Oman": "om", "Pakistan": "pk",
    "Palau": "pw", "Palestine": "ps", "Panama": "pa", "Papua New Guinea": "pg",
    "Paraguay": "py", "Peru": "pe", "Philippines": "ph", "Poland": "pl",
    "Portugal": "pt", "Puerto Rico": "pr", "Qatar": "qa", "Republic of Ireland": "ie",
    "Réunion": "re", "Romania": "ro", "Russia": "ru", "Rwanda": "rw",
    "Saint Kitts and Nevis": "kn", "Saint Lucia": "lc",
    "Saint Vincent and the Grenadines": "vc", "Samoa": "ws", "San Marino": "sm",
    "Saudi Arabia": "sa", "Scotland": "gb-sct", "Senegal": "sn", "Serbia": "rs",
    "Seychelles": "sc", "Sierra Leone": "sl", "Singapore": "sg", "Slovakia": "sk",
    "Slovenia": "si", "Solomon Islands": "sb", "Somalia": "so", "South Africa": "za",
    "South Korea": "kr", "South Sudan": "ss", "Spain": "es", "Sri Lanka": "lk",
    "Sudan": "sd", "Suriname": "sr", "Sweden": "se", "Switzerland": "ch", "Syria": "sy",
    "Tahiti": "pf", "Taiwan": "tw", "Tajikistan": "tj", "Tanzania": "tz",
    "Thailand": "th", "Timor-Leste": "tl", "Togo": "tg", "Tonga": "to",
    "Trinidad and Tobago": "tt", "Tunisia": "tn", "Turkey": "tr", "Turkmenistan": "tm",
    "Turks and Caicos Islands": "tc", "Uganda": "ug", "Ukraine": "ua",
    "United Arab Emirates": "ae", "United States": "us", "Uruguay": "uy",
    "Uzbekistan": "uz", "Vanuatu": "vu", "Venezuela": "ve", "Vietnam": "vn",
    "Wales": "gb-wls", "Yemen": "ye", "Zambia": "zm", "Zimbabwe": "zw",
}

# Quelques alias fréquents (orthographes alternatives présentes selon les sources).
_ALIASES: dict[str, str] = {
    "USA": "United States", "United States of America": "United States",
    "Cote d'Ivoire": "Ivory Coast", "Côte d'Ivoire": "Ivory Coast",
    "Korea Republic": "South Korea", "Korea DPR": "North Korea",
    "Ireland": "Republic of Ireland", "Czechia": "Czech Republic",
    "Cabo Verde": "Cape Verde", "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Macedonia": "North Macedonia", "Swaziland": "Eswatini",
    "Chinese Taipei": "Taiwan", "UAE": "United Arab Emirates",
}

# Index normalisé (insensible à la casse / aux accents / à la ponctuation).
_ISO_BY_NORM: dict[str, str] = {_norm(k): v for k, v in _INTL_ISO.items()}
for _alias, _canon in _ALIASES.items():
    _code = _INTL_ISO.get(_canon)
    if _code:
        _ISO_BY_NORM[_norm(_alias)] = _code

# Emojis drapeaux des sous-nations britanniques (séquences de balises Unicode).
_SUBDIV_EMOJI: dict[str, str] = {
    "gb-eng": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "gb-sct": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
    "gb-wls": "\U0001F3F4\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F",
    # Irlande du Nord : pas d'emoji standard → l'image flagcdn reste utilisée côté UI.
}

# Petits mots ignorés pour fabriquer des initiales propres.
_STOP_WORDS = {"de", "of", "the", "and", "et", "le", "la", "les", "des", "du",
               "fc", "cf", "afc", "sc", "ac", "as", "ss", "us", "city", "club"}


def iso_code(name: str | None) -> str | None:
    """Code ISO alpha-2 (ou sous-code flagcdn) d'une sélection, sinon None."""
    if not name:
        return None
    return _ISO_BY_NORM.get(_norm(name))


def flag_emoji(iso: str | None) -> str | None:
    """Emoji drapeau depuis un code ISO alpha-2 (None si non représentable)."""
    if not iso:
        return None
    if iso in _SUBDIV_EMOJI:
        return _SUBDIV_EMOJI[iso]
    if len(iso) != 2 or not iso.isalpha():
        return None  # sous-codes (gb-nir…) sans emoji standard
    base = 0x1F1E6
    return "".join(chr(base + (ord(c) - ord("a"))) for c in iso.lower())


def initials(name: str | None) -> str:
    """Initiales lisibles (1–2 lettres majuscules) pour une pastille."""
    if not name:
        return "?"
    words = [w for w in "".join(c if c.isalnum() or c.isspace() else " "
                                for c in name).split() if w]
    significant = [w for w in words if w.lower() not in _STOP_WORDS] or words
    if len(significant) >= 2:
        return (significant[0][0] + significant[1][0]).upper()
    w = significant[0]
    return (w[:2] if len(w) >= 2 else w[0]).upper()


def color(name: str | None) -> str:
    """Couleur de pastille déterministe et lisible sur fond sombre (hex)."""
    h = int(hashlib.md5((name or "?").encode("utf-8")).hexdigest()[:8], 16)
    hue = h % 360
    # saturation/luminosité fixes : assez vives mais lisibles avec du texte blanc.
    return _hsl_to_hex(hue / 360.0, 0.52, 0.46)


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    def hue_to_rgb(p: float, q: float, t: float) -> float:
        t %= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p
    if s == 0:
        r = g = b = l
    else:
        q = l * (1 + s) if l < 0.5 else l + s - l * s
        p = 2 * l - q
        r = hue_to_rgb(p, q, h + 1 / 3)
        g = hue_to_rgb(p, q, h)
        b = hue_to_rgb(p, q, h - 1 / 3)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def crest_url(name: str | None) -> str | None:
    """Écusson de club (URL d'image figée), sinon None. Aucun appel réseau."""
    if not name:
        return None
    return CLUB_CRESTS.get(name)


def badge(name: str | None, domain: str | None = None) -> dict:
    """Objet d'affichage pour une équipe : drapeau (sélection) ou pastille (sinon).

    - sélection reconnue → `{"kind":"flag","iso","emoji","label"}` ;
    - club avec écusson connu → pastille d'initiales **+ `crest`** (l'UI affiche l'image
      et retombe proprement sur les initiales si elle ne charge pas) ;
    - tout le reste → `{"kind":"initials","text","color","label"}`.
    Les initiales sont TOUJOURS fournies : aucun trou possible, même si l'image manque.
    """
    label = name or ""
    if domain != config.DOMAIN_CLUB:
        code = iso_code(name)
        if code:
            return {"kind": "flag", "iso": code, "emoji": flag_emoji(code), "label": label}
    out = {"kind": "initials", "text": initials(name), "color": color(name), "label": label}
    crest = crest_url(name)
    if crest:
        out["crest"] = crest
    return out

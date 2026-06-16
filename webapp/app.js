"use strict";

const $ = (id) => document.getElementById(id);
/* Honnêteté d'affichage : aucun match n'est jamais 100 % sûr. On n'affiche donc
   jamais « 100 % » ni « 0 % » bruts sur les écarts extrêmes (ex. France-Gibraltar),
   mais « >99 % » et « <1 % ». N'altère que l'affichage, pas les calculs. */
const pct = (p) => {
  if (p == null) return "—";
  const v = p * 100;
  if (v >= 99.5) return ">99%";
  if (v > 0 && v < 0.5) return "<1%";
  return Math.round(v) + "%";
};
const f2 = (x) => (x == null ? "—" : Number(x).toFixed(2));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Écusson : drapeau (sélection) ou pastille d'initiales (club / non mappé).
   Les données viennent du backend (pipeline/badges.py) ; ici, pur affichage. */
function badgeHTML(b) {
  if (!b) return "";
  if (b.kind === "flag") {
    return `<img class="team-badge flag" alt="" loading="lazy" width="22" height="16"` +
      ` src="https://flagcdn.com/32x24/${b.iso}.png"` +
      ` srcset="https://flagcdn.com/64x48/${b.iso}.png 2x">`;
  }
  const pill = `<span class="team-badge pill" aria-hidden="true" style="--pill:${b.color}">${esc(b.text)}</span>`;
  // Club avec écusson : on tente l'image ; si elle échoue, on remet la pastille (zéro trou).
  // Le repli est géré par un écouteur global (voir plus bas) : pas de handler inline.
  if (b.crest) {
    return `<img class="team-badge crest" alt="" loading="lazy" width="22" height="22"` +
      ` src="${esc(b.crest)}" data-fb="${esc(pill)}">`;
  }
  return pill;
}

/* Repli d'écusson : si une image de club ne charge pas, on la remplace par sa pastille
   d'initiales (data-fb). Capture car l'évènement « error » des images ne remonte pas. */
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img && img.tagName === "IMG" && img.classList.contains("crest") && img.dataset.fb) {
    img.outerHTML = img.dataset.fb;   // dataset décode déjà les entités HTML
  }
}, true);
let COMPS = { club: [], international: [] };
let LASTPRED = null;
let LOADED_DOMAIN = null;   // domaine dont les équipes sont actuellement chargées
let NAV_RESTORING = false;  // true pendant une restauration via le bouton retour
// Cache navigateur du socle statistique (déterministe) : une réanalyse du MÊME match
// (retour/suivant, re-soumission) s'affiche INSTANTANÉMENT, sans roue ni scintillement.
// Vidé à chaque rechargement de page (donc après un refresh des données -> reload).
const CLIENT_PRED = {};
const matchSig = (m) => [m.competition, m.home, m.away, m.neutral ? 1 : 0, m.useQ ? 1 : 0].join("|");
// Équipes du domaine courant : maps pour la recherche par saisie (typeahead datalist).
let TEAM_NAME = {};     // id -> nom affichable
let TEAM_ID = {};       // nom (minuscule) -> id

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = "Erreur " + res.status;
    try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

/* ----------------------------------------------------------- initialisation */
async function init() {
  try {
    COMPS = await api("/api/competitions");
    const sel = $("competition");
    const optGroup = (label, items) => {
      if (!items || !items.length) return "";
      return `<optgroup label="${label}">` +
        items.map((c) => `<option value="${c}">${c}</option>`).join("") + "</optgroup>";
    };
    sel.innerHTML = optGroup("Clubs", COMPS.club) + optGroup("Sélections", COMPS.international);
    sel.addEventListener("change", loadTeams);
    await loadTeams();
  } catch (e) {
    setStatus("Impossible de charger les compétitions : " + e.message, true);
  }
  bindUI();
  // État d'historique initial : la vue « Analyser » (formulaire vide). Le retour
  // depuis un résultat ramènera donc proprement au formulaire.
  history.replaceState({ view: "predict" }, "");
  loadMeta();
  loadFixtures();
}

/* Date de dernière mise à jour des données, affichée discrètement dans l'en-tête. */
async function loadMeta() {
  try {
    const m = await api("/api/meta");
    const iso = m.last_updated || m.latest_match_date;
    if (!iso) return;
    const d = new Date(iso);
    if (isNaN(d)) return;
    const txt = d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" });
    $("lastUpdated").textContent = "Données à jour au " + txt;
  } catch (e) { /* l'en-tête reste simplement vide */ }
}

function currentDomain() {
  const c = $("competition").value;
  return COMPS.club.includes(c) ? "club" : "international";
}

async function loadTeams() {
  const domain = currentDomain();
  try {
    const data = await api("/api/teams?domain=" + domain);
    const teams = (data.teams || []).slice();
    // maps id <-> nom (résolution de la saisie -> identifiant envoyé à l'API)
    TEAM_NAME = {}; TEAM_ID = {};
    teams.forEach((t) => { TEAM_NAME[t.id] = t.name; TEAM_ID[t.name.toLowerCase()] = t.id; });
    // datalist trié par ordre alphabétique : confortable pour la recherche au clavier
    const byName = teams.slice().sort((a, b) => a.name.localeCompare(b.name, "fr"));
    const opts = byName.map((t) => `<option value="${esc(t.name)}">`).join("");
    $("homeList").innerHTML = opts;
    $("awayList").innerHTML = opts;
    // valeurs par défaut : les deux meilleures équipes (Elo), pour une affiche crédible
    const byElo = teams.slice().sort((a, b) => (b.elo || 0) - (a.elo || 0));
    $("home").value = byElo[0] ? byElo[0].name : "";
    $("away").value = byElo[1] ? byElo[1].name : "";
    LOADED_DOMAIN = domain;
  } catch (e) {
    setStatus("Impossible de charger les équipes : " + e.message, true);
  }
}

/* Saisie d'une équipe -> identifiant (null si le texte ne correspond à aucune équipe). */
function teamId(el) {
  return TEAM_ID[(el.value || "").trim().toLowerCase()] || null;
}

/* ---------------------------------------------------------------- UI events */
function bindUI() {
  $("advToggle").addEventListener("change", (e) => { $("advanced").hidden = !e.target.checked; });
  $("predictForm").addEventListener("submit", onAnalyze);

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => navTo({ view: t.dataset.view })));

  $("refreshBtn").addEventListener("click", () => { $("refreshModal").hidden = false; });
  $("refreshCancel").addEventListener("click", cancelRefresh);
  $("refreshStart").addEventListener("click", startRefresh);

  $("simBtn").addEventListener("click", runSimulation);

  // Bouton « précédent » / geste retour : restaure l'état sans recharger la page.
  window.addEventListener("popstate", (e) => {
    NAV_RESTORING = true;
    try { applyState(e.state); } finally { NAV_RESTORING = false; }
  });
}

/* Bascule PURE de vue (onglets + panneaux), sans toucher à l'historique. */
function applyView(view) {
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.view === view;
    t.classList.toggle("is-active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  const VIEWS = { predict: "view-predict", upcoming: "view-upcoming", wc: "view-wc", track: "view-track" };
  Object.entries(VIEWS).forEach(([v, id]) => {
    const on = v === view;
    $(id).classList.toggle("is-active", on);
    $(id).hidden = !on;
  });
  if (view === "upcoming") loadUpcoming();
  if (view === "track") loadTrackRecord();
}

/* ------------------------------------------------------------- navigation
   Bouton « précédent » du navigateur (et geste retour mobile) : chaque
   changement d'onglet et chaque analyse crée une entrée d'historique
   (history.pushState). Le retour restaure l'état SANS recharger la page. */
function navTo(state) {
  if (NAV_RESTORING) return;                 // pendant une restauration : pas de push
  // Même état que l'entrée courante (re-soumission du même match, clic sur l'onglet
  // déjà actif) : on ré-applique sans empiler de doublon dans l'historique.
  if (JSON.stringify(state) === JSON.stringify(history.state)) { applyState(state); return; }
  history.pushState(state, "");
  applyState(state);
}

/* Applique un état d'historique (vue + éventuel match analysé). */
function applyState(state) {
  state = state || { view: "predict" };
  applyView(state.view || "predict");
  if (state.view === "predict") {
    if (state.match) {
      runPrediction(state.match);            // ré-affiche le résultat
    } else {
      $("results").hidden = true;            // retour « résultats -> formulaire »
      setStatus("");
    }
  }
}

/* S'assure que les équipes du domaine de `competition` sont chargées (sans
   réinitialiser inutilement le formulaire si le domaine n'a pas changé). */
async function ensureTeams(competition) {
  if ([...$("competition").options].some((o) => o.value === competition)) {
    $("competition").value = competition;
  }
  if (currentDomain() !== LOADED_DOMAIN) await loadTeams();
}

/* Ouvre la prédiction d'un match donné (réutilisé par « Matchs à venir » et la CdM). */
function analyzeMatch(competition, homeId, awayId, neutral) {
  const match = { competition, home: homeId, away: awayId, neutral: !!neutral, useQ: false };
  navTo({ view: "predict", match });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setStatus(msg, err) {
  const s = $("status");
  s.className = "status" + (err ? " err" : "");
  s.innerHTML = msg;
}

/* ---------------------------------------------------------------- analyse */
/* Soumission du formulaire : on valide, puis on NAVIGUE vers le résultat (ce qui
   crée une entrée d'historique). Le calcul réel est fait par runPrediction. */
async function onAnalyze(e) {
  e.preventDefault();
  const competition = $("competition").value;
  const home = teamId($("home")), away = teamId($("away"));
  if (!home || !away) { setStatus("Choisis deux équipes dans la liste proposée.", true); return; }
  if (home === away) { setStatus("Choisis deux équipes différentes.", true); return; }
  const match = { competition, home, away,
                  neutral: $("neutral").checked, useQ: $("useQualitative").checked };
  navTo({ view: "predict", match });
}

/* Calcule et affiche une prédiction pour `match` (ids d'équipes). Met le
   formulaire en cohérence (utile lors d'une restauration via le bouton retour).
   Ne touche PAS à l'historique : c'est l'appelant (navTo) qui s'en charge. */
async function runPrediction(match) {
  const { competition, home, away, neutral, useQ } = match;
  const btn = $("analyzeBtn");
  btn.disabled = true;
  const cached = CLIENT_PRED[matchSig(match)];
  // Match déjà calculé (retour/suivant, re-soumission) : affichage INSTANTANÉ,
  // sans masquer le résultat ni montrer la roue -> zéro scintillement.
  if (cached) {
    LASTPRED = cached;
    renderPrediction(cached);
    $("results").hidden = false;
    setStatus("");
  } else {
    setStatus('<span class="spinner"></span>Analyse en cours…');
    $("results").hidden = true;
  }
  try {
    await ensureTeams(competition);
    $("home").value = TEAM_NAME[home] || home;
    $("away").value = TEAM_NAME[away] || away;
    $("neutral").checked = !!neutral;
    $("useQualitative").checked = !!useQ;
    if (neutral || useQ) { $("advToggle").checked = true; $("advanced").hidden = false; }

    let pred = cached;
    if (!pred) {
      pred = await api("/api/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ competition, home, away, neutral, use_qualitative: useQ }),
      });
      CLIENT_PRED[matchSig(match)] = pred;
      LASTPRED = pred;
      renderPrediction(pred);
      $("results").hidden = false;
      setStatus("");
    }

    // appels secondaires : un échec ne casse pas la prédiction principale
    renderActuWithBase(pred, competition, home, away, neutral, useQ);
    loadStakeOrOdds(competition, home, away, neutral, useQ);
    loadScorers(competition, home, away, neutral, useQ);
  } catch (err) {
    if (!cached) setStatus(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function renderPrediction(p) {
  const probs = [
    { key: "home", label: p.home, v: p.p_home_win, cls: "home", badge: p.home_badge },
    { key: "draw", label: "Match nul", v: p.p_draw, cls: "draw", badge: null },
    { key: "away", label: p.away, v: p.p_away_win, cls: "away", badge: p.away_badge },
  ];
  const best = probs.slice().sort((a, b) => (b.v || 0) - (a.v || 0))[0];

  // recommandation en langage clair
  let title, text;
  const bp = best.v || 0;
  if (best.key === "draw") {
    title = "Match très serré";
    text = "Les deux équipes se valent. Le nul est l'issue la plus probable, un pari risqué dans les deux sens.";
  } else {
    const team = best.label;
    if (bp >= 0.6) { title = team + " grand favori"; text = "Le modèle voit " + team + " nettement au-dessus. Issue la plus solide du match."; }
    else if (bp >= 0.45) { title = team + " favori"; text = team + " part avec un avantage net, sans être à l'abri d'une surprise."; }
    else { title = "Match ouvert, léger avantage " + team; text = "Rien n'est joué : " + team + " est devant mais l'écart est mince."; }
  }
  $("recoTitle").textContent = title;
  $("recoText").textContent = text;
  $("recoPct").textContent = pct(bp);
  requestAnimationFrame(() => { $("recoBar").style.width = Math.round(bp * 100) + "%"; });

  // « pourquoi » : facteurs factuels (Elo, forme récente, terrain) calculés côté serveur
  const why = p.why;
  const whyBox = $("recoWhy");
  if (why && (why.summary || (why.factors || []).length)) {
    $("recoWhySummary").textContent = why.summary || "";
    $("recoWhyList").innerHTML = (why.factors || []).map((f) => `<li>${esc(f)}</li>`).join("");
    whyBox.hidden = false;
  } else {
    whyBox.hidden = true;
  }

  // barres 1X2
  $("oneXtwo").innerHTML = probs.map((o) =>
    `<div class="bar-row"><span class="name" title="${esc(o.label)}">${badgeHTML(o.badge)}${esc(o.label)}</span>
     <div class="bar-track"><div class="bar-fill ${o.cls}" data-w="${Math.round((o.v || 0) * 100)}"></div></div>
     <span class="pct">${pct(o.v)}</span></div>`).join("");
  requestAnimationFrame(() => {
    document.querySelectorAll("#oneXtwo .bar-fill").forEach((b) => { b.style.width = b.dataset.w + "%"; });
  });

  // metrics
  const ms = p.most_likely_score;
  $("mScore").textContent = Array.isArray(ms) ? `${ms[0]} – ${ms[1]}` : "—";
  $("mXg").textContent = `${f2(p.exp_home_goals)} – ${f2(p.exp_away_goals)}`;
  $("mOver").textContent = pct(p.p_over_2_5);
  $("mBtts").textContent = pct(p.p_btts);

  renderMatrix(p);
}

async function renderActuWithBase(pred, competition, home, away, neutral, useQ) {
  let base = null;
  if (useQ && pred.qualitative) {
    // état de chargement : la carte actu attend l'écho « sans actu » (réseau),
    // on évite ainsi un apparition tardive et brutale du panneau.
    const card = $("actuCard");
    $("actuBody").innerHTML =
      `<p style="color:var(--muted)"><span class="spinner"></span>Comparaison avec et sans l'actualité…</p>`;
    card.hidden = false;
    try {
      base = await api("/api/predict", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ competition, home, away, neutral, use_qualitative: false }),
      });
    } catch (e) { /* avant/après simplement omis */ }
  }
  renderActu(pred, base);
}

function renderActu(p, base) {
  const card = $("actuCard");
  if (!p.qualitative_enabled) { card.hidden = true; return; }
  const q = p.qualitative;

  if (!q) {
    $("actuBody").innerHTML =
      `<p style="color:var(--muted)">On a cherché l'actualité récente des deux équipes (blessures, suspensions, absences), mais aucun fait fiable et daté n'a été trouvé. Le résultat n'est donc pas modifié.</p>`;
    card.hidden = false;
    return;
  }

  const chg = (m) => { const d = Math.round((m - 1) * 100); return (d >= 0 ? "+" : "") + d + "%"; };
  const col = (m) => (m >= 1 ? "var(--green)" : "var(--red)");

  // effet chiffré sur les buts attendus
  const effect =
    `<div class="kv">
      <div><div class="k">Buts ${p.home}</div><div class="v">×${f2(q.mult_dom)} <span style="font-size:13px;color:${col(q.mult_dom)}">${chg(q.mult_dom)}</span></div></div>
      <div><div class="k">Buts ${p.away}</div><div class="v">×${f2(q.mult_ext)} <span style="font-size:13px;color:${col(q.mult_ext)}">${chg(q.mult_ext)}</span></div></div>
      <div><div class="k">Confiance</div><div class="v">${pct(q.confiance)}</div></div>
     </div>`;

  // avant / après (si la prédiction sans actu a pu être récupérée)
  let beforeAfter = "";
  if (base) {
    const row = (label, b, a, changed) =>
      `<tr${changed ? ' class="value-row"' : ""}><td>${label}</td><td class="num">${b}</td><td class="num">→ ${a}</td></tr>`;
    beforeAfter =
      `<table class="odds" style="margin-top:14px"><thead><tr><th>Issue</th><th>Sans l'actu</th><th>Avec l'actu</th></tr></thead><tbody>
        ${row("Victoire " + p.home, pct(base.p_home_win), pct(p.p_home_win), base.p_home_win !== p.p_home_win)}
        ${row("Match nul", pct(base.p_draw), pct(p.p_draw), base.p_draw !== p.p_draw)}
        ${row("Victoire " + p.away, pct(base.p_away_win), pct(p.p_away_win), base.p_away_win !== p.p_away_win)}
        ${row("Buts attendus", f2(base.exp_home_goals) + " – " + f2(base.exp_away_goals), f2(p.exp_home_goals) + " – " + f2(p.exp_away_goals), true)}
      </tbody></table>`;
  }

  // faits réellement trouvés en ligne (avec dates et sources cliquables)
  const faits = (q.faits || []).map((ft) => {
    const src = ft.source_url ? ` — <a href="${ft.source_url}" target="_blank" rel="noopener">${ft.source_titre || "source"}</a>` : "";
    const team = ft.equipe ? `<strong>${ft.equipe}</strong> · ` : "";
    return `<div class="fact"><span style="color:var(--faint)">[${ft.date || "?"}]</span> ${team}${ft.fait || ""}${src}</div>`;
  }).join("");

  const reasons = (q.facteurs || []).length
    ? `<p style="color:var(--muted);font-size:13px;margin-top:10px">Synthèse : ${q.facteurs.join(" · ")}</p>` : "";
  const srcline = `<p class="odds-src" style="margin-top:10px">Source : ${q.source || "?"}${q.as_of ? " · " + q.as_of.slice(0, 16).replace("T", " ") + " UTC" : ""}</p>`;

  $("actuBody").innerHTML =
    effect + beforeAfter +
    `<h4 style="margin:18px 0 8px;font-size:14px;color:var(--cyan)">Ce qui a été trouvé en ligne</h4>` +
    (faits || `<div class="fact" style="color:var(--muted)">Aucun fait daté retenu.</div>`) +
    reasons + srcline;
  card.hidden = false;
}

function renderMatrix(p) {
  const m = p.score_matrix;
  if (!Array.isArray(m)) { $("matrixCard").hidden = true; return; }
  $("matrixCard").hidden = false;
  let max = 0;
  m.forEach((row) => row.forEach((v) => { if (v > max) max = v; }));
  const mix = (t) => {
    const a = [22, 35, 63], b = [43, 213, 118];
    return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * t)).join(",")})`;
  };
  let html = '<div class="cell hd"></div>';
  for (let a = 0; a < m[0].length; a++) html += `<div class="cell hd">${a}</div>`;
  for (let h = 0; h < m.length; h++) {
    html += `<div class="cell hd">${h}</div>`;
    for (let a = 0; a < m[h].length; a++) {
      const v = m[h][a], t = max ? v / max : 0;
      html += `<div class="cell" style="background:${mix(t)}" title="${h}–${a} : ${pct(v)}">${Math.round(v * 100)}</div>`;
    }
  }
  $("matrix").innerHTML = html;
}

/* ---------------------------------------------------------- mise / cotes */
async function loadStakeOrOdds(competition, home, away, neutral, useQ) {
  const sel = $("selection").value;
  const odds = parseFloat($("odds").value);
  $("valueCard").hidden = true;
  $("oddsCard").hidden = true;

  if (sel && odds > 1) {
    // l'utilisateur a saisi une issue + une cote -> recommandation de mise
    try {
      const bankroll = parseFloat($("bankroll").value) || 100;
      const r = await api("/api/stake", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ competition, home, away, selection: sel, odds, bankroll, neutral, use_qualitative: useQ }),
      });
      renderStake(r);
    } catch (e) { /* on ignore en silence, panneau caché */ }
    return;
  }
  // sinon, comparateur de cotes / value
  try {
    const q = new URLSearchParams({ competition, home, away, neutral, use_qualitative: useQ });
    const o = await api("/api/odds/live?" + q.toString());
    renderOdds(o);
  } catch (e) { /* panneau caché */ }
}

function renderStake(r) {
  const card = $("valueCard");
  const lowConf = r.value && r.reliability === "low";
  const verdict = !r.value
    ? `<span class="badge no">Pas de value</span>`
    : lowConf
      ? `<span class="badge warn">Value à confirmer</span>`
      : `<span class="badge yes">Value détectée</span>`;
  const stake = r.value
    ? `<div><div class="k">Mise conseillée</div><div class="v good">${Math.round(r.stake_amount)} € <span style="font-size:13px;color:var(--muted)">(${pct(r.stake_fraction)} du capital)</span></div></div>`
    : `<div><div class="k">Mise conseillée</div><div class="v">0 €</div></div>`;
  $("valueBody").innerHTML =
    `<div class="value-verdict">${verdict}<span>${r.selection_label || ""}</span></div>
     <div class="kv">
       <div><div class="k">Proba du modèle</div><div class="v">${pct(r.model_prob)}</div></div>
       <div><div class="k">Proba selon la cote</div><div class="v">${pct(r.market_implied_prob)}</div></div>
       <div><div class="k">Avantage (edge)</div><div class="v ${r.edge > 0 ? "good" : ""}">${r.edge == null ? "—" : (r.edge >= 0 ? "+" : "") + Math.round(r.edge * 100) + "%"}</div></div>
       ${stake}
     </div>
     ${(r.warnings || []).length ? `<p class="warn">${r.warnings.join(" ")}</p>` : ""}`;
  card.hidden = false;
}

function renderOdds(o) {
  const card = $("oddsCard");
  const rows = o.markets || [];
  if (!rows.length) {
    if (o.reason) { $("oddsBody").innerHTML = `<p class="odds-src">${o.reason}</p>`; card.hidden = false; }
    return;
  }
  const src = o.source === "the-odds-api"
    ? `Cotes live (${o.n_books || 0} bookmakers).`
    : "Cotes football-data (historiques, pas un prix live).";
  const tag = (r) => {
    if (!r.value) return "";
    return r.value_reliable
      ? ' <span class="value-tag">value</span>'
      : ' <span class="value-tag warn" title="Le marché voit cette issue en outsider et le modèle est en fort désaccord : à confirmer.">value à confirmer</span>';
  };
  const body = rows.map((r) =>
    `<tr class="${r.value_reliable ? "value-row" : ""}">
       <td>${r.label}${tag(r)}</td>
       <td class="num">${f2(r.best_odds)}</td>
       <td>${r.book || ""}</td>
       <td class="num">${pct(r.model_prob)}</td>
       <td class="num">${r.edge == null ? "—" : (r.edge >= 0 ? "+" : "") + Math.round(r.edge * 100) + "%"}</td>
     </tr>`).join("");
  const flagged = rows.some((r) => r.value && !r.value_reliable);
  const note = flagged
    ? `<p class="odds-src">« Value à confirmer » : le marché voit l'issue en outsider et le modèle est en fort désaccord — fiabilité faible, ce n'est pas une opportunité sûre.</p>`
    : "";
  $("oddsBody").innerHTML =
    `<p class="odds-src">${src}</p>
     <table class="odds"><thead><tr><th>Issue</th><th>Meilleure cote</th><th>Book</th><th>Proba modèle</th><th>Avantage</th></tr></thead>
     <tbody>${body}</tbody></table>${note}`;
  card.hidden = false;
}

/* ---------------------------------------------------------------- buteurs */
async function loadScorers(competition, home, away, neutral, useQ) {
  $("scorersCard").hidden = true;
  if (currentDomain() !== "club") return;
  const parse = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
  try {
    const r = await api("/api/scorers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        competition, home, away, neutral, top_n: 6, use_qualitative: useQ,
        unavailable_home: parse($("unavHome").value), unavailable_away: parse($("unavAway").value),
      }),
    });
    if (!r.available) return;
    const col = (side) => {
      const list = (side.scorers || []);
      if (!list.length) return `<div><h4>${side.team || ""}</h4><p style="color:var(--muted);font-size:13px">Pas de données joueur.</p></div>`;
      const max = Math.max.apply(null, list.map((s) => s.prob || 0)) || 1;
      const rows = list.map((s) =>
        `<div class="sc-row"><span class="sc-name">${s.name}${s.position ? `<span class="pos">${s.position}</span>` : ""}</span>
         <span class="sc-prob">${pct(s.prob)}</span>
         <span class="sc-meter"><i style="width:${Math.round((s.prob / max) * 100)}%"></i></span></div>`).join("");
      return `<div><h4>${side.team || ""}</h4>${rows}</div>`;
    };
    $("scorersBody").innerHTML = col(r.home || {}) + col(r.away || {});
    $("scorersCard").hidden = false;
  } catch (e) { /* caché */ }
}

/* ---------------------------------------------------------- Matchs à venir */
let UPCOMING_LOADED = false;

function formatDay(iso) {
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d)) return iso;
  const s = d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function upcomingRow(m) {
  const time = m.commence_time
    ? new Date(m.commence_time).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
    : "";
  return `<div class="fx up">
    <span class="date">${time}</span>
    <span class="teams">${badgeHTML(m.home_badge)}${esc(m.home)} <span style="color:var(--faint)">vs</span> ${badgeHTML(m.away_badge)}${esc(m.away)}</span>
    <span class="up-comp">${esc(m.competition)}</span>
    <button data-c="${esc(m.competition)}" data-h="${esc(m.home_team_id)}" data-a="${esc(m.away_team_id)}" data-n="${m.neutral ? "1" : "0"}">Analyser</button>
  </div>`;
}

async function loadUpcoming(force) {
  if (UPCOMING_LOADED && !force) return;
  const host = $("upcomingList");
  host.innerHTML = '<p style="color:var(--muted)">Chargement…</p>';
  loadValueOfDay();
  try {
    const data = await api("/api/upcoming?days=7");
    UPCOMING_LOADED = true;
    const matches = data.matches || [];
    if (!matches.length) {
      host.innerHTML = '<p style="color:var(--muted)">Aucune affiche dans les prochains jours.</p>';
      $("upcomingSrc").textContent = "";
      return;
    }
    const groups = {};
    matches.forEach((m) => { (groups[m.date] = groups[m.date] || []).push(m); });
    const days = Object.keys(groups).sort();
    host.innerHTML = days.map((day) =>
      `<div class="up-day"><h4 class="up-date">${formatDay(day)}</h4>${groups[day].map(upcomingRow).join("")}</div>`
    ).join("");
    host.querySelectorAll("button[data-h]").forEach((b) => b.addEventListener("click",
      () => analyzeMatch(b.dataset.c, b.dataset.h, b.dataset.a, b.dataset.n === "1")));
    $("upcomingSrc").textContent = data.source === "the-odds-api"
      ? "Affiches et meilleures cotes via the-odds-api."
      : (data.source === "fixtures"
        ? "Affiches issues du calendrier (cotes indisponibles sans clé API)."
        : "");
  } catch (e) {
    host.innerHTML = `<p style="color:var(--red)">${esc(e.message)}</p>`;
  }
}

/* Encart « Meilleure value du jour » : meilleurs écarts modèle vs cotes du marché. */
async function loadValueOfDay() {
  const card = $("valueOfDay");
  try {
    const data = await api("/api/value/today?days=3&top_n=3");
    const items = data.items || [];
    if (!items.length) {
      // sans clé OU sans value trouvée : on n'encombre pas l'écran
      card.hidden = true;
      return;
    }
    const rows = items.map((it) => {
      const teams = `${badgeHTML(it.home_badge)}${esc(it.home)} <span style="color:var(--faint)">vs</span> ${badgeHTML(it.away_badge)}${esc(it.away)}`;
      const edge = (it.edge >= 0 ? "+" : "") + Math.round(it.edge * 100) + "%";
      return `<button class="vod-row" data-c="${esc(it.competition)}" data-h="${esc(it.home_team_id)}" data-a="${esc(it.away_team_id)}" data-n="${it.neutral ? "1" : "0"}">
        <span class="vod-match">${teams}</span>
        <span class="vod-pick">${esc(it.label)}</span>
        <span class="vod-odds">cote ${f2(it.best_odds)}<span class="vod-book">${esc(it.book || "")}</span></span>
        <span class="vod-edge">${edge}</span>
      </button>`;
    }).join("");
    card.innerHTML =
      `<h3 class="block-title">Meilleure value du jour <span class="info" tabindex="0" role="note" aria-label="Écart entre la probabilité du modèle et la meilleure cote du marché. Une value n'est jamais une garantie de gain.">i</span></h3>
       <div class="vod-list">${rows}</div>
       <p class="vod-note">La value compare la cote la plus haute trouvée à la probabilité du modèle. Ce n'est pas une garantie de gain : ne mise que ce que tu peux te permettre de perdre.</p>`;
    card.querySelectorAll(".vod-row").forEach((b) => b.addEventListener("click",
      () => analyzeMatch(b.dataset.c, b.dataset.h, b.dataset.a, b.dataset.n === "1")));
    card.hidden = false;
  } catch (e) {
    card.hidden = true;
  }
}

/* ------------------------------------------------------------- Track record */
let TRACK_LOADED = false;
const f3 = (x) => (x == null ? "—" : Number(x).toFixed(3));
const intFmt = (n) => (n == null ? "—" : Number(n).toLocaleString("fr-FR"));

function calibrationTable(rows) {
  const kept = (rows || []).filter((r) => (r.effectif || 0) >= 100);
  if (!kept.length) return "";
  const body = kept.map((r) => {
    const gap = Math.abs((r.observe || 0) - (r.predit_moyen || 0));
    return `<tr class="${gap <= 0.03 ? "value-row" : ""}">
      <td>${pct(r.predit_moyen)}</td>
      <td class="num">${pct(r.observe)}</td>
      <td class="num">${intFmt(r.effectif)}</td>
    </tr>`;
  }).join("");
  return `<table class="odds"><thead><tr><th>Le modèle annonce…</th><th>…ça arrive</th><th>Matchs</th></tr></thead><tbody>${body}</tbody></table>`;
}

function renderLiveTrackRecord(live) {
  // Historique RÉEL des prédictions (journal), clairement distingué du backtest.
  if (!live || !live.available) {
    const pend = (live && live.n_pending) || 0;
    return `<div class="panel" style="border-color:rgba(43,213,118,0.25)">
      <h3 class="block-title">Historique réel des prédictions</h3>
      <p class="track-note" style="margin-top:0">Pas encore de prédiction évaluée. Les
      matchs analysés et présents au calendrier sont enregistrés, puis notés
      automatiquement une fois joués.${pend ? ` <strong>${intFmt(pend)}</strong> en attente de résultat.` : ""}</p>
    </div>`;
  }
  const period = (live.since && live.until)
    ? `du ${live.since} au ${live.until}` : "";
  const clv = (live.clv_mean != null)
    ? `<div class="metric"><span class="m-label">CLV moyen <span class="info" tabindex="0" role="note" aria-label="Closing Line Value : cote captée vs cote de clôture. Positif = on a pris un meilleur prix que le marché final.">i</span></span><span class="m-value">${(live.clv_mean >= 0 ? "+" : "") + (live.clv_mean * 100).toFixed(1)}%</span></div>`
    : "";
  const roi = (live.roi != null)
    ? `<div class="metric"><span class="m-label">ROI (paris à plat, cotes captées)</span><span class="m-value">${(live.roi >= 0 ? "+" : "") + (live.roi * 100).toFixed(1)}%</span></div>`
    : "";
  const byComp = (live.by_competition || []).length
    ? `<h4 class="track-sub">Par compétition</h4>
       <table class="odds"><thead><tr><th>Compétition</th><th class="num">Prédictions</th><th class="num">Réussite 1X2</th><th class="num">RPS</th></tr></thead>
       <tbody>${live.by_competition.map((c) =>
         `<tr><td>${esc(c.competition)}</td><td class="num">${intFmt(c.n)}</td><td class="num">${pct(c.accuracy)}</td><td class="num">${f3(c.rps)}</td></tr>`).join("")}</tbody></table>`
    : "";
  const overTime = (live.over_time || []).length > 1
    ? `<h4 class="track-sub">RPS dans le temps</h4>
       <p class="track-note" style="margin-top:0">${live.over_time.map((p) =>
         `${p.period} : <strong>${f3(p.rps)}</strong> (${intFmt(p.n)})`).join(" · ")}</p>`
    : "";
  return `<div class="panel" style="border-color:rgba(43,213,118,0.25)">
    <h3 class="block-title">Historique réel des prédictions <span class="info" tabindex="0" role="note" aria-label="Performance mesurée sur les prédictions réellement faites par le site, notées après coup. À distinguer du backtest ci-dessous, qui rejoue l'histoire.">i</span></h3>
    <p class="track-note" style="margin-top:0">Performance des prédictions <strong>réellement faites par le site</strong>, notées après coup ${period}. Différent du <em>backtest</em> ci-dessous (qui rejoue l'histoire). ${intFmt(live.n_pending)} en attente de résultat.</p>
    <div class="metrics">
      <div class="metric"><span class="m-label">Prédictions évaluées</span><span class="m-value">${intFmt(live.n_settled)}</span></div>
      <div class="metric"><span class="m-label">Réussite 1X2 <span class="info" tabindex="0" role="note" aria-label="Part des matchs où l'issue la plus probable selon le modèle s'est réalisée.">i</span></span><span class="m-value">${pct(live.accuracy)}</span></div>
      <div class="metric"><span class="m-label">RPS réel</span><span class="m-value">${f3(live.rps)}</span></div>
      <div class="metric"><span class="m-label">Score de Brier</span><span class="m-value">${f3(live.brier)}</span></div>
      ${clv}${roi}
    </div>
    ${byComp}${overTime}
    <h4 class="track-sub">Calibration (prédictions réelles)</h4>
    ${calibrationTable(live.calibration)}
  </div>`;
}

function renderWcBilan(wc) {
  // Bilan honnête CdM : prédiction PRÉ-MATCH (walk-forward anti-fuite) vs résultat réel.
  if (!wc || !wc.available || !wc.n_matches) {
    return `<div class="panel" style="border-color:rgba(120,170,255,0.25)">
      <h3 class="block-title">Coupe du Monde 2026 — bilan prédit vs réel</h3>
      <p class="track-note" style="margin-top:0">Aucun match de Coupe du Monde encore
      joué (ou bilan pas encore calculé). Dès qu'un match est joué, sa prédiction
      pré-match apparaîtra ici, face au résultat réel.</p></div>`;
  }
  // Issue 1X2 prédite = celle JUGÉE par la pastille (cohérente avec predicted_outcome).
  const issueLabel = (m) => {
    const o = m.predicted_outcome;            // 0 dom / 1 nul / 2 ext
    const p = [m.p_home, m.p_draw, m.p_away][o];
    const lbl = o === 1 ? "Nul" : "Victoire " + (o === 0 ? m.home : m.away);
    return `${lbl} (${pct(p)})`;
  };
  const rows = wc.matches.map((m) => {
    const ok = m.correct_1x2 === 1;
    const verdict = ok ? `<span class="wc-ok">✓ juste</span>` : `<span class="wc-ko">✗ raté</span>`;
    const exact = (m.ml_home === m.actual_home && m.ml_away === m.actual_away)
      ? `<span class="wc-exact" title="Le score exact le plus probable correspond au score réel.">🎯 score exact</span>` : "";
    return `<div class="wc-match">
      <div class="wc-teams">${badgeHTML(m.home_badge)}${esc(m.home)} <span style="color:var(--faint)">–</span> ${badgeHTML(m.away_badge)}${esc(m.away)}</div>
      <div class="wc-pred">Issue prédite : <strong>${esc(issueLabel(m))}</strong><span class="wc-sub">score probable ${m.ml_home}–${m.ml_away}</span></div>
      <div class="wc-real">Réel : <strong>${m.actual_home}–${m.actual_away}</strong> ${verdict}${exact}</div>
    </div>`;
  }).join("");
  const legend = `<p class="track-note" style="margin-top:0">« ✓ juste / ✗ raté » juge l'<strong>issue</strong> (bon vainqueur ou nul) ; la mention <strong>🎯 score exact</strong> signale, à part, quand le score probable correspond aussi au score réel.</p>`;
  return `<div class="panel" style="border-color:rgba(120,170,255,0.25)">
    <h3 class="block-title">Coupe du Monde 2026 — bilan prédit vs réel <span class="info" tabindex="0" role="note" aria-label="Pour chaque match joué, ce que le modèle aurait prédit en n'utilisant QUE les matchs antérieurs à sa date (aucune fuite), face au résultat réel. Mesure walk-forward, distincte du journal de prédictions live.">i</span></h3>
    <p class="track-note" style="margin-top:0">Chaque match est prédit en n'utilisant QUE les données connues <strong>avant le coup d'envoi</strong> (modèle reconstruit sur les matchs antérieurs). C'est une mesure honnête, distincte de l'« historique réel » des prédictions live ci-dessus.</p>
    <div class="metrics">
      <div class="metric"><span class="m-label">Matchs joués évalués</span><span class="m-value">${intFmt(wc.n_matches)}</span></div>
      <div class="metric"><span class="m-label">Bon pronostic 1X2</span><span class="m-value">${pct(wc.accuracy)}</span></div>
      <div class="metric"><span class="m-label">RPS moyen</span><span class="m-value">${f3(wc.rps)}</span></div>
      <div class="metric"><span class="m-label">Score moyen prédit / réel</span><span class="m-value">${f2(wc.avg_pred_home_goals)}–${f2(wc.avg_pred_away_goals)} / ${f2(wc.avg_real_home_goals)}–${f2(wc.avg_real_away_goals)}</span></div>
    </div>
    ${legend}
    <div class="wc-list">${rows}</div>
    <h4 class="track-sub">Calibration sur la Coupe du Monde</h4>
    ${calibrationTable(wc.calibration)}
  </div>`;
}

function renderTrackRecord(data) {
  const live = renderLiveTrackRecord(data.live);
  const wc = renderWcBilan(data.wc);
  if (!data.available) {
    return live + wc +
      `<p class="track-note" style="text-align:center">Le backtest historique n'est pas encore généré (<code>python -m pipeline.backtest</code>).</p>`;
  }
  const club = data.club || {};
  const intl = data.international || {};
  const vb = club.value_betting || {};
  const gapBook = (club.rps_model_on_odds_subset != null && club.rps_bookmaker != null)
    ? club.rps_model_on_odds_subset - club.rps_bookmaker : null;

  const intro =
    `<div class="panel" style="border-color:rgba(255,255,255,0.12)">
      <p style="margin:0;color:var(--muted)">On rejoue l'histoire dans l'ordre du temps (walk-forward : on n'entraîne que sur le passé) et on mesure trois choses : la qualité de classement (<strong>RPS</strong>, plus bas = mieux), la <strong>calibration</strong> (quand on dit X %, est-ce que ça arrive X %&nbsp;?) et la comparaison au <strong>bookmaker</strong>. Verdict sans détour : le modèle est <strong>très bien calibré</strong> mais <strong>ne bat pas encore le bookmaker</strong> sur les clubs.</p>
    </div>`;

  const clubPanel =
    `<div class="panel">
      <h3 class="block-title">Clubs — 5 grands championnats</h3>
      <div class="metrics">
        <div class="metric"><span class="m-label">Prédictions hors échantillon</span><span class="m-value">${intFmt(club.n_predictions)}</span></div>
        <div class="metric"><span class="m-label">RPS modèle <span class="info" tabindex="0" role="note" aria-label="Ranked Probability Score : qualité des probabilités 1/X/2. Plus bas = mieux.">i</span></span><span class="m-value">${f3(club.rps_calibrated)}</span></div>
        <div class="metric"><span class="m-label">RPS bookmaker (à battre)</span><span class="m-value">${f3(club.rps_bookmaker)}</span></div>
        <div class="metric"><span class="m-label">Écart au bookmaker</span><span class="m-value" style="color:var(--red)">${gapBook == null ? "—" : "+" + f3(gapBook)}</span></div>
      </div>
      <div class="value-verdict" style="margin-top:16px">
        <span class="badge no">Value betting : ROI ${vb.roi == null ? "—" : (vb.roi >= 0 ? "+" : "") + (vb.roi * 100).toFixed(1) + "%"}</span>
        <span>${intFmt(vb.n_bets)} paris simulés (edge &gt; 5 %), profit ${vb.profit == null ? "—" : vb.profit.toFixed(0) + " u."}</span>
      </div>
      <p class="track-note">Le bookmaker reste devant (RPS plus bas) et la stratégie de paris à la valeur <strong>perd de l'argent</strong> sur l'historique. C'est le résultat honnête attendu : les cotes des grands championnats sont très efficientes.</p>
      <h4 class="track-sub">Calibration des probabilités</h4>
      ${calibrationTable(club.calibration)}
    </div>`;

  const intlPanel =
    `<div class="panel">
      <h3 class="block-title">Sélections — internationaux & Coupe du Monde</h3>
      <div class="metrics">
        <div class="metric"><span class="m-label">Prédictions hors échantillon</span><span class="m-value">${intFmt(intl.n_predictions)}</span></div>
        <div class="metric"><span class="m-label">RPS modèle</span><span class="m-value">${f3(intl.rps_calibrated)}</span></div>
        <div class="metric"><span class="m-label">RPS bookmaker</span><span class="m-value">indispo.</span></div>
        <div class="metric"><span class="m-label">Log-loss</span><span class="m-value">${f3(intl.logloss_calibrated)}</span></div>
      </div>
      <p class="track-note">Modèle solide et bien calibré, mais <strong>non comparable au marché</strong> faute de cotes internationales dans nos sources. RPS plus bas qu'en club car les matchs de sélection sont souvent plus déséquilibrés (donc plus faciles à classer).</p>
      <h4 class="track-sub">Calibration des probabilités</h4>
      ${calibrationTable(intl.calibration)}
    </div>`;

  const backtestIntro =
    `<h3 class="block-title" style="margin-top:8px">Backtest historique (walk-forward)</h3>`;
  return live + wc + backtestIntro + intro + clubPanel + intlPanel +
    `<p class="track-note" style="text-align:center">Reproductible : <code>python -m pipeline.backtest</code>. Les probabilités sont des estimations, pas des certitudes.</p>`;
}

async function loadTrackRecord(force) {
  if (TRACK_LOADED && !force) return;
  const host = $("trackBody");
  host.innerHTML = '<p style="color:var(--muted)">Chargement…</p>';
  try {
    const data = await api("/api/track-record");
    if (!data.available && !(data.live && data.live.available) && !(data.wc && data.wc.available)) {
      host.innerHTML = '<p style="color:var(--muted)">Aucun historique pour le moment. Lance <code>python -m pipeline.backtest</code> pour le backtest ; l\'historique réel se remplit au fil des matchs prédits puis joués.</p>';
      return;
    }
    TRACK_LOADED = true;
    host.innerHTML = renderTrackRecord(data);
  } catch (e) {
    host.innerHTML = `<p style="color:var(--red)">${esc(e.message)}</p>`;
  }
}

/* ---------------------------------------------------------- Coupe du Monde */
async function loadFixtures() {
  try {
    const data = await api("/api/fixtures");
    const fx = data.fixtures || [];
    if (!fx.length) { $("fixtures").innerHTML = '<p style="color:var(--muted)">Aucun match à venir.</p>'; return; }
    $("fixtures").innerHTML = fx.slice(0, 40).map((m) => {
      const d = (m.date || "").slice(0, 10);
      return `<div class="fx"><span class="date">${d}</span>
        <span class="teams">${badgeHTML(m.home_badge)}${esc(m.home)} <span style="color:var(--faint)">vs</span> ${badgeHTML(m.away_badge)}${esc(m.away)}</span>
        <button data-c="${esc(m.competition)}" data-h="${m.home_team_id}" data-a="${m.away_team_id}">Analyser</button></div>`;
    }).join("");
    $("fixtures").querySelectorAll("button").forEach((b) => b.addEventListener("click",
      () => analyzeMatch(b.dataset.c, b.dataset.h, b.dataset.a, true)));
  } catch (e) {
    $("fixtures").innerHTML = `<p style="color:var(--red)">${e.message}</p>`;
  }
}

async function runSimulation() {
  const btn = $("simBtn");
  btn.disabled = true;
  $("simStatus").textContent = "Simulation de milliers de tournois…";
  try {
    const r = await api("/api/simulate?n_sims=3000");
    const teams = (r.teams || []).slice().sort((a, b) => (b.p_title || 0) - (a.p_title || 0)).slice(0, 16);
    const max = Math.max.apply(null, teams.map((t) => t.p_title || 0)) || 1;
    $("simResult").innerHTML = teams.map((t) =>
      `<div class="sim-row"><span class="name">${badgeHTML(t.badge)}${esc(t.team)}</span>
       <div class="sim-track"><div class="sim-fill" style="width:${Math.round((t.p_title / max) * 100)}%"></div></div>
       <span class="pct">${pct(t.p_title)}</span></div>`).join("");
    $("simStatus").textContent = `${r.n_sims} simulations · probabilité de titre`;
  } catch (e) {
    $("simStatus").textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- refresh */
let refreshTimer = null, refreshStartTs = 0, refreshMode = "rapide";

function setRefreshMsg(txt) {
  const el = refreshStartTs ? Math.round((Date.now() - refreshStartTs) / 1000) : 0;
  $("refreshProgress").querySelector("span").textContent = refreshStartTs ? `${txt} (${el}s)` : txt;
}

async function startRefresh() {
  refreshMode = document.querySelector('input[name="mode"]:checked').value;
  refreshStartTs = Date.now();
  $("refreshProgress").hidden = false;
  $("refreshStart").disabled = true;
  setRefreshMsg(refreshMode === "complet"
    ? "Réentraînement du modèle, 1 à 2 minutes. Ne ferme pas la fenêtre"
    : "Téléchargement des derniers résultats");
  try {
    await api("/api/refresh?mode=" + refreshMode, { method: "POST" });
    refreshTimer = setInterval(pollRefresh, 2000);
  } catch (e) {
    setRefreshMsg(e.message);
    $("refreshStart").disabled = false;
  }
}

/* Annuler : stoppe le suivi, demande au serveur d'abandonner le job en cours
   (s'il y en a un), puis referme proprement. Le timeout global dur reste un
   filet de sécurité, mais l'annulation est désormais immédiate côté serveur. */
async function cancelRefresh() {
  const wasRunning = refreshTimer !== null;
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  refreshStartTs = 0;
  if (wasRunning) {
    try { await api("/api/refresh/cancel", { method: "POST" }); } catch (e) { /* déjà fini/idle */ }
  }
  $("refreshStart").disabled = false;
  $("refreshProgress").hidden = true;
  $("refreshModal").hidden = true;
}

async function pollRefresh() {
  try {
    const s = await api("/api/refresh/status");
    if (s.state === "done") {
      clearInterval(refreshTimer);
      $("refreshProgress").querySelector("span").textContent = "Données à jour. Rechargement…";
      setTimeout(() => location.reload(), 900);
    } else if (s.state === "error") {
      clearInterval(refreshTimer);
      $("refreshStart").disabled = false;
      $("refreshProgress").querySelector("span").textContent = "Échec : " + (s.message || "erreur");
    } else {
      setRefreshMsg(s.message || (refreshMode === "complet"
        ? "Réentraînement du modèle, ne ferme pas la fenêtre"
        : "Mise à jour en cours"));
    }
  } catch (e) { /* on retente au prochain tick */ }
}

init();

/* PWA : enregistrement discret du service worker (échec silencieux si non supporté). */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* pas de PWA, pas grave */ });
  });
}

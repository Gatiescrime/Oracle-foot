"use strict";

const $ = (id) => document.getElementById(id);
const pct = (p) => (p == null ? "—" : Math.round(p * 100) + "%");
const f2 = (x) => (x == null ? "—" : Number(x).toFixed(2));

let COMPS = { club: [], international: [] };
let LASTPRED = null;

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
  loadFixtures();
}

function currentDomain() {
  const c = $("competition").value;
  return COMPS.club.includes(c) ? "club" : "international";
}

async function loadTeams() {
  const domain = currentDomain();
  try {
    const data = await api("/api/teams?domain=" + domain);
    const teams = (data.teams || []).slice().sort((a, b) => (b.elo || 0) - (a.elo || 0));
    const opts = teams.map((t) => `<option value="${t.id}">${t.name}</option>`).join("");
    $("home").innerHTML = opts;
    $("away").innerHTML = opts;
    if (teams.length > 1) $("away").selectedIndex = 1;
  } catch (e) {
    setStatus("Impossible de charger les équipes : " + e.message, true);
  }
}

/* ---------------------------------------------------------------- UI events */
function bindUI() {
  $("advToggle").addEventListener("change", (e) => { $("advanced").hidden = !e.target.checked; });
  $("predictForm").addEventListener("submit", onAnalyze);

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));

  $("refreshBtn").addEventListener("click", () => { $("refreshModal").hidden = false; });
  $("refreshCancel").addEventListener("click", () => { $("refreshModal").hidden = true; });
  $("refreshStart").addEventListener("click", startRefresh);

  $("simBtn").addEventListener("click", runSimulation);
}

function switchView(view) {
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.view === view;
    t.classList.toggle("is-active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("view-predict").classList.toggle("is-active", view === "predict");
  $("view-predict").hidden = view !== "predict";
  $("view-wc").classList.toggle("is-active", view === "wc");
  $("view-wc").hidden = view !== "wc";
}

function setStatus(msg, err) {
  const s = $("status");
  s.className = "status" + (err ? " err" : "");
  s.innerHTML = msg;
}

/* ---------------------------------------------------------------- analyse */
async function onAnalyze(e) {
  e.preventDefault();
  const competition = $("competition").value;
  const home = $("home").value, away = $("away").value;
  if (!home || !away) return;
  if (home === away) { setStatus("Choisis deux équipes différentes.", true); return; }

  const neutral = $("neutral").checked;
  const useQ = $("useQualitative").checked;
  const btn = $("analyzeBtn");
  btn.disabled = true;
  setStatus('<span class="spinner"></span>Analyse en cours…');
  $("results").hidden = true;

  try {
    const pred = await api("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ competition, home, away, neutral, use_qualitative: useQ }),
    });
    LASTPRED = pred;
    renderPrediction(pred);
    $("results").hidden = false;
    setStatus("");

    // appels secondaires : un échec ne casse pas la prédiction principale
    renderActuWithBase(pred, competition, home, away, neutral, useQ);
    loadStakeOrOdds(competition, home, away, neutral, useQ);
    loadScorers(competition, home, away, neutral, useQ);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function renderPrediction(p) {
  const probs = [
    { key: "home", label: p.home, v: p.p_home_win, cls: "home" },
    { key: "draw", label: "Match nul", v: p.p_draw, cls: "draw" },
    { key: "away", label: p.away, v: p.p_away_win, cls: "away" },
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

  // barres 1X2
  $("oneXtwo").innerHTML = probs.map((o) =>
    `<div class="bar-row"><span class="name" title="${o.label}">${o.label}</span>
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
  const verdict = r.value
    ? `<span class="badge yes">Value détectée</span>`
    : `<span class="badge no">Pas de value</span>`;
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
  const body = rows.map((r) =>
    `<tr class="${r.value ? "value-row" : ""}">
       <td>${r.label}${r.value ? ' <span class="value-tag">value</span>' : ""}</td>
       <td class="num">${f2(r.best_odds)}</td>
       <td>${r.book || ""}</td>
       <td class="num">${pct(r.model_prob)}</td>
       <td class="num">${r.edge == null ? "—" : (r.edge >= 0 ? "+" : "") + Math.round(r.edge * 100) + "%"}</td>
     </tr>`).join("");
  $("oddsBody").innerHTML =
    `<p class="odds-src">${src}</p>
     <table class="odds"><thead><tr><th>Issue</th><th>Meilleure cote</th><th>Book</th><th>Proba modèle</th><th>Avantage</th></tr></thead>
     <tbody>${body}</tbody></table>`;
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

/* ---------------------------------------------------------- Coupe du Monde */
async function loadFixtures() {
  try {
    const data = await api("/api/fixtures");
    const fx = data.fixtures || [];
    if (!fx.length) { $("fixtures").innerHTML = '<p style="color:var(--muted)">Aucun match à venir.</p>'; return; }
    $("fixtures").innerHTML = fx.slice(0, 40).map((m) => {
      const d = (m.date || "").slice(0, 10);
      return `<div class="fx"><span class="date">${d}</span>
        <span class="teams">${m.home} <span style="color:var(--faint)">vs</span> ${m.away}</span>
        <button data-c="${m.competition}" data-h="${m.home_team_id}" data-a="${m.away_team_id}">Analyser</button></div>`;
    }).join("");
    $("fixtures").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      switchView("predict");
      const c = b.dataset.c;
      if ([...$("competition").options].some((o) => o.value === c)) $("competition").value = c;
      loadTeams().then(() => {
        $("home").value = b.dataset.h; $("away").value = b.dataset.a;
        $("neutral").checked = true; $("advToggle").checked = true; $("advanced").hidden = false;
        $("predictForm").requestSubmit();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    }));
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
      `<div class="sim-row"><span class="name">${t.team}</span>
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

/**
 * ORI Report page controller.
 *
 * This file only presents data already reduced by weather.py. It never reads
 * raw messages and does not reproduce NLP logic in the browser. The one
 * exception is the explicit "subtract a person" lens: compact per-person
 * contributions are removed from the displayed word and phrase totals.
 */
"use strict";

const state = {
  report: null,
  excluded: new Set(),
  circleMode: "circles",
  circleIndex: 0
};

// ---------- Small display helpers ----------

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>\"]/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;"
  })[character]);
}

function formatCount(value) {
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}m`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}k`;
  return String(value);
}

function friendlyDate(value) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC"
  }).format(new Date(`${value}T12:00:00Z`));
}

// Voice counts read as people: a prominent number plus one small person
// glyph per voice (capped so wide rows stay aligned).
function voiceIcons(count) {
  return `<span class="voices" title="${count} voices">` +
    `<b>${count}</b>${"<i></i>".repeat(Math.min(count, 6))}</span>`;
}

function topicName(topic) {
  return topic.label.split(" · ").slice(0, 2).join(" / ");
}

// ---------- Person subtraction lens ----------

function adjusted(row, contributionType) {
  let count = row.count;
  let voices = row.voices;
  for (const key of state.excluded) {
    const person = state.report.people.find(item => item.key === key);
    const contribution = person?.contributions?.[contributionType]?.[row.term] || 0;
    count -= contribution;
    if (contribution) voices -= 1;
  }
  return { ...row, count: Math.max(0, count), voices: Math.max(0, voices) };
}

function adjustedTokens() {
  let tokens = state.report.stats.tokens;
  for (const key of state.excluded) {
    const person = state.report.people.find(item => item.key === key);
    tokens -= person?.contributions?.tokens || 0;
  }
  return Math.max(1, tokens);
}

function expectedLabel(row, expected) {
  if (row.reference_status === "not_found") return "no match in broad-web baseline";
  if (expected < 0.01) return "broad-web expectation <0.01";
  if (expected < 1) return `broad-web expectation ${expected.toFixed(2)}`;
  if (expected < 10) return `broad-web expectation ${expected.toFixed(1)}`;
  return `broad-web expectation ${Math.round(expected)}`;
}

function lexiconRows(rows) {
  const tokenShare = adjustedTokens() / state.report.stats.tokens;
  const minimum = state.report.method.configuration.minimum_word_count;
  const minimumLift = state.report.method.configuration.word_reference_minimum_lift;
  const liftSaturation = state.report.method.lexicon_lift_saturation;
  const visible = rows
    .map(original => {
      const row = adjusted(original, "words");
      const expected = original.reference_expected == null
        ? null
        : original.reference_expected * tokenShare;
      const lift = expected == null ? null : row.count / Math.max(expected, 1e-12);
      const adoption = Math.log1p(row.count) * Math.log1p(row.voices);
      const boundedLift = Math.min(lift == null ? liftSaturation : lift, liftSaturation);
      const distinctiveness = Math.log2(Math.max(minimumLift, boundedLift));
      const score = adoption * distinctiveness;
      return { ...row, expected, lift, score };
    })
    .filter(row =>
      row.count >= minimum && row.voices >= 2 &&
      (row.lift == null || row.lift >= minimumLift)
    )
    .sort((left, right) =>
      right.score - left.score || right.count - left.count ||
      left.term.localeCompare(right.term)
    )
    .slice(0, 18);

  const renderRows = column => column.map(row => `
    <div class="lex-row">
      <b>${escapeHtml(row.term)}</b>
      <em>${escapeHtml(expectedLabel(row, row.expected))}</em>
      <small><b>${row.count}</b> uses · ${voiceIcons(row.voices)}</small>
    </div>
  `).join("");
  if (!visible.length) return '<div class="unit-note">No signal remains in this lens.</div>';
  const midpoint = Math.ceil(visible.length / 2);
  return `<div>${renderRows(visible.slice(0, midpoint))}</div>` +
    `<div>${renderRows(visible.slice(midpoint))}</div>`;
}

function phraseRows(rows) {
  const minimum = state.report.method.configuration.minimum_phrase_count;
  const visible = rows
    .map(original => {
      const row = adjusted(original, "phrases");
      const score = Math.log1p(row.count) * Math.log1p(row.voices) *
        original.association;
      return { ...row, score };
    })
    .filter(row => row.count >= minimum && row.voices >= 2)
    .sort((left, right) =>
      right.score - left.score || right.count - left.count ||
      left.term.localeCompare(right.term)
    )
    .slice(0, 16);

  return visible.map(row => `
    <span class="phrase">
      <b>${escapeHtml(row.term)}</b>
      <small><b>${row.count}</b> uses · ${voiceIcons(row.voices)}</small>
    </span>
  `).join("") || '<div class="unit-note">No construction remains in this lens.</div>';
}

function renderFilter() {
  const menu = document.getElementById("filter-menu");
  const button = document.getElementById("filter-button");
  button.textContent = state.excluded.size
    ? `Lexicon minus ${state.excluded.size}`
    : "Full lexicon";
  button.classList.toggle("has-filter", state.excluded.size > 0);
  menu.innerHTML = `
    <header>
      <span>Subtract from lexicon</span>
      <button class="small-button" id="clear-filter" type="button">Reset</button>
    </header>
    ${state.report.people.map(row => `
      <button class="filter-person ${state.excluded.has(row.key) ? "is-excluded" : ""}"
              data-person="${row.key}" type="button">
        <i></i><b>${escapeHtml(row.name)}</b><span>${row.messages}</span>
      </button>
    `).join("")}
  `;
  menu.querySelectorAll("[data-person]").forEach(personButton => {
    personButton.onclick = () => togglePerson(personButton.dataset.person);
  });
  menu.querySelector("#clear-filter").onclick = () => {
    state.excluded.clear();
    refreshLens();
  };
}

function togglePerson(key) {
  if (state.excluded.has(key)) state.excluded.delete(key);
  else state.excluded.add(key);
  refreshLens();
}

function refreshLens() {
  renderFilter();
  const characteristic = document.getElementById("lex-characteristic");
  if (characteristic) {
    characteristic.innerHTML = lexiconRows(state.report.lexicon.characteristic);
    document.getElementById("phrase-bigrams").innerHTML =
      phraseRows(state.report.phrases.bigrams);
    document.getElementById("phrase-trigrams").innerHTML =
      phraseRows(state.report.phrases.trigrams);
    document.getElementById("lens-status").textContent = state.excluded.size
      ? `${state.excluded.size} voice${state.excluded.size === 1 ? "" : "s"} subtracted`
      : "full community";
  }
  const people = document.getElementById("people-list");
  if (people) {
    people.innerHTML = peopleRows(state.report.people);
    bindPersonButtons();
  }
}

function bindPersonButtons() {
  document.querySelectorAll(".person-row[data-person]").forEach(button => {
    button.onclick = () => togglePerson(button.dataset.person);
  });
}

// ---------- Reusable rows and small charts ----------

// One line per word: term, a magnitude bar scaled to the column's largest
// count, the count itself, and the voices behind it. Rising is measured in
// the recent window, fading in the previous one — the column subtitle names
// the window, so the row carries no before/after arithmetic. Kind and first
// sighting live in the row tooltip.
function movementRows(rows, direction) {
  const visible = rows.slice(0, 10);
  const uses = direction === "rising"
    ? row => row.recent_uses
    : row => row.previous_uses;
  const maximum = Math.max(1, ...visible.map(uses));
  return visible.map(row => `
    <div class="movement-row"
         title="${row.kind === "word" ? "word" : `${row.kind[0]}-word phrase`} · first observed ${escapeHtml(row.first_observed)}">
      <b>${escapeHtml(row.term)}</b>
      <span class="movement-track"><span class="movement-fill"
        style="--w:${(uses(row) / maximum * 100).toFixed(1)}%"></span></span>
      <em>${uses(row)}</em>
      ${voiceIcons(row.voices)}
    </div>
  `).join("") || `<div class="unit-note">No ${direction} signals passed the threshold.</div>`;
}

function sourceRows(rows) {
  const maximum = Math.max(1, ...rows.map(row => row.count));
  return rows.slice(0, 14).map(row => `
    <div class="source-row">
      <span>${escapeHtml(row.domain)}</span>
      <div class="track"><div class="fill" style="width:${row.count / maximum * 100}%"></div></div>
      <b>${row.count} links · ${voiceIcons(row.voices)}</b>
    </div>
  `).join("");
}

function symbolRows(rows) {
  return rows.slice(0, 18).map(row => `
    <span class="symbol ${String(row.symbol).length > 4 ? "is-name" : ""}">
      ${escapeHtml(row.symbol)}<small>${row.count}</small>
    </span>
  `).join("");
}

function heatmap(report) {
  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const maximum = Math.max(1, ...report.activity.heatmap.flat());
  let html = "<span></span>" + Array.from({ length: 24 }, (_unused, hour) =>
    `<span class="heat-hour">${hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}</span>`
  ).join("");
  report.activity.heatmap.forEach((row, dayIndex) => {
    html += `<span class="heat-day">${dayNames[dayIndex]}</span>`;
    html += row.map((value, hour) => `
      <i class="heat-cell" style="--heat:${value / maximum}"
         title="${dayNames[dayIndex]} ${String(hour).padStart(2, "0")}:00 UTC · ${value} messages"></i>
    `).join("");
  });
  return html;
}

function peopleRows(rows) {
  return rows.slice(0, 13).map(row => {
    const topics = row.topics.slice(0, 2)
      .map(item => item.label.split(" · ").slice(0, 2).join("/"))
      .join(" · ") || "no stable topic";
    return `
      <button class="person-row ${state.excluded.has(row.key) ? "is-excluded" : ""}"
              data-person="${row.key}" type="button">
        <b>${escapeHtml(row.name)}</b><strong>${row.messages}</strong>
        <span>${(row.share * 100).toFixed(1)}% · ${escapeHtml(topics)} · click to subtract</span>
      </button>
    `;
  }).join("");
}

function languageContainers() {
  return `
    <div class="lex-columns" id="lex-characteristic">
      ${lexiconRows(state.report.lexicon.characteristic)}
    </div>
  `;
}

function phraseContainers() {
  return `
    <div class="phrase-group"><span class="mini-title">Two-word currents</span>
      <div class="phrase-list" id="phrase-bigrams">${phraseRows(state.report.phrases.bigrams)}</div>
    </div>
    <div class="phrase-group"><span class="mini-title">Three-word currents</span>
      <div class="phrase-list" id="phrase-trigrams">${phraseRows(state.report.phrases.trigrams)}</div>
    </div>
  `;
}

// ---------- Conversation circles ----------

function circleLine(row, labelField = "name") {
  return `<div class="data-line"><b>${escapeHtml(row[labelField])}</b>` +
    `<span>${row.messages ?? row.uses ?? row.mentions}</span></div>`;
}

function renderCircleExplorer() {
  const rows = state.circleMode === "circles"
    ? state.report.conversation_circles
    : state.report.channel_profiles;
  const index = Math.min(state.circleIndex, Math.max(0, rows.length - 1));
  const row = rows[index];
  const list = document.getElementById("circle-list");
  const detail = document.getElementById("circle-detail");
  if (!row) {
    list.innerHTML = "";
    detail.innerHTML = '<div class="unit-note">Not enough shared participation for a stable circle.</div>';
    return;
  }

  list.innerHTML = rows.map((item, itemIndex) => `
    <button class="circle-choice ${itemIndex === index ? "is-active" : ""}"
            data-circle="${itemIndex}" type="button">
      <b>${escapeHtml(item.label || `#${item.name}`)}</b>
      <span><b>${item.messages}</b> messages · ${voiceIcons(item.members?.length || item.voices)}</span>
    </button>
  `).join("");
  list.querySelectorAll("[data-circle]").forEach(button => {
    button.onclick = () => {
      state.circleIndex = Number(button.dataset.circle);
      renderCircleExplorer();
    };
  });

  const members = row.members || row.people || [];
  const channels = row.channels || [{ name: row.name, messages: row.messages }];
  const topics = row.topics || [];
  const language = row.language || [];
  const secondaryRows = state.circleMode === "circles" ? channels : topics;
  const secondary = secondaryRows.slice(0, 7).map(item =>
    state.circleMode === "circles"
      ? circleLine(item)
      : `<span class="topic-chip">${escapeHtml(topicName(item))}<small>${item.mentions} mentions · ${Math.round(item.share * 100)}%</small></span>`
  ).join("");

  detail.innerHTML = `
    <header><div>
      <span class="mini-title">${state.circleMode === "circles" ? "Participant overlap" : "Origin channel"}</span>
      <h3>${escapeHtml(row.label || `#${row.name}`)}</h3>
    </div><div><b>${row.messages}</b>
      <small>messages · ${row.share ? `${Math.round(row.share * 100)}%` : `${row.voices} voices`}</small>
    </div></header>
    <div class="circle-columns">
      <div><span class="mini-title">People</span>
        <div class="data-stack">${members.slice(0, 8).map(item => circleLine(item)).join("")}</div>
      </div>
      <div><span class="mini-title">${state.circleMode === "circles" ? "Rooms" : "Topics"}</span>
        <div class="data-stack">${secondary}</div>
      </div>
      <div><span class="mini-title">Characteristic language</span>
        <div class="data-stack">${language.slice(0, 8).map(item => `
          <div class="data-line"><b>${escapeHtml(item.term)}</b><span>${item.uses} · ${item.lift}×</span></div>
        `).join("")}</div>
        ${state.circleMode === "circles" ? `
          <span class="mini-title" style="margin-top:22px">Topics</span>
          ${topics.slice(0, 3).map(item => `
            <span class="topic-chip">${escapeHtml(item.label.split(" · ").slice(0, 2).join(" / "))}
              <small>${item.mentions} mentions</small></span>
          `).join("")}
        ` : ""}
      </div>
    </div>
  `;
}

// ---------- One renderer per slideshow chapter ----------

function weatherChapter(report) {
  return `
    <section class="chapter chapter-weather is-active" id="weather" data-label="Cloud">
      <div class="chapter-inner weather-layout">
        <div class="weather-copy">
          <span class="chapter-kicker">01 / ${escapeHtml(report.server.name)}</span>
          <h1><em>Memetic</em><br>Weather</h1>
          <div class="report-context"><div class="context-row"><small>Observation</small>
            <time>${friendlyDate(report.window.from)} — ${friendlyDate(report.window.to)}</time>
          </div></div>
          <div class="weather-stats">
            <div class="weather-stat"><b>${formatCount(report.stats.messages)}</b><span>messages</span></div>
            <div class="weather-stat"><b>${report.stats.active_voices}</b><span>voices</span></div>
            <div class="weather-stat"><b>${report.topics.length}</b><span>topic families</span></div>
            <div class="weather-stat"><b>${report.stats.channels_observed}</b><span>channels</span></div>
          </div>
          <div class="weather-legend"><span><i></i>color = topic</span>
            <span><i style="background:var(--paper)"></i>cloud = share</span>
            <span><i style="background:var(--sun)"></i>line = association</span>
          </div>
        </div>
        <div class="weather-viz">
          <div class="weather-shell"><canvas id="weather-map"></canvas>
            <div class="map-head"><b>Semantic cloud</b><span>drag to orbit · wheel to zoom · hover a word</span></div>
            <div class="semantic-readout" id="semantic-readout"></div>
          </div>
          <div class="calendar-reel" id="calendar-reel"></div>
          <div class="time-row"><button id="play">❚❚ Pause</button>
            <input id="time-slider" type="range" min="0" max="${report.days.length - 1}" value="0">
            <time id="map-date">${report.days[0]}</time>
          </div>
        </div>
      </div>
    </section>
  `;
}

function topicsChapter(report) {
  return `
    <section class="chapter" id="topics" data-label="Topics"><div class="chapter-inner topics-inner">
      <div class="chapter-heading"><div><span class="chapter-kicker">02 / Topic share</span>
        <h2>Topics over time</h2></div>
        <time>${friendlyDate(report.window.from)} — ${friendlyDate(report.window.to)} · each day = 100%</time>
      </div>
      <div><div id="topic-timeline"></div>
        <div class="timeline-note" id="timeline-note">Select a date to set the Memetic Weather cloud to the same moment.</div>
      </div>
    </div></section>
  `;
}

function movementChapter(report) {
  const movement = report.movement;
  const dates = movement.status === "ready"
    ? `${friendlyDate(movement.previous.from)} — ${friendlyDate(movement.recent.to)}`
    : "insufficient history";
  return `
    <section class="chapter" id="movement" data-label="Movement"><div class="chapter-inner">
      <div class="chapter-heading"><div><span class="chapter-kicker">03 / Language movement</span>
        <h2>What rose. What faded.</h2></div><time>${dates} · normalized by words</time>
      </div>
      <div class="movement-grid">
        <div class="movement-side"><h3><i class="dir">&#8599;</i> Rising <span>recent 13 weeks</span></h3>
          <div class="movement-head"><span></span><span></span><em>uses</em><span>speakers</span></div>
          ${movementRows(movement.rising, "rising")}</div>
        <div class="movement-side"><h3><i class="dir">&#8600;</i> Fading <span>previous 13 weeks</span></h3>
          <div class="movement-head"><span></span><span></span><em>uses</em><span>speakers</span></div>
          ${movementRows(movement.fading, "fading")}</div>
      </div>
    </div></section>
  `;
}

function lexiconChapter() {
  return `
    <section class="chapter" id="lexicon" data-label="Lexicon"><div class="chapter-inner">
      <div class="chapter-heading"><div><span class="chapter-kicker">04 / Community language</span>
        <h2>Community lexicon</h2></div><span class="lens-status" id="lens-status">full community</span>
      </div>
      <div class="dialect-grid">
        <div class="dialect-side"><h3>Characteristic words</h3>
          ${languageContainers()}</div>
        <div class="dialect-side"><h3>Repeated constructions</h3>${phraseContainers()}</div>
      </div>
    </div></section>
  `;
}

function circlesChapter() {
  return `
    <section class="chapter" id="circles" data-label="Circles"><div class="chapter-inner">
      <div class="chapter-heading"><div><span class="chapter-kicker">05 / Conversation circles</span>
        <h2>Who gathers where</h2></div>
        <div class="circle-tools"><button class="mode-button is-active" data-mode="circles" type="button">Participant circles</button>
          <button class="mode-button" data-mode="channels" type="button">Origin channels</button></div>
      </div>
      <div class="circle-explorer"><div class="circle-list" id="circle-list"></div>
        <div class="circle-detail" id="circle-detail"></div></div>
    </div></section>
  `;
}

function rhythmChapter(report) {
  return `
    <section class="chapter" id="rhythm" data-label="Rhythm"><div class="chapter-inner">
      <div class="chapter-heading"><div><span class="chapter-kicker">06 / Activity</span>
        <h2>Heartbeat &amp; people</h2></div>
        <time>${friendlyDate(report.window.from)} — ${friendlyDate(report.window.to)} · UTC</time>
      </div>
      <div class="rhythm-grid"><div class="heartbeat"><h3>Weekly heartbeat</h3>
        <div class="heat-grid">${heatmap(report)}</div></div>
        <div class="people-panel"><h3>Who is talking</h3><div id="people-list">${peopleRows(report.people)}</div></div>
      </div>
    </div></section>
  `;
}

function sourcesChapter(report) {
  return `
    <section class="chapter" id="sources" data-label="Sources"><div class="chapter-inner">
      <div class="chapter-heading"><div><span class="chapter-kicker">07 / Incoming signals</span>
        <h2>Source diet &amp; symbols</h2></div>
        <time>${friendlyDate(report.window.from)} — ${friendlyDate(report.window.to)}</time>
      </div>
      <div class="source-grid"><div class="source-panel"><h3>Shared sources</h3>${sourceRows(report.sources)}</div>
        <div class="symbol-panel"><h3>Common symbols</h3><div class="symbols">${symbolRows(report.symbols)}</div></div>
      </div>
    </div></section>
  `;
}

function methodChapter(report) {
  return `
    <section class="chapter" id="method" data-label="Method"><div class="chapter-inner method-layout">
      <div><span class="chapter-kicker">08 / Coverage &amp; method</span>
        <div class="method-title">What this<br>report measures</div>
        <div class="method-numbers">
          <div class="method-number"><b>${formatCount(report.stats.history_messages)}</b><span>historical messages</span></div>
          <div class="method-number"><b>${report.stats.active_voices}</b><span>current voices</span></div>
          <div class="method-number"><b>${friendlyDate(report.stats.history_from)}</b><span>history begins</span></div>
          <div class="method-number"><b>${friendlyDate(report.stats.history_to)}</b><span>history ends</span></div>
        </div>
      </div>
      <div><div class="method-list">
        <div class="method-row"><b>Canonical source</b><span>One normalized feed history, deduplicated by Discord message ID.</span></div>
        <div class="method-row"><b>Words</b><span>Literal lowercase surfaces; URLs, platform terms, member aliases, and conversational glue are filtered.</span></div>
        <div class="method-row"><b>Phrases</b><span>Repeated constructions selected by frequency, voice breadth, association, and broad-English comparison.</span></div>
        <div class="method-row"><b>Topics</b><span>Qualified words and bigrams connected by composition and observed context.</span></div>
        <div class="method-row"><b>Movement</b><span>Normalized recent 13-week rates versus the prior 13 weeks, shown as plain change.</span></div>
        <div class="method-row"><b>Circles</b><span>Participant overlap across channels; language labels each group after membership is detected.</span></div>
        <div class="method-row"><b>Report data</b><span>Names and aggregate contributions are retained. Raw messages and Discord identifiers are absent.</span></div>
      </div><footer class="report-footer"><span>Generated ${escapeHtml(report.generated)}</span>
        <span>${escapeHtml(report.schema)}</span></footer></div>
    </div></section>
  `;
}

// ---------- Page initialization ----------

function render(report) {
  state.report = report;
  document.title = `Memetic Weather · ${report.server.name}`;
  document.getElementById("brand-name").textContent = report.server.name;
  document.getElementById("station-meta").textContent =
    `${friendlyDate(report.window.from)} — ${friendlyDate(report.window.to)} · ` +
    `${formatCount(report.stats.messages)} messages`;

  document.getElementById("deck").innerHTML = [
    weatherChapter(report),
    topicsChapter(report),
    movementChapter(report),
    lexiconChapter(),
    circlesChapter(),
    rhythmChapter(report),
    sourcesChapter(report),
    methodChapter(report)
  ].join("");

  initializeWeather(report);
  initializeTimeline(report);
  initializeDeck();
  renderFilter();
  bindPersonButtons();
  renderCircleExplorer();
  document.querySelectorAll("[data-mode]").forEach(button => {
    button.onclick = () => {
      state.circleMode = button.dataset.mode;
      state.circleIndex = 0;
      document.querySelectorAll("[data-mode]").forEach(item =>
        item.classList.toggle("is-active", item === button)
      );
      renderCircleExplorer();
    };
  });
}

function initializeWeather(report) {
  if (window.memeticWeather) window.memeticWeather.destroy();
  window.memeticWeather = new MemeticWeather(document.getElementById("weather-map"), {
    topics: report.topics,
    edges: report.semantic_graph.edges,
    days: report.days,
    activeVoices: report.stats.active_voices,
    labelFor: topicName,
    slider: document.getElementById("time-slider"),
    playButton: document.getElementById("play"),
    dateLabel: document.getElementById("map-date"),
    calendar: document.getElementById("calendar-reel"),
    readout: document.getElementById("semantic-readout")
  });
}

function initializeTimeline(report) {
  // CSS variables rather than literal colors, so the chart follows the page
  // theme; the weather canvas keeps MEMETIC_PALETTE because its dark shell
  // never changes.
  window.topicTimeline = new TopicTimeline(document.getElementById("topic-timeline"), {
    topics: report.topics,
    days: report.days,
    labelFor: topicName,
    palette: Array.from({ length: 12 }, (_unused, index) => `var(--chart-${index})`),
    onSelect: index => {
      window.memeticWeather.time = index;
      window.memeticWeather.playing = false;
      document.getElementById("timeline-note").textContent =
        `Cloud set to ${report.days[index]}.`;
    }
  });
}

function initializeDeck() {
  const deck = document.getElementById("deck");
  const chapters = [...deck.querySelectorAll(".chapter")];
  const nav = document.getElementById("chapter-nav");
  nav.innerHTML = chapters.map((chapter, index) => `
    <button type="button" data-target="${chapter.id}">
      <span>${String(index + 1).padStart(2, "0")} ${escapeHtml(chapter.dataset.label)}</span><i></i>
    </button>
  `).join("");
  const buttons = [...nav.querySelectorAll("button")];
  const scrollChapter = (target, behavior = "smooth") => {
    // The slideshow itself is the scroll container. scrollIntoView may also
    // move the document viewport in headless and embedded browsers, leaving a
    // hash between chapters. Summing preceding chapter heights avoids the
    // offset-parent ambiguity of offsetTop inside this full-screen grid.
    const index = chapters.indexOf(target);
    const top = chapters.slice(0, Math.max(0, index))
      .reduce((sum, chapter) => sum + chapter.offsetHeight, 0);
    if (behavior === "auto") {
      const previousBehavior = deck.style.scrollBehavior;
      deck.style.scrollBehavior = "auto";
      deck.scrollTop = top;
      deck.style.scrollBehavior = previousBehavior;
    } else {
      deck.scrollTo({ top, behavior });
    }
  };

  let activeId = null;
  const activate = id => {
    if (id === activeId) return;
    activeId = id;
    chapters.forEach(chapter => chapter.classList.toggle("is-active", chapter.id === id));
    buttons.forEach(button =>
      button.setAttribute("aria-current", button.dataset.target === id ? "true" : "false")
    );
    if (window.memeticWeather) window.memeticWeather.setVisible(id === "weather");
  };

  buttons.forEach(button => {
    button.onclick = () => {
      history.replaceState(null, "", `#${button.dataset.target}`);
      scrollChapter(document.getElementById(button.dataset.target));
    };
  });
  activate(chapters[0].id);

  // The chapter under the middle of the viewport drives the navigation
  // marker and canvas pause. On phones a chapter grows several screens tall,
  // so a chapter-relative intersection ratio would never cross a threshold.
  // Handlers are assigned (not added) so a server switch replaces them.
  deck.onscroll = () => {
    const center = deck.scrollTop + deck.clientHeight / 2;
    let bottom = 0;
    for (const chapter of chapters) {
      bottom += chapter.offsetHeight;
      if (center < bottom) return activate(chapter.id);
    }
  };

  const scrollToHash = () => {
    const target = location.hash ? document.querySelector(location.hash) : null;
    if (target?.classList.contains("chapter")) {
      scrollChapter(target, "auto");
      activate(target.id);
    }
  };
  requestAnimationFrame(() => requestAnimationFrame(scrollToHash));
  onhashchange = scrollToHash;
  onkeydown = event => {
    if (/INPUT|BUTTON/.test(event.target.tagName) ||
        !["ArrowDown", "ArrowUp", "PageDown", "PageUp"].includes(event.key)) return;
    const current = Math.max(0, chapters.findIndex(chapter => chapter.classList.contains("is-active")));
    const direction = /Down/.test(event.key) ? 1 : -1;
    scrollChapter(chapters[Math.max(0, Math.min(chapters.length - 1, current + direction))]);
    event.preventDefault();
  };
}

document.getElementById("filter-button").onclick = () =>
  document.getElementById("filter-menu").classList.toggle("is-open");
document.addEventListener("click", event => {
  if (!event.target.closest(".filter-wrap")) {
    document.getElementById("filter-menu").classList.remove("is-open");
  }
});

// ---------- Server selection and bootstrap ----------

const reportCache = {};

function showLoadError(message) {
  document.getElementById("deck").innerHTML =
    `<div class="loading">${escapeHtml(message)}</div>`;
}

function renderServerPicker() {
  const box = document.getElementById("server-picker");
  if (!box) return;
  box.innerHTML = (state.servers || []).map(entry => `
    <button class="server-chip ${entry.id === state.serverId ? "is-active" : ""}"
            data-server="${escapeHtml(entry.id)}" type="button"
            title="${escapeHtml(entry.name)} · ${entry.messages} messages in window">
      ${entry.icon
        ? `<img src="${escapeHtml(entry.icon)}" alt="">`
        : `<i>${escapeHtml((entry.name || "?")[0].toUpperCase())}</i>`}
      <span>${escapeHtml(entry.name)}</span>
    </button>
  `).join("");
  box.querySelectorAll("[data-server]").forEach(button => {
    button.onclick = () => {
      const entry = state.servers.find(item => item.id === button.dataset.server);
      if (entry && entry.id !== state.serverId) loadServer(entry);
    };
  });
}

function loadServer(entry) {
  state.serverId = entry.id;
  renderServerPicker();
  const apply = report => {
    state.excluded.clear();
    state.circleMode = "circles";
    state.circleIndex = 0;
    render(report);
  };
  if (reportCache[entry.id]) {
    apply(reportCache[entry.id]);
    return;
  }
  fetch(`data/${entry.file}`, { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw Error(`data/${entry.file} not found — rerun build.py`);
      return response.json();
    })
    .then(report => {
      reportCache[entry.id] = report;
      apply(report);
    })
    .catch(error => showLoadError(error.message));
}

// servers.json is the only report entry point. Every server is reduced by the
// same builder and gets the same weather-<server-id>.json contract.
fetch("data/servers.json", { cache: "no-store" })
  .then(response => {
    if (!response.ok) throw Error("Run python3 ORI-report/build.py first");
    return response.json();
  })
  .then(payload => {
    state.servers = payload.servers || [];
    if (!state.servers.length) throw Error("No server reports were built");
    loadServer(state.servers[0]);
  })
  .catch(error => showLoadError(error.message));

/* Alert2Playbook page behaviour. Plain JavaScript, no framework.
   Every piece of text that came from an alert or an AI is escaped before it is shown. */
(() => {
  const C = window.A2P;
  document.documentElement.classList.add("js");

  const MAX = 4000;
  const TABS = ["try", "how", "good", "dev"];
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name, cls = "i") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const pad = (n) => String(n).padStart(2, "0");
  const fmtMs = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms < 1 ? "<1 ms" : Math.round(ms) + " ms");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const store = {
    get: (k, s = sessionStorage) => { try { return s.getItem(k); } catch { return null; } },
    set: (k, v, s = sessionStorage) => { try { v == null ? s.removeItem(k) : s.setItem(k, v); } catch {} },
  };
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const phone = matchMedia("(max-width: 760px)");
  const narrow = matchMedia("(max-width: 900px)");
  const debounce = (fn, ms = 120) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  const S = {
    samples: [], health: null, results: null,
    picked: null,          // sample object, or "own"
    run: null,             // full state of the run on screen
    steps: [],             // step records shown in the timeline
    replay: false,         // the run on screen is a saved one
    busy: false,
    code: store.get("a2p-code"),
    wantCode: false,       // the visitor asked to enter the demo code
    flash: "",             // one-off note under the Start button
    storyRun: null,
  };

  /* ================================================================ code blocks */
  function jsonHtml(value) {
    const text = JSON.stringify(value, null, 2) ?? "null";
    return esc(text)
      .replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)/g, '<span class="k">$1</span>$2')
      .replace(/(:\s*)(&quot;(?:[^&]|&(?!quot;))*?&quot;)/g, '$1<span class="s">$2</span>')
      .replace(/(:\s*)(-?\d+(?:\.\d+)?)/g, '$1<span class="n">$2</span>');
  }
  function codeBlock(value, raw = false) {
    const id = "c" + Math.random().toString(36).slice(2);
    const body = raw ? esc(value) : jsonHtml(value);
    return `<div class="codewrap"><button class="copy" data-copy="${id}" type="button">${icon("copy")}<span>Copy</span></button><pre class="code" id="${id}">${body}</pre></div>`;
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    const t = document.getElementById(b.dataset.copy)?.innerText || "";
    navigator.clipboard?.writeText(t).then(() => {
      b.classList.add("done"); b.innerHTML = `${icon("check")}<span>Copied</span>`;
      setTimeout(() => { b.classList.remove("done"); b.innerHTML = `${icon("copy")}<span>Copy</span>`; }, 1400);
    });
  });

  /* ================================================================ header, tabs, theme */
  const top = $("#top");
  const onScroll = () => top.classList.toggle("scrolled", window.scrollY > 4);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  function moveInd(animate = true) {
    const ind = $(".tab-ind"), act = $(".tab[aria-selected='true']");
    if (!ind || !act || phone.matches) return;
    ind.classList.toggle("no-anim", !animate);
    ind.style.setProperty("--x", act.offsetLeft + "px");
    ind.style.setProperty("--w", act.offsetWidth + "px");
  }

  function showTab(name, { scroll = true } = {}) {
    if (!TABS.includes(name)) name = "try";
    const was = $(".panel.active")?.id;
    $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
    if (location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
    moveInd();
    if (scroll && was !== "panel-" + name) window.scrollTo({ top: 0 });
    if (name === "how") requestAnimationFrame(drawEdges);
    if (name === "good") requestAnimationFrame(() => chart?.render(true));
    Hero.check();
  }
  $$(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
  window.addEventListener("hashchange", () => {
    const h = location.hash.slice(1);
    if (TABS.includes(h)) showTab(h);
  });
  // In-page links that point at a section of the Try tab scroll smoothly instead of switching tabs.
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href="#choose"]');
    if (!a) return;
    e.preventDefault();
    showTab("try", { scroll: false });
    $("#choose").scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "start" });
  });

  $("#theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("a2p-theme-v2", next, localStorage);
  };

  /* ================================================================ reveals + counters */
  const io = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => entries.forEach((e) => {
        if (!e.isIntersecting) return;
        e.target.classList.add("in");
        io.unobserve(e.target);
      }), { rootMargin: "0px 0px -6% 0px", threshold: 0.06 })
    : null;
  function observe(root = document) { $$(".reveal:not(.in)", root).forEach((el) => (io ? io.observe(el) : el.classList.add("in"))); }

  function countUp(el) {
    const to = +el.dataset.count;
    if (reduceMotion.matches || !to) return;
    const t0 = performance.now(), d = 1200;
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(to * e).toLocaleString();
      if (p < 1) requestAnimationFrame(tick);
    };
    el.textContent = "0";
    requestAnimationFrame(tick);
  }
  if (io) {
    const figIO = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return;
      $$("[data-count]", e.target).forEach(countUp);
      figIO.unobserve(e.target);
    }), { threshold: 0.4 });
    figIO.observe($("#figures"));
  }

  /* ================================================================ hero: one saved run, on a loop */
  const Hero = (() => {
    const card = $(".hero-run"), list = $("#hr-steps"), out = $("#hr-out"), clock = $("#hr-clock");
    // Defaults are the real numbers from data/sample_runs/ransomware-precursor.json; replaced by the loaded run.
    let D = {
      ms: { screen: 192, triage: 1413, retrieve: 1419, map_attack: 858, enrich: 0, write_playbook: 2984, critic: 359 },
      mapping: [["T1486", "Data Encrypted for Impact"], ["T1490", "Inhibit System Recovery"]],
      severity: "critical", planSteps: 14, verdict: "approve",
    };
    let gen = 0, visible = true, playing = false;

    function build() {
      list.innerHTML = C.STEPS.map((s) => `<li data-id="${s.id}"><span class="hr-n"><svg viewBox="0 0 24 24"><path pathLength="1" d="m5 12.5 4.5 4.5L19 7.5"/></svg></span><span class="hr-name">${esc(s.name)}</span><span class="hr-ms">${s.id === "finalize" ? "your turn" : fmtMs(D.ms[s.id] || 0)}</span></li>`).join("");
      out.innerHTML = D.mapping.map(([id, name], i) => `<span class="chip" style="--i:${i}"><b>${esc(id)}</b> ${esc(name)}</span>`).join("")
        + `<span class="tag sev ${esc(D.severity)}" style="--i:${D.mapping.length}">${esc(D.severity)}</span>`
        + `<span class="tag" style="--i:${D.mapping.length + 1}">${D.planSteps}-step plan</span>`
        + `<span class="tag ok" style="--i:${D.mapping.length + 2}">${icon("check")}${D.verdict === "approve" ? "reviewed" : "has notes"}</span>`;
    }
    function finalState() {
      $$("li", list).forEach((li) => (li.className = "done"));
      out.classList.add("show");
      clock.textContent = (Object.values(D.ms).reduce((a, b) => a + b, 0) / 1000).toFixed(1) + " s";
    }
    const active = () => visible && !document.hidden && $("#panel-try").classList.contains("active");

    async function loop(my) {
      playing = true;
      const total = () => Object.values(D.ms).reduce((a, b) => a + b, 0);
      while (my === gen) {
        card.classList.add("fade");
        await sleep(450);
        if (my !== gen) break;
        $$("li", list).forEach((li) => (li.className = ""));
        out.classList.remove("show");
        clock.textContent = "0.0 s";
        card.classList.remove("fade");
        await sleep(500);
        let elapsed = 0;
        for (const li of $$("li", list)) {
          if (my !== gen) break;
          const id = li.dataset.id;
          const real = D.ms[id] ?? 0;
          const dur = id === "finalize" ? 500 : Math.min(1500, Math.max(380, real / 1.7));
          li.className = "run";
          const t0 = performance.now();
          await new Promise((res) => {
            const tick = (t) => {
              if (my !== gen) return res();
              const p = Math.min(1, Math.max(0, (t - t0) / dur));
              clock.textContent = ((elapsed + real * p) / 1000).toFixed(1) + " s";
              p < 1 ? requestAnimationFrame(tick) : res();
            };
            requestAnimationFrame(tick);
          });
          elapsed += real;
          li.className = "done";
        }
        if (my !== gen) break;
        clock.textContent = (total() / 1000).toFixed(1) + " s";
        out.classList.add("show");
        await sleep(4200);
        while (my === gen && !active()) await sleep(400);
      }
      if (my === gen) playing = false;
    }
    function start() {
      build();
      if (reduceMotion.matches) return finalState();
      gen++; loop(gen);
    }
    function check() { if (reduceMotion.matches) return; if (active() && !playing) { gen++; loop(gen); } }
    function setRun(run) {
      if (!run) return;
      const ms = {};
      for (const s of run.steps || []) ms[s.node] = (ms[s.node] || 0) + (s.ms || 0);
      D = {
        ms,
        mapping: (run.mapping || []).map((m) => [m.technique_id, m.name]),
        severity: run.triage?.severity || "high",
        planSteps: C.PHASES.reduce((a, [k]) => a + (run.playbook?.[k]?.length || 0), 0),
        verdict: run.review?.verdict || "approve",
      };
      gen++; build();
      if (reduceMotion.matches) return finalState();
      loop(gen);
    }
    if (io) new IntersectionObserver((es) => { visible = es[0].isIntersecting; check(); }, { threshold: 0.15 }).observe(card);
    document.addEventListener("visibilitychange", check);
    return { start, setRun, check };
  })();

  /* ================================================================ demo code */
  const serverLocked = () => S.health?.live_runs === "need demo code";
  const locked = () => serverLocked() && !S.code;
  function renderLock() {
    const chip = $("#lock-chip");
    if (!serverLocked()) { chip.hidden = true; return; }
    chip.hidden = false;
    chip.classList.toggle("on", !!S.code);
    chip.innerHTML = `${icon(S.code ? "unlock" : "lock")}<span class="lock-text">${S.code ? "Live runs unlocked" : "Live runs locked"}</span>`;
    chip.title = S.code ? "Click to lock again" : "Live runs need a demo code. Saved runs always work.";
    chip.setAttribute("aria-label", chip.title);
  }
  $("#lock-chip").onclick = () => {
    if (S.code) { S.code = null; store.set("a2p-code", null); renderLock(); updateStart(); return; }
    S.wantCode = true;
    showTab("try", { scroll: false });
    updateStart();
    $("#code-area").scrollIntoView({ block: "center", behavior: reduceMotion.matches ? "auto" : "smooth" });
    setTimeout(() => $("#code").focus({ preventScroll: true }), 350);
  };
  async function unlock() {
    const code = $("#code").value.trim();
    if (!code) return;
    const msg = $("#code-msg");
    try {
      const r = await fetch("/api/unlock", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
      if (r.ok) {
        S.code = code; store.set("a2p-code", code);
        S.wantCode = false; S.flash = "Unlocked. Live runs work until you close this tab.";
        msg.innerHTML = ""; $("#code").value = "";
        renderLock(); updateStart(); renderPicks(true);
      } else {
        const d = await r.json().catch(() => ({}));
        msg.innerHTML = `<div class="note bad">${icon("warn")}<span>${esc(d.detail || "That code did not work.")}</span></div>`;
      }
    } catch { msg.innerHTML = `<div class="note bad">${icon("warn")}<span>Could not reach the server.</span></div>`; }
  }
  $("#code-go").onclick = unlock;
  $("#code").addEventListener("keydown", (e) => { if (e.key === "Enter") unlock(); });

  /* ================================================================ 01 choose */
  function renderPicks(keep = false) {
    const own = serverLocked() && !S.code ? "Runs live · needs the demo code" : "Runs live";
    $("#picks").innerHTML = S.samples.map((s, i) => `
      <button class="q-item" type="button" data-i="${i}" aria-pressed="false">
        <span class="q-ico">${icon(C.SAMPLE_ICONS[s.id] || "scan")}</span>
        <span class="q-text"><span class="q-title">${esc(s.simple_title || s.title)}</span><span class="q-meta">${esc(s.where || "")} · saved run</span></span>
        ${icon("chevron-right", "i q-chev")}
      </button>`).join("") + `
      <div class="q-sep" role="presentation"></div>
      <button class="q-item own" type="button" data-own="1" aria-pressed="false">
        <span class="q-ico">${icon("plus")}</span>
        <span class="q-text"><span class="q-title">Build your own alert</span><span class="q-meta">${own}</span></span>
        ${icon("chevron-right", "i q-chev")}
      </button>`;
    $$(".q-item").forEach((b) => (b.onclick = () => (b.dataset.own ? pickOwn(b) : pickSample(+b.dataset.i, b))));
    if (keep && S.picked) {
      const btn = S.picked === "own" ? $(".q-item.own") : $(`.q-item[data-i="${S.samples.indexOf(S.picked)}"]`);
      pressed(btn);
    }
  }
  function pressed(btn) {
    $$(".q-item").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    // On narrow screens the list scrolls sideways: bring the chosen card into view without moving the page.
    const q = $("#picks");
    if (btn && narrow.matches && (btn.offsetLeft < q.scrollLeft || btn.offsetLeft + btn.offsetWidth > q.scrollLeft + q.clientWidth)) {
      q.scrollTo({ left: btn.offsetLeft - parseFloat(getComputedStyle(q).paddingLeft), behavior: reduceMotion.matches ? "auto" : "smooth" });
    }
  }
  function setContext(where, story) {
    const w = $("#pick-where"), st = $("#pick-story");
    w.hidden = !where; w.textContent = where || "";
    st.hidden = !story; st.textContent = story || "";
  }
  function pickSample(i, btn) {
    if (S.busy) return;
    S.picked = S.samples[i]; S.flash = "";
    pressed(btn);
    $("#builder").hidden = true;
    setContext(S.picked.where, S.picked.story);
    setAlert(S.picked.alert);
    resetRun();
    updateStart();
  }
  function pickOwn(btn) {
    if (S.busy) return;
    S.picked = "own"; S.flash = "";
    pressed(btn);
    renderBuilder();
    $("#builder").hidden = false;
    setContext("Your alert", "");
    setAlert("");
    resetRun();
    updateStart();
    if (narrow.matches) $("#builder").scrollIntoView({ block: "start", behavior: reduceMotion.matches ? "auto" : "smooth" });
  }
  function setAlert(text) {
    $("#alert").value = text;
    $("#alert").dispatchEvent(new Event("input"));
  }
  $("#alert").addEventListener("input", () => {
    const n = $("#alert").value.length;
    $("#count").textContent = `${n.toLocaleString()} / ${MAX.toLocaleString()}`;
    $("#count").style.color = n > MAX ? "var(--danger)" : "";
    if (S.picked && S.picked !== "own" && $("#alert").value !== S.picked.alert) {
      S.picked = "own"; pressed(null);        // an edited sample is a custom alert now
      setContext("Edited alert", "");
    }
    updateStart();
  });

  /* ================================================================ alert builder */
  let kind = "win";
  function renderBuilder() {
    const B = C.BUILDER, t = B[kind];
    $("#builder").innerHTML = `
      <div class="builder-head"><h3>Build your own alert</h3><p>Pick the kind of event, fill in the boxes, then press <b>Write the alert</b>.</p></div>
      <div class="kinds" role="group" aria-label="Kind of event">${Object.entries(B).map(([k, v]) => `<button type="button" class="kind" data-kind="${k}" aria-pressed="${k === kind}" title="${esc(v.hint)}">${icon(v.icon)}${esc(v.label)}</button>`).join("")}</div>
      <div class="form-grid">
        ${t.fields.map(([key, label, ph, wide]) => `
          <label class="field ${wide ? "wide" : ""}">${esc(label)}
            ${wide ? `<textarea data-f="${key}" rows="2" placeholder="${esc(ph)}"></textarea>` : `<input data-f="${key}" placeholder="${esc(ph)}">`}
          </label>`).join("")}
      </div>
      <div class="builder-actions">
        <button class="btn" id="b-example" type="button">${icon("dice")}Fill an example</button>
        <button class="btn primary" id="b-make" type="button">Write the alert${icon("arrow-down", "i i-trail")}</button>
      </div>
      <details class="tips">
        <summary>${icon("chevron-right")}What makes a good alert</summary>
        <ol>
          <li><b>Where:</b> the computer or server name.</li>
          <li><b>Who:</b> the user or account.</li>
          <li><b>What ran:</b> the program and its exact command.</li>
          <li><b>What else:</b> anything odd around it, such as the time, a new IP address, or what happened just before or after.</li>
        </ol>
        <p>More clues give a better plan. Never paste real company data: this is a public demo.</p>
      </details>`;
    $$(".kind", $("#builder")).forEach((b) => (b.onclick = () => { kind = b.dataset.kind; renderBuilder(); }));
    let n = 0;
    $("#b-example").onclick = () => {
      const ex = t.examples[n++ % t.examples.length];
      $$("[data-f]", $("#builder")).forEach((el) => (el.value = ex[el.dataset.f] || ""));
    };
    $("#b-make").onclick = () => {
      const v = {};
      $$("[data-f]", $("#builder")).forEach((el) => (v[el.dataset.f] = el.value.trim() || el.placeholder));
      const now = new Date().toISOString().replace("T", " ").slice(0, 19) + "Z";
      setAlert(t.render(v, now));
      S.picked = "own";
      setContext(t.label, "");
      updateStart();
      $(".editor-card").scrollIntoView({ block: "center", behavior: reduceMotion.matches ? "auto" : "smooth" });
    };
  }

  /* ================================================================ start button */
  function updateStart() {
    const btn = $("#start"), note = $("#start-note");
    const text = $("#alert").value.trim();
    const valid = text.length >= 10 && text.length <= MAX;
    const isSample = S.picked && S.picked !== "own" && S.picked.run;
    $("#code-area").hidden = !(locked() && (S.wantCode || (S.picked && !isSample)));
    const set = (ic, label, disabled) => { btn.innerHTML = `${icon(ic)}<span>${label}</span>`; btn.disabled = disabled; };
    const flash = S.flash ? `<b>${esc(S.flash)}</b> ` : "";
    btn.classList.toggle("busy", S.busy);
    if (S.busy) { set("rotate", "Working…", true); return; }
    if (!S.picked) { set("play", "Start the agents", true); note.textContent = "Choose an alert first."; return; }
    if (isSample && locked()) {
      set("play", "Watch a saved run", false);
      note.innerHTML = "Plays back a real run saved earlier, step by step. No AI is called. Live runs need the demo code (the lock at the top).";
    } else if (locked()) {
      set("lock", "Enter the demo code to run live", true);
      note.textContent = "Your own alert needs a live run, and live runs use paid AI credit. Enter the demo code below.";
    } else {
      set("bolt", "Run the agents live", !valid);
      note.innerHTML = flash + (valid ? "The agents work on this alert now. It takes about 7 to 15 seconds." : "Write or build an alert first (at least 10 characters).");
    }
  }
  $("#start").onclick = () => {
    const isSample = S.picked && S.picked !== "own" && S.picked.run;
    if (isSample && locked()) return replay(S.picked.run);
    return liveRun("/api/run", { alert: $("#alert").value.trim() }, []);
  };

  /* ================================================================ 02 the timeline */
  function buildSteps() {
    $("#steps").innerHTML = C.STEPS.map((st, i) => `
      <li class="step" data-step="${st.id}" data-state="">
        <button class="stp" type="button" aria-label="Step ${i + 1}, ${esc(st.name)}. Open what went in and out.">
          <span class="st">${icon(st.icon)}<svg class="st-check" viewBox="0 0 24 24" aria-hidden="true"><path pathLength="1" d="m5 12.5 4.5 4.5L19 7.5"/></svg><span class="st-ring"></span></span>
          <span class="stp-name"><b>${esc(st.name)}</b><span class="stp-who">${esc(st.who)}</span></span>
          <span class="stp-result"></span>
          <span class="stp-time"></span>
          ${icon("chevron-right", "i stp-chev")}
        </button>
      </li>`).join("");
    $$(".step").forEach((li) => ($(".stp", li).onclick = () => openStep(li.dataset.step, S.run, S.steps)));
  }

  function resultLine(id) {
    const r = S.run || {};
    const last = (S.steps || []).filter((s) => s.node === id).slice(-1)[0];
    switch (id) {
      case "screen": return last?.score == null ? "" : last.flagged ? `Hidden instructions found (score ${last.score.toFixed(2)})` : `Clean (score ${last.score.toFixed(3)})`;
      case "triage": return r.triage ? `Severity ${r.triage.severity}. ${r.triage.behaviors?.length || 0} suspicious actions.` : "";
      case "retrieve": return r.candidates ? `${r.candidates.length} candidate techniques` : "";
      case "map_attack": return r.mapping ? `Named ${r.mapping.map((m) => m.technique_id).join(", ")}` : "";
      case "enrich": return last ? `${last.graph_nodes} pieces of official advice linked` : "";
      case "write_playbook": return r.playbook ? `Plan written, ${C.PHASES.reduce((a, [k]) => a + (r.playbook[k]?.length || 0), 0)} steps` : "";
      case "critic": return r.review ? (r.review.verdict === "approve" ? "Approved" : "Sent back with notes") : "";
      case "finalize": return r.status ? "Your turn: approve, change or download" : "";
      default: return "";
    }
  }

  function paintSteps(running) {
    const byNode = {};
    for (const s of S.steps) (byNode[s.node] ||= []).push(s);
    let done = 0;
    for (const st of C.STEPS) {
      const li = $(`.step[data-step="${st.id}"]`);
      const runs = byNode[st.id] || [];
      let state = runs.length ? "done" : "";
      if (st.id === "screen" && runs.some((r) => r.flagged)) state = "flag";
      if (st.id === running) state = "running";
      if (st.id === "finalize" && runs.length) state = "waiting";
      if (runs.length) done++;
      if (li.dataset.state !== state) li.dataset.state = state;
      const res = $(".stp-result", li), text = resultLine(st.id);
      if (res.textContent !== text) {
        res.textContent = text;
        res.classList.remove("fresh");
        if (text) { void res.offsetWidth; res.classList.add("fresh"); }
      }
      const ms = runs.reduce((a, r) => a + (r.ms || 0), 0);
      $(".stp-time", li).textContent = runs.length && st.id !== "finalize" ? fmtMs(ms) + (runs.length > 1 ? ` ×${runs.length}` : "") : state === "running" ? "…" : "";
    }
    $("#run-progress").style.setProperty("--p", String(done / C.STEPS.length));
    $(".run").classList.toggle("finished", done === C.STEPS.length);

    const llm = S.steps.filter((s) => s.input_tokens != null);
    if (!S.steps.length) { $("#totals").hidden = true; return; }
    const total = S.steps.reduce((a, s) => a + (s.ms || 0), 0);
    const tok = llm.reduce((a, s) => a + s.input_tokens + s.output_tokens, 0);
    const finished = done === C.STEPS.length && !running;
    $("#totals").hidden = false;
    $("#totals").innerHTML = `
      <span>${icon("clock")}Total <b>${fmtMs(total)}</b></span>
      <span>${icon("chip")}AI calls <b>${llm.length}</b></span>
      <span>${icon("hash")}Tokens <b>${tok.toLocaleString()}</b></span>
      ${S.replay ? `<span>${icon("history")}Saved run${S.run?.saved_at ? " from " + esc(S.run.saved_at) : ""}</span>` : `<span class="live">${icon("bolt")}Live run</span>`}
      ${finished ? `<span class="spacer"></span><button class="link-btn" type="button" id="to-plan">Read the plan${icon("arrow-down")}</button>` : ""}`;
    if (finished) $("#to-plan").onclick = () => $("#plan").scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "start" });
  }

  /* what went in / came out, built from the run state */
  function stepIO(id, run, steps) {
    const st = (steps || []).filter((s) => s.node === id);
    const last = st[st.length - 1] || {};
    const r = run || {};
    const ids = (xs) => (xs || []).map((m) => m.technique_id || m.id);
    switch (id) {
      case "screen": return { in: { alert: r.alert }, out: r.guard || null };
      case "triage": return { in: { alert: r.alert }, out: r.triage || null };
      case "retrieve": return { in: { search_phrases: last.queries || "(not stored in this saved run)" }, out: r.candidates ? r.candidates.map((c) => ({ id: c.id, name: c.name, found_by: c.sources })) : null };
      case "map_attack": return { in: { alert: "(the alert)", triage_summary: r.triage?.summary, candidates: (r.candidates || []).map((c) => `${c.id} ${c.name}`) }, out: r.mapping ? { mapping: r.mapping, rejected_by_code: last.rejected_ids || [] } : null };
      case "enrich": return { in: { techniques: ids(r.mapping) }, out: r.context ? { allowed_references: r.context.allowed_refs, graph: (r.context.techniques || []).map((t) => ({ id: t.id, name: t.name, mitigations: t.mitigations.map((m) => `${m.id} ${m.name}`), detections: t.detections.map((d) => `${d.id} ${d.name}`), d3fend: Object.fromEntries(Object.entries(t.d3fend).map(([k, v]) => [k, v.map((x) => `${x.id} ${x.name}`)])) })) } : null };
      case "write_playbook": return { in: { alert: "(the alert)", triage: r.triage?.summary, techniques: ids(r.mapping), allowed_references: r.context?.allowed_refs, analyst_feedback: r.feedback_history || [] }, out: r.playbook || null };
      case "critic": return { in: { playbook: "(the plan from step 6)", allowed_references: r.context?.allowed_refs }, out: r.review || null };
      case "finalize": return { in: { review: r.review?.verdict }, out: { status: r.status, note: "Any reference not in the allowed list was removed before you see the plan." } };
      default: return { in: null, out: null };
    }
  }

  /* ================================================================ drawer */
  const drawer = $("#drawer"), overlay = $("#overlay");
  let lastFocus = null;
  function openStep(id, run, steps) {
    const meta = C.STEPS.find((s) => s.id === id);
    const i = C.STEPS.indexOf(meta);
    const st = (steps || []).filter((s) => s.node === id);
    const ioData = stepIO(id, run, steps);
    const timing = st.length
      ? st.map((s) => `${s.model ? esc(s.model) + " · " : ""}${fmtMs(s.ms || 0)}${s.input_tokens != null ? ` · ${(s.input_tokens + s.output_tokens).toLocaleString()} tokens` : ""}${s.fallback_from ? ` · backup model used (first try: ${esc(s.fallback_from)})` : ""}`).join("<br>")
      : "Not run yet";
    $("#drawer-ico").innerHTML = icon(meta.icon);
    $("#drawer-kicker").textContent = `Step ${i + 1} of ${C.STEPS.length}`;
    $("#drawer-title").textContent = meta.name;
    $("#drawer-body").innerHTML = `
      <p>${esc(meta.plain)}</p>
      <dl class="kv">
        <dt>Who does it</dt><dd>${esc(meta.who)}</dd>
        <dt>Connection</dt><dd>${esc(meta.link)}</dd>
        <dt>This run</dt><dd class="mono xs">${timing}</dd>
      </dl>
      <div class="seg" role="group" aria-label="Input or output">
        <button type="button" data-io="in" aria-pressed="false">${icon("arrow-down")}What went in</button>
        <button type="button" data-io="out" aria-pressed="false">${icon("arrow-right")}What came out</button>
      </div>
      <div id="io-view"></div>`;
    const ran = st.length > 0 && run;
    const show = (which) => {
      $$("[data-io]", drawer).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.io === which)));
      const v = ioData[which];
      $("#io-view").innerHTML = !ran || v == null
        ? `<div class="note info">${icon("info")}<span>This step has not run yet. Choose an alert and press Start, then open this step again.</span></div>`
        : codeBlock(v);
    };
    $$("[data-io]", drawer).forEach((b) => (b.onclick = () => show(b.dataset.io)));
    show(ioData.out ? "out" : "in");
    $("#drawer-body").scrollTop = 0;
    openDrawer();
  }
  function openDrawer() {
    lastFocus = document.activeElement;
    drawer.style.transform = "";
    drawer.classList.add("open"); overlay.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    setTimeout(() => $("#drawer-close").focus({ preventScroll: true }), 60);
  }
  function closeDrawer() {
    if (!drawer.classList.contains("open")) return;
    drawer.style.transform = "";
    drawer.classList.remove("open"); overlay.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    lastFocus?.focus?.({ preventScroll: true });
  }
  $("#drawer-close").onclick = closeDrawer;
  overlay.onclick = closeDrawer;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  // On phones the drawer is a bottom sheet: drag the handle down, or flick it, to close.
  (() => {
    let drag = null;
    const head = $(".drawer-head"), grip = $(".drawer-grip");
    const down = (e) => {
      if (!phone.matches || e.target.closest("button")) return;
      drag = { y: e.clientY, t: performance.now(), dy: 0 };
      drawer.style.transition = "none";
      e.currentTarget.setPointerCapture?.(e.pointerId);
    };
    const move = (e) => {
      if (!drag) return;
      const raw = e.clientY - drag.y;
      drag.dy = raw > 0 ? raw : raw / 6;             // resist dragging upwards
      drawer.style.transform = `translateY(${drag.dy}px)`;
    };
    const up = () => {
      if (!drag) return;
      const v = drag.dy / Math.max(1, performance.now() - drag.t);
      drawer.style.transition = "";
      if (drag.dy > 120 || v > 0.5) closeDrawer(); else drawer.style.transform = "";
      drag = null;
    };
    for (const el of [head, grip]) {
      el.addEventListener("pointerdown", down);
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
    }
  })();

  /* ================================================================ running */
  const NEXT = { screen: "triage", triage: "retrieve", retrieve: "map_attack", map_attack: "enrich", enrich: "write_playbook", write_playbook: "critic" };

  function applyUpdate(node, update) {
    S.run = S.run || {};
    for (const [k, v] of Object.entries(update || {})) {
      if (k === "steps") S.steps = S.steps.concat(v);
      else S.run[k] = v;
    }
  }
  function resetRun() { S.run = null; S.steps = []; S.replay = false; paintSteps(null); renderResultEmpty(); }
  function toWatch() { $("#watch").scrollIntoView({ block: "start", behavior: reduceMotion.matches ? "auto" : "smooth" }); }

  async function replay(saved) {
    S.busy = true; S.replay = true; updateStart();
    S.run = { alert: saved.alert }; S.steps = [];
    renderResultLoading("Playing back a saved run…");
    const order = saved.steps || [];
    paintSteps(order[0]?.node || "screen");
    toWatch();
    for (let i = 0; i < order.length; i++) {
      const s = order[i];
      await sleep(Math.min(1400, Math.max(450, (s.ms || 0) / 2)));
      S.steps.push(s);
      // reveal the state fields this step produced
      const reveal = { screen: ["guard"], triage: ["triage"], retrieve: ["candidates"], map_attack: ["mapping"], enrich: ["context"], write_playbook: ["playbook"], critic: ["review", "revisions"] }[s.node] || [];
      for (const k of reveal) S.run[k] = saved[k];
      paintSteps(order[i + 1]?.node || (s.node === "critic" ? "finalize" : null));
    }
    await sleep(400);
    S.run = saved; S.steps.push({ node: "finalize", ms: 0 });
    paintSteps(null);
    renderResult(S.run);
    S.busy = false; updateStart();
  }

  async function liveRun(url, body, baseSteps, firstNode = "screen") {
    S.busy = true; S.replay = false; S.flash = ""; updateStart();
    if (!baseSteps.length) { S.run = { alert: body.alert }; S.steps = []; }
    paintSteps(firstNode);
    renderResultLoading("The agents are working…");
    toWatch();
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-Demo-Code": S.code || "" }, body: JSON.stringify(body) });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        if (res.status === 401) { S.code = null; store.set("a2p-code", null); renderLock(); }
        throw new Error(typeof d.detail === "string" ? d.detail : res.statusText);
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let k;
        while ((k = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
          const ev = /^event: (.*)$/m.exec(chunk)?.[1], data = /^data: (.*)$/m.exec(chunk)?.[1];
          if (!ev || !data) continue;
          const d = JSON.parse(data);
          if (ev === "node") {
            applyUpdate(d.node, d.update);
            let next = NEXT[d.node];
            if (d.node === "critic") next = d.update.review?.verdict === "revise" && (S.run.revisions || 0) < 2 ? "write_playbook" : "finalize";
            if (d.node === "finalize") S.steps.push({ node: "finalize", ms: 0 });
            paintSteps(d.node === "finalize" ? null : next);
          } else if (ev === "done") {
            const keep = S.steps;
            S.run = d.state; S.steps = keep;
            paintSteps(null); renderResult(S.run);
          } else if (ev === "error") {
            paintSteps(null); renderResultError(d.message);
          }
        }
      }
    } catch (e) {
      paintSteps(null); renderResultError(e.message);
    } finally { S.busy = false; updateStart(); }
  }

  /* ================================================================ 03 result */
  function renderResultEmpty() {
    $("#result").innerHTML = `<div class="empty"><span class="empty-ico">${icon("scan")}</span><p><b>No plan yet.</b> Choose an alert above and press Start. The plan appears here when the agents finish.</p></div>`;
  }
  function renderResultLoading(note) {
    $("#result").innerHTML = `<div class="loading"><p>${esc(note)}</p>${[92, 70, 84, 60, 76].map((w) => `<div class="skeleton" style="width:${w}%"></div>`).join("")}</div>`;
  }
  function renderResultError(m) {
    $("#result").innerHTML = `<div class="banner bad">${icon("warn")}<div>${esc(m)}</div></div><div class="empty"><p>The sample alerts always work, even without the demo code.</p></div>`;
  }

  function refIndex(r) {
    const idx = {};
    for (const t of r.context?.techniques || []) {
      idx[t.id] = { name: t.name, url: t.url };
      if (t.parent) idx[t.parent] = { name: t.parent, url: `https://attack.mitre.org/techniques/${t.parent}/` };
      for (const m of t.mitigations) idx[m.id] = m;
      for (const d of t.detections) idx[d.id] = d;
      for (const items of Object.values(t.d3fend)) for (const c of items) idx[c.id] = c;
    }
    return idx;
  }

  function moveRInd() {
    const ind = $(".rtab-ind"), act = $(".rtab[aria-selected='true']");
    if (!ind || !act) return;
    ind.style.setProperty("--x", act.offsetLeft + "px");
    ind.style.setProperty("--w", act.offsetWidth + "px");
  }

  let rtab = "what";
  function renderResult(r) {
    const t = r.triage, pb = r.playbook, rv = r.review, idx = refIndex(r);
    const ref = (x) => idx[x] ? `<a class="ref" href="${esc(idx[x].url)}" target="_blank" rel="noopener" title="${esc(idx[x].name)}">${esc(x)}</a>` : `<span class="ref">${esc(x)}</span>`;
    const tabs = [["what", "scan", "What happened"], ["attacks", "target", "Techniques"], ["plan", "list-check", "Response plan"], ["hunt", "search", "Where to look next"], ["check", "check-circle", "Double-check"]];
    const obs = t.observables || {};
    const list = (xs) => (xs?.length ? `<div class="chips">${xs.slice(0, 8).map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : `<span class="muted small">none in the alert</span>`);
    const conf = (c) => (c === "high" ? "ok" : c === "medium" ? "sev medium" : "");
    const views = {
      what: `
        <p class="lede">${esc(t.summary)}</p>
        <p class="muted">${esc(pb.executive_summary)}</p>
        <p class="small"><b>Why this severity:</b> ${esc(t.severity_reason)}</p>
        <div class="facts">
          <div class="fact"><small>Computers</small>${list(obs.hosts)}</div>
          <div class="fact"><small>Accounts</small>${list(obs.users)}</div>
          <div class="fact"><small>Programs and commands</small>${list(obs.processes)}</div>
          <div class="fact"><small>Network addresses</small>${list(obs.network)}</div>
        </div>
        <p class="subhead">What the attacker seems to be doing</p>
        <ul class="behaviors">${(t.behaviors || []).map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`,
      attacks: `
        <p class="muted">The MITRE ATT&amp;CK techniques this alert shows. The grey box is the exact proof, quoted from the alert.</p>
        ${r.mapping.map((m) => {
          const tech = (r.context?.techniques || []).find((x) => x.id === m.technique_id);
          const first = (tech?.description || "").split(". ")[0];
          return `<div class="tech"><a class="tech-id" href="${esc(idx[m.technique_id]?.url || "#")}" target="_blank" rel="noopener">${esc(m.technique_id)}</a>
            <div class="tech-body"><div class="row" style="gap:8px"><b>${esc(m.name)}</b><span class="tag ${conf(m.confidence)}">${esc(m.confidence)} confidence</span></div>
            ${first ? `<p>${esc(first)}.</p>` : ""}
            <div class="quote">${esc(m.evidence)}</div></div></div>`;
        }).join("")}`,
      plan: `
        <div class="ref-legend"><span>The small codes are the official advice each step is based on. Click one to open MITRE's page.</span>${C.REF_TYPES.map(([k, v]) => `<span><b>${k}</b> ${v}</span>`).join("")}</div>
        ${C.PHASES.map(([k, ic, title], i) => `<div class="phase"><span class="ph-ico">${icon(ic)}</span><div><h4><span>${pad(i + 1)}</span>${title}</h4><ol>${(pb[k] || []).map((s) => `<li><b>${esc(s.action)}</b><p>${esc(s.detail)}</p>${s.references?.length ? `<span class="refs">${s.references.map(ref).join("")}</span>` : ""}</li>`).join("")}</ol></div></div>`).join("")}`,
      hunt: `
        <p class="muted">Where to search next for more signs of the same attacker. The first line names the log to search.</p>
        <ol class="hunt">${(pb.hunt_leads || []).map((h) => `<li><b>${esc(h.log_source)}</b><span>${esc(h.look_for)}</span></li>`).join("")}</ol>`,
      check: `
        <div class="verdict"><span class="tag ${rv.verdict === "approve" ? "ok" : "sev high"}">${icon(rv.verdict === "approve" ? "check" : "warn")}${rv.verdict === "approve" ? "Approved by the reviewer" : "The reviewer still has notes"}</span>
        <span class="muted small">after ${r.revisions || 0} rewrite${r.revisions === 1 ? "" : "s"}</span></div>
        ${rv.verdict === "approve" ? `<p class="muted">The code checks passed (every reference is real, every part has steps) and the reviewer model found no real problem.</p>` : `<ul class="behaviors">${rv.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`}
        ${r.feedback_history?.length ? `<p class="subhead">Changes you asked for</p><ol class="behaviors">${r.feedback_history.map((f) => `<li>${esc(f)}</li>`).join("")}</ol>` : ""}`,
    };
    $("#result").innerHTML = `
      <div class="result-in">
        ${r.guard?.flagged ? `<div class="banner bad">${icon("message-alert")}<div><b>The safety check found hidden instructions in this alert</b> (score ${r.guard.score.toFixed(3)}). Someone wrote orders for the AI inside the alert. The agents ignored them, but read this plan with extra care.</div></div>` : ""}
        ${S.replay ? `<div class="banner info">${icon("history")}<div>This is a <b>saved run</b>, played back. Unlock with the demo code to run the agents live on any alert.</div></div>` : ""}
        <div class="res-head">
          <div class="tags"><span class="tag sev ${esc(t.severity)}">${esc(t.severity)} severity</span><span class="tag">${esc(t.platform)}</span></div>
          <h3>${esc(pb.title)}</h3>
        </div>
        <div class="rtabs" role="tablist" aria-label="Parts of the result"><span class="rtab-ind" aria-hidden="true"></span>${tabs.map(([k, ic, l]) => `<button class="rtab" role="tab" type="button" data-r="${k}" aria-selected="${k === rtab}">${icon(ic)}${l}</button>`).join("")}</div>
        <div class="rview" id="rview">${views[rtab]}</div>
        <div class="decide">
          <p class="decide-text" id="decision"><b>Your decision.</b> The plan is waiting for a person. Nothing has been done to any computer.</p>
          <div class="row">
            <button class="btn primary" id="approve" type="button">${icon("check")}Approve</button>
            <button class="btn" id="change" type="button">${icon("message")}Ask for changes</button>
            <button class="btn" id="download" type="button">${icon("download")}Download</button>
          </div>
        </div>
        <div class="fb" id="fb" hidden>
          <label class="field">What should change? Write it the way you would tell a colleague.
            <textarea id="fbtext" maxlength="1000" placeholder="Example: We use Microsoft Defender to isolate computers. Also add a step to tell the finance manager."></textarea>
          </label>
          <div class="row"><button class="btn primary" id="fbsend" type="button">${icon("pen")}Send to the writer</button><span class="small muted">The writer and the reviewer run again (steps 6 and 7).</span></div>
        </div>
      </div>`;
    requestAnimationFrame(moveRInd);
    $$(".rtab").forEach((b) => (b.onclick = () => {
      rtab = b.dataset.r;
      $$(".rtab").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      $("#rview").outerHTML = `<div class="rview" id="rview">${views[rtab]}</div>`;
      moveRInd();
    }));
    $("#approve").onclick = () => {
      $("#decision").innerHTML = `<b>Approved at ${esc(new Date().toLocaleTimeString())}.</b> Download it to hand it to the team.`;
      $("#approve").disabled = true;
    };
    $("#change").onclick = () => {
      if (locked()) { $("#decision").innerHTML = "<b>Needs the demo code.</b> Asking for changes runs the writer again live (the lock at the top)."; return; }
      $("#fb").hidden = !$("#fb").hidden;
      if (!$("#fb").hidden) $("#fbtext").focus();
    };
    $("#download").onclick = download;
    $("#fbsend").onclick = () => {
      const text = $("#fbtext").value.trim();
      if (text.length < 3) return;
      liveRun("/api/feedback", { state: S.run, feedback: text }, S.steps, "write_playbook");
    };
  }

  async function download() {
    if (!S.run) return;
    const res = await fetch("/api/markdown", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: S.run }) });
    const blob = new Blob([await res.text()], { type: "text/markdown" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "response-plan.md" });
    a.click(); URL.revokeObjectURL(a.href);
  }

  /* ================================================================ how it works */
  const nodeById = (id) => C.NODES.find((n) => n.id === id);
  function nodeBtn(n, { area = "", inner = false } = {}) {
    return `<button class="arch-node${inner ? " inner" : ""}" type="button" data-node="${n.id}" ${area ? `data-area="${area}"` : ""} aria-pressed="false">
      <span class="a-ico">${icon(n.icon)}</span><b>${esc(n.name)}</b><span class="a-sub">${esc(n.short)}</span></button>`;
  }
  function renderArch() {
    const simple = (id) => nodeBtn(nodeById(id), { area: nodeById(id).area });
    $("#map").innerHTML = `<svg class="arch-lines" id="arch-lines" aria-hidden="true"></svg>
      ${simple("you")}${simple("web")}
      <div class="arch-group" data-area="server" data-node-group="server">
        ${nodeBtn(nodeById("server"))}
        <p class="arch-group-label">inside the server</p>
        ${nodeBtn(nodeById("kg"), { inner: true })}
      </div>
      ${simple("groq")}${simple("pinecone")}${simple("langsmith")}${simple("github")}`;
    $$(".arch-node").forEach((b) => (b.onclick = () => {
      showNode(b.dataset.node);
      if (narrow.matches) $("#map-detail").scrollIntoView({ block: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" });
    }));
  }
  let selectedNode = "server";
  function showNode(id) {
    selectedNode = id;
    const n = nodeById(id);
    $$(".arch-node").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.node === id)));
    $$("#arch-lines .e").forEach((g) => g.classList.toggle("on", g.dataset.a === id || g.dataset.b === id));
    const d = $("#map-detail");
    d.innerHTML = `<div class="detail-in"><h3>${icon(n.icon)}${esc(n.name)}</h3><p>${esc(n.detail)}</p><p class="tech-line"><b>Technical:</b> ${esc(n.tech)}</p></div>`;
  }
  function drawEdges() {
    const svg = $("#arch-lines"), map = $("#map");
    if (!svg || narrow.matches || !map.offsetWidth) return;
    const box = map.getBoundingClientRect();
    const el = (id) => (id === "server" ? $('[data-node-group="server"]') : $(`.arch-node[data-node="${id}"]`)).getBoundingClientRect();
    const outs = {};
    svg.innerHTML = C.EDGES.map(([a, b, label]) => {
      const A = el(a), B = el(b);
      outs[a] = (outs[a] || 0) + 1;
      // Spread several lines leaving the same box, and aim them at their target's height.
      let y1 = Math.min(Math.max(B.top + B.height / 2, A.top + 18), A.bottom - 18) - box.top;
      if (a === "you") y1 = A.top + A.height / 2 - box.top + (b === "web" ? -10 : 10);
      const x1 = A.right - box.left, x2 = B.left - box.left, y2 = B.top + B.height / 2 - box.top;
      const dx = (x2 - x1) / 2;
      const d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
      return `<g class="e" data-a="${a}" data-b="${b}"><path class="edge" d="${d}"/><path class="flow" d="${d}"/><text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 7}" text-anchor="middle">${esc(label)}</text></g>`;
    }).join("");
    $$("#arch-lines .e").forEach((g) => g.classList.toggle("on", g.dataset.a === selectedNode || g.dataset.b === selectedNode));
  }

  function renderMessages() {
    $("#messages").innerHTML = C.MESSAGES.map((m) => `
      <button class="msg-row" type="button" data-msg="${m.n}" aria-pressed="false">
        <span class="m-idx">${pad(m.n)}</span><b>${esc(m.what)}</b>
        <span class="m-route">${esc(m.from)} ${icon("arrow-right")} ${esc(m.to)}</span>
        <span class="m-fmt">${esc(m.fmt)}</span>
      </button>`).join("");
    $$(".msg-row").forEach((b) => (b.onclick = () => showMsg(+b.dataset.msg)));
    showMsg(2, false);
  }
  let selectedMsg = 2;
  function placeMsgDetail() {
    const d = $("#message-detail"), row = $(`.msg-row[data-msg="${selectedMsg}"]`);
    if (narrow.matches && row) row.insertAdjacentElement("afterend", d);
    else if (d.parentElement !== $(".msgs")) $(".msgs").appendChild(d);
  }
  function showMsg(n, user = true) {
    selectedMsg = n;
    const m = C.MESSAGES.find((x) => x.n === n);
    $$(".msg-row").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.msg === n)));
    let example = m.example;
    if (n === 6 && S.storyRun) {
      const lines = S.storyRun.steps.filter((s) => s.model).map((s, i, arr) => ` ${i === arr.length - 1 ? "└─" : "├─"} ${s.node.padEnd(15)} ${s.model.padEnd(20)} ${fmtMs(s.ms).padStart(7)}  ${(s.input_tokens + s.output_tokens).toLocaleString()} tokens`);
      example = `Run "alert2playbook"  (the "Backups wiped" sample)\n` + lines.join("\n");
    }
    $("#message-detail").innerHTML = `<div class="detail-in">
      <h3>${esc(m.what)}</h3>
      <p class="m-route">${esc(m.from)} ${icon("arrow-right")} ${esc(m.to)}</p>
      <div><span class="tag">${esc(m.fmt)}</span></div>
      <p>${esc(m.explain)}</p>${codeBlock(example, true)}</div>`;
    placeMsgDetail();
    if (user && narrow.matches) $("#message-detail").scrollIntoView({ block: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" });
  }

  function renderHow() {
    renderArch();
    showNode("server");
    renderMessages();
    $("#trust").innerHTML = C.TRUST.map(([ic, h, t]) => `<div>${icon(ic)}<b>${esc(h)}</b><p>${esc(t)}</p></div>`).join("");
    $("#glossary").innerHTML = C.GLOSSARY.map(([h, t]) => `<div><dt>${esc(h)}</dt><dd>${esc(t)}</dd></div>`).join("");
  }

  function renderStory() {
    const run = S.storyRun;
    if (!run) { $("#story").innerHTML = `<li class="muted">The saved example run could not be loaded.</li>`; return; }
    // A readable preview: "key: value" lines with real text, not escaped JSON.
    const readable = (v) => {
      if (v == null) return "";
      if (typeof v !== "object") return String(v);
      if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? readable(x) : String(x))).join(", ");
      return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, " ")}: ${typeof x === "object" ? readable(x) : String(x)}`).join("\n");
    };
    const brief = (v) => {
      const s = readable(v);
      return esc(s.length > 280 ? s.slice(0, 280) + " …" : s);
    };
    $("#story").innerHTML = C.STEPS.map((st, i) => {
      const ioData = stepIO(st.id, run, run.steps);
      return `<li class="story-step reveal"><span class="s-ico">${icon(st.icon)}</span><div>
        <h3><span>${pad(i + 1)}</span>${esc(st.name)}</h3><p>${esc(st.plain)}</p>
        <div class="io"><div><small>${icon("arrow-down")}Went in</small><span class="mono">${brief(ioData.in)}</span></div><div><small>${icon("arrow-right")}Came out</small><span class="mono">${brief(ioData.out)}</span></div></div>
        <button class="link-btn" type="button" data-story="${st.id}">Open the full data${icon("arrow-right")}</button>
      </div></li>`;
    }).join("");
    $$("[data-story]").forEach((b) => (b.onclick = () => openStep(b.dataset.story, run, run.steps)));
    observe($("#story"));
  }

  /* ================================================================ results */
  let chart = null;
  function lineChart(el, { xs, series, yMin, yMax, fmtX }) {
    let first = true;
    function render(fromTab = false) {
      const W = el.clientWidth;
      if (!W) return;
      const small = W < 560;
      const H = small ? 270 : 310;
      const m = { t: 14, r: small ? 52 : 196, b: small ? 52 : 34, l: 40 };
      const tickX = (k) => (small ? String(k) : fmtX(k));
      const endText = (s) => (small ? `<tspan class="v">${s.values[s.values.length - 1]}%</tspan>` : `${esc(s.short)} <tspan class="v">${s.values[s.values.length - 1]}%</tspan>`);
      const pw = W - m.l - m.r, ph = H - m.t - m.b;
      const x = (k) => m.l + ((k - xs[0]) / (xs[xs.length - 1] - xs[0])) * pw;
      const y = (v) => m.t + (1 - (v - yMin) / (yMax - yMin)) * ph;
      const ticks = [];
      for (let v = yMin; v <= yMax; v += 10) ticks.push(v);
      const ends = series.map((s) => ({ s, y: y(s.values[s.values.length - 1]) })).sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 17);
      el.innerHTML = `
        <svg width="${W}" height="${H}" role="img" aria-label="Line chart: share of test alerts with the right technique in the shortlist, by shortlist size">
          <g class="grid">${ticks.map((v) => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>
          <g class="axis">${ticks.map((v) => `<text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v}%</text>`).join("")}
            ${xs.map((k) => `<text x="${x(k)}" y="${H - m.b + 20}" text-anchor="middle">${tickX(k)}</text>`).join("")}
            ${small ? `<text x="${m.l + pw / 2}" y="${H - 6}" text-anchor="middle">shortlist size</text>` : ""}</g>
          <line class="cross" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}"/>
          ${series.map((s) => `<g class="series">
            <path class="draw" d="${s.values.map((v, i) => `${i ? "L" : "M"}${x(xs[i])},${y(v)}`).join("")}" stroke="var(${s.color})"/>
            ${s.values.map((v, i) => `<circle class="pt" style="--i:${i}" cx="${x(xs[i])}" cy="${y(v)}" r="4" fill="var(${s.color})"/>`).join("")}
          </g>`).join("")}
          <g class="end">${ends.map(({ s, y: ly }) => `<text x="${W - m.r + 10}" y="${ly + 4}">${endText(s)}</text>`).join("")}</g>
          <rect class="hit" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" fill="transparent"/>
        </svg>
        <div class="tip" role="presentation"></div>`;
      $$(".draw", el).forEach((p) => p.style.setProperty("--len", p.getTotalLength()));
      if (!first && !fromTab) el.closest(".card")?.classList.add("static");
      first = false;
      const tip = $(".tip", el), cross = $(".cross", el), hit = $(".hit", el);
      const at = (clientX) => {
        const r = hit.getBoundingClientRect();
        const k = xs.reduce((best, kk) => (Math.abs(x(kk) - (clientX - r.left + m.l)) < Math.abs(x(best) - (clientX - r.left + m.l)) ? kk : best), xs[0]);
        const i = xs.indexOf(k), cx = x(k);
        cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
        tip.innerHTML = `<b>${fmtX(k)} results</b>` + [...series].sort((a, b) => b.values[i] - a.values[i]).map((s) => `<div><i style="background:var(${s.color})"></i><span>${esc(s.short)}</span><span class="v">${s.values[i]}%</span></div>`).join("");
        const left = cx + 14 + 190 > W ? cx - 14 - tip.offsetWidth : cx + 14;
        tip.style.left = Math.max(0, left) + "px"; tip.style.top = m.t + "px";
        el.classList.add("hover"); tip.classList.add("on");
      };
      hit.addEventListener("pointermove", (e) => at(e.clientX));
      hit.addEventListener("pointerdown", (e) => at(e.clientX));
      hit.addEventListener("pointerleave", () => { el.classList.remove("hover"); tip.classList.remove("on"); });
    }
    render();
    return { render };
  }

  function bar(label, v, b, i) {
    return `<div class="bar-row"><span>${label}</span><div class="bar-pair">
      <div class="bar"><span class="track"><span class="fill s1" style="width:${v}%;--i:${i * 2}"></span></span><span class="val">${v}%</span></div>
      <div class="bar"><span class="track"><span class="fill base" style="width:${b}%;--i:${i * 2 + 1}"></span></span><span class="val dim">${b}%</span></div>
    </div></div>`;
  }
  function renderGood() {
    const R = S.results;
    if (!R) { $("#good").innerHTML = `<div class="note info">${icon("info")}<span>The results could not be loaded.</span></div>`; return; }
    const cur = R.eval.current, base = R.eval.baseline, g = R.grounding, b = R.benchmark;
    const COLORS = ["--series-2", "--series-3", "--series-1"];
    const SHORT = ["Keyword", "Meaning", "Both (used)"];
    const series = R.retrieval.rows.map((row, i) => ({ name: row.name, short: SHORT[i] || row.name, values: row.values, color: COLORS[i] || "--series-1" }));
    $("#good").innerHTML = `
      <div class="kpis reveal">
        <div class="kpi"><div class="v">${cur.recall}<small>%</small></div><p>of ${cur.alerts} test alerts had the right technique in the shortlist of ${cur.candidates}</p></div>
        <div class="kpi"><div class="v">${cur.exact3}<small>%</small></div><p>had the exact right technique among the final picks (up to three)</p></div>
        <div class="kpi"><div class="v">${g.valid}<small> / ${g.cited}</small></div><p>references in the saved plans point to real MITRE entries</p></div>
        <div class="kpi"><div class="v">~${R.live.seconds}<small> s</small></div><p>for one live alert, from start to finished plan</p></div>
      </div>

      <section class="block">
        <header class="block-head reveal"><span class="idx">01</span><div><h2>The accuracy test</h2><p>${cur.alerts} alerts built from Atomic Red Team, a free library where every test is a real attack command with a known answer. Anything that gives the answer away was removed. Each alert is one bare command with no story around it, so this test is harder than real life.</p></div></header>
        <div class="card reveal">
          <div class="legend"><span><i style="background:var(--series-1)"></i>Current version · hybrid search, ${cur.candidates} candidates</span><span><i style="background:var(--series-base)"></i>First version · keyword search, ${base.candidates} candidates</span></div>
          <div class="bars">
            ${bar("Right technique in the shortlist", cur.recall, base.recall, 0)}
            ${bar("Right technique in the final picks", cur.exact3, base.exact3, 1)}
            ${bar("Right family in the final picks", cur.family3, base.family3, 2)}
            ${bar("Right technique picked first", cur.exact1, base.exact1, 3)}
          </div>
          <p class="fine">Why “picked first” is low: many test commands run through PowerShell, and the agent often lists PowerShell first. That is true, but it is not the labelled answer, which is usually second. This is why the plan covers up to three techniques.</p>
        </div>
      </section>

      <section class="block">
        <header class="block-head reveal"><span class="idx">02</span><div><h2>Does meaning search help?</h2><p>To compare search methods fairly, only the search step was replayed, with the same inputs every time. How often is the right technique in a shortlist of 10 to 30 results?</p></div></header>
        <div class="card reveal">
          <div class="legend">${series.map((s) => `<span><i class="line" style="background:var(${s.color})"></i>${esc(s.name)}</span>`).join("")}</div>
          <div class="chart-wrap" id="chart"></div>
          <button class="btn sm view-toggle" id="chart-table-btn" type="button" aria-expanded="false">${icon("list-check")}Show as a table</button>
          <div class="table-scroll" id="chart-table" hidden style="margin-top:12px"><table class="t"><thead><tr><th>Search method</th>${R.retrieval.ks.map((k) => `<th class="num">Top ${k}</th>`).join("")}</tr></thead>
            <tbody>${R.retrieval.rows.map((row) => `<tr><td>${esc(row.name)}</td>${row.values.map((v) => `<td class="num">${v}%</td>`).join("")}</tr>`).join("")}</tbody></table></div>
          <p class="fine">Keyword search stops improving at 20 results. Keyword and meaning together keep finding more, so the mapping agent now sees 25 candidates. At only 10 results the combined search is worse, and that is reported too.</p>
        </div>
      </section>

      <section class="block">
        <header class="block-head reveal"><span class="idx">03</span><div><h2>LangGraph vs CrewAI</h2><p>The same system built a second time with CrewAI, another popular agent framework: same models, same instructions, same search, same checks. Both ran the same ${b.alerts} sample alerts.</p></div></header>
        <div class="card reveal">
          <div class="table-scroll"><table class="t"><thead><tr><th></th>${b.columns.map((c, i) => `<th class="${i === 0 ? "hl" : ""}">${esc(c)}</th>`).join("")}</tr></thead>
          <tbody>${b.rows.map((row) => `<tr><td><b>${esc(row[0])}</b></td>${row.slice(1).map((v, i) => `<td class="${i === 0 ? "hl" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
          <p class="fine">${esc(b.note)}</p>
        </div>
      </section>

      <section class="block">
        <header class="block-head reveal"><span class="idx">04</span><div><h2>Automatic tests</h2></div></header>
        <div class="card tests reveal"><span class="t-ico">${icon("flask")}</span><p><b>${R.tests} automatic tests</b> run without internet and without any AI. They check the knowledge graph, the search, every safety guard (fake IDs are caught and removed), the review loop, change requests, the demo-code lock and the MCP tools.</p></div>
      </section>
      <p class="meta-line">${icon("clock")}<span>Measured on ${esc(R.date)}. Full method and every answer: <a href="https://github.com/M-AbubakarNadeem/Alert2Playbook/blob/main/docs/07-measuring-it.md" target="_blank" rel="noopener">docs/07-measuring-it.md</a></span></p>`;
    chart = lineChart($("#chart"), { xs: R.retrieval.ks, series, yMin: 50, yMax: 90, fmtX: (k) => `Top ${k}` });
    $("#chart-table-btn").onclick = (e) => {
      const t = $("#chart-table"), open = t.hidden;
      t.hidden = !open;
      e.currentTarget.setAttribute("aria-expanded", String(open));
      e.currentTarget.innerHTML = `${icon(open ? "chart" : "list-check")}${open ? "Hide the table" : "Show as a table"}`;
    };
    observe($("#good"));
  }

  /* ================================================================ developers */
  function renderDev() {
    const base = location.origin;
    const curlRun = `curl -N -X POST ${base}/api/run \\\n  -H "Content-Type: application/json" \\\n  -H "X-Demo-Code: YOUR_DEMO_CODE" \\\n  -d '{"alert": "Sysmon EID 1 on WS-01: schtasks /create /tn upd /tr powershell.exe /sc onlogon /ru SYSTEM"}'`;
    const a2a = `curl -X POST ${base}/a2a \\\n  -H "Content-Type: application/json" \\\n  -H "X-Demo-Code: YOUR_DEMO_CODE" \\\n  -d '{"jsonrpc": "2.0", "id": 1, "method": "SendMessage",\n       "params": {"message": {"messageId": "m1", "role": "ROLE_USER",\n                  "parts": [{"text": "auditd on web-03: www-data ran useradd -m support2"}]}}}'`;
    const EP = [
      ["GET", "/api/health", "Versions, models and search mode", false],
      ["GET", "/api/samples", "The sample alerts with their saved runs", false],
      ["POST", "/api/unlock", "Check a demo code: {\"code\"} → {\"ok\": true}", false],
      ["POST", "/api/run", "Run an alert: {\"alert\"} → live stream of steps (SSE)", true],
      ["POST", "/api/feedback", "Rewrite with a change request: {\"state\", \"feedback\"} → live stream", true],
      ["POST", "/api/markdown", "The plan as Markdown: {\"state\"}", false],
      ["GET", "/.well-known/agent-card.json", "The A2A Agent Card", false],
      ["POST", "/a2a", "A2A JSON-RPC SendMessage → a Task with two artifacts", true],
    ];
    const stack = ["Python 3.12", "LangGraph", "FastAPI", "Groq", "gpt-oss-120b", "gpt-oss-20b", "Qwen 3.8 27B", "Llama Prompt Guard 2", "Pinecone", "llama-text-embed-v2", "ATT&CK v20", "D3FEND 1.6", "LangSmith", "Pydantic", "pytest", "Vercel"];
    $("#dev").innerHTML = `
      <section class="block">
        <header class="block-head reveal"><span class="idx">01</span><div><h2>Web API</h2><p>“Demo code” means the <span class="mono">X-Demo-Code</span> header. Live runs use paid AI credit, so they are locked on this public demo. <a href="${base}/api/docs" target="_blank" rel="noopener">Interactive API docs</a></p></div></header>
        <div class="endpoints reveal">
          <div class="ep head"><span>Method</span><span>Path</span><span>What it does</span><span>Demo code</span></div>
          ${EP.map(([m, p, w, need]) => `<div class="ep"><span class="method ${m === "POST" ? "post" : ""}">${m}</span><span class="path">${esc(p)}</span><span class="what">${esc(w)}</span><span class="lockcell ${need ? "need" : ""}">${icon(need ? "lock" : "unlock")}${need ? "needed" : "open"}</span></div>`).join("")}
        </div>
        <div class="card dev-card reveal" style="margin-top:16px"><h3>${icon("terminal")}Run an alert from a terminal</h3><p class="muted" style="margin:0">Streams one line per finished step, then the full result.</p>${codeBlock(curlRun, true)}</div>
      </section>

      <section class="block">
        <header class="block-head reveal"><span class="idx">02</span><div><h2>A2A: let another agent call this one</h2><p>Agents find it through the <a href="${base}/.well-known/agent-card.json" target="_blank" rel="noopener">Agent Card</a> (A2A protocol v1.0), then send a <span class="mono">SendMessage</span> request. The answer is a completed Task with the plan as Markdown and as JSON.</p></div></header>
        <div class="card dev-card reveal">${codeBlock(a2a, true).replace('class="codewrap"', 'class="codewrap" style="margin-top:0"')}</div>
      </section>

      <section class="block">
        <header class="block-head reveal"><span class="idx">03</span><div><h2>MCP: give Claude these tools</h2><p>Clone the repository, install it, then add the server. Tools: <span class="mono">lookup_technique</span> and <span class="mono">search_techniques</span> (no keys needed), and <span class="mono">alert_to_playbook</span> (needs a Groq key).</p></div></header>
        <div class="card dev-card reveal">${codeBlock("claude mcp add alert2playbook -- python -m alert2playbook.mcp_server", true).replace('class="codewrap"', 'class="codewrap" style="margin-top:0"')}</div>
      </section>

      <section class="block">
        <header class="block-head reveal"><span class="idx">04</span><div><h2>Built with</h2></div></header>
        <div class="reveal"><div class="stack">${stack.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div>
        <p style="margin-top:18px"><a class="link-btn" href="https://github.com/M-AbubakarNadeem/Alert2Playbook" target="_blank" rel="noopener">Source code and the 11-page guide on GitHub${icon("arrow-right")}</a></p></div>
      </section>`;
  }

  /* ================================================================ resize */
  const onResize = debounce(() => {
    moveInd(false); moveRInd(); drawEdges(); placeMsgDetail();
    if ($("#panel-good").classList.contains("active")) chart?.render();
  }, 120);
  window.addEventListener("resize", onResize);

  /* ================================================================ boot */
  async function boot() {
    buildSteps();
    paintSteps(null);
    renderResultEmpty();
    renderHow();
    renderDev();
    Hero.start();
    const h = location.hash.slice(1);
    showTab(TABS.includes(h) ? h : "try", { scroll: false });
    moveInd(false);
    observe();

    const [samples, health, results] = await Promise.all([
      fetch("/api/samples").then((r) => r.json()).catch(() => []),
      fetch("/api/health").then((r) => r.json()).catch(() => null),
      fetch("/results.json").then((r) => r.json()).catch(() => null),
    ]);
    S.samples = samples; S.health = health; S.results = results;
    S.storyRun = samples.find((s) => s.id === "ransomware-precursor")?.run || null;
    renderPicks(); renderLock();
    if (S.samples.length) pickSample(0, $(".q-item"));
    updateStart(); renderStory(); renderGood();
    Hero.setRun(S.storyRun);
    if (health) {
      const v = Object.entries(health.attack_versions || {}).map(([d, ver]) => `${d.replace("-attack", "")} v${ver}`).join(", ");
      $("#config").textContent = `${health.techniques} ATT&CK techniques (${v}) · search: ${health.retrieval} · models on Groq: ${[...new Set(Object.values(health.models || {}))].join(", ")}`;
    } else {
      $("#config").textContent = "The server did not answer. Saved runs still play if the page loaded.";
    }
    if (h === "choose") $("#choose").scrollIntoView();
    document.fonts?.ready.then(() => { moveInd(false); drawEdges(); });
  }
  boot();
})();

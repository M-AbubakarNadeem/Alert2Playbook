/* Alert2Playbook page behaviour. Plain JavaScript, no framework.
   Every piece of text that came from an alert or an AI is escaped before it is shown. */
(() => {
  const C = window.A2P;
  const MAX = 4000;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtMs = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms) + " ms");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const store = {
    get: (k, s = sessionStorage) => { try { return s.getItem(k); } catch { return null; } },
    set: (k, v, s = sessionStorage) => { try { v == null ? s.removeItem(k) : s.setItem(k, v); } catch {} },
  };

  const S = {
    samples: [], health: null, results: null,
    picked: null,          // sample object, or "own"
    run: null,             // full state of the run on screen
    steps: [],             // step records shown in the boxes
    replay: false,         // the run on screen is a saved one
    busy: false,
    code: store.get("a2p-code"),
    storyRun: null,
  };

  /* ================================================================ JSON view */
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
    return `<div class="codewrap"><button class="btn small copy" data-copy="${id}" type="button">Copy</button><pre class="json" id="${id}">${body}</pre></div>`;
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    const t = document.getElementById(b.dataset.copy)?.innerText || "";
    navigator.clipboard?.writeText(t).then(() => { b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1200); });
  });

  /* ================================================================ tabs + theme */
  function showTab(name) {
    $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    window.scrollTo({ top: 0 });
  }
  $$(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
  window.addEventListener("hashchange", () => {
    const tab = location.hash.slice(1);
    if (["try", "how", "good", "dev"].includes(tab)) showTab(tab);
  });
  $("#theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("a2p-theme-v2", next, localStorage);
  };

  /* ================================================================ demo code */
  function renderLock() {
    const chip = $("#lock-chip");
    const locked = S.health?.live_runs === "need demo code";
    if (!locked) { chip.hidden = true; return; }
    chip.hidden = false;
    chip.classList.toggle("on", !!S.code);
    chip.textContent = S.code ? "🔓 Live runs unlocked" : "🔒 Live runs locked";
    chip.title = S.code ? "Click to lock again" : "Live runs need a demo code. Samples always work.";
  }
  $("#lock-chip").onclick = () => {
    if (S.code) { S.code = null; store.set("a2p-code", null); renderLock(); updateStart(); }
    else { showTab("try"); $("#code-area").hidden = false; $("#code").focus(); $("#code-area").scrollIntoView({ block: "center" }); }
  };
  async function unlock() {
    const code = $("#code").value.trim();
    if (!code) return;
    const msg = $("#code-msg");
    try {
      const r = await fetch("/api/unlock", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
      if (r.ok) {
        S.code = code; store.set("a2p-code", code);
        msg.innerHTML = `<div class="msg good">Unlocked. Live runs work until you close this tab.</div>`;
        $("#code").value = "";
        renderLock(); updateStart();
      } else {
        const d = await r.json().catch(() => ({}));
        msg.innerHTML = `<div class="msg bad">${esc(d.detail || "That code did not work.")}</div>`;
      }
    } catch { msg.innerHTML = `<div class="msg bad">Could not reach the server.</div>`; }
  }
  $("#code-go").onclick = unlock;
  $("#code").addEventListener("keydown", (e) => { if (e.key === "Enter") unlock(); });

  /* ================================================================ step 1: pick */
  function renderPicks() {
    const cards = S.samples.map((s, i) => `
      <button class="pick" type="button" data-i="${i}" aria-pressed="false">
        <span class="ico" aria-hidden="true">${s.icon || "🚨"}</span>
        <b>${esc(s.simple_title || s.title)}</b>
        <span>${esc(s.story || "")}</span>
        <span class="where">${esc(s.where || "")}</span>
      </button>`).join("");
    $("#picks").innerHTML = cards + `
      <button class="pick own" type="button" data-own="1" aria-pressed="false">
        <span class="ico" aria-hidden="true">✏️</span>
        <b>Build my own alert</b>
        <span>Fill in a short form (computer, user, what ran) and we write the alert for you. Or paste one.</span>
        <span class="where">${S.health?.live_runs === "need demo code" ? "Needs the demo code to run" : "Runs live"}</span>
      </button>`;
    $$(".pick").forEach((b) => (b.onclick = () => (b.dataset.own ? pickOwn(b) : pickSample(+b.dataset.i, b))));
  }
  function pressed(btn) { $$(".pick").forEach((b) => b.setAttribute("aria-pressed", String(b === btn))); }
  function pickSample(i, btn) {
    S.picked = S.samples[i];
    pressed(btn);
    $("#builder").hidden = true;
    setAlert(S.picked.alert);
    resetRun();
    updateStart();
  }
  function pickOwn(btn) {
    S.picked = "own";
    pressed(btn);
    renderBuilder();
    $("#builder").hidden = false;
    $("#alert-box").open = true;
    setAlert("");
    resetRun();
    updateStart();
    $("#builder").scrollIntoView({ block: "start", behavior: "smooth" });
  }
  function setAlert(text) {
    $("#alert").value = text;
    $("#alert").dispatchEvent(new Event("input"));
  }
  $("#alert").addEventListener("input", () => {
    const n = $("#alert").value.length;
    $("#count").textContent = `${n.toLocaleString()} / ${MAX.toLocaleString()} characters`;
    $("#count").style.color = n > MAX ? "var(--danger)" : "";
    if (S.picked && S.picked !== "own" && $("#alert").value !== S.picked.alert) {
      S.picked = "own"; pressed(null);  // an edited sample is a custom alert now
    }
    updateStart();
  });

  /* ================================================================ alert builder */
  let kind = "win";
  function renderBuilder() {
    const B = C.BUILDER;
    const t = B[kind];
    $("#builder").innerHTML = `
      <div>
        <h3 style="margin:0 0 var(--s2)">Build your own alert</h3>
        <p class="small muted" style="margin:0 0 var(--s4)">Choose what kind of event it is, fill the boxes, then press <b>Make the alert</b>. The text appears below, ready to run.</p>
        <label class="field">What happened?
          <select id="b-kind">${Object.entries(B).map(([k, v]) => `<option value="${k}" ${k === kind ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>
        </label>
        <div class="form-grid" style="margin-top:var(--s3)">
          ${t.fields.map(([key, label, ph, wide]) => `
            <label class="field ${wide ? "wide" : ""}">${esc(label)}
              ${wide ? `<textarea data-f="${key}" rows="2" style="min-height:64px" placeholder="${esc(ph)}"></textarea>` : `<input data-f="${key}" placeholder="${esc(ph)}">`}
            </label>`).join("")}
        </div>
        <div class="row" style="margin-top:var(--s4)">
          <button class="btn" id="b-example" type="button">🎲 Fill an example</button>
          <button class="btn primary" id="b-make" type="button">Make the alert ↓</button>
        </div>
      </div>
      <aside class="tips">
        <h4>A good alert answers 4 questions</h4>
        <ol>
          <li><b>Where?</b> The computer or server name.</li>
          <li><b>Who?</b> The user or account.</li>
          <li><b>What ran?</b> The program and its exact command.</li>
          <li><b>What else?</b> Anything strange around it: the time, a new IP address, what happened just before or after.</li>
        </ol>
        <p style="margin:var(--s3) 0 0">The more clues, the better the plan. Never paste real company data: this is a public demo.</p>
      </aside>`;
    $("#b-kind").onchange = (e) => { kind = e.target.value; renderBuilder(); };
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
      $("#alert-box").open = true;
      $("#alert-box").scrollIntoView({ block: "center", behavior: "smooth" });
    };
  }

  /* ================================================================ step 2: start */
  function locked() { return S.health?.live_runs === "need demo code" && !S.code; }
  function updateStart() {
    const btn = $("#start"), note = $("#start-note");
    const text = $("#alert").value.trim();
    const valid = text.length >= 10 && text.length <= MAX;
    const isSample = S.picked && S.picked !== "own" && S.picked.run;
    $("#code-area").hidden = !(locked() && S.picked === "own");
    if (S.busy) { btn.disabled = true; btn.textContent = "⏳ Working…"; return; }
    if (!S.picked) { btn.disabled = true; btn.textContent = "▶ Start the agents"; note.textContent = "Pick an alert first."; return; }
    if (isSample && locked()) {
      btn.disabled = false; btn.textContent = "▶ Watch a saved run";
      note.innerHTML = "This plays back a real run we saved earlier, step by step. No AI is called. To run the agents live, unlock with the demo code (🔒 at the top).";
    } else if (locked()) {
      btn.disabled = true; btn.textContent = "🔒 Enter the demo code to run live";
      note.textContent = "Your own alert needs a live run, and live runs use paid AI credit. Enter the demo code below.";
    } else {
      btn.disabled = !valid; btn.textContent = "▶ Run the agents live";
      note.textContent = valid ? "The 7 helpers will work on this alert now. It takes about 7 to 15 seconds." : "Write or build an alert first (at least 10 characters).";
    }
  }
  $("#start").onclick = () => {
    const isSample = S.picked && S.picked !== "own" && S.picked.run;
    if (isSample && locked()) return replay(S.picked.run);
    return liveRun("/api/run", { alert: $("#alert").value.trim() }, []);
  };

  /* ================================================================ step 3: the boxes */
  function resetRun() { S.run = null; S.steps = []; S.replay = false; renderSteps(null); $("#totals").hidden = true; renderResultEmpty(); }

  function resultLine(id, st) {
    const r = S.run || {};
    const last = (S.steps || []).filter((s) => s.node === id).slice(-1)[0];
    switch (id) {
      case "screen": return last?.score == null ? "" : last.flagged ? `⚠️ Trick found (score ${last.score.toFixed(2)})` : `Clean (score ${last.score.toFixed(3)})`;
      case "triage": return r.triage ? `Severity: ${r.triage.severity}. ${r.triage.behaviors?.length || 0} suspicious actions found.` : "";
      case "retrieve": return r.candidates ? `${r.candidates.length} possible attack tricks found` : "";
      case "map_attack": return r.mapping ? `Named: ${r.mapping.map((m) => m.technique_id).join(", ")}` : "";
      case "enrich": return last ? `${last.graph_nodes} pieces of official advice linked` : "";
      case "write_playbook": return r.playbook ? `Plan written: ${C.PHASES.reduce((a, [k]) => a + (r.playbook[k]?.length || 0), 0)} steps` : "";
      case "critic": return r.review ? (r.review.verdict === "approve" ? "Approved ✓" : "Sent back with notes") : "";
      case "finalize": return r.status ? "Waiting for you" : "";
      default: return "";
    }
  }

  function renderSteps(running) {
    const byNode = {};
    for (const s of S.steps) (byNode[s.node] ||= []).push(s);
    $("#steps").innerHTML = C.STEPS.map((st, i) => {
      const runs = byNode[st.id] || [];
      let state = runs.length ? "done" : "";
      if (st.id === "screen" && runs.some((r) => r.flagged)) state = "flag";
      if (st.id === running) state = "running";
      if (st.id === "finalize" && runs.length) state = "waiting";
      const label = { "": "Waiting", running: "Working", done: "Done", flag: "Warning", waiting: "Your turn" }[state];
      const ms = runs.reduce((a, r) => a + (r.ms || 0), 0);
      const meta = runs.length && st.id !== "finalize" ? ` · ${fmtMs(ms)}${runs.length > 1 ? ` · ran ${runs.length}×` : ""}` : "";
      return `<button class="stp" type="button" data-step="${st.id}" data-state="${state}">
        <span class="top-line"><span class="ico" aria-hidden="true">${st.icon}</span><span class="n">STEP ${i + 1}</span><span class="status">${label}</span></span>
        <b>${st.name}</b>
        <span class="who">${esc(st.who)}${meta}</span>
        <span class="result">${esc(resultLine(st.id, state))}</span>
        <span class="more">See what went in and out →</span>
      </button>`;
    }).join("");
    $$(".stp").forEach((b) => (b.onclick = () => openStep(b.dataset.step, S.run, S.steps)));

    const llm = S.steps.filter((s) => s.input_tokens != null);
    if (S.steps.length) {
      const total = S.steps.reduce((a, s) => a + (s.ms || 0), 0);
      const tok = llm.reduce((a, s) => a + s.input_tokens + s.output_tokens, 0);
      $("#totals").hidden = false;
      $("#totals").innerHTML = `<span>⏱ Total time <b>${fmtMs(total)}</b></span><span>🤖 AI calls <b>${llm.length}</b></span><span>🔤 Tokens <b>${tok.toLocaleString()}</b></span>${S.replay ? `<span>📼 Saved run${S.run?.saved_at ? " from " + esc(S.run.saved_at) : ""}</span>` : `<span>🟢 Live run</span>`}`;
    }
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

  function openStep(id, run, steps) {
    const meta = C.STEPS.find((s) => s.id === id);
    const i = C.STEPS.indexOf(meta);
    const st = (steps || []).filter((s) => s.node === id);
    const io = stepIO(id, run, steps);
    const timing = st.length ? st.map((s) => `${s.model ? esc(s.model) + " · " : ""}${fmtMs(s.ms || 0)}${s.input_tokens != null ? ` · ${(s.input_tokens + s.output_tokens).toLocaleString()} tokens` : ""}${s.fallback_from ? ` · backup model used (first try: ${esc(s.fallback_from)})` : ""}`).join("<br>") : "Not run yet";
    $("#drawer-kicker").textContent = `STEP ${i + 1} OF 8`;
    $("#drawer-title").textContent = `${meta.icon} ${meta.name}`;
    $("#drawer-body").innerHTML = `
      <p style="margin-top:0">${esc(meta.plain)}</p>
      <dl class="kv">
        <dt>Who does it</dt><dd>${esc(meta.who)}</dd>
        <dt>Connection</dt><dd>${esc(meta.link)}</dd>
        <dt>This run</dt><dd>${timing}</dd>
      </dl>
      <div class="dtabs" role="tablist">
        <button class="btn small" data-io="in" type="button">⬇ What went in</button>
        <button class="btn small" data-io="out" type="button">⬆ What came out</button>
      </div>
      <div id="io-view"></div>`;
    const ran = st.length > 0 && run;
    const show = (which) => {
      $$("[data-io]", $("#drawer-body")).forEach((b) => b.classList.toggle("primary", b.dataset.io === which));
      const v = io[which];
      $("#io-view").innerHTML = !ran || v == null ? `<p class="muted">This step has not run yet. Pick an alert and press Start, then open this box again.</p>` : codeBlock(v);
    };
    $$("[data-io]", $("#drawer-body")).forEach((b) => (b.onclick = () => show(b.dataset.io)));
    show(io.out ? "out" : "in");
    $("#drawer").classList.add("open"); $("#overlay").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
    $("#drawer-close").focus();
  }
  function closeDrawer() { $("#drawer").classList.remove("open"); $("#overlay").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }
  $("#drawer-close").onclick = closeDrawer;
  $("#overlay").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  /* ================================================================ running */
  const NEXT = { screen: "triage", triage: "retrieve", retrieve: "map_attack", map_attack: "enrich", enrich: "write_playbook", write_playbook: "critic" };

  function applyUpdate(node, update) {
    S.run = S.run || {};
    for (const [k, v] of Object.entries(update || {})) {
      if (k === "steps") S.steps = S.steps.concat(v);
      else S.run[k] = v;
    }
  }

  async function replay(saved) {
    S.busy = true; S.replay = true; updateStart();
    S.run = { alert: saved.alert }; S.steps = [];
    renderResultLoading("Playing back a saved run…");
    const order = saved.steps || [];
    renderSteps(order[0]?.node || "screen");
    $("#steps").scrollIntoView({ block: "start", behavior: "smooth" });
    for (let i = 0; i < order.length; i++) {
      const s = order[i];
      await sleep(Math.min(1400, Math.max(450, (s.ms || 0) / 2)));
      S.steps.push(s);
      // reveal the state fields this step produced
      const reveal = { screen: ["guard"], triage: ["triage"], retrieve: ["candidates"], map_attack: ["mapping"], enrich: ["context"], write_playbook: ["playbook"], critic: ["review", "revisions"] }[s.node] || [];
      for (const k of reveal) S.run[k] = saved[k];
      renderSteps(order[i + 1]?.node || (s.node === "critic" ? "finalize" : null));
    }
    await sleep(400);
    S.run = saved; S.steps.push({ node: "finalize", ms: 0 });
    renderSteps(null);
    renderResult(S.run);
    S.busy = false; updateStart();
  }

  async function liveRun(url, body, baseSteps, firstNode = "screen") {
    S.busy = true; S.replay = false; updateStart();
    if (!baseSteps.length) { S.run = { alert: body.alert }; S.steps = []; }
    renderSteps(firstNode);
    renderResultLoading("The helpers are working…");
    $("#steps").scrollIntoView({ block: "start", behavior: "smooth" });
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
            renderSteps(d.node === "finalize" ? null : next);
          } else if (ev === "done") {
            const keep = S.steps;
            S.run = d.state; S.steps = keep;
            renderSteps(null); renderResult(S.run);
          } else if (ev === "error") {
            renderSteps(null); renderResultError(d.message);
          }
        }
      }
    } catch (e) {
      renderSteps(null); renderResultError(e.message);
    } finally { S.busy = false; updateStart(); }
  }

  /* ================================================================ step 4: result */
  function renderResultEmpty() { $("#result").innerHTML = `<div class="empty"><div class="ico">📄</div><p>The plan appears here when the helpers finish.</p></div>`; }
  function renderResultLoading(note) { $("#result").innerHTML = `<p class="muted">${esc(note)}</p>` + [92, 70, 84, 60, 76].map((w) => `<div class="skeleton" style="width:${w}%"></div>`).join(""); }
  function renderResultError(m) { $("#result").innerHTML = `<div class="banner bad">⚠️ <div>${esc(m)}</div></div><p class="muted">Tip: the sample alerts always work, even without the demo code.</p>`; }

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

  let rtab = "what";
  function renderResult(r) {
    const t = r.triage, pb = r.playbook, rv = r.review, idx = refIndex(r);
    const ref = (x) => idx[x] ? `<a class="ref" href="${esc(idx[x].url)}" target="_blank" rel="noopener" title="${esc(idx[x].name)}">${esc(x)}</a>` : `<span class="ref">${esc(x)}</span>`;
    const tabs = [["what", "📝 What happened"], ["attacks", "🏷️ Attack names"], ["plan", "🗺️ Response plan"], ["hunt", "🔦 Where to look next"], ["check", "✅ Double-check"]];
    const obs = t.observables || {};
    const list = (xs) => (xs?.length ? `<div class="chips">${xs.slice(0, 8).map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : `<span class="muted small">none in the alert</span>`);
    const views = {
      what: `
        <div class="headline"><h3>${esc(pb.title)}</h3><span class="badge ${esc(t.severity)}">${esc(t.severity)} danger</span><span class="badge">${esc(t.platform)}</span></div>
        <p style="font-size:var(--fs-lg);margin:0">${esc(t.summary)}</p>
        <p class="muted">${esc(pb.executive_summary)}</p>
        <p class="small"><b>Why this danger level:</b> ${esc(t.severity_reason)}</p>
        <div class="facts">
          <div class="fact"><small>Where (computers)</small>${list(obs.hosts)}</div>
          <div class="fact"><small>Who (accounts)</small>${list(obs.users)}</div>
          <div class="fact"><small>Programs and commands</small>${list(obs.processes)}</div>
          <div class="fact"><small>Network addresses</small>${list(obs.network)}</div>
        </div>
        <p class="small"><b>What the attacker seems to be doing:</b></p>
        <ul>${(t.behaviors || []).map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`,
      attacks: `
        <p class="muted" style="margin-top:0">These are the known attack tricks from MITRE ATT&amp;CK that this alert shows. The grey box is the exact proof copied from the alert.</p>
        ${r.mapping.map((m) => {
          const tech = (r.context?.techniques || []).find((x) => x.id === m.technique_id);
          const first = (tech?.description || "").split(". ")[0];
          return `<div class="tech"><div class="tech-head"><a href="${esc(idx[m.technique_id]?.url || "#")}" target="_blank" rel="noopener">${esc(m.technique_id)}</a><b>${esc(m.name)}</b><span class="badge ${m.confidence === "high" ? "ok" : m.confidence === "medium" ? "medium" : ""}">${esc(m.confidence)} confidence</span></div>
            ${first ? `<p class="small muted" style="margin:var(--s2) 0 0">${esc(first)}.</p>` : ""}
            <div class="quote">${esc(m.evidence)}</div></div>`;
        }).join("")}`,
      plan: `
        <div class="ref-legend"><span>The small codes are the official advice each step is based on. Click one to open MITRE's page.</span>${C.REF_TYPES.map(([k, v]) => `<span><b>${k}</b> = ${v}</span>`).join("")}</div>
        ${C.PHASES.map(([k, ico, title]) => `<div class="phase"><h4><span aria-hidden="true">${ico}</span>${title}</h4><ol>${(pb[k] || []).map((s) => `<li><b>${esc(s.action)}</b><p>${esc(s.detail)}</p>${s.references?.length ? `<span class="refs">${s.references.map(ref).join("")}</span>` : ""}</li>`).join("")}</ol></div>`).join("")}`,
      hunt: `
        <p class="muted" style="margin-top:0">Where to search next for more signs of the same attacker. The first part names the log to search.</p>
        <ol>${(pb.hunt_leads || []).map((h) => `<li style="margin-bottom:var(--s3)"><b class="mono">${esc(h.log_source)}</b><br><span class="muted">${esc(h.look_for)}</span></li>`).join("")}</ol>`,
      check: `
        <p><span class="badge ${rv.verdict === "approve" ? "ok" : "high"}">${rv.verdict === "approve" ? "approved by the critic" : "critic still has notes"}</span>
        <span class="muted small">after ${r.revisions || 0} rewrite${r.revisions === 1 ? "" : "s"}</span></p>
        ${rv.verdict === "approve" ? `<p class="muted">The code checks passed (every reference is real, every part has steps) and the reviewer AI found no real problem.</p>` : `<ul>${rv.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`}
        ${r.feedback_history?.length ? `<p><b>Changes you asked for:</b></p><ol>${r.feedback_history.map((f) => `<li>${esc(f)}</li>`).join("")}</ol>` : ""}`,
    };
    $("#result").innerHTML = `
      ${r.guard?.flagged ? `<div class="banner bad">🎭 <div><b>The safety check found a hidden trick in this alert</b> (score ${r.guard.score.toFixed(3)}). Someone wrote instructions for the AI inside the alert. The helpers ignored them, but read this plan with extra care.</div></div>` : ""}
      ${S.replay ? `<div class="banner info">📼 <div>This is a <b>saved run</b> played back. Unlock with the demo code to run the agents live on any alert.</div></div>` : ""}
      <div class="rtabs" role="tablist">${tabs.map(([k, l]) => `<button class="rtab" role="tab" data-r="${k}" aria-selected="${k === rtab}">${l}</button>`).join("")}</div>
      <div id="rview">${views[rtab]}</div>
      <div class="decide">
        <h4>🙋 Your decision</h4>
        <p class="small muted" id="decision">The plan is waiting for a person. Nothing has been done to any computer.</p>
        <div class="row">
          <button class="btn primary" id="approve" type="button">✓ Approve</button>
          <button class="btn" id="change" type="button">✎ Ask for changes</button>
          <button class="btn" id="download" type="button">⬇ Download the plan</button>
        </div>
        <div id="fb" hidden style="margin-top:var(--s4)">
          <label class="field">What should change? Write it like you would tell a colleague.
            <textarea id="fbtext" maxlength="1000" style="min-height:80px;font-family:var(--font)" placeholder="Example: We use Microsoft Defender to isolate computers. Also add a step to tell the finance manager."></textarea>
          </label>
          <div class="row" style="margin-top:var(--s2)"><button class="btn primary" id="fbsend" type="button">Send to the writer</button><span class="small muted">The writer and the critic run again (steps 6 and 7).</span></div>
        </div>
      </div>`;
    $$(".rtab").forEach((b) => (b.onclick = () => { rtab = b.dataset.r; $$(".rtab").forEach((x) => x.setAttribute("aria-selected", String(x === b))); $("#rview").innerHTML = views[rtab]; }));
    $("#approve").onclick = () => { $("#decision").textContent = `✓ Approved at ${new Date().toLocaleTimeString()}. Download it to hand it to the team.`; $("#approve").disabled = true; };
    $("#change").onclick = () => {
      if (locked()) { $("#decision").innerHTML = "Asking for changes runs the writer again live, so it needs the demo code (🔒 at the top)."; return; }
      $("#fb").hidden = !$("#fb").hidden; $("#fbtext").focus();
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
  function renderHow() {
    $("#map").innerHTML = C.NODES.map((n) => `<button class="node" type="button" data-node="${n.id}" aria-pressed="false"><span class="ico" aria-hidden="true">${n.icon}</span><b>${esc(n.name)}</b><span>${esc(n.short)}</span></button>`).join("");
    const showNode = (id) => {
      const n = C.NODES.find((x) => x.id === id);
      $$(".node").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.node === id)));
      $("#map-detail").innerHTML = `<h3 style="margin-top:0">${n.icon} ${esc(n.name)}</h3><p>${esc(n.detail)}</p><p class="small muted"><b>Technical:</b> ${esc(n.tech)}</p>`;
    };
    $$(".node").forEach((b) => (b.onclick = () => showNode(b.dataset.node)));
    showNode("server");

    $("#messages").innerHTML = C.MESSAGES.map((m) => `<button class="message" type="button" data-msg="${m.n}" aria-pressed="false"><span class="num">${m.n}</span><span><b>${esc(m.what)}</b><br><span class="from">${esc(m.from)} → ${esc(m.to)}</span></span><span class="fmt">${esc(m.fmt)}</span></button>`).join("");
    const showMsg = (n) => {
      const m = C.MESSAGES.find((x) => x.n === n);
      $$(".message").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.msg === n)));
      let example = m.example;
      if (n === 6 && S.storyRun) {
        const lines = S.storyRun.steps.filter((s) => s.model).map((s, i, arr) => ` ${i === arr.length - 1 ? "└─" : "├─"} ${s.node.padEnd(15)} ${s.model.padEnd(20)} ${fmtMs(s.ms).padStart(7)}  ${(s.input_tokens + s.output_tokens).toLocaleString()} tokens`);
        example = `Run "alert2playbook"  (the "Backups wiped" sample)\n` + lines.join("\n");
      }
      $("#message-detail").innerHTML = `<h3 style="margin-top:0">${m.n}. ${esc(m.what)} <span class="muted small">(${esc(m.from)} → ${esc(m.to)})</span></h3><p>${esc(m.explain)}</p><p class="small muted"><b>Format:</b> ${esc(m.fmt)}</p>${codeBlock(example, true)}`;
    };
    $$(".message").forEach((b) => (b.onclick = () => showMsg(+b.dataset.msg)));
    showMsg(2);

    $("#trust").innerHTML = C.TRUST.map(([h, t]) => `<div><b>${h}</b><span>${esc(t)}</span></div>`).join("");
    $("#glossary").innerHTML = C.GLOSSARY.map(([h, t]) => `<div><b>${esc(h)}</b><span>${esc(t)}</span></div>`).join("");
  }

  function renderStory() {
    const run = S.storyRun;
    if (!run) { $("#story").innerHTML = `<p class="muted">The saved example run could not be loaded.</p>`; return; }
    // A readable preview: "key: value" lines with real text, not escaped JSON.
    const readable = (v) => {
      if (v == null) return "";
      if (typeof v !== "object") return String(v);
      if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? readable(x) : String(x))).join(", ");
      return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, " ")}: ${typeof x === "object" ? readable(x) : String(x)}`).join("\n");
    };
    const brief = (v) => {
      const s = readable(v);
      return esc(s.length > 300 ? s.slice(0, 300) + " …" : s);
    };
    $("#story").innerHTML = C.STEPS.map((st, i) => {
      const io = stepIO(st.id, run, run.steps);
      return `<div class="story-step"><div class="ico" aria-hidden="true">${st.icon}</div><div>
        <h4>${i + 1}. ${st.name}</h4><p>${esc(st.plain)}</p>
        <div class="io"><div><small>Went in</small><span class="mono xs">${brief(io.in)}</span></div><div><small>Came out</small><span class="mono xs">${brief(io.out)}</span></div></div>
        <button class="link-btn" type="button" data-story="${st.id}" style="margin-top:var(--s2)">Open the full data →</button>
      </div></div>`;
    }).join("");
    $$("[data-story]").forEach((b) => (b.onclick = () => openStep(b.dataset.story, run, run.steps)));
  }

  /* ================================================================ how good */
  function bar(label, value, max = 100, alt = false, suffix = "%") {
    const w = Math.max(2, Math.min(100, (value / max) * 100));
    return `<div class="bar-row"><span>${label}</span><span class="track"><span class="fill ${alt ? "alt" : ""}" style="width:${w}%;display:block"></span></span><span class="val">${value}${suffix}</span></div>`;
  }
  function renderGood() {
    const R = S.results;
    if (!R) { $("#good").innerHTML = `<p class="muted">Results could not be loaded.</p>`; return; }
    const cur = R.eval.current, base = R.eval.baseline, g = R.grounding, b = R.benchmark;
    $("#good").innerHTML = `
      <div class="stats">
        <div class="stat"><big>${cur.recall}%</big><span>of test alerts: the right attack trick was in the shortlist of ${cur.candidates}</span></div>
        <div class="stat"><big>${cur.exact3}%</big><span>the exact right trick was in the final picks (up to 3)</span></div>
        <div class="stat"><big>${g.valid} / ${g.cited}</big><span>references in the saved plans that are real MITRE IDs</span></div>
        <div class="stat"><big>~${R.live.seconds} s</big><span>for one live alert, start to finished plan</span></div>
      </div>

      <div class="section">
        <h2 class="step-title"><span class="num">1</span> The accuracy test</h2>
        <div class="card">
          <p style="margin-top:0">We made <b>${cur.alerts} test alerts</b> from Atomic Red Team, a free library where every test is a real attack command with a known answer. We removed anything that gives the answer away. Each alert is one command with no story around it, so this test is <b>harder than real life</b>.</p>
          <h4>Current version (hybrid search, ${cur.candidates} candidates)</h4>
          <div class="bars">
            ${bar("Right trick in the shortlist", cur.recall)}
            ${bar("Right trick in the final picks", cur.exact3)}
            ${bar("Right family of trick in the picks", cur.family3)}
            ${bar("Right trick picked first", cur.exact1)}
          </div>
          <h4>First version (keyword search only, ${base.candidates} candidates)</h4>
          <div class="bars">
            ${bar("Right trick in the shortlist", base.recall, 100, true)}
            ${bar("Right trick in the final picks", base.exact3, 100, true)}
            ${bar("Right family of trick in the picks", base.family3, 100, true)}
            ${bar("Right trick picked first", base.exact1, 100, true)}
          </div>
          <p class="small muted">Why "picked first" is low: many test commands run through PowerShell, and the AI often lists "PowerShell" first. That is true, but it is not the labelled answer. The right answer is usually second.</p>
        </div>
      </div>

      <div class="section">
        <h2 class="step-title"><span class="num">2</span> Does the meaning search help?</h2>
        <div class="card">
          <p style="margin-top:0">To compare search methods fairly, we replayed only the search step with the same inputs. How often is the right trick in the shortlist?</p>
          <div class="table-scroll"><table class="t"><thead><tr><th>Search method</th>${R.retrieval.ks.map((k) => `<th class="num-cell">Top ${k}</th>`).join("")}</tr></thead>
          <tbody>${R.retrieval.rows.map((row) => `<tr><td>${esc(row.name)}</td>${row.values.map((v) => `<td class="num-cell">${v}%</td>`).join("")}</tr>`).join("")}</tbody></table></div>
          <p class="small muted">Keyword search stops improving at 20 results. Keyword + meaning together keeps finding more, so the attack-matcher now sees 25 candidates.</p>
        </div>
      </div>

      <div class="section">
        <h2 class="step-title"><span class="num">3</span> LangGraph vs CrewAI</h2>
        <div class="card">
          <p style="margin-top:0">We built the exact same system a second time with <b>CrewAI</b>, another popular agent framework: same AI models, same instructions, same search, same checks. Then both ran the same ${b.alerts} sample alerts.</p>
          <div class="table-scroll"><table class="t"><thead><tr><th></th>${b.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
          <tbody>${b.rows.map((row) => `<tr><td><b>${esc(row[0])}</b></td>${row.slice(1).map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
          <p class="small muted">${esc(b.note)}</p>
        </div>
      </div>

      <div class="section">
        <h2 class="step-title"><span class="num">4</span> Automatic tests</h2>
        <div class="card"><p style="margin:0"><b>${R.tests} automatic tests</b> run without internet and without any AI. They check the knowledge graph, the search, every safety guard (fake IDs are caught and removed), the critic loop, your change requests, the demo-code lock and the MCP tools.</p></div>
      </div>
      <p class="xs muted">Numbers measured on ${esc(R.date)}. Full method and every answer: <a href="https://github.com/M-AbubakarNadeem/Alert2Playbook/blob/main/docs/07-measuring-it.md" target="_blank" rel="noopener">docs/07-measuring-it.md</a>.</p>`;
  }

  /* ================================================================ developers */
  function renderDev() {
    const base = location.origin;
    const curlRun = `curl -N -X POST ${base}/api/run \\\n  -H "Content-Type: application/json" \\\n  -H "X-Demo-Code: YOUR_DEMO_CODE" \\\n  -d '{"alert": "Sysmon EID 1 on WS-01: schtasks /create /tn upd /tr powershell.exe /sc onlogon /ru SYSTEM"}'`;
    const a2a = `curl -X POST ${base}/a2a \\\n  -H "Content-Type: application/json" \\\n  -H "X-Demo-Code: YOUR_DEMO_CODE" \\\n  -d '{"jsonrpc": "2.0", "id": 1, "method": "SendMessage",\n       "params": {"message": {"messageId": "m1", "role": "ROLE_USER",\n                  "parts": [{"text": "auditd on web-03: www-data ran useradd -m support2"}]}}}'`;
    $("#dev").innerHTML = `
      <div class="card">
        <h3 style="margin-top:0">Web API</h3>
        <div class="table-scroll"><table class="t"><thead><tr><th>Method</th><th>Path</th><th>Send</th><th>Get back</th><th>Code?</th></tr></thead><tbody>
          <tr><td>GET</td><td class="mono">/api/health</td><td>nothing</td><td>versions, models, search mode</td><td>no</td></tr>
          <tr><td>GET</td><td class="mono">/api/samples</td><td>nothing</td><td>sample alerts with saved runs</td><td>no</td></tr>
          <tr><td>POST</td><td class="mono">/api/unlock</td><td class="mono">{"code"}</td><td class="mono">{"ok": true}</td><td>no</td></tr>
          <tr><td>POST</td><td class="mono">/api/run</td><td class="mono">{"alert"}</td><td>live stream (server-sent events)</td><td>yes</td></tr>
          <tr><td>POST</td><td class="mono">/api/feedback</td><td class="mono">{"state", "feedback"}</td><td>live stream</td><td>yes</td></tr>
          <tr><td>POST</td><td class="mono">/api/markdown</td><td class="mono">{"state"}</td><td>the plan as Markdown</td><td>no</td></tr>
          <tr><td>GET</td><td class="mono">/.well-known/agent-card.json</td><td>nothing</td><td>A2A Agent Card</td><td>no</td></tr>
          <tr><td>POST</td><td class="mono">/a2a</td><td>JSON-RPC SendMessage</td><td>A2A Task with 2 artifacts</td><td>yes</td></tr>
        </tbody></table></div>
        <p class="small muted">"Code" means the <span class="mono">X-Demo-Code</span> header. Live runs use paid AI credit, so they are locked on this public demo. <a href="${base}/api/docs" target="_blank" rel="noopener">Interactive API docs →</a></p>
        <h4>Run an alert from a terminal</h4>${codeBlock(curlRun, true)}
      </div>
      <div class="card" style="margin-top:var(--s4)">
        <h3 style="margin-top:0">A2A: let another AI agent call this one</h3>
        <p>Agents find it through the <a href="${base}/.well-known/agent-card.json" target="_blank" rel="noopener">Agent Card</a> (A2A protocol v1.0), then send a <span class="mono">SendMessage</span> request. The answer is a completed Task with the plan as Markdown and as JSON.</p>
        ${codeBlock(a2a, true)}
      </div>
      <div class="card" style="margin-top:var(--s4)">
        <h3 style="margin-top:0">MCP: give Claude (or Cursor) these tools</h3>
        <p>Clone the repository, install it, then:</p>
        ${codeBlock("claude mcp add alert2playbook -- python -m alert2playbook.mcp_server", true)}
        <p class="small muted">Tools: <span class="mono">lookup_technique</span>, <span class="mono">search_techniques</span> (no keys needed) and <span class="mono">alert_to_playbook</span> (needs a Groq key).</p>
      </div>
      <div class="card" style="margin-top:var(--s4)">
        <h3 style="margin-top:0">Built with</h3>
        <p style="margin:0">Python 3.12 · LangGraph · FastAPI · Groq (gpt-oss-120b, gpt-oss-20b, Qwen 3.8 27B, Llama Prompt Guard 2) · Pinecone (llama-text-embed-v2) · MITRE ATT&amp;CK v20 + D3FEND 1.6 · LangSmith · Vercel · Pydantic · pytest. <a href="https://github.com/M-AbubakarNadeem/Alert2Playbook" target="_blank" rel="noopener">Source and 11-page guide on GitHub →</a></p>
      </div>`;
  }

  /* ================================================================ boot */
  async function boot() {
    renderSteps(null);
    renderHow();
    renderDev();
    const tab = (location.hash || "#try").slice(1);
    if (["try", "how", "good", "dev"].includes(tab)) showTab(tab);

    const [samples, health, results] = await Promise.all([
      fetch("/api/samples").then((r) => r.json()).catch(() => []),
      fetch("/api/health").then((r) => r.json()).catch(() => null),
      fetch("/results.json").then((r) => r.json()).catch(() => null),
    ]);
    S.samples = samples; S.health = health; S.results = results;
    S.storyRun = samples.find((s) => s.id === "ransomware-precursor")?.run || null;
    renderPicks(); renderLock(); updateStart(); renderStory(); renderGood();
    if (health) {
      const v = Object.entries(health.attack_versions || {}).map(([d, ver]) => `${d.replace("-attack", "")} v${ver}`).join(", ");
      $("#config").textContent = `Knowledge: ${health.techniques} ATT&CK attack tricks (${v}). Search: ${health.retrieval}. AI models on Groq: ${[...new Set(Object.values(health.models || {}))].join(", ")}.`;
    }
  }
  boot();
})();

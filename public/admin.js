/* The owner's dashboard. Everything here comes from /api/admin/*, which needs the login
   cookie. Values typed by visitors (names, cities) are escaped before they are shown. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name) => `<svg class="i" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const num = (n) => (n == null ? "–" : Number(n).toLocaleString());
  const fmtMs = (ms) => (!ms ? "–" : ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms + " ms");
  const when = (ts) => {
    const d = new Date(ts);
    return isNaN(d) ? esc(ts) : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  let data = null;

  $("#theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("a2p-theme-v2", next); } catch {}
  };

  /* ---------------------------------------------------------------- sign in */
  function show(view) {
    $("#signin").hidden = view !== "signin";
    $("#dash").hidden = view !== "dash";
    $("#logout").hidden = view !== "dash";
    $("#who").hidden = view !== "dash";
  }

  $("#pw-eye").onclick = () => {
    const input = $("#password"), reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    $("#pw-eye").setAttribute("aria-pressed", String(reveal));
    $("#pw-eye").setAttribute("aria-label", reveal ? "Hide the password" : "Show the password");
    $("#pw-eye").innerHTML = icon(reveal ? "eye-off" : "eye");
  };

  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const username = $("#username").value.trim(), password = $("#password").value;
    const msg = $("#login-msg"), btn = $("#login-go");
    if (!username || !password) { msg.innerHTML = `<div class="note bad">${icon("warn")}<span>Enter your username and password.</span></div>`; return; }
    btn.disabled = true; msg.innerHTML = "";
    try {
      const r = await fetch("/api/admin/login", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ username, password }) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        msg.innerHTML = `<div class="note bad">${icon("warn")}<span>${esc(typeof d.detail === "string" ? d.detail : "Sign-in failed.")}</span></div>`;
        return;
      }
      $("#password").value = "";
      await load();
    } catch {
      msg.innerHTML = `<div class="note bad">${icon("warn")}<span>Couldn't reach the server.</span></div>`;
    } finally {
      btn.disabled = false;
    }
  };

  $("#logout").onclick = async () => {
    await fetch("/api/admin/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
    data = null;
    show("signin");
    $("#username").focus();
  };

  /* ---------------------------------------------------------------- load */
  async function load() {
    const r = await fetch("/api/admin/overview", { credentials: "same-origin", cache: "no-store" });
    if (r.status === 401) { show("signin"); $("#username").focus(); return; }
    if (!r.ok) { show("signin"); $("#login-msg").innerHTML = `<div class="note bad">${icon("warn")}<span>The dashboard couldn't load (${r.status}).</span></div>`; return; }
    data = await r.json();
    const me = await fetch("/api/admin/me", { credentials: "same-origin" }).then((x) => x.json()).catch(() => ({}));
    $("#who").textContent = me.user ? `Signed in as ${me.user}` : "";
    show("dash");
    render();
  }
  $("#refresh").onclick = async () => {
    $("#refresh").disabled = true;
    try { await load(); } finally { $("#refresh").disabled = false; }
  };

  /* ---------------------------------------------------------------- render */
  function render() {
    const t = data.totals;
    $("#dash-sub").textContent = `Updated ${when(data.generated_at)}. The last ${num(data.runs.length)} events are kept in full.`;
    $("#store-note").innerHTML = data.store === "upstash" ? "" :
      `<div class="note info">${icon("info")}<span>The shared database isn't connected, so this only shows what one server copy has seen since it started.</span></div>`;

    const tiles = [
      [t.visitor_runs, "live runs by visitors", `${num(t.owner_runs)} more by you`],
      [t.unique_visitors, "different visitors", "counted without keeping IPs"],
      [t.unique_keys, "different keys", "told apart by fingerprint"],
      [t.success_rate == null ? null : t.success_rate + "%", "of visitor runs finished", ""],
      [t.key_checks, "key checks", `${num(t.key_checks_failed)} rejected by the provider`],
    ];
    $("#kpis").innerHTML = tiles.map(([v, label, sub]) => `<div><dt>${v == null ? "–" : esc(typeof v === "number" ? num(v) : v)}</dt><dd>${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</dd></div>`).join("");

    const P = data.providers;
    $("#providers").innerHTML = P.length ? `<div class="pbars">${P.map((p, i) => `
      <div class="pbar" style="--i:${i}">
        <span class="pname">${esc(p.provider)}</span>
        <span class="ptrack"><span class="pfill" style="width:${Math.max(p.share, 1.5)}%"></span></span>
        <span class="pval">${p.share}%</span>
        <span class="pmeta">${num(p.runs)} run${p.runs === 1 ? "" : "s"} · ${num(p.keys)} key${p.keys === 1 ? "" : "s"}</span>
      </div>`).join("")}</div>`
      : `<p class="empty-line">No visitor runs yet.</p>`;

    const max = Math.max(1, ...data.per_day.map((d) => d.runs));
    const total = data.per_day.reduce((a, d) => a + d.runs, 0);
    $("#days-sub").textContent = total ? `${num(total)} runs in the last two weeks.` : "Nothing in the last two weeks.";
    $("#days").innerHTML = data.per_day.map((d, i) => {
      const day = new Date(d.day + "T00:00:00Z");
      const label = day.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
      return `<div class="day" title="${esc(label)}: ${d.runs} run${d.runs === 1 ? "" : "s"}" style="--i:${i}">
        <span class="dval">${d.runs || ""}</span>
        <span class="dbar" style="height:${(d.runs / max) * 100}%"></span>
        <span class="dlab">${i % 2 ? "" : esc(label)}</span></div>`;
    }).join("");

    const providers = [...new Set(data.runs.map((r) => r.provider_label || r.provider).filter(Boolean))].sort();
    const current = $("#f-provider").value;
    $("#f-provider").innerHTML = `<option value="">All providers</option>` + providers.map((p) => `<option${p === current ? " selected" : ""}>${esc(p)}</option>`).join("");
    const present = new Set(data.runs.map((r) => r.id));
    for (const id of [...selected]) if (!present.has(id)) selected.delete(id);
    renderTable();
  }

  function filtered() {
    const q = $("#q").value.trim().toLowerCase(), prov = $("#f-provider").value, kind = $("#f-kind").value, mine = $("#f-mine").checked;
    return data.runs.filter((r) => {
      if (!mine && r.source === "owner") return false;
      if (prov && (r.provider_label || r.provider) !== prov) return false;
      if (kind === "runs" && r.kind === "check") return false;
      if (kind === "check" && r.kind !== "check") return false;
      if (!q) return true;
      return [r.name, r.model, r.key_masked, r.key_fp, r.country, r.city, r.provider_label, r.alert_kind, (r.techniques || []).join(" ")]
        .some((v) => String(v || "").toLowerCase().includes(q));
    });
  }

  /* Entries ticked for deletion, by id. Kept across filter changes, cleared on reload. */
  const selected = new Set();
  let confirming = false;

  function renderTable() {
    const rows = filtered();
    const what = (r) => r.kind === "check" ? "Key check" : r.kind === "feedback" ? "Change request" : r.entry === "a2a" ? "Run (A2A)" : "Run";
    const allOn = rows.length > 0 && rows.every((r) => selected.has(r.id));
    $("#runs").innerHTML = `<thead><tr>
        <th class="pick"><input type="checkbox" id="pick-all" aria-label="Select every entry shown"${allOn ? " checked" : ""}></th>
        <th>When</th><th>Name</th><th>Provider and model</th><th>Key</th><th>What</th><th>Result</th>
        <th>Techniques</th><th class="num">Tokens</th><th class="num">Time</th><th>Where</th><th>Browser</th>
      </tr></thead><tbody>${rows.map((r) => `<tr${selected.has(r.id) ? ' class="sel"' : ""}>
        <td class="pick"><input type="checkbox" data-id="${esc(r.id)}" aria-label="Select this entry"${selected.has(r.id) ? " checked" : ""}></td>
        <td class="nowrap">${when(r.ts)}</td>
        <td><b>${esc(r.name || "–")}</b></td>
        <td><span class="nowrap">${esc(r.provider_label || r.provider || "–")}</span>${r.model ? `<span class="sub mono">${esc(r.model)}</span>` : ""}</td>
        <td>${r.key_masked ? `<span class="mono keymask" title="Never stored in full">${icon("lock")}${esc(r.key_masked)}</span><span class="sub mono">fp ${esc(r.key_fp)}</span>` : `<span class="muted">demo code</span>`}</td>
        <td><span class="nowrap">${esc(what(r))}</span>${r.alert_kind ? `<span class="sub">${esc(r.alert_kind)}</span>` : ""}</td>
        <td>${r.status === "ok" ? `<span class="tag ok">${icon("check")}ok</span>` : `<span class="tag fail" title="${esc(r.error || "")}">${icon("x")}failed</span>${r.error ? `<span class="sub err">${esc(r.error)}</span>` : ""}`}</td>
        <td class="mono">${esc((r.techniques || []).join(", ") || "–")}${r.guard_flagged ? `<span class="sub err">injection flagged</span>` : ""}</td>
        <td class="num">${r.tokens ? num(r.tokens) : "–"}</td>
        <td class="num">${fmtMs(r.duration_ms)}</td>
        <td class="nowrap">${esc([r.city, r.country].filter(Boolean).join(", ") || "–")}</td>
        <td class="nowrap">${esc(r.browser || "–")}</td>
      </tr>`).join("")}</tbody>`;
    $("#count").textContent = rows.length ? `Showing ${num(rows.length)} of ${num(data.runs.length)}.` : "Nothing matches.";

    $("#pick-all").onchange = (e) => {
      for (const r of rows) e.target.checked ? selected.add(r.id) : selected.delete(r.id);
      confirming = false;
      renderTable();
    };
    for (const box of $("#runs").querySelectorAll("input[data-id]")) {
      box.onchange = () => {
        box.checked ? selected.add(box.dataset.id) : selected.delete(box.dataset.id);
        box.closest("tr").classList.toggle("sel", box.checked);
        $("#pick-all").checked = rows.length > 0 && rows.every((r) => selected.has(r.id));
        confirming = false;
        renderSelbar();
      };
    }
    renderSelbar();
  }

  function renderSelbar() {
    const bar = $("#selbar"), n = selected.size;
    bar.hidden = n === 0;
    bar.classList.toggle("confirm", confirming);
    if (!n) { bar.innerHTML = ""; return; }
    const label = `${num(n)} entr${n === 1 ? "y" : "ies"}`;
    bar.innerHTML = confirming
      ? `${icon("warn")}<span>Delete <b>${label}</b> for good? This can't be undone.</span><span class="spacer"></span>
         <button class="btn sm danger" id="del-yes" type="button">${icon("trash")}Yes, delete</button>
         <button class="btn sm" id="del-no" type="button">Keep them</button>`
      : `<span><b>${label}</b> selected</span><span class="spacer"></span>
         <button class="btn sm" id="del" type="button">${icon("trash")}Delete</button>
         <button class="link-btn" id="del-clear" type="button">Clear selection</button>`;
    if (confirming) {
      $("#del-yes").onclick = deleteSelected;
      $("#del-no").onclick = () => { confirming = false; renderSelbar(); };
      $("#del-no").focus();
    } else {
      $("#del").onclick = () => { confirming = true; renderSelbar(); };
      $("#del-clear").onclick = () => { selected.clear(); renderTable(); };
    }
  }

  async function deleteSelected() {
    const btn = $("#del-yes");
    btn.disabled = true;
    try {
      const r = await fetch("/api/admin/runs/delete", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ ids: [...selected] }) });
      if (r.status === 401) { show("signin"); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        $("#selbar").innerHTML = `${icon("warn")}<span>${esc(typeof d.detail === "string" ? d.detail : "Couldn't delete them.")}</span>`;
        return;
      }
      selected.clear(); confirming = false;
      await load();
      $("#count").textContent = `Deleted ${num(d.deleted)}. ` + $("#count").textContent;
    } catch {
      $("#selbar").innerHTML = `${icon("warn")}<span>Couldn't reach the server.</span>`;
    }
  }
  for (const id of ["#q", "#f-provider", "#f-kind", "#f-mine"]) $(id).addEventListener("input", () => data && renderTable());

  /* ---------------------------------------------------------------- CSV */
  $("#csv").onclick = () => {
    const cols = ["ts", "kind", "entry", "source", "name", "provider_label", "model", "key_masked", "key_fp", "status", "error", "alert_kind", "alert_chars", "techniques", "severity", "tokens", "duration_ms", "country", "city", "browser", "visitor"];
    // A leading = + - @ makes spreadsheets run a cell as a formula, so those get a quote.
    const cell = (v) => {
      let s = Array.isArray(v) ? v.join(" ") : String(v ?? "");
      if (/^[=+\-@]/.test(s)) s = "'" + s;
      return `"${s.replace(/"/g, '""')}"`;
    };
    const csv = [cols.join(","), ...filtered().map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: `alert2playbook-usage-${new Date().toISOString().slice(0, 10)}.csv` });
    a.click();
    URL.revokeObjectURL(a.href);
  };

  load().catch(() => show("signin"));
})();

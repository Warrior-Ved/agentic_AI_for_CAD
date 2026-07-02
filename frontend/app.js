/* Agentic CAD frontend controller: drives clarify -> plan -> confirm -> execute
   and renders each stage into the conversation log. */
(function () {
  "use strict";

  const log = document.getElementById("log");
  const composer = document.getElementById("composer");
  const input = document.getElementById("instruction");
  const sendBtn = document.getElementById("sendBtn");
  const resetBtn = document.getElementById("resetBtn");
  const spinner = document.getElementById("spinner");
  const spinnerText = document.getElementById("spinnerText");

  let busy = false;

  // -------------------------------------------------------------- utilities
  function el(tag, attrs, children) {
    const n = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "class") n.className = attrs[k];
      else if (k === "html") n.innerHTML = attrs[k];
      else if (k.startsWith("on") && typeof attrs[k] === "function") n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    }
    (children || []).forEach((c) => n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  }

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function addUser(text) {
    const m = el("div", { class: "msg user" }, [el("div", { class: "bubble" }, [text])]);
    log.appendChild(m); scrollDown(); return m;
  }

  function addCard(title, tag) {
    const head = [el("span", null, [title])];
    if (tag) head.push(el("span", { class: "tag" }, [tag]));
    const body = el("div", { class: "card-body" });
    const card = el("div", { class: "msg agent" }, [
      el("div", { class: "card" }, [el("div", { class: "card-head" }, head), body]),
    ]);
    log.appendChild(card); scrollDown();
    return body;
  }

  function spin(on, text) {
    spinner.classList.toggle("hidden", !on);
    if (text) spinnerText.textContent = text;
  }
  function setBusy(on, text) {
    busy = on; sendBtn.disabled = on; spin(on, text);
  }

  async function api(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }

  const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }));

  // ---------------------------------------------------------------- health
  async function refreshHealth() {
    try {
      const h = await (await fetch("/api/health")).json();
      setPill("freecad", h.freecad ? "FreeCAD " + h.freecad : "FreeCAD ✗", !!h.freecad);
      setPill("ollama", h.ollama ? "Ollama ✓" : "Ollama ✗", h.ollama);
      setPill("model", (h.planner_model || "model") + (h.planner_available ? " ✓" : " ✗"), h.planner_available);
    } catch (e) {
      setPill("ollama", "server ✗", false);
    }
  }
  function setPill(key, text, ok) {
    const p = document.querySelector(`.pill[data-k="${key}"]`);
    if (!p) return;
    p.textContent = text;
    p.classList.toggle("ok", !!ok);
    p.classList.toggle("bad", !ok);
  }

  // ------------------------------------------------------------------ flow
  async function onSubmit(instruction) {
    if (busy || !instruction.trim()) return;
    addUser(instruction);
    input.value = "";
    setBusy(true, "Thinking about what to ask…");
    try {
      const clar = await api("/api/clarify", { instruction });
      if (clar.needs_clarification && clar.questions.length) {
        renderClarify(clar);
        setBusy(false);
      } else {
        if (clar.assumptions && clar.assumptions.length) renderAssumptions(clar.assumptions);
        await generatePlan({});
      }
    } catch (e) {
      renderError(e.message);
      setBusy(false);
    }
  }

  function renderAssumptions(assumptions) {
    const body = addCard("Assumptions", "no questions needed");
    body.appendChild(el("ul", { class: "rationale-list" },
      assumptions.map((a) => el("li", null, [a]))));
  }

  function renderClarify(clar) {
    const body = addCard("A few questions before I build", "clarify");
    const form = el("div");
    clar.questions.forEach((q) => {
      const wrap = el("div", { class: "q" });
      wrap.appendChild(el("div", { class: "qtext" }, [q.question]));
      if (q.why) wrap.appendChild(el("div", { class: "qwhy" }, [q.why]));
      const field = el("input", { type: "text", "data-qid": q.id,
        placeholder: q.suggested ? ("default: " + q.suggested) : "" });
      if (q.suggested) field.value = q.suggested;
      if (q.options && q.options.length) {
        const opts = el("div", { class: "opts" });
        q.options.forEach((o) => opts.appendChild(el("span",
          { class: "chip" + (o === q.suggested ? " suggested" : ""),
            onclick: () => { field.value = o; } }, [o])));
        wrap.appendChild(opts);
      }
      wrap.appendChild(field);
      form.appendChild(wrap);
    });
    if (clar.assumptions && clar.assumptions.length) {
      form.appendChild(el("div", { class: "qwhy", style: "margin-top:10px" },
        ["Also assuming: " + clar.assumptions.join("; ")]));
    }
    const go = el("button", { class: "primary", style: "margin-top:12px",
      onclick: async () => {
        const answers = {};
        form.querySelectorAll("input[data-qid]").forEach((i) => { answers[i.getAttribute("data-qid")] = i.value.trim(); });
        go.disabled = true;
        await generatePlan(answers);
      } }, ["Generate plan →"]);
    form.appendChild(go);
    body.appendChild(form);
  }

  async function generatePlan(answers) {
    setBusy(true, "Planning the build…");
    try {
      const data = await api("/api/plan", { answers });
      renderPlan(data);
      await maybeShowModel(data.view_token, data.preview);
    } catch (e) {
      renderError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function renderPlan(data) {
    const p = data.plan, pv = data.preview;
    const body = addCard("Proposed plan", "preview");
    body.appendChild(el("div", { class: "qtext", style: "margin-bottom:8px" }, [p.summary]));

    const ol = el("ol", { class: "steps" });
    const stepStatus = {};
    (pv.steps || []).forEach((s) => { stepStatus[s.step] = s; });
    p.steps.forEach((s) => {
      const st = stepStatus[s.step];
      const argStr = Object.entries(s.args || {}).map(([k, v]) => `${k}=${v}`).join(", ");
      const row = el("li", null, [
        el("span", { class: "n" }, [String(s.step)]),
        el("span", null, [
          el("span", { class: "tool" }, [s.tool]),
          el("span", { class: "args" }, ["(" + argStr + ")"]),
        ]),
      ]);
      if (st) row.appendChild(el("span", { class: "st " + (st.ok ? "ok" : "fail") }, [st.ok ? "✓" : "✗"]));
      ol.appendChild(row);
      if (s.rationale) ol.appendChild(el("div", { class: "rationale" }, ["↳ " + s.rationale]));
    });
    body.appendChild(ol);

    // preview outcome banner
    if (pv.success) {
      body.appendChild(el("div", { class: "preview-line" }, [
        el("span", null, [el("span", null, ["Preview volume "]), el("b", null, [fmt(pv.final_volume) + " mm³"])]),
        geomChips(pv.geometry),
      ].filter(Boolean)));
      body.appendChild(el("div", { class: "banner ok" }, ["Preview built cleanly in a throwaway document — the live model is untouched. Approve to commit it."]));
    } else {
      const bad = (pv.steps || []).find((s) => !s.ok);
      body.appendChild(el("div", { class: "banner bad" },
        ["Preview failed: " + (bad ? `step ${bad.step} (${bad.tool}) — ${bad.error}` : pv.message)]));
    }

    // actions
    const actions = el("div", { class: "actions" });
    const approve = el("button", { class: "approve", disabled: !pv.success,
      onclick: () => doExecute(actions) }, ["✓ Approve & build"]);
    const rejectBtn = el("button", { class: "danger",
      onclick: () => toggleReject(body, actions) }, ["✗ Reject"]);
    actions.appendChild(approve);
    actions.appendChild(rejectBtn);
    body.appendChild(actions);
  }

  function geomChips(geom) {
    if (!geom || !geom.bbox) return null;
    const b = geom.bbox;
    return el("span", null, [
      el("span", null, ["Bounding box "]),
      el("b", null, [`${fmt(b.x_len)} × ${fmt(b.y_len)} × ${fmt(b.z_len)} mm`]),
    ]);
  }

  function toggleReject(body, actions) {
    if (body.querySelector(".reject-row")) return;
    const row = el("div", { class: "reject-row" });
    const fb = el("input", { type: "text", placeholder: "What should change? (e.g. move hole to the centre, make it blind 5mm deep)" });
    const send = el("button", { class: "primary",
      onclick: async () => {
        actions.remove(); row.remove();
        setBusy(true, "Revising the plan…");
        try {
          const data = await api("/api/replan", { feedback: fb.value.trim() || "please revise" });
          renderPlan(data);
          await maybeShowModel(data.view_token, data.preview);
        } catch (e) { renderError(e.message); }
        finally { setBusy(false); }
      } }, ["Send"]);
    row.appendChild(fb); row.appendChild(send);
    body.appendChild(row);
    fb.focus();
  }

  async function doExecute(actions) {
    actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
    setBusy(true, "Building into the live model…");
    try {
      const res = await api("/api/execute", {});
      renderExecution(res);
      if (res.success) await showModel(res.view_token);
    } catch (e) {
      renderError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function renderExecution(res) {
    const body = addCard(res.success ? "Built ✓" : "Build failed", "live model");
    if (res.success) {
      const g = res.geometry || {};
      const mp = g.mass_properties || {};
      body.appendChild(el("div", { class: "banner ok" }, ["Committed to the live model as a single undo step."]));
      body.appendChild(el("div", { class: "preview-line" }, [
        el("span", null, [el("span", null, ["Volume "]), el("b", null, [fmt(mp.volume_mm3) + " mm³"])]),
        el("span", null, [el("span", null, ["Surface "]), el("b", null, [fmt(mp.area_mm2) + " mm²"])]),
        el("span", null, [el("span", null, ["Solids "]), el("b", null, [String(mp.solid_count ?? "—")])]),
      ]));
      updateStats(g);
      enableDownloads(res.view_token);
    } else {
      const bad = (res.steps || []).find((s) => !s.ok);
      body.appendChild(el("div", { class: "banner bad" },
        [res.message || (bad ? `step ${bad.step} (${bad.tool}) — ${bad.error}` : "unknown error")]));
    }
  }

  function renderError(msg) {
    const body = addCard("Error", "!");
    body.appendChild(el("div", { class: "banner bad" }, [msg]));
  }

  // ------------------------------------------------------------- 3-D model
  async function maybeShowModel(token, preview) {
    if (preview && preview.success && preview.views && preview.views.stl) await showModel(token);
  }

  async function showModel(token) {
    try {
      await Viewer.loadSTL("/api/model.stl?v=" + (token || Date.now()));
    } catch (e) { /* keep the hint visible */ }
  }

  function updateStats(geom) {
    const stats = document.getElementById("stats");
    if (!geom || !geom.bbox) { stats.textContent = ""; return; }
    const b = geom.bbox, mp = geom.mass_properties || {};
    stats.innerHTML =
      `Size <b>${fmt(b.x_len)}×${fmt(b.y_len)}×${fmt(b.z_len)}</b> mm` +
      ` &nbsp; Vol <b>${fmt(mp.volume_mm3)}</b> mm³` +
      ` &nbsp; Faces <b>${mp.face_count ?? "—"}</b>`;
  }

  function enableDownloads(token) {
    const step = document.getElementById("dlStep");
    const stl = document.getElementById("dlStl");
    step.href = "/api/model.step?v=" + token;
    stl.href = "/api/model.stl?v=" + token;
    step.classList.remove("disabled");
    stl.classList.remove("disabled");
  }

  // --------------------------------------------------------------- wiring
  composer.addEventListener("submit", (e) => { e.preventDefault(); onSubmit(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(input.value); }
  });
  resetBtn.addEventListener("click", async () => {
    await api("/api/reset", {});
    // wipe the log except the intro
    Array.from(log.children).forEach((c, i) => { if (i > 0) c.remove(); });
    Viewer.clear();
    document.getElementById("stats").textContent = "";
    document.getElementById("dlStep").classList.add("disabled");
    document.getElementById("dlStl").classList.add("disabled");
  });

  window.addEventListener("DOMContentLoaded", () => {
    Viewer.mount(document.getElementById("viewer"));
    refreshHealth();
    setInterval(refreshHealth, 15000);
    input.focus();
  });
})();

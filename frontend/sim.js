/* Simulation panel controller: pick faces in the 3-D viewer, configure a
   static-structural or thermal analysis, run it on the backend (CalculiX),
   and render the colour-mapped result field with a legend + deform slider. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const panel = $("simPanel");
  const toggleBtn = $("simToggle");
  const statusEl = $("simStatus");

  const SET_COLORS = { fixed: "#3d9bff", load: "#ef5f6b", hot: "#f0a94c", cold: "#4fd6ff" };
  const SET_LABEL = { fixed: "fixed", load: "load", hot: "hot", cold: "cold" };

  const state = {
    open: false,
    object: null,
    faceCount: 0,
    sets: { fixed: new Set(), load: new Set(), hot: new Set(), cold: new Set() },
    activeSet: null,
    result: null,
    maxDisp: 0,
    deformMax: 1,
    poll: null,
  };

  // ------------------------------------------------------------- helpers
  async function api(path, body) {
    const res = await fetch(path, body === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }

  function setStatus(text, kind) {
    statusEl.textContent = text || "";
    statusEl.className = "sim-status" + (kind ? " " + kind : "");
  }

  function simTypeSets() {
    return $("simType").value === "static" ? ["fixed", "load"] : ["hot", "cold"];
  }

  // ------------------------------------------------------------ open/close
  async function openPanel() {
    try {
      setStatus("loading model faces…");
      const data = await api("/api/sim/faces");
      state.object = data.object;
      state.faceCount = data.faces.length;
      Object.values(state.sets).forEach((s) => s.clear());
      Viewer.showFaces(data.faces);
      panel.classList.remove("hidden");
      toggleBtn.classList.add("active");
      state.open = true;
      showSetup();
      setStatus(`analysing ${state.object} — pick faces, then run`);
      refreshChips();
    } catch (e) {
      setStatus("", null);
      panel.classList.add("hidden");
      alert("Simulation needs a built part first — " + e.message);
    }
  }

  function closePanel() {
    state.open = false;
    state.activeSet = null;
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
    panel.classList.add("hidden");
    toggleBtn.classList.remove("active");
    Viewer.clearSim();
    document.querySelectorAll(".pick-btn").forEach((b) => b.classList.remove("active"));
  }

  // -------------------------------------------------------------- picking
  function activatePick(setName) {
    state.activeSet = setName;
    document.querySelectorAll(".pick-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.set === setName));
    Viewer.setPickHandler((face) => togglePick(face));
    setStatus(`click faces in the viewer to toggle them as ${SET_LABEL[setName]}`);
  }

  function togglePick(face) {
    const set = state.sets[state.activeSet];
    if (!set) return;
    if (set.has(face)) {
      set.delete(face);
    } else {
      Object.values(state.sets).forEach((s) => s.delete(face)); // one role per face
      set.add(face);
    }
    recolor();
    refreshChips();
  }

  function recolor() {
    const map = {};
    for (const [name, set] of Object.entries(state.sets)) {
      set.forEach((f) => { map[f] = SET_COLORS[name]; });
    }
    Viewer.setFaceColors(map);
  }

  function refreshChips() {
    for (const name of Object.keys(state.sets)) {
      const holder = $("chips-" + name);
      if (!holder) continue;
      holder.innerHTML = "";
      state.sets[name].forEach((f) => {
        const chip = document.createElement("span");
        chip.className = "chip sim-chip";
        chip.style.borderColor = SET_COLORS[name];
        chip.textContent = f;
        chip.title = "click to remove";
        chip.onclick = () => { state.sets[name].delete(f); recolor(); refreshChips(); };
        holder.appendChild(chip);
      });
    }
  }

  // ------------------------------------------------------------------ run
  function buildBody() {
    const simType = $("simType").value;
    const body = {
      sim_type: simType,
      material: $("simMaterial").value,
      mesh_size: parseFloat($("simMesh").value) || null,
    };
    if (simType === "static") {
      body.fixed_faces = [...state.sets.fixed];
      body.load_faces = [...state.sets.load];
      body.force_n = parseFloat($("simForce").value);
      body.direction = $("simDir").value;
      if (!body.fixed_faces.length) throw new Error("pick at least one FIXED face");
      if (!body.load_faces.length) throw new Error("pick at least one LOAD face");
      if (!(body.force_n > 0)) throw new Error("force must be a positive number of newtons");
    } else {
      body.hot_faces = [...state.sets.hot];
      body.cold_faces = [...state.sets.cold];
      body.hot_temp_c = parseFloat($("simHotT").value);
      body.cold_temp_c = parseFloat($("simColdT").value);
      if (!body.hot_faces.length) throw new Error("pick at least one HOT face");
      if (!body.cold_faces.length) throw new Error("pick at least one COLD face");
      if (!(body.hot_temp_c > body.cold_temp_c)) throw new Error("hot temperature must exceed cold");
    }
    return body;
  }

  async function run() {
    let body;
    try { body = buildBody(); }
    catch (e) { setStatus(e.message, "bad"); return; }

    $("simRun").disabled = true;
    setStatus("meshing + solving (CalculiX)…", "busy");
    try {
      await api("/api/sim/run", body);
    } catch (e) {
      setStatus(e.message, "bad");
      $("simRun").disabled = false;
      return;
    }
    state.poll = setInterval(async () => {
      try {
        const st = await api("/api/sim/status");
        if (st.state === "running") return;
        clearInterval(state.poll); state.poll = null;
        $("simRun").disabled = false;
        if (st.state === "error") { setStatus(st.error, "bad"); return; }
        const result = await api("/api/sim/result");
        renderResults(result);
      } catch (e) {
        clearInterval(state.poll); state.poll = null;
        $("simRun").disabled = false;
        setStatus(e.message, "bad");
      }
    }, 1000);
  }

  // -------------------------------------------------------------- results
  function showSetup() {
    $("simResults").classList.add("hidden");
    $("simSetup").classList.remove("hidden");
    onTypeChange();
  }

  function renderResults(payload) {
    state.result = payload;
    $("simSetup").classList.add("hidden");
    $("simResults").classList.remove("hidden");
    setStatus("done in " + (payload.summary.solve_seconds ?? "?") + "s", "ok");

    // field selector
    const sel = $("simField");
    sel.innerHTML = "";
    Object.entries(payload.fields).forEach(([key, f]) => {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = f.label + " (" + f.unit + ")";
      sel.appendChild(opt);
    });
    const first = payload.sim_type === "static" ? "von_mises" : "temperature";
    sel.value = payload.fields[first] ? first : Object.keys(payload.fields)[0];

    // deform slider scale: 100% of slider ≈ deformation of 10% of model size
    state.maxDisp = payload.summary.max_displacement_mm || 0;
    const diag = modelDiag(payload.nodes);
    state.deformMax = state.maxDisp > 1e-9 ? (0.1 * diag) / state.maxDisp : 0;
    $("simDeform").value = 0;
    $("deformVal").textContent = "×0";
    $("deformWrap").classList.toggle("hidden", state.deformMax === 0);

    const range = Viewer.showResult(payload, sel.value);
    updateLegend(range);
    renderSummary(payload.summary);
  }

  function modelDiag(nodes) {
    let min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < nodes.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        if (nodes[i + k] < min[k]) min[k] = nodes[i + k];
        if (nodes[i + k] > max[k]) max[k] = nodes[i + k];
      }
    }
    return Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) || 1;
  }

  function updateLegend(range) {
    if (!range) return;
    const f = (v) => Math.abs(v) >= 1000 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3);
    $("legMin").textContent = f(range.min) + " " + range.unit;
    $("legMax").textContent = f(range.max) + " " + range.unit;
    $("legLabel").textContent = range.label;
  }

  function renderSummary(s) {
    const parts = [];
    const b = (t) => `<b>${t}</b>`;
    if (s.sim_type === "static") {
      parts.push(`Max stress ${b(s.max_von_mises_mpa + " MPa")}`);
      parts.push(`Max deflection ${b(s.max_displacement_mm + " mm")}`);
      if (s.safety_factor != null) {
        const cls = s.safety_factor >= 2 ? "good" : s.safety_factor >= 1 ? "warn" : "bad";
        parts.push(`Safety factor <b class="${cls}">${s.safety_factor}</b> (yield ${s.yield_strength_mpa} MPa)`);
      }
    } else {
      parts.push(`Temperature ${b(s.min_temp_c + " – " + s.max_temp_c + " °C")}`);
      parts.push(`Thermal expansion ${b(s.max_displacement_mm + " mm")}`);
    }
    parts.push(`${s.material}, ${s.nodes.toLocaleString()} nodes / ${s.elements.toLocaleString()} elements`);
    $("simSummary").innerHTML = parts.join(" &nbsp;·&nbsp; ");
  }

  // --------------------------------------------------------------- wiring
  function onTypeChange() {
    const isStatic = $("simType").value === "static";
    $("simStatic").classList.toggle("hidden", !isStatic);
    $("simThermal").classList.toggle("hidden", isStatic);
    const valid = simTypeSets().includes(state.activeSet);
    if (!valid && state.activeSet) activatePick(simTypeSets()[0]);
  }

  toggleBtn.addEventListener("click", () => (state.open ? closePanel() : openPanel()));
  $("simType").addEventListener("change", onTypeChange);
  document.querySelectorAll(".pick-btn").forEach((b) =>
    b.addEventListener("click", () => activatePick(b.dataset.set)));
  $("simRun").addEventListener("click", run);
  $("simField").addEventListener("change", () => {
    const range = Viewer.setResultField($("simField").value);
    updateLegend(range);
  });
  $("simDeform").addEventListener("input", () => {
    const frac = parseFloat($("simDeform").value) / 100;
    const scale = frac * state.deformMax;
    $("deformVal").textContent = "×" + (scale >= 10 ? scale.toFixed(0) : scale.toFixed(1));
    Viewer.setDeform(scale);
  });
  $("simBack").addEventListener("click", async () => {
    showSetup();
    const data = await api("/api/sim/faces");
    Viewer.showFaces(data.faces);
    recolor();
    setStatus(`analysing ${state.object} — adjust and re-run`);
  });

  // A newly built model invalidates any open simulation setup.
  window.addEventListener("model-loaded", () => { if (state.open) closePanel(); });
})();

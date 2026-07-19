/* Self-contained three.js viewer for the CAD app.
   Layers: the model STL, a pickable per-face group (simulation setup), and a
   colour-mapped FEM result mesh. Exactly one layer is visible at a time.
   Exposes window.Viewer with:
     mount, loadSTL, clear,
     showFaces, setFaceColors, setPickHandler,
     showResult, setResultField, setDeform, clearSim */
(function () {
  "use strict";

  let renderer, scene, camera, controls, mesh, grid, hint;
  let container;

  // --- simulation layers ---
  let faceGroup = null;          // THREE.Group of one mesh per CAD face
  let resultRoot = null;         // group holding the FEM result mesh
  let resultData = null;         // { geometry, base, disp, fields, activeField }
  let pickHandler = null;
  let downXY = null;

  const BASE_FACE = 0x8ea6c0;

  function mount(el) {
    container = el;
    hint = document.getElementById("viewerHint");

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0e1116);

    const w = el.clientWidth || 600, h = el.clientHeight || 400;
    camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100000);
    camera.position.set(90, 70, 120);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(w, h);
    el.appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x1a2230, 0.95));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(1, 1.4, 0.8);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x88bbff, 0.35);
    rim.position.set(-1, -0.6, -1);
    scene.add(rim);

    grid = new THREE.GridHelper(400, 40, 0x2a3340, 0x1c232d);
    scene.add(grid);
    scene.add(new THREE.AxesHelper(30));

    renderer.domElement.addEventListener("pointerdown", (e) => { downXY = [e.clientX, e.clientY]; });
    renderer.domElement.addEventListener("pointerup", onPointerUp);

    window.addEventListener("resize", onResize);
    animate();
  }

  function onResize() {
    if (!container) return;
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  function animate() {
    requestAnimationFrame(animate);
    if (controls) controls.update();
    if (renderer) renderer.render(scene, camera);
  }

  // ------------------------------------------------------------ disposal
  function disposeObject(root) {
    if (!root) return;
    root.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose());
    });
    scene.remove(root);
  }

  function clearMesh() {
    if (mesh) { disposeObject(mesh); mesh = null; }
  }

  function clearSim() {
    if (faceGroup) { disposeObject(faceGroup); faceGroup = null; }
    if (resultRoot) { disposeObject(resultRoot); resultRoot = null; resultData = null; }
    pickHandler = null;
    if (mesh) mesh.visible = true;
  }

  function clear() {
    clearMesh();
    clearSim();
    if (hint) hint.style.display = "grid";
  }

  // ------------------------------------------------------- camera framing
  function frameBox(bb) {
    const size = new THREE.Vector3();
    bb.getSize(size);
    const maxDim = Math.max(size.x, size.y, size.z) || 10;
    const dist = maxDim * 2.2;
    camera.position.set(dist * 0.75, dist * 0.6, dist);
    camera.near = maxDim / 100;
    camera.far = maxDim * 100;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0);
    controls.update();
    grid.position.y = -size.y / 2 - 0.01;
    const g = Math.ceil(maxDim * 2 / 20) * 20 || 40;
    grid.scale.setScalar(g / 400);
  }

  // FreeCAD is Z-up; the scene is Y-up. Rotate the group, then translate its
  // world-space centre to the origin (translation applies after rotation).
  function orientAndCenter(group) {
    group.rotation.x = -Math.PI / 2;
    group.updateMatrixWorld(true);
    const bb = new THREE.Box3().setFromObject(group);
    const c = new THREE.Vector3();
    bb.getCenter(c);
    group.position.set(-c.x, -c.y, -c.z);
    group.updateMatrixWorld(true);
    return new THREE.Box3().setFromObject(group);
  }

  // --------------------------------------------------------------- model STL
  function loadSTL(url) {
    const loader = new THREE.STLLoader();
    return new Promise((resolve, reject) => {
      loader.load(
        url,
        function (geometry) {
          clearMesh();
          clearSim();                     // a new model invalidates sim layers
          geometry.rotateX(-Math.PI / 2);
          geometry.computeVertexNormals();
          geometry.computeBoundingBox();
          const center = new THREE.Vector3();
          geometry.boundingBox.getCenter(center);
          geometry.translate(-center.x, -center.y, -center.z);
          geometry.computeBoundingBox();

          const material = new THREE.MeshStandardMaterial({
            color: BASE_FACE, metalness: 0.25, roughness: 0.55, flatShading: false,
          });
          mesh = new THREE.Mesh(geometry, material);
          const edges = new THREE.EdgesGeometry(geometry, 25);
          mesh.add(new THREE.LineSegments(
            edges, new THREE.LineBasicMaterial({ color: 0x0e1116, transparent: true, opacity: 0.35 })));
          scene.add(mesh);

          frameBox(geometry.boundingBox);
          if (hint) hint.style.display = "none";
          window.dispatchEvent(new CustomEvent("model-loaded"));
          resolve(geometry);
        },
        undefined,
        function (err) { reject(err); }
      );
    });
  }

  // ------------------------------------------------------ face picking layer
  function showFaces(faces) {
    clearSim();
    if (mesh) mesh.visible = false;
    if (resultRoot) resultRoot.visible = false;

    faceGroup = new THREE.Group();
    faces.forEach((f) => {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(f.vertices, 3));
      geo.setIndex(f.triangles);
      geo.computeVertexNormals();
      const mat = new THREE.MeshStandardMaterial({
        color: BASE_FACE, metalness: 0.2, roughness: 0.6,
        side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1,
      });
      const m = new THREE.Mesh(geo, mat);
      m.userData.face = f.name;
      const edges = new THREE.EdgesGeometry(geo, 25);
      m.add(new THREE.LineSegments(
        edges, new THREE.LineBasicMaterial({ color: 0x0e1116, transparent: true, opacity: 0.4 })));
      faceGroup.add(m);
    });
    scene.add(faceGroup);
    frameBox(orientAndCenter(faceGroup));
    if (hint) hint.style.display = "none";
  }

  function setFaceColors(map) {
    if (!faceGroup) return;
    faceGroup.children.forEach((m) => {
      const c = map[m.userData.face];
      m.material.color.set(c || BASE_FACE);
      m.material.emissive.set(c ? m.material.color : 0x000000);
      m.material.emissiveIntensity = c ? 0.25 : 0;
    });
  }

  function setPickHandler(fn) { pickHandler = fn; }

  function onPointerUp(e) {
    if (!pickHandler || !faceGroup || !faceGroup.visible || !downXY) return;
    if (Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]) > 5) return; // drag = orbit
    const rect = renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, camera);
    const hits = ray.intersectObjects(faceGroup.children, false);
    if (hits.length) pickHandler(hits[0].object.userData.face);
  }

  // -------------------------------------------------------- FEM result layer
  function jet(t) {
    const r = Math.min(Math.max(1.5 - Math.abs(4 * t - 3), 0), 1);
    const g = Math.min(Math.max(1.5 - Math.abs(4 * t - 2), 0), 1);
    const b = Math.min(Math.max(1.5 - Math.abs(4 * t - 1), 0), 1);
    return [r, g, b];
  }

  function showResult(payload, fieldName) {
    if (faceGroup) faceGroup.visible = false;
    if (mesh) mesh.visible = false;
    if (resultRoot) { disposeObject(resultRoot); resultRoot = null; }

    const geo = new THREE.BufferGeometry();
    const base = new Float32Array(payload.nodes);
    geo.setAttribute("position", new THREE.BufferAttribute(base.slice(), 3));
    geo.setIndex(payload.triangles);
    geo.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(base.length), 3));
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, metalness: 0.05, roughness: 0.7, side: THREE.DoubleSide,
    });
    const m = new THREE.Mesh(geo, mat);
    resultRoot = new THREE.Group();
    resultRoot.add(m);
    scene.add(resultRoot);

    resultData = {
      geometry: geo,
      base,
      disp: new Float32Array(payload.displacements),
      fields: payload.fields,
      activeField: null,
      deform: 0,
    };
    const range = setResultField(fieldName);
    frameBox(orientAndCenter(resultRoot));
    if (hint) hint.style.display = "none";
    return range;
  }

  function setResultField(fieldName) {
    if (!resultData || !resultData.fields[fieldName]) return null;
    const vals = resultData.fields[fieldName].values;
    let min = Infinity, max = -Infinity;
    for (const v of vals) { if (v < min) min = v; if (v > max) max = v; }
    const span = max - min || 1;
    const colors = resultData.geometry.attributes.color;
    for (let i = 0; i < vals.length; i++) {
      const [r, g, b] = jet((vals[i] - min) / span);
      colors.setXYZ(i, r, g, b);
    }
    colors.needsUpdate = true;
    resultData.activeField = fieldName;
    return { min, max, unit: resultData.fields[fieldName].unit,
             label: resultData.fields[fieldName].label };
  }

  function setDeform(scale) {
    if (!resultData) return;
    const pos = resultData.geometry.attributes.position;
    const { base, disp } = resultData;
    for (let i = 0; i < base.length; i++) pos.array[i] = base[i] + disp[i] * scale;
    pos.needsUpdate = true;
    resultData.geometry.computeVertexNormals();
    resultData.deform = scale;
  }

  window.Viewer = {
    mount, loadSTL, clear,
    showFaces, setFaceColors, setPickHandler,
    showResult, setResultField, setDeform, clearSim,
  };
})();

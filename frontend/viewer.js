/* Minimal, self-contained three.js STL viewer for the CAD preview.
   Exposes window.Viewer with { mount, loadSTL, clear, setSpinner }. */
(function () {
  "use strict";

  let renderer, scene, camera, controls, mesh, grid, hint;
  let container;

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

  function clearMesh() {
    if (mesh) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      mesh.material.dispose();
      mesh = null;
    }
  }

  function clear() {
    clearMesh();
    if (hint) hint.style.display = "grid";
  }

  // Load an STL from a URL and frame it nicely.
  function loadSTL(url) {
    const loader = new THREE.STLLoader();
    return new Promise((resolve, reject) => {
      loader.load(
        url,
        function (geometry) {
          clearMesh();
          geometry.rotateX(-Math.PI / 2); // FreeCAD Z-up -> three.js Y-up
          geometry.computeVertexNormals();
          geometry.computeBoundingBox();
          const bb = geometry.boundingBox;
          const center = new THREE.Vector3();
          bb.getCenter(center);
          geometry.translate(-center.x, -center.y, -center.z); // centre on origin
          geometry.computeBoundingBox();

          const material = new THREE.MeshStandardMaterial({
            color: 0x8ea6c0, metalness: 0.25, roughness: 0.55,
            flatShading: false,
          });
          mesh = new THREE.Mesh(geometry, material);

          // wireframe edges for an engineering look
          const edges = new THREE.EdgesGeometry(geometry, 25);
          const line = new THREE.LineSegments(
            edges, new THREE.LineBasicMaterial({ color: 0x0e1116, transparent: true, opacity: 0.35 })
          );
          mesh.add(line);
          scene.add(mesh);

          frameObject(geometry.boundingBox);
          if (hint) hint.style.display = "none";
          resolve(geometry);
        },
        undefined,
        function (err) { reject(err); }
      );
    });
  }

  // Move the camera + grid so the part fills the view regardless of size.
  function frameObject(bb) {
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

    // put the grid just under the part (Y is up after the rotation)
    grid.position.y = -size.y / 2 - 0.01;
    const g = Math.ceil(maxDim * 2 / 20) * 20 || 40;
    grid.scale.setScalar(g / 400);
  }

  window.Viewer = { mount, loadSTL, clear };
})();

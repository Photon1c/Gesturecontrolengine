/* Classic script — no ES modules (avoids strict MIME issues on Windows). */
(function () {
  if (typeof THREE === "undefined") {
    console.error("[Blackwing] THREE not loaded — check vendor/three.min.js");
    return;
  }

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x6a8aaa);
  scene.fog = new THREE.Fog(0x6a8aaa, 40, 120);

  const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 300);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(innerWidth, innerHeight);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  document.body.appendChild(renderer.domElement);

  const chaseTarget = new THREE.Vector3(0, 10, -6);
  const cameraOffset = new THREE.Vector3(0, 4, 14);

  scene.add(new THREE.HemisphereLight(0xdde8ff, 0x2a3040, 0.9));
  const sun = new THREE.DirectionalLight(0xfff2d0, 1.1);
  sun.position.set(20, 40, 10);
  sun.castShadow = true;
  scene.add(sun);

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(200, 200),
    new THREE.MeshStandardMaterial({ color: 0x3d5a45, roughness: 0.95 })
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const grid = new THREE.GridHelper(120, 60, 0x4a6070, 0x3a4a58);
  grid.position.y = 0.02;
  scene.add(grid);

  const crowMeshes = new Map();

  function buildCrow(color) {
    const group = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.35, 1.2, 6, 12),
      new THREE.MeshStandardMaterial({ color, metalness: 0.15, roughness: 0.65 })
    );
    body.rotation.z = Math.PI / 2;
    body.castShadow = true;
    group.add(body);

    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.28, 12, 10),
      new THREE.MeshStandardMaterial({ color: 0x111118, roughness: 0.5 })
    );
    head.position.set(0.85, 0.15, 0);
    head.castShadow = true;
    group.add(head);

    const beak = new THREE.Mesh(
      new THREE.ConeGeometry(0.1, 0.35, 6),
      new THREE.MeshStandardMaterial({ color: 0x3a3028 })
    );
    beak.rotation.z = -Math.PI / 2;
    beak.position.set(1.15, 0.12, 0);
    group.add(beak);

    const wingMat = new THREE.MeshStandardMaterial({
      color: 0x252530,
      side: THREE.DoubleSide,
      roughness: 0.7,
    });
    const leftWing = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 0.55), wingMat);
    leftWing.position.set(-0.1, 0.1, 0.55);
    leftWing.rotation.y = 0.3;
    group.add(leftWing);

    const rightWing = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 0.55), wingMat);
    rightWing.position.set(-0.1, 0.1, -0.55);
    rightWing.rotation.y = -0.3;
    group.add(rightWing);

    const tail = new THREE.Mesh(
      new THREE.ConeGeometry(0.2, 0.7, 5),
      new THREE.MeshStandardMaterial({ color: 0x181820 })
    );
    tail.rotation.z = Math.PI / 2 + 0.4;
    tail.position.set(-0.95, 0.05, 0);
    group.add(tail);

    group.userData.wings = { left: leftWing, right: rightWing };
    return group;
  }

  function ensureCrow(id, index) {
    if (crowMeshes.has(id)) return crowMeshes.get(id);
    const tint = index === 0 ? 0x1a1a22 : 0x2a2830;
    const mesh = buildCrow(tint);
    scene.add(mesh);
    crowMeshes.set(id, mesh);
    return mesh;
  }

  function applyFlock(state) {
    const flock = state.flock || [];
    flock.forEach(function (bird, i) {
      const mesh = ensureCrow(bird.id, i);
      const pos = bird.position;
      const rot = bird.rotation;
      mesh.position.set(pos[0], pos[1], pos[2]);
      mesh.rotation.set(rot[0], rot[1], rot[2]);

      const phase = bird.wing_phase || 0;
      const flap = (state.controls && state.controls.flap_power) || 0;
      const wingAngle = Math.sin(phase) * (0.25 + flap * 0.9);
      mesh.userData.wings.left.rotation.x = -wingAngle;
      mesh.userData.wings.right.rotation.x = wingAngle;
    });

    if (flock[0] && flock[0].position) {
      const p = flock[0].position;
      chaseTarget.set(p[0], p[1] + 2, p[2]);
    }
  }

  function updateCamera() {
    const desired = new THREE.Vector3().copy(chaseTarget).add(cameraOffset);
    camera.position.lerp(desired, 0.04);
    camera.lookAt(chaseTarget);
  }

  function animate() {
    requestAnimationFrame(animate);
    const state = window.__blackwingState;
    if (state) applyFlock(state);
    updateCamera();
    renderer.render(scene, camera);
  }

  addEventListener("resize", function () {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  animate();
})();

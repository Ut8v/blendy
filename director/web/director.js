// Director viewport: the browser is an input device, not a renderer.
// Z-up <-> Y-up and FOV arithmetic mirror compiler/coords.py exactly.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const b2t = v => new THREE.Vector3(v[0], v[2], -v[1]);           // coords.blender_to_gltf
const t2b = v => [v.x, -v.z, v.y];                                // coords.gltf_to_blender
function offsetBlender(d, azDeg, elDeg) {                         // coords.spherical_to_offset
  const az = azDeg * Math.PI / 180, el = elDeg * Math.PI / 180, r = d * Math.cos(el);
  return [r * Math.sin(az), -r * Math.cos(az), d * Math.sin(el)];
}
function vfov(focal, aspect, sensor) {                            // coords.focal_to_three_vfov
  const h = 2 * Math.atan(sensor / (2 * focal));
  return 2 * Math.atan(Math.tan(h / 2) / aspect) * 180 / Math.PI;
}

export function mountDirector(container, frameEl, api, studio) {
  const st = { shot: null, keys: [], live: null, playing: false, frame: 1, target: null, dist: 4, az: 0, el: 10, roll: 0, focal: 50, landmarks: {}, mixer: null, proxyRoot: null };
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2c2c32);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 1.2));
  scene.add(new THREE.GridHelper(20, 20, 0x555555, 0x3a3a3a));
  const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 500);
  const marker = new THREE.Mesh(new THREE.SphereGeometry(0.03, 12, 8), new THREE.MeshBasicMaterial({ color: 0xff8800 }));
  scene.add(camera, marker);

  const ui = document.createElement('div');
  ui.className = 'director-controls';
  ui.innerHTML = `
    <div class="kv"><span>frame</span><code data-k="frameNo">1</code></div>
    <input data-k="scrub" type="range" min="1" max="48" value="1">
    <div><button data-k="play">play</button> <button data-k="stop">stop</button></div>
    <h4>target</h4><select data-k="target" style="width:100%"></select>
    <div class="kv"><span>snapped</span><code data-k="snapped">–</code></div>
    <h4>camera</h4>
    <label>distance <code data-k="distV"></code><input data-k="dist" type="range" min="0.3" max="20" step="0.01" value="4"></label>
    <label>azimuth <code data-k="azV"></code><input data-k="az" type="range" min="-180" max="180" step="0.5" value="0"></label>
    <label>elevation <code data-k="elV"></code><input data-k="el" type="range" min="-89" max="89" step="0.5" value="10"></label>
    <label>roll <code data-k="rollV"></code><input data-k="roll" type="range" min="-45" max="45" step="0.5" value="0"></label>
    <label>focal <select data-k="focal" style="width:100%"></select></label>
    <div class="kv muted">drag orbit · wheel distance · shift+drag elevation</div>
    <h4>record</h4>
    <div><button data-k="key">set key</button> <button data-k="live" class="rec">● live</button> <button data-k="clear">clear</button></div>
    <div class="kv"><span>keys</span><code data-k="nkeys">0</code></div>
    <div><button data-k="save" class="primary">save take</button> <span data-k="status" class="muted"></span></div>`;
  container.appendChild(ui);
  const $ = k => ui.querySelector(`[data-k="${k}"]`);

  function targetWorld() {
    const t = st.target;
    if (t && st.landmarks[t]) { const p = new THREE.Vector3(); st.landmarks[t].getWorldPosition(p); return p; }
    return new THREE.Vector3(0, 1, 0);
  }
  function placeCamera() {
    if (!st.shot) return;
    const tgt = targetWorld();
    camera.position.copy(tgt).add(b2t(offsetBlender(st.dist, st.az, st.el)));
    camera.up.set(0, 1, 0); camera.lookAt(tgt); camera.rotateZ(st.roll * Math.PI / 180);
    camera.fov = vfov(st.focal, st.shot.aspect, st.shot.sensor_width); camera.updateProjectionMatrix();
    marker.position.copy(tgt);
    $('distV').textContent = st.dist.toFixed(2) + 'm'; $('azV').textContent = st.az.toFixed(1) + '°';
    $('elV').textContent = st.el.toFixed(1) + '°'; $('rollV').textContent = st.roll.toFixed(1) + '°';
    $('snapped').textContent = st.target ? 'yes' : 'NO (world space)';
  }
  function resize() {
    const W = container.clientWidth, H = container.clientHeight; if (!W || !H) return;
    renderer.setSize(W, H, false); camera.aspect = W / H; camera.updateProjectionMatrix();
    if (!st.shot) return;
    const a = st.shot.aspect; let w = W, h = W / a; if (h > H) { h = H; w = H * a; }
    const r = container.getBoundingClientRect(), pr = container.offsetParent.getBoundingClientRect();
    Object.assign(frameEl.style, { width: w + 'px', height: h + 'px', left: (r.left - pr.left + (W - w) / 2) + 'px', top: (r.top - pr.top + (H - h) / 2) + 'px' });
  }
  function setFrame(f) { st.frame = f; $('frameNo').textContent = f; $('scrub').value = f; if (st.mixer) st.mixer.setTime((f - 1) / st.shot.fps); }
  function sample() { return { frame: st.frame, target: st.target || t2b(targetWorld()), distance: st.dist, azimuth: st.az, elevation: st.el, roll: st.roll, focal: st.focal }; }
  function sampleLive() { if (st.live) st.live.push(sample()); }

  async function load(info) {
    if (!info) return;
    st.shot = info; st.landmarks = {}; st.mixer = null;
    if (st.proxyRoot) { scene.remove(st.proxyRoot); st.proxyRoot = null; }
    $('scrub').min = info.frame_start; $('scrub').max = info.frame_end; setFrame(info.frame_start);
    $('focal').innerHTML = info.lens_set.map(f => `<option value="${f}">${f}mm</option>`).join('');
    st.focal = info.lens_set[Math.min(2, info.lens_set.length - 1)]; $('focal').value = st.focal;
    $('target').innerHTML = '';
    if (!info.proxy) { $('status').textContent = 'no proxy: click "export proxy"'; resize(); placeCamera(); return; }
    const gltf = await new GLTFLoader().loadAsync(info.proxy);
    st.proxyRoot = gltf.scene; scene.add(gltf.scene);
    gltf.scene.traverse(o => {
      if (o.isMesh) o.material = new THREE.MeshStandardMaterial({ color: 0x8a8a90, flatShading: true });
      if (o.name.startsWith('LM_')) {
        const parts = o.name.slice(3).split('_'); const asset = parts.shift(); const ref = '@' + asset + '.' + parts.join('_');
        st.landmarks[ref] = o;
        o.add(new THREE.Mesh(new THREE.SphereGeometry(0.04, 10, 6), new THREE.MeshBasicMaterial({ color: 0x33aaff })));
        const opt = document.createElement('option'); opt.value = ref; opt.textContent = ref; $('target').appendChild(opt);
      }
    });
    if (gltf.animations.length) { st.mixer = new THREE.AnimationMixer(gltf.scene); gltf.animations.forEach(c => st.mixer.clipAction(c).play()); }
    const keys = Object.keys(st.landmarks);
    st.target = keys.find(k => k.endsWith('.eye_midpoint')) || keys[0] || null;
    if (st.target) $('target').value = st.target;
    $('status').textContent = `${keys.length} landmarks`;
    resize(); placeCamera();
  }

  let drag = null;
  renderer.domElement.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, az: st.az, el: st.el }; });
  addEventListener('pointerup', () => drag = null);
  addEventListener('pointermove', e => {
    if (!drag) return;
    if (!e.shiftKey) st.az = ((drag.az + (e.clientX - drag.x) * 0.3 + 180) % 360 + 360) % 360 - 180;
    st.el = Math.max(-89, Math.min(89, drag.el - (e.clientY - drag.y) * 0.2));
    $('az').value = st.az; $('el').value = st.el; placeCamera(); sampleLive();
  });
  renderer.domElement.addEventListener('wheel', e => { e.preventDefault(); st.dist = Math.max(0.3, st.dist * (1 + e.deltaY * 0.001)); $('dist').value = st.dist; placeCamera(); sampleLive(); }, { passive: false });
  for (const k of ['dist', 'az', 'el', 'roll']) $(k).addEventListener('input', e => { st[k] = parseFloat(e.target.value); placeCamera(); sampleLive(); });
  $('focal').addEventListener('change', e => { st.focal = parseFloat(e.target.value); placeCamera(); });   // snapped to lens_set: a select, never a slider
  $('target').addEventListener('change', e => { st.target = e.target.value; placeCamera(); });
  $('scrub').addEventListener('input', e => setFrame(parseInt(e.target.value)));
  $('play').onclick = () => st.playing = true;
  $('stop').onclick = () => st.playing = false;
  $('key').onclick = () => { st.keys = st.keys.filter(k => k.frame !== st.frame); st.keys.push(sample()); st.keys.sort((a, b) => a.frame - b.frame); $('nkeys').textContent = st.keys.length; };
  $('live').onclick = () => {
    if (st.live) { st.keys = st.live; st.live = null; st.playing = false; $('live').textContent = '● live'; $('nkeys').textContent = st.keys.length; $('status').textContent = 'live take captured; save to decimate'; }
    else { st.live = []; st.playing = true; $('live').textContent = '■ stop'; $('status').textContent = 'recording…'; }
  };
  $('clear').onclick = () => { st.keys = []; $('nkeys').textContent = 0; };
  $('save').onclick = async () => {
    if (!st.keys.length) { $('status').textContent = 'nothing to save'; return; }
    const mode = st.keys.length > 40 ? 'live' : 'keyframe';
    try {
      const r = await api.post('/api/take', { shot: st.shot.shot_id, mode, samples: st.keys });
      $('status').textContent = `saved ${r.take_id.slice(5, 20)} (${r.samples} samples, ${mode})`;
      st.keys = []; $('nkeys').textContent = 0; studio.refresh();
    } catch (e) { $('status').textContent = e.message; }
  };
  addEventListener('resize', resize);

  let last = performance.now();
  (function tick(now) {
    const dt = (now - last) / 1000; last = now;
    if (st.playing && st.shot) { const f = st.frame + dt * st.shot.fps; setFrame(f > st.shot.frame_end ? st.shot.frame_start : Math.floor(f)); if (st.live) sampleLive(); placeCamera(); }
    renderer.render(scene, camera); requestAnimationFrame(tick);
  })(last);
  resize();
  return { load, resize };
}

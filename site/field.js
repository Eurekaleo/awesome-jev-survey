// A field of typed decisions, drawn behind the page. Three faint glyphs stand for the three primitives:
// Choice (a cluster of bars that sums to one), Score (ordered levels with an expectation needle) and
// Noul (a ring filled to p(yes)). Every few seconds a glyph re-samples its distribution and eases toward it.
// Purely decorative: no data is shown, it pauses when hidden, and it is a single static frame under reduced motion.
const TAU = Math.PI * 2;

function sample(k) {
  // a peaked distribution: exponentiated noise, normalised
  const v = Array.from({length: k}, () => Math.exp(Math.random() * 3.2));
  const s = v.reduce((a, b) => a + b, 0);
  return v.map(x => x / s);
}

function makeGlyph(x, y) {
  const r = Math.random();
  const type = r < 0.5 ? 'choice' : r < 0.75 ? 'score' : 'noul';
  const k = type === 'choice' ? 3 + Math.floor(Math.random() * 3) : type === 'score' ? 5 : 1;
  const p = type === 'noul' ? [Math.random()] : sample(k);
  return {type, x, y, vx: (Math.random() - 0.5) * 6, vy: (Math.random() - 0.5) * 4, p, target: p.slice(), tint: Math.floor(Math.random() * 3),
    next: 2 + Math.random() * 7, scale: 0.8 + Math.random() * 0.5};
}

export function start(canvas) {
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const scheme = matchMedia('(prefers-color-scheme: dark)');
  let W = 0, H = 0, dpr = 1, glyphs = [], raf = 0, last = 0, acc = 0, rgb = [], alpha = 0.12;

  const readColors = () => {
    const cs = getComputedStyle(document.documentElement);
    rgb = ['--field-1', '--field-2', '--field-3'].map(v => cs.getPropertyValue(v).trim() || '90,100,180');
    alpha = parseFloat(cs.getPropertyValue('--field-alpha')) || 0.12;
  };
  const col = (i, a) => `rgba(${rgb[i]},${(alpha * a).toFixed(3)})`;

  function layout() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cell = W < 700 ? 120 : 150;
    glyphs = [];
    for (let y = cell / 2; y < H + cell; y += cell) {
      for (let x = cell / 2; x < W + cell; x += cell) {
        if (Math.random() < 0.18) continue; // leave gaps so the field breathes
        glyphs.push(makeGlyph(x + (Math.random() - 0.5) * cell * 0.7, y + (Math.random() - 0.5) * cell * 0.7));
      }
    }
  }

  function drawChoice(g) {
    const n = g.p.length, bw = 5 * g.scale, gap = 3 * g.scale, hmax = 36 * g.scale;
    const top = g.p.indexOf(Math.max(...g.p));
    let x = g.x - (n * bw + (n - 1) * gap) / 2;
    ctx.fillStyle = col(g.tint, 0.55);
    ctx.fillRect(x - 2, g.y + 1, n * bw + (n - 1) * gap + 4, 1);
    for (let i = 0; i < n; i++) {
      const h = Math.max(1.5, g.p[i] * hmax);
      ctx.fillStyle = col(g.tint, i === top ? 1.6 : 0.85);
      ctx.fillRect(x, g.y - h, bw, h);
      x += bw + gap;
    }
  }

  function drawScore(g) {
    const r = 17 * g.scale, n = g.p.length;
    let mean = 0;
    for (let i = 0; i < n; i++) {
      const a = Math.PI + (i / (n - 1)) * Math.PI;
      ctx.beginPath();
      ctx.fillStyle = col(g.tint, 0.6 + g.p[i] * 2.2);
      ctx.arc(g.x + Math.cos(a) * r, g.y + Math.sin(a) * r, 1.2 + g.p[i] * 5 * g.scale, 0, TAU);
      ctx.fill();
      mean += g.p[i] * i;
    }
    const a = Math.PI + (mean / (n - 1)) * Math.PI;
    ctx.strokeStyle = col(g.tint, 1.4); ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(g.x, g.y); ctx.lineTo(g.x + Math.cos(a) * r * 0.75, g.y + Math.sin(a) * r * 0.75); ctx.stroke();
  }

  function drawNoul(g) {
    const r = 11 * g.scale;
    ctx.lineWidth = 2.4 * g.scale;
    ctx.strokeStyle = col(g.tint, 0.5);
    ctx.beginPath(); ctx.arc(g.x, g.y, r, 0, TAU); ctx.stroke();
    ctx.strokeStyle = col(g.tint, 1.5);
    ctx.beginPath(); ctx.arc(g.x, g.y, r, -Math.PI / 2, -Math.PI / 2 + g.p[0] * TAU); ctx.stroke();
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (const g of glyphs) (g.type === 'choice' ? drawChoice : g.type === 'score' ? drawScore : drawNoul)(g);
  }

  function step(dt) {
    for (const g of glyphs) {
      g.x += g.vx * dt; g.y += g.vy * dt;
      if (g.x < -40) g.x += W + 80; else if (g.x > W + 40) g.x -= W + 80;
      if (g.y < -40) g.y += H + 80; else if (g.y > H + 40) g.y -= H + 80;
      g.next -= dt;
      if (g.next <= 0) { g.target = g.type === 'noul' ? [Math.random()] : sample(g.p.length); g.next = 3 + Math.random() * 7; }
      const k = 1 - Math.exp(-dt / 0.9);
      for (let i = 0; i < g.p.length; i++) g.p[i] += (g.target[i] - g.p[i]) * k;
    }
  }

  function frame(t) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (t - (last || t)) / 1000); last = t; acc += dt;
    if (acc < 1 / 30) return; // ~30 fps is plenty for a slow field
    step(acc); acc = 0; draw();
  }

  const play = () => { cancelAnimationFrame(raf); last = 0; if (!reduce.matches && !document.hidden) raf = requestAnimationFrame(frame); else draw(); };
  readColors(); layout(); draw(); play();
  let rt;
  addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { layout(); draw(); }, 150); }, {passive: true});
  document.addEventListener('visibilitychange', play);
  reduce.addEventListener?.('change', play);
  scheme.addEventListener?.('change', () => { readColors(); draw(); });
}

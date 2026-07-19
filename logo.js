// ORI ring — faithful port of closure-verification/docs/shatter.js
// (particulate ring: hover = magnet pull, click/hold = blow, springs back).
// Changes from the original: trails fade to actual transparency so the badge
// works on any page background, plus badge-sized, devicePixelRatio-aware
// rendering for crispness.
(function () {
  const canvas = document.getElementById("ori-logo");
  if (!canvas) return;
  // Resolve from logo.js itself so the same badge works on both the root feed
  // page and the nested ORI-report page.
  const assetBase = document.currentScript?.src || document.baseURI;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let W, H;

  let particles = [];
  let mx = 0, my = 0;
  let isDown = false;
  const DPR = Math.min(window.devicePixelRatio || 1, 2);

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const css = Math.min(rect.width, 86);
    W = canvas.width = Math.floor(css * DPR);
    H = canvas.height = Math.floor(css * DPR);
    canvas.style.width = css + "px";
    canvas.style.height = css + "px";
  }

  window.addEventListener("resize", () => { resize(); buildParticles(); });

  class Particle {
    constructor(ox, oy, r, g, b, size) {
      this.x = ox; this.y = oy;
      this.ox = ox; this.oy = oy;
      this.r = r; this.g = g; this.b = b;
      this.size = size; this.baseSize = size;

      this.vx = (Math.random() - 0.5) * 0.07;
      this.vy = (Math.random() - 0.5) * 0.07;

      this.friction    = 0.94 + Math.random() * 0.03;
      this.spring      = 0.007 + Math.random() * 0.003;
      this.wanderAngle = Math.random() * Math.PI * 2;
      this.wanderSpeed = 0.005 + Math.random() * 0.005;
    }

    update() {
      this.vx += (this.ox - this.x) * this.spring;
      this.vy += (this.oy - this.y) * this.spring;

      this.wanderAngle += this.wanderSpeed;
      this.vx += Math.cos(this.wanderAngle) * 0.007;
      this.vy += Math.sin(this.wanderAngle) * 0.007;

      if (isDown) {
        const dx = this.x - mx;
        const dy = this.y - my;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const radius = W * 0.3;
        if (dist < radius && dist > 0) {
          const force = (radius - dist) / radius;
          const angle = Math.atan2(dy, dx);
          this.vx += Math.cos(angle) * force * force * 1.4;
          this.vy += Math.sin(angle) * force * force * 1.4;
          this.size = this.baseSize * (1 + force * 0.9);
        } else {
          this.size += (this.baseSize - this.size) * 0.1;
        }
      } else {
        this.size += (this.baseSize - this.size) * 0.1;
      }

      this.vx *= this.friction;
      this.vy *= this.friction;
      this.x += this.vx;
      this.y += this.vy;
    }

    draw() {
      ctx.fillStyle = `rgb(${this.r},${this.g},${this.b})`;
      const s = Math.max(1, this.size);
      const half = s / 2;
      const rad = s > 4 ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(this.x - half + rad, this.y - half);
      ctx.lineTo(this.x + half - rad, this.y - half);
      ctx.quadraticCurveTo(this.x + half, this.y - half, this.x + half, this.y - half + rad);
      ctx.lineTo(this.x + half, this.y + half - rad);
      ctx.quadraticCurveTo(this.x + half, this.y + half, this.x + half - rad, this.y + half);
      ctx.lineTo(this.x - half + rad, this.y + half);
      ctx.quadraticCurveTo(this.x - half, this.y + half, this.x - half, this.y + half - rad);
      ctx.lineTo(this.x - half, this.y - half + rad);
      ctx.quadraticCurveTo(this.x - half, this.y - half, this.x - half + rad, this.y - half);
      ctx.fill();
    }
  }

  function buildParticles() {
    if (!window._oriImg) return;
    const img = window._oriImg;
    particles = [];

    const scale = Math.min((W * 0.88) / img.width, (H * 0.88) / img.height, 1);
    const iw = Math.floor(img.width * scale);
    const ih = Math.floor(img.height * scale);
    const ox = Math.floor((W - iw) / 2);
    const oy = Math.floor((H - ih) / 2);

    const oc = document.createElement("canvas");
    oc.width = iw; oc.height = ih;
    const octx = oc.getContext("2d");
    octx.drawImage(img, 0, 0, iw, ih);
    const data = octx.getImageData(0, 0, iw, ih).data;

    const target = Math.min(6000, Math.max(2000, (iw * ih) / 20));
    const gap = Math.max(2, Math.floor(Math.sqrt((iw * ih) / target)));
    const pSize = gap * 0.95;

    for (let y = 0; y < ih; y += gap) {
      for (let x = 0; x < iw; x += gap) {
        const i = (y * iw + x) * 4;
        if (data[i + 3] < 128) continue;
        particles.push(new Particle(ox + x, oy + y, data[i], data[i + 1], data[i + 2], pSize));
      }
    }
  }

  function render() {
    // Remove a fraction of the previous frame's alpha instead of painting a
    // page-specific background color. Old particles still leave soft trails,
    // while the canvas itself remains genuinely transparent.
    ctx.save();
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = "rgba(0,0,0,0.28)";
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
    particles.forEach(p => { p.update(); p.draw(); });
    requestAnimationFrame(render);
  }

  canvas.addEventListener("pointermove", e => {
    const r = canvas.getBoundingClientRect();
    mx = (e.clientX - r.left) * DPR;
    my = (e.clientY - r.top) * DPR;
  });
  canvas.addEventListener("pointerleave", () => isDown = false);
  canvas.addEventListener("pointerdown", () => isDown = true);
  canvas.addEventListener("pointerup", () => isDown = false);

  resize();

  const img = new Image();
  img.onload = () => {
    window._oriImg = img;
    buildParticles();
    render();
  };
  img.src = new URL("assets/gradient-circle.png", assetBase).href;
})();

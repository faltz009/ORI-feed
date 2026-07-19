/**
 * MemeticWeather
 *
 * A model-free, three-dimensional semantic weather engine. Every core is an
 * observed word. Every line is an aggregate of repeated proximity
 * inside messages. Topic detection supplies color, but never coordinates: the
 * association forces determine the network's shape in XYZ space.
 *
 * A fixed population of particles represents 100% of the detected topical
 * attention at the selected time. A word with 8% of that attention receives
 * roughly 8% of the particles. Because cloud radius grows with the cube root
 * of share, cloud volume—not merely label size—is proportional to attention.
 */
(function (global) {
  "use strict";

  const TAU = Math.PI * 2;
  const GOLDEN = 0.618033988749895;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const mix = (left, right, amount) => left + (right - left) * amount;
  const fract = value => value - Math.floor(value);
  const hash = value => fract(Math.sin(value * 12.9898 + 78.233) * 43758.5453);
  const escapeHtml = value => String(value).replace(/[&<>\"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;"
  })[character]);

  const PALETTE = [
    "#6dd9ff", "#927cff", "#e779ff", "#63f3cd", "#ff8ebc", "#ffd166",
    "#5ca8ff", "#b8f06a", "#ff8c69", "#72e0f4", "#c59bff", "#62d6a9"
  ];

  class MemeticWeather {
    constructor(canvas, options) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d", { alpha: false });
      this.options = options;
      this.days = options.days || [];
      this.topics = options.topics || [];
      this.nodes = [];
      this.edges = [];
      this.nodeById = new Map();
      this.particles = [];
      this.hovered = null;
      this.playing = true;
      this.visible = true;
      this.time = 0;
      this.lastFrame = performance.now();
      this.dragging = false;
      this.pointer = { x: -10_000, y: -10_000 };
      this.camera = { yaw: 0.48, pitch: -0.19, distance: 590 };

      this.resize();
      this.buildGraph();
      this.updateState(1, true);

      // Settle the first day's graph before it is shown. This is still the same
      // force system used live; it simply avoids presenting initialization noise.
      for (let iteration = 0; iteration < 220; iteration += 1) {
        this.simulate(1 / 60);
      }

      this.createParticles();
      this.updateParticles(1 / 60, true);
      this.bindInteraction();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(canvas.parentElement);
      requestAnimationFrame(timestamp => this.frame(timestamp));
    }

    resize() {
      const bounds = this.canvas.getBoundingClientRect();
      const width = Math.max(320, bounds.width || this.canvas.parentElement.clientWidth);
      const height = Math.max(300, bounds.height || this.canvas.parentElement.clientHeight);
      const pixelRatio = Math.min(2, global.devicePixelRatio || 1);
      const bitmapWidth = Math.round(width * pixelRatio);
      const bitmapHeight = Math.round(height * pixelRatio);
      const changed = this.canvas.width !== bitmapWidth || this.canvas.height !== bitmapHeight;
      this.width = width;
      this.height = height;
      this.pixelRatio = pixelRatio;
      if (changed) {
        this.canvas.width = bitmapWidth;
        this.canvas.height = bitmapHeight;
      }
      this.context.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
      this.camera.focal = Math.min(this.width, this.height) * 0.94;
      if (changed && this.nodes && this.nodes.length && this.particles.length) this.draw();
    }

    causalSeries(values) {
      const source = values || [];
      const weights = [0.58, 0.29, 0.13];
      return this.days.map((_day, index) => {
        let total = 0;
        let weight = 0;
        for (let offset = 0; offset < weights.length; offset += 1) {
          if (index - offset < 0) continue;
          total += Number(source[index - offset] || 0) * weights[offset];
          weight += weights[offset];
        }
        return total / Math.max(weight, 1e-9);
      });
    }

    valueAt(series) {
      if (!series.length) return 0;
      const left = clamp(Math.floor(this.time), 0, series.length - 1);
      const right = Math.min(series.length - 1, left + 1);
      return mix(series[left], series[right], this.time - left);
    }

    buildGraph() {
      this.topics.forEach((topic, topicIndex) => {
        topic.index = topicIndex;
        topic.color = PALETTE[topicIndex % PALETTE.length];

        // Topic centers are only deterministic starting conditions. Once the
        // force system runs, association edges—not these seeds—own position.
        const azimuth = TAU * hash(topicIndex * 17.13 + 2);
        const radius = 60 + hash(topicIndex * 9.7 + 4) * 105;
        const center = {
          x: Math.cos(azimuth) * radius,
          y: (hash(topicIndex * 5.3 + 7) - 0.5) * 210,
          z: Math.sin(azimuth) * radius
        };

        topic.units.forEach((unit, unitIndex) => {
          const seed = topicIndex * 101 + unitIndex * 7.31 + 1;
          const localRadius = 12 + Math.sqrt(unitIndex + 1) * 10;
          const theta = TAU * hash(seed);
          const zDirection = hash(seed + 3) * 2 - 1;
          const planar = Math.sqrt(Math.max(0, 1 - zDirection * zDirection));
          const node = {
            id: unit.term,
            index: this.nodes.length,
            topic,
            color: topic.color,
            kind: unit.kind,
            labelVisible: unit.label_visible !== false,
            voices: unit.voices,
            lift: unit.lift,
            total: unit.count,
            series: this.causalSeries(unit.series),
            x: center.x + Math.cos(theta) * planar * localRadius,
            y: center.y + zDirection * localRadius,
            z: center.z + Math.sin(theta) * planar * localRadius,
            vx: 0, vy: 0, vz: 0,
            fx: 0, fy: 0, fz: 0,
            current: 0,
            share: 0,
            opacity: 0,
            active: false,
            neighbors: []
          };
          this.nodes.push(node);
          this.nodeById.set(node.id, node);
        });
      });

      (this.options.edges || []).forEach(row => {
        const source = this.nodeById.get(row.source);
        const target = this.nodeById.get(row.target);
        if (!source || !target || source === target) return;
        const edge = {
          source,
          target,
          weight: clamp(Number(row.weight || 0), 0, 1),
          documents: Number(row.documents || 0),
          series: this.causalSeries(row.series),
          current: 0,
          opacity: 0
        };
        this.edges.push(edge);
        source.neighbors.push({ node: target, edge });
        target.neighbors.push({ node: source, edge });
      });
    }

    updateState(delta, immediate = false) {
      let total = 0;
      this.nodes.forEach(node => {
        node.current = this.valueAt(node.series);
        total += node.current;
      });

      const easing = immediate ? 1 : 1 - Math.exp(-delta * 5.5);
      this.nodes.forEach(node => {
        const wasActive = node.active;
        node.share = total > 0 ? node.current / total : 0;
        node.active = node.current > 0.001 && node.share > 0.00005;
        if (node.active && !wasActive && !immediate) this.enterAtLivingNeighbors(node);
        node.opacity = mix(node.opacity, node.active ? 1 : 0, easing);
      });

      let maximumEdge = 0;
      this.edges.forEach(edge => {
        edge.current = this.valueAt(edge.series);
        maximumEdge = Math.max(maximumEdge, edge.current);
      });
      this.edges.forEach(edge => {
        const alive = edge.source.active && edge.target.active;
        const target = alive && maximumEdge > 0 ? edge.current / maximumEdge : 0;
        edge.opacity = mix(edge.opacity, target, easing);
      });

      this.activeNodes = this.nodes.filter(node => node.active || node.opacity > 0.025);
      const readableNodes = this.nodes.filter(node => node.labelVisible && node.active);
      this.dominant = (readableNodes.length ? readableNodes : this.nodes).reduce(
        (best, node) => !best || node.share > best.share ? node : best,
        null
      );
    }

    enterAtLivingNeighbors(node) {
      const living = node.neighbors.filter(link => link.node.active);
      const sameTopic = this.nodes.filter(candidate =>
        candidate !== node && candidate.active && candidate.topic === node.topic
      );
      const anchors = living.length ? living : sameTopic;
      if (!anchors.length) {
        const leader = this.dominant;
        node.x = leader ? leader.x : 0;
        node.y = leader ? leader.y : 0;
        node.z = leader ? leader.z : 0;
      } else {
        const rows = living.length ? living.map(link => link.node) : anchors;
        node.x = rows.reduce((sum, item) => sum + item.x, 0) / rows.length;
        node.y = rows.reduce((sum, item) => sum + item.y, 0) / rows.length;
        node.z = rows.reduce((sum, item) => sum + item.z, 0) / rows.length;
      }
      const jitter = node.index * 19.17;
      node.x += (hash(jitter) - 0.5) * 18;
      node.y += (hash(jitter + 1) - 0.5) * 18;
      node.z += (hash(jitter + 2) - 0.5) * 18;
      node.vx = node.vy = node.vz = 0;
    }

    simulate(delta) {
      const step = clamp(delta * 60, 0.2, 2);
      const active = this.activeNodes || [];
      active.forEach(node => { node.fx = node.fy = node.fz = 0; });

      // Stable 30-day association is the skeleton. Today's co-occurrence
      // strengthens its spring; a zero-day edge stops drawing but does not make
      // two still-active words forget their established semantic neighborhood.
      this.edges.forEach(edge => {
        const left = edge.source;
        const right = edge.target;
        if (!left.active || !right.active) return;
        const dx = right.x - left.x;
        const dy = right.y - left.y;
        const dz = right.z - left.z;
        const distance = Math.max(1, Math.hypot(dx, dy, dz));
        const crossTopic = left.topic !== right.topic;
        const desired = (crossTopic ? 76 : 45) + (1 - edge.weight) * 54;
        const strength = (0.0025 + edge.weight * 0.0045) * (0.35 + edge.opacity * 0.65);
        const force = (distance - desired) * strength;
        const fx = dx / distance * force;
        const fy = dy / distance * force;
        const fz = dz / distance * force;
        left.fx += fx; left.fy += fy; left.fz += fz;
        right.fx -= fx; right.fy -= fy; right.fz -= fz;
      });

      // Three-dimensional charge keeps nodes readable without imposing a grid,
      // ring, globe, or any other visual coordinate system on the data.
      for (let leftIndex = 0; leftIndex < active.length; leftIndex += 1) {
        const left = active[leftIndex];
        for (let rightIndex = leftIndex + 1; rightIndex < active.length; rightIndex += 1) {
          const right = active[rightIndex];
          let dx = right.x - left.x;
          let dy = right.y - left.y;
          let dz = right.z - left.z;
          const distanceSquared = dx * dx + dy * dy + dz * dz + 36;
          const distance = Math.sqrt(distanceSquared);
          const force = 520 / distanceSquared;
          dx /= distance; dy /= distance; dz /= distance;
          left.fx -= dx * force; left.fy -= dy * force; left.fz -= dz * force;
          right.fx += dx * force; right.fy += dy * force; right.fz += dz * force;
        }
      }

      // Topic cohesion is deliberately weak: it reinforces the communities
      // found in the same edge graph while cross-topic bridges can still pull
      // families into a larger branching structure.
      const centroids = this.topics.map(() => ({ x: 0, y: 0, z: 0, weight: 0 }));
      active.forEach(node => {
        if (!node.active) return;
        const centroid = centroids[node.topic.index];
        const weight = Math.max(node.share, 0.001);
        centroid.x += node.x * weight;
        centroid.y += node.y * weight;
        centroid.z += node.z * weight;
        centroid.weight += weight;
      });
      active.forEach(node => {
        if (!node.active) return;
        const centroid = centroids[node.topic.index];
        if (centroid.weight) {
          node.fx += (centroid.x / centroid.weight - node.x) * 0.0014;
          node.fy += (centroid.y / centroid.weight - node.y) * 0.0014;
          node.fz += (centroid.z / centroid.weight - node.z) * 0.0014;
        }
        node.fx -= node.x * 0.0007;
        node.fy -= node.y * 0.0007;
        node.fz -= node.z * 0.0007;
      });

      const damping = Math.pow(0.875, step);
      this.nodes.forEach(node => {
        if (!node.active) {
          node.vx *= damping; node.vy *= damping; node.vz *= damping;
          return;
        }
        node.vx = (node.vx + node.fx * step) * damping;
        node.vy = (node.vy + node.fy * step) * damping;
        node.vz = (node.vz + node.fz * step) * damping;
        node.x += node.vx * step;
        node.y += node.vy * step;
        node.z += node.vz * step;
      });
    }

    createParticles() {
      const count = this.width < 620 ? 1500 : 3000;
      this.particles = Array.from({ length: count }, (_unused, index) => ({
        id: index,
        node: null,
        visualTopic: 0,
        x: 0, y: 0, z: 0,
        vx: 0, vy: 0, vz: 0,
        size: 0.55 + hash(index * 3.7 + 11) * 1.35
      }));
    }

    particleNode(quantile, cumulative) {
      let low = 0;
      let high = cumulative.length - 1;
      while (low < high) {
        const middle = (low + high) >> 1;
        if (quantile <= cumulative[middle].limit) high = middle;
        else low = middle + 1;
      }
      return cumulative[low] && cumulative[low].node;
    }

    updateParticles(delta, immediate = false) {
      const living = this.nodes.filter(node => node.active && node.share > 0);
      if (!living.length) return;
      let limit = 0;
      const cumulative = living.map(node => {
        limit += node.share;
        return { limit, node };
      });
      cumulative[cumulative.length - 1].limit = 1;
      const step = clamp(delta * 60, 0.2, 2);
      const damping = Math.pow(0.86, step);

      this.particles.forEach(particle => {
        const quantile = fract((particle.id + 0.5) * GOLDEN);
        const node = this.particleNode(quantile, cumulative) || living[0];
        const changed = particle.node !== node;
        particle.node = node;

        // Equal particle density makes volume proportional to share: radius is
        // the cube root of the fraction assigned to this word.
        const cloudRadius = Math.max(7, Math.cbrt(node.share) * 132);
        const seed = particle.id * 7.919 + node.index * 41.37;
        const theta = TAU * hash(seed);
        const zDirection = hash(seed + 1) * 2 - 1;
        const planar = Math.sqrt(Math.max(0, 1 - zDirection * zDirection));
        const radius = cloudRadius * Math.pow(hash(seed + 2), 0.48);
        const targetX = node.x + Math.cos(theta) * planar * radius;
        const targetY = node.y + zDirection * radius;
        const targetZ = node.z + Math.sin(theta) * planar * radius;

        if (immediate) {
          particle.x = targetX; particle.y = targetY; particle.z = targetZ;
          particle.visualTopic = node.topic.index;
          return;
        }
        const dx = targetX - particle.x;
        const dy = targetY - particle.y;
        const dz = targetZ - particle.z;
        const spring = changed ? 0.010 : 0.018;
        particle.vx = (particle.vx + dx * spring * step) * damping;
        particle.vy = (particle.vy + dy * spring * step) * damping;
        particle.vz = (particle.vz + dz * spring * step) * damping;
        particle.x += particle.vx * step;
        particle.y += particle.vy * step;
        particle.z += particle.vz * step;

        // A migrating particle keeps its old color while in transit and adopts
        // the destination family only when it reaches the new cloud.
        if (Math.hypot(dx, dy, dz) < cloudRadius * 1.15) {
          particle.visualTopic = node.topic.index;
        }
      });
    }

    project(x, y, z) {
      const yawCos = Math.cos(this.camera.yaw);
      const yawSin = Math.sin(this.camera.yaw);
      const pitchCos = Math.cos(this.camera.pitch);
      const pitchSin = Math.sin(this.camera.pitch);
      const horizontal = x * yawCos - z * yawSin;
      const yawDepth = x * yawSin + z * yawCos;
      const vertical = y * pitchCos - yawDepth * pitchSin;
      const cameraDepth = y * pitchSin + yawDepth * pitchCos;
      const distance = Math.max(40, this.camera.distance - cameraDepth);
      const scale = this.camera.focal / distance;
      return {
        x: this.width * 0.5 + horizontal * scale,
        y: this.height * 0.52 + vertical * scale,
        z: cameraDepth,
        scale
      };
    }

    drawBackground() {
      const context = this.context;
      const gradient = context.createRadialGradient(
        this.width * 0.52, this.height * 0.48, 20,
        this.width * 0.52, this.height * 0.48, Math.max(this.width, this.height) * 0.68
      );
      gradient.addColorStop(0, "#0a1422");
      gradient.addColorStop(0.52, "#060d17");
      gradient.addColorStop(1, "#03070d");
      context.fillStyle = gradient;
      context.fillRect(0, 0, this.width, this.height);
    }

    drawHalos() {
      const context = this.context;
      const totals = this.topics.map(() => ({ x: 0, y: 0, z: 0, share: 0 }));
      this.nodes.forEach(node => {
        if (!node.active) return;
        const total = totals[node.topic.index];
        total.x += node.x * node.share;
        total.y += node.y * node.share;
        total.z += node.z * node.share;
        total.share += node.share;
      });
      context.save();
      context.globalCompositeOperation = "screen";
      totals.forEach((total, index) => {
        if (!total.share) return;
        const point = this.project(
          total.x / total.share, total.y / total.share, total.z / total.share
        );
        const radius = (48 + Math.cbrt(total.share) * 170) * point.scale;
        const glow = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius);
        glow.addColorStop(0, `${PALETTE[index % PALETTE.length]}14`);
        glow.addColorStop(0.35, `${PALETTE[index % PALETTE.length]}09`);
        glow.addColorStop(1, `${PALETTE[index % PALETTE.length]}00`);
        context.fillStyle = glow;
        context.beginPath();
        context.arc(point.x, point.y, radius, 0, TAU);
        context.fill();
      });
      context.restore();
    }

    drawEdges() {
      const context = this.context;
      const hovered = this.hovered;
      const neighborIds = hovered ? new Set(hovered.neighbors.map(link => link.node.id)) : null;
      this.edges.forEach(edge => {
        const endpointOpacity = Math.min(edge.source.opacity, edge.target.opacity);
        if (endpointOpacity < 0.035) return;
        const source = edge.source.projected;
        const target = edge.target.projected;
        if (!source || !target) return;
        const touchesHover = hovered && (edge.source === hovered || edge.target === hovered);
        const fade = hovered && !touchesHover ? 0.12 : 1;
        const depth = clamp((source.z + target.z + 500) / 1000, 0.28, 1);
        const stableSkeleton = 0.14 + edge.weight * 0.2;
        const liveSignal = edge.opacity * (0.08 + edge.weight * 0.3);
        const alpha = clamp((stableSkeleton + liveSignal) * endpointOpacity * fade * depth, 0, 0.7);
        context.strokeStyle = touchesHover
          ? `rgba(235,248,255,${alpha + 0.18})`
          : `rgba(103,154,211,${alpha})`;
        context.lineWidth = touchesHover ? 1.65 : 0.52 + edge.weight * 0.9;
        context.beginPath();
        context.moveTo(source.x, source.y);
        context.lineTo(target.x, target.y);
        context.stroke();
      });
      this.neighborIds = neighborIds;
    }

    drawParticles() {
      const context = this.context;
      const buckets = Array.from({ length: 3 }, () =>
        Array.from({ length: this.topics.length }, () => [])
      );
      this.particles.forEach(particle => {
        const point = this.project(particle.x, particle.y, particle.z);
        const depth = clamp(Math.floor((point.z + 270) / 180), 0, 2);
        buckets[depth][particle.visualTopic % this.topics.length].push({ particle, point });
      });

      context.save();
      context.globalCompositeOperation = "lighter";
      buckets.forEach((topicBuckets, depth) => {
        topicBuckets.forEach((rows, topicIndex) => {
          if (!rows.length) return;
          const topicFade = this.hovered && this.hovered.topic.index !== topicIndex ? 0.25 : 1;
          context.fillStyle = `${PALETTE[topicIndex % PALETTE.length]}${["18", "26", "3d"][depth]}`;
          context.globalAlpha = topicFade;
          rows.forEach(({ particle, point }) => {
            const size = particle.size * point.scale * (0.72 + depth * 0.16);
            context.fillRect(point.x - size / 2, point.y - size / 2, size, size);
          });
          context.fillStyle = `${PALETTE[topicIndex % PALETTE.length]}8a`;
          rows.forEach(({ particle, point }) => {
            if (particle.id % 23 !== 0) return;
            const size = particle.size * point.scale * 1.25;
            context.fillRect(point.x - size / 2, point.y - size / 2, size, size);
          });
        });
      });
      context.restore();
    }

    drawNodesAndLabels() {
      const context = this.context;
      const projected = this.activeNodes
        .filter(node => node.opacity > 0.035)
        .sort((left, right) => left.projected.z - right.projected.z);
      projected.forEach(node => {
        const point = node.projected;
        const related = !this.hovered || node === this.hovered || this.neighborIds.has(node.id);
        const alpha = node.opacity * (related ? 0.92 : 0.2);
        const radius = (1.7 + Math.cbrt(node.share) * 16) * point.scale;
        context.save();
        context.globalCompositeOperation = "lighter";
        context.shadowColor = node.color;
        context.shadowBlur = node === this.hovered ? 20 : 9;
        context.fillStyle = node === this.hovered ? "#ffffff" : node.color;
        context.globalAlpha = alpha;
        context.beginPath();
        context.arc(point.x, point.y, Math.max(1.1, radius), 0, TAU);
        context.fill();
        context.restore();
      });

      const limit = this.width < 620 ? 9 : 16;
      // A hovered word's neighbors are the whole point of hovering: force
      // their labels on regardless of share, so a highlighted edge always
      // resolves to a readable word instead of an unlabeled bright dot.
      const forced = this.hovered
        ? [this.hovered, ...this.hovered.neighbors
            .map(link => link.node)
            .filter(node => node.active && node.opacity > 0.035)]
        : [];
      const candidates = [
        ...forced,
        ...[...this.activeNodes]
          .filter(node => node.labelVisible && node.opacity > 0.22 && node.share > 0 &&
            !forced.includes(node))
          .sort((left, right) => right.share - left.share)
          .slice(0, limit)
      ];
      const occupied = [];
      candidates.forEach(node => {
        const point = node.projected;
        const fontSize = clamp((10 + Math.cbrt(node.share) * 30) * point.scale, 10, 27);
        context.font = `${node === this.hovered ? 600 : 400} ${fontSize}px Georgia, serif`;
        const width = context.measureText(node.id).width;
        const box = {
          left: point.x - width / 2 - 5,
          right: point.x + width / 2 + 5,
          top: point.y - fontSize - 12,
          bottom: point.y + 5
        };
        const collision = occupied.some(other => !(
          box.right < other.left || box.left > other.right ||
          box.bottom < other.top || box.top > other.bottom
        ));
        if (collision && !forced.includes(node)) return;
        occupied.push(box);
        const related = !this.hovered || node === this.hovered || this.neighborIds.has(node.id);
        context.textAlign = "center";
        context.textBaseline = "bottom";
        context.lineJoin = "round";
        context.lineWidth = 4;
        context.strokeStyle = "rgba(2,7,12,.9)";
        context.globalAlpha = node.opacity * (related ? 0.9 : 0.16);
        context.strokeText(node.id, point.x, point.y - 6);
        context.fillStyle = node === this.hovered ? "#ffffff" : node.color;
        context.fillText(node.id, point.x, point.y - 6);
      });
      context.globalAlpha = 1;
    }

    draw() {
      this.drawBackground();
      this.activeNodes.forEach(node => {
        node.projected = this.project(node.x, node.y, node.z);
      });
      this.drawHalos();
      this.drawEdges();
      this.drawParticles();
      this.drawNodesAndLabels();
    }

    findHovered() {
      if (this.dragging) return;
      let closest = null;
      let distance = 24;
      this.activeNodes.forEach(node => {
        if (!node.projected || node.opacity < 0.15) return;
        const candidate = Math.hypot(
          node.projected.x - this.pointer.x,
          node.projected.y - this.pointer.y
        );
        if (candidate < distance) {
          distance = candidate;
          closest = node;
        }
      });
      this.hovered = closest;
    }

    updateReadout() {
      const node = this.hovered || this.dominant;
      if (!node || !this.options.readout) return;
      const day = this.days[clamp(Math.round(this.time), 0, this.days.length - 1)];
      const key = `${node.id}:${day}:${this.hovered ? 1 : 0}`;
      if (key === this.readoutKey) return;
      this.readoutKey = key;
      const family = this.options.labelFor ? this.options.labelFor(node.topic) : node.topic.label;
      this.options.readout.innerHTML =
        `<small>${this.hovered ? "word node" : "dominant word"}</small>` +
        `<b>${escapeHtml(node.id)}</b>` +
        `<span>${(node.share * 100).toFixed(1)}% of detected attention · ` +
        `${escapeHtml(family)} · ${node.voices} voices</span>`;
    }

    renderCalendar(index) {
      const calendar = this.options.calendar;
      if (!calendar || index === this.calendarIndex) return;
      this.calendarIndex = index;
      const weekdays = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
      const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
      calendar.innerHTML = [-2, -1, 0, 1, 2].map(offset => {
        const dayIndex = index + offset;
        if (dayIndex < 0 || dayIndex >= this.days.length) {
          return `<span class="calendar-day is-empty" aria-hidden="true"></span>`;
        }
        const date = new Date(`${this.days[dayIndex]}T12:00:00Z`);
        return `<span class="calendar-day${offset === 0 ? " is-current" : ""}">` +
          `<small>${weekdays[date.getUTCDay()]}</small>` +
          `<b>${String(date.getUTCDate()).padStart(2, "0")}</b>` +
          `<em>${months[date.getUTCMonth()]}</em></span>`;
      }).join("");
      calendar.animate(
        [{ opacity: 0.55, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }],
        { duration: 180, easing: "ease-out" }
      );
    }

    updateControls() {
      const index = clamp(Math.round(this.time), 0, this.days.length - 1);
      if (this.options.slider && !this.scrubbing) this.options.slider.value = this.time;
      if (this.options.dateLabel) this.options.dateLabel.textContent = this.days[index] || "";
      this.renderCalendar(index);
      if (this.options.playButton) {
        this.options.playButton.textContent = this.playing ? "❚❚ Pause" : "▶ Play";
      }
    }

    bindInteraction() {
      const canvas = this.canvas;
      const localPoint = event => {
        const bounds = canvas.getBoundingClientRect();
        return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
      };
      canvas.addEventListener("pointerdown", event => {
        this.dragging = true;
        this.dragOrigin = { ...localPoint(event), yaw: this.camera.yaw, pitch: this.camera.pitch };
        canvas.setPointerCapture(event.pointerId);
      });
      canvas.addEventListener("pointermove", event => {
        const point = localPoint(event);
        this.pointer = point;
        if (!this.dragging) return;
        this.camera.yaw = this.dragOrigin.yaw + (point.x - this.dragOrigin.x) * 0.008;
        this.camera.pitch = clamp(
          this.dragOrigin.pitch + (point.y - this.dragOrigin.y) * 0.006,
          -1.15, 1.15
        );
      });
      const stopDragging = event => {
        this.dragging = false;
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      };
      canvas.addEventListener("pointerup", stopDragging);
      canvas.addEventListener("pointercancel", stopDragging);
      canvas.addEventListener("pointerleave", () => {
        if (!this.dragging) this.pointer = { x: -10_000, y: -10_000 };
      });
      canvas.addEventListener("wheel", event => {
        event.preventDefault();
        this.camera.distance = clamp(
          this.camera.distance * Math.exp(event.deltaY * 0.001), 380, 1080
        );
      }, { passive: false });

      if (this.options.slider) {
        this.options.slider.min = 0;
        this.options.slider.max = Math.max(0, this.days.length - 1);
        this.options.slider.step = 0.01;
        this.options.slider.value = 0;
        this.options.slider.addEventListener("pointerdown", () => { this.scrubbing = true; });
        this.options.slider.addEventListener("pointerup", () => { this.scrubbing = false; });
        this.options.slider.addEventListener("input", event => {
          this.time = Number(event.target.value);
          this.playing = false;
        });
      }
      if (this.options.playButton) {
        this.options.playButton.addEventListener("click", () => {
          if (!this.playing && this.time >= this.days.length - 1 - 0.01) this.time = 0;
          this.playing = !this.playing;
        });
      }
    }

    setVisible(visible) {
      this.visible = visible;
      if (visible) this.lastFrame = performance.now();
    }

    destroy() {
      // A replaced instance (server switch) must stop its frame loop and
      // observers instead of animating a detached canvas forever.
      this.destroyed = true;
      this.resizeObserver.disconnect();
    }

    frame(timestamp) {
      if (this.destroyed) return;
      const delta = clamp((timestamp - this.lastFrame) / 1000, 0.001, 0.05);
      this.lastFrame = timestamp;
      if (!this.visible) {
        requestAnimationFrame(next => this.frame(next));
        return;
      }
      if (this.playing && this.days.length > 1) {
        this.time += delta / 1.35;
        if (this.time >= this.days.length - 1) {
          this.time = this.days.length - 1;
          this.playing = false;
        }
      }
      if (!this.dragging && !this.hovered) this.camera.yaw += delta * 0.032;
      this.updateState(delta);
      this.simulate(delta);
      this.updateParticles(delta);
      this.draw();
      this.findHovered();
      this.updateReadout();
      this.updateControls();
      requestAnimationFrame(next => this.frame(next));
    }
  }

  global.MEMETIC_PALETTE = PALETTE.slice();
  global.MemeticWeather = MemeticWeather;
})(window);

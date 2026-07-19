/**
 * TopicTimeline
 *
 * One explanatory counterpart to the 3D weather field. Topic series are
 * normalized per day, so the chart answers a plain question: what share of the
 * detected topical language belonged to each family on this date?
 */
(function (global) {
  "use strict";

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const escapeHtml = value => String(value).replace(/[&<>\"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;"
  })[character]);

  class TopicTimeline {
    constructor(root, options) {
      this.root = root;
      this.topics = options.topics || [];
      this.days = options.days || [];
      this.labelFor = options.labelFor || (topic => topic.label);
      this.palette = options.palette || global.MEMETIC_PALETTE || [];
      this.onSelect = options.onSelect || (() => {});
      this.selectedDay = this.days.length - 1;
      this.width = 1000;
      this.height = 500;
      this.margin = { top: 24, right: 34, bottom: 54, left: 58 };
      this.prepare();
      this.defaultTopics = this.rows.slice(0, 4).map(row => row.topicIndex);
      this.visibleTopics = new Set(this.defaultTopics);
      this.render();
    }

    prepare() {
      const smoothed = this.topics.map(topic => this.causalSeries(topic.series));
      this.rows = this.topics.map((topic, topicIndex) => ({
        topic,
        topicIndex,
        label: this.labelFor(topic),
        color: this.palette[topicIndex % this.palette.length] || "#8fdac1",
        values: this.days.map((_day, dayIndex) => {
          const total = smoothed.reduce(
            (sum, series) => sum + Number(series[dayIndex] || 0), 0
          );
          return total ? Number(smoothed[topicIndex][dayIndex] || 0) / total : 0;
        })
      }));
      const observedMaximum = Math.max(
        0.1,
        ...this.rows.flatMap(row => row.values)
      );
      this.maximum = Math.ceil(observedMaximum * 20) / 20;
    }

    causalSeries(values) {
      const weights = [0.58, 0.29, 0.13];
      return this.days.map((_day, index) => {
        let total = 0;
        let weight = 0;
        weights.forEach((amount, offset) => {
          if (index - offset < 0) return;
          total += Number(values[index - offset] || 0) * amount;
          weight += amount;
        });
        return total / Math.max(weight, 1e-9);
      });
    }

    xFor(index) {
      const inner = this.width - this.margin.left - this.margin.right;
      return this.margin.left + index / Math.max(1, this.days.length - 1) * inner;
    }

    yFor(value) {
      const inner = this.height - this.margin.top - this.margin.bottom;
      return this.margin.top + inner - value / this.maximum * inner;
    }

    pathFor(values) {
      return values.map((value, index) =>
        `${index ? "L" : "M"}${this.xFor(index).toFixed(1)},${this.yFor(value).toFixed(1)}`
      ).join(" ");
    }

    tickIndexes() {
      const last = Math.max(0, this.days.length - 1);
      return [...new Set([0, Math.round(last * 0.25), Math.round(last * 0.5), Math.round(last * 0.75), last])];
    }

    render() {
      const yTicks = Array.from({ length: 5 }, (_unused, index) => this.maximum * index / 4);
      const grid = yTicks.map(value => {
        const y = this.yFor(value);
        return `<g><line class="timeline-grid" x1="${this.margin.left}" x2="${this.width - this.margin.right}" y1="${y}" y2="${y}"/>` +
          `<text class="timeline-axis-label" x="${this.margin.left - 12}" y="${y + 4}" text-anchor="end">${Math.round(value * 100)}%</text></g>`;
      }).join("");
      const xTicks = this.tickIndexes().map(index =>
        `<text class="timeline-axis-label" x="${this.xFor(index)}" y="${this.height - 17}" text-anchor="middle">${escapeHtml((this.days[index] || "").slice(5))}</text>`
      ).join("");
      const lines = this.rows.map(row =>
        `<path class="topic-line" data-topic="${row.topicIndex}" d="${this.pathFor(row.values)}" style="stroke:${row.color}"/>`
      ).join("");
      const legend = this.rows.map(row =>
        `<button class="topic-key" data-topic="${row.topicIndex}" style="--topic:${row.color}" aria-pressed="false">` +
          `<i></i><span>${escapeHtml(row.label)}</span></button>`
      ).join("");

      this.root.innerHTML =
        `<div class="topic-chart-frame">` +
          `<svg class="topic-chart" viewBox="0 0 ${this.width} ${this.height}" role="img" aria-label="Daily share of detected topic attention">` +
            `<g>${grid}${xTicks}</g><g class="topic-lines">${lines}</g>` +
            `<line class="timeline-cursor" x1="0" x2="0" y1="${this.margin.top}" y2="${this.height - this.margin.bottom}"/>` +
            `<g class="timeline-points"></g><rect class="timeline-hit" x="${this.margin.left}" y="${this.margin.top}" width="${this.width - this.margin.left - this.margin.right}" height="${this.height - this.margin.top - this.margin.bottom}"/>` +
          `</svg>` +
          `<div class="timeline-tooltip" hidden></div>` +
        `</div><div class="topic-controls"><div class="topic-legend">${legend}</div>` +
          `<div class="topic-actions"><button type="button" data-action="default">Top four</button><button type="button" data-action="all">Show all</button></div></div>`;

      this.svg = this.root.querySelector(".topic-chart");
      this.cursor = this.root.querySelector(".timeline-cursor");
      this.points = this.root.querySelector(".timeline-points");
      this.tooltip = this.root.querySelector(".timeline-tooltip");
      this.hit = this.root.querySelector(".timeline-hit");
      this.bind();
      this.applyVisibility();
      this.showDay(this.selectedDay, false);
    }

    bind() {
      this.hit.addEventListener("pointermove", event => {
        const bounds = this.svg.getBoundingClientRect();
        const viewX = (event.clientX - bounds.left) / bounds.width * this.width;
        const ratio = (viewX - this.margin.left) /
          (this.width - this.margin.left - this.margin.right);
        this.showDay(Math.round(clamp(ratio, 0, 1) * (this.days.length - 1)), true, event);
      });
      this.hit.addEventListener("pointerleave", () => {
        this.showDay(this.selectedDay, false);
      });
      this.hit.addEventListener("click", event => {
        const bounds = this.svg.getBoundingClientRect();
        const viewX = (event.clientX - bounds.left) / bounds.width * this.width;
        const ratio = (viewX - this.margin.left) /
          (this.width - this.margin.left - this.margin.right);
        this.selectedDay = Math.round(clamp(ratio, 0, 1) * (this.days.length - 1));
        this.onSelect(this.selectedDay);
        this.showDay(this.selectedDay, false, event);
      });
      this.root.querySelectorAll(".topic-key").forEach(button => {
        button.addEventListener("click", () => {
          const index = Number(button.dataset.topic);
          if (this.visibleTopics.has(index) && this.visibleTopics.size > 1) {
            this.visibleTopics.delete(index);
          } else {
            this.visibleTopics.add(index);
          }
          this.applyVisibility();
          this.showDay(this.selectedDay, false);
        });
      });
      this.root.querySelectorAll(".topic-actions button").forEach(button => {
        button.addEventListener("click", () => {
          this.visibleTopics = button.dataset.action === "all"
            ? new Set(this.rows.map(row => row.topicIndex))
            : new Set(this.defaultTopics);
          this.applyVisibility();
          this.showDay(this.selectedDay, false);
        });
      });
    }

    applyVisibility() {
      this.root.querySelectorAll(".topic-line").forEach(line => {
        line.classList.toggle("is-hidden", !this.visibleTopics.has(Number(line.dataset.topic)));
      });
      this.root.querySelectorAll(".topic-key").forEach(button => {
        const selected = this.visibleTopics.has(Number(button.dataset.topic));
        button.classList.toggle("is-active", selected);
        button.setAttribute("aria-pressed", selected ? "true" : "false");
      });
    }

    showDay(index, followingPointer, event) {
      if (!this.days.length) return;
      const dayIndex = clamp(index, 0, this.days.length - 1);
      const x = this.xFor(dayIndex);
      this.cursor.setAttribute("x1", x);
      this.cursor.setAttribute("x2", x);
      const visibleRows = this.rows.filter(row => this.visibleTopics.has(row.topicIndex));
      this.points.innerHTML = visibleRows.map(row =>
        `<circle cx="${x}" cy="${this.yFor(row.values[dayIndex])}" r="4" style="fill:${row.color}"/>`
      ).join("");

      const ranked = [...visibleRows].sort(
        (left, right) => right.values[dayIndex] - left.values[dayIndex]
      ).slice(0, 5);
      this.tooltip.hidden = false;
      this.tooltip.innerHTML = `<b>${escapeHtml(this.days[dayIndex])}</b>` + ranked.map(row =>
        `<span><i style="background:${row.color}"></i>${escapeHtml(row.label)}<strong>${(row.values[dayIndex] * 100).toFixed(1)}%</strong></span>`
      ).join("");

      const chartFrame = this.root.querySelector(".topic-chart-frame");
      const frameBounds = chartFrame.getBoundingClientRect();
      if (followingPointer && event) {
        const left = clamp(event.clientX - frameBounds.left + 18, 12, frameBounds.width - 210);
        const top = clamp(event.clientY - frameBounds.top - 54, 12, frameBounds.height - 180);
        this.tooltip.style.left = `${left}px`;
        this.tooltip.style.top = `${top}px`;
      } else {
        this.tooltip.style.left = "auto";
        this.tooltip.style.right = "18px";
        this.tooltip.style.top = "18px";
      }
    }
  }

  global.TopicTimeline = TopicTimeline;
})(window);

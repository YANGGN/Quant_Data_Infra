"use strict";
(() => {
  const source = document.getElementById("fp-data");
  if (!source) return;
  const {days, windows, statistic_columns = [], statistic_rows = []} = JSON.parse(source.dataset.series);
  const statistics = statistic_rows.map(row => Object.fromEntries(statistic_columns.map((key, i) => [key, row[i]])));
  if (!days.length) return;
  const chart = document.getElementById("fp-chart");
  const metric = document.getElementById("fp-metric");
  const slider = document.getElementById("fp-day");
  const detail = document.getElementById("fp-window");
  const selected = document.getElementById("fp-selected");
  const zChart = document.getElementById("fp-z-chart");
  const zMetric = document.getElementById("fp-z-metric");
  const byDate = new Map(statistics.map(row => [row.trade_date, row]));
  const markers = [];
  const ns = "http://www.w3.org/2000/svg";
  const fmt = (value, suffix = "") => value === null || value === undefined ? "Unavailable" :
    Number(value).toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2}) + suffix;
  const peValue = day => day[3] != null ? fmt(day[3], "×") :
    day[4] === "zero_forward_eps" ? "Undefined — zero EPS" : "Data unavailable";
  const status = day => day[3] == null ? (day[4] === "zero_forward_eps" ?
    "Undefined — zero EPS" : "Data unavailable · " + day[4].replaceAll("_", " ")) :
    day[3] < 0 ? "Expected loss" : day[4].replaceAll("_", " ");
  const label = value => String(value || "unavailable").replaceAll("_", " ");
  function el(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.append(node);
    return node;
  }
  function svgEl(tag, attributes, parent) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (parent) parent.append(node);
    return node;
  }
  const times = days.map(day => Date.parse(day[0] + "T00:00:00Z"));
  function inspect(index) {
    const day = days[index];
    document.getElementById("fp-date").textContent = day[0];
    slider.value = index;
    slider.setAttribute("aria-valuetext", day[0]);
    selected.replaceChildren();
    el("p", day[0] + " · P/E " + peValue(day) + " · EPS " + fmt(day[2]) + " · Close " + fmt(day[1]), selected);
    el("p", status(day), selected);
    const stats = byDate.get(day[0]);
    if (stats) {
      el("p", "Winsorized P/E " + fmt(stats.winsorized_pe, "×") + " · Z-score " + fmt(stats.z_score, "σ") + " · Raw P/E z-score " + fmt(stats.raw_z_score, "σ"), selected);
      const reason = stats.z_score_reason ? " · " + label(stats.z_score_reason) : "";
      el("p", stats.valid_count + " usable observations in " + stats.session_count + " sessions · " + stats.window_start + " to " + stats.window_end + reason, selected);
      if (stats.lower_bound !== null)
        el("p", "Caps " + fmt(stats.lower_bound, "×") + " to " + fmt(stats.upper_bound, "×") + (stats.was_clipped ? " · This session was capped" : " · This session is within the caps"), selected);
      if (stats.rolling_mean !== null)
        el("p", "Window mean " + fmt(stats.rolling_mean, "×") + " · Standard deviation " + fmt(stats.rolling_std, "×") + " · " + stats.negative_count + " negative observations", selected);
    }
    const window = windows[day[5]];
    detail.replaceChildren();
    if (!window) el("p", "No announcement window is available for this session.", detail);
    else {
      el("p", "Announcement " + (window.announcement || "Unavailable") + " · Effective session " + (window.effective_date || "Unavailable"), detail);
      el("p", "Reported fiscal period ended " + (window.reported_period_end || "Unavailable"), detail);
      const wrap = el("div", undefined, detail); wrap.className = "fp-table-scroll";
      const table = el("table", undefined, wrap);
      const head = el("tr", undefined, el("thead", undefined, table));
      el("th", "Forward fiscal period end", head); el("th", "Mean EPS estimate", head);
      const body = el("tbody", undefined, table);
      for (const component of window.components || []) {
        const row = el("tr", undefined, body);
        el("td", component.period_end || "Unavailable", row); el("td", fmt(component.eps), row);
      }
      if (!(window.components || []).length) {
        const cell = el("td", "No quarterly components available", el("tr", undefined, body)); cell.colSpan = 2;
      }
      el("p", "Flags: " + ((window.flags || []).map(label).join(", ") || "None recorded"), detail).className = "fp-flags";
    }
    for (const item of markers) {
      item.marker.setAttribute("x1", item.x(index)); item.marker.setAttribute("x2", item.x(index));
      const value = day[item.column];
      item.dot.setAttribute("visibility", value == null ? "hidden" : "visible");
      if (value != null) {item.dot.setAttribute("cx", item.x(index)); item.dot.setAttribute("cy", item.y(value));}
    }
  }
  function drawPanel(chart, metric, emptyId, isZ) {
    chart.hidden = false;
    const width = Math.max(chart.clientWidth, 270), height = width < 500 ? 260 : 310;
    const left = 62, right = width - 14, top = 12, bottom = height - 30;
    const column = Number(metric.value);
    const values = days.map(day => day[column]).filter(value => Number.isFinite(value));
    chart.replaceChildren();
    document.getElementById(emptyId).hidden = values.length > 0;
    chart.hidden = values.length === 0;
    if (!values.length) return;
    let low = Math.min(...values), high = Math.max(...values);
    const padding = (high - low) * 0.08 || Math.abs(high) * 0.08 || 1;
    low -= padding; high += padding;
    const x = index => left + (times[index] - times[0]) / (times.at(-1) - times[0] || 1) * (right - left);
    const y = value => bottom - (value - low) / (high - low) * (bottom - top);
    const svg = svgEl("svg", {viewBox:"0 0 " + width + " " + height, role:"img", "aria-label": metric.options[metric.selectedIndex].text + " daily history. Use the session slider to inspect values."}, chart);
    for (let i = 0; i <= 4; i++) {
      const value = low + (high - low) * i / 4, pos = y(value);
      svgEl("line", {x1:left, x2:right, y1:pos, y2:pos, class:"fp-grid"}, svg);
      const text = svgEl("text", {x:left-9, y:pos+4, "text-anchor":"end"}, svg);
      text.textContent = Math.abs(value) >= 10000 ? value.toPrecision(3) : value.toLocaleString("en-US", {maximumFractionDigits:1});
    }
    for (const index of [0, Math.floor((days.length-1)/2), days.length-1]) {
      const text = svgEl("text", {x:x(index), y:height-6, "text-anchor": index === 0 ? "start" : index === days.length-1 ? "end" : "middle"}, svg);
      text.textContent = days[index][0];
    }
    if (low < 0 && high > 0)
      svgEl("line", {x1:left, x2:right, y1:y(0), y2:y(0), class:"fp-zero"}, svg);
    if (isZ) for (const level of [-2, 2]) {
      if (level > low && level < high) {
        svgEl("line", {x1:left,x2:right,y1:y(level),y2:y(level),class:"fp-z-guide"}, svg);
        const text = svgEl("text", {x:right-3,y:y(level)-5,"text-anchor":"end"}, svg);
        text.textContent = (level > 0 ? "+" : "") + level + "σ";
      }
    }
    // A P/E sign change crosses an undefined ratio; EPS and price remain continuous.
    const connects = (a, b) => a >= 0 && b < days.length &&
      days[a][column] != null && days[b][column] != null &&
      (![3,6,7,8].includes(column) || Math.sign(days[a][2]) === Math.sign(days[b][2]));
    let path = "";
    days.forEach((day,index) => {
      if (day[column] == null) return;
      path += (connects(index-1, index) ? " L" : " M") + x(index).toFixed(2) + " " + y(day[column]).toFixed(2);
      if (!connects(index-1, index) && !connects(index, index+1))
        svgEl("circle", {cx:x(index),cy:y(day[column]),r:2,fill:isZ ? "#177a76" : "#326db1"}, svg);
    });
    svgEl("path", {d:path,class:isZ ? "fp-line fp-z-line" : "fp-line"}, svg);
    const marker = svgEl("line", {y1:top,y2:bottom,class:"fp-marker"}, svg);
    const dot = svgEl("circle", {r:4,class:"fp-dot"}, svg);
    markers.push({marker, dot, x, y, column});
    svg.addEventListener("click", event => {
      const rect = svg.getBoundingClientRect();
      const position = (event.clientX - rect.left) / rect.width * width;
      const target = times[0] + (position-left)/(right-left)*(times.at(-1)-times[0]);
      let index = 0;
      for (let i=1; i<times.length; i++) if (Math.abs(times[i]-target) < Math.abs(times[index]-target)) index=i;
      inspect(index);
    });
  }
  function draw() {
    markers.length = 0;
    drawPanel(chart, metric, "fp-empty-chart", false);
    drawPanel(zChart, zMetric, "fp-empty-z-chart", true);
    inspect(Number(slider.value));
  }
  chart.hidden = false;
  document.getElementById("fp-scrubber").hidden = false;
  slider.addEventListener("input", () => inspect(Number(slider.value)));
  metric.addEventListener("change", draw);
  zMetric.addEventListener("change", draw);
  let resize;
  window.addEventListener("resize", () => {clearTimeout(resize); resize=setTimeout(() => {chart.hidden=false; draw();}, 100);});
  draw();
})();

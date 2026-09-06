/* Progressive, local-only presentation for the current Inspector. */
(function () {
  "use strict";
  var root = document.querySelector("body.inspector");
  if (!root) return;

  var nav = root.querySelector("#inspector-navigation");
  var navToggle = root.querySelector("[data-inspector-nav-toggle]");
  var narrow = window.matchMedia("(max-width: 900px)");
  var navOpen = false;
  function renderNavigation() {
    var expanded = !narrow.matches || navOpen;
    nav.hidden = !expanded;
    navToggle.hidden = !narrow.matches;
    navToggle.setAttribute("aria-expanded", String(expanded));
    navToggle.textContent = expanded ? "Close navigation" : "Browse data";
  }
  if (nav && navToggle) {
    navToggle.addEventListener("click", function () {
      navOpen = !navOpen;
      renderNavigation();
    });
    nav.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && narrow.matches) {
        navOpen = false;
        renderNavigation();
        navToggle.focus();
      }
    });
    narrow.addEventListener("change", renderNavigation);
    renderNavigation();
  }

  var announcement = document.createElement("p");
  announcement.className = "inspector-sr-only";
  announcement.setAttribute("aria-live", "polite");
  root.appendChild(announcement);

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function cellText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
    if (node.nodeType === Node.ELEMENT_NODE && node.tagName === "BR") return "\n";
    return Array.from(node.childNodes).map(cellText).join("");
  }

  var resetRecordDetails = [];
  root.querySelectorAll("table[data-inspector-table]").forEach(function (table, tableIndex) {
    var workspace = table.closest("[data-inspector-table-workspace]");
    var head = table.tHead && table.tHead.rows[0];
    var body = table.tBodies[0];
    if (!workspace || !head || !body) return;
    var headers = Array.from(head.cells).map(function (cell) { return cell.textContent; });
    var rows = Array.from(body.rows).filter(function (row) {
      return row.cells.length === headers.length &&
        !Array.from(row.cells).some(function (cell) { return cell.colSpan > 1; });
    });
    if (!rows.length) return;
    var scroll = table.closest(".table-scroll");
    if (scroll && !scroll.hasAttribute("aria-label")) {
      scroll.setAttribute("role", "region");
      scroll.setAttribute("aria-label", table.caption ? table.caption.textContent : "Inspector table");
    }
    var identityIndex = headers.findIndex(function (label) {
      return /^(dataset or source|dataset|symbol|series|contract|date|trade date|published|tool)$/i.test(label);
    });
    if (identityIndex < 0) identityIndex = 0;
    head.cells[identityIndex].classList.add("inspector-identity");
    Array.from(head.cells).forEach(function (cell) { cell.setAttribute("scope", "col"); });
    var actionHead = element("th", "inspector-row-action", "Record");
    actionHead.scope = "col";
    head.appendChild(actionHead);

    var panel = element("aside", "inspector-record-details");
    panel.id = "inspector-record-details-" + tableIndex;
    panel.setAttribute("data-inspector-record-details", "");
    panel.setAttribute("aria-label", "Selected record details");
    panel.hidden = true;
    var panelHeader = element("div", "inspector-record-header");
    var heading = element("h3", "", "Record details");
    heading.tabIndex = -1;
    var close = element("button", "inspector-close-details", "Close");
    close.type = "button";
    close.setAttribute("data-inspector-close-details", "");
    close.setAttribute("aria-label", "Close record details");
    panelHeader.append(heading, close);
    var title = element("p", "inspector-record-title");
    var note = element("p", "inspector-record-note", table.getAttribute("data-inspector-record-note") || "Original retained fields · exact displayed values");
    var fields = element("dl");
    panel.append(panelHeader, title, note, fields);
    workspace.appendChild(panel);
    var activeButton = null;
    var activeRow = null;

    function closeDetails(restoreFocus) {
      panel.hidden = true;
      workspace.classList.remove("has-record-details");
      if (activeRow) activeRow.classList.remove("is-inspected");
      if (activeButton) {
        activeButton.setAttribute("aria-pressed", "false");
        if (restoreFocus !== false) activeButton.focus();
      }
      activeRow = null;
      activeButton = null;
      announcement.textContent = "Record details closed.";
    }
    resetRecordDetails.push(function () {
      if (activeButton) closeDetails(false);
    });
    close.addEventListener("click", closeDetails);
    panel.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDetails();
      }
    });

    rows.forEach(function (row, rowIndex) {
      var cells = Array.from(row.cells);
      cells[identityIndex].classList.add("inspector-identity");
      var identity = cells[identityIndex].textContent;
      var shortIdentity = identity.length > 96 ? identity.slice(0, 96) + "…" : identity;
      var label = "Row " + (rowIndex + 1) + " · " + shortIdentity;
      var actionCell = element("td", "inspector-row-action");
      var button = element("button", "inspector-record-button", "Inspect");
      button.type = "button";
      button.setAttribute("data-inspector-record-button", "");
      button.setAttribute("aria-label", "Inspect " + label);
      button.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-controls", panel.id);
      actionCell.appendChild(button);
      row.appendChild(actionCell);

      button.addEventListener("click", function () {
        if (activeButton) activeButton.setAttribute("aria-pressed", "false");
        if (activeRow) activeRow.classList.remove("is-inspected");
        activeButton = button;
        activeRow = row;
        button.setAttribute("aria-pressed", "true");
        row.classList.add("is-inspected");
        title.textContent = label;
        fields.replaceChildren();
        cells.forEach(function (cell, index) {
          var term = element("dt", "", cell.getAttribute("data-label") || cell.getAttribute("data-field") || headers[index]);
          var value = element("dd");
          var kind = cell.getAttribute("data-value-kind") || "text";
          value.setAttribute("data-value-kind", kind);
          if (kind === "null") {
            value.textContent = "Missing (null)";
          } else if (kind === "empty-string") {
            value.textContent = 'Empty string ("")';
          } else {
            // Preserve text nodes verbatim; existing BR separators remain newlines.
            value.textContent = cellText(cell);
          }
          var link = cell.querySelector("a[href]");
          if (link && /^(https?:|\/)/.test(link.getAttribute("href") || "")) {
            value.appendChild(document.createTextNode(" "));
            var detailLink = element("a", "", "Open link");
            detailLink.href = link.getAttribute("href");
            if (link.target === "_blank") {
              detailLink.target = "_blank";
              detailLink.rel = "noopener noreferrer";
            }
            value.appendChild(detailLink);
          }
          fields.append(term, value);
        });
        panel.hidden = false;
        workspace.classList.add("has-record-details");
        heading.focus({preventScroll: true});
        if (window.matchMedia("(max-width: 1160px)").matches) {
          panel.scrollIntoView({block: "nearest", behavior: "auto"});
        }
        announcement.textContent = "Record details open. " + label + ". " + cells.length + " fields.";
      });
    });
    // Supplemental columns remain in the DOM for exact details and no-JS fallback.
    table.classList.add("inspector-enhanced");
  });

  var groupSelect = root.querySelector("[data-status-group-select]");
  var groupControl = root.querySelector("[data-status-group-control]");
  var statusGroups = Array.from(root.querySelectorAll("[data-status-group]"));
  if (groupSelect && groupControl && statusGroups.length) {
    function showStatusGroup(announce) {
      var selected = groupSelect.value === "other" ? "other" : "live";
      var focusInGroup = statusGroups.some(function (group) {
        return group.contains(document.activeElement);
      });
      groupSelect.value = selected;
      resetRecordDetails.forEach(function (reset) { reset(); });
      statusGroups.forEach(function (group) {
        group.hidden = group.getAttribute("data-status-group") !== selected;
      });
      if (focusInGroup) groupSelect.focus();
      if (announce) {
        announcement.textContent = groupSelect.options[groupSelect.selectedIndex].textContent + " selected.";
      }
    }
    groupSelect.value = "live";
    showStatusGroup(false);
    groupSelect.addEventListener("change", function () { showStatusGroup(true); });
    groupControl.hidden = false;
  }
})();

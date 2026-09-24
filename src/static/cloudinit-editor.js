/* Cloud-init node editor.
   Schema-driven editor for the cloud-config mapping inside a Linux seed.
   Reads cc_schema / cc_view.doc from JSON data islands in cloudinit_editor.html,
   keeps a deep-copied state, and mirrors it into the hidden [data-cc-state] input.
   Never log editor state or seed text: they can hold secrets. */
(() => {
  "use strict";

  /* Every field type this editor renders. tests/test_cloudinit_editor.py parses this literal. */
  const FIELD_TYPES = Object.freeze([
    "string",
    "text",
    "bool",
    "int",
    "enum",
    "flex",
    "string_list",
    "object",
    "row_list",
    "yaml_value",
    "disk_layout",
    "all_or_list",
    "lines",
    "yaml",
    "string_or_list",
    "command_list",
    "map",
    "enum_list",
    "package_list",
  ]);

  const WIDE_TYPES = new Set([
    "text",
    "string_list",
    "object",
    "row_list",
    "yaml_value",
    "disk_layout",
    "all_or_list",
    "lines",
    "yaml",
    "string_or_list",
    "command_list",
    "map",
    "enum_list",
    "package_list",
  ]);

  const BLOCK_LABELS = {
    ssh_keys: "Use the machine SSH keys field",
    packages: "Use the machine packages field",
  };
  const CREDENTIAL_CHOICES = [
    ["{{password_hash}}", "{{password_hash}} (hash)"],
    ["{{password}}", "{{password}} (plain)"],
  ];
  const ADVANCED_HINT_KEYS = [
    "cloud_init_modules",
    "cloud_config_modules",
    "cloud_final_modules",
    "merge_how",
    "merge_type",
    "launch-index",
    "output",
    "reporting",
    "system_info",
  ];

  const OVERVIEW = "__overview__";
  const ADVANCED = "__advanced__";
  const INSTALLER = "__installer__";
  const ADVANCED_GROUP = "Advanced";

  const MORE_THRESHOLD = 12;
  const MORE_VISIBLE = 8;
  const PREVIEW_DELAY_MS = 600;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICON_PATHS = {
    up: "M4 12l1.41 1.41L11 7.83V20h2V7.83l5.58 5.59L20 12l-8-8-8 8z",
    down: "M20 12l-1.41-1.41L13 16.17V4h-2v12.17l-5.58-5.59L4 12l8 8 8-8z",
    remove: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    chevron: "M8.59 16.59 10 18l6-6-6-6-1.41 1.41L13.17 12z",
    external:
      "M14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7zM19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2v7z",
  };

  /* Server supports action "preview"; flips to "apply" once if it answers 400 Unknown action. */
  let previewAction = "preview";
  let previewWanted = false;
  let uidCounter = 0;

  const editors = new WeakMap();

  /* ---------- small helpers ---------- */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function button(className, text, label) {
    const btn = el("button", className, text);
    btn.type = "button";
    if (label) {
      btn.setAttribute("aria-label", label);
      btn.title = label;
    }
    return btn;
  }

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", ICON_PATHS[name] || "");
    svg.append(path);
    return svg;
  }

  function iconButton(name, label) {
    const btn = button("icon-btn", undefined, label);
    btn.append(icon(name));
    return btn;
  }

  function uid(prefix) {
    uidCounter += 1;
    return `cc-${prefix || "f"}-${uidCounter}`;
  }

  function deepCopy(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isScalar(value) {
    return value === null || value === undefined || ["string", "number", "boolean"].includes(typeof value);
  }

  function isBlockMarker(value, token) {
    return isPlainObject(value) && typeof value.__pxe_block__ === "string" && (token === undefined || value.__pxe_block__ === token);
  }

  /* Deep "is anything set": false and 0 count, empty strings/lists/objects do not. */
  function hasValue(value) {
    if (value === null || value === undefined) {
      return false;
    }
    if (typeof value === "string") {
      return value.trim() !== "";
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return true;
    }
    if (Array.isArray(value)) {
      return value.some(hasValue);
    }
    if (typeof value === "object") {
      if (isBlockMarker(value)) {
        return true;
      }
      return Object.keys(value).some((key) => hasValue(value[key]));
    }
    return false;
  }

  /* Canonical form for dirty tracking: drops empties so "typed then erased" is clean again. */
  function normalize(value) {
    if (Array.isArray(value)) {
      const items = value.map(normalize);
      return items.some((item) => item !== undefined) ? items : undefined;
    }
    if (isPlainObject(value)) {
      const out = {};
      for (const key of Object.keys(value).sort()) {
        const item = normalize(value[key]);
        if (item !== undefined) {
          out[key] = item;
        }
      }
      return Object.keys(out).length ? out : undefined;
    }
    if (value === null || value === undefined) {
      return undefined;
    }
    if (typeof value === "string" && value.trim() === "") {
      return undefined;
    }
    return value;
  }

  function scalarText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    if (isBlockMarker(value)) {
      return `{{${value.__pxe_block__}}}`;
    }
    if (typeof value === "object") {
      return JSON.stringify(value);
    }
    return String(value);
  }

  function displayValue(value) {
    if (typeof value === "string") {
      return value;
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  function formatDefault(value) {
    if (value === true) {
      return "yes";
    }
    if (value === false) {
      return "no";
    }
    if (typeof value === "object") {
      return JSON.stringify(value);
    }
    return String(value);
  }

  function linesOf(text) {
    return String(text || "")
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "");
  }

  function autoRows(textarea, min, max, pad) {
    const count = String(textarea.value || "").split("\n").length + (pad === undefined ? 1 : pad);
    textarea.rows = Math.max(min, Math.min(max, count));
  }

  function singular(label) {
    const text = String(label || "").trim().toLowerCase();
    if (!text) {
      return "item";
    }
    if (/ies$/.test(text)) {
      return text.replace(/ies$/, "y");
    }
    if (/(sses|xes|ches|shes|ases)$/.test(text)) {
      return text.replace(/es$/, "");
    }
    if (/[^s]s$/.test(text) && text.length > 3) {
      return text.slice(0, -1);
    }
    return text;
  }

  function capitalize(text) {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
  }

  function shellSplit(text) {
    const out = [];
    let cur = "";
    let quote = null;
    let started = false;
    const src = String(text || "");
    for (let i = 0; i < src.length; i += 1) {
      const ch = src[i];
      if (quote) {
        if (ch === quote) {
          quote = null;
        } else if (ch === "\\" && quote === '"' && i + 1 < src.length) {
          i += 1;
          cur += src[i];
        } else {
          cur += ch;
        }
        continue;
      }
      if (ch === "'" || ch === '"') {
        quote = ch;
        started = true;
        continue;
      }
      if (ch === "\\" && i + 1 < src.length) {
        i += 1;
        cur += src[i];
        started = true;
        continue;
      }
      if (/\s/.test(ch)) {
        if (started) {
          out.push(cur);
          cur = "";
          started = false;
        }
        continue;
      }
      cur += ch;
      started = true;
    }
    if (started) {
      out.push(cur);
    }
    return out;
  }

  function shellQuote(arg) {
    const text = String(arg);
    if (text !== "" && /^[A-Za-z0-9_@%+=:,./-]+$/.test(text)) {
      return text;
    }
    return `'${text.replace(/'/g, "'\\''")}'`;
  }

  /* Top-level keys under `autoinstall:` when the server does not send installer_keys. */
  function guessInstallerKeys(seedText) {
    const lines = String(seedText || "").split(/\r?\n/);
    const start = lines.findIndex((line) => /^autoinstall:\s*(#.*)?$/.test(line));
    if (start < 0) {
      return [];
    }
    const keys = [];
    let indent = null;
    for (let i = start + 1; i < lines.length; i += 1) {
      const line = lines[i];
      if (!line.trim() || /^\s*#/.test(line)) {
        continue;
      }
      if (!/^\s/.test(line)) {
        break;
      }
      const match = line.match(/^(\s+)([A-Za-z0-9_.-]+):/);
      if (!match) {
        continue;
      }
      if (indent === null) {
        indent = match[1].length;
      }
      if (match[1].length === indent && match[2] !== "user-data" && !keys.includes(match[2])) {
        keys.push(match[2]);
      }
    }
    return keys;
  }

  /* ---------- bindings: read/write one value inside the editor state ---------- */

  function keyBind(parent, key) {
    return {
      path: parent.path.concat([key]),
      get() {
        const obj = parent.get();
        return isPlainObject(obj) ? obj[key] : undefined;
      },
      set(value) {
        let obj = parent.get();
        if (!isPlainObject(obj)) {
          if (value === undefined) {
            return;
          }
          obj = {};
          parent.set(obj);
        }
        if (value === undefined) {
          delete obj[key];
        } else {
          obj[key] = value;
        }
      },
    };
  }

  function indexBind(listBind, index) {
    return {
      path: listBind.path.concat([index]),
      get() {
        const list = listBind.get();
        return Array.isArray(list) ? list[index] : undefined;
      },
      set(value) {
        let list = listBind.get();
        if (!Array.isArray(list)) {
          list = [];
          listBind.set(list);
        }
        list[index] = value === undefined ? "" : value;
      },
    };
  }

  function schemaKey(path) {
    return path.filter((part) => typeof part !== "number").join(".");
  }

  /* ---------- the editor ---------- */

  function createEditor(root, options) {
    const opts = options || {};
    const schemaEl = root.querySelector("script.cc-schema");
    const docEl = root.querySelector("script.cc-doc");
    const nav = root.querySelector(".cc-nav");
    const detail = root.querySelector(".cc-detail");
    if (!schemaEl || !docEl || !nav || !detail) {
      return null;
    }
    let schema;
    let doc;
    try {
      schema = JSON.parse(schemaEl.textContent || "null");
      doc = JSON.parse(docEl.textContent || "null");
    } catch {
      return null;
    }
    if (!Array.isArray(schema)) {
      return null;
    }

    const ac = new AbortController();
    const on = (target, type, handler, capture) => {
      if (target) {
        target.addEventListener(type, handler, { signal: ac.signal, capture: Boolean(capture) });
      }
    };

    const nodes = schema.filter((node) => isPlainObject(node) && typeof node.id === "string" && node.id);
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const keyOwner = new Map();
    for (const node of nodes) {
      for (const field of node.fields || []) {
        if (field && typeof field.key === "string" && !keyOwner.has(field.key)) {
          keyOwner.set(field.key, node.id);
        }
      }
    }

    const state = isPlainObject(doc) ? deepCopy(doc) : {};
    if (!isPlainObject(state.__yaml__)) {
      state.__yaml__ = {};
    }
    const rootBind = { path: [], get: () => state, set: () => {} };
    const yamlHolder = keyBind(rootBind, "__yaml__");

    const mode = typeof opts.mode === "string" && opts.mode ? opts.mode : root.dataset.mode || "";
    const notices = Array.isArray(opts.notices) ? opts.notices.filter((item) => typeof item === "string" && item) : [];
    let installerKeys = Array.isArray(opts.installerKeys)
      ? opts.installerKeys.filter((item) => typeof item === "string" && item)
      : null;
    if (mode === "autoinstall" && (!installerKeys || !installerKeys.length)) {
      const guessed = guessInstallerKeys(opts.seedText);
      installerKeys = guessed.length ? guessed : installerKeys;
    }
    const disabled = root.getAttribute("data-disabled") === "1";

    const search = root.querySelector(".cc-search");
    const onlySet = root.querySelector(".cc-only-set-box");
    const navSelect = root.querySelector(".cc-nav-select");
    const extraBox = root.querySelector("[data-cc-extra]");
    const extraHome = root.querySelector(".cc-extra-home");
    const preview = root.querySelector(".cc-preview");
    const previewBody = root.querySelector(".cc-preview-body");
    const previewStatus = root.querySelector(".cc-preview-status");
    const previewTitle = root.querySelector(".cc-preview-title");
    const statusEl = opts.statusEl || null;

    root.querySelectorAll("[data-cc-state]").forEach((node) => node.remove());
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.setAttribute("data-cc-state", "");
    hidden.value = JSON.stringify(state);
    root.append(hidden);

    const extraText = () => (extraBox ? extraBox.value : "");
    const snapshot = () => `${JSON.stringify(normalize(state) || {})}\u0000${extraText().trim()}`;
    let baseline = snapshot();

    const pseudo = {
      [OVERVIEW]: { id: OVERVIEW, label: "Overview", pseudo: true },
      [ADVANCED]: { id: ADVANCED, label: "Other keys (YAML)", group: ADVANCED_GROUP, pseudo: true },
      [INSTALLER]: { id: INSTALLER, label: "Installer (read-only)", group: ADVANCED_GROUP, pseudo: true },
    };

    function nodeFor(id) {
      return nodeById.get(id) || pseudo[id] || null;
    }

    function nodeConfigured(node) {
      if (!node) {
        return false;
      }
      if (node.id === ADVANCED) {
        return extraText().trim() !== "";
      }
      if (node.pseudo) {
        return false;
      }
      for (const field of node.fields || []) {
        if (!field) {
          continue;
        }
        if (field.type === "yaml") {
          if (hasValue(state.__yaml__[node.id])) {
            return true;
          }
          continue;
        }
        if (typeof field.key === "string" && hasValue(state[field.key])) {
          return true;
        }
      }
      return false;
    }

    /* ---------- search index ---------- */

    const searchIndex = new Map();
    function collectFields(fields, path, out) {
      for (const field of fields || []) {
        if (!isPlainObject(field)) {
          continue;
        }
        const key = typeof field.key === "string" ? field.key : "";
        const fieldPath = key ? path.concat([key]) : path;
        out.push({
          key: schemaKey(fieldPath),
          label: String(field.label || key),
          text: `${key} ${field.label || ""} ${field.help || ""}`.toLowerCase(),
        });
        collectFields(field.object_fields, fieldPath, out);
        collectFields(field.item_fields, fieldPath, out);
      }
    }
    for (const node of nodes) {
      const fields = [];
      collectFields(node.fields, [], fields);
      searchIndex.set(node.id, {
        base: [node.label, node.id, node.group, node.help, node.module].filter(Boolean).join(" ").toLowerCase(),
        name: `${node.label || ""} ${node.id}`.toLowerCase(),
        fields,
      });
    }
    searchIndex.set(ADVANCED, {
      base: `other keys yaml advanced extra ${ADVANCED_HINT_KEYS.join(" ")}`,
      fields: [],
    });
    searchIndex.set(INSTALLER, {
      base: `installer autoinstall read-only ${(installerKeys || []).join(" ")}`.toLowerCase(),
      fields: [],
    });

    function matchNode(id, terms) {
      const entry = searchIndex.get(id);
      if (!entry) {
        return null;
      }
      const missing = terms.filter((term) => !entry.base.includes(term));
      if (!missing.length) {
        const name = entry.name || "";
        let score = 1;
        if (terms.every((term) => name.includes(term))) {
          score = name.startsWith(terms[0]) ? 4 : 3;
        }
        return { hit: null, score };
      }
      const hay = `${entry.base} ${entry.fields.map((field) => field.text).join(" ")}`;
      if (!terms.every((term) => hay.includes(term))) {
        return null;
      }
      const hit =
        entry.fields.find((field) => missing.every((term) => field.text.includes(term))) ||
        entry.fields.find((field) => field.text.includes(missing[0]));
      const keyHit = hit && missing.every((term) => `${hit.key} ${hit.label}`.toLowerCase().includes(term));
      return { hit: hit || null, score: keyHit ? 2 : 0 };
    }

    /* ---------- navigation ---------- */

    const navRefs = new Map();
    const groupRefs = new Map();
    const searchHits = new Map();
    const searchScores = new Map();
    let currentId = OVERVIEW;
    let filterWasActive = false;

    function navButton(node) {
      const btn = button("cc-node");
      btn.dataset.node = node.id;
      btn.tabIndex = -1;
      const dot = el("span", node.id === OVERVIEW ? "cc-dot cc-dot-none" : "cc-dot");
      dot.setAttribute("aria-hidden", "true");
      const text = el("span", "cc-node-text");
      const name = el("span", "cc-node-name", node.label || node.id);
      const sr = el("span", "cc-sr", "");
      const hint = el("span", "cc-node-hint");
      hint.hidden = true;
      text.append(name, sr, hint);
      btn.append(dot, text);
      on(btn, "click", () => showNode(node.id, { path: searchHits.get(node.id) || null }));
      navRefs.set(node.id, { btn, hint, sr, node });
      return btn;
    }

    function buildNav() {
      nav.replaceChildren();
      navRefs.clear();
      groupRefs.clear();
      nav.append(navButton(pseudo[OVERVIEW]));
      const groups = new Map();
      for (const node of nodes) {
        const name = typeof node.group === "string" && node.group ? node.group : "Other";
        if (!groups.has(name)) {
          groups.set(name, []);
        }
        groups.get(name).push(node);
      }
      const advancedNodes = [pseudo[ADVANCED]];
      if (mode === "autoinstall") {
        advancedNodes.push(pseudo[INSTALLER]);
      }
      const all = Array.from(groups.entries());
      all.push([ADVANCED_GROUP, advancedNodes]);
      for (const [name, members] of all) {
        const details = el("details", "cc-group");
        const summary = el("summary");
        summary.tabIndex = -1;
        const count = el("span", "cc-group-count");
        count.hidden = true;
        summary.append(el("span", "cc-group-name", name), count);
        const list = el("div", "cc-group-items");
        for (const node of members) {
          list.append(navButton(node));
        }
        details.append(summary, list);
        nav.append(details);
        groupRefs.set(name, { details, count, members, summary });
      }
      const empty = el("p", "cc-nav-empty meta", "No modules match.");
      empty.hidden = true;
      nav.append(empty);
      restoreGroupOpenState();
    }

    function restoreGroupOpenState() {
      groupRefs.forEach((ref) => {
        ref.details.open = ref.members.some((node) => node.id === currentId || nodeConfigured(node));
      });
    }

    function refreshNavState() {
      let configured = 0;
      navRefs.forEach((ref, id) => {
        const set = nodeConfigured(ref.node);
        ref.btn.classList.toggle("is-set", set);
        ref.sr.textContent = set ? " (configured)" : "";
        if (set && id !== OVERVIEW && id !== INSTALLER) {
          configured += 1;
        }
      });
      groupRefs.forEach((ref) => {
        const setCount = ref.members.filter((node) => nodeConfigured(node)).length;
        ref.count.hidden = setCount === 0;
        ref.count.textContent = String(setCount);
        ref.count.title = `${setCount} of ${ref.members.length} configured`;
      });
      if (onlySet && onlySet.checked) {
        applyFilter();
      } else {
        refreshSelect();
      }
      return configured;
    }

    function filterTerms() {
      const q = search ? search.value.trim().toLowerCase() : "";
      return q ? q.split(/\s+/) : [];
    }

    function applyFilter() {
      const terms = filterTerms();
      const only = Boolean(onlySet && onlySet.checked);
      const active = terms.length > 0 || only;
      searchHits.clear();
      searchScores.clear();
      let anyVisible = false;
      groupRefs.forEach((ref) => {
        let visible = 0;
        for (const node of ref.members) {
          const navRef = navRefs.get(node.id);
          let ok = true;
          let hit = null;
          let score = 0;
          if (terms.length) {
            const result = matchNode(node.id, terms);
            ok = Boolean(result);
            hit = result ? result.hit : null;
            score = result ? result.score : 0;
          }
          if (ok && only && node.id !== currentId && !nodeConfigured(node)) {
            ok = false;
          }
          navRef.btn.hidden = !ok;
          navRef.hint.hidden = !(ok && hit);
          navRef.hint.textContent = ok && hit ? `${hit.label} · ${hit.key}` : "";
          if (ok && hit) {
            searchHits.set(node.id, hit.key);
          }
          if (ok && terms.length) {
            searchScores.set(node.id, score);
          }
          if (ok) {
            visible += 1;
          }
        }
        ref.details.hidden = visible === 0;
        if (active && visible > 0) {
          ref.details.open = true;
        }
        if (visible > 0) {
          anyVisible = true;
        }
      });
      if (!active && filterWasActive) {
        restoreGroupOpenState();
      }
      filterWasActive = active;
      const empty = nav.querySelector(".cc-nav-empty");
      if (empty) {
        empty.hidden = anyVisible;
      }
      refreshSelect();
      syncTabStops();
    }

    function refreshSelect() {
      if (!navSelect) {
        return;
      }
      navSelect.replaceChildren();
      const overview = el("option", "", "Overview");
      overview.value = OVERVIEW;
      navSelect.append(overview);
      groupRefs.forEach((ref, name) => {
        const visible = ref.members.filter((node) => {
          const navRef = navRefs.get(node.id);
          return navRef && !navRef.btn.hidden;
        });
        if (!visible.length) {
          return;
        }
        const group = el("optgroup");
        group.label = name;
        for (const node of visible) {
          const opt = el("option", "", `${nodeConfigured(node) ? "● " : ""}${node.label || node.id}`);
          opt.value = node.id;
          group.append(opt);
        }
        navSelect.append(group);
      });
      navSelect.value = currentId;
      if (navSelect.value !== currentId) {
        const opt = el("option", "", nodeFor(currentId)?.label || currentId);
        opt.value = currentId;
        navSelect.append(opt);
        navSelect.value = currentId;
      }
    }

    function navItems() {
      return Array.from(nav.querySelectorAll(".cc-node, .cc-group > summary")).filter((item) => {
        if (item.hidden) {
          return false;
        }
        const group = item.closest(".cc-group");
        if (!group) {
          return true;
        }
        if (group.hidden) {
          return false;
        }
        return item.tagName === "SUMMARY" || group.open;
      });
    }

    function syncTabStops() {
      const items = navItems();
      let stop = items.find((item) => item.classList.contains("cc-node") && item.dataset.node === currentId);
      if (!stop) {
        stop = items[0];
      }
      nav.querySelectorAll(".cc-node, .cc-group > summary").forEach((item) => {
        item.tabIndex = item === stop ? 0 : -1;
      });
    }

    on(nav, "keydown", (event) => {
      const items = navItems();
      const index = items.indexOf(document.activeElement);
      if (index < 0) {
        return;
      }
      const current = items[index];
      let target = null;
      if (event.key === "ArrowDown") {
        target = items[Math.min(items.length - 1, index + 1)];
      } else if (event.key === "ArrowUp") {
        target = items[Math.max(0, index - 1)];
      } else if (event.key === "Home") {
        target = items[0];
      } else if (event.key === "End") {
        target = items[items.length - 1];
      } else if (event.key === "ArrowRight" && current.tagName === "SUMMARY") {
        current.parentElement.open = true;
        event.preventDefault();
        return;
      } else if (event.key === "ArrowLeft") {
        if (current.tagName === "SUMMARY") {
          current.parentElement.open = false;
          syncTabStops();
          current.tabIndex = 0;
        } else {
          const group = current.closest(".cc-group");
          if (group) {
            target = group.querySelector("summary");
          }
        }
      } else {
        return;
      }
      event.preventDefault();
      if (target) {
        items.forEach((item) => {
          item.tabIndex = -1;
        });
        target.tabIndex = 0;
        target.focus();
      }
    });
    on(nav, "toggle", () => syncTabStops(), true);

    on(search, "input", () => applyFilter());
    on(search, "keydown", (event) => {
      if (event.key === "Escape" && search.value) {
        event.preventDefault();
        event.stopPropagation();
        search.value = "";
        applyFilter();
      } else if (event.key === "ArrowDown") {
        const first = navItems().find((item) => item.classList.contains("cc-node") && item.dataset.node !== OVERVIEW);
        if (first) {
          event.preventDefault();
          first.tabIndex = 0;
          first.focus();
        }
      } else if (event.key === "Enter") {
        let best = null;
        let bestScore = -1;
        navRefs.forEach((ref, id) => {
          if (id === OVERVIEW || ref.btn.hidden || (ref.btn.closest(".cc-group") || {}).hidden) {
            return;
          }
          const score = searchScores.get(id) || 0;
          if (score > bestScore) {
            best = id;
            bestScore = score;
          }
        });
        if (best) {
          event.preventDefault();
          showNode(best, { path: searchHits.get(best) || null });
        }
      }
    });
    on(onlySet, "change", () => applyFilter());
    on(navSelect, "change", () => showNode(navSelect.value, { path: searchHits.get(navSelect.value) || null }));

    /* ---------- change tracking ---------- */

    let paneRefresh = null;
    const expanders = new WeakMap();

    function isDirty() {
      return snapshot() !== baseline;
    }

    function refreshFooter(configured) {
      if (!statusEl) {
        return;
      }
      statusEl.replaceChildren();
      const count = configured === undefined ? countConfigured() : configured;
      statusEl.append(el("span", "cc-status-count", `${count} ${count === 1 ? "module" : "modules"} configured`));
      if (isDirty()) {
        statusEl.append(el("span", "cc-status-sep", " · "), el("span", "cc-dirty", "Unsaved changes"));
      } else {
        statusEl.append(el("span", "cc-status-sep", " · "), el("span", "cc-status-hint", "Apply writes into the user-data box; Save on the page keeps it."));
      }
    }

    function countConfigured() {
      let total = 0;
      for (const node of nodes) {
        if (nodeConfigured(node)) {
          total += 1;
        }
      }
      if (extraText().trim()) {
        total += 1;
      }
      return total;
    }

    function changed() {
      hidden.value = JSON.stringify(state);
      const configured = refreshNavState();
      refreshFooter(configured);
      if (paneRefresh) {
        paneRefresh();
      }
      schedulePreview();
    }

    on(extraBox, "input", () => changed());

    /* ---------- preview ---------- */

    let previewTimer = null;
    let previewSeq = 0;
    let lastPreview = null;
    let nodeYamlView = null;

    function previewOpen() {
      return Boolean(preview && !preview.hidden);
    }

    function schedulePreview() {
      if (!previewOpen() && !nodeYamlView) {
        return;
      }
      if (previewStatus) {
        previewStatus.textContent = "Updating…";
      }
      if (preview) {
        preview.classList.add("is-stale");
      }
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(refreshPreview, PREVIEW_DELAY_MS);
    }

    async function requestPreview() {
      const body = {
        action: previewAction,
        seed: typeof opts.getSeed === "function" ? opts.getSeed() : "",
        cc_json: hidden.value,
        cc_extra_yaml: extraText(),
      };
      try {
        return await postEditor(body);
      } catch (err) {
        if (previewAction === "preview" && err && err.status === 400 && /unknown action/i.test(String(err.message))) {
          previewAction = "apply";
          return requestPreview();
        }
        throw err;
      }
    }

    async function refreshPreview() {
      window.clearTimeout(previewTimer);
      if (!previewOpen() && !nodeYamlView) {
        return;
      }
      previewSeq += 1;
      const seq = previewSeq;
      if (previewStatus) {
        previewStatus.textContent = "Updating…";
      }
      try {
        const result = await requestPreview();
        if (seq !== previewSeq || ac.signal.aborted) {
          return;
        }
        lastPreview = result || {};
        renderPreview(null);
      } catch (err) {
        if (seq !== previewSeq || ac.signal.aborted) {
          return;
        }
        renderPreview(err instanceof Error ? err.message : "Preview failed");
      }
    }

    function renderPreview(errorMessage) {
      const fallback = previewAction !== "preview";
      if (preview && previewBody) {
        preview.classList.toggle("is-stale", Boolean(errorMessage));
        const errorEl = preview.querySelector(".cc-preview-error");
        if (errorEl) {
          errorEl.hidden = !errorMessage;
          errorEl.textContent = errorMessage || "";
        }
        let title = "user-data after Apply";
        if (lastPreview) {
          let text = lastPreview.user_data;
          if (typeof text !== "string") {
            const excerpt = mode === "autoinstall" ? userDataExcerpt(lastPreview.seed) : null;
            text = excerpt || lastPreview.seed;
            if (excerpt) {
              title = "autoinstall user-data after Apply";
            } else if (mode !== "autoinstall") {
              title = "user-data after Apply";
            } else {
              title = "Seed file after Apply";
            }
          }
          previewBody.textContent = typeof text === "string" && text ? text : "# (empty)";
        }
        if (previewTitle) {
          previewTitle.textContent = title;
        }
        if (previewStatus) {
          previewStatus.textContent = errorMessage ? "Not updated" : "Up to date";
        }
      }
      if (nodeYamlView) {
        nodeYamlView.render(errorMessage);
      }
    }

    /* Fallback when the server has no preview action: cut autoinstall.user-data out of the seed. */
    function userDataExcerpt(seedText) {
      if (typeof seedText !== "string") {
        return null;
      }
      const lines = seedText.split(/\r?\n/);
      const start = lines.findIndex((line) => /^\s+user-data:\s*$/.test(line));
      if (start < 0) {
        return null;
      }
      const indentOf = (line) => line.length - line.trimStart().length;
      const indent = indentOf(lines[start]);
      const out = [];
      for (let i = start + 1; i < lines.length; i += 1) {
        const line = lines[i];
        if (line.trim() && indentOf(line) <= indent) {
          break;
        }
        out.push(line.slice(Math.min(indent + 2, indentOf(line))));
      }
      while (out.length && !out[out.length - 1].trim()) {
        out.pop();
      }
      return out.length ? `#cloud-config\n${out.join("\n")}` : null;
    }

    function setPreviewOpen(open) {
      if (!preview) {
        return;
      }
      preview.hidden = !open;
      root.classList.toggle("cc-has-preview", open);
      if (open) {
        refreshPreview();
      }
    }

    /* ---------- field rendering ---------- */

    function childCtx(ctx, overrides) {
      return { ...ctx, checks: [], ...overrides };
    }

    function runChecks(ctx) {
      let bad = false;
      for (const check of ctx.checks) {
        if (check()) {
          bad = true;
        }
      }
      return bad;
    }

    function isRequired(field, ctx) {
      if (field.required) {
        return true;
      }
      if (ctx.entryRule === "chpasswd") {
        if (field.key === "name") {
          return true;
        }
        if (field.key === "password") {
          const row = ctx.scope ? ctx.scope.get() : null;
          return !(isPlainObject(row) && row.type === "RANDOM");
        }
      }
      return false;
    }

    function addRequiredCheck(ctx, field, bind, control) {
      if (!field.required && ctx.entryRule !== "chpasswd") {
        return;
      }
      ctx.checks.push(() => {
        const scopeValue = ctx.scope ? ctx.scope.get() : null;
        const bad = isRequired(field, ctx) && !hasValue(bind.get()) && hasValue(scopeValue);
        control.classList.toggle("cc-invalid", bad);
        if (bad) {
          control.setAttribute("aria-invalid", "true");
        } else {
          control.removeAttribute("aria-invalid");
        }
        return bad;
      });
    }

    function frame(field, ctx, frameOpts) {
      const fo = frameOpts || {};
      const wrap = el("div");
      const id = uid();
      const head = el("div", "cc-label-row");
      const label = el(fo.group ? "span" : "label", "cc-label");
      if (!fo.group) {
        label.htmlFor = id;
      }
      label.id = `${id}-label`;
      label.append(document.createTextNode(String(field.label || field.key || "Value")));
      if (
        ctx.depth === 0 &&
        typeof ctx.soloLabel === "string" &&
        String(field.label || "").trim().toLowerCase() === ctx.soloLabel.trim().toLowerCase()
      ) {
        label.classList.add("cc-sr");
      }
      if (isRequired(field, ctx)) {
        const star = el("span", "cc-req", " *");
        star.setAttribute("aria-hidden", "true");
        label.append(star);
      }
      head.append(label);
      if (field.key && field.label && String(field.label) !== String(field.key) && !String(field.key).startsWith("__")) {
        head.append(el("code", "cc-key", field.key));
      }
      if (field.deprecated) {
        const chip = el("span", "cc-deprecated", "Deprecated");
        chip.title = typeof field.deprecated === "string" ? field.deprecated : "Deprecated in cloud-init";
        head.append(chip);
      }
      wrap.append(head);
      return { wrap, id, labelId: label.id, head };
    }

    function appendHelp(wrap, field, control, extra) {
      const parts = [];
      if (typeof field.deprecated === "string" && field.deprecated) {
        parts.push(["cc-help cc-help-warn", field.deprecated]);
      }
      if (typeof field.help === "string" && field.help) {
        parts.push(["cc-help", field.help]);
      }
      if (extra) {
        parts.push(["cc-help", extra]);
      }
      const helpText = typeof field.help === "string" ? field.help : "";
      if (field.default !== undefined && field.default !== null && field.default !== "" && !/default/i.test(helpText)) {
        parts.push(["cc-help", `Default: ${formatDefault(field.default)}`]);
      }
      const ids = [];
      for (const [cls, text] of parts) {
        const p = el("p", cls, text);
        p.id = uid("help");
        ids.push(p.id);
        wrap.append(p);
      }
      if (control && ids.length) {
        control.setAttribute("aria-describedby", ids.join(" "));
      }
    }

    function setPlaceholder(control, field) {
      if (field.secret || field.credential) {
        return;
      }
      const raw = field.placeholder;
      if (raw === undefined || raw === null || raw === "") {
        return;
      }
      const lines = String(raw)
        .split("\n")
        .filter((line) => line.trim() !== "");
      if (!lines.length) {
        return;
      }
      if (control.tagName === "TEXTAREA" && lines.length > 1) {
        control.placeholder = `e.g.\n${lines.slice(0, 4).join("\n")}${lines.length > 4 ? "\n…" : ""}`;
      } else {
        control.placeholder = `e.g. ${lines[0]}${lines.length > 1 ? " …" : ""}`;
      }
    }

    function replaceField(wrap, field, bind, ctx) {
      const fresh = renderField(field, bind, ctx);
      wrap.replaceWith(fresh);
      runChecks(ctx);
      return fresh;
    }

    /* A value this control cannot edit: show it read-only and keep it. */
    function renderMismatch(field, bind, ctx, reason) {
      const f = frame(field, ctx, { group: true });
      f.wrap.classList.add("cc-span");
      const value = bind.get();
      const pre = el("pre", "cc-readonly", field.secret || field.credential ? "•••••• (hidden)" : displayValue(value));
      pre.setAttribute("aria-labelledby", f.labelId);
      const actions = el("div", "cc-inline-actions");
      const clear = button("btn-ghost btn-sm", "Clear value");
      on(clear, "click", () => {
        bind.set(undefined);
        ctx.onChange();
        replaceField(f.wrap, field, bind, ctx);
      });
      actions.append(clear);
      f.wrap.append(pre);
      appendHelp(f.wrap, field, pre, reason || "The form cannot edit this value's shape. It is kept as-is on Apply.");
      f.wrap.append(actions);
      return f.wrap;
    }

    function renderUnknown(field, bind, ctx) {
      const type = String(field.type || "");
      const f = frame(field, ctx, { group: true });
      const value = bind.get();
      const pre = el("pre", "cc-readonly", hasValue(value) ? displayValue(value) : "(not set)");
      f.wrap.append(pre);
      appendHelp(f.wrap, field, pre, `This editor does not know the field type "${type}". The value is kept as-is.`);
      return f.wrap;
    }

    function renderCredential(field, bind, ctx) {
      const f = frame(field, ctx);
      const value = bind.get();
      const sel = el("select");
      sel.id = f.id;
      const unset = el("option", "", "Unset");
      unset.value = "";
      sel.append(unset);
      const choices = CREDENTIAL_CHOICES.slice();
      if (field.placeholder === "{{password}}") {
        choices.reverse();
      }
      for (const [val, text] of choices) {
        const opt = el("option", "", text);
        opt.value = val;
        sel.append(opt);
      }
      const current = typeof value === "string" ? value.trim() : value;
      let literal = false;
      if (!hasValue(current)) {
        sel.value = "";
      } else if (choices.some(([val]) => val === current)) {
        sel.value = current;
      } else {
        literal = true;
        const opt = el("option", "", "Current value (hidden)");
        opt.value = "__literal__";
        sel.append(opt);
        sel.value = "__literal__";
      }
      const warn = el("p", "cc-help cc-help-warn", "Literal secrets are rejected on Apply. Pick a placeholder; the machine password fills it at deploy.");
      warn.hidden = !literal;
      on(sel, "change", () => {
        if (sel.value === "__literal__") {
          return;
        }
        bind.set(sel.value === "" ? undefined : sel.value);
        warn.hidden = true;
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, sel);
      f.wrap.append(sel);
      appendHelp(f.wrap, field, sel, "Filled from the machine password at deploy.");
      f.wrap.append(warn);
      return f.wrap;
    }

    function renderString(field, bind, ctx) {
      if (field.credential) {
        return renderCredential(field, bind, ctx);
      }
      const value = bind.get();
      if (!isScalar(value) && !isBlockMarker(value)) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx);
      const input = el("input");
      input.id = f.id;
      input.type = field.secret ? "password" : "text";
      input.autocomplete = field.secret ? "new-password" : "off";
      input.spellcheck = false;
      setPlaceholder(input, field);
      input.value = scalarText(value);
      on(input, "input", () => {
        bind.set(input.value === "" ? undefined : input.value);
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, input);
      if (field.secret) {
        const row = el("div", "cc-secret-row");
        const toggle = button("btn-ghost btn-sm", "Show");
        toggle.setAttribute("aria-pressed", "false");
        toggle.setAttribute("aria-label", `Show ${field.label || field.key}`);
        on(toggle, "click", () => {
          const show = input.type === "password";
          input.type = show ? "text" : "password";
          toggle.textContent = show ? "Hide" : "Show";
          toggle.setAttribute("aria-pressed", show ? "true" : "false");
        });
        row.append(input, toggle);
        f.wrap.append(row);
      } else {
        f.wrap.append(input);
      }
      appendHelp(f.wrap, field, input);
      return f.wrap;
    }

    function renderText(field, bind, ctx) {
      const value = bind.get();
      if (!isScalar(value)) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx);
      const ta = el("textarea", "seed-editor");
      ta.id = f.id;
      ta.spellcheck = false;
      ta.value = scalarText(value);
      autoRows(ta, 3, 14);
      setPlaceholder(ta, field);
      on(ta, "input", () => {
        bind.set(ta.value === "" ? undefined : ta.value);
        autoRows(ta, 3, 14);
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, ta);
      if (field.secret && hasValue(value)) {
        const masked = el("div", "cc-secret-mask");
        const lineCount = String(value).split("\n").length;
        masked.append(el("span", "cc-muted", `Set (${lineCount} ${lineCount === 1 ? "line" : "lines"}, hidden)`));
        const show = button("btn-ghost btn-sm", "Show and edit");
        on(show, "click", () => {
          masked.hidden = true;
          ta.hidden = false;
          ta.focus();
        });
        masked.append(show);
        ta.hidden = true;
        f.wrap.append(masked, ta);
      } else {
        f.wrap.append(ta);
      }
      appendHelp(f.wrap, field, ta);
      return f.wrap;
    }

    function segmented(name, options, current, labelledBy, onPick) {
      const seg = el("div", "cc-seg");
      seg.setAttribute("role", "radiogroup");
      if (labelledBy) {
        seg.setAttribute("aria-labelledby", labelledBy);
      }
      const radios = [];
      for (const [val, text] of options) {
        const lab = el("label", "cc-seg-opt");
        const radio = el("input");
        radio.type = "radio";
        radio.name = name;
        radio.value = val;
        radio.checked = val === current;
        on(radio, "change", () => {
          if (radio.checked) {
            onPick(val);
          }
        });
        lab.append(radio, el("span", "", text));
        seg.append(lab);
        radios.push(radio);
      }
      seg.radios = radios;
      return seg;
    }

    function renderBool(field, bind, ctx) {
      const value = bind.get();
      let current = "";
      if (value === true || value === "true") {
        current = "yes";
      } else if (value === false || value === "false") {
        current = "no";
      } else if (value !== null && value !== undefined && value !== "") {
        return renderMismatch(field, bind, ctx, "This value is not a plain yes/no. It is kept as-is on Apply.");
      }
      const f = frame(field, ctx, { group: true });
      const seg = segmented(
        uid("bool"),
        [
          ["", "Unset"],
          ["yes", "Yes"],
          ["no", "No"],
        ],
        current,
        f.labelId,
        (val) => {
          bind.set(val === "" ? undefined : val === "yes");
          ctx.onChange();
        },
      );
      f.wrap.append(seg);
      appendHelp(f.wrap, field, seg);
      return f.wrap;
    }

    function renderInt(field, bind, ctx) {
      const value = bind.get();
      if (!(value === null || value === undefined || value === "" || typeof value === "number" || (typeof value === "string" && /^-?\d+$/.test(value.trim())))) {
        return renderMismatch(field, bind, ctx, "This value is not a whole number. It is kept as-is on Apply.");
      }
      const f = frame(field, ctx);
      const input = el("input");
      input.id = f.id;
      input.type = "number";
      input.step = "1";
      input.inputMode = "numeric";
      setPlaceholder(input, field);
      input.value = value === null || value === undefined ? "" : String(value).trim();
      on(input, "input", () => {
        const text = input.value.trim();
        if (input.validity && input.validity.badInput) {
          input.classList.add("cc-invalid");
          return;
        }
        input.classList.remove("cc-invalid");
        if (text === "") {
          bind.set(undefined);
        } else if (/^-?\d+$/.test(text)) {
          bind.set(Number(text));
        } else {
          bind.set(text);
        }
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, input);
      f.wrap.append(input);
      appendHelp(f.wrap, field, input);
      return f.wrap;
    }

    function renderEnum(field, bind, ctx) {
      const value = bind.get();
      if (!isScalar(value)) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx);
      const sel = el("select");
      sel.id = f.id;
      const choices = (Array.isArray(field.choices) ? field.choices : []).map((choice) => (choice === null ? "" : String(choice)));
      if (!choices.length || choices[0] !== "") {
        choices.unshift("");
      }
      for (const choice of choices) {
        if (choice === "" && sel.options.length) {
          continue;
        }
        const hasDefault = field.default !== undefined && field.default !== null && field.default !== "";
        const text = choice === "" ? (hasDefault ? `Unset (default: ${formatDefault(field.default)})` : "Unset") : choice;
        const opt = el("option", "", text);
        opt.value = choice;
        sel.append(opt);
      }
      const current = value === null || value === undefined ? "" : String(value);
      if (!choices.includes(current)) {
        const opt = el("option", "", `${current} (not in list)`);
        opt.value = current;
        sel.append(opt);
      }
      sel.value = current;
      on(sel, "change", () => {
        bind.set(sel.value === "" ? undefined : sel.value);
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, sel);
      f.wrap.append(sel);
      const shown = Object.assign({}, field, { default: undefined });
      appendHelp(f.wrap, shown, sel);
      return f.wrap;
    }

    function renderFlex(field, bind, ctx) {
      const value = bind.get();
      if (!isScalar(value)) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx);
      const input = el("input");
      input.id = f.id;
      input.type = "text";
      input.autocomplete = "off";
      input.spellcheck = false;
      setPlaceholder(input, field);
      input.value = scalarText(value);
      f.wrap.append(input);
      const choices = Array.isArray(field.choices) ? field.choices.filter((choice) => choice !== "" && choice !== null) : [];
      if (choices.length) {
        const list = el("datalist");
        list.id = uid("dl");
        for (const choice of choices) {
          const opt = el("option");
          opt.value = String(choice);
          list.append(opt);
        }
        input.setAttribute("list", list.id);
        f.wrap.append(list);
      }
      on(input, "input", () => {
        bind.set(input.value.trim() === "" ? undefined : input.value);
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, input);
      appendHelp(f.wrap, field, input, choices.length ? `Suggestions: ${choices.map(String).join(", ")}. Numbers and true/false are typed.` : "Text, a number, or true/false.");
      return f.wrap;
    }

    function listText(value) {
      if (Array.isArray(value)) {
        return value.map((item) => scalarText(item)).join("\n");
      }
      if (typeof value === "string") {
        return value;
      }
      return "";
    }

    function renderStringList(field, bind, ctx, extraHelp) {
      const value = bind.get();
      const token = typeof field.block_token === "string" && field.block_token ? field.block_token : null;
      const blocked = Boolean(token && isBlockMarker(value, token));
      if (!blocked && value !== null && value !== undefined && !(typeof value === "string") && !(Array.isArray(value) && value.every(isScalar))) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx);
      const ta = el("textarea");
      ta.id = f.id;
      ta.spellcheck = false;
      ta.value = blocked ? "" : listText(value);
      autoRows(ta, 2, 12);
      setPlaceholder(ta, field);
      on(ta, "input", () => {
        const items = linesOf(ta.value);
        bind.set(items.length ? items : undefined);
        autoRows(ta, 2, 12);
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, ta);
      if (token) {
        const check = el("label", "check cc-check");
        const box = el("input");
        box.type = "checkbox";
        box.checked = blocked;
        check.append(box, document.createTextNode(` ${BLOCK_LABELS[token] || `Use the machine ${token} field`} `), el("code", "", `{{${token}}}`));
        ta.hidden = blocked;
        on(box, "change", () => {
          if (box.checked) {
            bind.set({ __pxe_block__: token });
            ta.hidden = true;
          } else {
            const items = linesOf(ta.value);
            bind.set(items.length ? items : undefined);
            ta.hidden = false;
            ta.focus();
          }
          ctx.onChange();
        });
        f.wrap.append(check);
      }
      f.wrap.append(ta);
      appendHelp(f.wrap, field, ta, extraHelp || "One per line.");
      if (token) {
        const lineHint = f.wrap.lastElementChild;
        const box = f.wrap.querySelector(".cc-check input");
        const syncHint = () => {
          if (lineHint && lineHint.classList.contains("cc-help")) {
            lineHint.hidden = Boolean(box && box.checked);
          }
        };
        syncHint();
        on(box, "change", syncHint);
      }
      return f.wrap;
    }

    function renderLines(field, bind, ctx) {
      let hint = "One per line.";
      if (field.parser === "groups") {
        hint = "One group per line. Write name: member1, member2 to add members.";
      } else if (field.parser === "options") {
        hint = "One key: value per line.";
      }
      const value = bind.get();
      if (Array.isArray(value) && !value.every(isScalar)) {
        return renderMismatch(field, bind, ctx);
      }
      return renderStringList(Object.assign({}, field, { block_token: null }), bind, ctx, hint);
    }

    function renderYamlValue(field, bind, ctx) {
      const value = bind.get();
      const f = frame(field, ctx);
      const ta = el("textarea", "seed-editor");
      ta.id = f.id;
      ta.spellcheck = false;
      ta.value = value === null || value === undefined ? "" : typeof value === "string" ? value : displayValue(value);
      autoRows(ta, 3, 16);
      setPlaceholder(ta, field);
      on(ta, "input", () => {
        bind.set(ta.value.trim() === "" ? undefined : ta.value);
        autoRows(ta, 3, 16);
        ctx.onChange();
      });
      addRequiredCheck(ctx, field, bind, ta);
      f.wrap.append(ta);
      let hint = "YAML.";
      if (field.yaml_type === "dict") {
        hint = "YAML mapping (key: value).";
      } else if (field.yaml_type === "list") {
        hint = "YAML list (- item).";
      }
      appendHelp(f.wrap, field, ta, hint);
      return f.wrap;
    }

    function renderDiskLayout(field, bind, ctx) {
      const value = bind.get();
      if (value !== null && value !== undefined && !isPlainObject(value)) {
        return renderMismatch(field, bind, ctx);
      }
      const current = isPlainObject(value) ? value : {};
      const f = frame(field, ctx);
      const sel = el("select");
      sel.id = f.id;
      for (const [val, text] of [
        ["", "Unset"],
        ["true", "One partition for the whole disk (true)"],
        ["false", "No partitions (false)"],
        ["remove", "Remove the partition table (remove)"],
        ["custom", "Custom partitions"],
      ]) {
        const opt = el("option", "", text);
        opt.value = val;
        sel.append(opt);
      }
      const modeValue = typeof current.mode === "string" ? current.mode : "";
      if (![...sel.options].some((opt) => opt.value === modeValue)) {
        const opt = el("option", "", `${modeValue} (not in list)`);
        opt.value = modeValue;
        sel.append(opt);
      }
      sel.value = modeValue;
      const ta = el("textarea", "seed-editor");
      ta.spellcheck = false;
      ta.setAttribute("aria-label", `${field.label || field.key} partitions`);
      ta.placeholder = "e.g.\n50\n100, 82";
      ta.value = Array.isArray(current.lines) ? current.lines.map(scalarText).join("\n") : "";
      autoRows(ta, 3, 10);
      ta.hidden = modeValue !== "custom";
      const hint = el("p", "cc-help", "One partition per line: a size in percent (50), or size, partition type (100, 82).");
      hint.hidden = ta.hidden;
      on(sel, "change", () => {
        ta.hidden = sel.value !== "custom";
        hint.hidden = ta.hidden;
        if (sel.value === "") {
          bind.set(undefined);
        } else {
          bind.set({ mode: sel.value, lines: linesOf(ta.value) });
        }
        ctx.onChange();
      });
      on(ta, "input", () => {
        bind.set({ mode: "custom", lines: linesOf(ta.value) });
        autoRows(ta, 3, 10);
        ctx.onChange();
      });
      f.wrap.append(sel, ta, hint);
      appendHelp(f.wrap, field, sel);
      return f.wrap;
    }

    function checkboxGroup(labelledBy, choices, selected, onToggle) {
      const box = el("div", "cc-checks");
      box.setAttribute("role", "group");
      if (labelledBy) {
        box.setAttribute("aria-labelledby", labelledBy);
      }
      const known = choices.map(String);
      const extras = selected.filter((item) => !known.includes(String(item)));
      const entries = known.map((choice) => [choice, choice]).concat(extras.map((item) => [String(item), `${item} (not in list)`]));
      for (const [val, text] of entries) {
        const lab = el("label", "check cc-check");
        const input = el("input");
        input.type = "checkbox";
        input.value = val;
        input.checked = selected.map(String).includes(val);
        on(input, "change", () => onToggle());
        lab.append(input, document.createTextNode(` ${text}`));
        box.append(lab);
      }
      box.values = () => Array.from(box.querySelectorAll("input:checked")).map((input) => input.value);
      return box;
    }

    function renderEnumList(field, bind, ctx) {
      const value = bind.get();
      if (value !== null && value !== undefined && !(Array.isArray(value) && value.every(isScalar))) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx, { group: true });
      const selected = Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined) : [];
      const choices = Array.isArray(field.choices) ? field.choices.filter((choice) => choice !== "" && choice !== null) : [];
      const group = checkboxGroup(f.labelId, choices, selected, () => {
        const picked = group.values();
        bind.set(picked.length ? picked : undefined);
        ctx.onChange();
      });
      f.wrap.append(group);
      appendHelp(f.wrap, field, group);
      return f.wrap;
    }

    function renderAllOrList(field, bind, ctx) {
      const value = bind.get();
      const isAll = value === "all" || (isPlainObject(value) && value.all === true);
      if (!isAll && value !== null && value !== undefined && !(Array.isArray(value) && value.every(isScalar))) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx, { group: true });
      const allLabel = el("label", "check cc-check");
      const allBox = el("input");
      allBox.type = "checkbox";
      allBox.checked = isAll;
      allLabel.append(allBox, document.createTextNode(" All"));
      const selected = Array.isArray(value) ? value : [];
      const choices = Array.isArray(field.choices) ? field.choices.filter((choice) => choice !== "" && choice !== null) : [];
      let listControl;
      const readList = () => (choices.length ? listControl.values() : linesOf(listControl.value));
      if (choices.length) {
        listControl = checkboxGroup(f.labelId, choices, selected, () => {
          const picked = readList();
          bind.set(picked.length ? picked : undefined);
          ctx.onChange();
        });
      } else {
        listControl = el("textarea");
        listControl.setAttribute("aria-labelledby", f.labelId);
        listControl.value = selected.map(scalarText).join("\n");
        autoRows(listControl, 3, 10);
        setPlaceholder(listControl, field);
        on(listControl, "input", () => {
          const picked = readList();
          bind.set(picked.length ? picked : undefined);
          ctx.onChange();
        });
      }
      listControl.hidden = isAll;
      on(allBox, "change", () => {
        if (allBox.checked) {
          bind.set({ all: true });
        } else {
          const picked = readList();
          bind.set(picked.length ? picked : undefined);
        }
        listControl.hidden = allBox.checked;
        ctx.onChange();
      });
      f.wrap.append(allLabel, listControl);
      appendHelp(f.wrap, field, listControl, choices.length ? "" : "Or one per line.");
      return f.wrap;
    }

    function renderStringOrList(field, bind, ctx) {
      const value = bind.get();
      const allowFalse = Boolean(field.allow_false);
      const isFalse = value === false;
      if (!isFalse && value !== null && value !== undefined && typeof value !== "string" && !(Array.isArray(value) && value.every(isScalar))) {
        return renderMismatch(field, bind, ctx);
      }
      if (isFalse && !allowFalse) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx);
      const ta = el("textarea");
      ta.id = f.id;
      ta.spellcheck = false;
      ta.value = Array.isArray(value) ? value.map(scalarText).join("\n") : typeof value === "string" ? value : "";
      autoRows(ta, 2, 10);
      setPlaceholder(ta, field);
      const options = el("div", "cc-inline-options");
      const listLabel = el("label", "check cc-check");
      const listBox = el("input");
      listBox.type = "checkbox";
      listBox.checked = Array.isArray(value);
      listLabel.append(listBox, document.createTextNode(" Write as a list"));
      options.append(listLabel);
      let falseBox = null;
      if (allowFalse) {
        const falseLabel = el("label", "check cc-check");
        falseBox = el("input");
        falseBox.type = "checkbox";
        falseBox.checked = isFalse;
        falseLabel.append(falseBox, document.createTextNode(" Set to false"));
        options.append(falseLabel);
      }
      const compute = () => {
        if (falseBox && falseBox.checked) {
          return false;
        }
        const items = linesOf(ta.value);
        if (!items.length) {
          return undefined;
        }
        if (listBox.checked || items.length > 1) {
          return items;
        }
        return items[0];
      };
      const sync = () => {
        const multi = linesOf(ta.value).length > 1;
        if (multi) {
          listBox.checked = true;
        }
        listBox.disabled = multi || Boolean(falseBox && falseBox.checked);
        ta.disabled = Boolean(falseBox && falseBox.checked);
      };
      sync();
      on(ta, "input", () => {
        sync();
        autoRows(ta, 2, 10);
        bind.set(compute());
        ctx.onChange();
      });
      on(listBox, "change", () => {
        bind.set(compute());
        ctx.onChange();
      });
      if (falseBox) {
        on(falseBox, "change", () => {
          sync();
          bind.set(compute());
          ctx.onChange();
        });
      }
      addRequiredCheck(ctx, field, bind, ta);
      f.wrap.append(ta, options);
      appendHelp(f.wrap, field, ta, "One value, or one per line for a list.");
      return f.wrap;
    }

    function listTools(index, length, noun, handlers) {
      const tools = el("div", "cc-card-tools");
      const up = iconButton("up", `Move ${noun} ${index + 1} up`);
      const down = iconButton("down", `Move ${noun} ${index + 1} down`);
      const remove = iconButton("remove", `Remove ${noun} ${index + 1}`);
      up.disabled = index === 0;
      down.disabled = index >= length - 1;
      up.dataset.tool = "up";
      down.dataset.tool = "down";
      remove.dataset.tool = "remove";
      remove.classList.add("cc-remove");
      on(up, "click", () => handlers.move(index, -1, "up"));
      on(down, "click", () => handlers.move(index, 1, "down"));
      on(remove, "click", () => handlers.remove(index));
      tools.append(up, down, remove);
      return tools;
    }

    /* Shared list mechanics for cards, commands, packages, and map entries. */
    function listController(bind, ctx, noun, draw, container, addBtn) {
      const rows = () => {
        const value = bind.get();
        return Array.isArray(value) ? value : [];
      };
      const focusIn = (index, selector) => {
        const items = container.querySelectorAll(":scope > .cc-item");
        const target = items[index];
        if (!target) {
          if (addBtn) {
            addBtn.focus();
          }
          return;
        }
        const focusable = (selector && target.querySelector(selector)) || target.querySelector("button, input, select, textarea");
        if (focusable) {
          if (focusable.disabled) {
            const alt = target.querySelector("[data-tool]:not(:disabled)");
            (alt || focusable).focus();
          } else {
            focusable.focus();
          }
        }
      };
      return {
        rows,
        move(index, delta, tool) {
          const list = rows();
          const next = index + delta;
          if (next < 0 || next >= list.length) {
            return;
          }
          [list[index], list[next]] = [list[next], list[index]];
          ctx.onChange();
          draw();
          focusIn(next, `[data-tool="${tool}"]`);
        },
        remove(index) {
          const list = rows();
          if (hasValue(list[index]) && !window.confirm(`Remove this ${noun}?`)) {
            return;
          }
          list.splice(index, 1);
          if (!list.length) {
            bind.set(undefined);
          }
          ctx.onChange();
          draw();
          focusIn(Math.min(index, rows().length - 1));
        },
        add(item) {
          let list = bind.get();
          if (!Array.isArray(list)) {
            list = [];
            bind.set(list);
          }
          list.push(item);
          draw();
          focusIn(list.length - 1, "input:not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea, select");
          ctx.onChange();
          return list.length - 1;
        },
      };
    }

    function listHead(field, f, countText) {
      const count = el("span", "cc-list-count", countText);
      f.head.append(count);
      return count;
    }

    function cardTitleKeys(field) {
      const items = Array.isArray(field.item_fields) ? field.item_fields : [];
      const keys = [];
      if (typeof field.summary_key === "string") {
        keys.push(field.summary_key);
      }
      if (typeof field.key_field === "string") {
        keys.push(field.key_field);
      }
      const required = items.find((item) => item.required && !item.secret && !item.credential);
      if (required) {
        keys.push(required.key);
      }
      const firstString = items.find((item) => (item.type === "string" || item.type === undefined) && !item.secret && !item.credential);
      if (firstString) {
        keys.push(firstString.key);
      }
      return keys.filter((key, index) => keys.indexOf(key) === index);
    }

    function renderRowList(field, bind, ctx) {
      const value = bind.get();
      if (value !== null && value !== undefined && !Array.isArray(value)) {
        return renderMismatch(field, bind, ctx, "The form expects a list here. The current value is kept as-is on Apply.");
      }
      const f = frame(field, ctx, { group: true });
      f.wrap.classList.add("cc-list");
      const itemFields = Array.isArray(field.item_fields) ? field.item_fields : [];
      const noun = typeof field.item_label === "string" && field.item_label ? field.item_label : singular(field.label || field.key);
      const tuple = field.emit === "tuple";
      const titleKeys = cardTitleKeys(field);
      const openRows = new WeakSet();
      const cards = el("ol", tuple ? "cc-tuple" : "cc-cards");
      cards.setAttribute("aria-labelledby", f.labelId);
      if (tuple) {
        cards.style.setProperty("--cc-cols", String(Math.max(1, itemFields.length)));
      }
      const addBtn = button("btn-ghost btn-sm cc-add", `+ Add ${noun}`);
      const count = listHead(field, f, "");
      let firstDraw = true;
      const controller = listController(bind, ctx, noun, draw, cards, addBtn);

      function titleFor(row, index) {
        if (!isPlainObject(row)) {
          return scalarText(row) || `${capitalize(noun)} ${index + 1}`;
        }
        for (const key of titleKeys) {
          if (hasValue(row[key])) {
            const text = scalarText(row[key]);
            return text.length > 60 ? `${text.slice(0, 57)}…` : text;
          }
        }
        return `New ${noun}`;
      }

      function tupleHeader() {
        const head = el("li", "cc-tuple-row cc-tuple-head");
        head.setAttribute("aria-hidden", "true");
        for (const item of itemFields) {
          head.append(el("span", "", item.label || item.key));
        }
        head.append(el("span", ""));
        return head;
      }

      function tupleRow(index, length) {
        const rowBind = indexBind(bind, index);
        const row = rowBind.get();
        const li = el("li", "cc-tuple-row cc-item");
        if (!isPlainObject(row)) {
          const pre = el("pre", "cc-readonly", displayValue(row));
          pre.style.gridColumn = `1 / span ${itemFields.length}`;
          li.append(pre);
        } else {
          for (const item of itemFields) {
            const cellBind = keyBind(rowBind, item.key);
            const cell = cellBind.get();
            const input = el("input");
            input.type = "text";
            input.spellcheck = false;
            input.autocomplete = "off";
            input.setAttribute("aria-label", `${item.label || item.key}, ${noun} ${index + 1}`);
            if (cell === null) {
              input.placeholder = "null";
            } else if (!isScalar(cell)) {
              input.value = JSON.stringify(cell);
              input.readOnly = true;
            } else {
              input.value = scalarText(cell);
            }
            on(input, "input", () => {
              cellBind.set(input.value === "" ? undefined : input.value);
              ctx.onChange();
            });
            li.append(input);
          }
        }
        li.append(listTools(index, length, noun, controller));
        return li;
      }

      function scalarCard(index, length) {
        const rowBind = indexBind(bind, index);
        const row = rowBind.get();
        const li = el("li", "cc-card cc-card-scalar cc-item");
        const head = el("div", "cc-card-head");
        if (isScalar(row)) {
          const input = el("input");
          input.type = "text";
          input.spellcheck = false;
          input.value = scalarText(row);
          input.setAttribute("aria-label", `${capitalize(noun)} ${index + 1}`);
          on(input, "input", () => {
            rowBind.set(input.value);
            ctx.onChange();
          });
          head.append(input);
        } else {
          head.append(el("pre", "cc-readonly", displayValue(row)));
        }
        head.append(listTools(index, length, noun, controller));
        li.append(head);
        return li;
      }

      function card(index, length) {
        const rowBind = indexBind(bind, index);
        const row = rowBind.get();
        if (!isPlainObject(row)) {
          return scalarCard(index, length);
        }
        const li = el("li", "cc-card cc-item");
        const head = el("div", "cc-card-head");
        const bodyId = uid("card");
        const toggle = button("cc-card-toggle");
        toggle.setAttribute("aria-controls", bodyId);
        const title = el("span", "cc-card-title");
        const sub = el("span", "cc-card-sub");
        const warn = el("span", "cc-card-warn", "Required field missing");
        warn.hidden = true;
        toggle.append(icon("chevron"), title, sub);
        head.append(toggle, warn, listTools(index, length, noun, controller));
        const body = el("div", "cc-card-body");
        body.id = bodyId;
        const setOpen = (open) => {
          body.hidden = !open;
          toggle.setAttribute("aria-expanded", open ? "true" : "false");
          if (open) {
            openRows.add(row);
          } else {
            openRows.delete(row);
          }
        };
        const cardCtx = childCtx(ctx, {
          depth: ctx.depth + 1,
          scope: rowBind,
          entryRule: typeof field.entry_rule === "string" ? field.entry_rule : null,
        });
        const refresh = () => {
          const current = rowBind.get();
          title.textContent = titleFor(current, index);
          title.classList.toggle("cc-muted", !titleKeys.some((key) => isPlainObject(current) && hasValue(current[key])));
          const setCount = isPlainObject(current) ? Object.keys(current).filter((key) => hasValue(current[key])).length : 0;
          sub.textContent = setCount ? `#${index + 1} · ${setCount} set` : `#${index + 1}`;
          warn.hidden = !runChecks(cardCtx);
        };
        cardCtx.onChange = () => {
          refresh();
          ctx.onChange();
        };
        renderFieldList(itemFields, rowBind, body, cardCtx);
        const known = new Set(itemFields.map((item) => item.key));
        const unknown = Object.keys(row).filter((key) => !known.has(key) && !key.startsWith("__") && hasValue(row[key]));
        if (unknown.length) {
          const note = el("p", "cc-help");
          note.append(document.createTextNode("Also kept as-is: "));
          unknown.forEach((key, i) => {
            if (i) {
              note.append(document.createTextNode(", "));
            }
            note.append(el("code", "", key));
          });
          body.append(note);
        }
        if (field.preserve_extra || hasValue(row.__extra__)) {
          body.append(renderExtraBox(rowBind, cardCtx));
        }
        on(toggle, "click", () => setOpen(body.hidden));
        expanders.set(body, () => setOpen(true));
        const startOpen = openRows.has(row) || (firstDraw && length === 1);
        setOpen(startOpen);
        refresh();
        li.append(head, body);
        return li;
      }

      function draw() {
        cards.replaceChildren();
        const list = controller.rows();
        if (tuple && list.length) {
          cards.append(tupleHeader());
        }
        list.forEach((row, index) => {
          cards.append(tuple ? tupleRow(index, list.length) : card(index, list.length));
        });
        if (!list.length) {
          cards.append(el("li", "cc-list-empty", `No ${noun} entries yet.`));
        }
        count.textContent = list.length ? `${list.length} ${list.length === 1 ? "item" : "items"}` : "";
        firstDraw = false;
      }

      on(addBtn, "click", () => {
        const row = {};
        openRows.add(row);
        controller.add(row);
      });
      draw();
      f.wrap.append(cards, addBtn);
      appendHelp(f.wrap, field, cards);
      return f.wrap;
    }

    function renderCommandList(field, bind, ctx) {
      const value = bind.get();
      if (value !== null && value !== undefined && !Array.isArray(value)) {
        return renderMismatch(field, bind, ctx, "The form expects a list of commands. The current value is kept as-is on Apply.");
      }
      const f = frame(field, ctx, { group: true });
      f.wrap.classList.add("cc-list");
      const list = el("ol", "cc-cmds");
      list.setAttribute("aria-labelledby", f.labelId);
      const addBtn = button("btn-ghost btn-sm cc-add", "+ Add command");
      const count = listHead(field, f, "");
      const controller = listController(bind, ctx, "command", draw, list, addBtn);

      function commandRow(index, length) {
        const itemBind = indexBind(bind, index);
        const item = itemBind.get();
        const li = el("li", "cc-cmd cc-item");
        li.append(el("span", "cc-cmd-num", String(index + 1)));
        const main = el("div", "cc-cmd-main");
        if (typeof item === "string" || (Array.isArray(item) && item.every(isScalar))) {
          const argv = Array.isArray(item);
          const ta = el("textarea", "seed-editor");
          ta.spellcheck = false;
          ta.setAttribute("aria-label", `Command ${index + 1}${argv ? " arguments, one per line" : ""}`);
          ta.value = argv ? item.map(scalarText).join("\n") : item;
          autoRows(ta, 1, 10, 0);
          if (!ta.value) {
            ta.placeholder = argv ? "one argument per line" : "e.g. echo hello";
          }
          const seg = segmented(
            uid("cmd"),
            [
              ["shell", "Shell"],
              ["argv", "Argv"],
            ],
            argv ? "argv" : "shell",
            null,
            (picked) => {
              const current = itemBind.get();
              if (picked === "argv" && typeof current === "string") {
                itemBind.set(shellSplit(current));
              } else if (picked === "shell" && Array.isArray(current)) {
                itemBind.set(current.map(shellQuote).join(" "));
              }
              ctx.onChange();
              draw();
              const again = list.querySelectorAll(":scope > .cc-item")[index];
              const radio = again && again.querySelector(`input[value="${picked}"]`);
              if (radio) {
                radio.focus();
              }
            },
          );
          seg.classList.add("cc-seg-sm");
          seg.setAttribute("aria-label", `Command ${index + 1} form`);
          on(ta, "input", () => {
            if (Array.isArray(itemBind.get())) {
              itemBind.set(ta.value.split("\n").filter((line) => line !== ""));
            } else {
              itemBind.set(ta.value);
            }
            autoRows(ta, 1, 10, 0);
            ctx.onChange();
          });
          main.append(ta, seg);
        } else {
          main.append(el("pre", "cc-readonly", displayValue(item)));
          main.append(el("p", "cc-help", "Kept as-is."));
        }
        li.append(main, listTools(index, length, "command", controller));
        return li;
      }

      function draw() {
        list.replaceChildren();
        const rows = controller.rows();
        rows.forEach((row, index) => list.append(commandRow(index, rows.length)));
        if (!rows.length) {
          list.append(el("li", "cc-list-empty", "No commands yet."));
        }
        count.textContent = rows.length ? `${rows.length} ${rows.length === 1 ? "command" : "commands"}` : "";
      }

      on(addBtn, "click", () => controller.add(""));
      draw();
      f.wrap.append(list, addBtn);
      appendHelp(f.wrap, field, list, "Shell runs the line through sh -c. Argv runs the program directly, one argument per line.");
      return f.wrap;
    }

    function renderMap(field, bind, ctx) {
      const value = bind.get();
      if (value !== null && value !== undefined && !Array.isArray(value)) {
        return renderMismatch(field, bind, ctx, "The form expects key/value entries. The current value is kept as-is on Apply.");
      }
      const f = frame(field, ctx, { group: true });
      f.wrap.classList.add("cc-list");
      const valueType = typeof field.value_type === "string" ? field.value_type : "string";
      const keyLabel = field.key_label || "Key";
      const valueLabel = field.value_label || "Value";
      const list = el("ol", "cc-map");
      list.setAttribute("aria-labelledby", f.labelId);
      const addBtn = button("btn-ghost btn-sm cc-add", "+ Add entry");
      const count = listHead(field, f, "");
      const controller = listController(bind, ctx, "entry", draw, list, addBtn);
      const keyInputs = [];

      function checkDuplicates() {
        const seen = new Map();
        for (const input of keyInputs) {
          const key = input.value.trim();
          if (!key) {
            continue;
          }
          seen.set(key, (seen.get(key) || 0) + 1);
        }
        for (const input of keyInputs) {
          const dup = (seen.get(input.value.trim()) || 0) > 1;
          input.classList.toggle("cc-invalid", dup);
          input.title = dup ? "Duplicate key" : "";
        }
      }

      function entryRow(index, length) {
        const entryBind = indexBind(bind, index);
        const entry = entryBind.get();
        const li = el("li", "cc-map-row cc-item");
        if (!isPlainObject(entry)) {
          li.append(el("pre", "cc-readonly", displayValue(entry)));
        } else {
          const keyInput = el("input");
          keyInput.type = "text";
          keyInput.spellcheck = false;
          keyInput.autocomplete = "off";
          keyInput.value = scalarText(entry.key);
          keyInput.placeholder = keyLabel;
          keyInput.setAttribute("aria-label", `${keyLabel} ${index + 1}`);
          keyInputs.push(keyInput);
          const valBind = keyBind(entryBind, "value");
          const current = entry.value;
          let valInput;
          if (valueType === "yaml") {
            valInput = el("textarea", "seed-editor");
            valInput.value = current === null || current === undefined ? "" : typeof current === "string" ? current : displayValue(current);
            autoRows(valInput, 1, 8, 0);
          } else {
            valInput = el("input");
            valInput.type = "text";
            valInput.autocomplete = "off";
            valInput.value = scalarText(current);
          }
          valInput.spellcheck = false;
          valInput.placeholder = valueLabel;
          valInput.setAttribute("aria-label", `${valueLabel} ${index + 1}`);
          on(keyInput, "input", () => {
            keyBind(entryBind, "key").set(keyInput.value);
            checkDuplicates();
            ctx.onChange();
          });
          on(valInput, "input", () => {
            valBind.set(valInput.value);
            if (valInput.tagName === "TEXTAREA") {
              autoRows(valInput, 1, 8, 0);
            }
            ctx.onChange();
          });
          li.append(keyInput, valInput);
        }
        const tools = el("div", "cc-card-tools");
        const remove = iconButton("remove", `Remove entry ${index + 1}`);
        remove.dataset.tool = "remove";
        remove.classList.add("cc-remove");
        on(remove, "click", () => controller.remove(index));
        tools.append(remove);
        li.append(tools);
        return li;
      }

      function draw() {
        list.replaceChildren();
        keyInputs.length = 0;
        const rows = controller.rows();
        if (rows.length) {
          const head = el("li", "cc-map-row cc-map-head");
          head.setAttribute("aria-hidden", "true");
          head.append(el("span", "", keyLabel), el("span", "", valueLabel), el("span", ""));
          list.append(head);
        }
        rows.forEach((row, index) => list.append(entryRow(index, rows.length)));
        if (!rows.length) {
          list.append(el("li", "cc-list-empty", "No entries yet."));
        }
        count.textContent = rows.length ? `${rows.length} ${rows.length === 1 ? "entry" : "entries"}` : "";
        checkDuplicates();
      }

      on(addBtn, "click", () => controller.add({ key: "", value: "" }));
      draw();
      f.wrap.append(list, addBtn);
      let hint = "";
      if (valueType === "yaml") {
        hint = "Values are YAML.";
      } else if (valueType === "scalar") {
        hint = "Values can be text, numbers, or true/false.";
      }
      appendHelp(f.wrap, field, list, hint);
      return f.wrap;
    }

    function renderPackageList(field, bind, ctx) {
      const value = bind.get();
      const token = typeof field.block_token === "string" && field.block_token ? field.block_token : null;
      const blocked = Boolean(token && isBlockMarker(value, token));
      if (!blocked && value !== null && value !== undefined && !Array.isArray(value)) {
        return renderMismatch(field, bind, ctx);
      }
      const f = frame(field, ctx, { group: true });
      f.wrap.classList.add("cc-list");
      const managers = Array.isArray(field.managers) && field.managers.length ? field.managers.map(String) : ["apt", "snap"];
      const list = el("ol", "cc-pkgs");
      list.setAttribute("aria-labelledby", f.labelId);
      const addBtn = button("btn-ghost btn-sm cc-add", "+ Add package");
      const count = listHead(field, f, "");
      const controller = listController(bind, ctx, "package", draw, list, addBtn);
      const body = el("div", "cc-pkg-body");

      function pkgRow(index, length) {
        const rowBind = indexBind(bind, index);
        const row = rowBind.get();
        const li = el("li", "cc-pkg-row cc-item");
        if (!isPlainObject(row)) {
          const pre = el("pre", "cc-readonly", displayValue(row));
          pre.style.gridColumn = "1 / span 3";
          li.append(pre);
        } else {
          const name = el("input");
          name.type = "text";
          name.spellcheck = false;
          name.autocomplete = "off";
          name.value = scalarText(row.name);
          name.placeholder = "package";
          name.setAttribute("aria-label", `Package ${index + 1} name`);
          const version = el("input");
          version.type = "text";
          version.spellcheck = false;
          version.autocomplete = "off";
          version.value = scalarText(row.version);
          version.placeholder = "any version";
          version.setAttribute("aria-label", `Package ${index + 1} version`);
          const manager = el("select");
          manager.setAttribute("aria-label", `Package ${index + 1} manager`);
          const def = el("option", "", "Default");
          def.value = "";
          manager.append(def);
          for (const item of managers) {
            const opt = el("option", "", item);
            opt.value = item;
            manager.append(opt);
          }
          const currentManager = row.manager === null || row.manager === undefined ? "" : String(row.manager);
          if (![...manager.options].some((opt) => opt.value === currentManager)) {
            const opt = el("option", "", currentManager);
            opt.value = currentManager;
            manager.append(opt);
          }
          manager.value = currentManager;
          const required = () => {
            const bad = !hasValue(name.value) && (hasValue(version.value) || manager.value !== "");
            name.classList.toggle("cc-invalid", bad);
          };
          on(name, "input", () => {
            keyBind(rowBind, "name").set(name.value === "" ? undefined : name.value);
            required();
            ctx.onChange();
          });
          on(version, "input", () => {
            keyBind(rowBind, "version").set(version.value === "" ? undefined : version.value);
            required();
            ctx.onChange();
          });
          on(manager, "change", () => {
            keyBind(rowBind, "manager").set(manager.value);
            required();
            ctx.onChange();
          });
          required();
          li.append(name, version, manager);
        }
        li.append(listTools(index, length, "package", controller));
        return li;
      }

      function draw() {
        list.replaceChildren();
        const rows = controller.rows();
        if (rows.length) {
          const head = el("li", "cc-pkg-row cc-map-head");
          head.setAttribute("aria-hidden", "true");
          head.append(el("span", "", "Package"), el("span", "", "Version"), el("span", "", "Manager"), el("span", ""));
          list.append(head);
        }
        rows.forEach((row, index) => list.append(pkgRow(index, rows.length)));
        if (!rows.length) {
          list.append(el("li", "cc-list-empty", "No packages yet."));
        }
        count.textContent = rows.length ? `${rows.length} ${rows.length === 1 ? "package" : "packages"}` : "";
      }

      const bulk = el("details", "cc-bulk");
      bulk.append(el("summary", "", "Add several"));
      const bulkBox = el("textarea");
      bulkBox.rows = 4;
      bulkBox.spellcheck = false;
      bulkBox.placeholder = "One package name per line";
      bulkBox.setAttribute("aria-label", "Package names, one per line");
      const bulkAdd = button("btn-secondary btn-sm", "Add packages");
      on(bulkAdd, "click", () => {
        const names = linesOf(bulkBox.value);
        if (!names.length) {
          return;
        }
        let rows = bind.get();
        if (!Array.isArray(rows)) {
          rows = [];
          bind.set(rows);
        }
        for (const item of names) {
          rows.push({ name: item, version: "", manager: "" });
        }
        bulkBox.value = "";
        bulk.open = false;
        draw();
        ctx.onChange();
      });
      bulk.append(bulkBox, bulkAdd);

      body.append(list, el("div", "cc-list-actions"));
      body.lastChild.append(addBtn, bulk);
      on(addBtn, "click", () => controller.add({ name: "", version: "", manager: "" }));

      if (token) {
        const check = el("label", "check cc-check");
        const box = el("input");
        box.type = "checkbox";
        box.checked = blocked;
        check.append(box, document.createTextNode(` ${BLOCK_LABELS[token] || `Use the machine ${token} field`} `), el("code", "", `{{${token}}}`));
        body.hidden = blocked;
        on(box, "change", () => {
          if (box.checked) {
            bind.set({ __pxe_block__: token });
          } else {
            bind.set(undefined);
            draw();
          }
          body.hidden = box.checked;
          ctx.onChange();
        });
        f.wrap.append(check);
      }
      if (!blocked) {
        draw();
      }
      f.wrap.append(body);
      appendHelp(f.wrap, field, list, "Leave the version empty for the newest. Manager picks apt or snap; Default uses the distro package manager.");
      return f.wrap;
    }

    function renderExtraBox(bind, ctx) {
      const extraBind = keyBind(bind, "__extra__");
      const value = extraBind.get();
      const box = el("details", "cc-extra");
      box.dataset.ccPath = extraBind.path.join(".");
      box.open = hasValue(value);
      const summary = el("summary", "", "Other keys (YAML)");
      box.append(summary);
      const ta = el("textarea", "seed-editor");
      ta.spellcheck = false;
      ta.value = typeof value === "string" ? value : hasValue(value) ? displayValue(value) : "";
      ta.setAttribute("aria-label", "Other keys (YAML)");
      ta.placeholder = "key: value";
      autoRows(ta, 3, 12);
      on(ta, "input", () => {
        extraBind.set(ta.value.trim() === "" ? undefined : ta.value);
        autoRows(ta, 3, 12);
        ctx.onChange();
      });
      box.append(ta, el("p", "cc-help", "Keys the form does not show. They are written as-is."));
      return box;
    }

    function renderObject(field, bind, ctx) {
      const value = bind.get();
      if (value !== null && value !== undefined && !isPlainObject(value)) {
        return renderMismatch(field, bind, ctx, "The form expects a mapping here. The current value is kept as-is on Apply.");
      }
      const box = el("details", "cc-object");
      box.open = ctx.depth === 0 || hasValue(value);
      const summary = el("summary");
      const titleText = el("span", "cc-object-title", field.label || field.key);
      summary.append(titleText);
      if (field.key && field.label && String(field.label) !== String(field.key)) {
        summary.append(el("code", "cc-key", field.key));
      }
      if (field.deprecated) {
        const chip = el("span", "cc-deprecated", "Deprecated");
        chip.title = typeof field.deprecated === "string" ? field.deprecated : "Deprecated";
        summary.append(chip);
      }
      const count = el("span", "cc-object-count");
      summary.append(count);
      box.append(summary);
      const body = el("div", "cc-object-body");
      const objCtx = childCtx(ctx, { depth: ctx.depth + 1, scope: bind, entryRule: null });
      const refresh = () => {
        const current = bind.get();
        const setCount = isPlainObject(current) ? Object.keys(current).filter((key) => hasValue(current[key])).length : 0;
        count.textContent = setCount ? `${setCount} set` : "";
        runChecks(objCtx);
      };
      objCtx.onChange = () => {
        refresh();
        ctx.onChange();
      };
      if (typeof field.help === "string" && field.help) {
        body.append(el("p", "cc-help", field.help));
      }
      if (typeof field.deprecated === "string" && field.deprecated) {
        body.append(el("p", "cc-help cc-help-warn", field.deprecated));
      }
      if (typeof field.require_subkey === "string" && field.require_subkey) {
        const sub = (field.object_fields || []).find((item) => item.key === field.require_subkey);
        body.append(el("p", "cc-help", `Only written when ${sub ? sub.label || sub.key : field.require_subkey} is set.`));
      }
      renderFieldList(field.object_fields || [], bind, body, objCtx);
      appendUnknownNote(body, field.object_fields || [], value);
      if (field.preserve_extra || (isPlainObject(value) && hasValue(value.__extra__))) {
        body.append(renderExtraBox(bind, objCtx));
      }
      box.append(body);
      refresh();
      return box;
    }

    function appendUnknownNote(container, fields, value) {
      if (!isPlainObject(value)) {
        return;
      }
      const known = new Set(fields.map((item) => item.key));
      const unknown = Object.keys(value).filter((key) => !known.has(key) && !key.startsWith("__") && hasValue(value[key]));
      if (!unknown.length) {
        return;
      }
      const note = el("p", "cc-help");
      note.append(document.createTextNode("Also kept as-is: "));
      unknown.forEach((key, i) => {
        if (i) {
          note.append(document.createTextNode(", "));
        }
        note.append(el("code", "", key));
      });
      container.append(note);
    }

    const RENDERERS = {
      string: renderString,
      text: renderText,
      bool: renderBool,
      int: renderInt,
      enum: renderEnum,
      flex: renderFlex,
      string_list: (field, bind, ctx) => renderStringList(field, bind, ctx),
      object: renderObject,
      row_list: renderRowList,
      yaml_value: renderYamlValue,
      disk_layout: renderDiskLayout,
      all_or_list: renderAllOrList,
      lines: renderLines,
      yaml: renderYamlValue,
      string_or_list: renderStringOrList,
      command_list: renderCommandList,
      map: renderMap,
      enum_list: renderEnumList,
      package_list: renderPackageList,
    };

    function renderField(field, bind, ctx) {
      const type = typeof field.type === "string" && field.type ? field.type : "string";
      const known = FIELD_TYPES.includes(type) && RENDERERS[type];
      const wrap = known ? RENDERERS[type](field, bind, ctx) : renderUnknown(field, bind, ctx);
      wrap.classList.add("cc-field");
      if (!known || WIDE_TYPES.has(type)) {
        wrap.classList.add("cc-span");
      }
      wrap.dataset.ccPath = bind.path.join(".");
      wrap.dataset.ccKey = schemaKey(bind.path);
      return wrap;
    }

    function fieldBind(field, parentBind, ctx) {
      if (field.type === "yaml" && ctx.node) {
        const holder = yamlHolder;
        return {
          path: ["__yaml__", ctx.node.id],
          get() {
            const blobs = holder.get();
            return isPlainObject(blobs) ? blobs[ctx.node.id] : undefined;
          },
          set(value) {
            let blobs = holder.get();
            if (!isPlainObject(blobs)) {
              blobs = {};
              holder.set(blobs);
            }
            if (value === undefined) {
              delete blobs[ctx.node.id];
            } else {
              blobs[ctx.node.id] = value;
            }
          },
        };
      }
      return keyBind(parentBind, field.key);
    }

    function renderFieldList(fields, parentBind, container, ctx) {
      const grid = el("div", "cc-grid");
      const visible = [];
      for (const field of fields || []) {
        if (!isPlainObject(field) || (typeof field.key !== "string" && field.type !== "yaml")) {
          continue;
        }
        const bind = fieldBind(field, parentBind, ctx);
        if (field.deprecated && !hasValue(bind.get()) && !ctx.pane.showDeprecated) {
          ctx.pane.hiddenDeprecated += 1;
          continue;
        }
        visible.push([field, bind]);
      }
      let moreGrid = null;
      let moreBox = null;
      let hiddenCount = 0;
      if (visible.length > MORE_THRESHOLD) {
        moreBox = el("details", "cc-more");
        moreGrid = el("div", "cc-grid");
      }
      visible.forEach(([field, bind], index) => {
        const promote = !moreBox || index < MORE_VISIBLE || hasValue(bind.get());
        const node = renderField(field, bind, ctx);
        if (promote) {
          grid.append(node);
        } else {
          hiddenCount += 1;
          moreGrid.append(node);
        }
      });
      container.append(grid);
      if (moreBox && hiddenCount) {
        moreBox.append(el("summary", "", `${hiddenCount} more ${hiddenCount === 1 ? "option" : "options"}`), moreGrid);
        container.append(moreBox);
      }
      runChecks(ctx);
    }

    /* ---------- panes ---------- */

    function paneHeading(text) {
      const heading = el("h3", "cc-pane-title", text);
      heading.tabIndex = -1;
      heading.id = uid("title");
      detail.setAttribute("aria-labelledby", heading.id);
      return heading;
    }

    function parkExtra() {
      if (extraBox && extraHome && extraBox.parentElement !== extraHome) {
        extraHome.append(extraBox);
      }
    }

    function renderOverview() {
      const pane = el("div", "cc-pane");
      const head = el("div", "cc-pane-head");
      const titleRow = el("div", "cc-pane-title-row");
      titleRow.append(paneHeading("Overview"));
      head.append(titleRow);
      pane.append(head);
      if (mode === "autoinstall") {
        pane.append(
          el(
            "p",
            "meta cc-pane-help",
            "This is an Ubuntu autoinstall file. The editor changes the cloud-config under autoinstall → user-data, which runs on the first boot of the installed system. The installer section stays as it is.",
          ),
        );
      } else {
        pane.append(el("p", "meta cc-pane-help", "This is a #cloud-config file. Every module below writes one or more top-level keys."));
      }
      if (notices.length) {
        const box = el("div", "alert alert-warn cc-notices");
        box.append(el("strong", "", notices.length === 1 ? "Notice" : "Notices"));
        const list = el("ul");
        for (const item of notices) {
          list.append(el("li", "", item));
        }
        box.append(list);
        pane.append(box);
      }
      const section = el("section", "cc-overview-section");
      section.append(el("h4", "", "Configured"));
      const chips = el("div", "cc-chips");
      const configured = nodes.filter((node) => nodeConfigured(node));
      if (extraText().trim()) {
        configured.push(pseudo[ADVANCED]);
      }
      for (const node of configured) {
        const chip = button("cc-chip-btn");
        chip.append(el("span", "cc-dot cc-dot-on"), el("span", "", node.label || node.id));
        if (node.group) {
          chip.append(el("span", "cc-chip-group", node.group));
        }
        on(chip, "click", () => showNode(node.id));
        chips.append(chip);
      }
      if (!configured.length) {
        section.append(el("p", "meta", "Nothing is set yet. Pick a module on the left, or search for a key."));
      } else {
        section.append(chips);
      }
      pane.append(section);
      if (mode === "autoinstall") {
        const inst = el("section", "cc-overview-section");
        inst.append(el("h4", "", "Installer section (read-only)"));
        if (installerKeys && installerKeys.length) {
          const keys = el("div", "cc-chips");
          for (const key of installerKeys) {
            keys.append(el("code", "cc-chip", key));
          }
          inst.append(keys);
        }
        const link = button("btn-ghost btn-sm", "About the installer section");
        on(link, "click", () => showNode(INSTALLER));
        inst.append(link);
        pane.append(inst);
      }
      const tips = el("section", "cc-overview-section");
      tips.append(el("h4", "", "Tips"));
      const list = el("ul", "cc-tips");
      const tip1 = el("li");
      tip1.append(el("kbd", "", "/"), document.createTextNode(" searches module names and YAML keys."));
      const tip2 = el("li");
      tip2.append(el("kbd", "", "↑"), document.createTextNode(" "), el("kbd", "", "↓"), document.createTextNode(" move through the module list; Enter opens one."));
      const tip3 = el("li", "", "Apply writes into the user-data box on the page. Save on the page keeps it.");
      list.append(tip1, tip2, tip3);
      tips.append(list);
      pane.append(tips);
      detail.append(pane);
      paneRefresh = null;
    }

    function renderAdvanced() {
      const pane = el("div", "cc-pane");
      const head = el("div", "cc-pane-head");
      const titleRow = el("div", "cc-pane-title-row");
      titleRow.append(paneHeading("Other keys (YAML)"));
      head.append(titleRow);
      pane.append(head);
      pane.append(
        el(
          "p",
          "meta cc-pane-help",
          "Top-level cloud-config keys this form does not model. They are written as-is. A key that has its own module (for example hostname) belongs in that module.",
        ),
      );
      if (mode === "autoinstall") {
        pane.append(el("p", "meta cc-pane-help", "This is not the autoinstall installer section; these keys go under user-data."));
      }
      if (extraBox) {
        const label = el("label", "cc-label", "YAML mapping");
        extraBox.id = extraBox.id || "cc-extra-yaml";
        label.htmlFor = extraBox.id;
        extraBox.spellcheck = false;
        autoRows(extraBox, 12, 30);
        pane.append(label, extraBox);
      }
      const hints = el("p", "cc-help cc-hint-keys");
      hints.append(document.createTextNode("Keys that usually live here: "));
      ADVANCED_HINT_KEYS.forEach((key, i) => {
        if (i) {
          hints.append(document.createTextNode(" "));
        }
        hints.append(el("code", "", key));
      });
      pane.append(hints);
      detail.append(pane);
      paneRefresh = null;
    }

    function renderInstaller() {
      const pane = el("div", "cc-pane");
      const head = el("div", "cc-pane-head");
      const titleRow = el("div", "cc-pane-title-row");
      titleRow.append(paneHeading("Installer (read-only)"));
      titleRow.append(el("span", "badge badge-disabled", "read-only"));
      head.append(titleRow);
      pane.append(head);
      pane.append(
        el(
          "p",
          "meta cc-pane-help",
          "These keys belong to the Ubuntu installer (subiquity autoinstall), not to cloud-init. The editor keeps them exactly as they are. Change them in the user-data box on the page.",
        ),
      );
      if (installerKeys && installerKeys.length) {
        const keys = el("div", "cc-chips");
        for (const key of installerKeys) {
          keys.append(el("code", "cc-chip", key));
        }
        pane.append(keys);
      } else {
        pane.append(el("p", "meta", "No installer keys found."));
      }
      detail.append(pane);
      paneRefresh = null;
    }

    function clearNode(node) {
      for (const field of node.fields || []) {
        if (!field) {
          continue;
        }
        if (field.type === "yaml") {
          delete state.__yaml__[node.id];
        } else if (typeof field.key === "string") {
          delete state[field.key];
        }
      }
    }

    async function insertExample(node, statusLine) {
      if (nodeConfigured(node) && !window.confirm(`Replace the current ${node.label || node.id} values with the example?`)) {
        return;
      }
      statusLine.textContent = "Loading example…";
      statusLine.hidden = false;
      try {
        const view = await postEditor({ action: "view", seed: `#cloud-config\n${node.example}` });
        const exampleDoc = isPlainObject(view.doc) ? view.doc : {};
        clearNode(node);
        for (const field of node.fields || []) {
          if (!field) {
            continue;
          }
          if (field.type === "yaml") {
            const blobs = isPlainObject(exampleDoc.__yaml__) ? exampleDoc.__yaml__ : {};
            if (blobs[node.id] !== undefined) {
              state.__yaml__[node.id] = blobs[node.id];
            }
          } else if (typeof field.key === "string" && exampleDoc[field.key] !== undefined) {
            state[field.key] = deepCopy(exampleDoc[field.key]);
          }
        }
        changed();
        showNode(node.id);
      } catch (err) {
        statusLine.textContent = err instanceof Error ? err.message : "Could not load the example";
      }
    }

    function exampleText(node) {
      if (typeof node.example === "string" && node.example.trim()) {
        return { text: node.example.replace(/\s+$/, ""), insertable: true };
      }
      const fields = (node.fields || []).filter((field) => field && field.type === "row_list" && typeof field.placeholder === "string" && field.placeholder.includes("\n"));
      if (!fields.length) {
        return null;
      }
      const text = fields
        .map((field) => `${field.key}:\n${field.placeholder.split("\n").map((line) => `  ${line}`).join("\n")}`)
        .join("\n");
      return { text, insertable: false };
    }

    function renderNodePane(node) {
      const pane = el("div", "cc-pane");
      const head = el("div", "cc-pane-head");
      const titleRow = el("div", "cc-pane-title-row");
      titleRow.append(paneHeading(node.label || node.id));
      if (typeof node.module === "string" && node.module) {
        titleRow.append(el("code", "cc-chip", node.module));
      }
      if (node.group) {
        titleRow.append(el("span", "cc-pane-group", node.group));
      }
      const actions = el("div", "cc-pane-actions");
      const tabs = el("div", "cc-tabs");
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "View");
      const formTab = button("cc-tab", "Form");
      const yamlTab = button("cc-tab", "YAML");
      formTab.setAttribute("role", "tab");
      yamlTab.setAttribute("role", "tab");
      formTab.id = uid("tab");
      yamlTab.id = uid("tab");
      tabs.append(formTab, yamlTab);
      actions.append(tabs);
      if (typeof node.doc_url === "string" && /^https:\/\//i.test(node.doc_url)) {
        const docs = el("a", "btn btn-ghost btn-sm cc-docs", "Docs");
        docs.href = node.doc_url;
        docs.target = "_blank";
        docs.rel = "noopener noreferrer";
        docs.setAttribute("aria-label", `${node.label || node.id} documentation (opens in a new tab)`);
        docs.append(icon("external"));
        actions.append(docs);
      }
      const clear = button("btn-ghost btn-sm cc-clear", "Clear");
      clear.title = "Remove every key this module writes";
      actions.append(clear);
      head.append(titleRow, actions);
      pane.append(head);

      if (typeof node.help === "string" && node.help) {
        pane.append(el("p", "meta cc-pane-help", node.help));
      }
      if (node.deprecated) {
        pane.append(el("p", "alert alert-warn", typeof node.deprecated === "string" ? node.deprecated : "This module is deprecated."));
      }
      const example = exampleText(node);
      if (example) {
        const box = el("details", "cc-example");
        box.append(el("summary", "", "Example"));
        box.append(el("pre", "cc-code", example.text));
        if (example.insertable && !disabled) {
          const row = el("div", "cc-inline-actions");
          const insert = button("btn-secondary btn-sm", "Insert example");
          const status = el("span", "cc-help");
          status.hidden = true;
          on(insert, "click", () => insertExample(node, status));
          row.append(insert, status);
          box.append(row);
        }
        pane.append(box);
      }

      const formPanel = el("div", "cc-form");
      formPanel.setAttribute("role", "tabpanel");
      formPanel.setAttribute("aria-labelledby", formTab.id);
      formPanel.id = uid("panel");
      const yamlPanel = el("div", "cc-node-yaml");
      yamlPanel.setAttribute("role", "tabpanel");
      yamlPanel.setAttribute("aria-labelledby", yamlTab.id);
      yamlPanel.id = uid("panel");
      yamlPanel.hidden = true;
      formTab.setAttribute("aria-controls", formPanel.id);
      yamlTab.setAttribute("aria-controls", yamlPanel.id);
      const yamlStatus = el("p", "cc-help", "");
      const yamlPre = el("pre", "cc-code cc-node-yaml-body", "");
      yamlPanel.append(yamlStatus, yamlPre);

      const paneState = { showDeprecated: false, hiddenDeprecated: 0 };
      const ctx = {
        node,
        depth: 0,
        scope: null,
        entryRule: null,
        pane: paneState,
        checks: [],
        onChange: () => {
          runChecks(ctx);
          changed();
        },
      };

      function drawForm() {
        formPanel.replaceChildren();
        paneState.hiddenDeprecated = 0;
        ctx.checks = [];
        const fields = Array.isArray(node.fields) ? node.fields : [];
        const only = fields.length === 1 ? fields[0] : null;
        if (only && only.type === "object" && typeof only.key === "string") {
          const bind = keyBind(rootBind, only.key);
          const value = bind.get();
          if (value === null || value === undefined || isPlainObject(value)) {
            ctx.scope = bind;
            if (typeof only.help === "string" && only.help && only.help !== node.help) {
              formPanel.append(el("p", "cc-help", only.help));
            }
            if (typeof only.require_subkey === "string" && only.require_subkey) {
              const sub = (only.object_fields || []).find((item) => item.key === only.require_subkey);
              formPanel.append(el("p", "cc-help", `Only written when ${sub ? sub.label || sub.key : only.require_subkey} is set.`));
            }
            renderFieldList(only.object_fields || [], bind, formPanel, ctx);
            appendUnknownNote(formPanel, only.object_fields || [], value);
            if (only.preserve_extra || (isPlainObject(value) && hasValue(value.__extra__))) {
              formPanel.append(renderExtraBox(bind, ctx));
            }
          } else {
            formPanel.append(renderField(only, bind, ctx));
          }
        } else {
          ctx.soloLabel = fields.length === 1 ? String(node.label || "") : null;
          renderFieldList(fields, rootBind, formPanel, ctx);
        }
        if (paneState.hiddenDeprecated) {
          const row = el("p", "cc-help cc-deprecated-toggle");
          row.append(
            document.createTextNode(
              `${paneState.hiddenDeprecated} deprecated ${paneState.hiddenDeprecated === 1 ? "option is" : "options are"} hidden. `,
            ),
          );
          const show = button("cc-linkbtn", "Show");
          on(show, "click", () => {
            paneState.showDeprecated = true;
            drawForm();
          });
          row.append(show);
          formPanel.append(row);
        } else if (paneState.showDeprecated) {
          const row = el("p", "cc-help cc-deprecated-toggle");
          const hide = button("cc-linkbtn", "Hide unset deprecated options");
          on(hide, "click", () => {
            paneState.showDeprecated = false;
            drawForm();
          });
          row.append(hide);
          formPanel.append(row);
        }
        if (disabled) {
          formPanel.querySelectorAll("input, select, textarea, button").forEach((control) => {
            control.disabled = true;
          });
        }
      }

      function selectTab(which) {
        const yaml = which === "yaml";
        formTab.setAttribute("aria-selected", yaml ? "false" : "true");
        yamlTab.setAttribute("aria-selected", yaml ? "true" : "false");
        formTab.tabIndex = yaml ? -1 : 0;
        yamlTab.tabIndex = yaml ? 0 : -1;
        formPanel.hidden = yaml;
        yamlPanel.hidden = !yaml;
        if (yaml) {
          nodeYamlView = {
            render(errorMessage) {
              if (errorMessage) {
                yamlStatus.textContent = errorMessage;
                yamlStatus.className = "cc-help cc-help-err";
                return;
              }
              const map = lastPreview && isPlainObject(lastPreview.node_yaml) ? lastPreview.node_yaml : null;
              if (!map) {
                yamlStatus.className = "cc-help";
                yamlStatus.textContent = lastPreview
                  ? "This server does not return per-module YAML. Use Preview for the whole file."
                  : "Loading…";
                yamlPre.hidden = true;
                return;
              }
              const text = typeof map[node.id] === "string" ? map[node.id].replace(/\s+$/, "") : "";
              yamlStatus.className = "cc-help";
              yamlStatus.textContent = "Read-only. This is what Apply writes for this module.";
              yamlPre.hidden = false;
              yamlPre.textContent = text || "# Nothing set for this module.";
            },
          };
          nodeYamlView.render(null);
          refreshPreview();
        } else {
          nodeYamlView = null;
        }
      }
      on(formTab, "click", () => selectTab("form"));
      on(yamlTab, "click", () => selectTab("yaml"));
      on(tabs, "keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
          return;
        }
        event.preventDefault();
        const next = document.activeElement === formTab ? yamlTab : formTab;
        next.focus();
        next.click();
      });
      on(clear, "click", () => {
        if (!nodeConfigured(node)) {
          return;
        }
        if (!window.confirm(`Clear every ${node.label || node.id} setting?`)) {
          return;
        }
        clearNode(node);
        changed();
        drawForm();
      });
      clear.disabled = disabled;

      drawForm();
      selectTab("form");
      pane.append(formPanel, yamlPanel);
      detail.append(pane);
      paneRefresh = () => {
        clear.disabled = disabled || !nodeConfigured(node);
      };
      paneRefresh();
    }

    function reveal(path, byKey) {
      if (!path) {
        return;
      }
      const parts = Array.isArray(path) ? path.map(String) : String(path).split(".");
      const attr = byKey ? "data-cc-key" : "data-cc-path";
      let target = null;
      for (let n = parts.length; n > 0 && !target; n -= 1) {
        const wanted = parts.slice(0, n).join(".");
        target = Array.from(detail.querySelectorAll(`[${attr}]`)).find((node) => node.getAttribute(attr) === wanted) || null;
      }
      if (!target) {
        return;
      }
      for (let node = target; node && node !== detail; node = node.parentElement) {
        if (node.tagName === "DETAILS") {
          node.open = true;
        }
        if (expanders.has(node)) {
          expanders.get(node)();
        }
      }
      target.scrollIntoView({ block: "center" });
      target.classList.remove("cc-flash");
      void target.offsetWidth;
      target.classList.add("cc-flash");
      const control = target.querySelector("input:not([type=hidden]), select, textarea, button");
      if (control) {
        control.focus({ preventScroll: true });
      }
    }

    function showNode(id, showOpts) {
      const so = showOpts || {};
      const node = nodeFor(id);
      if (!node) {
        return;
      }
      parkExtra();
      currentId = node.id;
      nodeYamlView = null;
      paneRefresh = null;
      navRefs.forEach((ref, refId) => {
        if (refId === currentId) {
          ref.btn.setAttribute("aria-current", "true");
        } else {
          ref.btn.removeAttribute("aria-current");
        }
      });
      const activeRef = navRefs.get(currentId);
      if (activeRef) {
        const group = activeRef.btn.closest(".cc-group");
        if (group && !group.open) {
          group.open = true;
        }
      }
      if (navSelect) {
        refreshSelect();
      }
      syncTabStops();
      detail.replaceChildren();
      detail.scrollTop = 0;
      if (node.id === OVERVIEW) {
        renderOverview();
      } else if (node.id === ADVANCED) {
        renderAdvanced();
      } else if (node.id === INSTALLER) {
        renderInstaller();
      } else {
        renderNodePane(node);
      }
      if (activeRef && typeof activeRef.btn.scrollIntoView === "function") {
        activeRef.btn.scrollIntoView({ block: "nearest" });
      }
      if (so.path) {
        reveal(so.path, so.byKey !== false && !so.exact);
      } else if (so.focus !== false) {
        const heading = detail.querySelector(".cc-pane-title");
        if (heading) {
          heading.focus({ preventScroll: true });
        }
      }
    }

    /* Jump to a node from a server error: {node?, path?}. */
    function revealError(nodeId, path) {
      let target = typeof nodeId === "string" && nodeFor(nodeId) ? nodeId : null;
      /* Paths arrive as ["users", 0, "name"] or "users[0].name". */
      const parts = Array.isArray(path)
        ? path.map(String)
        : typeof path === "string" && path
          ? path
              .replace(/\[(\d+)\]/g, ".$1")
              .split(".")
              .filter((part) => part !== "")
          : [];
      if (parts[0] === ADVANCED) {
        showNode(ADVANCED, { focus: false });
        if (extraBox) {
          extraBox.focus();
        }
        return true;
      }
      if (!target && parts.length && keyOwner.has(parts[0])) {
        target = keyOwner.get(parts[0]);
      }
      if (!target && typeof nodeId === "string" && keyOwner.has(nodeId)) {
        target = keyOwner.get(nodeId);
      }
      if (!target) {
        return false;
      }
      showNode(target, parts.length ? { path: parts, exact: true, byKey: false } : {});
      return true;
    }

    buildNav();
    refreshNavState();
    applyFilter();
    refreshFooter();
    showNode(OVERVIEW, { focus: false });

    return {
      state,
      isDirty,
      markClean() {
        baseline = snapshot();
        refreshFooter();
      },
      focusStart() {
        const heading = detail.querySelector(".cc-pane-title");
        if (heading) {
          heading.focus({ preventScroll: true });
        }
      },
      focusSearch() {
        if (search && search.offsetParent !== null) {
          search.focus();
          search.select();
        }
      },
      setPreviewOpen,
      revealError,
      destroy() {
        window.clearTimeout(previewTimer);
        previewSeq += 1;
        parkExtra();
        ac.abort();
      },
    };
  }

  /* ---------- dialog wiring ---------- */

  function editorError(dialog, message) {
    const alert = dialog.querySelector("[data-cc-error]");
    if (!alert) {
      return;
    }
    alert.hidden = !message;
    alert.textContent = message || "";
  }

  function mountEditor(root, view, extras) {
    const docEl = root.querySelector("script.cc-doc");
    if (docEl) {
      docEl.textContent = JSON.stringify((view && view.doc) || {});
    }
    const extra = root.querySelector("[data-cc-extra]");
    if (extra) {
      extra.value = (view && typeof view.extra_yaml === "string" && view.extra_yaml) || "";
    }
    const previous = editors.get(root);
    if (previous) {
      previous.destroy();
    }
    root.dataset.ccReady = "1";
    const editor = createEditor(root, {
      mode: view && typeof view.mode === "string" ? view.mode : root.dataset.mode || "",
      notices: view && view.notices,
      installerKeys: view && view.installer_keys,
      seedText: extras && extras.seedText,
      getSeed: extras && extras.getSeed,
      statusEl: extras && extras.statusEl,
    });
    if (editor) {
      editors.set(root, editor);
    } else {
      editors.delete(root);
    }
    return editor;
  }

  function unmountEditor(root, message) {
    const previous = editors.get(root);
    if (previous) {
      previous.destroy();
      editors.delete(root);
    }
    root.querySelectorAll("[data-cc-state]").forEach((node) => node.remove());
    const nav = root.querySelector(".cc-nav");
    const detail = root.querySelector(".cc-detail");
    if (nav) {
      nav.replaceChildren();
    }
    if (detail) {
      detail.replaceChildren();
      const pane = el("div", "cc-pane cc-pane-empty");
      pane.append(el("h3", "cc-pane-title", "The editor could not open this file"));
      pane.append(el("p", "meta", message || "Fix the user-data box on the page, then open the editor again."));
      detail.append(pane);
    }
  }

  async function postEditor(body) {
    const response = await fetch("/api/cloud-init/editor", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      let message = "Could not update the seed";
      let node = null;
      let path = null;
      const detail = payload ? payload.detail : null;
      if (typeof detail === "string") {
        message = detail;
      } else if (isPlainObject(detail)) {
        if (typeof detail.message === "string") {
          message = detail.message;
        } else if (typeof detail.detail === "string") {
          message = detail.detail;
        }
        node = typeof detail.node === "string" ? detail.node : null;
        path = detail.path !== undefined ? detail.path : null;
      }
      if (payload && typeof payload.node === "string") {
        node = payload.node;
      }
      if (payload && payload.path !== undefined && payload.path !== null) {
        path = payload.path;
      }
      if (response.status === 401) {
        message = "Your session ended. Reload the page and sign in again.";
      }
      const err = new Error(message);
      err.status = response.status;
      err.node = node;
      err.path = path;
      throw err;
    }
    return payload || {};
  }

  function wireDialog(dialog) {
    if (!dialog || dialog.dataset.ccWired) {
      return;
    }
    dialog.dataset.ccWired = "1";
    const root = dialog.querySelector("[data-cc-editor]");
    const statusEl = dialog.querySelector("[data-cc-status]");
    const modeBadge = dialog.querySelector("[data-cc-mode]");
    const previewToggle = dialog.querySelector("[data-cc-preview-toggle]");
    const applyBtn = dialog.querySelector("[data-cc-apply]");
    const seedEl = () => document.querySelector("[data-cc-seed]");

    function setMode(mode) {
      if (!modeBadge) {
        return;
      }
      if (mode === "autoinstall") {
        modeBadge.textContent = "autoinstall user-data";
        modeBadge.hidden = false;
      } else if (mode === "cloud-config") {
        modeBadge.textContent = "cloud-config";
        modeBadge.hidden = false;
      } else {
        modeBadge.hidden = true;
      }
    }

    function syncPreviewToggle() {
      if (previewToggle) {
        previewToggle.setAttribute("aria-pressed", previewWanted ? "true" : "false");
        previewToggle.classList.toggle("is-on", previewWanted);
      }
    }

    function requestClose() {
      const editor = root ? editors.get(root) : null;
      if (editor && editor.isDirty() && !window.confirm("Discard your changes in the cloud-init editor?")) {
        return;
      }
      dialog.close();
    }

    dialog.addEventListener("keydown", (event) => {
      if (event.defaultPrevented) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        requestClose();
        return;
      }
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const editor = root ? editors.get(root) : null;
        if (editor) {
          event.preventDefault();
          editor.focusSearch();
        }
      }
    });
    dialog.addEventListener("cancel", (event) => {
      const editor = root ? editors.get(root) : null;
      if (editor && editor.isDirty()) {
        event.preventDefault();
        requestClose();
      }
    });
    let downOutside = false;
    const outside = (event) => {
      const rect = dialog.getBoundingClientRect();
      return (
        event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom
      );
    };
    dialog.addEventListener("pointerdown", (event) => {
      downOutside = event.target === dialog && outside(event);
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog && downOutside && outside(event)) {
        requestClose();
      }
      downOutside = false;
    });
    dialog.querySelectorAll("[data-cc-cancel], [data-cc-close]").forEach((btn) => {
      btn.addEventListener("click", () => requestClose());
    });
    if (previewToggle) {
      previewToggle.addEventListener("click", () => {
        previewWanted = !previewWanted;
        syncPreviewToggle();
        const editor = root ? editors.get(root) : null;
        if (editor) {
          editor.setPreviewOpen(previewWanted);
        }
      });
    }
    dialog.addEventListener("close", () => {
      editorError(dialog, "");
    });

    dialog.ccOpen = async (openerBtn) => {
      const seed = seedEl();
      if (!seed || !root || typeof dialog.showModal !== "function") {
        return;
      }
      if (openerBtn) {
        openerBtn.disabled = true;
      }
      editorError(dialog, "");
      try {
        const view = await postEditor({ action: "view", seed: seed.value });
        const editor = mountEditor(root, view, {
          seedText: seed.value,
          getSeed: () => seedEl()?.value || "",
          statusEl,
        });
        setMode(view && view.mode);
        if (applyBtn) {
          applyBtn.disabled = !editor;
        }
        if (editor) {
          editor.setPreviewOpen(previewWanted);
        }
        syncPreviewToggle();
        if (!dialog.open) {
          dialog.showModal();
        }
        if (editor) {
          editor.focusStart();
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Could not open the editor";
        unmountEditor(root, "Fix the user-data box on the page, then open the editor again.");
        editorError(dialog, message);
        setMode("");
        if (statusEl) {
          statusEl.textContent = "";
        }
        if (applyBtn) {
          applyBtn.disabled = true;
        }
        if (!dialog.open) {
          dialog.showModal();
        }
      } finally {
        if (openerBtn) {
          openerBtn.disabled = false;
        }
      }
    };

    if (applyBtn) {
      applyBtn.addEventListener("click", async () => {
        const seed = seedEl();
        const stateInput = root ? root.querySelector("[data-cc-state]") : null;
        const extra = root ? root.querySelector("[data-cc-extra]") : null;
        if (!seed || !stateInput) {
          return;
        }
        applyBtn.disabled = true;
        editorError(dialog, "");
        try {
          const result = await postEditor({
            action: "apply",
            seed: seed.value,
            cc_json: stateInput.value,
            cc_extra_yaml: extra ? extra.value : "",
          });
          seed.value = result.seed || "";
          seed.dispatchEvent(new Event("input", { bubbles: true }));
          seed.dispatchEvent(new Event("change", { bubbles: true }));
          const editor = editors.get(root);
          if (editor) {
            editor.markClean();
          }
          dialog.close();
        } catch (err) {
          editorError(dialog, err instanceof Error ? err.message : "Could not apply the editor");
          const editor = editors.get(root);
          if (editor && err) {
            editor.revealError(err.node, err.path);
          }
        } finally {
          applyBtn.disabled = false;
        }
      });
    }
  }

  function wireEditorDialogs() {
    document.querySelectorAll("dialog.cc-editor-dialog").forEach((dialog) => wireDialog(dialog));
    document.querySelectorAll("[data-cc-open]").forEach((btn) => {
      if (btn.dataset.ccOpenWired) {
        return;
      }
      btn.dataset.ccOpenWired = "1";
      btn.addEventListener("click", () => {
        const dialog = document.getElementById(btn.getAttribute("data-cc-dialog") || "");
        if (!dialog) {
          return;
        }
        wireDialog(dialog);
        if (typeof dialog.ccOpen === "function") {
          dialog.ccOpen(btn);
        }
      });
    });
  }

  function boot() {
    document.querySelectorAll("[data-cc-editor]").forEach((root) => {
      if (root.dataset.ccReady) {
        return;
      }
      root.dataset.ccReady = "1";
      const dialog = root.closest("dialog");
      const editor = createEditor(root, {
        mode: root.dataset.mode || "",
        statusEl: dialog ? dialog.querySelector("[data-cc-status]") : null,
        getSeed: () => document.querySelector("[data-cc-seed]")?.value || "",
      });
      if (editor) {
        editors.set(root, editor);
      }
    });
    wireEditorDialogs();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

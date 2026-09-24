/* Cloud-init node editor.
   Schema-driven editor for the cloud-config mapping inside a Linux seed.
   Reads cc_schema / cc_view.doc from JSON data islands in cloudinit_editor.html,
   keeps a deep-copied state, and posts it as the hidden cc_json field. */
(() => {
  const BLOCK_CHECKBOX_LABELS = {
    ssh_keys: "Use the machine SSH keys field",
    packages: "Use the machine packages field",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function deepCopy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function getPath(obj, key) {
    let cur = obj;
    for (const part of key.split(".")) {
      if (cur === null || cur === undefined || typeof cur !== "object") {
        return undefined;
      }
      cur = cur[part];
    }
    return cur;
  }

  function setPath(obj, key, value) {
    const parts = key.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const part = parts[i];
      if (cur[part] === null || cur[part] === undefined || typeof cur[part] !== "object") {
        cur[part] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      }
      cur = cur[part];
    }
    cur[parts[parts.length - 1]] = value;
  }

  /* Empty means null, "", [], {}, or a whitespace-only string.
     false and 0 count as set; a __pxe_block__ marker counts as set. */
  function isEmptyValue(value) {
    if (value === null || value === undefined) {
      return true;
    }
    if (typeof value === "string") {
      return value.trim() === "";
    }
    if (Array.isArray(value)) {
      return value.length === 0;
    }
    if (typeof value === "object") {
      if (Object.prototype.hasOwnProperty.call(value, "__pxe_block__")) {
        return false;
      }
      return Object.keys(value).length === 0;
    }
    return false;
  }

  function isBlockToken(value, token) {
    return (
      value !== null &&
      value !== undefined &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      value.__pxe_block__ === token
    );
  }

  function nodeIsSet(node, state) {
    for (const field of node.fields || []) {
      if (field.type === "yaml") {
        continue;
      }
      if (!isEmptyValue(getPath(state, field.key))) {
        return true;
      }
    }
    const yamlText = state.__yaml__ ? state.__yaml__[node.id] : "";
    return typeof yamlText === "string" && yamlText.trim() !== "";
  }

  function initCloudInitEditor(root) {
    const schemaEl = root.querySelector("script.cc-schema");
    const docEl = root.querySelector("script.cc-doc");
    if (!schemaEl || !docEl) {
      return;
    }
    let schema;
    let doc;
    try {
      schema = JSON.parse(schemaEl.textContent || "null");
      doc = JSON.parse(docEl.textContent || "null");
    } catch {
      return;
    }
    if (!Array.isArray(schema)) {
      return;
    }
    const state = doc && typeof doc === "object" && !Array.isArray(doc) ? deepCopy(doc) : {};
    if (!state.__yaml__ || typeof state.__yaml__ !== "object" || Array.isArray(state.__yaml__)) {
      state.__yaml__ = {};
    }
    const disabled = root.getAttribute("data-disabled") === "1";
    const nav = root.querySelector(".cc-nav");
    const detail = root.querySelector(".cc-detail");
    if (!nav || !detail) {
      return;
    }

    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.setAttribute("data-cc-state", "");
    hidden.value = JSON.stringify(state);
    root.append(hidden);

    const nodeButtons = new Map();
    let currentNode = null;

    function sync() {
      hidden.value = JSON.stringify(state);
      nodeButtons.forEach((ref) => {
        ref.setSpan.hidden = !nodeIsSet(ref.node, state);
      });
    }

    function wrapLabel(field, control) {
      const label = el("label");
      const title = field.label || field.key;
      label.append(document.createTextNode(field.required ? `${title} *` : title));
      label.append(control);
      return label;
    }

    function textInput(field, path) {
      const input = el("input");
      input.type = field.secret ? "password" : "text";
      if (field.placeholder) {
        input.placeholder = field.placeholder;
      }
      const value = getPath(state, path);
      input.value = value === null || value === undefined ? "" : String(value);
      if (disabled) {
        input.disabled = true;
      }
      input.addEventListener("input", () => {
        if (field.type === "int") {
          const trimmed = input.value.trim();
          if (trimmed === "") {
            setPath(state, path, "");
          } else {
            const num = Number(trimmed);
            setPath(state, path, Number.isInteger(num) ? num : trimmed);
          }
        } else {
          setPath(state, path, input.value);
        }
        sync();
      });
      return wrapLabel(field, input);
    }

    function plainTextarea(field, path) {
      const ta = el("textarea", field.type === "text" ? "seed-editor" : "");
      ta.rows = field.type === "text" || field.type === "yaml_value" ? 5 : 4;
      if (field.placeholder) {
        ta.placeholder = field.placeholder;
      }
      const value = getPath(state, path);
      ta.value = value === null || value === undefined ? "" : String(value);
      if (disabled) {
        ta.disabled = true;
      }
      ta.addEventListener("input", () => {
        setPath(state, path, ta.value);
        sync();
      });
      return wrapLabel(field, ta);
    }

    function listTextarea(field, path) {
      const value = getPath(state, path);
      const ta = el("textarea");
      ta.rows = 5;
      if (field.placeholder) {
        ta.placeholder = field.placeholder;
      }
      ta.value = Array.isArray(value) ? value.join("\n") : "";
      if (disabled) {
        ta.disabled = true;
      }
      ta.addEventListener("input", () => {
        const items = ta.value
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line !== "");
        setPath(state, path, items);
        sync();
      });
      return wrapLabel(field, ta);
    }

    function blockListField(field, path) {
      const token = field.block_token;
      const blocked = isBlockToken(getPath(state, path), token);
      const wrap = el("div", "cc-blockfield");
      const checkLabel = el("label", "check");
      const box = el("input");
      box.type = "checkbox";
      box.checked = blocked;
      if (disabled) {
        box.disabled = true;
      }
      checkLabel.append(box, document.createTextNode(BLOCK_CHECKBOX_LABELS[token] || `Use the machine ${token} field`));
      const listLabel = listTextarea(field, path);
      listLabel.hidden = blocked;
      if (!disabled) {
        box.addEventListener("change", () => {
          if (box.checked) {
            setPath(state, path, { __pxe_block__: token });
            listLabel.hidden = true;
          } else {
            setPath(state, path, []);
            listLabel.hidden = false;
          }
          sync();
        });
      }
      wrap.append(checkLabel, listLabel);
      return wrap;
    }

    function boolSelect(field, path) {
      const sel = el("select");
      for (const [value, label] of [
        ["", "Unset"],
        ["true", "True"],
        ["false", "False"],
      ]) {
        const opt = el("option", "", label);
        opt.value = value;
        sel.append(opt);
      }
      const value = getPath(state, path);
      sel.value = value === true ? "true" : value === false ? "false" : "";
      if (disabled) {
        sel.disabled = true;
      }
      sel.addEventListener("change", () => {
        setPath(state, path, sel.value === "" ? null : sel.value === "true");
        sync();
      });
      return wrapLabel(field, sel);
    }

    function enumSelect(field, path) {
      const sel = el("select");
      const choices = Array.isArray(field.choices) ? field.choices.slice() : [];
      if (choices.length === 0 || choices[0] !== "") {
        choices.unshift("");
      }
      for (const choice of choices) {
        const label =
          choice === ""
            ? field.default !== undefined && field.default !== null && field.default !== ""
              ? `Unset (default: ${field.default})`
              : "Unset"
            : choice === "true"
              ? "True"
              : choice === "false"
                ? "False"
                : choice;
        const opt = el("option", "", label);
        opt.value = choice;
        sel.append(opt);
      }
      const raw = getPath(state, path);
      const current =
        raw === null || raw === undefined || raw === ""
          ? ""
          : raw === true
            ? "true"
            : raw === false
              ? "false"
              : String(raw);
      if (!choices.includes(current)) {
        const opt = el("option", "", current);
        opt.value = current;
        sel.append(opt);
      }
      sel.value = current;
      if (disabled) {
        sel.disabled = true;
      }
      sel.addEventListener("change", () => {
        setPath(state, path, sel.value);
        sync();
      });
      return wrapLabel(field, sel);
    }

    function yamlTextarea(node, field) {
      const ta = el("textarea", "seed-editor");
      ta.rows = 10;
      const value = state.__yaml__[node.id];
      ta.value = typeof value === "string" ? value : "";
      if (disabled) {
        ta.disabled = true;
      }
      ta.addEventListener("input", () => {
        state.__yaml__[node.id] = ta.value;
        sync();
      });
      return wrapLabel(field, ta);
    }

    function newRow(field) {
      const row = {};
      for (const item of field.item_fields || []) {
        row[item.key] = item.type === "bool" ? null : "";
      }
      return row;
    }

    function renderRow(node, field, path, index) {
      const fs = el("fieldset", "cc-row");
      const head = el("div", "cc-row-head");
      head.append(el("span", "cc-row-title", `Row ${index + 1}`));
      const removeBtn = el("button", "btn-ghost btn-sm", "Remove");
      removeBtn.type = "button";
      if (disabled) {
        removeBtn.disabled = true;
      } else {
        removeBtn.addEventListener("click", () => {
          const list = getPath(state, path);
          if (Array.isArray(list)) {
            list.splice(index, 1);
            sync();
            renderDetail(currentNode);
          }
        });
      }
      head.append(removeBtn);
      fs.append(head);
      for (const item of field.item_fields || []) {
        fs.append(renderField(node, item, `${path}.${index}.${item.key}`));
      }
      return fs;
    }

    function yamlScalar(value) {
      if (value && typeof value === "object" && value.__pxe_block__) {
        return `{{${value.__pxe_block__}}}`;
      }
      if (value === null || value === undefined) {
        return "null";
      }
      if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
      }
      const text = String(value);
      if (text === "" || /[:#{}[\],&*!|>'"%@`]|^\s|\s$/.test(text) || text.includes("\n")) {
        return JSON.stringify(text);
      }
      return text;
    }

    function dumpYamlList(rows) {
      if (!Array.isArray(rows) || rows.length === 0) {
        return "";
      }
      return rows
        .map((row) => {
          if (row === null || typeof row !== "object" || Array.isArray(row)) {
            return `- ${yamlScalar(row)}`;
          }
          const keys = Object.keys(row).filter((key) => row[key] !== "" && row[key] !== null && row[key] !== undefined);
          if (keys.length === 0) {
            return "- {}";
          }
          const lines = keys.map((key, index) => {
            const rendered = Array.isArray(row[key]) ? row[key].map((item) => yamlScalar(item)).join(", ") : yamlScalar(row[key]);
            return `${index === 0 ? "- " : "  "}${key}: ${rendered}`;
          });
          return lines.join("\n");
        })
        .join("\n");
    }

    function parseYamlList(text) {
      const rows = [];
      let current = null;
      for (const raw of text.split("\n")) {
        if (!raw.trim()) {
          continue;
        }
        const item = raw.match(/^-\s*(.*)$/);
        if (item) {
          if (current) {
            rows.push(current);
          }
          const rest = item[1];
          if (!rest || rest === "{}") {
            current = {};
            continue;
          }
          const keyed = rest.match(/^([^:]+):\s*(.*)$/);
          if (!keyed) {
            current = parseScalar(rest);
            rows.push(current);
            current = null;
            continue;
          }
          current = {};
          current[keyed[1].trim()] = parseScalar(keyed[2]);
          continue;
        }
        const nested = raw.match(/^\s+([^:]+):\s*(.*)$/);
        if (nested && current && typeof current === "object" && !Array.isArray(current)) {
          current[nested[1].trim()] = parseScalar(nested[2]);
          continue;
        }
        throw new Error("List items must start with '-'");
      }
      if (current) {
        rows.push(current);
      }
      return rows;
    }

    function parseScalar(text) {
      const trimmed = text.trim();
      if (trimmed === "true") {
        return true;
      }
      if (trimmed === "false") {
        return false;
      }
      if (trimmed === "null" || trimmed === "") {
        return "";
      }
      if (/^-?\d+$/.test(trimmed)) {
        return Number(trimmed);
      }
      const block = trimmed.match(/^\{\{([a-z_]+)\}\}$/);
      if (block && (block[1] === "ssh_keys" || block[1] === "packages")) {
        return { __pxe_block__: block[1] };
      }
      if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
        return trimmed.slice(1, -1);
      }
      return trimmed;
    }

    function renderDiskLayout(field, path) {
      const wrap = el("fieldset", "cc-row");
      const current = getPath(state, path) || {};
      const sel = el("select");
      for (const [value, label] of [
        ["", "Unset"],
        ["true", "One partition"],
        ["false", "No partitions"],
        ["remove", "Remove table"],
        ["custom", "Custom"],
      ]) {
        const opt = el("option", "", label);
        opt.value = value;
        sel.append(opt);
      }
      sel.value = current.mode || "";
      const lines = el("textarea");
      lines.rows = 4;
      lines.placeholder = "50\n100, 82";
      lines.value = Array.isArray(current.lines) ? current.lines.join("\n") : "";
      lines.hidden = sel.value !== "custom";
      if (disabled) {
        sel.disabled = true;
        lines.disabled = true;
      } else {
        sel.addEventListener("change", () => {
          lines.hidden = sel.value !== "custom";
          setPath(state, path, { mode: sel.value, lines: lines.value.split("\n") });
          sync();
        });
        lines.addEventListener("input", () => {
          setPath(state, path, { mode: "custom", lines: lines.value.split("\n") });
          sync();
        });
      }
      wrap.append(wrapLabel(field, sel), lines);
      return wrap;
    }

    function renderAllOrList(node, field, path) {
      const wrap = el("div");
      const current = getPath(state, path);
      const all = current && typeof current === "object" && current.all === true;
      const checkLabel = el("label", "check");
      const box = el("input");
      box.type = "checkbox";
      box.checked = all;
      checkLabel.append(box, document.createTextNode(" Post all fields"));
      const list = listTextarea(field, path);
      list.hidden = all;
      if (!disabled) {
        box.addEventListener("change", () => {
          if (box.checked) {
            setPath(state, path, { all: true });
            list.hidden = true;
          } else {
            setPath(state, path, []);
            list.hidden = false;
          }
          sync();
          renderDetail(currentNode);
        });
      }
      wrap.append(checkLabel, list);
      return wrap;
    }

    function renderRowList(node, field, path) {
      const wrap = el("div", "cc-rowlist");
      wrap.append(el("span", "cc-rowlist-label", field.label || field.key));
      const form = el("fieldset", "cc-row");
      const ordered = (field.item_fields || []).slice().sort((a, b) => Number(Boolean(b.required)) - Number(Boolean(a.required)));
      for (const item of ordered) {
        form.append(renderField(node, item, `__draft__.${path}.${item.key}`));
      }
      const error = el("p", "alert alert-error", "");
      error.hidden = true;
      const addBtn = el("button", "btn-ghost btn-sm", "Add");
      addBtn.type = "button";
      const box = el("textarea", "seed-editor");
      box.rows = 8;
      const current = getPath(state, path);
      box.value = typeof current === "string" ? current : dumpYamlList(Array.isArray(current) ? current : []);
      if (!box.value && field.placeholder) {
        box.placeholder = field.placeholder;
      }
      if (disabled) {
        addBtn.disabled = true;
        box.disabled = true;
      } else {
        addBtn.addEventListener("click", () => {
          let list = getPath(state, path);
          if (typeof list === "string") {
            try {
              list = parseYamlList(list);
            } catch (err) {
              error.hidden = false;
              error.textContent = err instanceof Error ? err.message : "Invalid list";
              return;
            }
          }
          if (!Array.isArray(list)) {
            list = [];
          }
          const row = {};
          for (const item of field.item_fields || []) {
            const value = getPath(state, `__draft__.${path}.${item.key}`);
            if (!isEmptyValue(value)) {
              row[item.key] = value;
            }
          }
          const missing = (field.item_fields || []).filter((item) => item.required && isEmptyValue(row[item.key]));
          if (field.entry_rule === "chpasswd") {
            if (isEmptyValue(row.name)) {
              missing.push({ label: "Name" });
            }
            if (row.type !== "RANDOM" && isEmptyValue(row.password) && isEmptyValue(row.name) === false) {
              missing.push({ label: "Password" });
            }
          }
          if (missing.length) {
            error.hidden = false;
            error.textContent = `Required: ${missing.map((item) => item.label || item.key).join(", ")}`;
            return;
          }
          error.hidden = true;
          list.push(row);
          setPath(state, path, list);
          for (const item of field.item_fields || []) {
            setPath(state, `__draft__.${path}.${item.key}`, item.type === "bool" ? null : "");
          }
          sync();
          renderDetail(currentNode);
        });
        box.addEventListener("input", () => {
          try {
            const parsed = box.value.trim() ? parseYamlList(box.value) : [];
            setPath(state, path, parsed);
            error.hidden = true;
          } catch (err) {
            setPath(state, path, box.value);
            error.hidden = false;
            error.textContent = err instanceof Error ? err.message : "Invalid list";
          }
          sync();
        });
      }
      wrap.append(form, error, addBtn, box);
      return wrap;
    }

    function renderObject(node, field, path) {
      const wrap = el("fieldset", "cc-row");
      wrap.append(el("span", "cc-rowlist-label", field.label || field.key));
      for (const sub of field.object_fields || []) {
        wrap.append(renderField(node, sub, `${path}.${sub.key}`));
      }
      const extra = getPath(state, `${path}.__extra__`);
      if (typeof extra === "string" && extra.trim()) {
        const ta = el("textarea", "seed-editor");
        ta.rows = 4;
        ta.value = extra;
        ta.addEventListener("input", () => {
          setPath(state, `${path}.__extra__`, ta.value);
          sync();
        });
        wrap.append(wrapLabel({ key: "__extra__", label: "Additional keys" }, ta));
      }
      return wrap;
    }

    function renderField(node, field, path) {
      const type = field.type || "string";
      if (type === "object") {
        return renderObject(node, field, path);
      }
      if (type === "row_list") {
        return renderRowList(node, field, path);
      }
      if (type === "disk_layout") {
        return renderDiskLayout(field, path);
      }
      if (type === "all_or_list") {
        return renderAllOrList(node, field, path);
      }
      if (type === "yaml_value") {
        return plainTextarea(field, path);
      }
      if (type === "flex") {
        return textInput(field, path);
      }
      if (type === "yaml") {
        return yamlTextarea(node, field);
      }
      if (type === "bool") {
        return boolSelect(field, path);
      }
      if (type === "enum") {
        return enumSelect(field, path);
      }
      if (type === "string_list" && field.block_token) {
        return blockListField(field, path);
      }
      if (type === "string_list" || type === "lines") {
        return listTextarea(field, path);
      }
      if (type === "text") {
        return plainTextarea(field, path);
      }
      return textInput(field, path);
    }

    function renderDetail(node) {
      if (!node) {
        return;
      }
      currentNode = node;
      detail.replaceChildren();
      detail.append(el("h3", "cc-detail-title", node.label || node.id));
      if (node.help) {
        detail.append(el("p", "meta", node.help));
      }
      const fields = el("div", "cc-fields");
      for (const field of node.fields || []) {
        fields.append(renderField(node, field, field.key));
      }
      detail.append(fields);
    }

    function showNode(node) {
      nodeButtons.forEach((ref, id) => {
        const active = id === node.id;
        ref.btn.classList.toggle("is-active", active);
        ref.btn.setAttribute("aria-selected", active ? "true" : "false");
      });
      renderDetail(node);
    }

    let lastGroup = null;
    for (const node of schema) {
      if (!node || typeof node !== "object") {
        continue;
      }
      if (node.group !== lastGroup) {
        nav.append(el("h3", "cc-group", node.group || ""));
        lastGroup = node.group;
      }
      const btn = el("button", "cc-node");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", "false");
      btn.append(el("span", "cc-node-name", node.label || node.id));
      const setSpan = el("span", "cc-set", "set");
      setSpan.hidden = true;
      btn.append(setSpan);
      btn.addEventListener("click", () => showNode(node));
      nav.append(btn);
      nodeButtons.set(node.id, { btn, setSpan, node });
    }

    sync();
    const initial =
      schema.find((node) => node && typeof node === "object" && nodeIsSet(node, state)) ||
      schema.find((node) => node && node.id === "hostname") ||
      schema[0];
    if (initial) {
      showNode(initial);
    }
  }

  function editorError(dialog, message) {
    const alert = dialog.querySelector("[data-cc-error]");
    if (!alert) {
      return;
    }
    alert.hidden = !message;
    alert.textContent = message || "";
  }

  function mountEditor(root, doc, extraYaml) {
    const docEl = root.querySelector("script.cc-doc");
    if (docEl) {
      docEl.textContent = JSON.stringify(doc || {});
    }
    const extra = root.querySelector("[data-cc-extra]");
    if (extra) {
      extra.value = extraYaml || "";
    }
    const nav = root.querySelector(".cc-nav");
    const detail = root.querySelector(".cc-detail");
    if (nav) {
      nav.replaceChildren();
    }
    if (detail) {
      detail.replaceChildren();
    }
    root.querySelector("[data-cc-state]")?.remove();
    delete root.dataset.ccReady;
    root.dataset.ccReady = "1";
    initCloudInitEditor(root);
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
      const detail = payload.detail;
      const message = typeof detail === "string" ? detail : "Could not update the seed";
      throw new Error(message);
    }
    return payload;
  }

  function wireEditorDialogs() {
    document.querySelectorAll("[data-cc-open]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const dialog = document.getElementById(btn.getAttribute("data-cc-dialog") || "");
        const seed = document.querySelector("[data-cc-seed]");
        const root = dialog ? dialog.querySelector("[data-cc-editor]") : null;
        if (!dialog || !seed || !root || typeof dialog.showModal !== "function") {
          return;
        }
        const applyBtn = dialog.querySelector("[data-cc-apply]");
        btn.disabled = true;
        editorError(dialog, "");
        try {
          const view = await postEditor({ action: "view", seed: seed.value });
          mountEditor(root, view.doc, view.extra_yaml);
          if (applyBtn) {
            applyBtn.disabled = false;
          }
          dialog.showModal();
        } catch (err) {
          editorError(dialog, err instanceof Error ? err.message : "Could not open the editor");
          if (applyBtn) {
            applyBtn.disabled = true;
          }
          dialog.showModal();
        } finally {
          btn.disabled = false;
        }
      });
    });
    document.querySelectorAll("[data-cc-apply]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const dialog = btn.closest("dialog");
        const seed = document.querySelector("[data-cc-seed]");
        const root = dialog ? dialog.querySelector("[data-cc-editor]") : null;
        const stateInput = root ? root.querySelector("[data-cc-state]") : null;
        const extra = root ? root.querySelector("[data-cc-extra]") : null;
        if (!dialog || !seed || !stateInput) {
          return;
        }
        btn.disabled = true;
        editorError(dialog, "");
        try {
          const result = await postEditor({
            action: "apply",
            seed: seed.value,
            cc_json: stateInput.value,
            cc_extra_yaml: extra ? extra.value : "",
          });
          seed.value = result.seed || "";
          dialog.close();
        } catch (err) {
          editorError(dialog, err instanceof Error ? err.message : "Could not apply the editor");
          btn.disabled = false;
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
      initCloudInitEditor(root);
    });
    wireEditorDialogs();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

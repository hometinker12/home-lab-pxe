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

    const extraYaml = root.querySelector('textarea[name="cc_extra_yaml"]');
    const formId = extraYaml ? extraYaml.getAttribute("form") : null;
    const form = (extraYaml && extraYaml.form) || root.closest("form");

    /* Value first, then the name, so a mid-init submit never posts a blank cc_json. */
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.value = JSON.stringify(state);
    if (formId) {
      hidden.setAttribute("form", formId);
    }
    hidden.name = "cc_json";
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
      label.append(document.createTextNode(field.label || field.key));
      label.append(control);
      return label;
    }

    function textInput(field, path) {
      const input = el("input");
      input.type = "text";
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
      ta.rows = field.type === "text" ? 5 : 4;
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
        const label = choice === "" ? "Unset" : choice === "true" ? "True" : choice === "false" ? "False" : choice;
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

    function renderRowList(node, field, path) {
      const wrap = el("div", "cc-rowlist");
      wrap.append(el("span", "cc-rowlist-label", field.label || field.key));
      const value = getPath(state, path);
      const rows = Array.isArray(value) ? value : [];
      rows.forEach((row, index) => {
        wrap.append(renderRow(node, field, path, index));
      });
      const addBtn = el("button", "btn-ghost btn-sm", "Add row");
      addBtn.type = "button";
      if (disabled) {
        addBtn.disabled = true;
      } else {
        addBtn.addEventListener("click", () => {
          let list = getPath(state, path);
          if (!Array.isArray(list)) {
            list = [];
            setPath(state, path, list);
          }
          list.push(newRow(field));
          sync();
          renderDetail(currentNode);
        });
      }
      wrap.append(addBtn);
      return wrap;
    }

    function renderObject(node, field, path) {
      const wrap = el("fieldset", "cc-row");
      wrap.append(el("span", "cc-rowlist-label", field.label || field.key));
      for (const sub of field.object_fields || []) {
        wrap.append(renderField(node, sub, `${path}.${sub.key}`));
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
    if (form) {
      form.addEventListener("submit", () => {
        hidden.value = JSON.stringify(state);
      });
    }
  }

  function boot() {
    document.querySelectorAll("[data-cc-editor]").forEach((root) => {
      if (root.dataset.ccReady) {
        return;
      }
      root.dataset.ccReady = "1";
      initCloudInitEditor(root);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

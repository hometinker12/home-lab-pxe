(() => {
  function syncImageForm(form) {
    const select = form.querySelector("[name='os_family']");
    if (!select) {
      return;
    }
    const family = select.value;
    form.querySelectorAll("[data-os]").forEach((el) => {
      const allowed = (el.getAttribute("data-os") || "").split(",").map((part) => part.trim());
      el.hidden = !allowed.includes(family);
    });
    const folder = form.querySelector("[name='folder_id']");
    if (folder && !folder.dataset.userPicked) {
      const mapped = form.getAttribute(`data-folder-${family}`);
      if (mapped && [...folder.options].some((opt) => opt.value === mapped)) {
        folder.value = mapped;
      }
    }
  }

  function syncDhcpForm(form) {
    const select = form.querySelector("[name='mode']");
    if (!select) {
      return;
    }
    const mode = select.value;
    form.querySelectorAll("[data-dhcp-mode]").forEach((el) => {
      el.hidden = el.getAttribute("data-dhcp-mode") !== mode;
    });
  }

  document.querySelectorAll("form.image-form").forEach((form) => {
    const select = form.querySelector("[name='os_family']");
    if (!select) {
      return;
    }
    select.addEventListener("change", () => syncImageForm(form));
    const folder = form.querySelector("[name='folder_id']");
    if (folder) {
      folder.addEventListener("change", () => {
        folder.dataset.userPicked = "1";
      });
    }
    syncImageForm(form);
    const sourceSelect = form.querySelector("select[name='source_id']");
    const wimIndex = form.querySelector("[name='wim_index']");
    if (sourceSelect && wimIndex) {
      sourceSelect.addEventListener("change", () => {
        const selected = sourceSelect.options[sourceSelect.selectedIndex];
        const index = selected && selected.getAttribute("data-wim-index");
        if (index) {
          wimIndex.value = index;
        }
      });
    }
    const isoDisplay = form.querySelector("[data-iso-path-display]");
    const isoFile = form.querySelector("input[name='iso_file']");
    if (isoDisplay && isoFile) {
      isoFile.addEventListener("change", () => {
        if (isoFile.files && isoFile.files[0]) {
          isoDisplay.value = isoFile.files[0].name;
        }
      });
    }
    form.addEventListener("submit", (event) => {
      const iso = form.querySelector("input[name='iso_file']");
      if (!iso || !iso.files || !iso.files.length) {
        return;
      }
      event.preventDefault();
      uploadIsoWithProgress(form);
    });
  });

  document.querySelectorAll("form.dhcp-form").forEach((form) => {
    const select = form.querySelector("[name='mode']");
    if (!select) {
      return;
    }
    select.addEventListener("change", () => syncDhcpForm(form));
    syncDhcpForm(form);
  });

  function enclosingDialog(form) {
    return form.closest("dialog");
  }

  function setImageFormBusy(form, busy) {
    form.querySelectorAll("button").forEach((btn) => {
      btn.disabled = busy;
    });
  }

  function reopenImageDialog(dlg) {
    if (dlg && typeof dlg.showModal === "function") {
      showModalPreservingScroll(dlg);
    }
  }

  function failIsoUpload(form, dlg, message) {
    hideUploadOverlay();
    setImageFormBusy(form, false);
    reopenImageDialog(dlg);
    window.alert(message);
  }

  function uploadOverlay() {
    let overlay = document.querySelector("dialog.upload-overlay");
    if (overlay) {
      return overlay;
    }
    overlay = document.createElement("dialog");
    overlay.className = "upload-overlay";
    overlay.setAttribute("role", "alertdialog");
    overlay.setAttribute("aria-live", "polite");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML =
      '<div class="upload-status"><p data-upload-label>Uploading ISO…</p><progress data-upload-bar max="100" value="0"></progress></div>';
    overlay.addEventListener("cancel", (event) => {
      event.preventDefault();
    });
    document.body.append(overlay);
    return overlay;
  }

  function setUploadStatus(percent, label) {
    const overlay = uploadOverlay();
    if (typeof overlay.showModal === "function") {
      if (!overlay.open) {
        overlay.showModal();
      }
    } else {
      overlay.hidden = false;
    }
    document.body.setAttribute("aria-busy", "true");
    const text = overlay.querySelector("[data-upload-label]");
    const bar = overlay.querySelector("[data-upload-bar]");
    if (text) {
      text.textContent = label;
    }
    if (bar) {
      if (percent == null) {
        bar.removeAttribute("value");
      } else {
        bar.max = 100;
        bar.value = String(percent);
      }
    }
  }

  function hideUploadOverlay() {
    const overlay = document.querySelector("dialog.upload-overlay");
    if (overlay) {
      if (typeof overlay.close === "function" && overlay.open) {
        overlay.close();
      }
      overlay.hidden = true;
    }
    document.body.removeAttribute("aria-busy");
  }

  function uploadIsoWithProgress(form) {
    const payload = new FormData(form);
    const dlg = enclosingDialog(form);
    const xhr = new XMLHttpRequest();
    const action = form.getAttribute("action") || window.location.pathname;
    xhr.open((form.getAttribute("method") || "POST").toUpperCase(), action);
    xhr.withCredentials = true;
    setImageFormBusy(form, true);
    if (dlg && typeof dlg.close === "function" && dlg.open) {
      dlg.close();
    }
    setUploadStatus(0, "Uploading ISO… 0%");
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && event.total > 0) {
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
        setUploadStatus(percent, `Uploading ISO… ${percent}%`);
        return;
      }
      setUploadStatus(null, "Uploading ISO…");
    });
    xhr.upload.addEventListener("load", () => {
      setUploadStatus(100, "Starting extraction…");
    });
    xhr.addEventListener("error", () => {
      failIsoUpload(form, dlg, "ISO upload failed. Check the connection and try again.");
    });
    xhr.addEventListener("abort", () => {
      hideUploadOverlay();
      setImageFormBusy(form, false);
      reopenImageDialog(dlg);
    });
    xhr.addEventListener("load", () => {
      if (xhr.status === 401 || xhr.status === 403 || /\/login\/?$/.test(xhr.responseURL || "")) {
        window.location.assign("/login");
        return;
      }
      if (xhr.status >= 400) {
        failIsoUpload(form, dlg, "ISO upload failed.");
        return;
      }
      if ((xhr.responseText || "").includes("alert-error")) {
        document.open();
        document.write(xhr.responseText);
        document.close();
        return;
      }
      window.location.assign("/images");
    });
    xhr.send(payload);
  }

  function extractInProgress(status) {
    return status === "queued" || status === "extracting";
  }

  function renderExtractCell(cell, status, error) {
    cell.setAttribute("data-status", status);
    cell.replaceChildren();
    cell.removeAttribute("title");
    let badge = null;
    if (status === "queued" || status === "extracting" || status === "ready" || status === "failed") {
      badge = document.createElement("span");
      badge.className =
        status === "queued"
          ? "badge badge-pending"
          : status === "extracting"
            ? "badge badge-deploying"
            : status === "ready"
              ? "badge badge-deployed"
              : "badge badge-disabled";
      badge.textContent = status;
      cell.append(badge);
    } else {
      cell.textContent = "—";
    }
    if (status === "failed" && error) {
      if (badge) {
        badge.title = error;
      }
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = error;
      cell.append(meta);
      cell.title = error;
    }
  }

  function setImageEditControl(row, imageId, busy) {
    const current = row.querySelector("[data-image-edit]");
    if (!current) {
      return;
    }
    if (busy) {
      if (current.tagName === "BUTTON") {
        current.disabled = true;
        current.title = "Wait until extraction finishes";
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-ghost btn-sm";
      btn.disabled = true;
      btn.setAttribute("data-image-edit", "");
      btn.title = "Wait until extraction finishes";
      btn.textContent = "Edit";
      current.replaceWith(btn);
      return;
    }
    if (current.tagName === "A") {
      current.removeAttribute("title");
      return;
    }
    const link = document.createElement("a");
    link.className = "btn-ghost btn-sm";
    link.href = `/images/${imageId}`;
    link.setAttribute("data-image-edit", "");
    link.textContent = "Edit";
    current.replaceWith(link);
  }

  const extractCells = [...document.querySelectorAll(".extract-status[data-status]")];
  const extractBusy = extractCells.some((el) => extractInProgress(el.getAttribute("data-status")));
  const imageRows = document.querySelector("[data-image-id]");
  if (extractBusy && imageRows) {
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch("/api/images", { headers: { Accept: "application/json" } });
        if (!response.ok) {
          return;
        }
        const images = await response.json();
        let stillBusy = false;
        images.forEach((img) => {
          const row = document.querySelector(`[data-image-id="${img.id}"]`);
          if (!row) {
            return;
          }
          const cell = row.querySelector(".extract-status");
          if (!cell) {
            return;
          }
          const status = img.extract_status || "idle";
          if (extractInProgress(status)) {
            stillBusy = true;
          }
          renderExtractCell(cell, status, img.extract_error || "");
          setImageEditControl(row, img.id, extractInProgress(status));
        });
        if (!stillBusy) {
          window.clearInterval(timer);
        }
      } catch {
        /* keep polling */
      }
    }, 5000);
  }

  const extractBusyDetail = document.querySelector("[data-extract-busy]");
  if (extractBusyDetail) {
    const imageId = Number(extractBusyDetail.getAttribute("data-extract-busy"));
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch("/api/images", { headers: { Accept: "application/json" } });
        if (!response.ok) {
          return;
        }
        const images = await response.json();
        const img = images.find((row) => Number(row.id) === imageId);
        if (!img || extractInProgress(img.extract_status || "idle")) {
          return;
        }
        window.clearInterval(timer);
        window.location.reload();
      } catch {
        /* keep polling */
      }
    }, 5000);
  }

  const fm = document.querySelector(".fm");
  if (fm) {
    const rows = () => [...fm.querySelectorAll(".fm-row")];
    const deletePath = fm.querySelector("[data-fm='delete-path']");
    const deleteBtn = fm.querySelector("[data-fm='delete']");
    const fileInput = fm.querySelector("[data-fm='file']");
    const filename = fm.querySelector("[data-fm='filename']");
    const mkdirDlg = document.getElementById("fm-mkdir");
    const pathForm = fm.querySelector(".fm-path");

    function selectRow(row) {
      rows().forEach((item) => item.classList.toggle("is-selected", item === row));
      const path = row ? row.getAttribute("data-path") || "" : "";
      if (deletePath) {
        deletePath.value = path;
      }
      if (deleteBtn) {
        deleteBtn.disabled = !path;
      }
      if (filename) {
        filename.value = row ? row.getAttribute("data-name") || "" : "";
      }
    }

    fm.addEventListener("click", (event) => {
      const row = event.target.closest(".fm-row");
      if (!row) {
        return;
      }
      if (event.target.closest("[data-fm='ipxe-source']")) {
        return;
      }
      if (row.getAttribute("data-kind") === "dir") {
        const href = row.getAttribute("data-href");
        if (href) {
          window.location.href = href;
        }
        return;
      }
      if (event.target.closest("a.fm-name")) {
        event.preventDefault();
      }
      selectRow(row);
    });
    fm.addEventListener("dblclick", (event) => {
      const row = event.target.closest(".fm-row");
      if (!row || row.getAttribute("data-kind") === "dir") {
        return;
      }
      const href = row.getAttribute("data-href");
      if (href) {
        window.location.href = href;
      }
    });

    const back = fm.querySelector("[data-fm='back']");
    if (back) {
      back.addEventListener("click", () => window.history.back());
    }
    const forward = fm.querySelector("[data-fm='forward']");
    if (forward) {
      forward.addEventListener("click", () => window.history.forward());
    }
    const mkdirBtn = fm.querySelector("[data-fm='mkdir']");
    if (mkdirBtn && mkdirDlg) {
      mkdirBtn.addEventListener("click", () => mkdirDlg.showModal());
    }
    const mkdirCancel = document.querySelector("[data-fm='mkdir-cancel']");
    if (mkdirCancel && mkdirDlg) {
      mkdirCancel.addEventListener("click", () => mkdirDlg.close());
    }
    const uploadBtn = fm.querySelector("[data-fm='upload']");
    if (uploadBtn && fileInput) {
      uploadBtn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        if (fileInput.files && fileInput.files.length) {
          fileInput.closest("form").submit();
        }
      });
    }
    const search = fm.querySelector("[data-fm='search']");
    if (search) {
      search.addEventListener("input", () => {
        const q = search.value.trim().toLowerCase();
        rows().forEach((row) => {
          const name = (row.getAttribute("data-name") || "").toLowerCase();
          row.hidden = Boolean(q) && !name.includes(q);
        });
      });
    }
    if (pathForm) {
      pathForm.addEventListener("submit", () => {
        const input = pathForm.querySelector("input[name='dir']");
        const rootInput = pathForm.querySelector("input[name='root']");
        if (!input) {
          return;
        }
        let value = input.value.trim().replaceAll("\\", "/");
        const match = value.match(/^([a-z]+):\/?(.*)$/i);
        if (match && rootInput) {
          rootInput.value = match[1].toLowerCase();
          value = match[2];
        }
        if (value === "/" || value === ".") {
          value = "";
        }
        input.value = value.replace(/^\//, "");
      });
    }
    fm.querySelectorAll(".fm-list th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.getAttribute("data-sort");
        const body = fm.querySelector(".fm-list tbody");
        const sorted = rows().sort((a, b) => {
          const av = a.getAttribute(`data-${key}`) || a.getAttribute("data-name") || "";
          const bv = b.getAttribute(`data-${key}`) || b.getAttribute("data-name") || "";
          if (key === "size") {
            return Number(av) - Number(bv);
          }
          return av.localeCompare(bv, undefined, { sensitivity: "base" });
        });
        sorted.forEach((row) => body.append(row));
      });
    });
  }

  const BOOT_MENU_SCROLL_KEY = "pxe:boot-menu-scroll";

  function onBootMenuPage() {
    return window.location.pathname === "/boot-menu";
  }

  function rememberBootMenuScroll() {
    if (!onBootMenuPage()) {
      return;
    }
    try {
      sessionStorage.setItem(BOOT_MENU_SCROLL_KEY, String(window.scrollY));
    } catch {
      /* ignore quota / private mode */
    }
  }

  function takeBootMenuScroll() {
    if (!onBootMenuPage()) {
      return null;
    }
    try {
      const raw = sessionStorage.getItem(BOOT_MENU_SCROLL_KEY);
      if (raw === null) {
        return null;
      }
      sessionStorage.removeItem(BOOT_MENU_SCROLL_KEY);
      const y = Number(raw);
      return Number.isNaN(y) ? null : y;
    } catch {
      return null;
    }
  }

  function applyScrollY(y) {
    history.scrollRestoration = "manual";
    const go = () => window.scrollTo(0, y);
    go();
    requestAnimationFrame(() => {
      go();
      requestAnimationFrame(go);
    });
    window.addEventListener("load", go, { once: true });
    window.addEventListener("pageshow", go, { once: true });
  }

  function showModalPreservingScroll(dlg) {
    const x = window.scrollX;
    const y = window.scrollY;
    dlg.showModal();
    const restore = () => window.scrollTo(x, y);
    restore();
    requestAnimationFrame(() => {
      restore();
      requestAnimationFrame(restore);
    });
    const focus = dlg.querySelector("[autofocus], select, input:not([type=hidden])");
    if (focus) {
      focus.focus({ preventScroll: true });
    }
  }

  function wireDialogs() {
    document.querySelectorAll("[data-open-dialog]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        const dlg = document.getElementById(btn.getAttribute("data-open-dialog"));
        if (dlg && typeof dlg.showModal === "function") {
          const action = btn.getAttribute("data-move-action");
          if (action) {
            const form = dlg.querySelector("form");
            if (form) {
              form.setAttribute("action", action);
            }
            const select = dlg.querySelector("[name='folder_id']");
            const current = btn.getAttribute("data-move-folder");
            if (select && current) {
              select.value = current;
            }
            const label = dlg.querySelector("[data-move-image-name]");
            if (label) {
              label.textContent = btn.getAttribute("data-move-name") || "";
            }
          }
          showModalPreservingScroll(dlg);
        }
      });
    });
    document.querySelectorAll("dialog.modal").forEach((dlg) => {
      dlg.querySelectorAll("[data-close-dialog]").forEach((btn) => {
        btn.addEventListener("click", () => dlg.close());
      });
      dlg.addEventListener("click", (event) => {
        if (event.target === dlg) {
          dlg.close();
        }
      });
      if (dlg.hasAttribute("open") && typeof dlg.showModal === "function") {
        dlg.close();
        showModalPreservingScroll(dlg);
      }
    });
  }
  const pendingBootMenuScroll = takeBootMenuScroll();
  wireDialogs();
  if (pendingBootMenuScroll !== null) {
    applyScrollY(pendingBootMenuScroll);
  }

  document.addEventListener("click", (event) => {
    if (!onBootMenuPage() || event.defaultPrevented || event.button !== 0) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    const link = event.target.closest("a[href]");
    if (!link) {
      return;
    }
    let dest;
    try {
      dest = new URL(link.href, window.location.href);
    } catch {
      return;
    }
    if (dest.origin !== window.location.origin || dest.pathname !== "/boot-menu") {
      return;
    }
    if (link.classList.contains("is-active")) {
      event.preventDefault();
      return;
    }
    rememberBootMenuScroll();
  });

  const sshPub = document.getElementById("ssh-pub");
  const sshKeys = document.getElementById("ssh-keys");
  if (sshPub && sshKeys) {
    sshPub.addEventListener("change", async () => {
      const file = sshPub.files && sshPub.files[0];
      if (!file) {
        return;
      }
      const text = (await file.text()).trim();
      if (!text) {
        return;
      }
      sshKeys.value = sshKeys.value.trim() ? `${sshKeys.value.replace(/\s+$/, "")}\n${text}\n` : `${text}\n`;
      sshPub.value = "";
    });
  }

  const machineSearch = document.getElementById("machine-search");
  const machineRows = [...document.querySelectorAll("tr[data-machine-id]")];
  let machineState = "";
  function applyMachineFilter() {
    const query = (machineSearch && machineSearch.value ? machineSearch.value : "").trim().toLowerCase();
    machineRows.forEach((row) => {
      const hay = row.getAttribute("data-search") || "";
      const state = row.getAttribute("data-state") || "";
      const hide = (query && !hay.includes(query)) || (machineState && state !== machineState);
      row.classList.toggle("is-filtered", hide);
    });
  }
  if (machineSearch) {
    machineSearch.addEventListener("input", applyMachineFilter);
  }
  const machineStateFilter = document.getElementById("machine-state-filter");
  if (machineStateFilter) {
    machineStateFilter.addEventListener("change", () => {
      machineState = machineStateFilter.value || "";
      applyMachineFilter();
    });
  }

  const stateLabels = {
    pending: "Pending",
    ready: "Ready",
    deploying: "Deploying",
    imaging: "Imaging",
    timeout_error: "Timeout Error",
    deployed: "Deployed",
    staged: "Staged",
    disabled: "Disabled",
    failed: "Install failed",
  };
  if (machineRows.some((row) => ["deploying", "imaging", "staged"].includes(row.getAttribute("data-state")))) {
    window.setInterval(async () => {
      try {
        const response = await fetch("/api/machines", { headers: { Accept: "application/json" } });
        if (!response.ok) {
          return;
        }
        const machines = await response.json();
        machines.forEach((machine) => {
          const row = document.querySelector(`tr[data-machine-id="${machine.id}"]`);
          if (!row) {
            return;
          }
          row.setAttribute("data-state", machine.state);
          row.className = `state-${machine.state}`;
          const badge = row.querySelector(".badge");
          if (badge) {
            badge.className = `badge badge-${machine.state}`;
            badge.textContent = stateLabels[machine.state] || machine.state;
          }
          const ip = row.querySelector("td:nth-child(5)");
          if (ip) {
            ip.textContent = machine.last_ip || "—";
          }
        });
        applyMachineFilter();
      } catch {
        /* keep polling */
      }
    }, 5000);
  }

  const imageOs = document.getElementById("image-os-filter");
  const imageExtract = document.getElementById("image-extract-filter");
  const imageFilterRows = [...document.querySelectorAll("tr[data-image-id]")];
  function applyImageFilter() {
    const os = imageOs ? imageOs.value : "";
    const extract = imageExtract ? imageExtract.value : "";
    imageFilterRows.forEach((row) => {
      const hide = (os && row.getAttribute("data-os") !== os) || (extract && row.getAttribute("data-extract") !== extract);
      row.classList.toggle("is-filtered", hide);
    });
  }
  if (imageOs) {
    imageOs.addEventListener("change", applyImageFilter);
  }
  if (imageExtract) {
    imageExtract.addEventListener("change", applyImageFilter);
  }

  document.addEventListener("submit", (event) => {
    if (!onBootMenuPage()) {
      return;
    }
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const action = form.getAttribute("action") || window.location.href;
    let dest;
    try {
      dest = new URL(action, window.location.href);
    } catch {
      return;
    }
    if (dest.origin !== window.location.origin) {
      return;
    }
    if (dest.pathname === "/boot-menu" || dest.pathname.startsWith("/boot-menu/")) {
      rememberBootMenuScroll();
    }
  });
})();

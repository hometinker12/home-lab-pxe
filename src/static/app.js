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

  function uploadOverlay() {
    let overlay = document.querySelector(".upload-overlay");
    if (overlay) {
      return overlay;
    }
    overlay = document.createElement("div");
    overlay.className = "upload-overlay";
    overlay.hidden = true;
    overlay.setAttribute("role", "alertdialog");
    overlay.setAttribute("aria-live", "polite");
    overlay.innerHTML =
      '<div class="upload-status"><p data-upload-label>Uploading ISO…</p><progress data-upload-bar max="100" value="0"></progress></div>';
    document.body.append(overlay);
    return overlay;
  }

  function setUploadStatus(percent, label) {
    const overlay = uploadOverlay();
    overlay.hidden = false;
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
    const overlay = document.querySelector(".upload-overlay");
    if (overlay) {
      overlay.hidden = true;
    }
    document.body.removeAttribute("aria-busy");
  }

  function uploadIsoWithProgress(form) {
    const xhr = new XMLHttpRequest();
    const action = form.getAttribute("action") || window.location.pathname;
    xhr.open((form.getAttribute("method") || "POST").toUpperCase(), action);
    xhr.withCredentials = true;
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
      hideUploadOverlay();
      window.alert("ISO upload failed. Check the connection and try again.");
    });
    xhr.addEventListener("abort", () => hideUploadOverlay());
    xhr.addEventListener("load", () => {
      if (xhr.status === 401 || xhr.status === 403 || /\/login\/?$/.test(xhr.responseURL || "")) {
        window.location.assign("/login");
        return;
      }
      if (xhr.status >= 400) {
        hideUploadOverlay();
        window.alert("ISO upload failed.");
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
    xhr.send(new FormData(form));
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
          dlg.showModal();
          const focus = dlg.querySelector("[autofocus], select, input:not([type=hidden])");
          if (focus) {
            focus.focus();
          }
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
        dlg.showModal();
      }
    });
  }
  wireDialogs();
})();

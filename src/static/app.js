(() => {
  function syncImageForm(form) {
    const select = form.querySelector("[name='os_family']");
    if (!select) {
      return;
    }
    const family = select.value;
    form.querySelectorAll("[data-os]").forEach((el) => {
      el.hidden = el.getAttribute("data-os") !== family;
    });
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

  const extractCells = [...document.querySelectorAll(".extract-status[data-status]")];
  const extractBusy = extractCells.some((el) => {
    const status = el.getAttribute("data-status");
    return status === "queued" || status === "extracting";
  });
  if (extractBusy && !document.querySelector("form.image-form textarea")) {
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
          cell.setAttribute("data-status", status);
          if (status === "queued" || status === "extracting") {
            stillBusy = true;
          }
          const label =
            status === "queued"
              ? "queued"
              : status === "extracting"
                ? "extracting"
                : status === "ready"
                  ? "ready"
                  : status === "failed"
                    ? "failed"
                    : "—";
          cell.textContent = label;
          if (status === "failed" && img.extract_error) {
            cell.title = img.extract_error;
          }
        });
        if (!stillBusy) {
          window.clearInterval(timer);
        }
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
      if (event.target.closest("a.fm-name")) {
        event.preventDefault();
      }
      selectRow(row);
    });
    fm.addEventListener("dblclick", (event) => {
      const row = event.target.closest(".fm-row");
      const href = row && row.getAttribute("data-href");
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
        if (!input) {
          return;
        }
        let value = input.value.trim().replaceAll("\\", "/");
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
})();

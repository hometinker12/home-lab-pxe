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
  });

  document.querySelectorAll("form.dhcp-form").forEach((form) => {
    const select = form.querySelector("[name='mode']");
    if (!select) {
      return;
    }
    select.addEventListener("change", () => syncDhcpForm(form));
    syncDhcpForm(form);
  });

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

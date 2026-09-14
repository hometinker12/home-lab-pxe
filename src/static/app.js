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
})();

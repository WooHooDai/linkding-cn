const FILTER_DRAWER_REOPEN_KEY = "ld:reopen-filter-drawer";

let drawerHookInstalled = false;

export function installDrawerReopenHook() {
  if (drawerHookInstalled) {
    return;
  }

  document.addEventListener("turbo:load", () => {
    if (window.sessionStorage.getItem(FILTER_DRAWER_REOPEN_KEY) !== "1") {
      return;
    }

    window.sessionStorage.removeItem(FILTER_DRAWER_REOPEN_KEY);
    const trigger =
      document.querySelector("ld-filter-drawer-trigger") ||
      document.querySelector("[ld-filter-drawer-trigger]");
    if (trigger && !document.querySelector(".filter-drawer.active")) {
      trigger.click();
    }
  });

  drawerHookInstalled = true;
}

export function persistOpenDrawerState() {
  if (document.querySelector(".filter-drawer.active")) {
    window.sessionStorage.setItem(FILTER_DRAWER_REOPEN_KEY, "1");
  }
}

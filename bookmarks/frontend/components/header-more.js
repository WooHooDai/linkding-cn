import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";

async function savePref(url, field, value) {
  const body = new FormData();
  body.append("csrfmiddlewaretoken", getCSRFToken());
  body.append("form_id", "profile_quick");
  body.append("form_fields", field);
  if (value !== null && value !== undefined && value !== "") {
    body.append(field, value);
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    headers: {
      "X-CSRFToken": getCSRFToken(),
      "X-Requested-With": "XMLHttpRequest",
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to save ${field}`);
  }
  return response.json();
}

function applyStickyPref(field, enabled) {
  const selector =
    field === "sticky_header_controls"
      ? ".section-header[data-sticky-on]"
      : ".bookmark-pagination[data-sticky-on]";
  document
    .querySelectorAll(selector)
    .forEach((el) =>
      el.setAttribute("data-sticky-on", enabled ? "true" : "false"),
    );
}

class HeaderMoreMenu extends Behavior {
  constructor(element) {
    super(element);
    this.url = this.element.dataset.saveUrl;
    this.onActionClick = this.onActionClick.bind(this);
    this.onPrefChange = this.onPrefChange.bind(this);
    this.init();
  }

  init() {
    this.element.addEventListener("click", this.onActionClick);
    this.element.addEventListener("change", this.onPrefChange);
  }

  onActionClick(event) {
    const actionButton = event.target.closest(".header-menu-action");
    if (!actionButton) return;
    const action = actionButton.dataset.menuAction;

    if (action === "bulk-edit") {
      document.querySelector(".bulk-edit-active-toggle")?.click();
    } else if (action === "random-sort") {
      const single = document.querySelector("[ld-random-single]");
      if (single) {
        single.click();
      } else {
        const randomForm = document.querySelector(
          'form[method="post"] input[name="sort"][value="random"]',
        )?.closest("form");
        randomForm?.requestSubmit();
      }
    }

    this.element.closest("ld-dropdown")?.close?.();
  }

  async onPrefChange(event) {
    const input = event.target.closest("[data-pref]");
    if (!input) return;
    const field = input.dataset.pref;
    let value;
    if (input.type === "checkbox") {
      value = input.checked ? "on" : "";
    } else {
      value = input.value;
    }

    try {
      await savePref(this.url, field, value);
    } catch (error) {
      console.error(error);
      return;
    }

    if (field === "sticky_header_controls" || field === "sticky_pagination") {
      applyStickyPref(field, Boolean(value));
    } else if (field === "bookmark_view_mode" || field === "items_per_page") {
      window.location.reload();
    }
  }
}

registerBehavior("data-header-more", HeaderMoreMenu);
registerBehavior("data-header-prefs", HeaderMoreMenu);

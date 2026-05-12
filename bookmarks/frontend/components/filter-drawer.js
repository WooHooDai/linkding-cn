import { html, render } from "lit";
import { gettext } from "../utils/i18n.js";
import { installDrawerReopenHook } from "../state/filter-drawer-state.js";
import { isKeyboardActive } from "../utils/focus.js";
import { HeadlessElement } from "../utils/element.js";
import { Modal } from "./modal.js";

installDrawerReopenHook();

class FilterDrawerTrigger extends HeadlessElement {
  init() {
    this.onClick = this.onClick.bind(this);
    this.addEventListener("click", this.onClick);
  }

  disconnectedCallback() {
    this.removeEventListener("click", this.onClick);
  }

  onClick() {
    const modal = document.createElement("ld-filter-drawer");
    document.body.querySelector(".modals")?.appendChild(modal);
  }
}

customElements.define("ld-filter-drawer-trigger", FilterDrawerTrigger);

class FilterDrawer extends Modal {
  connectedCallback() {
    this.classList.add("modal", "drawer", "filter-drawer");

    render(
      html`
        <div class="modal-overlay" data-close-modal></div>
        <div class="modal-container" role="dialog" aria-modal="true">
          <div class="modal-header">
            <h2>${gettext("Sidebar")}</h2>
            <button
              class="btn btn-noborder close"
              aria-label=${gettext("Close dialog")}
              data-close-modal
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                stroke-width="2"
                stroke="currentColor"
                fill="none"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path stroke="none" d="M0 0h24v24H0z" fill="none"></path>
                <path d="M18 6l-12 12"></path>
                <path d="M6 6l12 12"></path>
              </svg>
            </button>
          </div>
          <div class="modal-body"></div>
        </div>
      `,
      this,
    );

    this.teleport();
    this.doClose = this.doClose.bind(this);
    document.addEventListener("turbo:before-cache", this.doClose);
    this.getBoundingClientRect();
    requestAnimationFrame(() => this.classList.add("active"));
    super.init();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.teleportBack();
    document.removeEventListener("turbo:before-cache", this.doClose);
  }

  mapHeading(container, from, to) {
    container.querySelectorAll(from).forEach((heading) => {
      const newHeading = document.createElement(to);
      newHeading.textContent = heading.textContent;
      heading.replaceWith(newHeading);
    });
  }

  teleport() {
    const content = this.querySelector(".modal-body");
    const sidePanel = document.querySelector(".sidebar");
    if (!content || !sidePanel) {
      return;
    }
    content.append(...sidePanel.children);
    this.mapHeading(content, "h2", "h3");
  }

  teleportBack() {
    const sidePanel = document.querySelector(".sidebar");
    const content = this.querySelector(".modal-body");
    if (!content || !sidePanel) {
      return;
    }
    sidePanel.append(...content.children);
    this.mapHeading(sidePanel, "h3", "h2");
  }

  doClose() {
    super.doClose();
    const restoreFocusElement =
      document.querySelector("ld-filter-drawer-trigger") || document.body;
    restoreFocusElement.focus({ focusVisible: isKeyboardActive() });
  }
}

customElements.define("ld-filter-drawer", FilterDrawer);

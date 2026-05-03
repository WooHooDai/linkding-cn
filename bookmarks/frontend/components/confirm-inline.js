import { Behavior, registerBehavior } from "./runtime.js";
import { gettext } from "../utils/i18n.js";

let activePopup = null;

function dismissActive() {
  if (activePopup) {
    activePopup.close();
    activePopup = null;
  }
}

// Global listeners for dismiss
document.addEventListener("click", (event) => {
  if (activePopup && !activePopup.contains(event.target)) {
    dismissActive();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    dismissActive();
  }
});

document.addEventListener("turbo:before-cache", dismissActive);

class ConfirmPopup extends HTMLElement {
  connectedCallback() {
    dismissActive();

    const button = this._button;
    const question =
      button.getAttribute("ld-confirm-question") ||
      gettext("Are you sure?");

    const rect = button.getBoundingClientRect();
    const top = rect.bottom + 6;

    // Render off-screen to measure
    this.style.cssText = "position:fixed;visibility:hidden;";
    this.innerHTML = `<span class="confirm-popup-question">${question}</span><span class="confirm-popup-actions"><button type="button" class="btn btn-sm">${gettext("Cancel")}</button><button type="button" class="btn btn-sm btn-error">${gettext("Confirm")}</button></span>`;

    const popupWidth = this.offsetWidth;
    let left = rect.left + rect.width / 2 - popupWidth / 2;

    // Keep within viewport horizontally
    if (left < 8) left = 8;
    if (left + popupWidth > window.innerWidth - 8) {
      left = window.innerWidth - 8 - popupWidth;
    }

    // Final position
    this.style.cssText = `position:fixed;top:${top}px;left:${left}px;`;

    this.querySelector(".confirm-popup-actions .btn:not(.btn-error)").addEventListener("click", (e) => {
      e.stopPropagation();
      dismissActive();
    });

    this.querySelector(".confirm-popup-actions .btn-error").addEventListener("click", (e) => {
      e.stopPropagation();
      const form = button.closest("form");
      if (form) {
        form.requestSubmit(button);
      }
      dismissActive();
    });

    activePopup = this;
  }

  close() {
    this.remove();
    if (activePopup === this) {
      activePopup = null;
    }
    Behavior.interacting = false;
  }
}

customElements.define("ld-confirm-popup", ConfirmPopup);

class ConfirmButtonBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.onClick = this.onClick.bind(this);
    element.addEventListener("click", this.onClick);
  }

  destroy() {
    this.element.removeEventListener("click", this.onClick);
  }

  onClick(event) {
    event.preventDefault();
    event.stopPropagation();
    Behavior.interacting = true;

    const popup = document.createElement("ld-confirm-popup");
    popup._button = this.element;
    document.body.appendChild(popup);
  }
}

registerBehavior("ld-confirm-button", ConfirmButtonBehavior);

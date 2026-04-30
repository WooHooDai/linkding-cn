import { html, LitElement } from "lit";
import { gettext } from "../behaviors/i18n.js";
import { FocusTrapController, isKeyboardActive } from "../utils/focus.js";
import { PositionController } from "../utils/position-controller.js";

let confirmId = 0;

function nextConfirmId() {
  return `confirm-${confirmId++}`;
}

function removeAll() {
  document
    .querySelectorAll("ld-confirm-dropdown")
    .forEach((dropdown) => dropdown.close());
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-confirm]");
  if (!button) {
    return;
  }

  removeAll();
  event.preventDefault();

  const dropdown = document.createElement("ld-confirm-dropdown");
  dropdown.button = button;
  document.body.appendChild(dropdown);
});

document.addEventListener("turbo:before-cache", removeAll);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    removeAll();
  }
});

class ConfirmDropdown extends LitElement {
  constructor() {
    super();
    this.confirmId = nextConfirmId();
  }

  createRenderRoot() {
    return this;
  }

  firstUpdated() {
    this.classList.add("dropdown", "confirm-dropdown", "active");

    const menu = this.querySelector(".menu");
    this.positionController = new PositionController({
      anchor: this.button,
      overlay: menu,
      arrow: this.querySelector(".menu-arrow"),
      offset: 12,
    });
    this.positionController.enable();
    this.focusTrap = new FocusTrapController(menu);
  }

  render() {
    const questionText =
      this.button.dataset.confirmQuestion || gettext("Are you sure?");
    return html`
      <div
        class="menu with-arrow"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby=${this.confirmId}
      >
        <span id=${this.confirmId} style="font-weight: bold;">
          ${questionText}
        </span>
        <button type="button" class="btn" @click=${this.close}>
          ${gettext("Cancel")}
        </button>
        <button type="submit" class="btn btn-error" @click=${this.confirm}>
          ${gettext("Confirm")}
        </button>
        <div class="menu-arrow"></div>
      </div>
    `;
  }

  confirm() {
    this.button.closest("form")?.requestSubmit(this.button);
    this.close();
  }

  close() {
    this.positionController?.disable();
    this.focusTrap?.destroy();
    this.remove();
    this.button?.focus({ focusVisible: isKeyboardActive() });
  }
}

customElements.define("ld-confirm-dropdown", ConfirmDropdown);

import { Behavior, registerBehavior } from "./runtime.js";
import { gettext } from "../utils/i18n.js";

let activePopup = null;
let scrollRafId = null;

function dismissActive() {
  if (activePopup) {
    activePopup.close();
    activePopup = null;
  }
}

function updatePopupPosition() {
  if (!activePopup || !activePopup._button) return;
  const button = activePopup._button;
  const rect = button.getBoundingClientRect();
  const popupWidth = activePopup.offsetWidth;
  const popupHeight = activePopup.offsetHeight;

  let left = rect.left + rect.width / 2 - popupWidth / 2;
  if (left < 8) left = 8;
  if (left + popupWidth > window.innerWidth - 8) {
    left = window.innerWidth - 8 - popupWidth;
  }

  const preferUp = rect.top + rect.height / 2 > window.innerHeight / 2;
  let top;
  if (preferUp) {
    top = rect.top - popupHeight - 6;
    if (top < 8) top = rect.bottom + 6;
  } else {
    top = rect.bottom + 6;
    if (top + popupHeight > window.innerHeight - 8) {
      top = rect.top - popupHeight - 6;
    }
  }

  activePopup.style.cssText = `position:fixed;top:${top}px;left:${left}px;`;
}

function onScroll() {
  if (scrollRafId) return;
  scrollRafId = requestAnimationFrame(() => {
    scrollRafId = null;
    updatePopupPosition();
  });
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
    const isDanger = button.hasAttribute("ld-confirm-danger");
    const confirmClass = isDanger ? "btn-error" : "btn-primary";

    const rect = button.getBoundingClientRect();

    // Render off-screen to measure
    this.style.cssText = "position:fixed;visibility:hidden;";
    this.innerHTML = `<span class="confirm-popup-question">${question}</span><span class="confirm-popup-actions"><button type="button" class="btn btn-sm">${gettext("Cancel")}</button><button type="button" class="btn btn-sm ${confirmClass}">${gettext("Confirm")}</button></span>`;

    const popupWidth = this.offsetWidth;
    const popupHeight = this.offsetHeight;
    let left = rect.left + rect.width / 2 - popupWidth / 2;

    // Keep within viewport horizontally
    if (left < 8) left = 8;
    if (left + popupWidth > window.innerWidth - 8) {
      left = window.innerWidth - 8 - popupWidth;
    }

    // Position: prefer up when button is in the lower half of viewport
    const preferUp = rect.top + rect.height / 2 > window.innerHeight / 2;
    let top;
    if (preferUp) {
      top = rect.top - popupHeight - 6;
      if (top < 8) top = rect.bottom + 6;
    } else {
      top = rect.bottom + 6;
      if (top + popupHeight > window.innerHeight - 8) {
        top = rect.top - popupHeight - 6;
      }
    }

    // Final position
    this.style.cssText = `position:fixed;top:${top}px;left:${left}px;`;

    const buttons = this.querySelectorAll(".confirm-popup-actions .btn");
    const cancelBtn = buttons[0];
    const confirmBtn = buttons[1];

    if (cancelBtn) cancelBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      dismissActive();
    });

    if (confirmBtn) confirmBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      // 支持回调模式（用于非 form 场景，如阅读页面侧边栏）
      if (this._onConfirm) {
        this._onConfirm();
      } else {
        const form = button.closest("form");
        if (form) {
          form.requestSubmit(button);
        }
      }
      dismissActive();
    });

    activePopup = this;
    window.addEventListener("scroll", onScroll, true);
  }

  close() {
    this.remove();
    if (activePopup === this) {
      activePopup = null;
    }
    window.removeEventListener("scroll", onScroll, true);
    if (scrollRafId) {
      cancelAnimationFrame(scrollRafId);
      scrollRafId = null;
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
    // 动作本身没有破坏性、或调用方已在别处确认过时，直接走默认提交
    if (this.element.hasAttribute("data-skip-confirm")) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    Behavior.interacting = true;

    const popup = document.createElement("ld-confirm-popup");
    popup._button = this.element;
    popup._onConfirm = this.element._onConfirm || null;
    document.body.appendChild(popup);
  }
}

registerBehavior("ld-confirm-button", ConfirmButtonBehavior);

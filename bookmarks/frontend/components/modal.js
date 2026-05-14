import { FocusTrapController } from "../utils/focus.js";
import { HeadlessElement } from "../utils/element.js";

export class Modal extends HeadlessElement {
  init() {
    this.onClose = this.onClose.bind(this);
    this.onKeyDown = this.onKeyDown.bind(this);

    this.querySelectorAll("[data-close-modal]").forEach((button) => {
      button.addEventListener("click", this.onClose);
    });
    document.addEventListener("keydown", this.onKeyDown);

    this.setupInert();
    this.focusTrap = new FocusTrapController(
      this.querySelector(".modal-container"),
    );
  }

  disconnectedCallback() {
    this.querySelectorAll("[data-close-modal]").forEach((button) => {
      button.removeEventListener("click", this.onClose);
    });
    document.removeEventListener("keydown", this.onKeyDown);
    this.clearInert();
    this.focusTrap?.destroy();
  }

  setupInert() {
    document
      .querySelectorAll("body > *:not(.modals)")
      .forEach((element) => element.setAttribute("inert", ""));
    document.body.classList.add("scroll-lock");
  }

  clearInert() {
    document
      .querySelectorAll("body > *")
      .forEach((element) => element.removeAttribute("inert"));
    document.body.classList.remove("scroll-lock");
  }

  onKeyDown(event) {
    const targetNodeName = event.target.nodeName;
    const isInputTarget =
      targetNodeName === "INPUT" ||
      targetNodeName === "SELECT" ||
      targetNodeName === "TEXTAREA";

    if (isInputTarget) {
      return;
    }

    if (event.key === "Escape") {
      this.onClose(event);
    }
  }

  onClose(event) {
    event.preventDefault();
    this.classList.add("closing");
    this.addEventListener(
      "animationend",
      (animationEvent) => {
        if (animationEvent.animationName === "fade-out") {
          this.doClose();
        }
      },
      { once: true },
    );
  }

  doClose() {
    this.remove();
    this.dispatchEvent(new CustomEvent("modal:close"));

    const closeUrl = this.dataset.closeUrl;
    const frame = this.dataset.turboFrame;
    const action = this.dataset.turboAction || "replace";
    if (closeUrl) {
      Turbo.visit(closeUrl, { action, frame });
    }
  }
}

customElements.define("ld-modal", Modal);

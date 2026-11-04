import { FocusTrapController } from "../utils/focus.js";
import { HeadlessElement } from "../utils/element.js";

let bodyScrollLockDepth = 0;
let bodyOriginalPaddingRight = "";

// 补偿量必须和「滚动条消失」落在同一帧完成，才能抵消掉那次宽度变化。
// body 上挂着 padding-inline 过渡（布局抽屉 / 侧边栏让位用的 0.2s），直接写 padding 会被拉成动画：
// 于是遮罩下的内容先被滚动条消失顶一下、再用 0.2s 滑回去，看起来就是页面在抖动。
// 这里写之前临时摘掉过渡、写完强制回流把这一帧定住，再还原（padding 已是目标值，不会补动画）。
function setBodyPaddingRight(body, value) {
  const previousTransition = body.style.transition;
  body.style.transition = "none";
  body.style.paddingRight = value;
  void body.offsetWidth;
  body.style.transition = previousTransition;
}

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
    this.lockBodyScroll();
    document
      .querySelectorAll("body > *:not(.modals)")
      .forEach((element) => element.setAttribute("inert", ""));
  }

  clearInert() {
    document
      .querySelectorAll("body > *")
      .forEach((element) => element.removeAttribute("inert"));
    this.unlockBodyScroll();
  }

  // modal 弹出时，通过动态补偿滚动条轨道宽度，避免视觉闪动
  lockBodyScroll() {
    const body = document.body;

    if (bodyScrollLockDepth === 0) {
      bodyOriginalPaddingRight = body.style.paddingRight || "";
      const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;

      if (scrollbarWidth > 0) {
        const currentPadding = parseFloat(getComputedStyle(body).paddingRight) || 0;
        setBodyPaddingRight(body, `${currentPadding + scrollbarWidth}px`);
      }

      body.classList.add("scroll-lock");
    }

    bodyScrollLockDepth += 1;
  }

  unlockBodyScroll() {
    const body = document.body;

    if (bodyScrollLockDepth === 0) {
      return;
    }

    bodyScrollLockDepth -= 1;

    if (bodyScrollLockDepth === 0) {
      body.classList.remove("scroll-lock");
      setBodyPaddingRight(body, bodyOriginalPaddingRight);
      bodyOriginalPaddingRight = "";
    }
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

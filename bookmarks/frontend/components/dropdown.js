import { HeadlessElement } from "../utils/element.js";
import { PositionController } from "../utils/position-controller.js";

class Dropdown extends HeadlessElement {
  constructor() {
    super();
    this.opened = false;
    this.onClick = this.onClick.bind(this);
    this.onOutsideClick = this.onOutsideClick.bind(this);
    this.onEscape = this.onEscape.bind(this);
    this.onFocusOut = this.onFocusOut.bind(this);
  }

  init() {
    this.style.setProperty("--dropdown-focus-display", "none");
    this.addEventListener("keydown", this.onEscape);
    this.addEventListener("focusout", this.onFocusOut);

    this.toggle = this.querySelector(".dropdown-toggle");
    this.toggle?.setAttribute("aria-expanded", "false");
    this.toggle?.addEventListener("click", this.onClick);
  }

  disconnectedCallback() {
    this.close();
    this.toggle?.removeEventListener("click", this.onClick);
    this.removeEventListener("keydown", this.onEscape);
    this.removeEventListener("focusout", this.onFocusOut);
  }

  open() {
    this.opened = true;
    this.classList.add("active");
    this.toggle?.setAttribute("aria-expanded", "true");
    // popover-open 一旦成立，菜单就不再匹配 :focus-within；保持焦点外观与展开态一致
    this.toggle?.focus({ preventScroll: true });
    if (!this.positionController) {
      const menu = this.querySelector(".menu");
      if (!this.toggle || !menu) return;
      // 搜索偏好面板的宽度来源是整个搜索行；面板宽度封顶后不能再按搜索框
      // 左边缘展开，否则会离行尾的筛选按钮太远，改成与按钮右缘对齐
      const searchContainer = this.closest(".search-container");
      this.positionController = new PositionController({
        anchor: searchContainer || this.toggle,
        overlay: menu,
        placement:
          searchContainer ||
          this.classList.contains("dropdown-right") ||
          this.closest("header")
            ? "bottom-end"
            : "bottom-start",
        autoWidth:
          menu.classList.contains("menu-panel") &&
          !menu.classList.contains("header-more-panel"),
        widthAnchor: searchContainer || this.toggle,
      });
    }
    this.positionController?.enable();
    document.addEventListener("click", this.onOutsideClick);
  }

  close() {
    this.opened = false;
    this.classList.remove("active");
    this.toggle?.setAttribute("aria-expanded", "false");
    this.positionController?.disable();
    document.removeEventListener("click", this.onOutsideClick);
  }

  onClick() {
    if (this.opened) {
      this.close();
    } else {
      this.open();
    }
  }

  onOutsideClick(event) {
    if (!this.contains(event.target)) {
      this.close();
    }
  }

  onEscape(event) {
    if (event.key === "Escape" && this.opened) {
      event.preventDefault();
      this.close();
      this.toggle?.focus();
    }
  }

  onFocusOut(event) {
    if (!this.contains(event.relatedTarget)) {
      this.close();
    }
  }
}

customElements.define("ld-dropdown", Dropdown);

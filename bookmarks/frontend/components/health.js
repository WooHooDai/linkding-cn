import { HeadlessElement } from "../utils/element.js";
import { getCSRFToken } from "../utils/csrf.js";
import { Behavior, registerBehavior } from "./runtime.js";

/**
 * 通用健康状态 popover 交互（列表工具栏指示器与详情弹窗 chip 共用）。
 *
 * - 桌面：CSS `:hover` / `:focus` 显示 popover
 * - 触屏 / 键盘：点击（Enter/空格）切换 popover，Escape / 点击外部关闭
 * - 点击 popover 内的刷新按钮（`[data-health-check]`）不触发 toggle
 *
 * @param {HTMLElement} host 带 `.health-popover` 子元素的宿主元素
 * @returns {{ destroy: () => void }}
 */
export function attachHealthPopover(host) {
  const isOpen = () => host.classList.contains("health-popover-open");

  /**
   * 将弹窗钳制在视口内：窄屏限制宽度，超出右/左/下边缘时反向平移。
   * 列表工具栏中健康指示靠近行尾，绝对定位（left:0）的弹窗在小屏上
   * 容易右溢出视口，打开时按当前视口重算一次，并在滚动/缩放时保持。
   */
  const clampPopover = () => {
    const popover = host.querySelector(".health-popover");
    if (!popover) return;
    const margin = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (vw < 640) {
      popover.style.maxWidth = `calc(100vw - ${2 * margin}px)`;
    } else {
      popover.style.maxWidth = "";
    }
    const rect = popover.getBoundingClientRect();
    const overflowRight = rect.right - (vw - margin);
    const overflowLeft = margin - rect.left;
    const overflowBottom = rect.bottom - (vh - margin);
    if (overflowRight > 0) {
      popover.style.left = `${-overflowRight}px`;
    } else if (overflowLeft > 0) {
      popover.style.left = `${overflowLeft}px`;
    } else {
      popover.style.left = "";
    }
    popover.style.top = overflowBottom > 0 ? `${-(rect.bottom - (vh - margin))}px` : "";
  };

  const close = () => {
    host.classList.remove("health-popover-open");
    host.setAttribute("aria-expanded", "false");
    const popover = host.querySelector(".health-popover");
    if (popover) {
      popover.style.left = "";
      popover.style.top = "";
      popover.style.maxWidth = "";
    }
    window.removeEventListener("resize", clampPopover);
    window.removeEventListener("scroll", clampPopover);
    document.removeEventListener("click", handleOutside, true);
  };

  const open = () => {
    host.classList.add("health-popover-open");
    host.setAttribute("aria-expanded", "true");
    document.addEventListener("click", handleOutside, true);
    window.addEventListener("resize", clampPopover);
    window.addEventListener("scroll", clampPopover, { passive: true });
    clampPopover();
  };

  const toggle = () => {
    if (isOpen()) {
      close();
    } else {
      open();
    }
  };

  const handleKeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    } else if (event.key === "Escape") {
      close();
    }
  };

  const handleClick = (event) => {
    if (event.target.closest("[data-health-check]")) {
      return;
    }
    toggle();
  };

  const handleOutside = (event) => {
    if (!host.contains(event.target)) {
      close();
    }
  };

  host.addEventListener("click", handleClick);
  host.addEventListener("keydown", handleKeydown);

  return {
    destroy() {
      host.removeEventListener("click", handleClick);
      host.removeEventListener("keydown", handleKeydown);
      document.removeEventListener("click", handleOutside, true);
    },
  };
}

/**
 * 重新检查健康状态（列表工具栏指示器 popover 内刷新按钮）。
 *
 * 点击 `[data-health-check]` 后 POST 到 check 端点，就地更新：
 * dot 颜色 / 状态文字 / popover 详情（HTTP 码、说明、检查时间），
 * 按钮文案切换为"重新检查"。
 *
 * @param {HTMLElement} host 带 `.health-popover` 的宿主元素
 * @returns {{ destroy: () => void }}
 */
export function attachHealthRefresh(host) {
  const popover = host.querySelector(".health-popover");
  const dot = host.querySelector(".health-dot");
  const text = host.querySelector(".health-text");

  const button = host.querySelector("[data-health-check]");
  if (!button || !popover) {
    return { destroy() {} };
  }

  const setRow = (selector, value) => {
    const row = popover.querySelector(selector);
    if (row) {
      row.textContent = value;
      row.hidden = !value;
    }
  };

  const onClick = async (event) => {
    event.preventDefault();
    event.stopPropagation();

    // 注意：textContent 赋值会清空按钮内的 SVG 图标，必须保存/恢复 innerHTML
    const previousHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = "…";

    try {
      const response = await fetch(button.dataset.checkUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCSRFToken(),
        },
      });
      if (!response.ok) {
        throw new Error(`check failed: ${response.status}`);
      }
      const data = await response.json();

      // ok 也显示指示器：重检后不再隐藏宿主，统一走就地更新
      // skipped（L1+L2 关）→ status 为 null，按未检查派生 unknown 显示
      const status = data.status || "unknown";
      const statusDisplay = data.status_display || "Unknown";

      // dot 状态色（重检后结果总是新鲜的，先移除旧状态/stale class 再添加新状态）
      if (dot) {
        dot.classList.remove(
          ...Array.from(dot.classList).filter((c) => c.startsWith("health-dot--")),
        );
        dot.classList.add(`health-dot--${status}`);
      }
      // 状态文字
      if (text) {
        text.textContent = statusDisplay;
      }
      // popover 详情
      const statusEl = popover.querySelector(".health-popover-status");
      if (statusEl) {
        statusEl.textContent = statusDisplay;
      }
      // 移除过期区块（重检后结果总是新鲜的）
      popover.querySelectorAll(
        ".health-popover-divider, .health-popover-section-title, .health-popover-row-status",
      ).forEach((el) => el.remove());
      setRow(".health-popover-row-http", data.http_status ? `HTTP ${data.http_status}` : "");
      setRow(".health-popover-reason", data.reason ?? "");
      setRow(
        ".health-popover-checked",
        data.checked_at_display ? `${data.checked_at_label ?? ""} ${data.checked_at_display}` : "",
      );
    } catch (error) {
      // 保留按钮原样，避免图标消失
      button.title = button.dataset.labelRecheck || button.title;
    } finally {
      button.disabled = false;
      button.innerHTML = previousHtml;
    }
  };

  button.addEventListener("click", onClick);

  return {
    destroy() {
      button.removeEventListener("click", onClick);
    },
  };
}


/**
 * 书签列表工具栏的健康状态指示器。
 */
class HealthIndicator extends HeadlessElement {
  init() {
    this._popoverCleanup = attachHealthPopover(this);
    this._refreshCleanup = attachHealthRefresh(this);
  }

  disconnectedCallback() {
    if (this._popoverCleanup) {
      this._popoverCleanup.destroy();
      this._popoverCleanup = null;
    }
    if (this._refreshCleanup) {
      this._refreshCleanup.destroy();
      this._refreshCleanup = null;
    }
  }
}

customElements.define("ld-health-indicator", HealthIndicator);

/**
 * 健康状态筛选的 "All" 批量勾选控件。
 * 点击 All 时：存在未勾选的状态 → 全选；所有状态均已勾选 → 全部取消。
 * All 复选框本身作为控件提交（value="all"），后端 from_request 会忽略该值。
 */
class HealthFilterBehavior extends Behavior {
  constructor(element) {
    super(element);
    this.onAllChange = this.onAllChange.bind(this);
    this.syncAllState = this.syncAllState.bind(this);
    this.init();
  }

  init() {
    const form = this.element.closest("form");
    this.statusCheckboxes = form
      ? form.querySelectorAll('input[name="health_status"]:not([value="all"])')
      : [];
    this.allCheckbox = this.element.querySelector('input[name="health_status"][value="all"]');

    if (this.allCheckbox) {
      this.allCheckbox.addEventListener("change", this.onAllChange);
      // 单个状态勾选变化时同步 All 的勾选状态：
      // 只要存在未勾选的状态，All 就应当被取消勾选（全选指示语义）
      this.statusCheckboxes.forEach((cb) => {
        cb.addEventListener("change", this.syncAllState);
      });
      this.syncAllState();
    }
  }

  syncAllState() {
    const statuses = Array.from(this.statusCheckboxes);
    const allChecked =
      statuses.length > 0 && statuses.every((cb) => cb.checked);
    this.allCheckbox.checked = allChecked;
  }

  onAllChange(event) {
    const target = event.target;
    const statuses = Array.from(this.statusCheckboxes);
    const someUnchecked = statuses.some((cb) => !cb.checked);

    if (someUnchecked) {
      // 有未勾选 → 全选
      statuses.forEach((cb) => {
        cb.checked = true;
      });
      target.checked = true;
    } else {
      // 全部已勾选 → 全部取消
      statuses.forEach((cb) => {
        cb.checked = false;
      });
      target.checked = false;
    }

    // 触发表单 change 事件，让搜索页自动提交筛选
    const form = this.element.closest("form");
    if (form) {
      form.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  destroy() {
    if (this.allCheckbox) {
      this.allCheckbox.removeEventListener("change", this.onAllChange);
    }
    this.statusCheckboxes.forEach((cb) => {
      cb.removeEventListener("change", this.syncAllState);
    });
  }
}

registerBehavior("ld-health-filter", HealthFilterBehavior);

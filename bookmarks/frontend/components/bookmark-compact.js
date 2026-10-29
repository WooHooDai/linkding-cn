import { Behavior, registerBehavior } from "./runtime.js";

/**
 * 紧凑视图的行展开/折叠。
 *
 * 桌面（精确指针 + 支持 hover）：只有按钮能钉住展开/折叠，本体点击不切换；
 * 触屏/粗指针：点按条目本体同样切换（hover 不存在，本体是主要入口）。
 * 各条目独立开合，不联动、不排他。
 */
const ITEM_SELECTOR = "li[ld-bookmark-item]";
const INTERACTIVE_SELECTOR =
  "a, button, input, textarea, label, ld-health-indicator";

class BookmarkCompact extends Behavior {
  constructor(element) {
    super(element);
    this.finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
    this.onClick = this.onClick.bind(this);
    element.addEventListener("click", this.onClick);
  }

  destroy() {
    this.element.removeEventListener("click", this.onClick);
  }

  onClick(event) {
    const item = event.target.closest(ITEM_SELECTOR);
    if (!item || !this.element.contains(item)) return;

    // 展开/折叠按钮：独立切换当前条目
    const toggleBtn = event.target.closest(".compact-toggle-btn");
    if (toggleBtn) {
      this.toggleItem(toggleBtn.closest(ITEM_SELECTOR));
      return;
    }
    // 其余交互元素（链接/输入/健康指示等）自行处理，不触发切换
    if (event.target.closest(INTERACTIVE_SELECTOR)) return;
    // 桌面本体点击不钉住；触屏本体点按切换
    if (this.finePointer.matches) return;
    this.toggleItem(item);
  }

  toggleItem(item) {
    const expanded = item.classList.toggle("toolbar-visible");
    this.syncButton(item.querySelector(".compact-toggle-btn"), expanded);
    // 桌面端按钮收起后指针仍在行内：临时抑制 :hover 预览，直到指针离开，
    // 否则收起状态会被悬停样式立刻顶回来。
    if (!expanded && this.finePointer.matches) {
      item.classList.add("toolbar-suppressed");
      item.addEventListener(
        "pointerleave",
        () => item.classList.remove("toolbar-suppressed"),
        { once: true },
      );
    } else if (expanded) {
      item.classList.remove("toolbar-suppressed");
    }
  }

  syncButton(btn, expanded) {
    if (!btn) return;
    btn.setAttribute("aria-expanded", expanded ? "true" : "false");
    btn.title = expanded ? btn.dataset.titleCollapse : btn.dataset.titleExpand;
  }

  /** 紧凑视图的笔记常驻会撑破单行，e 切换「hover 行时显示笔记」。 */
  handleShortcut(action) {
    if (action !== "toggle_notes") return false;
    this.element.classList.toggle("notes-hover");
    return true;
  }
}

registerBehavior("ld-bookmark-compact", BookmarkCompact);

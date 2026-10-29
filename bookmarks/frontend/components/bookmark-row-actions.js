import { handleBookmarkAction } from "../utils/bookmark-action.js";
import { gettext } from "./bookmark-row-editor.js";

/** 状态动作的逆动作：回滚时用它把整行按钮恢复回去 */
const INVERT_ACTION = {
  share: "unshare",
  unshare: "share",
  mark_as_read: "mark_as_unread",
  mark_as_unread: "mark_as_read",
};

/** 行内动作按钮（分享/已读/归档/删除）：乐观更新，失败回滚。 */
export const bookmarkRowActions = {
  // ==========================================
  // Action 按钮（分享、已读、归档、删除）
  // 采用乐观更新：先改 DOM，再发 API，失败回滚
  // ==========================================

  _initActionButtons() {
    const actionButtons = this.element.querySelectorAll("button[data-action]");
    actionButtons.forEach((button) => {
      if (!button.dataset.action) return;

      if (button.hasAttribute("ld-confirm-button")) {
        button._onConfirm = () =>
          this._executeAction(button, button.dataset.action);
      } else {
        button.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this._executeAction(button, button.dataset.action);
        });
      }
    });
  },

  async _executeAction(button, action) {
    handleBookmarkAction({
      bookmarkId: this.bookmarkId,
      action,
      onOptimistic: () => this._applyStateToRow(action, true),
      onRollback: () => this._applyStateToRow(action, false),
    });
  },

  /* 状态可能有多处入口（状态列、悬浮工具栏、列表状态按钮），
     乐观更新必须整行同步，否则另一处的按钮会停留在旧状态 */
  _applyStateToRow(action, optimistic) {
    const isShare = action === "share" || action === "unshare";
    const isRead = action === "mark_as_read" || action === "mark_as_unread";
    const target = optimistic ? action : INVERT_ACTION[action];
    if (!target) return;

    if (isRead) {
      this.element.classList.toggle("unread", target === "mark_as_unread");
      this.element
        .querySelectorAll(
          '[data-action="mark_as_read"], [data-action="mark_as_unread"]',
        )
        .forEach((btn) =>
          this._toggleReadButton(btn, target === "mark_as_read"),
        );
    }
    if (isShare) {
      this.element
        .querySelectorAll('[data-action="share"], [data-action="unshare"]')
        .forEach((btn) => this._toggleShareButton(btn, target === "share"));
    }
  },

  // ---- 图标替换 ----

  _setIcon(button, href) {
    const oldUse = button.querySelector("svg.action-icon use");
    if (oldUse) {
      const newUse = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "use",
      );
      newUse.setAttribute("href", href);
      oldUse.replaceWith(newUse);
    }
  },

  _toggleShareButton(button, isShared) {
    this._setIcon(button, isShared ? "#ld-icon-share" : "#ld-icon-share-x");
    button.name = isShared ? "unshare" : "share";
    button.dataset.action = isShared ? "unshare" : "share";
    button.title = isShared ? gettext("Shared") : gettext("Share");
  },

  _toggleReadButton(button, isRead) {
    this._setIcon(button, isRead ? "#ld-icon-read-check" : "#ld-icon-unread-x");
    button.dataset.action = isRead ? "mark_as_unread" : "mark_as_read";
    button.title = isRead ? gettext("Mark as unread") : gettext("Mark as read");
    this.element.classList.toggle("unread", !isRead);
  },
};

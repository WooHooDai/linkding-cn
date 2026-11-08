/**
 * 可撤销删除：移到回收站 / 彻底删除。
 *
 * 行先淡出，节点保留，5s 内可以撤销：
 * - 移到回收站：请求立刻发出，撤销再调 restore；
 * - 彻底删除：窗口内不发请求，撤销即完全没发生；窗口结束才发。
 *
 * 窗口内关闭页面或整页跳转时，用 keepalive 补发彻底删除，避免「点了却没删掉」。
 * 补发失败的方向是安全的：书签还在，最多是多删一次。
 */

import { dismissToast, showToast } from "../components/toast.js";
import { gettext } from "./i18n.js";
import { sendBookmarkAction, updateBookmarkCount } from "./bookmark-action.js";

const UNDO_MS = 5000;
const FADE_MS = 200;

const pending = new Map();

function hideRow(entry) {
  if (!entry.element) return;
  entry.element.style.transition = `opacity ${FADE_MS}ms ease`;
  entry.element.style.opacity = "0";
  entry.hideTimer = window.setTimeout(() => {
    entry.element.replaceWith(entry.placeholder);
  }, FADE_MS);
}

function showRow(entry) {
  window.clearTimeout(entry.hideTimer);
  if (!entry.element) return;
  entry.element.style.opacity = "";
  entry.element.style.transition = "";
  // 淡出动画还没结束就撤销时，节点仍在原位，只要把透明度还原
  if (entry.placeholder.parentNode) {
    entry.placeholder.replaceWith(entry.element);
  }
}

function undo(entry, { rollback = false } = {}) {
  if (pending.get(entry.bookmarkId) !== entry) return;
  pending.delete(entry.bookmarkId);
  dismissToast(entry.toastId);
  showRow(entry);
  updateBookmarkCount(1);

  if (rollback) return;

  // 彻底删除尚未发请求，撤销后无需回滚服务端
  if (entry.action !== "trash") {
    entry.onUndo?.();
    return;
  }
  // 移到回收站要等 restore 落库再回填弹窗，否则弹窗读到的是回收站里的状态
  Promise.resolve(entry.request)
    .then(() =>
      sendBookmarkAction({ bookmarkId: entry.bookmarkId, action: "restore" }),
    )
    .catch(() => showToast(gettext("Could not undo"), { tone: "error" }))
    .finally(() => entry.onUndo?.());
}

function expire(entry) {
  if (pending.get(entry.bookmarkId) !== entry) return;
  pending.delete(entry.bookmarkId);
  dismissToast(entry.toastId);
  if (entry.action !== "remove") return;

  entry.request = sendBookmarkAction({
    bookmarkId: entry.bookmarkId,
    action: "remove",
  }).catch((error) => {
    // 已经被别的页签删掉了：保持移除状态即可
    if (error.status === 404) return;
    showRow(entry);
    updateBookmarkCount(1);
    showToast(gettext("Could not delete the bookmark"), { tone: "error" });
  });
}

export function deleteWithUndo({ bookmarkId, action, element, onUndo }) {
  if (pending.has(bookmarkId)) return null;

  const entry = {
    bookmarkId,
    action,
    element,
    onUndo,
    placeholder: document.createComment("deleted-bookmark"),
    hideTimer: null,
    toastId: null,
    request: null,
  };
  pending.set(bookmarkId, entry);

  hideRow(entry);
  updateBookmarkCount(-1);

  entry.toastId = showToast(
    action === "remove"
      ? gettext("Deleted permanently")
      : gettext("Moved to trash"),
    {
      duration: UNDO_MS,
      action: { label: gettext("Undo"), onClick: () => undo(entry) },
      onTimeout: () => expire(entry),
      onClose: () => expire(entry),
    },
  );

  if (action === "trash") {
    entry.request = sendBookmarkAction({
      bookmarkId,
      action: "trash",
      keepalive: true,
    }).catch((error) => {
      if (error.status === 404) return;
      undo(entry, { rollback: true });
      showToast(gettext("Could not delete the bookmark"), { tone: "error" });
    });
  }

  return entry;
}

// 整页卸载前把窗口内的彻底删除补发出去：不 await，交给 keepalive 送达
function flush() {
  pending.forEach((entry) => {
    if (entry.action !== "remove") return;
    sendBookmarkAction({
      bookmarkId: entry.bookmarkId,
      action: "remove",
      keepalive: true,
    }).catch(() => {});
  });
  pending.clear();
}

document.addEventListener("pagehide", flush);

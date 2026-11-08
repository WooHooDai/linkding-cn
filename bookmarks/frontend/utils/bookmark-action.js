/**
 * 书签状态变更：统一处理 API 调用 + DOM 更新 + 失败回滚
 *
 * @param {Object} options
 * @param {string}   options.bookmarkId
 * @param {string}   options.action      - "share"|"unshare"|"mark_as_read"|"mark_as_unread"|"archive"|"unarchive"|"trash"|"restore"|"remove"
 * @param {Function} [options.onOptimistic] - 乐观更新回调（同步执行）
 * @param {Function} [options.onRollback]   - 失败回滚回调
 */
import { getCSRFToken } from "./csrf.js";

const FIELD_ACTIONS = ["share", "unshare", "mark_as_read", "mark_as_unread"];

/**
 * 只发请求，不碰 DOM。供需要自己控制行状态（如可撤销删除）的调用方复用。
 * keepalive 用于页面即将卸载时的补发：请求不随文档销毁而取消。
 */
export async function sendBookmarkAction({
  bookmarkId,
  action,
  keepalive = false,
}) {
  const isFieldAction = FIELD_ACTIONS.includes(action);
  const response = await fetch(resolveUrl(bookmarkId, action), {
    method: isFieldAction ? "PATCH" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken(),
    },
    ...(isFieldAction ? { body: JSON.stringify(resolveBody(action)) } : {}),
    keepalive,
  });
  if (!response.ok) {
    const error = new Error(`${action} failed`);
    error.status = response.status;
    throw error;
  }
  return response;
}

export async function handleBookmarkAction({ bookmarkId, action, onOptimistic, onRollback }) {
  const isFieldAction = FIELD_ACTIONS.includes(action);
  const isStateToggle = ["archive", "unarchive"].includes(action);
  const isDestructive = ["trash", "restore", "remove"].includes(action);

  // 乐观更新
  if (onOptimistic) onOptimistic();

  // 隐藏不属于当前页面的列表项
  const item = document.querySelector(`li[data-bookmark-id="${bookmarkId}"]`);
  if (item && (isFieldAction || isStateToggle)) {
    const state = resolveState(bookmarkId, action);
    item.style.display = itemBelongsToPage(state) ? "" : "none";
  }

  // 淡出 + 移除
  if (item && isDestructive) {
    item.style.transition = "opacity 0.2s ease";
    item.style.opacity = "0";
  }

  try {
    await sendBookmarkAction({ bookmarkId, action });

    // 成功：移除已淡出的元素
    if (isDestructive && item) {
      setTimeout(() => {
        item.remove();
        updateBookmarkCount(-1);
      }, 200);
    }

  } catch {
    // 回滚
    if (item) {
      item.style.opacity = "";
      item.style.display = "";
    }
    if (onRollback) onRollback();
  }
}

// ---- 当前页面是否还需要这个书签 ----

const PAGE = {
  get active()  { return !location.pathname.includes("/archived") && !location.pathname.includes("/shared") && !location.pathname.includes("/trash"); },
  get archive() { return location.pathname.includes("/archived"); },
  get shared()  { return location.pathname.includes("/shared"); },
  get trash()   { return location.pathname.includes("/trash"); },
};

function itemBelongsToPage(state) {
  if (state.is_deleted !== undefined && state.is_deleted && !PAGE.trash) return false;
  if (state.is_deleted !== undefined && !state.is_deleted && PAGE.trash) return false;
  if (state.is_archived !== undefined && state.is_archived && PAGE.active) return false;
  if (state.is_archived !== undefined && !state.is_archived && PAGE.archive) return false;
  if (state.shared !== undefined && !state.shared && PAGE.shared) return false;
  return true;
}

// ---- 根据 action 推算出目标状态 ----

function resolveState(bookmarkId, action) {
  switch (action) {
    case "archive":        return { is_archived: true };
    case "unarchive":      return { is_archived: false };
    case "share":          return { shared: true };
    case "unshare":        return { shared: false };
    case "trash":          return { is_deleted: true };
    case "restore":        return { is_deleted: false };
    case "remove":         return { is_deleted: true };
    case "mark_as_read":   return {};
    case "mark_as_unread": return {};
    default:               return {};
  }
}

// ---- API 路径 / 请求体 ----

function resolveUrl(bookmarkId, action) {
  const base = `/api/bookmarks/${bookmarkId}`;
  if (["share", "unshare", "mark_as_read", "mark_as_unread"].includes(action)) {
    return `${base}/`;
  }
  return `${base}/${action}/`;
}

function resolveBody(action) {
  switch (action) {
    case "share":          return { shared: true };
    case "unshare":        return { shared: false };
    case "mark_as_read":   return { unread: false };
    case "mark_as_unread": return { unread: true };
    default:               return null;
  }
}

// ---- 工具函数 ----

export function updateBookmarkCount(delta) {
  const countEl = document.getElementById("bookmark-list-total");
  if (!countEl) return;
  const match = countEl.textContent.match(/\((\d+)\)/);
  if (match) {
    const newCount = Math.max(0, parseInt(match[1], 10) + delta);
    countEl.textContent = `(${newCount})`;
  }
}


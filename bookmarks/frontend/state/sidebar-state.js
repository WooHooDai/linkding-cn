// 侧边栏开合状态在 localStorage（优先）与 cookie（服务端据此决定是否懒加载）里。
// 「常驻」存的是默认值，改默认值必须对齐这两处，否则重载后被旧状态顶回去。
export const SIDEBAR_SCOPE_BOOKMARKS = "bookmarks";
export const SIDEBAR_SCOPE_HIGHLIGHTS = "highlights";

export function getSidebarScope(root = document) {
  return root.querySelector(".highlights-page")
    ? SIDEBAR_SCOPE_HIGHLIGHTS
    : SIDEBAR_SCOPE_BOOKMARKS;
}

export function syncSidebarStateCache(scope, enabled) {
  try {
    localStorage.removeItem("ld:sidebar-state:bookmarks");
    localStorage.removeItem("ld:sidebar-state:highlights");
  } catch {
    // 隐私模式下 localStorage 不可用，忽略
  }
  const cookieName =
    scope === SIDEBAR_SCOPE_HIGHLIGHTS
      ? "ld_sidebar_highlights"
      : "ld_sidebar_bookmarks";
  document.cookie = `${cookieName}=${enabled ? "1" : "0"}; path=/; max-age=31536000; SameSite=Lax`;
}

// 侧边栏开合状态在 localStorage（优先）与 cookie（服务端据此决定是否懒加载）里。
// 「常驻」存的是默认值，改默认值必须对齐这两处，否则重载后被旧状态顶回去。
export const SIDEBAR_SCOPE_BOOKMARKS = "bookmarks";
export const SIDEBAR_SCOPE_HIGHLIGHTS = "highlights";

// 书签页与高亮页的开合记忆各存一份，彼此独立
const STATE_STORAGE_KEYS = {
  [SIDEBAR_SCOPE_BOOKMARKS]: "ld:sidebar-state:bookmarks",
  [SIDEBAR_SCOPE_HIGHLIGHTS]: "ld:sidebar-state:highlights",
};

const STATE_COOKIE_NAMES = {
  [SIDEBAR_SCOPE_BOOKMARKS]: "ld_sidebar_bookmarks",
  [SIDEBAR_SCOPE_HIGHLIGHTS]: "ld_sidebar_highlights",
};

export function getSidebarScope(root = document) {
  return root.querySelector(".highlights-page")
    ? SIDEBAR_SCOPE_HIGHLIGHTS
    : SIDEBAR_SCOPE_BOOKMARKS;
}

// 只清除指定那一侧的记忆：改书签的开合默认值不该影响高亮页记住的状态
export function clearSidebarState(scope) {
  try {
    localStorage.removeItem(
      STATE_STORAGE_KEYS[scope] || STATE_STORAGE_KEYS[SIDEBAR_SCOPE_BOOKMARKS],
    );
  } catch {
    // 隐私模式下 localStorage 不可用，忽略
  }
}

export function syncSidebarStateCache(scope, enabled) {
  clearSidebarState(scope);
  const cookieName =
    STATE_COOKIE_NAMES[scope] || STATE_COOKIE_NAMES[SIDEBAR_SCOPE_BOOKMARKS];
  document.cookie = `${cookieName}=${enabled ? "1" : "0"}; path=/; max-age=31536000; SameSite=Lax`;
}

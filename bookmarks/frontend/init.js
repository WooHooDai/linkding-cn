import { syncPanelSpace } from "./utils/panel-space.js";

/**
 * 侧边栏状态初始化
 *
 * 规则：localStorage 优先，data-sidebar-default 兜底。
 * 在 turbo:load 时执行，确保每次页面加载都同步状态。
 */
function initSidebarState() {
  const page = document.querySelector(".bookmarks-page, .highlights-page");
  if (!page) return;

  const isHighlights = page.classList.contains("highlights-page");
  const storageKey = isHighlights ? "ld:sidebar-state:highlights" : "ld:sidebar-state:bookmarks";

  let stored;
  try { stored = localStorage.getItem(storageKey); } catch (e) { stored = null; }

  // localStorage 优先；不存在时：移动端默认关闭（覆盖式侧边栏），桌面端读取服务器设置
  const shouldOpen = stored !== null
    ? stored === "1"
    : window.innerWidth > 840 && page.dataset.sidebarDefault === "1";

  page.classList.remove("sidebar-open", "sidebar-closed", "sidebar-visible");
  if (shouldOpen) {
    page.classList.add("sidebar-open", "sidebar-visible");
    // 如果侧边栏内容是懒加载的占位符，立即加载
    const sidebar = page.querySelector(".sidebar");
    if (sidebar && sidebar.querySelector("[data-sidebar-lazy-placeholder]")) {
      let retries = 0;
      const tryLoad = () => {
        if (typeof window.loadSidebarContent === "function") {
          window.loadSidebarContent(page);
        } else if (++retries < 100) {
          setTimeout(tryLoad, 10);
        }
      };
      tryLoad();
    }
  } else {
    page.classList.add("sidebar-closed");
  }
  
  // 同步侧边栏状态到 Cookie，让服务端判断是否需要懒加载
  try {
    const cookieName = isHighlights ? "ld_sidebar_highlights" : "ld_sidebar_bookmarks";
    document.cookie = `${cookieName}=${shouldOpen ? "1" : "0"}; path=/; max-age=31536000; SameSite=Lax`;
  } catch {}

  // 开合状态一变就要重算让位，否则首帧会先按「关闭」排版再跳一下
  syncPanelSpace();
}

document.addEventListener("turbo:load", initSidebarState);

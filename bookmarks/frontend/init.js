import { primePanelSpace, syncPanelSpace } from "./utils/panel-space.js";
import { isDrawerRememberedOpen } from "./components/layout-drawer.js";

/**
 * 侧边栏状态初始化
 *
 * 规则：localStorage 优先，data-sidebar-default 兜底。
 * 在 turbo:load 时执行，确保每次页面加载都同步状态。
 */
function sidebarStorageKey(page) {
  return page.classList.contains("highlights-page")
    ? "ld:sidebar-state:highlights"
    : "ld:sidebar-state:bookmarks";
}

function sidebarCookieName(page) {
  return page.classList.contains("highlights-page")
    ? "ld_sidebar_highlights"
    : "ld_sidebar_bookmarks";
}

// 把开合状态落到页面根节点上，返回最终是否展开
function applySidebarState(page) {
  let stored = null;
  try {
    stored = localStorage.getItem(sidebarStorageKey(page));
  } catch {
    stored = null;
  }

  // localStorage 优先；不存在时：移动端默认关闭（覆盖式侧边栏），桌面端读取服务器设置
  const shouldOpen =
    stored !== null
      ? stored === "1"
      : window.innerWidth > 840 && page.dataset.sidebarDefault === "1";

  page.classList.remove("sidebar-open", "sidebar-closed", "sidebar-visible");
  if (shouldOpen) {
    page.classList.add("sidebar-open", "sidebar-visible");
  } else {
    page.classList.add("sidebar-closed");
  }
  return shouldOpen;
}

function initSidebarState() {
  const page = document.querySelector(".bookmarks-page, .highlights-page");
  if (!page) return;

  const shouldOpen = applySidebarState(page);

  // 如果侧边栏内容是懒加载的占位符，立即加载
  if (shouldOpen) {
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
  }

  // 同步侧边栏状态到 Cookie，让服务端判断是否需要懒加载
  try {
    const cookieName = sidebarCookieName(page);
    document.cookie = `${cookieName}=${shouldOpen ? "1" : "0"}; path=/; max-age=31536000; SameSite=Lax`;
  } catch {}

  // 开合状态一变就要重算让位，否则首帧会先按「关闭」排版再跳一下
  syncPanelSpace();
}

// Turbo 换 body 之前把会跨页保持的东西一次性摆好：侧边栏开合、抽屉开合、面板让位。
// 否则首帧先按服务端默认（全宽、无面板）排一遍，等 turbo:load / 抽屉行为恢复后再推挤，
// 表格列宽会跟着重算两三次。
function primeIncomingPage(event) {
  const newBody = event.detail.newBody;
  if (!newBody) return;

  const page = newBody.querySelector(".bookmarks-page, .highlights-page");
  if (page) applySidebarState(page);

  const drawer = newBody.querySelector("[data-layout-drawer]");
  const drawerOpen = Boolean(drawer) && isDrawerRememberedOpen();
  if (drawer) {
    drawer.classList.toggle("is-open", drawerOpen);
    if (drawerOpen) {
      drawer.removeAttribute("inert");
      drawer.setAttribute("aria-hidden", "false");
    } else {
      drawer.setAttribute("inert", "");
      drawer.setAttribute("aria-hidden", "true");
    }
  }
  newBody.classList.toggle("layout-drawer-open", drawerOpen);

  // 让位类先在未插入的 body 上算好，插入时就是终态；这一帧也不要过渡
  newBody.classList.add("panel-space-no-anim");
  primePanelSpace(newBody, drawerOpen);
}

document.addEventListener("turbo:before-render", primeIncomingPage);

document.addEventListener("turbo:load", initSidebarState);

// 首帧已经就位，恢复后续交互该有的过渡
document.addEventListener("turbo:load", () => {
  const body = document.body;
  requestAnimationFrame(() =>
    requestAnimationFrame(() => body.classList.remove("panel-space-no-anim")),
  );
});

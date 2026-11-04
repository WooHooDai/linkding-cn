import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";

// panel-space.js 顶层读 window/document、注册监听，所以先铺最小环境再打包加载。
// 这里只验证「推 / 浮 / 让位到留白」三档判据，像素交给实机脚本。
const bodyClasses = new Set();
let env = {};

globalThis.requestAnimationFrame = (fn) => fn();

function makeDocument() {
  return {
    documentElement: { clientWidth: env.width },
    body: {
      classList: {
        add: (name) => bodyClasses.add(name),
        remove: (...names) => names.forEach((name) => bodyClasses.delete(name)),
        toggle: (name, on) => {
          if (on) bodyClasses.add(name);
          else bodyClasses.delete(name);
        },
        contains: (name) => bodyClasses.has(name),
      },
    },
    addEventListener: () => {},
    querySelector: (selector) => {
      if (selector.includes("layout-drawer.is-open")) {
        return env.drawerOpen ? { id: "layout-drawer" } : null;
      }
      if (selector.includes("bookmarks-page")) {
        if (!env.page) return null;
        return {
          dataset: { sidebarDefault: env.sidebarDefault ?? "0" },
          classList: {
            contains: (name) =>
              name === "sidebar-open" ? Boolean(env.sidebarOpen) : false,
          },
        };
      }
      if (selector.includes("container")) {
        return { nodeType: 1 };
      }
      return null;
    },
  };
}

globalThis.getComputedStyle = () => ({ maxWidth: env.maxWidth ?? "960px" });
globalThis.addEventListener = () => {};
globalThis.window = {
  matchMedia: (query) => ({
    matches: Boolean(env.mobile),
    media: query,
    addEventListener() {},
    removeEventListener() {},
  }),
  addEventListener: () => {},
  dispatchEvent: () => true,
  getComputedStyle: () => ({ maxWidth: env.maxWidth ?? "960px" }),
};
globalThis.document = makeDocument();

const result = buildSync({
  entryPoints: ["bookmarks/frontend/utils/panel-space.js"],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const { syncPanelSpace } = await import(
  "data:text/javascript;base64," +
    Buffer.from(result.outputFiles[0].text).toString("base64")
);

function decide({
  width,
  drawerOpen = false,
  sidebarOpen = false,
  mobile = false,
  sidebarDefault = "0",
}) {
  bodyClasses.clear();
  env = { width, drawerOpen, sidebarOpen, mobile, sidebarDefault, page: true };
  globalThis.document = makeDocument();
  syncPanelSpace();
  return {
    drawerPush: bodyClasses.has("layout-drawer-push"),
    sidebarPush: bodyClasses.has("sidebar-push"),
  };
}

test("超宽屏：面板落进留白，内容不动", () => {
  // 1920 - 960 = 960 留白，两侧各 480，360/340 都放得下
  assert.deepEqual(decide({ width: 1920, drawerOpen: true }), {
    drawerPush: false,
    sidebarPush: false,
  });
  assert.deepEqual(decide({ width: 1920, sidebarOpen: true }), {
    drawerPush: false,
    sidebarPush: false,
  });
});

test("中宽屏：让位但不挤窄列表", () => {
  // 1600 两侧留白各 320，装不下 340/360，但让位后仍有 1240/1260
  assert.deepEqual(decide({ width: 1600, drawerOpen: true }), {
    drawerPush: true,
    sidebarPush: false,
  });
  assert.deepEqual(decide({ width: 1600, sidebarOpen: true }), {
    drawerPush: false,
    sidebarPush: true,
  });
});

test("两个面板同时打开：放得下就都让位", () => {
  assert.deepEqual(decide({ width: 1600, drawerOpen: true, sidebarOpen: true }), {
    drawerPush: true,
    sidebarPush: true,
  });
});

test("两个面板同时打开：放不下时只让抽屉，侧边栏浮层", () => {
  // 1280 - 360 - 340 = 580 < 840
  assert.deepEqual(decide({ width: 1280, drawerOpen: true, sidebarOpen: true }), {
    drawerPush: true,
    sidebarPush: false,
  });
});

test("宽度不足：不推页面，改为浮层覆盖", () => {
  // 1000 - 360 = 640 / 1000 - 340 = 660，都低于 720
  assert.deepEqual(decide({ width: 1000, drawerOpen: true, sidebarOpen: true }), {
    drawerPush: false,
    sidebarPush: false,
  });
});

test("移动端：不做让位（面板本身就是全宽浮层）", () => {
  assert.deepEqual(
    decide({ width: 430, drawerOpen: true, sidebarOpen: true, mobile: true }),
    { drawerPush: false, sidebarPush: false },
  );
});

test("常驻侧边栏不走让位判断", () => {
  assert.deepEqual(
    decide({ width: 1600, sidebarOpen: true, sidebarDefault: "1" }),
    { drawerPush: false, sidebarPush: false },
  );
});

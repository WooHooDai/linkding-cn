import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";

// selection.js 是 ESM 语法（.js 扩展名），项目默认 CommonJS；用 esbuild 打包成
// 独立 ESM 再经 data URL 加载，不改源文件、不影响浏览器端打包。
function loadSelection() {
  const result = buildSync({
    entryPoints: ["bookmarks/frontend/utils/selection.js"],
    bundle: true,
    format: "esm",
    platform: "node",
    write: false,
  });
  const code = result.outputFiles[0].text;
  return import(
    "data:text/javascript;base64," + Buffer.from(code).toString("base64")
  );
}

const { Selection, sliceRange } = await loadSelection();

test("sliceRange returns the inclusive range in display order", () => {
  const order = ["a", "b", "c", "d"];
  assert.deepEqual(sliceRange(order, "b", "d"), ["b", "c", "d"]);
  assert.deepEqual(sliceRange(order, "d", "b"), ["b", "c", "d"]);
  assert.equal(sliceRange(order, "b", "zzz"), null);
});

test("individual selection toggles membership", () => {
  const selection = new Selection();
  selection.set(["1", "2"], true);
  assert.equal(selection.isSelected("1"), true);
  assert.equal(selection.count(10), 2);
  selection.set("1", false);
  assert.equal(selection.isSelected("1"), false);
  assert.equal(selection.count(10), 1);
});

test("triState reports none/some/all", () => {
  const selection = new Selection();
  assert.equal(selection.triState(["1", "2"]), "none");
  selection.set("1", true);
  assert.equal(selection.triState(["1", "2"]), "some");
  selection.set("2", true);
  assert.equal(selection.triState(["1", "2"]), "all");
  assert.equal(selection.triState([]), "none");
});

test("select-all mode records exclusions instead of ids", () => {
  const selection = new Selection({ selectAll: true });
  assert.equal(selection.count(50), 50);
  selection.set(["7", "8"], false);
  assert.equal(selection.isSelected("7"), false);
  assert.equal(selection.count(50), 48);
  assert.deepEqual(selection.payload([]), {
    selectAcross: true,
    ids: [],
    excludeIds: ["7", "8"],
  });
  selection.set("7", true);
  assert.equal(selection.isSelected("7"), true);
  assert.equal(selection.count(50), 49);
});

test("payload sends only ids that are not rendered as checkboxes", () => {
  const selection = new Selection();
  selection.set(["1", "2", "3"], true);
  assert.deepEqual(selection.payload(["1", "2"]), {
    selectAcross: false,
    ids: ["3"],
    excludeIds: [],
  });
});

test("setAll drops materialised ids in both directions", () => {
  const selection = new Selection();
  selection.set(["1"], true);
  selection.setAll(true);
  assert.equal(selection.count(30), 30);
  assert.equal(selection.isSelected("1"), true);
  selection.set("2", false);
  selection.setAll(false);
  assert.equal(selection.count(30), 0);
  assert.deepEqual(selection.payload([]), {
    selectAcross: false,
    ids: [],
    excludeIds: [],
  });
});

test("state survives a JSON round trip", () => {
  const selection = new Selection({ selectAll: true, excludedIds: ["4"] });
  const restored = Selection.from(JSON.parse(JSON.stringify(selection)));
  assert.equal(restored.selectAll, true);
  assert.equal(restored.isSelected("4"), false);
  assert.equal(restored.count(10), 9);
  assert.equal(Selection.from(null).isEmpty, true);
});

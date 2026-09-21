import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";

// tag-match.js / tag-cache.js 是 ESM 语法（.js 扩展名），项目默认 CommonJS；
// 且 tag-cache.js 顶层依赖 document（api.js 的 dataset 读取 + Cache 构造函数的
// addEventListener）。因此用 esbuild 打包成独立 ESM 再经 data URL 加载，
// 不修改源文件、不影响浏览器端打包。
globalThis.document = {
  documentElement: { dataset: {} },
  addEventListener: () => {},
};

function loadBundled(entryPoint) {
  const result = buildSync({
    entryPoints: [entryPoint],
    bundle: true,
    format: "esm",
    platform: "node",
    write: false,
  });
  const code = result.outputFiles[0].text;
  return import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
}

const { scoreTag } = await loadBundled("bookmarks/frontend/utils/tag-match.js");
const { sortTagsForAutocomplete } = await loadBundled(
  "bookmarks/frontend/utils/tag-cache.js",
);

// ---------- scoreTag ----------

test("scoreTag: name 前缀命中得 0，大小写不敏感", () => {
  const tag = { name: "Docker", pinyin_full: "docker", pinyin_first: "d" };
  assert.equal(scoreTag(tag, "dock"), 0);
});

test("scoreTag: 全拼前缀命中得 1", () => {
  const tag = { name: "最爱", pinyin_full: "zuiai", pinyin_first: "za" };
  assert.equal(scoreTag(tag, "zui"), 1);
});

test("scoreTag: 首字母前缀命中得 2（输入 'za' 只命中首字母，不命中全拼 zuiai）", () => {
  const tag = { name: "最爱", pinyin_full: "zuiai", pinyin_first: "za" };
  assert.equal(scoreTag(tag, "za"), 2);
});

test("scoreTag: name 任意位置子串命中得 3", () => {
  const tag = { name: "docker-compose", pinyin_full: "", pinyin_first: "" };
  assert.equal(scoreTag(tag, "compose"), 3);
});

test("scoreTag: 不匹配得 -1", () => {
  const tag = { name: "docker", pinyin_full: "docker", pinyin_first: "d" };
  assert.equal(scoreTag(tag, "xyz"), -1);
});

test("scoreTag: 拼音不做子串匹配（'ban' 不能命中全拼 muban）", () => {
  const tag = { name: "模板", pinyin_full: "muban", pinyin_first: "mb" };
  assert.equal(scoreTag(tag, "ban"), -1);
});

test("scoreTag: name 子串命中时拼音前缀仍按分值排序（0 < 1 < 2 < 3）", () => {
  const tag = { name: "api-docs", pinyin_full: "api-docs", pinyin_first: "a-d" };
  assert.equal(scoreTag(tag, "api"), 0);
  const fastapi = { name: "fastapi", pinyin_full: "fastapi", pinyin_first: "f" };
  assert.equal(scoreTag(fastapi, "api"), 3);
});

test("scoreTag: 空 pinyin 字段不报错", () => {
  assert.equal(scoreTag({ name: "docker", pinyin_full: null, pinyin_first: null }, "dock"), 0);
});

test("scoreTag: 特殊字符输入无正则转义问题", () => {
  assert.equal(scoreTag({ name: "c++", pinyin_full: "c++", pinyin_first: "c" }, "++"), 3);
  assert.equal(scoreTag({ name: "[docs]", pinyin_full: "[docs]", pinyin_first: "d" }, "["), 0);
});

// ---------- sortTagsForAutocomplete ----------

test("sortTagsForAutocomplete: 按使用频次降序", () => {
  const tags = [
    { name: "a", bookmark_count: 2 },
    { name: "b", bookmark_count: 3 },
    { name: "c", bookmark_count: 1 },
  ];
  assert.deepEqual(
    sortTagsForAutocomplete(tags).map((t) => t.name),
    ["b", "a", "c"],
  );
});

test("sortTagsForAutocomplete: 频次相同时非 CJK 在前", () => {
  const tags = [
    { name: "中国", bookmark_count: 1 },
    { name: "alpha", bookmark_count: 1 },
  ];
  assert.deepEqual(
    sortTagsForAutocomplete(tags).map((t) => t.name),
    ["alpha", "中国"],
  );
});

test("sortTagsForAutocomplete: 同频次同类型按名称升序（不区分大小写）", () => {
  const tags = [
    { name: "Beta", bookmark_count: 1 },
    { name: "alpha", bookmark_count: 1 },
  ];
  assert.deepEqual(
    sortTagsForAutocomplete(tags).map((t) => t.name),
    ["alpha", "Beta"],
  );
});

test("sortTagsForAutocomplete: bookmark_count 缺失视为 0", () => {
  const tags = [{ name: "a" }, { name: "b", bookmark_count: 2 }];
  assert.deepEqual(
    sortTagsForAutocomplete(tags).map((t) => t.name),
    ["b", "a"],
  );
});

test("sortTagsForAutocomplete: 纯函数，不修改入参", () => {
  const tags = [
    { name: "b", bookmark_count: 2 },
    { name: "a", bookmark_count: 3 },
  ];
  const original = [...tags];
  sortTagsForAutocomplete(tags);
  assert.deepEqual(tags, original);
});

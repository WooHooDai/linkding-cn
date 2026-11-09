// 构建 skins/ 下的完整皮肤（default-light / default-dark 是结构层的组成部分，
// 由 theme.css 内联，不单独产出）。
// 保留注释：皮肤文件头的 @skin 元数据在运行时用于列出可用皮肤。
//
// 另外产出 skin-default-export.css：默认皮肤的导出件。明暗源码合并成一份带
// @skin 头的 CSS 只在这里发生，运行时（bookmarks/skins.py）只读它，不再自己算。
const fs = require("fs");
const path = require("path");
const postcss = require("postcss");
const cssnano = require("cssnano");

const root = path.join(__dirname, "..");
const srcDir = path.join(root, "bookmarks", "styles", "skins");
const outDir = path.join(root, "bookmarks", "static", "skins");
// 放在 skins/ 外层，否则会被 skins() 的 glob("*.css") 当成可选皮肤
const exportFile = path.join(root, "bookmarks", "static", "skin-default-export.css");

function mergeDefaultSkin() {
  const light = fs.readFileSync(path.join(srcDir, "default-light.css"), "utf8");
  const dark = fs.readFileSync(path.join(srcDir, "default-dark.css"), "utf8");
  const lightScoped = light.replace(":root", 'html[data-theme="light"]');
  const css =
    "/* @skin\n" +
    '{ "id": "default", "name": "Default", "author": "linkding",\n' +
    '  "version": "1.0.0", "preview": ["#ffffff", "#5a54dc", "#374151"] }\n' +
    "   默认主题的导出版本，由明暗两份源码合并而成。 */\n\n" +
    `${lightScoped.trim()}\n\n${dark.trim()}\n`;
  fs.writeFileSync(exportFile, css);
  console.log(`default skin -> ${path.relative(root, exportFile)}`);
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true });
  const files = fs
    .readdirSync(srcDir)
    .filter((name) => name.endsWith(".css") && !name.startsWith("default-"));

  for (const name of files) {
    const from = path.join(srcDir, name);
    const to = path.join(outDir, name);
    const css = fs.readFileSync(from, "utf8");
    const result = await postcss([
      cssnano({ preset: ["default", { discardComments: false }] }),
    ]).process(css, { from });
    fs.writeFileSync(to, result.css);
    console.log(`skin ${name} -> ${path.relative(root, to)}`);
  }

  console.log(`${files.length} skin(s) built`);
  mergeDefaultSkin();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

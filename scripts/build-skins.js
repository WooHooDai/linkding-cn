// 构建 skins/ 下的完整皮肤（default-light / default-dark 是结构层的组成部分，
// 由 theme.css 内联，不单独产出）。
// 保留注释：皮肤文件头的 @skin 元数据在运行时用于列出可用皮肤。
const fs = require("fs");
const path = require("path");
const postcss = require("postcss");
const cssnano = require("cssnano");

const root = path.join(__dirname, "..");
const srcDir = path.join(root, "bookmarks", "styles", "skins");
const outDir = path.join(root, "bookmarks", "static", "skins");

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
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

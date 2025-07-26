const cssnano = require("cssnano");
const postcssImport = require("postcss-import");
const postcssNesting = require("postcss-nesting");

module.exports = {
  plugins: [
    postcssImport,
    postcssNesting,
    cssnano({
      preset: [
        "default",
        {
          // 禁用 postcss-reduce-initial 插件
          // reduceInitial: false,
          // 或者更精确地控制哪些属性不被优化
          reduceInitial: {
            // 禁用 align-items 的优化
            alignItems: false,
            // 禁用其他可能影响布局的属性
            // display: false,
            // position: false,
          }
        },
      ],
    }),
  ],
};

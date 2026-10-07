/**
 * favicon 对比度兜底。
 *
 * 给「在当前页面背景上几乎看不见」的 favicon（浅色模式下的纯白 logo、
 * 深色模式下的纯黑 logo）加一圈描边，其余图标一概不动。
 * 判定与描边色见 classify()，参数在 914 个真实缓存图标上校准过。
 *
 * 放在前端是必要的：只有浏览器同时拿得到图片像素和图标真实所处的背景色。
 */

const SELECTOR = "img.favicon, img.info-favicon";
const OUTLINE_CLASS = "favicon-outline";

// —— 判定参数 ——
const INK_ALPHA = 0.5; // 算作「字形像素」的不透明度门槛（排除抗锯齿边缘）
const OPAQUE_SKIP = 0.9; // 整块不透明 = 自带底，放过
const VISIBLE_CONTRAST = 1.5; // 合成到背景后对比度低于此值的像素算看不见
const VISIBLE_MIN = 0.1; // 看得见的字形像素低于此比例 -> 需要描边
const SAMPLE_LIMIT = 48; // 采样边长上限

// 0.5px 亚像素光晕会把标称 alpha 打约 5 折，取值要偏高才够看
const RING_DARK = "rgba(0, 0, 0, 0.85)";
const RING_LIGHT = "rgba(255, 255, 255, 0.85)";

let canvasContext = null;

// 同一批 favicon 共享祖先，背景色只算一次
const backgroundCache = new Map();

function getCanvasContext() {
  if (!canvasContext) {
    const canvas = document.createElement("canvas");
    canvasContext = canvas.getContext("2d", { willReadFrequently: true });
  }
  return canvasContext;
}

function srgbToLinear(value) {
  const v = value / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function relativeLuminance(r, g, b) {
  return (
    0.2126 * srgbToLinear(r) +
    0.7152 * srgbToLinear(g) +
    0.0722 * srgbToLinear(b)
  );
}

function contrastRatio(luminanceA, luminanceB) {
  const lighter = Math.max(luminanceA, luminanceB);
  const darker = Math.min(luminanceA, luminanceB);
  return (lighter + 0.05) / (darker + 0.05);
}

/** 解析 getComputedStyle 返回的 rgb()/rgba()，解析不了返回 null */
function parseColor(value) {
  const match = /^rgba?\(([^)]+)\)$/.exec(value);
  if (!match) return null;
  const parts = match[1]
    .split(/[,/\s]+/)
    .filter(Boolean)
    .map(Number);
  if (parts.length < 3 || parts.some(Number.isNaN)) return null;
  return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
}

/**
 * 把祖先链上的 background-color 逐层合成，得到该元素实际所处的背景色。
 * 不读主题变量，所以新增主题、用户自定义 CSS 改背景都自动成立。
 */
function resolveBackground(element) {
  if (!element) return [255, 255, 255];
  const cached = backgroundCache.get(element);
  if (cached) return cached;

  const background = resolveBackground(element.parentElement);
  const own = parseColor(getComputedStyle(element).backgroundColor);
  let result = background;
  if (own && own[3] > 0) {
    if (own[3] >= 0.999) {
      result = [own[0], own[1], own[2]];
    } else {
      result = [
        own[0] * own[3] + background[0] * (1 - own[3]),
        own[1] * own[3] + background[1] * (1 - own[3]),
        own[2] * own[3] + background[2] * (1 - own[3]),
      ];
    }
  }
  backgroundCache.set(element, result);
  return result;
}

/**
 * 图标与背景合成后，还有多少比例的字形像素是看得见的。
 * 接近 0 说明整个图标消失了，需要描边。
 */
function classify(imageData, background) {
  const pixels = imageData.data;
  const count = imageData.width * imageData.height;
  const backgroundLuminance = relativeLuminance(...background);

  let alphaSum = 0;
  let inkCount = 0;
  let visibleCount = 0;

  for (let i = 0; i < count; i++) {
    const alpha = pixels[i * 4 + 3] / 255;
    alphaSum += alpha;
    if (alpha < INK_ALPHA) continue;
    inkCount++;

    const r = pixels[i * 4] * alpha + background[0] * (1 - alpha);
    const g = pixels[i * 4 + 1] * alpha + background[1] * (1 - alpha);
    const b = pixels[i * 4 + 2] * alpha + background[2] * (1 - alpha);
    if (
      contrastRatio(relativeLuminance(r, g, b), backgroundLuminance) >=
      VISIBLE_CONTRAST
    ) {
      visibleCount++;
    }
  }

  const meanAlpha = alphaSum / count;
  if (meanAlpha > OPAQUE_SKIP || !inkCount) {
    return { need: false, visibleFrac: 1, meanAlpha };
  }

  const visibleFrac = visibleCount / inkCount;
  return { need: visibleFrac < VISIBLE_MIN, visibleFrac, meanAlpha };
}

/** 按采样上限把图标画到复用 canvas 上，取回像素 */
function measure(img) {
  const naturalWidth = img.naturalWidth;
  const naturalHeight = img.naturalHeight;
  if (!naturalWidth || !naturalHeight) return null;

  const scale = Math.min(
    1,
    SAMPLE_LIMIT / Math.max(naturalWidth, naturalHeight),
  );
  const width = Math.max(1, Math.round(naturalWidth * scale));
  const height = Math.max(1, Math.round(naturalHeight * scale));

  const context = getCanvasContext();
  context.canvas.width = width;
  context.canvas.height = height;
  context.drawImage(img, 0, 0, width, height);
  return context.getImageData(0, 0, width, height);
}

async function apply(img) {
  if (img.dataset.faviconOutlineChecked) return;
  img.dataset.faviconOutlineChecked = "1";

  try {
    if (!img.complete || !img.naturalWidth) {
      await img.decode();
    }
    if (!img.naturalWidth) return;

    const background = resolveBackground(img.parentElement);
    const imageData = measure(img);
    if (!imageData) return;

    const { need } = classify(imageData, background);
    if (!need) return;

    // 描边要和背景反着来才看得见
    img.style.setProperty(
      "--favicon-outline-color",
      relativeLuminance(...background) > 0.5 ? RING_DARK : RING_LIGHT,
    );
    img.classList.add(OUTLINE_CLASS);
  } catch {
    // 读像素失败（如跨域图污染 canvas）时保持原样，不影响页面
  }
}

function scan(root) {
  root.querySelectorAll(SELECTOR).forEach(apply);
}

function scanDocument() {
  backgroundCache.clear();
  scan(document);
}

// img 的 load 不冒泡，从 document 捕获即可覆盖 Turbo Stream / 阅读器侧栏等动态插入的图标
document.addEventListener(
  "load",
  (event) => {
    if (
      event.target instanceof HTMLImageElement &&
      event.target.matches(SELECTOR)
    ) {
      apply(event.target);
    }
  },
  true,
);

document.addEventListener("turbo:load", scanDocument);
document.addEventListener("turbo:render", scanDocument);

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", scanDocument, { once: true });
} else {
  scanDocument();
}

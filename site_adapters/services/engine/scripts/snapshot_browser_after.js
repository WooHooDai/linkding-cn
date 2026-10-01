const fs = require("fs");
const vm = require("vm");
const { parseHTML } = require("linkedom");

const input = JSON.parse(fs.readFileSync("/dev/stdin", "utf8"));
const { scriptPath, url, config, outputPath } = input;

const source = fs.readFileSync(scriptPath, "utf8");
const html = fs.readFileSync(outputPath, "utf8");
const { document, window } = parseHTML(html);

// `after` runs against a Linkedom DOM, not a real browser. Attribute/property
// reflection is incomplete, so scripts must use setAttribute/getAttribute for
// values that need to persist into the saved HTML.
//
// Linkedom is lightweight and fast, but if after hooks increasingly rely on
// browser-style property reflection or more complete DOM behavior, consider
// switching this runner to jsdom. If layout or real media behavior is needed,
// use a real browser instead.

const context = {
  document,
  window,
  console,
  setTimeout,
  clearTimeout,
  URL,
  fetch,
};
context.globalThis = context;
context.Node = window.Node || globalThis.Node;
context.CustomEvent = window.CustomEvent || globalThis.CustomEvent;
context.HTMLElement = window.HTMLElement || globalThis.HTMLElement;

vm.createContext(context);
vm.runInContext(source, context);

const after = vm.runInContext(
  "typeof after !== 'undefined' ? after : null",
  context
);

(async () => {
  // Only rewrite the file when the after hook actually mutated the DOM:
  // linkedom serialization is not lossless (attribute quote reordering, lost
  // comments/noscript), so SingleFile's complete snapshot must not be
  // re-serialized on a no-op hook.
  // NB: do not name these locals `after` — that would shadow the hook resolved
  // from the vm context below and hit a temporal-dead-zone error.
  const beforeHtml = document.toString();
  if (typeof after === "function") {
    await after(url, config);
  }
  const afterHtml = document.toString();
  if (afterHtml !== beforeHtml) {
    fs.writeFileSync(outputPath, afterHtml);
  }
})().catch((error) => {
  process.stderr.write("Snapshot after hook error: " + (error.stack || error.message) + "\n");
  process.exitCode = 1;
});

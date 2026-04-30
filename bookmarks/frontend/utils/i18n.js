export function gettext(message) {
  return typeof window.gettext === "function" ? window.gettext(message) : message;
}

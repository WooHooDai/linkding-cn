export function stripPreferenceQueryParams(href, queryParams) {
  const visibleUrl = new URL(href, window.location.origin);

  queryParams.forEach((key) => {
    visibleUrl.searchParams.delete(key);
  });

  return visibleUrl.toString();
}

export function applyRequestHeaders(headerBag, requestHeaders) {
  if (headerBag instanceof Headers) {
    Object.entries(requestHeaders).forEach(([key, value]) => {
      headerBag.set(key, value);
    });
    return;
  }

  Object.assign(headerBag, requestHeaders);
}

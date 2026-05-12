(function () {
  const bookmarkUrl = window.location;
  const title =
    document.querySelector('title')?.textContent ||
    document.querySelector('meta[property=&quot;og:title&quot;]')?.getAttribute('content') ||
    '';
  const description =
    document.querySelector('meta[name=&quot;description&quot;]')?.getAttribute('content') ||
    document
      .querySelector('meta[property=&quot;og:description&quot;]')
      ?.getAttribute('content') ||
    '';

  let applicationUrl = '{{ application_url }}';
  applicationUrl += '?url=' + encodeURIComponent(bookmarkUrl);
  applicationUrl += '&title=' + encodeURIComponent(title);
  applicationUrl += '&description=' + encodeURIComponent(description);
  applicationUrl += '&auto_close';

  window.open(applicationUrl);
})();

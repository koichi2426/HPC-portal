/**
 * HPC Portal: 画面共通のXSRF取得とJSON API通信を提供する。
 */
(function (global) {
  "use strict";

  var portal = global.HpcPortal || {};

  function readXsrfCookie() {
    var match = global.document.cookie.match(/(?:^|; )_xsrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function apiUrl(url) {
    var xsrf = readXsrfCookie();
    if (!xsrf || /(?:^|[?&])_xsrf=/.test(url)) return url;
    return url + (url.indexOf("?") === -1 ? "?" : "&") +
      "_xsrf=" + encodeURIComponent(xsrf);
  }

  function apiHeaders(headers) {
    var result = Object.assign(
      { "Content-Type": "application/json", "Accept": "application/json" },
      headers || {}
    );
    var xsrf = readXsrfCookie();
    if (xsrf) result["X-XSRFToken"] = xsrf;
    return result;
  }

  function requestJson(url, options) {
    var requestOptions = Object.assign(
      { credentials: "same-origin", cache: "no-store" },
      options || {}
    );
    if (requestOptions.method && requestOptions.method.toUpperCase() !== "GET") {
      requestOptions.headers = apiHeaders(requestOptions.headers);
      url = apiUrl(url);
    }
    return global.fetch(url, requestOptions).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          throw new Error(body.error || body.message || ("HTTP " + response.status));
        }
        return body;
      });
    });
  }

  portal.readXsrfCookie = readXsrfCookie;
  portal.apiUrl = apiUrl;
  portal.apiHeaders = apiHeaders;
  portal.requestJson = requestJson;
  global.HpcPortal = portal;
})(window);

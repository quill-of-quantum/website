(() => {
  if (window.__netMetricsSent) return;
  window.__netMetricsSent = true;

  function buildPayload() {
    if (!window.performance || !performance.getEntriesByType) return null;
    const nav = performance.getEntriesByType("navigation")[0];
    if (!nav) return null;
    const dns = Math.max(0, nav.domainLookupEnd - nav.domainLookupStart);
    const connect = Math.max(0, nav.connectEnd - nav.connectStart);
    const ttfb = Math.max(0, nav.responseStart - nav.requestStart);
    const download = Math.max(0, nav.responseEnd - nav.responseStart);
    const total = Math.max(0, nav.responseEnd - nav.startTime);
    return {
      dns_ms: dns,
      connect_ms: connect,
      ttfb_ms: ttfb,
      download_ms: download,
      total_ms: total
    };
  }

  function sendMetrics() {
    const payload = buildPayload();
    if (!payload) return;
    const url = "/api/metrics/network";
    const body = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: "application/json" });
      navigator.sendBeacon(url, blob);
      return;
    }
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true
    }).catch(() => {});
  }

  if (document.readyState === "complete") {
    sendMetrics();
  } else {
    window.addEventListener("load", sendMetrics, { once: true });
  }
})();

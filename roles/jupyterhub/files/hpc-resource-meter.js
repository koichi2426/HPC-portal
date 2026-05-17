/**
 * HPC Portal: リソースメーター自動更新（5秒間隔）
 * コンテナに data-hpc-resource-meter を付けた要素を対象にする。
 */
(function (global) {
  "use strict";

  var INTERVAL_MS = 5000;
  var API_URL = "/hub/hpc-resource-status";

  function format1(value) {
    return Number(value || 0).toFixed(1);
  }

  function buildTextMap(data) {
    return {
      cpu_status: data.cpu_status,
      cpu_available_count: "残り " + format1(data.cpu_available_count) + " vCPU",
      cpu_total: "最大 " + (data.cpu_total || 0) + " vCPU",
      mem_status: data.mem_status,
      mem_available_gb: "残り " + format1(data.mem_available_gb) + " GB",
      mem_total_gb: "最大 " + format1(data.mem_total_gb) + " GB",
      disk_status: data.disk_status,
      disk_available_gb: "残り " + format1(data.disk_available_gb) + " GB",
      disk_total_gb: "最大 " + format1(data.disk_total_gb) + " GB",
      gpu_status: data.gpu_status,
      gpu_available_count:
        "空き " + (data.gpu_available_count || 0) + "/" + (data.gpu_max || 0) + " GPU",
      gpu_vram_available_gb:
        "VRAM " +
        format1(data.gpu_vram_available_gb) +
        "/" +
        format1(data.gpu_vram_total_gb) +
        " GB",
    };
  }

  function formatUpdatedAt(data) {
    if (data && data.updated_at) {
      try {
        return (
          "最終更新 " +
          new Date(Number(data.updated_at) * 1000).toLocaleTimeString("ja-JP")
        );
      } catch (e) {
        /* fall through */
      }
    }
    return "最終更新 " + new Date().toLocaleTimeString("ja-JP");
  }

  function apply(root, data) {
    var text = buildTextMap(data);
    root.querySelectorAll("[data-resource-text]").forEach(function (el) {
      var key = el.getAttribute("data-resource-text");
      if (text[key] !== undefined) {
        el.textContent = text[key];
      }
    });
    root.querySelectorAll("[data-resource-width]").forEach(function (el) {
      var key = el.getAttribute("data-resource-width");
      var pct = Math.max(0, Math.min(100, Number(data[key] || 0)));
      el.style.width = pct.toFixed(0) + "%";
    });
    root.querySelectorAll("[data-resource-updated-at]").forEach(function (el) {
      el.textContent = formatUpdatedAt(data);
      el.classList.remove("hpc-resource-stale", "hpc-resource-loading");
      el.classList.add("hpc-resource-live");
    });
    root.classList.add("hpc-resource-refreshed");
    global.setTimeout(function () {
      root.classList.remove("hpc-resource-refreshed");
    }, 450);
  }

  function setLoading(root) {
    root.querySelectorAll("[data-resource-updated-at]").forEach(function (el) {
      if (!el.classList.contains("hpc-resource-live")) {
        el.textContent = "取得中…";
      }
      el.classList.add("hpc-resource-loading");
      el.classList.remove("hpc-resource-stale");
    });
  }

  function setError(root) {
    root.querySelectorAll("[data-resource-updated-at]").forEach(function (el) {
      el.textContent = "更新失敗（5秒後に再試行）";
      el.classList.add("hpc-resource-stale");
      el.classList.remove("hpc-resource-loading", "hpc-resource-live");
    });
  }

  function refresh(root) {
    setLoading(root);
    return global
      .fetch(API_URL, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("HTTP " + r.status);
        }
        return r.json();
      })
      .then(function (data) {
        apply(root, data);
        return data;
      })
      .catch(function () {
        setError(root);
      });
  }

  function start(root, opts) {
    if (!root) {
      return null;
    }
    var interval = (opts && opts.intervalMs) || INTERVAL_MS;
    refresh(root);
    var timer = global.setInterval(function () {
      refresh(root);
    }, interval);
    return {
      stop: function () {
        global.clearInterval(timer);
      },
    };
  }

  function startAll() {
    global.document.querySelectorAll("[data-hpc-resource-meter]").forEach(function (root) {
      start(root);
    });
  }

  global.HpcResourceMeter = {
    start: start,
    startAll: startAll,
    refresh: refresh,
    INTERVAL_MS: INTERVAL_MS,
  };

  function onReady() {
    startAll();
  }

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
})(window);

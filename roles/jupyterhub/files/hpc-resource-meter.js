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
      mem_used_gb: format1(data.mem_used_gb) + " GB",
      mem_total_gb: "最大 " + format1(data.mem_total_gb) + " GB",
      disk_status: data.disk_status,
      disk_available_gb: "残り " + format1(data.disk_available_gb) + " GB",
      disk_total_gb: "最大 " + format1(data.disk_total_gb) + " GB",
    };
  }

  function renderGpuProcesses(root, data) {
    var processes = Array.isArray(data.gpu_processes) ? data.gpu_processes : [];
    var available = data.gpu_processes_available !== false;
    root.querySelectorAll("[data-gpu-process-count]").forEach(function (el) {
      el.textContent = available ? "利用中 " + processes.length + "件" : "取得できません";
    });
    root.querySelectorAll("[data-gpu-process-list]").forEach(function (list) {
      list.replaceChildren();
      if (!available || processes.length === 0) {
        var empty = global.document.createElement("li");
        empty.className = "hpc-gpu-process-empty";
        empty.textContent = available ? "GPUを使用中のプロセスはありません" : "GPUプロセス情報を取得できません";
        list.appendChild(empty);
        return;
      }
      processes.forEach(function (process) {
        var item = global.document.createElement("li");
        item.className = "hpc-gpu-process-item";
        var name = global.document.createElement("span");
        name.className = "hpc-gpu-process-name";
        name.textContent = String(process.name || "不明");
        var meta = global.document.createElement("span");
        meta.className = "hpc-gpu-process-meta";
        meta.textContent = String(process.username || "不明") + " · PID " + String(process.pid || "—");
        item.appendChild(name);
        item.appendChild(meta);
        list.appendChild(item);
      });
    });
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
    renderGpuProcesses(root, data);
    root.querySelectorAll("[data-resource-updated-at]").forEach(function (el) {
      el.textContent = formatUpdatedAt(data);
      el.classList.remove("hpc-resource-stale", "hpc-resource-loading");
      el.classList.add("hpc-resource-live");
    });
    root.querySelectorAll("[data-resource-refresh-live]").forEach(function (el) {
      el.textContent = "";
    });
    root.classList.add("hpc-resource-refreshed");
    global.setTimeout(function () {
      root.classList.remove("hpc-resource-refreshed");
    }, 450);
  }

  function setLoading(root) {
    root.classList.add("hpc-resource-updating");
    root.querySelectorAll("[data-resource-updated-at]").forEach(function (el) {
      el.classList.remove("hpc-resource-stale");
    });
    root.querySelectorAll("[data-resource-refresh-status]").forEach(function (el) {
      el.classList.add("is-updating");
    });
    root.querySelectorAll("[data-resource-refresh-live]").forEach(function (el) {
      el.textContent = "取得中";
    });
  }

  function setError(root) {
    root.querySelectorAll("[data-resource-updated-at]").forEach(function (el) {
      el.classList.add("hpc-resource-stale");
      el.classList.remove("hpc-resource-loading", "hpc-resource-live");
    });
    root.querySelectorAll("[data-resource-refresh-live]").forEach(function (el) {
      el.textContent = "更新に失敗しました。5秒後に再試行します";
    });
  }

  function finishLoading(root) {
    root.classList.remove("hpc-resource-updating");
    root.querySelectorAll("[data-resource-refresh-status]").forEach(function (el) {
      el.classList.remove("is-updating");
    });
  }

  function refresh(root) {
    if (root.classList.contains("hpc-resource-updating")) {
      return Promise.resolve();
    }
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
      })
      .then(function () {
        finishLoading(root);
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

  function closeResourceMenus(event) {
    global.document.querySelectorAll(".hpc-resource-menu[open], .hpc-gpu-processes[open]").forEach(function (menu) {
      if (!event || !menu.contains(event.target)) {
        menu.open = false;
      }
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
    global.document.addEventListener("click", closeResourceMenus);
    global.document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      var openMenu = global.document.querySelector(".hpc-resource-menu[open], .hpc-gpu-processes[open]");
      if (!openMenu) return;
      closeResourceMenus();
      var summary = openMenu.querySelector("summary");
      if (summary) summary.focus();
    });
  }

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
})(window);

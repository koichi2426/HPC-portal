/**
 * HPC Portal: named serverとshared Ollamaの起動状態を自動更新する。
 */
(function (global) {
  "use strict";

  var portal = global.HpcPortal;
  var INTERVAL_MS = 2500;
  var USER_API_URL = "/hub/api/user";
  var ADMIN_API_URL = "/hub/admin/users/api";
  var timer = null;
  var inFlight = false;
  var reloadPending = false;

  function userState(server) {
    if (!server) {
      return { state: "stopped", label: "停止済み" };
    }
    if (server.pending) {
      return {
        state: "pending",
        label: "起動中（" + String(server.pending) + "）",
      };
    }
    if (server.ready === true) {
      return { state: "running", label: "実行中" };
    }
    if (server.ready === false) {
      return { state: "stopped", label: "停止済み" };
    }
    if (server.url || server.started) {
      return { state: "running", label: "実行中" };
    }
    return { state: "pending", label: "起動状態を確認中" };
  }

  function setProgress(root, visible) {
    root.querySelectorAll("[data-hpc-app-progress]").forEach(function (progress) {
      progress.hidden = !visible;
    });
  }

  function requestReload(delay) {
    if (reloadPending) {
      return;
    }
    reloadPending = true;
    global.setTimeout(function () {
      global.location.reload();
    }, delay || 0);
  }

  function setState(root, next) {
    var previous = root.getAttribute("data-hpc-app-state") || "";
    var reload = root.getAttribute("data-hpc-reload-on-change") !== "false";
    var changed = previous && previous !== next.state;

    root.setAttribute("data-hpc-app-state", next.state);
    root.querySelectorAll("[data-hpc-app-status-text]").forEach(function (status) {
      status.textContent = next.label;
      status.classList.toggle("hpc-status-ok", next.state === "running");
      status.classList.toggle("hpc-status-warn", next.state === "pending");
      status.classList.toggle("hpc-status-danger", next.state === "stopped");
    });
    root.querySelectorAll("[data-hpc-app-status-badge]").forEach(function (badge) {
      badge.textContent = next.label;
      badge.classList.toggle("running", next.state === "running");
      badge.classList.toggle("pending", next.state === "pending");
    });
    setProgress(root, next.state === "pending");

    if (changed && reload) {
      requestReload(next.state === "running" ? 500 : 0);
    }
  }

  function updateNamedServers(data) {
    var servers = data.servers || {};
    global.document.querySelectorAll("[data-hpc-app-status]").forEach(function (root) {
      var name = root.getAttribute("data-server-name") || "";
      setState(root, userState(servers[name]));
    });
    if (global.document.querySelector("[data-hpc-app-list]")) {
      var represented = {};
      global.document.querySelectorAll("[data-hpc-app-status]").forEach(function (root) {
        represented[root.getAttribute("data-server-name") || ""] = true;
      });
      Object.keys(servers).some(function (name) {
        var server = servers[name] || {};
        var active = Boolean(
          server.pending ||
          server.ready === true ||
          (server.ready === undefined && (server.url || server.started))
        );
        if (active && !represented[name]) {
          requestReload(0);
          return true;
        }
        return false;
      });
    }
  }

  function updateSharedOllama(body) {
    var data = body.data || {};
    var next;
    if (!data.running) {
      next = { state: "stopped", label: "停止済み" };
    } else if (data.api) {
      next = { state: "running", label: "実行中 / API OK" };
    } else {
      next = { state: "pending", label: "起動中 / API応答待ち" };
    }
    global.document
      .querySelectorAll("[data-hpc-shared-ollama-status]")
      .forEach(function (root) {
        var configuredTargetVersion =
          root.getAttribute("data-hpc-ollama-target-version") || "";
        if (root.hasAttribute("data-hpc-shared-ollama-home-card")) {
          root.hidden = !data.running;
        }
        setState(root, next);
        root.querySelectorAll("[data-hpc-ollama-job-id]").forEach(function (job) {
          var jobIds = data.job_ids;
          var jobLabel = Array.isArray(jobIds) ? jobIds.join(", ") : String(jobIds || "");
          job.textContent = jobLabel ? "(job " + jobLabel + ")" : "";
        });
        root.querySelectorAll("[data-hpc-ollama-allocation]").forEach(function (allocation) {
          allocation.textContent =
            String(data.cpus || "—") + " vCPU · " +
            String(data.memory || "—") + " RAM · 1 GPU · 無制限";
        });
        root.querySelectorAll("[data-hpc-ollama-api-status]").forEach(function (status) {
          status.textContent = data.api ? "OK" : "起動待ち / 未応答";
          status.classList.toggle("hpc-status-ok", Boolean(data.api));
          status.classList.toggle("hpc-status-warn", !data.api);
        });
        root.querySelectorAll("[data-hpc-ollama-running-version]").forEach(function (version) {
          version.textContent = data.version ? "v" + data.version.replace(/^v/, "") : "確認中";
        });
        root.querySelectorAll("[data-hpc-ollama-target-version]").forEach(function (version) {
          var targetVersion = data.target_version || configuredTargetVersion;
          if (targetVersion) {
            version.textContent = "v" + targetVersion.replace(/^v/, "");
          }
        });
        root.querySelectorAll("[data-hpc-ollama-version-update]").forEach(function (badge) {
          var runningVersion = (data.version || "").replace(/^v/, "");
          var targetVersion = (
            data.target_version || configuredTargetVersion || ""
          ).replace(/^v/, "");
          badge.hidden = !(runningVersion && targetVersion && runningVersion !== targetVersion);
        });
      });
  }

  function updateOpenWebuiVersion(root, body) {
    var data = body.data || {};
    var running = data.running_version || "";
    var target = data.target_version || "";
    var runningElement = root.querySelector("[data-hpc-running-version]");
    var runningLabel = root.querySelector("[data-hpc-running-version-label]");
    var targetElement = root.querySelector("[data-hpc-target-version]");
    var updateElement = root.querySelector("[data-hpc-version-update]");

    if (runningElement) {
      runningElement.textContent = running ? "v" + running : "不明";
    }
    if (runningLabel) {
      runningLabel.textContent = data.verified ? "起動中" : "起動時";
    }
    if (targetElement) {
      targetElement.textContent = target ? "v" + target : "不明";
    }
    if (updateElement) {
      updateElement.hidden = !data.update_available;
    }
  }

  function refreshOpenWebuiVersions() {
    var roots = Array.prototype.slice.call(
      global.document.querySelectorAll("[data-hpc-openwebui-version-url]")
    );
    return Promise.all(
      roots.map(function (root) {
        var url = root.getAttribute("data-hpc-openwebui-version-url") || "";
        if (!url) return Promise.resolve();
        return portal.requestJson(url, {
          credentials: "same-origin",
          cache: "no-store",
        })
          .then(function (body) {
            updateOpenWebuiVersion(root, body);
          })
          .catch(function () {
            var runningElement = root.querySelector("[data-hpc-running-version]");
            if (runningElement && runningElement.textContent === "確認中") {
              runningElement.textContent = "不明";
            }
          });
      })
    );
  }

  function refreshNamedServers() {
    if (
      !global.document.querySelector(
        "[data-hpc-app-status], [data-hpc-app-list]"
      )
    ) {
      return Promise.resolve();
    }
    var xsrf = portal.readXsrfCookie();
    return portal.requestJson(USER_API_URL, {
      credentials: "same-origin",
      cache: "no-store",
      headers: xsrf ? { "X-XSRFToken": xsrf } : {},
    }).then(updateNamedServers);
  }

  function refreshSharedOllama() {
    if (!global.document.querySelector("[data-hpc-shared-ollama-status]")) {
      return Promise.resolve();
    }
    var xsrf = portal.readXsrfCookie();
    return portal.requestJson(ADMIN_API_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: Object.assign(
        { "Content-Type": "application/json" },
        xsrf ? { "X-XSRFToken": xsrf } : {}
      ),
      body: JSON.stringify({ action: "ollama_status" }),
    }).then(updateSharedOllama);
  }

  function schedule() {
    global.clearTimeout(timer);
    timer = global.setTimeout(refresh, INTERVAL_MS);
  }

  function refresh() {
    if (inFlight || global.document.hidden) {
      schedule();
      return;
    }
    inFlight = true;
    Promise.all([refreshNamedServers(), refreshSharedOllama()])
      .catch(function () {
        /* 現在の表示を維持し、次回ポーリングで再試行する。 */
      })
      .then(function () {
        inFlight = false;
        schedule();
      });
  }

  function start() {
    if (
      !global.document.querySelector(
        "[data-hpc-app-status], [data-hpc-shared-ollama-status], [data-hpc-app-list], [data-hpc-openwebui-version-url]"
      )
    ) {
      return;
    }
    refresh();
    refreshOpenWebuiVersions();
    global.document.addEventListener("visibilitychange", function () {
      if (!global.document.hidden) {
        refresh();
      }
    });
  }

  global.HpcAppStatus = {
    refresh: refresh,
    refreshOpenWebuiVersions: refreshOpenWebuiVersions,
    start: start,
  };
  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})(window);

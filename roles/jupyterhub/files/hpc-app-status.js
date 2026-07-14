/**
 * HPC Portal: named serverとshared Ollamaの起動状態を自動更新する。
 */
(function (global) {
  "use strict";

  var INTERVAL_MS = 2500;
  var USER_API_URL = "/hub/api/user";
  var ADMIN_API_URL = "/hub/admin/users/api";
  var ADMIN_APPS_API_URL = "/hub/admin/apps/api";
  var timer = null;
  var inFlight = false;
  var adminAppsInFlight = false;
  var reloadPending = false;

  function readXsrfCookie() {
    var match = global.document.cookie.match(/(?:^|; )_xsrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function requestJson(url, options) {
    return global.fetch(url, options).then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    });
  }

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
        return requestJson(url, {
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
    var xsrf = readXsrfCookie();
    return requestJson(USER_API_URL, {
      credentials: "same-origin",
      cache: "no-store",
      headers: xsrf ? { "X-XSRFToken": xsrf } : {},
    }).then(updateNamedServers);
  }

  function refreshSharedOllama() {
    if (!global.document.querySelector("[data-hpc-shared-ollama-status]")) {
      return Promise.resolve();
    }
    var xsrf = readXsrfCookie();
    return requestJson(ADMIN_API_URL, {
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

  function appendCell(row, text, className) {
    var cell = global.document.createElement("td");
    if (className) {
      var content = global.document.createElement("span");
      content.className = className;
      content.textContent = text;
      cell.appendChild(content);
    } else {
      cell.textContent = text;
    }
    row.appendChild(cell);
    return cell;
  }

  function buildAdminAppDetails(app, openJobIds, body) {
    var jobId = String(app.job_id || "");
    var isOpen = openJobIds.indexOf(jobId) !== -1;
    var toggle = global.document.createElement("button");
    toggle.type = "button";
    toggle.className = "hpc-admin-app-details-toggle";
    toggle.textContent = "詳細";
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    toggle.setAttribute("aria-controls", "hpc-admin-app-detail-" + jobId);

    var detailRow = global.document.createElement("tr");
    detailRow.id = "hpc-admin-app-detail-" + jobId;
    detailRow.className = "hpc-admin-app-detail-row";
    detailRow.setAttribute("data-detail-job-id", jobId);
    detailRow.hidden = !isOpen;
    var detailCell = global.document.createElement("td");
    detailCell.colSpan = 8;
    var panel = global.document.createElement("div");
    panel.className = "hpc-admin-app-details-panel";
    var list = global.document.createElement("dl");
    [
      ["Job ID", String(app.job_id || "—")],
      ["最大実使用メモリ", String(app.max_rss_label || "取得不可")],
      ["開始日時", String(app.started_at || "—")],
    ].forEach(function (entry) {
      var line = global.document.createElement("div");
      var term = global.document.createElement("dt");
      var value = global.document.createElement("dd");
      term.textContent = entry[0];
      value.textContent = entry[1];
      line.appendChild(term);
      line.appendChild(value);
      list.appendChild(line);
    });
    panel.appendChild(list);
    if (app.app === "Ollama") {
      var link = global.document.createElement("a");
      link.href = "/hub/apps/shared-ollama";
      link.textContent = "Ollama管理を開く →";
      panel.appendChild(link);
    }
    detailCell.appendChild(panel);
    detailRow.appendChild(detailCell);

    toggle.addEventListener("click", function () {
      var willOpen = detailRow.hidden;
      detailRow.hidden = !willOpen;
      toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
    return { toggle: toggle, row: detailRow };
  }

  function renderAdminApps(root, apps, error) {
    var body = root.querySelector("[data-hpc-admin-apps-body]");
    if (!body) return;
    var openJobIds = Array.prototype.map.call(
      body.querySelectorAll(".hpc-admin-app-detail-row:not([hidden])"),
      function (row) {
        return row.getAttribute("data-detail-job-id") || "";
      }
    );
    body.replaceChildren();
    if (!apps.length) {
      var emptyRow = global.document.createElement("tr");
      emptyRow.className = "hpc-admin-apps-empty";
      var emptyCell = global.document.createElement("td");
      emptyCell.colSpan = 8;
      emptyCell.textContent = error
        ? "取得できません: " + error
        : "起動中のアプリケーションはありません";
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
      return;
    }
    apps.forEach(function (app) {
      var row = global.document.createElement("tr");
      row.setAttribute("data-job-id", String(app.job_id || ""));
      var userCell = appendCell(row, "");
      var username = global.document.createElement("strong");
      username.textContent = String(app.username || "—");
      userCell.appendChild(username);
      if (app.display_name) {
        var displayName = global.document.createElement("span");
        displayName.className = "hpc-admin-app-display-name";
        displayName.textContent = String(app.display_name);
        userCell.appendChild(displayName);
      }
      appendCell(row, String(app.app || "—"));
      appendCell(
        row,
        String(app.state_label || app.state || "不明"),
        "hpc-admin-app-state is-" + String(app.state || "unknown").toLowerCase()
      );
      appendCell(row, String(app.cpus || "—") + " vCPU");
      appendCell(row, String(app.memory || "—"));
      appendCell(row, String(app.gpus || 0));
      appendCell(row, String(app.elapsed || "—"));
      var detailsCell = appendCell(row, "");
      var details = buildAdminAppDetails(app, openJobIds, body);
      detailsCell.appendChild(details.toggle);
      body.appendChild(row);
      body.appendChild(details.row);
    });
  }

  function refreshAdminApps(root) {
    if (adminAppsInFlight) return Promise.resolve();
    adminAppsInFlight = true;
    var updated = root.querySelector("[data-hpc-admin-apps-updated]");
    var message = root.querySelector("[data-hpc-admin-apps-message]");
    var reload = root.querySelector("[data-hpc-admin-apps-reload]");
    var refreshStatus = root.querySelector("[data-hpc-admin-apps-refresh]");
    var live = root.querySelector("[data-hpc-admin-apps-live]");
    if (refreshStatus) refreshStatus.classList.add("is-updating");
    if (live) live.textContent = "更新中";
    if (reload) reload.disabled = true;
    return requestJson(ADMIN_APPS_API_URL, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    })
      .then(function (body) {
        renderAdminApps(root, Array.isArray(body.apps) ? body.apps : [], body.error || "");
        if (updated) {
          updated.textContent =
            "最終更新 " +
            new Date(Number(body.updated_at || Date.now() / 1000) * 1000).toLocaleTimeString(
              "ja-JP",
              { hour: "2-digit", minute: "2-digit", second: "2-digit" }
            );
        }
        if (message) {
          message.textContent = "";
          message.className = "gx10-admin-msg";
        }
      })
      .catch(function (error) {
        if (message) {
          message.textContent = error.message;
          message.className = "gx10-admin-msg err";
        }
      })
      .then(function () {
        adminAppsInFlight = false;
        if (refreshStatus) refreshStatus.classList.remove("is-updating");
        if (live) live.textContent = "";
        if (reload) reload.disabled = false;
      });
  }

  function startAdminApps() {
    var root = global.document.querySelector("[data-hpc-admin-apps]");
    if (!root) return;
    var reload = root.querySelector("[data-hpc-admin-apps-reload]");
    if (reload) {
      reload.addEventListener("click", function () {
        refreshAdminApps(root);
      });
    }
    refreshAdminApps(root);
    global.setInterval(function () {
      if (!global.document.hidden) refreshAdminApps(root);
    }, 5000);
    global.document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      var detailRow = root.querySelector(".hpc-admin-app-detail-row:not([hidden])");
      if (!detailRow) return;
      var jobId = detailRow.getAttribute("data-detail-job-id") || "";
      detailRow.hidden = true;
      var toggle = root.querySelector(
        ".hpc-admin-app-details-toggle[aria-controls='hpc-admin-app-detail-" + jobId + "']"
      );
      if (toggle) {
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
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
    startAdminApps();
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

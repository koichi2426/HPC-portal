/**
 * HPC Portal: 管理者向け起動中アプリ一覧を更新する。
 */
(function (global) {
  "use strict";

  var portal = global.HpcPortal;
  var ADMIN_APPS_API_URL = "/hub/admin/apps/api";
  var adminAppsInFlight = false;

  function appendCell(row, label, text, className) {
    var cell = global.document.createElement("td");
    cell.setAttribute("data-label", label);
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
      row.className = "hpc-admin-app-row";
      row.setAttribute("data-job-id", String(app.job_id || ""));
      var userCell = appendCell(row, "ユーザー", "");
      var username = global.document.createElement("strong");
      username.textContent = String(app.username || "—");
      userCell.appendChild(username);
      if (app.display_name) {
        var displayName = global.document.createElement("span");
        displayName.className = "hpc-admin-app-display-name";
        displayName.textContent = String(app.display_name);
        userCell.appendChild(displayName);
      }
      appendCell(row, "アプリ", String(app.app || "—"));
      appendCell(
        row,
        "状態",
        String(app.state_label || app.state || "不明"),
        "hpc-admin-app-state is-" + String(app.state || "unknown").toLowerCase()
      );
      appendCell(row, "CPU割当", String(app.cpus || "—") + " vCPU");
      appendCell(row, "メモリ上限", String(app.memory || "—"));
      appendCell(row, "GPU", String(app.gpus || 0));
      appendCell(row, "実行時間", String(app.elapsed || "—"));
      var detailsCell = appendCell(row, "詳細", "");
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
    return portal.requestJson(ADMIN_APPS_API_URL, {
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
  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", startAdminApps);
  } else {
    startAdminApps();
  }
})(window);

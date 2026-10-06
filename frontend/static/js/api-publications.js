/** API公開画面のポート表・アプリ一覧・公開設定モーダルを制御する。 */
(function () {
  "use strict";

  function start() {
    var portal = window.HpcPortal;
    var root = document.querySelector("[data-api-page]");
    var form = root && root.querySelector("[data-publication-form]");
    if (!portal || !form) return;

    var candidates = [];
    var apps = [];
    var portsLoaded = false;
    var appsLoaded = false;
    var loadingPorts = false;
    var loadingApps = false;
    var fetchError = "";
    var editing = false;
    var dialog = root.querySelector("[data-publication-dialog]");
    var openMenu = null;
    var menuTrigger = null;
    var dialogTrigger = null;
    var states = {
      published: ["公開中", "is-enabled"],
      unpublished: ["停止中", "is-unknown"],
      checking: ["接続確認中", "is-unissued"],
      configuring: ["公開準備中", "is-unissued"],
      error: ["公開エラー", "is-disabled"],
      disconnected: ["接続エラー", "is-disabled"],
      disabled: ["利用停止中", "is-disabled"]
    };

    function el(tag, text, className) {
      var node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    function message(section, text, kind) {
      var node = root.querySelector("[data-" + section + "-message]");
      node.textContent = text;
      node.hidden = !text;
      node.className = "hpc-api-message" + (kind ? " is-" + kind : "");
    }

    function emptyRow(list, columns, text) {
      var row = el("tr");
      var cell = el("td", text, "hpc-empty");
      cell.colSpan = columns;
      row.appendChild(cell);
      list.replaceChildren(row);
    }

    function setLoading(section, loading) {
      root.querySelector("[data-" + section + "-refresh]").classList.toggle("is-updating", loading);
      root.querySelector("[data-" + section + "-live]").textContent = loading ? "更新中" : "";
      root.querySelector("[data-refresh-" + section + "]").disabled = loading;
    }

    function updated(section, timestamp) {
      var time = new Date(timestamp * 1000).toLocaleTimeString("ja-JP", {
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      });
      root.querySelector("[data-" + section + "-checked]").textContent = "最終更新 " + time;
    }

    function setBusy(busy) {
      root.dataset.apiBusy = String(busy);
      root.querySelectorAll("[data-api-operation]").forEach(function (button) {
        button.disabled = busy || root.dataset.apiAvailable !== "true";
      });
    }

    function operationButton(text, variant, action) {
      var button = el("button", text, "gx10-admin-btn gx10-admin-btn-" + variant);
      button.type = "button";
      button.dataset.apiOperation = "";
      button.disabled = root.dataset.apiBusy === "true" || root.dataset.apiAvailable !== "true";
      button.addEventListener("click", action);
      return button;
    }

    // 確認できた連番だけをまとめ、使用中の番号をまたぐ範囲を作らない。
    function renderPorts(ports) {
      var numbers = Array.from(new Set(ports)).sort(function (a, b) { return a - b; });
      var ranges = [];
      numbers.forEach(function (port) {
        var last = ranges[ranges.length - 1];
        if (last && port === last[1] + 1 && last[1] - last[0] < 15) last[1] = port;
        else ranges.push([port, port]);
      });

      var list = root.querySelector("[data-free-ports]");
      if (!ranges.length) {
        emptyRow(list, 3, "空きポート候補はありません");
        return;
      }
      list.replaceChildren();
      for (var index = 0; index < ranges.length; index += 3) {
        var row = el("tr");
        for (var column = 0; column < 3; column += 1) {
          var range = ranges[index + column];
          var text = range ? (range[0] === range[1] ? String(range[0]) : range[0] + "–" + range[1]) : "";
          row.appendChild(el("td", text));
        }
        list.appendChild(row);
      }
    }

    function renderCandidates() {
      var list = root.querySelector("[data-candidate-list]");
      var search = root.querySelector("[data-candidate-search]").value.toLowerCase();
      var shown = candidates.filter(function (app) {
        return [app.display_name, app.workdir, app.port].join(" ").toLowerCase().indexOf(search) >= 0;
      });
      if (!shown.length) {
        emptyRow(list, 5, candidates.length ? "該当するアプリはありません" : "起動済みアプリはありません");
        return;
      }

      list.replaceChildren();
      shown.forEach(function (app) {
        var row = el("tr");
        var name = el("td");
        name.appendChild(el("strong", app.display_name || "HTTPアプリ"));
        row.appendChild(name);
        var path = el("td");
        path.appendChild(el("code", app.workdir || "—"));
        row.appendChild(path);
        row.appendChild(el("td", app.port));
        var started = el("td");
        if (app.started_at) {
          var date = new Date(app.started_at * 1000);
          var time = el("time", date.toLocaleString("ja-JP", {
            month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
          }));
          time.dateTime = date.toISOString();
          time.title = date.toLocaleString("ja-JP");
          started.appendChild(time);
        } else started.textContent = "—";
        row.appendChild(started);
        var action = el("td", undefined, "hpc-api-actions-cell");
        action.appendChild(operationButton("公開", "primary", function (event) {
          openPublication(app, null, event.currentTarget);
        }));
        row.appendChild(action);
        list.appendChild(row);
      });
    }

    function isInteracting(section) {
      var focused = document.activeElement;
      return dialog.open || openMenu || root.dataset.apiBusy === "true" ||
        (focused && focused.closest("[data-" + section + "-list]"));
    }

    async function refreshPorts(force) {
      // 候補の更新で、公開フォームや一覧操作中の対象を入れ替えない。
      if (loadingPorts || document.hidden || root.dataset.apiAvailable !== "true" ||
          (!force && isInteracting("candidate"))) return false;
      loadingPorts = true;
      setLoading("ports", true);
      try {
        var data = await portal.requestJson("/hub/api-publications/ports");
        if (document.hidden || (!force && isInteracting("candidate"))) return false;
        candidates = data.listeners;
        portsLoaded = true;
        renderPorts(data.free);
        renderCandidates();
        updated("ports", data.checked_at);
        message("ports", "");
        return true;
      } catch (error) {
        message("ports", error.message, "error");
        if (!portsLoaded) {
          emptyRow(root.querySelector("[data-free-ports]"), 3, "取得できませんでした");
          emptyRow(root.querySelector("[data-candidate-list]"), 5, "取得できませんでした");
        }
        return false;
      } finally {
        loadingPorts = false;
        setLoading("ports", false);
      }
    }

    function closeMenu(restoreFocus) {
      if (!openMenu) return;
      openMenu.hidden = true;
      menuTrigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) menuTrigger.focus();
      openMenu = null;
      menuTrigger = null;
    }

    function showMenu(trigger, menu) {
      if (openMenu === menu) {
        closeMenu(true);
        return;
      }
      closeMenu(false);
      openMenu = menu;
      menuTrigger = trigger;
      menu.hidden = false;
      var anchor = trigger.getBoundingClientRect();
      var bounds = menu.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(anchor.right - bounds.width, innerWidth - bounds.width - 8)) + "px";
      menu.style.top = (anchor.bottom + bounds.height + 8 < innerHeight ? anchor.bottom + 5 : Math.max(8, anchor.top - bounds.height - 5)) + "px";
      trigger.setAttribute("aria-expanded", "true");
      var first = menu.querySelector("button:not(:disabled)");
      if (first) first.focus();
    }

    function appendMenuAction(menu, text, action, variant) {
      var button = operationButton(text, "muted", function () {
        closeMenu(true);
        action();
      });
      button.className = "hpc-user-action-item" + (variant ? " is-" + variant : "");
      button.setAttribute("role", "menuitem");
      menu.appendChild(button);
    }

    function renderApps() {
      var focused = document.activeElement;
      var focusedRow = focused && focused.closest("[data-publication-list] tr");
      var focusedName = focusedRow && focusedRow.dataset.apiName;
      var focusedMenu = focused && focused.classList.contains("hpc-user-actions-trigger");
      closeMenu(false);
      var list = root.querySelector("[data-publication-list]");
      if (!apps.length) {
        emptyRow(list, 5, "登録したAPIはありません");
        if (focusedName) root.querySelector("[data-refresh-publications]").focus({preventScroll: true});
        return;
      }
      list.replaceChildren();
      apps.forEach(function (app) {
        var row = el("tr");
        row.dataset.apiName = app.name;
        var name = el("td");
        name.appendChild(el("strong", app.display_name || app.name));
        if (app.display_name && app.display_name !== app.name) {
          name.appendChild(el("div", app.name, "hpc-api-url-name"));
        }
        row.appendChild(name);
        var url = el("td");
        var address = el("code", app.url, "hpc-api-url");
        address.title = app.url;
        url.appendChild(address);
        row.appendChild(url);
        row.appendChild(el("td", app.port));
        var state = states[app.state] || ["確認が必要", "is-unknown"];
        var status = el("td");
        status.appendChild(el("span", state[0], "hpc-api-status-badge " + state[1]));
        row.appendChild(status);
        var actions = el("td", undefined, "hpc-api-actions-cell");
        var toolbar = el("div", undefined, "hpc-api-toolbar");
        var copy = el("button", "URLをコピー", "gx10-admin-btn gx10-admin-btn-muted");
        copy.type = "button";
        copy.addEventListener("click", async function () {
          try {
            await navigator.clipboard.writeText(app.url);
            message("publications", "URLをコピーしました", "success");
          } catch (_) {
            message("publications", "コピーできませんでした。公開URLを選択してコピーしてください。", "error");
          }
        });
        toolbar.appendChild(copy);
        var trigger = el("button", "…", "hpc-user-actions-trigger");
        trigger.type = "button";
        trigger.setAttribute("aria-label", (app.display_name || app.name) + "の操作メニューを開く");
        trigger.setAttribute("aria-haspopup", "menu");
        trigger.setAttribute("aria-expanded", "false");
        var menu = el("div", undefined, "hpc-user-actions-menu");
        menu.setAttribute("role", "menu");
        menu.hidden = true;
        appendMenuAction(menu, "設定を変更", async function () {
          if (await refreshPorts(true)) openPublication(null, app, trigger);
        });
        if (["unpublished", "error", "disconnected"].indexOf(app.state) >= 0) {
          appendMenuAction(menu, app.state === "unpublished" ? "公開する" : "再試行", function () {
            operate(app.name, "publish");
          });
        }
        if (["published", "checking", "configuring", "error", "disconnected"].indexOf(app.state) >= 0) {
          appendMenuAction(menu, "公開を停止", function () { operate(app.name, "unpublish"); }, "warning");
        }
        appendMenuAction(menu, "削除", function () { operate(app.name, "delete"); }, "danger");
        trigger.addEventListener("click", function (event) {
          event.stopPropagation();
          showMenu(trigger, menu);
        });
        toolbar.appendChild(trigger);
        toolbar.appendChild(menu);
        actions.appendChild(toolbar);
        row.appendChild(actions);
        list.appendChild(row);
      });
      if (focusedName) {
        var updatedRow = Array.from(list.children).find(function (row) { return row.dataset.apiName === focusedName; });
        var nextFocus = updatedRow && updatedRow.querySelector(focusedMenu ? ".hpc-user-actions-trigger" : "button");
        (nextFocus || root.querySelector("[data-refresh-publications]")).focus({preventScroll: true});
      }
    }

    async function refreshApps(force) {
      // 自動更新で、メニュー操作やフォーム入力中のフォーカスを奪わない。
      if (loadingApps || document.hidden || (!force && isInteracting("publication"))) return;
      loadingApps = true;
      setLoading("publications", true);
      try {
        var data = await portal.requestJson("/hub/api-publications/api");
        if (document.hidden || (!force && isInteracting("publication"))) return;
        apps = data.apps;
        appsLoaded = true;
        if (!dialog.open && !openMenu) renderApps();
        updated("publications", Date.now() / 1000);
        if (fetchError && root.querySelector("[data-publications-message]").textContent === fetchError) message("publications", "");
        fetchError = "";
      } catch (error) {
        fetchError = error.message;
        message("publications", error.message, "error");
        if (!appsLoaded) emptyRow(root.querySelector("[data-publication-list]"), 5, "取得できませんでした");
      } finally {
        loadingApps = false;
        setLoading("publications", false);
      }
    }

    function updateTarget() {
      var app = candidates.find(function (row) { return row.candidate === form.elements.candidate.value; });
      form.elements.candidate.required = !form.elements.port.value;
      root.querySelector("[data-selected-candidate]").textContent = app ? app.workdir + " · ポート " + app.port : "";
    }

    function updateUrl() {
      root.querySelector("[data-url-preview]").textContent = root.dataset.publicUrlPrefix + (form.elements.name.value || "my-api") + "/";
    }

    function openPublication(candidate, app, trigger) {
      if (root.dataset.apiBusy === "true" || root.dataset.apiAvailable !== "true") return;
      closeMenu(false);
      editing = !!app;
      dialogTrigger = trigger;
      form.reset();
      form.elements.name.readOnly = editing;
      form.elements.name.value = app ? app.name : "";
      form.elements.display_name.value = app ? app.display_name : (candidate.display_name || "");
      form.elements.health_path.value = app ? (app.health_path || "") : "";
      var select = form.elements.candidate;
      select.replaceChildren(el("option", "アプリを選択"));
      select.firstChild.value = "";
      candidates.forEach(function (row) {
        var option = el("option", (row.display_name || "HTTPアプリ") + " · " + row.port);
        option.value = row.candidate;
        select.appendChild(option);
      });
      var target = candidate || candidates.find(function (row) { return row.port === app.port && row.workdir === app.workdir; });
      select.value = target ? target.candidate : "";
      root.querySelector("[data-dialog-title]").textContent = editing ? "APIの設定を変更" : "APIを公開";
      root.querySelector("[data-publication-submit]").textContent = editing ? "更新して公開" : "公開する";
      message("dialog", "");
      updateTarget();
      updateUrl();
      dialog.showModal();
      (editing ? select : form.elements.name).focus();
    }

    async function operate(name, action) {
      if (root.dataset.apiBusy === "true" || root.dataset.apiAvailable !== "true") return;
      var question = action === "delete" ? "このAPIの公開設定を削除しますか？\nアプリの実行は続きます。" : "このAPIの公開を停止しますか？\nアプリの実行は続きます。";
      if (action !== "publish" && !window.confirm(question)) return;
      setBusy(true);
      message("publications", "更新中…");
      try {
        await portal.requestJson("/hub/api-publications/api", {
          method: "POST", body: JSON.stringify({name: name, action: action})
        });
        message("publications", action === "delete" ? "削除しました" : action === "unpublish" ? "公開を停止しました" : "公開しました", "success");
      } catch (error) {
        message("publications", error.message, "error");
      } finally {
        setBusy(false);
        await refreshApps(true);
      }
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (root.dataset.apiBusy === "true" || root.dataset.apiAvailable !== "true") return;
      if (!editing && apps.some(function (app) { return app.name === form.elements.name.value; })) {
        message("dialog", "このURLの名前は登録済みです。別の名前を入力してください。", "error");
        form.elements.name.focus();
        return;
      }
      var data = {
        name: form.elements.name.value,
        display_name: form.elements.display_name.value,
        health_path: form.elements.health_path.value,
        candidate: form.elements.candidate.value
      };
      if (form.elements.port.value) data.port = Number(form.elements.port.value);
      setBusy(true);
      form.querySelectorAll("input, select, [data-close-dialog]").forEach(function (input) { input.disabled = true; });
      message("dialog", "公開中…");
      try {
        await portal.requestJson("/hub/api-publications/api", {
          method: "POST", body: JSON.stringify(data)
        });
        dialog.close();
        message("publications", editing ? "設定を更新しました" : "公開しました", "success");
      } catch (error) {
        message("dialog", error.message, "error");
      } finally {
        setBusy(false);
        form.querySelectorAll("input, select, [data-close-dialog]").forEach(function (input) { input.disabled = false; });
        await refreshApps(true);
        // 外部設定に失敗しても登録が残る場合があるため、同じURLの設定を再試行できる。
        if (dialog.open && apps.some(function (app) { return app.name === data.name; })) {
          editing = true;
          form.elements.name.readOnly = true;
          root.querySelector("[data-publication-submit]").textContent = "更新して公開";
        }
      }
    });

    root.querySelectorAll("[data-close-dialog]").forEach(function (button) {
      button.addEventListener("click", function () { dialog.close(); });
    });
    dialog.addEventListener("cancel", function (event) {
      if (root.dataset.apiBusy === "true") event.preventDefault();
    });
    dialog.addEventListener("close", function () {
      if (dialogTrigger && dialogTrigger.isConnected) dialogTrigger.focus();
    });
    form.elements.name.addEventListener("input", updateUrl);
    form.elements.candidate.addEventListener("change", function () {
      form.elements.port.value = "";
      updateTarget();
    });
    form.elements.port.addEventListener("input", function () {
      form.elements.candidate.value = "";
      updateTarget();
    });
    root.querySelector("[data-candidate-search]").addEventListener("input", function () {
      if (portsLoaded) renderCandidates();
    });
    root.querySelector("[data-refresh-ports]").addEventListener("click", function () { refreshPorts(true); });
    root.querySelector("[data-refresh-publications]").addEventListener("click", function () { refreshApps(true); });
    document.addEventListener("click", function (event) {
      if (openMenu && !openMenu.contains(event.target) && event.target !== menuTrigger) closeMenu(false);
    });
    document.addEventListener("keydown", function (event) {
      if (!openMenu) return;
      if (event.key === "Escape") {
        closeMenu(true);
        return;
      }
      var items = Array.from(openMenu.querySelectorAll("button:not(:disabled)"));
      var index = items.indexOf(document.activeElement);
      if (!items.length || ["ArrowDown", "ArrowUp", "Home", "End"].indexOf(event.key) < 0) return;
      event.preventDefault();
      if (event.key === "Home") index = 0;
      else if (event.key === "End") index = items.length - 1;
      else index = (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[index].focus();
    });
    window.addEventListener("resize", function () { closeMenu(false); });
    window.addEventListener("scroll", function () { closeMenu(false); }, true);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) closeMenu(false);
      else {
        refreshPorts(false);
        refreshApps(false);
      }
    });

    refreshPorts(true);
    refreshApps(true);
    window.setInterval(function () { refreshPorts(false); }, 30000);
    window.setInterval(function () { refreshApps(false); }, 10000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

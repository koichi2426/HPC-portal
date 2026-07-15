/**
 * HPC Portal: 管理ユーザー画面の一覧・操作・並べ替えを制御する。
 */
(function () {
  var portal = window.HpcPortal;
  var sortKey = "username";
  var sortDirection = "asc";
  function apiUrl() { return portal.apiUrl("/hub/admin/users/api"); }
  function apiHeaders() { return portal.apiHeaders(); }
  function showMsg(el, text, ok) {
    el.textContent = text;
    el.className = "gx10-admin-msg " + (ok ? "ok" : "err");
  }
  function showWarn(el, text) {
    el.textContent = text;
    el.className = "gx10-admin-msg warn";
  }
  function failOp(message) {
    alert(message);
  }
  function showCredentials(username, password, apiKey) {
    document.getElementById("credential-username").textContent = username;
    document.getElementById("initial-password-value").textContent = password || "";
    document.getElementById("create-keyvalue").textContent = apiKey || "";
    var warning = document.getElementById("credential-warning");
    if (password && apiKey) {
      warning.textContent = "初期パスワードとAPI keyは、この画面で一度だけ表示されます。画面を移動する前に安全な経路で共有してください。";
    } else if (password) {
      warning.textContent = "初期パスワードは保存されません。画面を移動する前にユーザーへ安全な経路で共有してください。";
    } else {
      warning.textContent = "API keyは、この画面で一度だけ表示されます。画面を移動する前に安全な経路で共有してください。";
    }
    document.getElementById("password-key-line").hidden = !password;
    document.getElementById("api-key-line").hidden = !apiKey;
    var apiBaseHelp = document.getElementById("api-base-help");
    if (apiBaseHelp) apiBaseHelp.hidden = !apiKey;
    var box = document.getElementById("create-keybox");
    box.className = "gx10-admin-keybox visible";
    box.scrollIntoView({behavior: "smooth", block: "nearest"});
  }
  function applyUserSort() {
    var tbody = document.getElementById("users-tbody");
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
    rows.sort(function (a, b) {
      var result;
      if (sortKey === "created") {
        result = Number(a.getAttribute("data-uid")) - Number(b.getAttribute("data-uid"));
      } else {
        result = (a.getAttribute("data-username") || "").localeCompare(
          b.getAttribute("data-username") || "",
          undefined,
          {numeric: true, sensitivity: "base"}
        );
      }
      return sortDirection === "desc" ? -result : result;
    });
    rows.forEach(function (row) { tbody.appendChild(row); });
  }
  function updateSortOptions() {
    document.querySelectorAll("[data-sort-key]").forEach(function (button) {
      var selected = button.getAttribute("data-sort-key") === sortKey;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    document.querySelectorAll("[data-sort-direction]").forEach(function (button) {
      var selected = button.getAttribute("data-sort-direction") === sortDirection;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }
  document.querySelectorAll(".hpc-sort-option").forEach(function (button) {
    button.onclick = function () {
      if (button.hasAttribute("data-sort-key")) {
        sortKey = button.getAttribute("data-sort-key");
      }
      if (button.hasAttribute("data-sort-direction")) {
        sortDirection = button.getAttribute("data-sort-direction");
      }
      updateSortOptions();
      applyUserSort();
      document.getElementById("user-sort-dropdown").open = false;
    };
  });
  document.addEventListener("click", function (event) {
    var dropdown = document.getElementById("user-sort-dropdown");
    if (dropdown.open && !dropdown.contains(event.target)) dropdown.open = false;
  });
  var displayNameModal = document.getElementById("hpc-display-name-modal");
  var displayNameForm = document.getElementById("hpc-display-name-form");
  var displayNameInput = document.getElementById("hpc-display-name-input");
  var displayNameError = document.getElementById("hpc-display-name-error");
  var displayNameUser = "";
  function closeDisplayNameModal() {
    displayNameModal.hidden = true;
    displayNameUser = "";
    displayNameError.textContent = "";
    displayNameError.className = "gx10-admin-msg";
  }
  function openDisplayNameModal(username, displayName) {
    displayNameUser = username;
    document.getElementById("hpc-display-name-modal-title").textContent = username + " の表示名";
    displayNameInput.value = displayName || "";
    displayNameError.textContent = "";
    displayNameError.className = "gx10-admin-msg";
    displayNameModal.hidden = false;
    displayNameInput.focus();
    displayNameInput.select();
  }
  document.getElementById("hpc-display-name-cancel").onclick = closeDisplayNameModal;
  displayNameModal.onclick = function (event) {
    if (event.target === displayNameModal) closeDisplayNameModal();
  };
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      if (!displayNameModal.hidden) closeDisplayNameModal();
      var openMenu = document.querySelector('.hpc-user-actions-menu:not([hidden])');
      var openTrigger = openMenu ? openMenu.parentElement.querySelector(".hpc-user-actions-trigger") : null;
      closeUserActionMenus();
      if (openTrigger) openTrigger.focus();
    }
  });
  displayNameForm.onsubmit = function (event) {
    event.preventDefault();
    if (!displayNameUser) return;
    var username = displayNameUser;
    var button = document.getElementById("hpc-display-name-save");
    button.disabled = true;
    postAction({
      action: "display_name",
      username: username,
      display_name: displayNameInput.value.trim()
    }).then(function () {
      closeDisplayNameModal();
      showMsg(document.getElementById("list-msg"), username + " の表示名を更新しました", true);
      return reloadUsers().catch(function (error) {
        showMsg(document.getElementById("list-msg"), "表示名は更新しましたが、一覧を再読み込みできませんでした: " + error.message, false);
      });
    }).catch(function (error) {
      showMsg(displayNameError, error.message, false);
    }).finally(function () {
      button.disabled = false;
    });
  };
  function closeUserActionMenus(exceptMenu) {
    document.querySelectorAll(".hpc-user-actions-menu").forEach(function (menu) {
      if (menu === exceptMenu) return;
      menu.hidden = true;
      menu.style.visibility = "";
      var trigger = menu.parentElement.querySelector(".hpc-user-actions-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    });
  }
  function openUserActionMenu(trigger, menu) {
    closeUserActionMenus(menu);
    menu.hidden = false;
    menu.style.visibility = "hidden";
    var triggerRect = trigger.getBoundingClientRect();
    var menuRect = menu.getBoundingClientRect();
    var left = Math.max(8, Math.min(triggerRect.right - menuRect.width, window.innerWidth - menuRect.width - 8));
    var top = triggerRect.bottom + 5;
    if (top + menuRect.height > window.innerHeight - 8) {
      top = Math.max(8, triggerRect.top - menuRect.height - 5);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    menu.style.visibility = "";
    trigger.setAttribute("aria-expanded", "true");
    var firstItem = menu.querySelector("button:not(:disabled)");
    if (firstItem) firstItem.focus();
  }
  function bindActionMenus() {
    document.querySelectorAll(".hpc-user-actions-trigger").forEach(function (trigger) {
      trigger.onclick = function (event) {
        event.stopPropagation();
        var menu = trigger.parentElement.querySelector(".hpc-user-actions-menu");
        if (!menu) return;
        if (!menu.hidden) {
          closeUserActionMenus();
          trigger.focus();
          return;
        }
        openUserActionMenu(trigger, menu);
      };
    });
    document.querySelectorAll(".hpc-user-actions-menu").forEach(function (menu) {
      menu.onclick = function (event) { event.stopPropagation(); };
      menu.onkeydown = function (event) {
        var items = Array.prototype.slice.call(menu.querySelectorAll("button:not(:disabled)"));
        if (!items.length) return;
        var current = items.indexOf(document.activeElement);
        var next = current;
        if (event.key === "ArrowDown") next = current < 0 ? 0 : (current + 1) % items.length;
        else if (event.key === "ArrowUp") next = current <= 0 ? items.length - 1 : current - 1;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = items.length - 1;
        else return;
        event.preventDefault();
        items[next].focus();
      };
    });
  }
  document.addEventListener("click", function () { closeUserActionMenus(); });
  window.addEventListener("resize", function () { closeUserActionMenus(); });
  window.addEventListener("scroll", function () { closeUserActionMenus(); }, true);
  function bindRowButtons() {
    bindActionMenus();
    document.querySelectorAll(".hpc-display-name-btn").forEach(function (btn) {
      btn.onclick = function () {
        closeUserActionMenus();
        openDisplayNameModal(
          btn.getAttribute("data-username"),
          btn.getAttribute("data-display-name") || ""
        );
      };
    });
    document.querySelectorAll(".hpc-del-btn").forEach(function (btn) {
      btn.onclick = function () {
        var name = btn.getAttribute("data-username");
        closeUserActionMenus();
        if (!confirm(name + " を削除しますか？ホームディレクトリも削除されます。")) return;
        btn.disabled = true;
        postAction({ action: "delete", username: name })
          .then(function (body) {
            if (body.warning) {
              showWarn(document.getElementById("list-msg"), name + " を削除しました。" + body.warning);
              return reloadUsers();
            }
            window.location.reload();
          })
          .catch(function (e) {
            showMsg(document.getElementById("list-msg"), e.message, false);
            failOp(e.message);
            btn.disabled = false;
          });
      };
    });
    document.querySelectorAll(".hpc-pw-btn").forEach(function (btn) {
      btn.onclick = function () {
        var name = btn.getAttribute("data-username");
        closeUserActionMenus();
        if (!confirm(name + " のパスワードを再発行しますか？\nログイン済みのセッションや実行中アプリは終了しません。")) return;
        btn.disabled = true;
        postAction({ action: "password_regenerate", username: name })
          .then(function (body) {
            showCredentials(name, body.initial_password, "");
            showMsg(document.getElementById("list-msg"), name + " のパスワードを再発行しました", true);
          })
          .catch(function (e) {
            showMsg(document.getElementById("list-msg"), e.message, false);
            failOp(e.message);
          })
          .finally(function () { btn.disabled = false; });
      };
    });
    document.querySelectorAll(".hpc-api-access-btn").forEach(function (btn) {
      btn.onclick = function () {
        var name = btn.getAttribute("data-username");
        closeUserActionMenus();
        var action = btn.getAttribute("data-action");
        var apiState = btn.getAttribute("data-api-state") || "";
        var enabling = action === "api_enable";
        var actionLabel = enabling ? "有効化" : "無効化";
        var confirmText = name + " の LLM APIを" + actionLabel + "しますか？";
        if (apiState === "unissued") {
          confirmText = name + " の LLM APIを有効化して、API keyを発行しますか？";
        }
        if (!confirm(confirmText)) return;
        btn.disabled = true;
        postAction({ action: action, username: name })
          .then(function (body) {
            if (body.api_key) {
              showCredentials(name, "", body.api_key);
              showMsg(document.getElementById("list-msg"), name + " の LLM APIを有効化し、API keyを発行しました", true);
            } else {
              showMsg(document.getElementById("list-msg"), name + " の LLM APIを" + actionLabel + "しました", true);
            }
            return reloadUsers().catch(function (error) {
              showWarn(document.getElementById("list-msg"), "LLM APIは" + actionLabel + "しましたが、一覧を再読み込みできませんでした: " + error.message);
            });
          })
          .catch(function (e) {
            showMsg(document.getElementById("list-msg"), e.message, false);
            failOp(e.message);
            return reloadUsers().catch(function () {});
          })
          .finally(function () { btn.disabled = false; });
      };
    });
    document.querySelectorAll(".hpc-sudo-access-btn").forEach(function (btn) {
      btn.onclick = function () {
        var name = btn.getAttribute("data-username");
        var action = btn.getAttribute("data-action");
        var enabling = action === "sudo_enable";
        closeUserActionMenus();
        var confirmText = enabling
          ? name + " にsudo権限を付与しますか？\nこのユーザーはサーバー全体を管理できるようになります。次回ログインから確実に反映されます。"
          : name + " のsudo権限を解除しますか？\nログイン済みセッションや実行中アプリは終了せず、次回ログインから確実に反映されます。";
        if (!confirm(confirmText)) return;
        btn.disabled = true;
        postAction({ action: action, username: name })
          .then(function () {
            showMsg(document.getElementById("list-msg"), name + " のsudo権限を" + (enabling ? "有効化" : "無効化") + "しました", true);
            return reloadUsers().catch(function (error) {
              showWarn(document.getElementById("list-msg"), "sudo権限は変更しましたが、一覧を再読み込みできませんでした: " + error.message);
            });
          })
          .catch(function (e) {
            showMsg(document.getElementById("list-msg"), e.message, false);
            failOp(e.message);
          })
          .finally(function () { btn.disabled = false; });
      };
    });
  }
  function postAction(payload) {
    return fetch(apiUrl(), {
      method: "POST",
      credentials: "same-origin",
      headers: apiHeaders(),
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || body.message || ("HTTP " + r.status));
        return body;
      });
    });
  }
  function reloadUsers() {
    return fetch(apiUrl(), { credentials: "same-origin", headers: apiHeaders() })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(body.error || body.message || ("HTTP " + r.status));
          return body;
        });
      })
      .then(function (data) {
        var tbody = document.getElementById("users-tbody");
        tbody.innerHTML = "";
        (data.users || []).forEach(function (u) {
          var tr = document.createElement("tr");
          tr.setAttribute("data-username", u.username);
          tr.setAttribute("data-uid", String(u.uid));

          var usernameCell = document.createElement("td");
          var usernameStrong = document.createElement("strong");
          usernameStrong.textContent = u.username;
          usernameCell.appendChild(usernameStrong);
          if (u.protected) {
            var badge = document.createElement("span");
            badge.className = "gx10-admin-badge";
            badge.style.marginLeft = "6px";
            badge.textContent = "保護";
            usernameCell.appendChild(badge);
          }
          tr.appendChild(usernameCell);

          var displayNameCell = document.createElement("td");
          var displayNameValue = document.createElement("span");
          displayNameValue.className = "hpc-display-name-value";
          displayNameValue.textContent = u.display_name || "—";
          displayNameCell.appendChild(displayNameValue);
          tr.appendChild(displayNameCell);

          var uidCell = document.createElement("td");
          uidCell.textContent = String(u.uid);
          tr.appendChild(uidCell);

          var homeCell = document.createElement("td");
          var homeCode = document.createElement("code");
          homeCode.textContent = u.home;
          homeCell.appendChild(homeCode);
          tr.appendChild(homeCell);

          var storageCell = document.createElement("td");
          var storageValue = document.createElement("span");
          storageValue.className = "hpc-storage-usage" + (u.storage_used_bytes == null ? " is-unknown" : "");
          storageValue.textContent = u.storage_used_label || "確認不可";
          if (u.storage_message) storageValue.title = u.storage_message;
          storageCell.appendChild(storageValue);
          tr.appendChild(storageCell);

          var sudoStatusCell = document.createElement("td");
          var sudoStatus = document.createElement("span");
          sudoStatus.className = "hpc-sudo-status-badge " + (u.sudo_enabled ? "is-enabled" : "is-disabled");
          sudoStatus.textContent = u.sudo_enabled ? "sudo可" : "一般";
          sudoStatus.title = "設定されたsudoグループに" + (u.sudo_enabled ? "所属しています" : "所属していません");
          sudoStatusCell.appendChild(sudoStatus);
          tr.appendChild(sudoStatusCell);

          var apiStatusCell = document.createElement("td");
          var apiStatus = document.createElement("span");
          var apiAccess = ["enabled", "disabled", "unissued"].indexOf(u.api_access) >= 0 ? u.api_access : "unknown";
          apiStatus.className = "hpc-api-status-badge is-" + apiAccess;
          apiStatus.textContent = apiAccess === "enabled" ? "有効" : (apiAccess === "disabled" ? "無効" : (apiAccess === "unissued" ? "未発行" : "確認不可"));
          if (u.api_access_message) apiStatus.title = u.api_access_message;
          apiStatusCell.appendChild(apiStatus);
          tr.appendChild(apiStatusCell);

          var operationCell = document.createElement("td");
          operationCell.className = "hpc-user-actions-cell";
          var actionTrigger = document.createElement("button");
          actionTrigger.type = "button";
          actionTrigger.className = "hpc-user-actions-trigger";
          actionTrigger.setAttribute("aria-label", u.username + " の操作メニューを開く");
          actionTrigger.setAttribute("aria-haspopup", "menu");
          actionTrigger.setAttribute("aria-expanded", "false");
          actionTrigger.textContent = "…";
          operationCell.appendChild(actionTrigger);
          var actionMenu = document.createElement("div");
          actionMenu.className = "hpc-user-actions-menu";
          actionMenu.setAttribute("role", "menu");
          actionMenu.hidden = true;
          function appendActionItem(label, className, action, apiState, disabled, title) {
            var actionButton = document.createElement("button");
            actionButton.type = "button";
            actionButton.setAttribute("role", "menuitem");
            actionButton.className = "hpc-user-action-item " + className;
            actionButton.setAttribute("data-username", u.username);
            if (action) actionButton.setAttribute("data-action", action);
            if (apiState) actionButton.setAttribute("data-api-state", apiState);
            if (disabled) actionButton.disabled = true;
            if (title) actionButton.title = title;
            actionButton.textContent = label;
            actionMenu.appendChild(actionButton);
            return actionButton;
          }
          var displayAction = appendActionItem("表示名を変更", "hpc-display-name-btn", "", "", false, "");
          displayAction.setAttribute("data-display-name", u.display_name || "");
          if (!u.protected) {
            appendActionItem("パスワード再発行", "hpc-pw-btn", "", "", false, "");
            if (apiAccess === "enabled") {
              appendActionItem("LLM API無効化", "hpc-api-access-btn", "api_disable", "enabled", false, "");
            } else if (apiAccess === "disabled") {
              appendActionItem("LLM API有効化", "hpc-api-access-btn", "api_enable", "disabled", false, "");
            } else if (apiAccess === "unissued") {
              appendActionItem("LLM API有効化", "hpc-api-access-btn", "api_enable", "unissued", false, "");
            }
          }
          var sudoDivider = document.createElement("div");
          sudoDivider.className = "hpc-user-action-divider";
          sudoDivider.setAttribute("role", "separator");
          actionMenu.appendChild(sudoDivider);
          if (u.sudo_enabled) {
            appendActionItem("sudo無効化", "is-warning hpc-sudo-access-btn", "sudo_disable", "", u.protected, u.protected ? "保護ユーザーのsudo権限は解除できません" : "");
          } else {
            appendActionItem("sudo有効化", "hpc-sudo-access-btn", "sudo_enable", "", false, "");
          }
          if (!u.protected) {
            var deleteDivider = document.createElement("div");
            deleteDivider.className = "hpc-user-action-divider";
            deleteDivider.setAttribute("role", "separator");
            actionMenu.appendChild(deleteDivider);
            appendActionItem("ユーザー削除", "is-danger hpc-del-btn", "", "", false, "");
          }
          operationCell.appendChild(actionMenu);
          tr.appendChild(operationCell);
          tbody.appendChild(tr);
        });
        applyUserSort();
        bindRowButtons();
      });
  }
  var createForm = document.getElementById("hpc-create-user-form");
  var keyBox = document.getElementById("create-keybox");
  var keyValue = document.getElementById("create-keyvalue");
  document.querySelectorAll(".hpc-copy-btn").forEach(function (button) {
    button.onclick = function () {
      var target = document.getElementById(button.getAttribute("data-copy-target"));
      var value = target ? target.textContent : "";
      if (!value) return;
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        window.prompt("値をコピーしてください", value);
        return;
      }
      navigator.clipboard.writeText(value).then(function () {
        showMsg(document.getElementById("create-msg"), "コピーしました", true);
      }).catch(function () {
        window.prompt("値をコピーしてください", value);
      });
    };
  });
  createForm.onsubmit = function (ev) {
    ev.preventDefault();
    var btn = document.getElementById("create-btn");
    var msg = document.getElementById("create-msg");
    keyBox.className = "gx10-admin-keybox";
    keyValue.textContent = "";
    document.getElementById("initial-password-value").textContent = "";
    btn.disabled = true;
    postAction({
      action: "create",
      username: document.getElementById("new-username").value.trim().toLowerCase(),
      display_name: document.getElementById("new-display-name").value.trim(),
      sudo: document.getElementById("new-sudo").checked
    }).then(function (body) {
      showCredentials(body.username, body.initial_password, body.api_key || "");
      if (body.warning) {
        showWarn(msg, "ユーザーは作成しました。" + body.warning);
      } else {
        showMsg(msg, "ユーザーを作成しました", true);
      }
      createForm.reset();
      if (createForm.getAttribute("data-default-sudo") === "true") {
        document.getElementById("new-sudo").checked = true;
      }
      return reloadUsers();
    }).catch(function (e) {
      showMsg(msg, e.message, false);
      failOp(e.message);
    }).finally(function () { btn.disabled = false; });
  };
  document.getElementById("reload-btn").onclick = function () {
    reloadUsers().catch(function (e) {
      showMsg(document.getElementById("list-msg"), e.message, false);
      failOp(e.message);
    });
  };
  applyUserSort();
  updateSortOptions();
  bindRowButtons();
})();

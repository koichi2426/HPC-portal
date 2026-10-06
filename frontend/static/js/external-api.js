/* Personal credentials and independently started application publication. */
(function () {
  "use strict";
  function start() {
    var portal = window.HpcPortal;
    if (!portal) return;
    var root = document.querySelector("[data-external-api], [data-api-publications]");
    var values = null, selected = "", candidates = [], apps = [], busy = false;
    var states = {published: "公開中", unpublished: "未公開", checking: "接続確認中",
      configuring: "Cloudflare 設定中", error: "設定失敗・再試行可能", disconnected: "接続できません",
      disabled: "利用停止中"};
    function message(text) {
      var node = root && root.querySelector("[data-external-message]");
      if (node) node.textContent = text;
    }
    function date(value) { return value ? new Date(value * 1000).toLocaleString("ja-JP") : "不明"; }
    function el(tag, text) {
      var node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      return node;
    }
    function hide() {
      values = null;
      if (!root) return;
      root.querySelectorAll("[data-credential-value]").forEach(function (input) { input.value = ""; });
      var fields = root.querySelector("[data-credential-fields]");
      if (fields) fields.hidden = true;
    }
    function download(data) {
      var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], {type: "application/json"}));
      var link = el("a");
      link.href = url; link.download = "hpc-api.json";
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }
    async function copy(text) {
      try { await navigator.clipboard.writeText(text); message("コピーしました"); }
      catch (_) { message("コピーできませんでした。表示欄からコピーしてください。"); }
    }
    async function credentials(action) {
      if (busy) return;
      if (action.indexOf("rotate_") === 0 && !window.confirm("旧トークンが利用できなくなります。再発行しますか？")) return;
      busy = true; hide(); message("処理中…");
      var controls = root.querySelectorAll("[data-credential-action], [data-download-config]");
      controls.forEach(function (item) { item.disabled = true; });
      try {
        var data = await portal.requestJson("/hub/external-api/credentials", {method: "POST", body: JSON.stringify({action: action})});
        if (action === "download") { download(data); message("接続設定をダウンロードしました"); }
        else {
          values = data;
          root.querySelectorAll("[data-credential-value]").forEach(function (input) { input.value = values[input.dataset.credentialValue] || ""; });
          root.querySelector("[data-credential-fields]").hidden = false;
          message(action === "reveal" ? "接続情報を取得しました" : "再発行しました。接続設定を取り直してください。");
        }
      } catch (error) { hide(); message(error.message); }
      finally { busy = false; controls.forEach(function (item) { item.disabled = false; }); }
    }
    if (root && root.hasAttribute("data-external-api")) {
      root.querySelectorAll("[data-credential-action]").forEach(function (button) {
        button.addEventListener("click", function () { credentials(button.dataset.credentialAction); });
      });
      root.querySelectorAll("[data-copy-credential]").forEach(function (button) {
        button.addEventListener("click", function () { if (values) copy(values[button.dataset.copyCredential]); });
      });
      var env = root.querySelector("[data-copy-env]");
      if (env) env.addEventListener("click", function () {
        if (values) copy("HPC_BASE_URL=" + values.base_url + "\nCF_ACCESS_CLIENT_ID=" + values.client_id +
          "\nCF_ACCESS_CLIENT_SECRET=" + values.client_secret + "\nJUPYTERHUB_TOKEN=" + values.jupyterhub_token + "\n");
      });
      var hideButton = root.querySelector("[data-hide-credentials]");
      if (hideButton) hideButton.addEventListener("click", hide);
      var downloadButton = root.querySelector("[data-download-config]");
      if (downloadButton) downloadButton.addEventListener("click", function () { credentials("download"); });
      window.addEventListener("pagehide", hide);
      document.addEventListener("visibilitychange", function () { if (document.hidden) hide(); });
    }
    function renderCandidates() {
      var list = root.querySelector("[data-candidate-list]");
      var search = root.querySelector("[data-candidate-search]").value.toLowerCase();
      list.replaceChildren();
      var shown = candidates.filter(function (row) {
        return (row.display_name + " " + row.workdir + " " + row.port).toLowerCase().indexOf(search) >= 0;
      });
      shown.forEach(function (row) {
        var card = el("div"); card.className = "gx10-resource-card";
        card.appendChild(el("strong", row.display_name || "HTTP アプリ"));
        card.appendChild(el("p", row.workdir || "作業フォルダは取得できません"));
        card.appendChild(el("p", "ポート：" + row.port + " ／ " + row.state + " ／ 起動：" + date(row.started_at)));
        var button = el("button", selected === row.candidate ? "選択中" : "このアプリを選択");
        button.type = "button";
        button.className = "gx10-admin-btn " + (selected === row.candidate ? "gx10-admin-btn-primary" : "gx10-admin-btn-muted");
        button.setAttribute("aria-pressed", String(selected === row.candidate));
        button.addEventListener("click", function () {
          selected = row.candidate;
          var form = root.querySelector("[data-publication-form]");
          form.elements.port.value = row.port;
          if (!form.elements.display_name.value) form.elements.display_name.value = row.display_name;
          root.querySelector("[data-selected-candidate]").textContent = (row.display_name || "HTTP アプリ") + " / " + row.port;
          renderCandidates();
        });
        card.appendChild(button); list.appendChild(card);
      });
      root.querySelector("[data-candidate-empty]").hidden = !!shown.length;
    }
    async function refreshPorts() {
      var button = root.querySelector("[data-refresh-ports]");
      button.disabled = true;
      try {
        var data = await portal.requestJson("/hub/api-publications/ports");
        candidates = data.listeners;
        if (!candidates.some(function (row) { return row.candidate === selected; })) {
          selected = ""; root.querySelector("[data-selected-candidate]").textContent = "未選択";
        }
        root.querySelector("[data-free-ports]").textContent = data.free.length ? data.free.join("、") : "候補なし";
        root.querySelector("[data-ports-checked]").textContent = "確認日時：" + date(data.checked_at);
        root.querySelector("[data-port-settings]").textContent = "候補範囲：" + data.range.join("〜") + " ／ 予約ポート：" + data.reserved.join("、");
        renderCandidates();
      } catch (error) { message(error.message); }
      finally { button.disabled = false; }
    }
    async function operate(name, action) {
      if (busy) return;
      if (action === "delete" && !window.confirm("公開設定を削除しますか？アプリのプロセスは終了しません。")) return;
      busy = true;
      try {
        await portal.requestJson("/hub/api-publications/api", {method: "POST", body: JSON.stringify({name: name, action: action})});
        message("公開設定を更新しました"); await refreshApps();
      } catch (error) { message(error.message); await refreshApps(); }
      finally { busy = false; }
    }
    function renderApps() {
      var list = root.querySelector("[data-publication-list]");
      list.replaceChildren();
      if (!apps.length) list.appendChild(el("p", "登録済み API はありません"));
      apps.forEach(function (app) {
        var card = el("div"); card.className = "gx10-resource-card";
        card.appendChild(el("strong", app.display_name + " (" + app.name + ")"));
        card.appendChild(el("p", states[app.state] || app.state));
        card.appendChild(el("p", (app.workdir || "作業フォルダ不明") + " ／ ポート：" + app.port + " ／ 起動：" + date(app.started_at)));
        var url = el("input"); url.className = "form-control"; url.readOnly = true; url.value = app.url; url.setAttribute("aria-label", "公開 URL");
        card.appendChild(url);
        var actions = el("div"); actions.className = "hpc-api-actions";
        var copyButton = el("button", "URL をコピー"); copyButton.type = "button";
        copyButton.className = "gx10-admin-btn gx10-admin-btn-muted";
        copyButton.addEventListener("click", function () { copy(app.url); }); actions.appendChild(copyButton);
        var edit = el("button", "接続先を変更"); edit.type = "button";
        edit.className = "gx10-admin-btn gx10-admin-btn-muted";
        edit.addEventListener("click", function () {
          var form = root.querySelector("[data-publication-form]");
          form.elements.name.value = app.name; form.elements.display_name.value = app.display_name;
          form.elements.health_path.value = app.health_path; form.elements.port.value = "";
          selected = ""; root.querySelector("[data-selected-candidate]").textContent = "新しい接続先を選択してください";
          renderCandidates(); form.scrollIntoView({behavior: "smooth"}); form.elements.name.focus();
        });
        actions.appendChild(edit);
        [["publish", "公開・再試行"], ["unpublish", "公開停止"], ["delete", "登録削除"]].forEach(function (item) {
          var button = el("button", item[1]); button.type = "button";
          // 公開・停止・削除を、既存の管理画面と同じ色で区別する。
          var variant = item[0] === "publish" ? "primary" : item[0] === "delete" ? "danger" : "warn";
          button.className = "gx10-admin-btn gx10-admin-btn-" + variant;
          button.addEventListener("click", function () { operate(app.name, item[0]); }); actions.appendChild(button);
        });
        card.appendChild(actions);
        list.appendChild(card);
      });
    }
    var homeList = document.querySelector("[data-api-app-list]");
    async function refreshApps() {
      if (document.hidden) return;
      try {
        var data = await portal.requestJson("/hub/api-publications/api");
        apps = data.apps;
        if (root && root.hasAttribute("data-api-publications")) renderApps();
        if (homeList) {
          homeList.hidden = !apps.length;
          var rows = homeList.querySelector("[data-api-app-rows]"); rows.replaceChildren();
          apps.forEach(function (app) {
            var link = el("a", app.display_name + " — " + (states[app.state] || app.state));
            link.href = "/hub/api-publications"; var row = el("p"); row.appendChild(link); rows.appendChild(row);
          });
        }
      } catch (error) { if (root) message(error.message); }
    }
    if (root && root.hasAttribute("data-api-publications") && root.querySelector("[data-publication-form]")) {
      root.querySelector("[data-refresh-ports]").addEventListener("click", refreshPorts);
      root.querySelector("[data-candidate-search]").addEventListener("input", renderCandidates);
      var form = root.querySelector("[data-publication-form]");
      form.elements.port.addEventListener("input", function () {
        selected = ""; root.querySelector("[data-selected-candidate]").textContent = "ポートを手動指定";
      });
      form.addEventListener("submit", async function (event) {
        event.preventDefault(); if (busy) return;
        var data = {name: form.elements.name.value, display_name: form.elements.display_name.value,
          health_path: form.elements.health_path.value, candidate: selected};
        if (form.elements.port.value) data.port = Number(form.elements.port.value);
        if (!selected && !data.port) { message("アプリを選択するか、接続先ポートを指定してください"); return; }
        busy = true; var submit = form.querySelector("[type=submit]"); submit.disabled = true;
        message("接続を確認し、Cloudflare に設定しています…");
        try {
          await portal.requestJson("/hub/api-publications/api", {method: "POST", body: JSON.stringify(data)});
          message("公開しました");
        } catch (error) { message(error.message); }
        finally { busy = false; submit.disabled = false; await refreshApps(); }
      });
      refreshPorts(); refreshApps();
      window.setInterval(refreshApps, 10000);
    } else if (homeList) { refreshApps(); window.setInterval(refreshApps, 10000); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

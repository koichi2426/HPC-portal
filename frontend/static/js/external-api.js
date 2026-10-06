/** API公開画面の接続情報を自動取得し、コピー・個別再発行を制御する。 */
(function () {
  "use strict";

  function start() {
    var portal = window.HpcPortal;
    var page = document.querySelector("[data-api-page]");
    var root = page && page.querySelector("[data-api-credentials]");
    if (!portal || !root) return;

    var values = null;
    var generation = 0;
    var loading = false;
    var retry = root.querySelector("[data-credential-retry]");
    var notice = root.querySelector("[data-credential-message]");

    function message(text, error) {
      notice.textContent = text;
      notice.hidden = !text;
      notice.className = "hpc-api-message" + (error ? " is-error" : "");
    }

    function clearValues(placeholder) {
      // 欄は開いたまま、タブを離れた後に届く応答も含めて秘密値を消す。
      generation += 1;
      values = null;
      root.querySelectorAll("[data-credential-value]").forEach(function (input) {
        input.value = "";
        input.placeholder = placeholder || "—";
      });
      updateControls();
    }

    function updateControls() {
      root.querySelectorAll("[data-credential-action], [data-download-config]").forEach(function (button) {
        button.disabled = loading || page.dataset.apiBusy === "true" || page.dataset.apiAvailable !== "true";
      });
      root.querySelectorAll("[data-copy-credential], [data-copy-env]").forEach(function (button) {
        button.disabled = loading || !values;
      });
    }

    function setBusy(busy) {
      page.dataset.apiBusy = String(busy);
      page.querySelectorAll("[data-api-operation]").forEach(function (button) {
        button.disabled = busy || page.dataset.apiAvailable !== "true";
      });
    }

    function download(data) {
      var url = URL.createObjectURL(new Blob(
        [JSON.stringify(data, null, 2) + "\n"],
        {type: "application/json"}
      ));
      var link = document.createElement("a");
      link.href = url;
      link.download = "hpc-api.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    async function copy(text) {
      try {
        await navigator.clipboard.writeText(text);
        message("コピーしました");
      } catch (_) {
        message("コピーできませんでした。表示欄からコピーしてください。", true);
      }
    }

    async function credentials(action) {
      var locksPage = action !== "reveal";
      if (loading || document.hidden || page.dataset.apiAvailable !== "true") return;
      if (locksPage && page.dataset.apiBusy === "true") return;
      if (action.indexOf("rotate_") === 0) {
        var name = action === "rotate_cloudflare" ? "Cloudflare Service Token" : "JupyterHub API Token";
        if (!window.confirm(name + "を再発行しますか？\n現在のトークンは使えなくなります。外部プログラムの設定を更新してください。")) return;
      }

      loading = true;
      retry.hidden = true;
      if (action !== "download") clearValues("取得中…");
      var requestGeneration = generation;
      var discarded = false;
      // 初期表示の読み取りは、APIの公開操作を妨げない。
      if (locksPage) setBusy(true);
      updateControls();
      message("取得中…");
      try {
        var data = await portal.requestJson("/hub/external-api/credentials", {
          method: "POST",
          body: JSON.stringify({action: action})
        });
        if (document.hidden || requestGeneration !== generation) {
          discarded = true;
          message("");
          return;
        }
        if (action === "download") {
          download(data);
          message("設定をダウンロードしました");
          return;
        }

        values = data;
        root.querySelectorAll("[data-credential-value]").forEach(function (input) {
          input.value = values[input.dataset.credentialValue] || "";
          input.placeholder = "";
        });
        message(action === "reveal" ? "" : "再発行しました。外部プログラムの設定を更新してください。");
      } catch (error) {
        if (action !== "download") clearValues("取得できません");
        retry.hidden = !!values;
        message(error.message, true);
      } finally {
        loading = false;
        if (locksPage) setBusy(false);
        updateControls();
        if (discarded && !document.hidden && !values) credentials("reveal");
      }
    }

    root.querySelectorAll("[data-credential-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        credentials(button.dataset.credentialAction);
      });
    });
    root.querySelectorAll("[data-copy-credential]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (values) copy(values[button.dataset.copyCredential]);
      });
    });
    root.querySelector("[data-copy-env]").addEventListener("click", function () {
      if (!values) return;
      copy("CF_ACCESS_CLIENT_ID=" + values.client_id +
        "\nCF_ACCESS_CLIENT_SECRET=" + values.client_secret +
        "\nJUPYTERHUB_TOKEN=" + values.jupyterhub_token + "\n");
    });
    root.querySelector("[data-download-config]").addEventListener("click", function () {
      credentials("download");
    });
    window.addEventListener("pagehide", function () { clearValues(); });
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) credentials("reveal");
    });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) clearValues();
      else if (!values) credentials("reveal");
    });
    updateControls();
    credentials("reveal");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

/** API用Service TokenとHubトークンの独立した発行・更新を制御する。 */
(function () {
  "use strict";

  function start() {
    var portal = window.HpcPortal;
    var page = document.querySelector("[data-api-page]");
    var root = page && page.querySelector("[data-api-credentials]");
    if (!portal || !root) return;
    var values = null, loading = false, generation = 0;
    var notice = root.querySelector("[data-credential-message]");
    var retry = root.querySelector("[data-credential-retry]");

    function message(text, error) {
      notice.textContent = text;
      notice.hidden = !text;
      notice.className = "hpc-api-message" + (error ? " is-error" : "");
    }
    function clearValues() {
      generation += 1;
      values = null;
      root.querySelectorAll("[data-credential-value]").forEach(function (input) { input.value = ""; });
      updateControls();
    }
    function updateControls() {
      var enabled = values ? values.enabled : root.dataset.credentialEnabled === "true";
      var serviceReady = values && values.service_state === "ready";
      var hubReady = values && values.hub_state === "ready";
      var pending = values && ["issuing", "rotating_cloudflare", "revoking_cloudflare", "revoking"].indexOf(values.service_state) >= 0;
      var busy = loading || page.dataset.apiBusy === "true";
      root.querySelectorAll("[data-credential-action]").forEach(function (button) {
        var action = button.dataset.credentialAction;
        if (button.dataset.credentialKind === "cloudflare") {
          action = serviceReady ? "rotate_cloudflare" : "issue";
          button.dataset.credentialAction = action;
          button.textContent = serviceReady ? "再発行" : "発行";
        }
        button.disabled = busy || !enabled || (pending && action !== "reveal") ||
          (action === "revoke_cloudflare" && !serviceReady) ||
          (action === "revoke_jupyterhub" && !hubReady);
      });
      root.querySelectorAll("[data-copy-credential], [data-copy-env]").forEach(function (button) {
        button.disabled = loading || !values || !enabled;
      });
      root.querySelector("[data-download-config]").disabled = busy || !enabled || !serviceReady || !hubReady;
    }
    function apply(data) {
      values = data;
      page.dataset.apiAvailable = String(data.enabled && data.service_state === "ready" && data.hub_state === "ready");
      var status = root.querySelector(".hpc-api-status-badge");
      status.textContent = !data.enabled ? "利用停止中" : {
        unissued: "Service Token未発行", ready: "Service Token発行済み", issuing: "発行中",
        rotating_cloudflare: "更新中", revoking_cloudflare: "失効中", revoking: "停止処理中"
      }[data.service_state] || "確認が必要";
      status.className = "hpc-api-status-badge " + (data.enabled && data.service_state === "ready" ? "is-enabled" : "is-unissued");
      root.querySelectorAll("[data-credential-value]").forEach(function (input) {
        input.value = data[input.dataset.credentialValue] || "";
        input.placeholder = "—";
      });
    }
    async function credentials(action) {
      if (loading || document.hidden || page.dataset.apiBusy === "true") return;
      if ((action.indexOf("rotate_") === 0 || action.indexOf("revoke_") === 0) &&
          !window.confirm("選択したトークンを" + (action.indexOf("revoke_") === 0 ? "失効" : "再発行") + "しますか？現在のトークンは使えなくなります。")) return;
      loading = true;
      retry.hidden = true;
      if (action !== "download") clearValues();
      var requestGeneration = generation, discarded = false, changed = false;
      if (action !== "reveal") page.dataset.apiBusy = "true";
      updateControls();
      try {
        var data = await portal.requestJson("/hub/external-api/credentials", {method: "POST", body: JSON.stringify({action: action})});
        if (document.hidden || requestGeneration !== generation) { discarded = true; return; }
        if (action === "download") {
          var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], {type: "application/json"}));
          var link = document.createElement("a");
          link.href = url; link.download = "hpc-api.json"; link.click();
          window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        } else apply(data);
        message(action === "reveal" ? "" : "操作が完了しました。");
        changed = action !== "reveal" && action !== "download";
      } catch (error) {
        retry.hidden = false;
        message(error.message, true);
      } finally {
        loading = false;
        if (action !== "reveal") page.dataset.apiBusy = "false";
        updateControls();
        if (changed) window.dispatchEvent(new Event("hpc-api-credentials-changed"));
        if (discarded && !document.hidden) credentials("reveal");
      }
    }
    root.querySelectorAll("[data-credential-action]").forEach(function (button) {
      button.addEventListener("click", function () { credentials(button.dataset.credentialAction); });
    });
    async function copy(text) {
      try { await navigator.clipboard.writeText(text); message("コピーしました"); }
      catch (_) { message("表示欄からコピーしてください。", true); }
    }
    root.querySelectorAll("[data-copy-credential]").forEach(function (button) {
      button.addEventListener("click", function () { if (values) copy(values[button.dataset.copyCredential]); });
    });
    root.querySelector("[data-copy-env]").addEventListener("click", function () {
      if (values) copy("CF_ACCESS_CLIENT_ID=" + values.client_id + "\nCF_ACCESS_CLIENT_SECRET=" + values.client_secret + "\nJUPYTERHUB_TOKEN=" + values.jupyterhub_token + "\n");
    });
    root.querySelector("[data-download-config]").addEventListener("click", function () { credentials("download"); });
    window.addEventListener("pagehide", clearValues);
    window.addEventListener("pageshow", function (event) { if (event.persisted) credentials("reveal"); });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) clearValues(); else credentials("reveal");
    });
    updateControls();
    credentials("reveal");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

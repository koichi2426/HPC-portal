/** SSH接続情報の発行・コピー・失効を本人の操作で行う。 */
(function () {
  "use strict";
  function start() {
    var portal = window.HpcPortal, root = document.querySelector("[data-ssh-page]");
    var issue = root && root.querySelector("[data-ssh-issue]");
    if (!portal || !issue) return;
    var values = null, loading = false, generation = 0;
    var notice = root.querySelector("[data-ssh-message]");
    function update() {
      var ready = values && values.enabled && values.state === "ready";
      var pending = values && ["issuing", "rotating", "revoking"].indexOf(values.state) >= 0;
      issue.textContent = ready ? "再発行" : "発行";
      issue.disabled = loading || !values || !values.enabled || pending;
      root.querySelectorAll("[data-ssh-action]").forEach(function (button) {
        button.disabled = loading || (button.dataset.sshAction !== "reveal" && !ready);
      });
      root.querySelectorAll("[data-ssh-copy]").forEach(function (button) {
        button.disabled = loading || !values || !values[button.dataset.sshCopy];
      });
    }
    function clear() {
      generation += 1;
      values = null;
      root.querySelectorAll("[data-ssh-value]").forEach(function (input) { input.value = ""; });
      update();
    }
    async function operate(action) {
      if (loading || document.hidden) return;
      if ((action === "rotate" || action === "revoke") && !window.confirm("SSH用トークンを" + (action === "rotate" ? "再発行" : "失効") + "しますか？現在のトークンは使えなくなります。")) return;
      loading = true;
      clear();
      var requestGeneration = generation, discarded = false;
      update();
      notice.hidden = true;
      try {
        var data = await portal.requestJson("/hub/ssh-access/credentials", {method: "POST", body: JSON.stringify({action: action})});
        if (document.hidden || requestGeneration !== generation) { discarded = true; return; }
        values = data;
        root.querySelectorAll("[data-ssh-value]").forEach(function (input) { input.value = data[input.dataset.sshValue] || ""; });
        var status = root.querySelector("[data-ssh-state]");
        status.textContent = !data.enabled ? "利用停止中" : {unissued: "未発行", ready: "利用可能", issuing: "発行中", rotating: "更新中", revoking: "失効中"}[data.state] || "確認が必要";
        status.className = "hpc-api-status-badge " + (data.enabled && data.state === "ready" ? "is-enabled" : "is-unissued");
        if (action === "download") {
          var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], {type: "application/json"}));
          var link = document.createElement("a"); link.href = url; link.download = "hpc-ssh.json"; link.click();
          window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        }
      } catch (error) { notice.textContent = error.message; notice.hidden = false; }
      finally { loading = false; update(); if (discarded && !document.hidden) operate("reveal"); }
    }
    issue.addEventListener("click", function () { operate(values && values.state === "ready" ? "rotate" : "issue"); });
    root.querySelectorAll("[data-ssh-action]").forEach(function (button) { button.addEventListener("click", function () { operate(button.dataset.sshAction); }); });
    root.querySelectorAll("[data-ssh-copy]").forEach(function (button) {
      button.addEventListener("click", async function () {
        if (!values) return;
        try { await navigator.clipboard.writeText(values[button.dataset.sshCopy]); }
        catch (_) { notice.textContent = "表示欄からコピーしてください。"; notice.hidden = false; }
      });
    });
    window.addEventListener("pagehide", clear);
    window.addEventListener("pageshow", function (event) { if (event.persisted) operate("reveal"); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) clear(); else operate("reveal"); });
    operate("reveal");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

/**
 * HPC Portal: アプリ詳細画面からJupyterHubサーバーを削除する。
 */
(function (global) {
  "use strict";

  function deleteApp(button) {
    if (!button || button.disabled) return;
    var serverName = button.getAttribute("data-server-name") || "";
    if (!global.confirm(
      "このアプリケーションを削除しますか？\n関連する Slurm ジョブも終了します。"
    )) return;
    var page = global.document.querySelector(".gx10-app-detail-page");
    var user = page ? page.getAttribute("data-hpc-user") || "" : "";
    if (!user) {
      global.alert("ユーザー情報を取得できません");
      return;
    }
    var api = serverName
      ? "/hub/api/users/" + encodeURIComponent(user) + "/servers/" + encodeURIComponent(serverName)
      : "/hub/api/users/" + encodeURIComponent(user) + "/server";
    button.disabled = true;
    button.textContent = "削除中…";
    global.HpcPortal.requestJson(api, { method: "DELETE" })
      .then(function () { global.location.href = "/hub/home"; })
      .catch(function () {
        button.disabled = false;
        button.textContent = "削除";
        global.alert("削除に失敗しました。しばらくしてから再度お試しください。");
      });
  }

  global.hpcDeleteApp = deleteApp;
})(window);

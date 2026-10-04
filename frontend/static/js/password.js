/**
 * HPC Portal: 本人用パスワード変更画面を制御する。
 */
(function () {
  var portal = window.HpcPortal;
  var form = document.getElementById("hpc-own-password-form");
  var msg = document.getElementById("password-msg");
  var passwordToggles = document.querySelectorAll("[data-password-toggle]");
  function setPasswordVisibility(toggle, visible) {
    var input = document.getElementById(toggle.getAttribute("data-password-toggle"));
    if (!input) return;
    var label = toggle.getAttribute("data-password-label") || "パスワード";
    var action = visible ? "非表示" : "表示";
    input.type = visible ? "text" : "password";
    toggle.setAttribute("aria-pressed", visible ? "true" : "false");
    toggle.setAttribute("aria-label", label + "を" + action);
    toggle.setAttribute("title", label + "を" + action);
  }
  passwordToggles.forEach(function (toggle) {
    toggle.onclick = function () {
      setPasswordVisibility(toggle, toggle.getAttribute("aria-pressed") !== "true");
    };
  });
  form.onsubmit = function (ev) {
    ev.preventDefault();
    var currentPassword = document.getElementById("current-password").value;
    var newPassword = document.getElementById("new-password").value;
    var confirmPassword = document.getElementById("confirm-password").value;
    if (newPassword !== confirmPassword) {
      msg.textContent = "新しいパスワードが確認入力と一致しません";
      msg.className = "gx10-admin-msg err";
      return;
    }
    var button = document.getElementById("password-submit");
    button.disabled = true;
    portal.requestJson("/hub/account/password/api", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword
      })
    }).then(function () {
      form.reset();
      passwordToggles.forEach(function (toggle) {
        setPasswordVisibility(toggle, false);
      });
      msg.textContent = "パスワードを変更しました。次回のログインから新しいパスワードを使用してください。";
      msg.className = "gx10-admin-msg ok";
    }).catch(function (error) {
      msg.textContent = error.message;
      msg.className = "gx10-admin-msg err";
    }).finally(function () {
      button.disabled = false;
    });
  };
})();

/**
 * HPC Portal: 本人用LLM API Key画面を制御する。
 */
(function () {
  var portal = window.HpcPortal;
  function apiUrl() { return portal.apiUrl("/hub/llm-api/api"); }
  function apiHeaders() { return portal.apiHeaders(); }
  function showMsg(text, ok) {
    var msg = document.getElementById("key-msg");
    msg.textContent = text;
    msg.className = "gx10-key-msg " + (ok ? "ok" : "err");
  }
  function copyExample(id, btn) {
    var text = (document.getElementById(id).textContent || "").trim();
    var status = document.getElementById(btn.getAttribute("data-copy-status") || "");
    function copied() {
      var original = btn.textContent;
      btn.textContent = "✓ コピー済み";
      btn.disabled = true;
      if (status) status.textContent = "";
      window.setTimeout(function () { btn.textContent = original; btn.disabled = false; }, 1600);
    }
    function failed() {
      if (status) status.textContent = "コピーできませんでした。コードを選択してコピーしてください。";
    }
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      window.prompt("コードをコピーしてください", text);
      return;
    }
    navigator.clipboard.writeText(text).then(function () {
      copied();
    }).catch(failed);
  }
  document.querySelectorAll(".gx10-model-select").forEach(function (btn) {
    btn.onclick = function () {
      var model = btn.getAttribute("data-model") || "";
      document.querySelectorAll(".gx10-model-select").forEach(function (item) { item.classList.remove("selected"); });
      btn.classList.add("selected");
      document.querySelectorAll("[data-model-output]").forEach(function (output) { output.textContent = model; });
    };
  });
  document.querySelectorAll(".gx10-copy-example").forEach(function (btn) {
    btn.onclick = function () { copyExample(btn.getAttribute("data-copy"), btn); };
  });
  function copyKey() {
    var key = document.getElementById("key-value").textContent || "";
    if (!key) return;
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      window.prompt("API key をコピーしてください", key);
      return;
    }
    navigator.clipboard.writeText(key).then(function () {
      showMsg("API key をコピーしました", true);
    }).catch(function () {
      window.prompt("API key をコピーしてください", key);
    });
  }
  document.getElementById("copy-key-btn").onclick = copyKey;
  document.getElementById("regen-btn").onclick = function () {
    var btn = document.getElementById("regen-btn");
    var box = document.getElementById("key-box");
    var value = document.getElementById("key-value");
    if (!confirm("既存の自分用 API key を無効化して、新しい key を発行しますか？")) return;
    btn.disabled = true;
    box.className = "gx10-key-box";
    value.textContent = "";
    fetch(apiUrl(), {
      method: "POST",
      credentials: "same-origin",
      headers: apiHeaders(),
      body: JSON.stringify({ action: "regenerate" })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || body.message || ("HTTP " + r.status));
        return body;
      });
    }).then(function (body) {
      value.textContent = body.api_key || "";
      box.className = "gx10-key-box visible";
      showMsg("API key を再発行しました", true);
    }).catch(function (e) {
      showMsg(e.message, false);
    }).finally(function () {
      btn.disabled = false;
    });
  };
})();

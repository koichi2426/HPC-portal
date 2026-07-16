/**
 * HPC Portal: アプリ起動フォームと実行中アプリの停止操作を制御する。
 */
(function (global) {
  "use strict";

  var portal = global.HpcPortal;

  function portalUser() {
    var root = global.document.getElementById("resource-dashboard");
    return root ? root.getAttribute("data-hpc-user") || "" : "";
  }

  function stopServer(button) {
    if (!button || button.disabled) return;
    var serverName = button.getAttribute("data-server-name") || "";
    if (!global.confirm(
      "このアプリケーションを停止・削除しますか？\n関連する Slurm ジョブも終了します。"
    )) return;
    var user = portalUser();
    if (!user) {
      global.alert("ユーザー情報を取得できません");
      return;
    }
    var api = serverName
      ? "/hub/api/users/" + encodeURIComponent(user) + "/servers/" + encodeURIComponent(serverName)
      : "/hub/api/users/" + encodeURIComponent(user) + "/server";
    button.disabled = true;
    button.textContent = "停止中…";
    portal.requestJson(api, { method: "DELETE" })
      .then(function () { global.location.reload(); })
      .catch(function () {
        button.disabled = false;
        button.textContent = "停止";
        global.alert("停止に失敗しました。しばらくしてから再度お試しください。");
      });
  }

  function start() {
    var form = global.document.querySelector("form");
    var appChoice = global.document.querySelector('select[name="app_choice"]');
    var sharedBox = global.document.getElementById("shared-ollama-options");
    var standardBox = global.document.getElementById("standard-resource-options");
    var standardHelp = global.document.getElementById("standard-resource-help");
    var appVersionHelp = global.document.getElementById("app-version-help");
    var recommendationCard = global.document.getElementById("app-resource-recommendation");

    function setFormValue(name, value) {
      var element = global.document.querySelector('[name="' + name + '"]');
      if (element && value !== undefined) element.value = value;
    }

    function applyRecommendation(option, isSharedOllama) {
      if (!option) return;
      var recommendation = option.dataset;
      if (isSharedOllama) {
        setFormValue("ollama_cpus", recommendation.cpu);
        setFormValue("ollama_memory", recommendation.memory);
      } else {
        setFormValue("cpu", recommendation.cpu);
        setFormValue("mem", recommendation.memory);
        setFormValue("gpu", recommendation.gpu);
        setFormValue("hours", recommendation.hours);
      }
      if (!recommendationCard) return;
      var values = {
        "[data-recommendation-label]": recommendation.label,
        "[data-recommendation-cpu]": recommendation.cpu + " vCPU",
        "[data-recommendation-memory]": recommendation.memoryLabel,
        "[data-recommendation-gpu]": recommendation.gpu,
        "[data-recommendation-hours]": recommendation.hoursLabel,
        "[data-recommendation-summary]": recommendation.summary,
        "[data-recommendation-guidance]": recommendation.guidance,
      };
      Object.keys(values).forEach(function (selector) {
        var target = recommendationCard.querySelector(selector);
        if (target) target.textContent = values[selector] || "";
      });
    }

    function refreshAppChoice() {
      if (!appChoice) return;
      var isSharedOllama = appChoice.value === "shared-ollama";
      var selectedOption = appChoice.options[appChoice.selectedIndex];
      if (sharedBox) sharedBox.style.display = isSharedOllama ? "block" : "none";
      if (standardBox) {
        standardBox.style.display = isSharedOllama ? "none" : "block";
        standardBox.querySelectorAll("input, select, textarea, button").forEach(function (element) {
          element.disabled = isSharedOllama;
        });
      }
      if (standardHelp) standardHelp.style.display = isSharedOllama ? "none" : "block";
      if (appVersionHelp) {
        var labelKey = appChoice.value === "open-webui"
          ? "openwebuiLabel"
          : (isSharedOllama ? "ollamaLabel" : "ubuntuLabel");
        appVersionHelp.textContent = "バージョン: " + appVersionHelp.dataset[labelKey];
      }
      applyRecommendation(selectedOption, isSharedOllama);
    }

    if (appChoice) {
      appChoice.addEventListener("change", refreshAppChoice);
      refreshAppChoice();
    }
    if (!form) return;
    form.onsubmit = function (event) {
      if (appChoice && appChoice.value === "shared-ollama") {
        event.preventDefault();
        var cpus = global.document.querySelector('select[name="ollama_cpus"]').value;
        var memory = global.document.querySelector('select[name="ollama_memory"]').value;
        portal.requestJson("/hub/admin/users/api", {
          method: "POST",
          body: JSON.stringify({ action: "ollama_start", cpus: cpus, memory: memory }),
        })
          .then(function () { global.location.href = "/hub/apps/shared-ollama"; })
          .catch(function (error) {
            global.alert(error.message || "Ollama の起動に失敗しました");
          });
        return false;
      }
      var inputs = global.document.querySelectorAll('input[name="_xsrf"]');
      for (var index = 1; index < inputs.length; index += 1) inputs[index].remove();
    };
  }

  global.hpcStopServer = stopServer;
  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})(window);

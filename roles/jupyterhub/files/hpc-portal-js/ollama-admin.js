/**
 * HPC Portal: 共有Ollamaとモデル管理操作を制御する。
 */
function hpcOllamaPost(payload) {
  return window.HpcPortal.requestJson("/hub/admin/users/api", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
function hpcOllamaMsg(text, ok) {
  var el = document.getElementById("ollama-action-msg");
  if (!el) return;
  el.className = ok ? "hpc-help hpc-status-ok" : "hpc-help hpc-status-danger";
  el.textContent = text;
}
function hpcOllamaSetPullButton(btn, state, model) {
  if (!btn) return;
  var input = document.getElementById("ollama-pull-model");
  btn.dataset.pullState = state || "idle";
  btn.dataset.model = model || "";
  btn.classList.toggle("gx10-primary-btn", state !== "pulling" && state !== "cancelling" && state !== "cleanup_failed");
  btn.classList.toggle("gx10-delete-btn", state === "pulling" || state === "cancelling" || state === "cleanup_failed");
  if (state === "pulling") {
    btn.textContent = "中止";
    btn.disabled = false;
  } else if (state === "cancelling") {
    btn.textContent = "中止中…";
    btn.disabled = true;
  } else if (state === "cleanup_failed") {
    btn.textContent = "削除を再試行";
    btn.disabled = false;
  } else {
    btn.textContent = "Pull";
    btn.disabled = false;
  }
  if (input) {
    input.disabled = state === "pulling" || state === "cancelling" || state === "cleanup_failed";
    if (model && input.value.trim() !== model) input.value = model;
  }
}
function hpcOllamaHideProgress() {
  var box = document.getElementById("ollama-pull-progress");
  if (box) box.hidden = true;
}
function hpcOllamaPull(btn) {
  var pullState = btn.dataset.pullState || "idle";
  if (pullState === "pulling" || pullState === "cleanup_failed") {
    hpcOllamaCancelPull(btn);
    return;
  }
  var input = document.getElementById("ollama-pull-model");
  var model = input ? input.value.trim() : "";
  if (!model) return hpcOllamaMsg("モデル名を入力してください", false);
  btn.disabled = true;
  hpcOllamaRenderProgress({status: "ダウンロードを開始しています"});
  hpcOllamaPost({action: "ollama_pull", model: model}).then(function (body) {
    hpcOllamaMsg(model + " のダウンロードを開始しました", true);
    hpcOllamaSetPullButton(btn, "pulling", model);
    hpcOllamaPollPull(model, btn, 0, 0);
  }).catch(function (e) {
    hpcOllamaFinishProgress("開始に失敗しました", false);
    hpcOllamaMsg(e.message, false);
    btn.disabled = false;
  });
}
function hpcOllamaCancelPull(btn) {
  var model = btn.dataset.model || "";
  var cleanupRetry = btn.dataset.pullState === "cleanup_failed";
  if (!model) return hpcOllamaMsg("中止対象のモデルを確認できません", false);
  if (!cleanupRetry && !confirm(model + " のダウンロードを中止しますか？\n途中までダウンロードしたデータも削除します。")) return;
  hpcOllamaSetPullButton(btn, "cancelling", model);
  hpcOllamaRenderProgress({status: cleanupRetry ? "途中データを削除しています" : "ダウンロードを中止しています"});
  hpcOllamaPost({action: "ollama_pull_cancel", model: model}).then(function (body) {
    var data = body.data || {};
    if (data.status === "already_completed") {
      hpcOllamaSetPullButton(btn, "pulling", model);
      hpcOllamaPollPull(model, btn, 0, 0);
      return;
    }
    hpcOllamaSetPullButton(btn, "idle", "");
    hpcOllamaHideProgress();
    hpcOllamaMsg(model + " のダウンロードを中止し、途中データを削除しました", true);
  }).catch(function (e) {
    hpcOllamaPost({action: "ollama_pull_status", model: model}).then(function (body) {
      var data = body.data || {};
      if (data.state === "pulling") {
        hpcOllamaSetPullButton(btn, "pulling", model);
        hpcOllamaPollPull(model, btn, 0, 0);
        return;
      }
      if (data.state === "cancelled_cleanup_failed") {
        hpcOllamaSetPullButton(btn, "cleanup_failed", model);
        hpcOllamaFinishProgress("中止済み・途中データの削除に失敗", false);
      } else {
        hpcOllamaSetPullButton(btn, "idle", "");
      }
      hpcOllamaMsg(e.message, false);
    }).catch(function () {
      hpcOllamaSetPullButton(btn, "idle", "");
      hpcOllamaMsg(e.message, false);
    });
  });
}
function hpcFormatBytes(value) {
  if (typeof value !== "number" || value < 0) return "";
  var units = ["B", "KB", "MB", "GB", "TB"], i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return value.toFixed(i >= 3 ? 1 : 0) + " " + units[i];
}
function hpcOllamaRenderProgress(data) {
  var box = document.getElementById("ollama-pull-progress");
  var text = document.getElementById("ollama-pull-progress-text");
  var fill = document.getElementById("ollama-pull-progress-fill");
  if (!box || !text || !fill) return;
  box.hidden = false;
  var total = data.total, completed = data.completed;
  var pct = total > 0 && completed != null ? Math.min(100, completed / total * 100) : 0;
  var progress = fill.parentElement;
  progress.classList.remove("hpc-progress-error");
  if (total > 0 && completed != null) {
    progress.classList.remove("hpc-progress-indeterminate");
    fill.style.width = pct + "%";
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", "100");
    progress.setAttribute("aria-valuenow", pct.toFixed(0));
  } else {
    progress.classList.add("hpc-progress-indeterminate");
    fill.style.width = "";
    progress.setAttribute("role", "progressbar");
    progress.removeAttribute("aria-valuenow");
  }
  var amount = total > 0 && completed != null ? " " + hpcFormatBytes(completed) + " / " + hpcFormatBytes(total) : "";
  text.textContent = (data.status || "ダウンロード中") + (total > 0 ? " " + pct.toFixed(0) + "%" : "") + amount;
}
function hpcOllamaFinishProgress(message, ok) {
  var box = document.getElementById("ollama-pull-progress");
  var text = document.getElementById("ollama-pull-progress-text");
  var fill = document.getElementById("ollama-pull-progress-fill");
  if (!box || !text || !fill) return;
  var progress = fill.parentElement;
  box.hidden = false;
  progress.classList.remove("hpc-progress-indeterminate");
  progress.classList.toggle("hpc-progress-error", !ok);
  fill.style.width = ok ? "100%" : "";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", "100");
  progress.setAttribute("aria-valuenow", ok ? "100" : "0");
  text.textContent = message;
}
function hpcOllamaShowRegistration(data, model, reloadOnSuccess) {
  var registration = data.litellm_registration || {};
  var retry = document.getElementById("ollama-register-retry");
  if (registration.state === "registered" || registration.state === "already_registered") {
    hpcOllamaFinishProgress("ダウンロード完了・LiteLLM登録済み", true);
    hpcOllamaMsg(model + " をLiteLLMへ登録しました", true);
    if (retry) retry.hidden = true;
    if (reloadOnSuccess) window.setTimeout(function () { window.location.reload(); }, 900);
    return;
  }
  hpcOllamaFinishProgress("ダウンロード完了・LiteLLM登録失敗", false);
  hpcOllamaMsg(registration.message || "LiteLLM登録に失敗しました", false);
  if (retry) {
    retry.setAttribute("data-model", model);
    retry.hidden = false;
    retry.disabled = false;
  }
}
function hpcOllamaRegisterRetry(btn) {
  var model = btn.getAttribute("data-model") || "";
  if (!model || btn.disabled) return;
  btn.disabled = true;
  btn.textContent = "登録中…";
  hpcOllamaPost({action: "ollama_register_model", model: model}).then(function (body) {
    hpcOllamaShowRegistration({litellm_registration: body.data || {}}, model, true);
  }).catch(function (e) {
    hpcOllamaMsg(e.message, false);
    btn.disabled = false;
  }).finally(function () {
    btn.textContent = "LiteLLM登録を再試行";
  });
}
function hpcOllamaPollPull(model, btn, attempts, failures) {
  attempts = attempts || 0;
  failures = failures || 0;
  if (btn && btn.dataset.pullState !== "pulling") return;
  hpcOllamaPost({action: "ollama_pull_status", model: model}).then(function (body) {
    if (btn && btn.dataset.pullState !== "pulling") return;
    var data = body.data || {};
    if (data.state === "pulling") {
      hpcOllamaRenderProgress(data);
      window.setTimeout(function () { hpcOllamaPollPull(model, btn, attempts + 1, 0); }, 1200);
      return;
    }
    if (data.state === "completed") {
      hpcOllamaSetPullButton(btn, "idle", "");
      hpcOllamaShowRegistration(data, model, true);
      return;
    }
    if (data.state === "idle" && attempts < 10) {
      hpcOllamaRenderProgress({status: "ダウンロードを準備しています"});
      window.setTimeout(function () { hpcOllamaPollPull(model, btn, attempts + 1, 0); }, 1200);
      return;
    }
    if (data.state === "busy") {
      hpcOllamaFinishProgress("別のモデルをダウンロード中です", false);
      hpcOllamaMsg((data.active_model || "別のモデル") + " をダウンロード中です", false);
    } else if (data.state === "failed") {
      hpcOllamaFinishProgress("ダウンロードに失敗しました", false);
      hpcOllamaMsg(data.error || model + " のダウンロードに失敗しました", false);
    } else if (data.state === "cancelled") {
      hpcOllamaHideProgress();
      hpcOllamaMsg(model + " のダウンロードを中止しました", true);
    } else if (data.state === "cancelled_cleanup_failed") {
      hpcOllamaSetPullButton(btn, "cleanup_failed", model);
      hpcOllamaFinishProgress("中止済み・途中データの削除に失敗", false);
      hpcOllamaMsg("途中データを削除できませんでした。削除を再試行してください", false);
      return;
    } else {
      hpcOllamaFinishProgress("状態を確認できません", false);
      hpcOllamaMsg("ダウンロード状態を確認できません", false);
    }
    hpcOllamaSetPullButton(btn, "idle", "");
  }).catch(function (e) {
    if (btn && btn.dataset.pullState !== "pulling") return;
    if (failures < 3) {
      hpcOllamaRenderProgress({status: "通信を再試行しています"});
      window.setTimeout(function () { hpcOllamaPollPull(model, btn, attempts, failures + 1); }, 2000);
      return;
    }
    hpcOllamaFinishProgress("進捗の取得に失敗しました", false);
    hpcOllamaMsg(e.message, false);
    hpcOllamaSetPullButton(btn, "idle", "");
  });
}
document.addEventListener("DOMContentLoaded", function () {
  if (!document.getElementById("ollama-pull-progress")) return;
  hpcOllamaPost({action: "ollama_pull_status"}).then(function (body) {
    var data = body.data || {};
    var btn = document.getElementById("ollama-pull-button");
    if (data.state === "pulling" && data.model) {
      hpcOllamaSetPullButton(btn, "pulling", data.model);
      hpcOllamaPollPull(data.model, btn, 0, 0);
    }
    if (data.state === "cancelled_cleanup_failed" && data.model) {
      hpcOllamaSetPullButton(btn, "cleanup_failed", data.model);
      hpcOllamaFinishProgress("中止済み・途中データの削除に失敗", false);
      hpcOllamaMsg("途中データを削除できませんでした。削除を再試行してください", false);
    }
    if (data.state === "completed" && data.model && (data.litellm_registration || {}).state === "failed") {
      hpcOllamaShowRegistration(data, data.model, false);
    }
  }).catch(function () {});
});
function hpcOllamaDelete(btn) {
  var model = btn.getAttribute("data-model") || "";
  if (!model || !confirm(model + " をOllamaとLiteLLMから削除しますか？")) return;
  btn.disabled = true;
  hpcOllamaPost({action: "ollama_delete", model: model}).then(function () {
    window.location.reload();
  }).catch(function (e) {
    hpcOllamaMsg(e.message, false);
    btn.disabled = false;
  });
}
function hpcOllamaStop(btn) {
  if (!confirm("Ollama を停止しますか？")) return;
  btn.disabled = true;
  hpcOllamaPost({action: "ollama_stop"}).then(function () {
    window.location.href = "/hub/home";
  }).catch(function (e) {
    alert(e.message);
    btn.disabled = false;
  });
}

// 我的问卷：新建、列表、复制链接、发布 / 关闭 / 重新开放 / 删除。

const listBox = document.getElementById("survey-list");

function shareUrl(s) {
  return location.origin + s.shareUrl;
}

async function copyLink(s) {
  try {
    await navigator.clipboard.writeText(shareUrl(s));
    toast("链接已复制", "success");
  } catch {
    window.prompt("复制下面的链接：", shareUrl(s));
  }
}

async function act(method, path, body, okMessage) {
  try {
    await api(method, path, body);
    toast(okMessage, "success");
    await loadList();
  } catch (err) {
    toast(err.message, "error");
  }
}

function reopenControls(s) {
  // 截止时间已过时，重新开放必须设置新的截止时间或取消截止
  const input = el("input", { type: "datetime-local", class: "small", "data-testid": "reopen-deadline" });
  const deadlinePassed = s.settings.deadline && new Date(s.settings.deadline) <= new Date();
  const box = el("div", { class: "row", hidden: true },
    deadlinePassed ? el("span", { class: "hint" }, "截止时间已过，请设置新的截止时间或取消截止：") : null,
    deadlinePassed ? input : null,
    el("button", {
      class: "btn btn-primary", "data-testid": "reopen-confirm",
      onclick: () => {
        if (!deadlinePassed) return act("POST", `/api/surveys/${s.id}/reopen`, undefined, "已重新开放");
        if (!input.value) return toast("请选择新的截止时间", "error");
        return act("POST", `/api/surveys/${s.id}/reopen`, { deadline: fromLocalInput(input.value) }, "已重新开放");
      },
    }, deadlinePassed ? "按新截止时间开放" : "确认重新开放"),
    deadlinePassed ? el("button", {
      class: "btn", "data-testid": "reopen-no-deadline",
      onclick: () => act("POST", `/api/surveys/${s.id}/reopen`, { deadline: null }, "已重新开放（不再截止）"),
    }, "取消截止并开放") : null,
  );
  return box;
}

function surveyCard(s) {
  const reopenBox = reopenControls(s);
  const actions = el("div", { class: "row" });
  const add = (label, handler, cls = "btn", testid = null) =>
    actions.append(el("button", { class: cls, onclick: handler, "data-testid": testid }, label));

  if (s.phase === "draft") {
    add("编辑题目", () => { location.href = `/static/edit.html?id=${s.id}`; }, "btn btn-primary", "edit");
  } else {
    add("查看题目", () => { location.href = `/static/edit.html?id=${s.id}`; }, "btn", "view");
    add("统计", () => { location.href = `/static/stats.html?id=${s.id}`; }, "btn btn-primary", "stats");
    add("复制链接", () => copyLink(s), "btn", "copy-link");
  }
  if (s.phase === "open") {
    add("关闭", () => act("POST", `/api/surveys/${s.id}/close`, undefined, "已关闭"), "btn", "close");
  }
  if (s.phase === "closed" || s.phase === "expired") {
    add("重新开放", () => { reopenBox.hidden = !reopenBox.hidden; }, "btn", "reopen");
  }
  add("删除", () => {
    const tail = s.responseCount ? `已收到的 ${s.responseCount} 份答卷将无法再查看。` : "";
    if (window.confirm(`确定删除「${s.title}」吗？${tail}`)) {
      act("DELETE", `/api/surveys/${s.id}`, undefined, "已删除");
    }
  }, "btn btn-danger", "delete");

  return el("div", { class: "card", "data-testid": "survey-card", "data-survey-id": s.id },
    el("div", { class: "spread" },
      el("div", {},
        el("h3", { "data-testid": "survey-title" }, s.title),
        el("p", { class: "muted" },
          el("span", { class: `badge badge-${s.phase}`, "data-testid": "survey-phase" }, PHASE_LABELS[s.phase]),
          `  ${s.questionCount} 道题 · ${s.responseCount} 份答卷 · 创建于 ${fmtTime(s.createdAt)}`,
          s.settings.deadline ? ` · 截止 ${fmtTime(s.settings.deadline)}` : "",
          s.settings.allowAnonymous ? " · 匿名" : "",
          s.settings.allowMultipleSubmissions ? "" : " · 每人限填一次",
        ),
        s.phase === "draft" ? null
          : el("p", {}, "分享链接：", el("span", { class: "share", "data-testid": "share-url" }, shareUrl(s))),
      ),
    ),
    actions,
    reopenBox,
  );
}

async function loadList() {
  try {
    const surveys = await api("GET", "/api/surveys");
    clear(listBox);
    if (surveys.length === 0) {
      listBox.append(el("p", { class: "muted" }, "还没有问卷，先在上面新建一份吧。"));
    }
    surveys.forEach((s) => listBox.append(surveyCard(s)));
  } catch (err) {
    clear(listBox).append(el("p", { class: "error" }, err.message));
  }
}

document.getElementById("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const s = await api("POST", "/api/surveys", {
      title: document.getElementById("new-title").value,
      description: document.getElementById("new-description").value,
      settings: {
        allowAnonymous: document.getElementById("new-anonymous").checked,
        allowMultipleSubmissions: document.getElementById("new-multiple").checked,
        deadline: fromLocalInput(document.getElementById("new-deadline").value),
      },
    });
    location.href = `/static/edit.html?id=${s.id}`;
  } catch (err) {
    toast(err.message, "error");
  }
});

if (requireLogin()) {
  renderHeader();
  loadList();
}

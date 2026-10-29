// 题目详情：当前内容、版本历史（查看 / 基于任一版本修改 / 恢复 / 复制）、共享、加入题库、被哪些问卷使用、删除。

const app = document.getElementById("app");
const questionId = queryParam("id");
let question = null;
let versions = [];
let usage = null;
let myBanks = [];
let source = null;  // 复制来源的题目详情；原题已删除或不再共享给我时为 null
let panel = null;   // { kind: "view" | "edit", version: VersionOut }

async function reload() {
  [question, versions, usage] = await Promise.all([
    api("GET", `/api/questions/${questionId}`),
    api("GET", `/api/questions/${questionId}/versions`),
    api("GET", `/api/questions/${questionId}/usage`),
  ]);
  myBanks = question.isOwner ? await api("GET", "/api/banks?scope=mine") : [];
  source = question.derivedFrom
    ? await api("GET", `/api/questions/${question.derivedFrom.questionId}`).catch(() => null) : null;
  render();
}

// 这道题是从哪道题复制来的（需求四）：看得到原题时显示标题并可点进去
function derivedFromText() {
  const d = question.derivedFrom;
  if (!d) return null;
  return el("span", { "data-testid": "derived-from" }, " · 复制自 ",
    source ? el("a", { href: `/static/question.html?id=${d.questionId}` }, `「${source.content.title}」`)
      : "另一道题（原题已删除或不再共享给你）",
    ` v${d.version}`);
}

// 修改前先看影响范围（需求六）：保存只生成新版本，正在用的问卷不受影响
function usageNotice() {
  const total = usage.totalSurveys;
  const text = total === 0 ? "目前没有问卷在用这道题。"
    : `目前有 ${total} 份问卷在用这道题（${usage.versions.map((v) => `v${v.version} ${v.mine + v.others} 份`).join("，")}）。`;
  return el("p", { class: "notice", "data-testid": "edit-usage-notice" }, text,
    `保存后生成新版本 v${question.latest.version + 1}，已有的问卷仍用原来的版本，不受影响；需要时可在问卷编辑页把草稿切换到新版本。`);
}

async function act(method, path, body, okMessage) {
  try {
    const result = await api(method, path, body);
    panel = null;
    toast(okMessage, "success");
    await reload();
    return result;
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
}

async function openVersion(kind, number) {
  try {
    panel = { kind, version: await api("GET", `/api/questions/${questionId}/versions/${number}`) };
    render();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function fork(number) {
  try {
    const copy = await api("POST", `/api/questions/${questionId}/fork`, { version: number });
    location.href = `/static/question.html?id=${copy.id}`;
  } catch (err) {
    toast(err.message, "error");
  }
}

// ---------- 版本历史 ----------

function versionTable() {
  const latest = question.latest.version;
  const rows = [...versions].reverse().map((v) => el("tr", { "data-testid": "version-row" },
    el("td", {}, el("b", {}, `v${v.version}`), v.version === latest ? el("span", { class: "muted" }, " 最新") : null),
    el("td", { "data-testid": "version-parent" }, v.parentVersion ? `v${v.parentVersion}` : "—"),
    el("td", {}, KIND_LABELS[v.kind] || v.kind),
    el("td", {}, v.title, v.note ? el("div", { class: "muted" }, v.note) : null),
    el("td", { class: "muted" }, fmtTime(v.createdAt)),
    el("td", {}, el("div", { class: "row" },
      el("button", { class: "btn btn-link", "data-testid": "version-view", onclick: () => openVersion("view", v.version) }, "查看"),
      question.isOwner ? el("button", { class: "btn btn-link", "data-testid": "version-edit",
                                         onclick: () => openVersion("edit", v.version) }, "基于它修改") : null,
      question.isOwner && v.version !== latest ? el("button", {
        class: "btn btn-link", "data-testid": "version-restore",
        onclick: () => act("POST", `/api/questions/${questionId}/versions/${v.version}/restore`, undefined,
          `已用 v${v.version} 的内容生成新版本`),
      }, "恢复") : null,
      el("button", { class: "btn btn-link", "data-testid": "version-fork", onclick: () => fork(v.version) },
        "复制为我的题"))),
  ));
  return el("div", { class: "card", "data-testid": "version-history" },
    el("h2", {}, "版本历史"),
    el("p", { class: "hint" },
      "每次修改都生成新版本，旧版本不会改变；「基于」一栏是它从哪个版本改来的（可以基于任意旧版本修改）。" +
      "恢复旧版本 = 用它的内容生成一个新版本。"),
    el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["版本", "基于", "方式", "标题 / 说明", "时间", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))),
    versionPanel());
}

function versionPanel() {
  if (!panel) return null;
  const v = panel.version;
  if (panel.kind === "view") {
    return el("div", { class: "stack", "data-testid": "version-panel" },
      el("h3", {}, `v${v.version} 的内容`), contentView(v.content),
      el("button", { class: "btn", onclick: () => { panel = null; render(); } }, "收起"));
  }
  return el("div", { "data-testid": "version-panel" },
    el("h3", {}, `基于 v${v.version} 修改（保存后生成新版本）`),
    usageNotice(),
    questionForm(v.content, (body, note) => act("POST", `/api/questions/${questionId}/versions`,
      { baseVersion: v.version, content: body, note }, "已生成新版本"),
    { lockType: true, withNote: true, submitLabel: "保存为新版本", onCancel: () => { panel = null; render(); } }));
}

// ---------- 被哪些问卷使用 ----------

function usageCard() {
  const rows = usage.mySurveys.map((s) => el("tr", { "data-testid": "usage-row" },
    el("td", {}, el("a", { href: `/static/edit.html?id=${s.surveyId}` }, s.title)),
    el("td", {}, el("span", { class: `badge badge-${s.phase}` }, PHASE_LABELS[s.phase])),
    el("td", {}, `v${s.version}`, s.isLatest ? null : el("span", { class: "badge badge-new" },
      s.phase === "draft" ? "可切换到最新" : "旧版本")),
    el("td", {}, `第 ${s.questionNumber} 题`),
    el("td", {}, `${s.responseCount} 份答卷`)));
  return el("div", { class: "card", "data-testid": "usage" },
    el("h2", {}, "被哪些问卷使用"),
    el("p", { class: "muted", "data-testid": "usage-summary" },
      usage.versions.length === 0 ? "还没有问卷在用这道题。"
        : usage.versions.map((v) => `v${v.version}：我的 ${v.mine} 份` + (question.isOwner ? `、别人的 ${v.others} 份` : ""))
          .join("；")),
    question.isOwner && usage.othersTotal > 0
      ? el("p", { class: "hint" }, `另有 ${usage.othersTotal} 份别人的问卷在用（不显示标题）。修改只会生成新版本，不影响它们。`) : null,
    rows.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["我的问卷", "状态", "版本", "位置", "答卷"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))) : null,
    el("p", { class: "hint" }, "草稿里用的是旧版本时，可以到问卷编辑页的这道题上「切换版本」；已经脱钩的问卷不计入。"));
}

// ---------- 其他 ----------

function bankControls() {
  if (!question.isOwner) return null;
  const select = el("select", { class: "small", "data-testid": "bank-select" },
    myBanks.map((b) => el("option", { value: b.id }, b.name)));
  return el("div", { class: "card stack" },
    el("h2", {}, "加入题库"),
    myBanks.length === 0 ? el("p", { class: "muted" }, "你还没有题库，可以在题库首页的「我的题库」里新建。") :
      el("div", { class: "row" }, select, el("button", {
        class: "btn", "data-testid": "bank-add",
        onclick: () => act("POST", `/api/banks/${select.value}/items`, { questionId }, "已加入题库"),
      }, "加入")));
}

function render() {
  const c = question.content;
  clear(app).append(el("div", {},
    el("p", {}, el("a", { href: "/static/library.html" }, "← 返回题库")),
    el("div", { class: "spread" },
      el("div", {},
        el("h1", { "data-testid": "question-heading" }, c.title),
        el("p", { class: "muted" }, `${TYPE_LABELS[c.type]} · 最新 v${question.latest.version} · 创建者 ${question.owner}`,
          derivedFromText())),
      el("div", { class: "row" },
        el("a", { class: "btn btn-primary", href: `/static/question-stats.html?id=${questionId}`, "data-testid": "to-cross-stats" },
          "跨问卷统计"),
        question.isOwner ? el("button", {
          class: "btn btn-danger", "data-testid": "question-delete",
          onclick: async () => {
            if (!window.confirm(`删除「${c.title}」？已经用到它的问卷不受影响。`)) return;
            try {
              await api("DELETE", `/api/questions/${questionId}`);
              location.href = "/static/library.html";
            } catch (err) { toast(err.message, "error"); }
          },
        }, "删除") : null)),
    question.isOwner ? null : el("p", { class: "notice" },
      `这道题由 ${question.owner} 共享给你：可以用到自己的问卷里，或复制为自己的题后再修改。`),
    el("div", { class: "card" }, el("h2", {}, "当前内容"), contentView(c)),
    versionTable(),
    usageCard(),
    question.isOwner ? shareBox(question.sharedWith || [],
      (username) => act("POST", `/api/questions/${questionId}/shares`, { username }, `已共享给 ${username}`),
      (username) => act("DELETE", `/api/questions/${questionId}/shares/${encodeURIComponent(username)}`, undefined, "已取消共享"))
      : null,
    bankControls(),
  ));
}

if (requireLogin()) {
  renderHeader();
  reload().catch((err) => clear(app).append(el("p", { class: "error" }, err.message)));
}

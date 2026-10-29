// 题库首页：我的题目 / 共享给我的题目 / 我的题库 / 共享给我的题库；新建题目、新建题库。

const app = document.getElementById("app");
const TABS = [
  ["mine", "我的题目", "保存常用的题，以后建问卷时直接选用。修改会生成新版本，用旧版本的问卷不受影响。"],
  ["shared", "共享给我的题目", "别人直接共享给你的题：可以查看、用到自己的问卷里、复制为自己的题，但不能修改。"],
  ["banks", "我的题库", "把一组题整理成题库（如「学生基本信息」），可以整组共享给别人。"],
  ["shared-banks", "共享给我的题库", "别人共享给你的题库：里面的题你都可以查看和使用。"],
];
let tab = TABS.some(([k]) => k === location.hash.slice(1)) ? location.hash.slice(1) : "mine";
let creating = false;

function questionItem(q) {
  return el("div", { class: "list-item", "data-testid": "library-item" },
    el("div", {},
      el("a", { href: `/static/question.html?id=${q.id}`, "data-testid": "library-item-title" }, q.latest.title),
      el("div", { class: "muted" },
        `${TYPE_LABELS[q.latest.type]} · 最新 v${q.latest.version} · 更新于 ${fmtTime(q.updatedAt)}`,
        q.isOwner ? "" : ` · 创建者 ${q.owner}`,
        q.derivedFrom ? ` · 复制而来（来源 v${q.derivedFrom.version}，详见题目页）` : "")),
    el("a", { class: "btn", href: `/static/question-stats.html?id=${q.id}` }, "跨问卷统计"));
}

function bankItem(b) {
  return el("div", { class: "list-item", "data-testid": "bank-item" },
    el("div", {},
      el("a", { href: `/static/bank.html?id=${b.id}`, "data-testid": "bank-item-title" }, b.name),
      el("div", { class: "muted" }, `${b.itemCount} 道题`, b.isOwner ? "" : ` · 创建者 ${b.owner}`,
        b.description ? ` · ${b.description}` : "")));
}

function newQuestionForm() {
  return questionForm(null, async (body, note) => {
    try {
      const q = await api("POST", "/api/questions", { content: body, note });
      location.href = `/static/question.html?id=${q.id}`;
    } catch (err) {
      toast(err.message, "error");
    }
  }, { onCancel: () => { creating = false; render(); }, submitLabel: "保存到题库" });
}

function newBankForm() {
  const name = el("input", { type: "text", maxlength: 100, "data-testid": "bank-name" });
  const description = el("textarea", { maxlength: 1000, "data-testid": "bank-description" });
  return el("form", {
    class: "card stack", "data-testid": "bank-form",
    onsubmit: async (e) => {
      e.preventDefault();
      try {
        const bank = await api("POST", "/api/banks", { name: name.value, description: description.value });
        location.href = `/static/bank.html?id=${bank.id}`;
      } catch (err) {
        toast(err.message, "error");
      }
    },
  },
    el("label", {}, "名称", name),
    el("label", {}, "说明（可选）", description),
    el("div", { class: "row" },
      el("button", { type: "submit", class: "btn btn-primary", "data-testid": "bank-save" }, "创建题库"),
      el("button", { type: "button", class: "btn", onclick: () => { creating = false; render(); } }, "取消")));
}

async function tabBody() {
  if (tab === "mine" || tab === "shared") {
    const questions = await api("GET", `/api/questions?scope=${tab}`);
    return el("div", {},
      tab === "mine" ? (creating ? newQuestionForm()
        : el("button", { class: "btn btn-primary", "data-testid": "new-question",
                         onclick: () => { creating = true; render(); } }, "新建题目")) : null,
      el("div", { class: "card", "data-testid": "library-list" },
        questions.length ? questions.map(questionItem) : el("p", { class: "muted" }, "还没有题目。")));
  }
  const banks = await api("GET", `/api/banks?scope=${tab === "banks" ? "mine" : "shared"}`);
  return el("div", {},
    tab === "banks" ? (creating ? newBankForm()
      : el("button", { class: "btn btn-primary", "data-testid": "new-bank",
                       onclick: () => { creating = true; render(); } }, "新建题库")) : null,
    el("div", { class: "card", "data-testid": "bank-list" },
      banks.length ? banks.map(bankItem) : el("p", { class: "muted" }, "还没有题库。")));
}

async function render() {
  let body;
  try {
    body = await tabBody();
  } catch (err) {
    body = el("p", { class: "error" }, err.message);
  }
  const hint = TABS.find(([k]) => k === tab)[2];
  clear(app).append(el("div", {},
    el("h1", {}, "题库"),
    el("div", { class: "tabs" }, TABS.map(([k, label]) => el("button", {
      class: "tab" + (k === tab ? " active" : ""), "data-testid": `library-tab-${k}`,
      onclick: () => { tab = k; creating = false; location.hash = k; render(); },
    }, label))),
    el("p", { class: "hint" }, hint),
    body));
}

if (requireLogin()) {
  renderHeader();
  render();
}

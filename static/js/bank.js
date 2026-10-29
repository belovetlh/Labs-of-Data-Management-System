// 题库详情：题目列表（加入 / 移出）、名称和说明、共享、删除。只有创建者能修改。

const app = document.getElementById("app");
const bankId = queryParam("id");
let bank = null;
let myQuestions = [];

async function reload() {
  bank = await api("GET", `/api/banks/${bankId}`);
  myQuestions = bank.isOwner ? await api("GET", "/api/questions?scope=mine") : [];
  render();
}

async function act(method, path, body, okMessage) {
  try {
    await api(method, path, body);
    toast(okMessage, "success");
    await reload();
  } catch (err) {
    toast(err.message, "error");
  }
}

function itemRow(q) {
  return el("div", { class: "list-item", "data-testid": "bank-question" },
    el("div", {},
      el("a", { href: `/static/question.html?id=${q.id}` }, q.latest.title),
      el("div", { class: "muted" }, `${TYPE_LABELS[q.latest.type]} · 最新 v${q.latest.version} · 加入于 ${fmtTime(q.addedAt)}`)),
    bank.isOwner ? el("button", {
      class: "btn btn-danger", "data-testid": "bank-remove",
      onclick: () => act("DELETE", `/api/banks/${bankId}/items/${q.id}`, undefined, "已移出题库"),
    }, "移出") : null);
}

function addControls() {
  const inBank = new Set(bank.items.map((q) => q.id));
  const candidates = myQuestions.filter((q) => !inBank.has(q.id));
  const select = el("select", { class: "small", "data-testid": "bank-candidate" },
    candidates.map((q) => el("option", { value: q.id }, q.latest.title)));
  return el("div", { class: "row" },
    candidates.length === 0 ? el("span", { class: "muted" }, "你的题都已经在这个题库里了（只能加入自己创建的题）。") : [
      select,
      el("button", { class: "btn", "data-testid": "bank-add-item",
                     onclick: () => act("POST", `/api/banks/${bankId}/items`, { questionId: select.value }, "已加入") },
        "加入题库"),
    ]);
}

function infoForm() {
  const name = el("input", { type: "text", maxlength: 100, value: bank.name, "data-testid": "bank-name" });
  const description = el("textarea", { maxlength: 1000, "data-testid": "bank-description" }, bank.description);
  return el("form", {
    class: "card stack",
    onsubmit: (e) => {
      e.preventDefault();
      act("PATCH", `/api/banks/${bankId}`, { name: name.value, description: description.value }, "已保存");
    },
  },
    el("h2", {}, "名称和说明"),
    el("label", {}, "名称", name),
    el("label", {}, "说明", description),
    el("div", { class: "row" },
      el("button", { type: "submit", class: "btn" }, "保存"),
      el("button", {
        type: "button", class: "btn btn-danger", "data-testid": "bank-delete",
        onclick: async () => {
          if (!window.confirm(`删除题库「${bank.name}」？里面的题不会被删除。`)) return;
          try {
            await api("DELETE", `/api/banks/${bankId}`);
            location.href = "/static/library.html#banks";
          } catch (err) { toast(err.message, "error"); }
        },
      }, "删除题库")));
}

function render() {
  clear(app).append(el("div", {},
    el("p", {}, el("a", { href: `/static/library.html#${bank.isOwner ? "banks" : "shared-banks"}` }, "← 返回题库")),
    el("h1", { "data-testid": "bank-heading" }, bank.name),
    el("p", { class: "muted" }, `${bank.itemCount} 道题 · 创建者 ${bank.owner}`),
    bank.description ? el("p", {}, bank.description) : null,
    bank.isOwner ? null : el("p", { class: "notice" },
      `这个题库由 ${bank.owner} 共享给你：里面的题都可以查看、用到自己的问卷里或复制为自己的题。`),
    el("div", { class: "card", "data-testid": "bank-items" },
      el("h2", {}, "题目"),
      bank.items.length ? bank.items.map(itemRow) : el("p", { class: "muted" }, "题库里还没有题。"),
      bank.isOwner ? addControls() : null),
    bank.isOwner ? shareBox(bank.sharedWith || [],
      (username) => act("POST", `/api/banks/${bankId}/shares`, { username }, `已共享给 ${username}`),
      (username) => act("DELETE", `/api/banks/${bankId}/shares/${encodeURIComponent(username)}`, undefined, "已取消共享"))
      : null,
    bank.isOwner ? infoForm() : null,
  ));
}

if (requireLogin()) {
  renderHeader();
  reload().catch((err) => clear(app).append(el("p", { class: "error" }, err.message)));
}

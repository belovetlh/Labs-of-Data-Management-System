// 统计页：选择题用纯 CSS 条形图；数字题显示平均 / 最小 / 最大；填空题显示最新 5 条，可分页查看全部。

const app = document.getElementById("app");
const surveyId = queryParam("id");
const PAGE_SIZE = 10;

function entryItem(e) {
  return el("li", { "data-testid": "entry" },
    el("div", {}, String(e.value)),
    el("div", { class: "muted" }, fmtTime(e.submittedAt), e.respondent ? ` · ${e.respondent}` : ""));
}

function choiceBlock(q) {
  return el("div", {}, q.options.map((o) => barRow(o.label, o.percent, `${o.count} · ${o.percent}%`)));
}

function entriesBlock(q) {
  const list = el("ul", { class: "entries" }, (q.entries || []).map(entryItem));
  const pager = el("div", { class: "row" });
  let page = 0;

  async function loadPage(p) {
    try {
      const data = await api("GET", `/api/surveys/${surveyId}/stats/${q.id}?page=${p}&pageSize=${PAGE_SIZE}`);
      page = p;
      clear(list).append(...data.entries.map(entryItem));
      const pages = Math.max(1, Math.ceil(data.entriesTotal / PAGE_SIZE));
      clear(pager).append(
        el("button", { class: "btn", disabled: page <= 1, onclick: () => loadPage(page - 1) }, "上一页"),
        el("span", { class: "muted", "data-testid": "page-info" }, `第 ${page} / ${pages} 页`),
        el("button", { class: "btn", disabled: page >= pages, "data-testid": "next-page", onclick: () => loadPage(page + 1) }, "下一页"),
      );
    } catch (err) {
      toast(err.message, "error");
    }
  }

  if (q.entriesTotal > (q.entries || []).length) {
    pager.append(el("button", { class: "btn-link btn", "data-testid": "show-all", onclick: () => loadPage(1) },
      `查看全部 ${q.entriesTotal} 条`));
  }
  return el("div", {},
    q.entriesTotal === 0 ? el("p", { class: "muted" }, "还没有人填写。") : list,
    pager);
}

function questionBlock(q, i, total) {
  return el("div", { class: "card", "data-testid": "question-stat", "data-question-id": q.id },
    el("h3", {}, `${i + 1}. `, q.title, el("span", { class: "muted" }, `  ${TYPE_LABELS[q.type]}`)),
    el("p", { class: "muted", "data-testid": "answered" }, `回答人数 ${q.answeredCount} / 总提交 ${total}`),
    q.options ? choiceBlock(q) : null,
    q.type === "number_input" ? el("div", { class: "stat-numbers" },
      el("div", {}, el("b", { "data-testid": "avg" }, q.average ?? "—"), "平均值"),
      el("div", {}, el("b", {}, q.min ?? "—"), "最小值"),
      el("div", {}, el("b", {}, q.max ?? "—"), "最大值")) : null,
    q.options ? null : entriesBlock(q),
  );
}

async function load() {
  try {
    const stats = await api("GET", `/api/surveys/${surveyId}/stats`);
    clear(app).append(el("div", {},
      el("p", {}, el("a", { href: "/static/surveys.html" }, "← 返回我的问卷")),
      el("h1", { "data-testid": "stats-title" }, stats.title),
      el("p", { class: "muted", "data-testid": "total" }, `共收到 ${stats.totalResponses} 份答卷`),
      stats.questions.map((q, i) => questionBlock(q, i, stats.totalResponses)),
    ));
  } catch (err) {
    clear(app).append(el("p", { class: "error" }, err.message));
  }
}

if (requireLogin()) {
  renderHeader();
  load();
}

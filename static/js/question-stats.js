// 跨问卷统计：一道题库题在自己所有问卷里的回答情况。默认合并全部版本，可只看某个版本；
// 选择题按选项 id 合并并标出改过文字的选项；数字题显示区间分布；文本题分页列出内容。

const app = document.getElementById("app");
const questionId = queryParam("id");
const PAGE_SIZE = 10;
const params = { version: "", bucketSize: "", page: 1 };
let versions = [];

function query() {
  const q = new URLSearchParams({ page: params.page, pageSize: PAGE_SIZE });
  if (params.version) q.set("version", params.version);
  if (params.bucketSize) q.set("bucketSize", params.bucketSize);
  return q.toString();
}

async function load() {
  try {
    const stats = await api("GET", `/api/questions/${questionId}/stats?${query()}`);
    render(stats);
  } catch (err) {
    if (app.querySelector("[data-testid=cross-heading]")) toast(err.message, "error");   // 保留上一次的结果
    else clear(app).append(el("p", { class: "error" }, err.message));
  }
}

function controls(stats) {
  const versionSelect = el("select", { class: "small", "data-testid": "cross-version" },
    el("option", { value: "" }, "全部版本（合并）"),
    versions.map((v) => el("option", { value: v.version }, `只看 v${v.version}`)));
  versionSelect.value = params.version;
  versionSelect.addEventListener("change", () => { params.version = versionSelect.value; params.page = 1; load(); });
  const bucket = el("input", {
    type: "number", step: "any", min: 0, class: "small", "data-testid": "cross-bucket",
    placeholder: stats.bucketSize ? `自动（${stats.bucketSize}）` : "自动", value: params.bucketSize,
  });
  return el("div", { class: "card row" },
    "范围", versionSelect,
    stats.type === "number_input" ? [
      el("span", { class: "muted" }, " 区间宽度"), bucket,
      el("button", { class: "btn", "data-testid": "cross-bucket-apply",
                     onclick: () => { params.bucketSize = bucket.value; load(); } }, "应用"),
    ] : null);
}

function choiceCard(stats) {
  const allVersions = new Set(stats.bySurvey.map((s) => s.version));
  return el("div", { class: "card" },
    el("h2", {}, "选项分布"),
    stats.options.map((o) => {
      const label = el("span", {}, o.label,
        o.otherLabels.length ? el("span", { class: "badge badge-new", "data-testid": "other-labels",
                                            title: "选项 id 相同、文字改过" }, `也叫「${o.otherLabels.join("」「")}」`) : null,
        o.versions.length < allVersions.size ? el("span", { class: "muted" }, ` 仅 ${o.versions.map((v) => `v${v}`).join("、")}`) : null);
      return barRow(label, o.percent, `${o.count} · ${o.percent}%`);
    }),
    el("p", { class: "hint" },
      "不同版本里 id 相同的选项合并计数，显示最新版本的文字；标了「也叫」的选项在别的版本里文字不同，" +
      "如果改字改变了含义，可以在上方只看某个版本。占比的分母是回答了这道题的人数" +
      (stats.type === "multiple_choice" ? "；多选题各项相加可超过 100%。" : "。")));
}

function numberCard(stats) {
  const answered = stats.answeredCount;
  return el("div", { class: "card" },
    el("h2", {}, "数值分布"),
    el("div", { class: "stat-numbers" },
      el("div", {}, el("b", { "data-testid": "cross-avg" }, stats.average ?? "—"), "平均值"),
      el("div", {}, el("b", {}, stats.min ?? "—"), "最小值"),
      el("div", {}, el("b", {}, stats.max ?? "—"), "最大值")),
    stats.distribution.length === 0 ? el("p", { class: "muted" }, "还没有数据。") :
      el("div", {}, stats.distribution.map((b) => {
        const percent = answered ? Math.round(b.count * 10000 / answered) / 100 : 0;
        return barRow(`${b.from} ~ ${b.to}`, percent, `${b.count} 人 · ${percent}%`, "bucket");
      })),
    el("p", { class: "hint" }, `区间宽度 ${stats.bucketSize ?? "—"}，含下界、不含上界（如「20 ~ 25」包含 20，不包含 25）。`));
}

function textCard(stats) {
  const pages = Math.max(1, Math.ceil(stats.entriesTotal / PAGE_SIZE));
  const go = (p) => { params.page = p; load(); };
  return el("div", { class: "card" },
    el("h2", {}, "填写内容"),
    stats.entriesTotal === 0 ? el("p", { class: "muted" }, "还没有人填写。") :
      el("ul", { class: "entries" }, stats.entries.map((e) => el("li", { "data-testid": "entry" },
        el("div", {}, String(e.value)),
        el("div", { class: "muted" }, `${e.surveyTitle} · ${fmtTime(e.submittedAt)}`, e.respondent ? ` · ${e.respondent}` : "")))),
    pages > 1 ? el("div", { class: "row" },
      el("button", { class: "btn", disabled: stats.page <= 1, onclick: () => go(stats.page - 1) }, "上一页"),
      el("span", { class: "muted", "data-testid": "page-info" }, `第 ${stats.page} / ${pages} 页`),
      el("button", { class: "btn", disabled: stats.page >= pages, "data-testid": "next-page",
                     onclick: () => go(stats.page + 1) }, "下一页")) : null,
    el("p", { class: "hint" }, "匿名问卷的答卷不显示填写者。"));
}

function surveyTable(stats) {
  return el("div", { class: "card", "data-testid": "by-survey" },
    el("h2", {}, "各问卷分别多少"),
    el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["问卷", "版本", "位置", "回答 / 总提交"].map((h) => el("th", {}, h)))),
      el("tbody", {}, stats.bySurvey.map((s) => el("tr", { "data-testid": "by-survey-row" },
        el("td", {}, el("a", { href: `/static/stats.html?id=${s.surveyId}` }, s.title)),
        el("td", {}, `v${s.version}`),
        el("td", {}, `第 ${s.questionNumber} 题`),
        el("td", {}, `${s.answeredCount} / ${s.totalResponses}`)))))));
}

function render(stats) {
  const body = stats.surveyCount === 0
    ? el("div", { class: "card" }, el("p", { class: "muted", "data-testid": "cross-empty" },
      params.version ? "你的问卷里没有用这个版本的。" :
        "你还没有问卷用到这道题。从题库把它加入问卷，或把问卷里的题存入题库后，答卷才会计入这里。"))
    : [
      stats.options ? choiceCard(stats) : null,
      stats.type === "number_input" ? numberCard(stats) : null,
      stats.type === "text_input" ? textCard(stats) : null,
      surveyTable(stats),
    ];
  clear(app).append(el("div", {},
    el("p", {}, el("a", { href: `/static/question.html?id=${questionId}` }, "← 返回题目")),
    el("h1", { "data-testid": "cross-heading" }, `跨问卷统计：${stats.title}`),
    el("p", { class: "muted", "data-testid": "cross-summary" },
      `${TYPE_LABELS[stats.type]} · ${stats.surveyCount} 份问卷 · 共 ${stats.totalResponses} 份答卷 · ` +
      `${stats.answeredCount} 份回答了这道题`),
    el("p", { class: "hint" }, "只统计你自己的问卷；别人用这道题收集的数据不会出现在这里。已脱钩的题不计入。"),
    controls(stats),
    body));
}

if (requireLogin()) {
  renderHeader();
  api("GET", `/api/questions/${questionId}/versions`)
    .then((v) => { versions = v; return load(); })
    .catch((err) => clear(app).append(el("p", { class: "error" }, err.message)));
}

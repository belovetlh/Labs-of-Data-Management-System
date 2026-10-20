// 填写问卷：一题一页。下一题由服务端 navigate 按跳转规则计算（前端不实现跳转逻辑）。
//
// 答案约定（设计第 17 条）：answers 里没有某题 = 还没答到；值为 null = 看过、不答。
// 只在点"提交"时保存；中途离开页面，什么都不会留下。

const app = document.getElementById("app");
const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());

let view = null;          // 填写视图（标题、说明、题目……）
let answers = {};         // 题目 id → 值
let path = [];            // 最近一次 navigate 返回的答题路径
let current = null;       // 正在显示的题目 id；null 表示"确认提交"页
let errorText = "";

const byId = (qid) => view.questions.find((q) => q.id === qid);

function header() {
  return el("div", { class: "card" },
    el("h1", { "data-testid": "fill-title" }, view.title),
    view.description ? el("p", {}, view.description) : null,
    view.allowAnonymous ? el("p", { class: "hint", "data-testid": "anonymous-note" },
      "本问卷为匿名问卷，不会记录你的身份。") : null,
  );
}

function message(text, testid) {
  clear(app).append(header(), el("div", { class: "card" }, el("p", { "data-testid": testid }, text)));
}

function hintFor(q) {
  const v = q.validation || {};
  if (q.type === "multiple_choice") {
    if (v.minSelect != null && v.minSelect === v.maxSelect) return `请选择 ${v.minSelect} 项`;
    const parts = [];
    if (v.minSelect != null) parts.push(`至少 ${v.minSelect} 项`);
    if (v.maxSelect != null) parts.push(`最多 ${v.maxSelect} 项`);
    return parts.length ? `多选，${parts.join("，")}` : "多选";
  }
  if (q.type === "text_input") {
    const parts = [];
    if (v.minLength != null) parts.push(`至少 ${v.minLength} 字`);
    if (v.maxLength != null) parts.push(`最多 ${v.maxLength} 字`);
    return parts.join("，");
  }
  if (q.type === "number_input") {
    const parts = [v.integer ? "请输入整数" : "请输入数字"];
    if (v.min != null && v.max != null) parts.push(`范围 ${v.min}–${v.max}`);
    else if (v.min != null) parts.push(`不小于 ${v.min}`);
    else if (v.max != null) parts.push(`不大于 ${v.max}`);
    return parts.join("，");
  }
  return "";
}

// 渲染一道题的输入控件，返回 { node, read }；read() 取出当前的值（没填返回 null）
function inputFor(q) {
  const saved = answers[q.id];
  if (q.type === "single_choice") {
    const radios = q.options.map((o) => el("label", { class: "choice" },
      el("input", { type: "radio", name: q.id, value: o.id, checked: saved === o.id, "data-testid": "choice" }), o.label));
    return { node: el("div", {}, radios), read: () => {
      const r = radios.map((l) => l.querySelector("input")).find((i) => i.checked);
      return r ? r.value : null;
    } };
  }
  if (q.type === "multiple_choice") {
    const chosen = new Set(Array.isArray(saved) ? saved : []);
    const boxes = q.options.map((o) => el("label", { class: "choice" },
      el("input", { type: "checkbox", value: o.id, checked: chosen.has(o.id), "data-testid": "choice" }), o.label));
    return { node: el("div", {}, boxes), read: () => {
      const values = boxes.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
      return values.length ? values : null;
    } };
  }
  if (q.type === "text_input") {
    const t = el("textarea", { "data-testid": "text-answer" }, typeof saved === "string" ? saved : "");
    return { node: t, read: () => (t.value.trim() === "" ? null : t.value) };
  }
  const n = el("input", { type: "number", step: "any", "data-testid": "number-answer", value: saved ?? "" });
  return { node: n, read: () => (n.value === "" ? null : Number(n.value)) };
}

async function navigate() {
  return api("POST", `/api/fill/${slug}/navigate`, { answers });
}

function showQuestion(qid) {
  current = qid;
  const q = byId(qid);
  const index = path.indexOf(qid);
  const input = inputFor(q);
  // 有跳转时实际要答的题数事先不知道，进度按问卷总题数估算
  const total = view.questions.length;
  const position = view.questions.findIndex((x) => x.id === qid);
  clear(app).append(
    header(),
    el("div", { class: "card question", "data-testid": "fill-question", "data-question-id": q.id },
      el("p", { class: "muted", "data-testid": "fill-progress" }, `第 ${position + 1} 题 / 共 ${total} 题`),
      el("div", { class: "progress" }, el("div", { style: `width: ${Math.round((position / total) * 100)}%` })),
      el("h2", { "data-testid": "fill-question-title" }, q.title, q.required ? el("span", { class: "required" }, "*") : null),
      hintFor(q) ? el("p", { class: "hint" }, hintFor(q)) : null,
      input.node,
      el("p", { class: "error", "data-testid": "fill-error", role: "alert" }, errorText),
      el("div", { class: "row" },
        index > 0 ? el("button", { class: "btn", "data-testid": "prev", onclick: () => {
          answers[q.id] = input.read();   // 保留本题已填内容
          errorText = "";
          showQuestion(path[index - 1]);
        } }, "上一题") : null,
        el("button", { class: "btn btn-primary", "data-testid": "next", onclick: () => next(q, input) }, "下一题"),
      ),
    ),
  );
}

async function next(q, input) {
  answers[q.id] = input.read();   // 非必答题不填 → null：看过、不答
  let res;
  try {
    res = await navigate();
  } catch (err) {
    return toast(err.message, "error");
  }
  if (res.errors[q.id]) {
    errorText = res.errors[q.id];
    return showQuestion(q.id);
  }
  errorText = "";
  // 改了前面的答案后，按新的跳转不再经过的题：清掉它们的答案
  res.offPath.forEach((qid) => delete answers[qid]);
  path = res.path;
  const i = path.indexOf(q.id);
  if (i + 1 < path.length) return showQuestion(path[i + 1]);
  if (res.complete) return showReview();
  if (res.nextQuestion) return showQuestion(res.nextQuestion);
}

function showReview() {
  current = null;
  const answered = path.filter((qid) => answers[qid] !== null && answers[qid] !== undefined).length;
  clear(app).append(
    header(),
    el("div", { class: "card stack", "data-testid": "fill-review" },
      el("h2", {}, "已答完所有题目"),
      el("p", {}, `本次共经过 ${path.length} 道题，作答 ${answered} 道。提交后不能修改。`),
      el("div", { class: "row" },
        el("button", { class: "btn", "data-testid": "prev", onclick: () => showQuestion(path[path.length - 1]) }, "上一题"),
        el("button", { class: "btn btn-primary", "data-testid": "submit", onclick: submit }, "提交"),
      ),
    ),
  );
}

async function submit() {
  try {
    await api("POST", `/api/fill/${slug}/responses`, { answers });
    message("提交成功，感谢你的参与！", "fill-done");
  } catch (err) {
    if (err.status === 422 && err.data && err.data.detail && err.data.detail.errors) {
      // 回到第一道出错的题，显示错误
      const [qid, text] = Object.entries(err.data.detail.errors)[0];
      if (byId(qid)) {
        errorText = text;
        return showQuestion(qid);
      }
    }
    toast(err.message, "error");
  }
}

async function start() {
  try {
    view = await api("GET", `/api/fill/${slug}`);
  } catch (err) {
    const text = err.status === 410 ? "问卷已删除" : err.status === 404 ? "问卷不存在" : err.message;
    return clear(app).append(el("div", { class: "card" }, el("p", { "data-testid": "fill-unavailable" }, text)));
  }
  if (!view.canSubmit) return message(view.reason, "fill-unavailable");
  try {
    const res = await navigate();
    path = res.path;
    showQuestion(res.nextQuestion);
  } catch (err) {
    message(err.message, "fill-unavailable");
  }
}

if (requireLogin()) {
  renderHeader();
  start();
}

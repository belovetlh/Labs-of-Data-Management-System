// 编辑问卷（仅草稿）：基本信息、题目增删改与上下移动、跳转规则、发布。已发布的问卷只读。
// 阶段二：从题库选题、切换来自题库的题所用的版本、修改时选择"只改本问卷 / 存为新版本"、把题存入题库（已发布也可以）。

const app = document.getElementById("app");
const surveyId = queryParam("id");
let survey = null;
// 当前展开的面板：{ kind: "add" | "edit" | "rules" | "pick" | "switch" | "libraryChoice", qid, body }
let openPanel = null;

const OPS_BY_TYPE = {
  single_choice: [["eq", "选了"], ["in", "选了其中任一"]],
  multiple_choice: [["containsAny", "包含任一"], ["containsAll", "全部包含"], ["equals", "恰好是"]],
  number_input: [["eq", "等于"], ["ne", "不等于"], ["gt", "大于"], ["gte", "大于等于"],
                 ["lt", "小于"], ["lte", "小于等于"], ["between", "在区间内"]],
};

async function reload(detail) {
  survey = detail || await api("GET", `/api/surveys/${surveyId}`);
  render();
}

function closePanel() {
  openPanel = null;
  render();
}

async function call(method, path, body, okMessage) {
  try {
    const detail = await api(method, path, body);
    openPanel = null;
    if (okMessage) toast(okMessage, "success");
    await reload(detail);
    return true;
  } catch (err) {
    toast(err.message, "error");
    return false;
  }
}

// ---------- 题目摘要 ----------

function optionLabel(q, oid) {
  const o = (q.options || []).find((x) => x.id === oid);
  return o ? o.label : oid;
}

function questionNo(qid) {
  const i = survey.questions.findIndex((q) => q.id === qid);
  return i >= 0 ? `第 ${i + 1} 题` : qid;
}

function ruleText(q, r) {
  const op = (OPS_BY_TYPE[q.type] || []).find(([k]) => k === r.when.op);
  let value = r.when.value;
  if (q.type !== "number_input") {
    value = Array.isArray(value) ? value.map((v) => optionLabel(q, v)).join("、") : optionLabel(q, value);
  } else if (r.when.op === "between") {
    value = `${value[0]} ~ ${value[1]}`;
  }
  const target = r.goto === "END" ? "结束问卷" : questionNo(r.goto);
  return `如果${op ? op[1] : r.when.op}「${value}」→ 跳到${target}`;
}

// ---------- 跳转规则编辑器 ----------

function ruleEditor(q, index) {
  const later = survey.questions.slice(index + 1);
  const list = el("div", { "data-testid": "rule-list" });

  function valueControl(op, value) {
    if (q.type === "number_input") {
      if (op === "between") {
        const a = el("input", { type: "number", step: "any", class: "small", "data-testid": "rule-value-a", value: value ? value[0] : "" });
        const b = el("input", { type: "number", step: "any", class: "small", "data-testid": "rule-value-b", value: value ? value[1] : "" });
        return { node: el("span", { class: "row" }, a, "~", b), read: () => [numberOrNull(a), numberOrNull(b)] };
      }
      const n = el("input", { type: "number", step: "any", class: "small", "data-testid": "rule-value", value: value ?? "" });
      return { node: n, read: () => numberOrNull(n) };
    }
    if (q.type === "single_choice" && op === "eq") {
      const s = el("select", { class: "small", "data-testid": "rule-value" },
        q.options.map((o) => el("option", { value: o.id }, o.label)));
      if (value) s.value = value;
      return { node: s, read: () => s.value };
    }
    const chosen = new Set(Array.isArray(value) ? value : []);
    const boxes = q.options.map((o) => el("label", { class: "inline" },
      el("input", { type: "checkbox", value: o.id, checked: chosen.has(o.id), "data-testid": "rule-option" }), o.label));
    return {
      node: el("span", {}, boxes),
      read: () => boxes.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value),
    };
  }

  function addRow(rule) {
    const opSelect = el("select", { class: "small", "data-testid": "rule-op" },
      OPS_BY_TYPE[q.type].map(([k, label]) => el("option", { value: k }, label)));
    opSelect.value = rule ? rule.when.op : OPS_BY_TYPE[q.type][0][0];
    const gotoSelect = el("select", { class: "small", "data-testid": "rule-goto" },
      later.map((t) => el("option", { value: t.id }, `${questionNo(t.id)}：${t.title}`)),
      el("option", { value: "END" }, "结束问卷"));
    gotoSelect.value = rule ? rule.goto : (later[0] ? later[0].id : "END");
    const valueSlot = el("span");
    let control = null;
    const renderValue = (value) => { control = valueControl(opSelect.value, value); clear(valueSlot).append(control.node); };
    renderValue(rule ? rule.when.value : undefined);
    opSelect.addEventListener("change", () => renderValue(undefined));
    const row = el("div", { class: "rule-row", "data-testid": "rule-row" },
      "如果", opSelect, valueSlot, "→ 跳到", gotoSelect,
      el("button", { type: "button", class: "btn btn-danger", onclick: () => row.remove() }, "删除"));
    row.readRule = () => ({ when: { op: opSelect.value, value: control.read() }, goto: gotoSelect.value });
    list.append(row);
  }

  (q.jumpRules || []).forEach(addRow);

  return el("div", { class: "card stack", "data-testid": "rule-editor" },
    el("h3", {}, `${questionNo(q.id)}的跳转规则`),
    el("p", { class: "hint" }, "按顺序判断，第一条满足的生效；都不满足就进入下一题。只能跳到后面的题。"),
    list,
    el("div", { class: "row" },
      el("button", { type: "button", class: "btn", "data-testid": "rule-add", onclick: () => addRow(null) }, "添加规则"),
      el("button", {
        type: "button", class: "btn btn-primary", "data-testid": "rule-save",
        onclick: () => call("PUT", `/api/surveys/${surveyId}/questions/${q.id}/jump-rules`,
          { rules: [...list.children].map((r) => r.readRule()) }, "跳转规则已保存"),
      }, "保存规则"),
      el("button", { type: "button", class: "btn", onclick: closePanel }, "取消")),
  );
}

// ---------- 页面 ----------

function infoForm() {
  const title = el("input", { type: "text", value: survey.title, "data-testid": "info-title" });
  const desc = el("textarea", { "data-testid": "info-description" }, survey.description);
  const anon = el("input", { type: "checkbox", checked: survey.settings.allowAnonymous });
  const multi = el("input", { type: "checkbox", checked: survey.settings.allowMultipleSubmissions });
  const deadline = el("input", { type: "datetime-local", value: toLocalInput(survey.settings.deadline) });
  return el("form", {
    class: "card stack",
    onsubmit: (e) => {
      e.preventDefault();
      call("PATCH", `/api/surveys/${surveyId}`, {
        title: title.value, description: desc.value,
        settings: { allowAnonymous: anon.checked, allowMultipleSubmissions: multi.checked,
                    deadline: fromLocalInput(deadline.value) },
      }, "已保存");
    },
  },
    el("h2", {}, "基本信息"),
    el("label", {}, "标题", title),
    el("label", {}, "说明", desc),
    el("div", {}, el("label", { class: "inline" }, anon, "匿名问卷"),
      el("label", { class: "inline" }, multi, "同一人可以填写多次")),
    el("label", {}, "截止时间（留空表示不截止）", deadline),
    el("div", {}, el("button", { type: "submit", class: "btn" }, "保存基本信息")),
  );
}

function move(index, delta) {
  const ids = survey.questions.map((q) => q.id);
  const [moved] = ids.splice(index, 1);
  ids.splice(index + delta, 0, moved);
  call("PUT", `/api/surveys/${surveyId}/question-order`, { questionIds: ids });
}

// ---------- 阶段二：问卷 ↔ 题库 ----------

// 修改题目：来自题库的题改了内容时，后端返回 409 要求选择处理方式（是否"改了内容"只由后端判断，见设计 4.7 节）
async function saveQuestion(q, body, action) {
  const url = `/api/surveys/${surveyId}/questions/${q.id}` + (action ? `?libraryAction=${action}` : "");
  try {
    const detail = await api("PUT", url, body);
    openPanel = null;
    toast({ detach: "已保存；这道题不再关联题库", new_version: "已保存，并在题库中生成了新版本" }[action] || "已保存",
          "success");
    await reload(detail);
  } catch (err) {
    if (err.status === 409 && q.source && !action && err.message.includes("libraryAction")) {
      openPanel = { kind: "libraryChoice", qid: q.id, body };
      render();
      return;
    }
    toast(err.message, "error");
  }
}

function libraryChoicePanel(q) {
  const lib = q.library || {};
  const canNewVersion = lib.available && lib.canEdit;
  const body = openPanel.body;
  return el("div", { class: "card stack notice", "data-testid": "library-choice" },
    el("p", {}, `这道题来自题库（v${q.source.version}），你改了题目内容。请选择保存方式：`),
    el("div", { class: "row" },
      el("button", { class: "btn", "data-testid": "choice-detach", onclick: () => saveQuestion(q, body, "detach") },
        "只改本问卷"),
      canNewVersion ? el("button", {
        class: "btn btn-primary", "data-testid": "choice-new-version",
        onclick: () => saveQuestion(q, body, "new_version"),
      }, "存为题库新版本") : null,
      el("button", { class: "btn", onclick: () => { openPanel = { kind: "edit", qid: q.id, body }; render(); } },
        "返回修改")),
    el("p", { class: "hint" }, "只改本问卷：这道题与题库脱钩，题库和别的问卷都不受影响，以后也不再计入这道题的跨问卷统计。"),
    canNewVersion
      ? el("p", { class: "hint" }, "存为题库新版本：题库里多一个版本，本问卷改用它；已经用旧版本的问卷不受影响。")
      : el("p", { class: "hint" }, "这道题不是你创建的（或已不能使用），只能只改本问卷；也可以先在题库中把它复制为自己的题。"),
  );
}

function switchPanel(q) {
  const select = el("select", { class: "small", "data-testid": "switch-version" });
  api("GET", `/api/questions/${q.source.questionId}/versions`).then((versions) => {
    select.append(...versions.map((v) => el("option", { value: v.version },
      `v${v.version}：${v.title}${v.version === q.source.version ? "（当前）" : ""}`)));
    select.value = String(q.library.latestVersion);
  }).catch((err) => toast(err.message, "error"));
  return el("div", { class: "card stack", "data-testid": "switch-panel" },
    el("div", { class: "row" }, "换成题库里的", select,
      el("button", {
        class: "btn btn-primary", "data-testid": "switch-save",
        onclick: () => call("PUT", `/api/surveys/${surveyId}/questions/${q.id}/source`,
          { version: Number(select.value) }, "已切换版本"),
      }, "切换"),
      el("button", { class: "btn", onclick: closePanel }, "取消")),
    el("p", { class: "hint" }, "保留本问卷里的必答设置和跳转规则；新版本删掉了跳转规则用到的选项时会被拒绝。"));
}

// 从题库选题：我的题目 / 共享给我的 / 题库（先选题库再选题）
function pickPanel() {
  const used = new Set(survey.questions.filter((q) => q.source).map((q) => q.source.questionId));
  const filter = el("input", { type: "text", placeholder: "按标题筛选", "data-testid": "pick-filter" });
  const tabBar = el("div", { class: "tabs" });
  const bankSlot = el("div");
  const list = el("div", { "data-testid": "pick-list" });
  let items = [];

  const add = (item, version) => call("POST", `/api/surveys/${surveyId}/questions/from-library`,
    version ? { questionId: item.id, version } : { questionId: item.id }, "已从题库加入");

  function row(item) {
    const actions = el("div", { class: "row" });
    if (used.has(item.id)) {
      actions.append(el("button", { class: "btn", disabled: true }, "已添加"));
    } else {
      actions.append(
        el("button", { class: "btn btn-primary", "data-testid": "pick-add", onclick: () => add(item) },
          `加入（v${item.latest.version}）`),
        el("button", {
          class: "btn btn-link", "data-testid": "pick-other-version",
          onclick: async () => {
            try {
              const versions = await api("GET", `/api/questions/${item.id}/versions`);
              const select = el("select", { class: "small", "data-testid": "pick-version" },
                versions.map((v) => el("option", { value: v.version }, `v${v.version}：${v.title}`)));
              clear(actions).append(select, el("button", {
                class: "btn btn-primary", "data-testid": "pick-add-version",
                onclick: () => add(item, Number(select.value)),
              }, "加入"));
            } catch (err) { toast(err.message, "error"); }
          },
        }, "其他版本"));
    }
    return el("div", { class: "list-item", "data-testid": "pick-item" },
      el("div", {},
        el("div", {}, item.latest.title),
        el("div", { class: "muted" }, `${TYPE_LABELS[item.latest.type]} · v${item.latest.version}`,
          item.isOwner ? "" : ` · ${item.owner}`)),
      actions);
  }

  function show() {
    const word = filter.value.trim();
    const shown = items.filter((i) => !word || i.latest.title.includes(word));
    clear(list).append(...(shown.length ? shown.map(row) : [el("p", { class: "muted" }, "没有可选的题。")]));
  }

  async function load(tab) {
    clear(tabBar).append(...[["mine", "我的题目"], ["shared", "共享给我的"], ["banks", "题库"]].map(([k, label]) =>
      el("button", { class: "tab" + (k === tab ? " active" : ""), "data-testid": `pick-tab-${k}`,
                     onclick: () => load(k) }, label)));
    clear(bankSlot);
    items = [];
    try {
      if (tab !== "banks") {
        items = await api("GET", `/api/questions?scope=${tab}`);
        return show();
      }
      const [mine, shared] = await Promise.all([api("GET", "/api/banks?scope=mine"), api("GET", "/api/banks?scope=shared")]);
      const banks = [...mine, ...shared];
      const select = el("select", { "data-testid": "pick-bank" },
        el("option", { value: "" }, banks.length ? "选择一个题库" : "还没有题库"),
        banks.map((b) => el("option", { value: b.id }, `${b.name}（${b.itemCount} 道题${b.isOwner ? "" : ` · ${b.owner}`}）`)));
      select.addEventListener("change", async () => {
        try {
          items = select.value ? (await api("GET", `/api/banks/${select.value}`)).items : [];
        } catch (err) {
          items = [];
          toast(err.message, "error");   // 例如题库刚被删除或取消共享
        }
        show();
      });
      bankSlot.append(select);
      show();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  filter.addEventListener("input", show);
  load("mine");
  return el("div", { class: "card stack", "data-testid": "pick-panel" },
    el("div", { class: "spread" }, el("h3", {}, "从题库选题"),
      el("button", { class: "btn", onclick: closePanel }, "关闭")),
    tabBar, bankSlot, filter, list,
    el("p", { class: "hint" }, "加入的是题目内容的副本：之后题库里有新版本，本问卷不会自动改变，可以在题目上切换版本。"));
}

function libraryLine(q) {
  if (q.source) {
    const lib = q.library || { available: false };
    return el("div", { class: "row", "data-testid": "library-line" },
      el("span", { class: "badge badge-lib", "data-testid": "library-badge" }, `题库 v${q.source.version}`),
      lib.available && !lib.isLatest
        ? el("span", { class: "badge badge-new", "data-testid": "library-newer" }, `题库已有 v${lib.latestVersion}`) : null,
      lib.available
        ? el("a", { class: "muted", href: `/static/question.html?id=${q.source.questionId}` }, "在题库中查看")
        : el("span", { class: "muted" }, "题库中已删除或不再共享给你（本问卷照常使用）"));
  }
  if (q.derivedFrom) {
    return el("div", { class: "muted", "data-testid": "library-line" }, `已脱钩：改自题库 v${q.derivedFrom.version}，不再关联`);
  }
  return null;
}

// ---------- 题目卡片 ----------

function questionCard(q, i, editable) {
  const details = el("div", { class: "muted" },
    q.options ? `选项：${q.options.map((o) => o.label).join(" / ")}` : null,
    validationText(q) ? el("div", {}, `规则：${validationText(q)}`) : null,
    (q.jumpRules || []).map((r) => el("div", { "data-testid": "rule-summary" }, `跳转：${ruleText(q, r)}`)),
  );
  const saveButton = q.source ? null : el("button", {
    class: "btn", "data-testid": "q-save-to-library",
    onclick: () => call("POST", `/api/surveys/${surveyId}/questions/${q.id}/save-to-library`, {}, "已存入题库"),
  }, "存入题库");
  const buttons = editable ? el("div", { class: "row" },
    el("button", { class: "btn", disabled: i === 0, onclick: () => move(i, -1) }, "上移"),
    el("button", { class: "btn", disabled: i === survey.questions.length - 1, onclick: () => move(i, 1) }, "下移"),
    el("button", { class: "btn", "data-testid": "q-edit", onclick: () => { openPanel = { kind: "edit", qid: q.id }; render(); } }, "编辑"),
    q.type !== "text_input" ? el("button", {
      class: "btn", "data-testid": "q-rules",
      onclick: () => { openPanel = { kind: "rules", qid: q.id }; render(); },
    }, "跳转规则") : null,
    q.source && q.library && q.library.available ? el("button", {
      class: "btn", "data-testid": "q-switch",
      onclick: () => { openPanel = { kind: "switch", qid: q.id }; render(); },
    }, "切换版本") : null,
    saveButton,
    el("button", {
      class: "btn btn-danger", "data-testid": "q-delete",
      onclick: () => { if (window.confirm(`删除${questionNo(q.id)}「${q.title}」？`)) call("DELETE", `/api/surveys/${surveyId}/questions/${q.id}`, undefined, "已删除"); },
    }, "删除"),
  ) : (saveButton ? el("div", { class: "row" }, saveButton) : null);   // 已发布：只能存入题库，内容不变

  const card = el("div", { class: "card question", "data-testid": "question-card" },
    el("h3", {}, `${i + 1}. `, q.title, q.required ? el("span", { class: "required" }, "*") : null,
      el("span", { class: "muted" }, `  ${TYPE_LABELS[q.type]}`)),
    libraryLine(q), details, buttons);
  const panel = openPanel && openPanel.qid === q.id ? openPanel.kind : null;
  if (panel === "edit") {
    // 从"选择保存方式"返回时，表单里保留刚才改过的内容
    const current = openPanel.body ? { ...q, ...openPanel.body } : q;
    return [card, questionForm(current, (body) => saveQuestion(q, body), { onCancel: closePanel })];
  }
  if (panel === "rules") return [card, ruleEditor(q, i)];
  if (panel === "switch") return [card, switchPanel(q)];
  if (panel === "libraryChoice") return [card, libraryChoicePanel(q)];
  return card;
}

function addControls() {
  if (openPanel && openPanel.kind === "add") {
    return questionForm(null, (body) => call("POST", `/api/surveys/${surveyId}/questions`, body, "已添加"),
      { onCancel: closePanel });
  }
  if (openPanel && openPanel.kind === "pick") return pickPanel();
  return el("div", { class: "row" },
    el("button", { class: "btn btn-primary", "data-testid": "add-question",
                   onclick: () => { openPanel = { kind: "add" }; render(); } }, "添加题目"),
    el("button", { class: "btn", "data-testid": "pick-from-library",
                   onclick: () => { openPanel = { kind: "pick" }; render(); } }, "从题库选题"));
}

function render() {
  const editable = survey.status === "draft";
  // 用 el() 包一层：子元素里有数组（题目列表），el() 会展开，原生 append() 不会
  clear(app).append(el("div", {},
    el("p", {}, el("a", { href: "/static/surveys.html" }, "← 返回我的问卷")),
    el("div", { class: "spread" },
      el("div", {},
        el("h1", { "data-testid": "survey-heading" }, survey.title),
        el("p", { class: "muted" }, el("span", { class: `badge badge-${survey.phase}` }, PHASE_LABELS[survey.phase]),
          `  共 ${survey.questions.length} 道题`)),
      editable ? el("button", {
        class: "btn btn-primary", "data-testid": "publish",
        onclick: () => call("POST", `/api/surveys/${surveyId}/publish`, undefined, "已发布"),
      }, "发布问卷") : null,
    ),
    editable ? null : el("div", { class: "card" },
      el("p", {}, "问卷已发布，内容不能再修改；如有问题请删除后重新创建。题目仍可以存入题库（内容不变）。"),
      el("p", {}, "分享链接：", el("span", { class: "share", "data-testid": "share-url" }, location.origin + survey.shareUrl)),
      el("a", { class: "btn btn-primary", href: `/static/stats.html?id=${survey.id}` }, "查看统计")),
    editable ? infoForm() : null,
    el("h2", {}, "题目"),
    survey.questions.length === 0 ? el("p", { class: "muted" }, "还没有题目。") : null,
    survey.questions.map((q, i) => questionCard(q, i, editable)),
    editable ? addControls() : null,
  ));
}

if (requireLogin()) {
  renderHeader();
  reload().catch((err) => clear(app).append(el("p", { class: "error" }, err.message)));
}

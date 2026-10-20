// 编辑问卷（仅草稿）：基本信息、题目增删改与上下移动、跳转规则、发布。已发布的问卷只读。

const app = document.getElementById("app");
const surveyId = queryParam("id");
let survey = null;
let openPanel = null;   // 当前展开的编辑面板：{ kind: "add" | "edit" | "rules", qid }

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

function validationText(q) {
  const v = q.validation || {};
  const parts = [];
  if (q.type === "multiple_choice") {
    if (v.minSelect != null && v.minSelect === v.maxSelect) parts.push(`必须选 ${v.minSelect} 项`);
    else {
      if (v.minSelect != null) parts.push(`至少选 ${v.minSelect} 项`);
      if (v.maxSelect != null) parts.push(`最多选 ${v.maxSelect} 项`);
    }
  }
  if (q.type === "text_input") {
    if (v.minLength != null) parts.push(`至少 ${v.minLength} 字`);
    if (v.maxLength != null) parts.push(`最多 ${v.maxLength} 字`);
  }
  if (q.type === "number_input") {
    if (v.integer) parts.push("必须为整数");
    if (v.min != null) parts.push(`最小 ${v.min}`);
    if (v.max != null) parts.push(`最大 ${v.max}`);
  }
  return parts.join("，");
}

// ---------- 题目表单（添加 / 修改共用） ----------

function numberOrNull(input) {
  return input.value === "" ? null : Number(input.value);
}

function questionForm(existing, onSubmit) {
  const typeSelect = el("select", { "data-testid": "q-type" },
    Object.entries(TYPE_LABELS).map(([k, label]) => el("option", { value: k }, label)));
  typeSelect.value = existing ? existing.type : "single_choice";
  const title = el("input", { type: "text", "data-testid": "q-title", value: existing ? existing.title : "" });
  const required = el("input", { type: "checkbox", "data-testid": "q-required", checked: existing ? existing.required : false });
  const optionsBox = el("div", { "data-testid": "q-options" });
  const rulesBox = el("div");
  const v = (existing && existing.validation) || {};
  const fields = {
    minSelect: el("input", { type: "number", min: 0, class: "small", "data-testid": "q-min-select", value: v.minSelect ?? "" }),
    maxSelect: el("input", { type: "number", min: 1, class: "small", "data-testid": "q-max-select", value: v.maxSelect ?? "" }),
    minLength: el("input", { type: "number", min: 0, class: "small", "data-testid": "q-min-length", value: v.minLength ?? "" }),
    maxLength: el("input", { type: "number", min: 1, class: "small", "data-testid": "q-max-length", value: v.maxLength ?? "" }),
    min: el("input", { type: "number", step: "any", class: "small", "data-testid": "q-min", value: v.min ?? "" }),
    max: el("input", { type: "number", step: "any", class: "small", "data-testid": "q-max", value: v.max ?? "" }),
    integer: el("input", { type: "checkbox", "data-testid": "q-integer", checked: Boolean(v.integer) }),
  };

  function addOption(label = "", id = null) {
    const input = el("input", { type: "text", value: label, "data-testid": "q-option" });
    if (id) input.dataset.optionId = id;   // 原有选项保留 id，改文字不影响跳转规则
    const row = el("div", { class: "option-row" }, input,
      el("button", { type: "button", class: "btn btn-danger", onclick: () => row.remove() }, "删除"));
    optionsBox.append(row);
  }

  function renderTypeFields() {
    const t = typeSelect.value;
    clear(rulesBox);
    if (t === "single_choice" || t === "multiple_choice") {
      rulesBox.append(
        el("h3", {}, "选项"), optionsBox,
        el("button", { type: "button", class: "btn", "data-testid": "q-add-option", onclick: () => addOption() }, "添加选项"),
      );
      if (optionsBox.children.length === 0) { addOption(); addOption(); }
    }
    if (t === "multiple_choice") {
      rulesBox.append(el("div", { class: "row" }, "至少选", fields.minSelect, "项，最多选", fields.maxSelect,
        "项", el("span", { class: "hint" }, "（两者相等即「必须选 N 项」，留空表示不限）")));
    }
    if (t === "text_input") {
      rulesBox.append(el("div", { class: "row" }, "至少", fields.minLength, "字，最多", fields.maxLength, "字"));
    }
    if (t === "number_input") {
      rulesBox.append(el("div", { class: "row" }, "最小值", fields.min, "最大值", fields.max,
        el("label", { class: "inline" }, fields.integer, "必须为整数")));
    }
  }

  (existing && existing.options || []).forEach((o) => addOption(o.label, o.id));
  typeSelect.addEventListener("change", renderTypeFields);
  renderTypeFields();

  function collect() {
    const t = typeSelect.value;
    const body = { type: t, title: title.value, required: required.checked };
    if (t === "single_choice" || t === "multiple_choice") {
      body.options = [...optionsBox.querySelectorAll("input")].map((i) =>
        i.dataset.optionId ? { id: i.dataset.optionId, label: i.value } : { label: i.value });
    }
    if (t === "multiple_choice") body.validation = { minSelect: numberOrNull(fields.minSelect), maxSelect: numberOrNull(fields.maxSelect) };
    if (t === "text_input") body.validation = { minLength: numberOrNull(fields.minLength), maxLength: numberOrNull(fields.maxLength) };
    if (t === "number_input") body.validation = { min: numberOrNull(fields.min), max: numberOrNull(fields.max), integer: fields.integer.checked };
    return body;
  }

  return el("form", {
    class: "card stack", "data-testid": "question-form",
    onsubmit: (e) => { e.preventDefault(); onSubmit(collect()); },
  },
    el("div", { class: "row" }, el("label", { class: "inline" }, "题型", typeSelect),
      el("label", { class: "inline" }, required, "必答")),
    el("label", {}, "题目", title),
    rulesBox,
    el("div", { class: "row" },
      el("button", { type: "submit", class: "btn btn-primary", "data-testid": "q-save" }, existing ? "保存修改" : "添加"),
      el("button", { type: "button", class: "btn", onclick: () => { openPanel = null; render(); } }, "取消")),
  );
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
      el("button", { type: "button", class: "btn", onclick: () => { openPanel = null; render(); } }, "取消")),
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

function questionCard(q, i, editable) {
  const details = el("div", { class: "muted" },
    q.options ? `选项：${q.options.map((o) => o.label).join(" / ")}` : null,
    validationText(q) ? el("div", {}, `规则：${validationText(q)}`) : null,
    (q.jumpRules || []).map((r) => el("div", { "data-testid": "rule-summary" }, `跳转：${ruleText(q, r)}`)),
  );
  const buttons = editable ? el("div", { class: "row" },
    el("button", { class: "btn", disabled: i === 0, onclick: () => move(i, -1) }, "上移"),
    el("button", { class: "btn", disabled: i === survey.questions.length - 1, onclick: () => move(i, 1) }, "下移"),
    el("button", { class: "btn", "data-testid": "q-edit", onclick: () => { openPanel = { kind: "edit", qid: q.id }; render(); } }, "编辑"),
    q.type !== "text_input" ? el("button", {
      class: "btn", "data-testid": "q-rules",
      onclick: () => { openPanel = { kind: "rules", qid: q.id }; render(); },
    }, "跳转规则") : null,
    el("button", {
      class: "btn btn-danger", "data-testid": "q-delete",
      onclick: () => { if (window.confirm(`删除${questionNo(q.id)}「${q.title}」？`)) call("DELETE", `/api/surveys/${surveyId}/questions/${q.id}`, undefined, "已删除"); },
    }, "删除"),
  ) : null;

  const card = el("div", { class: "card question", "data-testid": "question-card" },
    el("h3", {}, `${i + 1}. `, q.title, q.required ? el("span", { class: "required" }, "*") : null,
      el("span", { class: "muted" }, `  ${TYPE_LABELS[q.type]}`)),
    details, buttons);
  if (openPanel && openPanel.qid === q.id && openPanel.kind === "edit") {
    return [card, questionForm(q, (body) => call("PUT", `/api/surveys/${surveyId}/questions/${q.id}`, body, "已保存"))];
  }
  if (openPanel && openPanel.qid === q.id && openPanel.kind === "rules") {
    return [card, ruleEditor(q, i)];
  }
  return card;
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
      el("p", {}, "问卷已发布，内容不能再修改；如有问题请删除后重新创建。"),
      el("p", {}, "分享链接：", el("span", { class: "share", "data-testid": "share-url" }, location.origin + survey.shareUrl)),
      el("a", { class: "btn btn-primary", href: `/static/stats.html?id=${survey.id}` }, "查看统计")),
    editable ? infoForm() : null,
    el("h2", {}, "题目"),
    survey.questions.length === 0 ? el("p", { class: "muted" }, "还没有题目。") : null,
    survey.questions.map((q, i) => questionCard(q, i, editable)),
    editable ? (openPanel && openPanel.kind === "add"
      ? questionForm(null, (body) => call("POST", `/api/surveys/${surveyId}/questions`, body, "已添加"))
      : el("button", { class: "btn btn-primary", "data-testid": "add-question",
                       onclick: () => { openPanel = { kind: "add" }; render(); } }, "添加题目")) : null,
  ));
}

if (requireLogin()) {
  renderHeader();
  reload().catch((err) => clear(app).append(el("p", { class: "error" }, err.message)));
}

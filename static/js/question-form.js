// 题目表单与题目内容的文字说明：问卷编辑页（阶段一）和题库页面（阶段二）共用。

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

function numberOrNull(input) {
  return input.value === "" ? null : Number(input.value);
}

// existing：要修改的题（或题库里的某个版本内容），null 为新建。
// options：onCancel 点"取消"时调用；submitLabel 按钮文字；lockType 不允许改题型（题库建新版本时题型须相同）；
// withNote 显示"修改说明"输入框，onSubmit 的第二个参数即为说明。
function questionForm(existing, onSubmit, options = {}) {
  const typeSelect = el("select", { "data-testid": "q-type" },
    Object.entries(TYPE_LABELS).map(([k, label]) => el("option", { value: k }, label)));
  typeSelect.value = existing ? existing.type : "single_choice";
  typeSelect.disabled = Boolean(options.lockType);
  const note = el("input", { type: "text", maxlength: 200, "data-testid": "q-note", placeholder: "例如：改了措辞、加了一个选项（可选）" });
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
    onsubmit: (e) => { e.preventDefault(); onSubmit(collect(), note.value); },
  },
    el("div", { class: "row" }, el("label", { class: "inline" }, "题型", typeSelect),
      el("label", { class: "inline" }, required, "必答")),
    el("label", {}, "题目", title),
    rulesBox,
    options.withNote ? el("label", {}, "修改说明", note) : null,
    el("div", { class: "row" },
      el("button", { type: "submit", class: "btn btn-primary", "data-testid": "q-save" },
        options.submitLabel || (existing ? "保存修改" : "添加")),
      el("button", { type: "button", class: "btn", onclick: () => options.onCancel && options.onCancel() }, "取消")),
  );
}

// 题目内容的只读展示：选项、规则、是否必答
function contentView(c) {
  return el("div", { class: "content-view", "data-testid": "content-view" },
    el("div", {}, el("b", {}, c.title), c.required ? el("span", { class: "required" }, "*") : null,
      el("span", { class: "muted" }, `  ${TYPE_LABELS[c.type]}`)),
    c.options ? el("div", { class: "muted" }, `选项：${c.options.map((o) => o.label).join(" / ")}`) : null,
    validationText(c) ? el("div", { class: "muted" }, `规则：${validationText(c)}`) : null);
}

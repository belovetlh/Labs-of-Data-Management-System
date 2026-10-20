// 各页面共用：带令牌请求接口、登录状态、安全地创建页面元素、提示消息、时间格式。
//
// 安全约定：页面上所有用户输入的内容（问卷标题、选项、填写内容……）一律经 el() 以文本节点插入，
// 不使用 innerHTML，避免有人在内容里写脚本（XSS）偷走登录令牌。

const TOKEN_KEY = "survey_token";
const USER_KEY = "survey_user";

const PHASE_LABELS = { draft: "草稿", open: "收集中", expired: "已截止", closed: "已关闭" };
const TYPE_LABELS = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  text_input: "文本填空",
  number_input: "数字填空",
};

// ---------- 登录状态 ----------

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* 隐私模式等情况下无法保存，忽略 */ }
}

function getToken() {
  return storageGet(TOKEN_KEY);
}

function getUser() {
  try { return JSON.parse(storageGet(USER_KEY) || "null"); } catch { return null; }
}

function setAuth(token, user) {
  storageSet(TOKEN_KEY, token);
  storageSet(USER_KEY, JSON.stringify(user));
}

function clearAuth() {
  try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); } catch { /* 忽略 */ }
}

function goLogin() {
  const next = location.pathname + location.search;
  location.href = "/static/login.html?next=" + encodeURIComponent(next);
}

function requireLogin() {
  if (!getToken()) {
    goLogin();
    return false;
  }
  return true;
}

// ---------- 请求接口 ----------

class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function errorMessage(data, status) {
  if (!data || data.detail === undefined) return `请求失败（${status}）`;
  const d = data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e.msg).join("；");   // 接口参数格式错误
  if (d.message) return d.message;                                // 提交答卷：按题目分开的错误
  return `请求失败（${status}）`;
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  if (res.status !== 204) {
    try { data = await res.json(); } catch { data = null; }
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    clearAuth();
    goLogin();   // 令牌过期或无效：回登录页，登录后回到当前页面
  }
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res.status), data);
  return data;
}

// ---------- 创建页面元素 ----------

// el("button", { class: "btn", onclick: fn }, "文字", 子元素...)
// 字符串子元素一律作为文本节点插入（安全）；属性值用 setAttribute 设置。
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = Boolean(value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// ---------- 提示消息 ----------

function toast(message, kind = "info") {
  let box = document.getElementById("toasts");
  if (!box) {
    box = el("div", { id: "toasts" });
    document.body.append(box);
  }
  const item = el("div", { class: `toast toast-${kind}`, role: "status", "data-testid": "toast" }, message);
  box.append(item);
  setTimeout(() => item.remove(), kind === "error" ? 6000 : 3000);
}

// ---------- 时间 ----------

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "—";
}

// ISO 时间 → <input type="datetime-local"> 的值（本地时间）
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// <input type="datetime-local"> 的值 → 带时区的 ISO 时间（接口要求截止时间带时区）
function fromLocalInput(value) {
  return value ? new Date(value).toISOString() : null;
}

// ---------- 页面框架 ----------

function renderHeader() {
  const user = getUser();
  const header = el("header", { class: "topbar" },
    el("a", { href: "/static/surveys.html", class: "brand" }, "问卷系统"),
    el("nav", {},
      user ? el("span", { class: "who", "data-testid": "current-user" }, `${user.displayName}（${user.username}）`) : null,
      user ? el("button", {
        class: "btn btn-link", "data-testid": "logout",
        onclick: () => { clearAuth(); location.href = "/static/login.html"; },
      }, "退出") : null,
    ),
  );
  document.body.prepend(header);
}

function queryParam(name) {
  return new URLSearchParams(location.search).get(name);
}

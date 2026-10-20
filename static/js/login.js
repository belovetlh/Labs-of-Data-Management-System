// 登录 / 注册页。登录成功后回到 ?next= 指定的页面（只允许本站路径，防止被跳到外站）。

function nextUrl() {
  const next = queryParam("next");
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/static/surveys.html";
}

function showError(message) {
  document.getElementById("auth-error").textContent = message || "";
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.getElementById("login-form").hidden = name !== "login";
  document.getElementById("register-form").hidden = name !== "register";
  showError("");
}

async function login(username, password) {
  const data = await api("POST", "/api/auth/login", { username, password });
  setAuth(data.accessToken, data.user);
  location.href = nextUrl();
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  try {
    await login(document.getElementById("login-username").value.trim(),
                document.getElementById("login-password").value);
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  const username = document.getElementById("reg-username").value.trim();
  const password = document.getElementById("reg-password").value;
  try {
    await api("POST", "/api/auth/register", {
      username, password, displayName: document.getElementById("reg-display").value,
    });
    await login(username, password);   // 注册成功后自动登录
  } catch (err) {
    showError(err.message);
  }
});

if (getToken()) location.href = nextUrl();   // 已登录则直接进入

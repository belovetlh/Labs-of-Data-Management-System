"""浏览器自动测试（端到端）：用 Playwright 控制无界面的 Chromium，像真人一样操作页面。

运行方式与其他测试相同（pytest）。没有安装 Playwright 或 Chromium 时自动跳过，并提示安装方法：
    .venv/Scripts/python -m playwright install chromium

关键步骤的截图保存到 docs/screenshots/，可直接用于报告。
"""

import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.main import app

SHOT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
PASSWORD = "Passw0rd!"

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # 没装 playwright：整个文件跳过
    pytest.skip("未安装 playwright，跳过浏览器测试（pip install playwright）", allow_module_level=True)


# ---------- 测试环境：真实后端 + 无界面浏览器 ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    """在后台线程启动后端（连接测试库），测试结束后关闭。"""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except PlaywrightError as e:
            pytest.skip(f"未安装 Chromium，跳过浏览器测试（playwright install chromium）：{e}")
        yield b
        b.close()


@pytest.fixture()
def new_page(browser, base_url):
    """每次调用开一个全新的浏览器上下文（相当于另一个人、另一台电脑）。"""
    contexts = []

    def _new_page():
        ctx = browser.new_context(base_url=base_url, viewport={"width": 1100, "height": 900}, locale="zh-CN")
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(10_000)
        page.on("dialog", lambda d: d.accept())   # 删除等操作的确认框一律点"确定"
        return page

    yield _new_page
    for ctx in contexts:
        ctx.close()


def shot(page, name):
    """截图前移除浮动的提示消息，避免遮挡页面内容。"""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.evaluate("document.getElementById('toasts')?.remove()")
    page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=True)


# ---------- 页面操作 ----------

def register(page, username, display_name):
    page.get_by_test_id("tab-register").click()
    page.locator("#reg-username").fill(username)
    page.locator("#reg-display").fill(display_name)
    page.locator("#reg-password").fill(PASSWORD)
    page.get_by_test_id("register-submit").click()


def login(page, username):
    page.locator("#login-username").fill(username)
    page.locator("#login-password").fill(PASSWORD)
    page.get_by_test_id("login-submit").click()


def add_question(page, qtype, title, required=False, options=(), **validation):
    page.get_by_test_id("add-question").click()
    form = page.get_by_test_id("question-form")
    form.get_by_test_id("q-type").select_option(qtype)
    form.get_by_test_id("q-title").fill(title)
    if required:
        form.get_by_test_id("q-required").check()
    for i, label in enumerate(options):
        if i >= 2:
            form.get_by_test_id("q-add-option").click()
        form.get_by_test_id("q-option").nth(i).fill(label)
    for testid, value in validation.items():
        field = form.get_by_test_id(f"q-{testid.replace('_', '-')}")
        if value is True:
            field.check()
        else:
            field.fill(str(value))
    form.get_by_test_id("q-save").click()
    expect(page.get_by_test_id("question-form")).to_have_count(0)


def create_published_survey(page, title):
    """作者在页面上建一份 4 道题的问卷：第 1 题选"其他" → 跳到第 4 题。返回分享链接路径。"""
    page.goto("/static/surveys.html")
    page.locator("#new-title").fill(title)
    page.locator("#new-description").fill("关于校园生活的小调查，约 1 分钟")
    page.get_by_test_id("create-survey").click()
    expect(page).to_have_url(re.compile(r"/static/edit\.html\?id="))

    add_question(page, "single_choice", "你的年级", required=True, options=["大一", "大二", "其他"])
    add_question(page, "multiple_choice", "常去的食堂", required=True, options=["一食堂", "二食堂", "三食堂"],
                 min_select=2, max_select=3)
    add_question(page, "number_input", "每月生活费（元）", required=True, min=0, max=10000, integer=True)
    add_question(page, "text_input", "你的建议", max_length=200)
    expect(page.get_by_test_id("question-card")).to_have_count(4)

    page.get_by_test_id("q-rules").first.click()
    editor = page.get_by_test_id("rule-editor")
    editor.get_by_test_id("rule-add").click()
    editor.get_by_test_id("rule-value").select_option(label="其他")
    editor.get_by_test_id("rule-goto").select_option(label="第 4 题：你的建议")
    editor.get_by_test_id("rule-save").click()
    expect(page.get_by_test_id("rule-summary")).to_have_text("跳转：如果选了「其他」→ 跳到第 4 题")
    return page


def share_path(page) -> str:
    url = page.get_by_test_id("share-url").first.inner_text()
    return "/" + url.split("/", 3)[3]


# ---------- 测试 ----------

def test_full_flow_create_fill_and_view_stats(new_page):
    # 1. 作者注册并建问卷、设跳转、发布
    owner = new_page()
    owner.goto("/static/login.html")
    shot(owner, "01-登录注册页")
    register(owner, "author_01", "王老师")
    expect(owner).to_have_url(re.compile(r"/static/surveys\.html"))
    expect(owner.get_by_test_id("current-user")).to_have_text("王老师（author_01）")

    create_published_survey(owner, "校园生活调查")
    shot(owner, "02-编辑问卷与跳转规则")
    owner.get_by_test_id("publish").click()
    expect(owner.get_by_test_id("share-url")).to_be_visible()
    link = share_path(owner)
    assert re.fullmatch(r"/survey/[A-Za-z0-9]{8}", link)
    shot(owner, "03-已发布")

    owner.goto("/static/surveys.html")
    expect(owner.get_by_test_id("survey-phase")).to_have_text("收集中")
    shot(owner, "04-我的问卷列表")

    # 2. 填写者：未登录打开链接 → 先去登录页，注册后自动回到问卷
    resp = new_page()
    resp.goto(link)
    expect(resp).to_have_url(re.compile(r"/static/login\.html\?next=%2Fsurvey%2F"))
    register(resp, "student_01", "李同学")
    expect(resp).to_have_url(re.compile(re.escape(link) + "$"))
    expect(resp.get_by_test_id("fill-title")).to_have_text("校园生活调查")
    shot(resp, "05-填写-第1题")

    q_title = resp.get_by_test_id("fill-question-title")
    # 选"其他" → 按规则直接跳到第 4 题
    resp.get_by_label("其他").check()
    resp.get_by_test_id("next").click()
    expect(q_title).to_contain_text("你的建议")
    expect(resp.get_by_test_id("fill-progress")).to_have_text("第 4 题 / 共 4 题")

    # 回到上一题改成"大一" → 按顺序进入第 2 题
    resp.get_by_test_id("prev").click()
    expect(q_title).to_contain_text("你的年级")
    resp.get_by_label("大一").check()
    resp.get_by_test_id("next").click()
    expect(q_title).to_contain_text("常去的食堂")

    # 多选只选 1 项 → 提示错误，停在本题
    resp.get_by_label("一食堂").check()
    resp.get_by_test_id("next").click()
    expect(resp.get_by_test_id("fill-error")).to_have_text("至少选择 2 项")
    shot(resp, "06-填写-校验错误提示")
    resp.get_by_label("二食堂").check()
    resp.get_by_test_id("next").click()

    # 数字超出范围 → 提示错误；改正后继续
    expect(q_title).to_contain_text("每月生活费")
    resp.get_by_test_id("number-answer").fill("20000")
    resp.get_by_test_id("next").click()
    expect(resp.get_by_test_id("fill-error")).to_have_text("请输入 0–10000 之间的整数")
    resp.get_by_test_id("number-answer").fill("1500")
    resp.get_by_test_id("next").click()

    expect(q_title).to_contain_text("你的建议")
    resp.get_by_test_id("text-answer").fill("希望食堂晚上开得更晚一些")
    resp.get_by_test_id("next").click()
    expect(resp.get_by_test_id("fill-review")).to_contain_text("本次共经过 4 道题，作答 4 道")
    resp.get_by_test_id("submit").click()
    expect(resp.get_by_test_id("fill-done")).to_have_text("提交成功，感谢你的参与！")
    shot(resp, "07-提交成功")

    # 3. 第二位填写者选"其他"，第 2、3 题被跳过；建议不填（非必答）
    resp2 = new_page()
    resp2.goto(link)
    register(resp2, "student_02", "赵同学")
    resp2.get_by_label("其他").check()
    resp2.get_by_test_id("next").click()
    expect(resp2.get_by_test_id("fill-question-title")).to_contain_text("你的建议")
    resp2.get_by_test_id("next").click()
    expect(resp2.get_by_test_id("fill-review")).to_contain_text("本次共经过 2 道题，作答 1 道")
    resp2.get_by_test_id("submit").click()
    expect(resp2.get_by_test_id("fill-done")).to_be_visible()

    # 4. 作者看统计
    owner.goto("/static/surveys.html")
    owner.get_by_test_id("stats").click()
    expect(owner.get_by_test_id("total")).to_have_text("共收到 2 份答卷")
    stats = owner.get_by_test_id("question-stat")
    expect(stats.nth(0).get_by_test_id("option-count")).to_have_text(["1 · 50%", "0 · 0%", "1 · 50%"])
    expect(stats.nth(1).get_by_test_id("answered")).to_have_text("回答人数 1 / 总提交 2")   # 有一人被跳过
    expect(stats.nth(2).get_by_test_id("avg")).to_have_text("1500")
    expect(stats.nth(3).get_by_test_id("entry")).to_have_count(1)
    expect(stats.nth(3).get_by_test_id("entry")).to_contain_text("李同学（student_01）")
    shot(owner, "08-统计结果")


def test_multiple_submissions_disallowed_shows_message(new_page):
    owner = new_page()
    owner.goto("/static/login.html")
    register(owner, "author_02", "作者二")
    owner.locator("#new-title").fill("每人限填一次")
    owner.locator("#new-multiple").uncheck()
    owner.get_by_test_id("create-survey").click()
    add_question(owner, "text_input", "随便说点什么", required=True)
    owner.get_by_test_id("publish").click()
    link = share_path(owner)

    resp = new_page()
    resp.goto(link)
    register(resp, "student_03", "钱同学")
    resp.get_by_test_id("text-answer").fill("第一次")
    resp.get_by_test_id("next").click()
    resp.get_by_test_id("submit").click()
    expect(resp.get_by_test_id("fill-done")).to_be_visible()

    resp.goto(link)   # 再次打开：一打开就提示已填过
    expect(resp.get_by_test_id("fill-unavailable")).to_have_text("你已经填写过这份问卷")


def test_closed_and_deleted_links(new_page):
    owner = new_page()
    owner.goto("/static/login.html")
    register(owner, "author_03", "作者三")
    owner.locator("#new-title").fill("马上关闭的问卷")
    owner.get_by_test_id("create-survey").click()
    add_question(owner, "text_input", "题目")
    owner.get_by_test_id("publish").click()
    link = share_path(owner)

    owner.goto("/static/surveys.html")
    expect(owner.get_by_test_id("reopen-confirm")).to_be_hidden()   # 收集中的问卷不显示重开按钮
    owner.get_by_test_id("close").click()
    expect(owner.get_by_test_id("survey-phase")).to_have_text("已关闭")
    expect(owner.get_by_test_id("reopen-confirm")).to_be_hidden()   # 点"重新开放"之前也不显示

    resp = new_page()
    resp.goto(link)
    register(resp, "student_04", "孙同学")
    expect(resp.get_by_test_id("fill-unavailable")).to_have_text("问卷已关闭")

    owner.get_by_test_id("reopen").click()          # 重新开放
    owner.get_by_test_id("reopen-confirm").click()
    expect(owner.get_by_test_id("survey-phase")).to_have_text("收集中")
    resp.reload()
    expect(resp.get_by_test_id("fill-question")).to_be_visible()

    owner.get_by_test_id("delete").click()          # 删除（确认框自动点"确定"）
    expect(owner.get_by_test_id("survey-card")).to_have_count(0)
    resp.reload()
    expect(resp.get_by_test_id("fill-unavailable")).to_have_text("问卷已删除")


def test_user_content_is_shown_as_text_not_executed(new_page):
    """XSS 检查：标题里写脚本，页面上应显示为文字，脚本不会执行。"""
    evil = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>'
    owner = new_page()
    owner.goto("/static/login.html")
    register(owner, "author_04", "作者四")
    owner.locator("#new-title").fill(evil)
    owner.get_by_test_id("create-survey").click()
    expect(owner.get_by_test_id("survey-heading")).to_have_text(evil)
    owner.goto("/static/surveys.html")
    expect(owner.get_by_test_id("survey-title")).to_have_text(evil)
    assert owner.evaluate("window.__xss") is None


def test_login_errors_and_logout(new_page):
    page = new_page()
    page.goto("/static/login.html")
    login(page, "nobody_here")
    expect(page.get_by_test_id("auth-error")).to_have_text("账号或密码错误")

    register(page, "author_05", "作者五")
    expect(page).to_have_url(re.compile(r"/static/surveys\.html"))
    page.get_by_test_id("logout").click()
    expect(page).to_have_url(re.compile(r"/static/login\.html"))
    page.goto("/static/surveys.html")   # 退出后访问需要登录的页面 → 回到登录页
    expect(page).to_have_url(re.compile(r"/static/login\.html\?next="))

"""页面路由测试：后端能正确提供静态页面和分享链接。"""


def test_root_redirects_to_my_surveys(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/static/surveys.html"


def test_share_link_serves_fill_page(client):
    resp = client.get("/survey/k3X9pQ2a")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "/static/js/fill.js" in resp.text


def test_static_pages_exist(client):
    for page in ["login", "surveys", "edit", "fill", "stats"]:
        resp = client.get(f"/static/{page}.html")
        assert resp.status_code == 200, page
    for asset in ["css/style.css", "js/common.js", "js/fill.js"]:
        assert client.get(f"/static/{asset}").status_code == 200, asset


def test_pages_not_listed_in_api_docs(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths and "/survey/{slug}" not in paths

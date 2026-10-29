"""0.9.1: the read-only /docs route used by the System view (README, SECURITY, CHANGELOG as plain text)."""
from test_phase8 import make_app


def test_docs_route_serves_only_the_three_documents_as_plain_text(config, db):
    c, _ = make_app(config, db)
    for name, marker in (("README.md", "BTC Signal Lab"), ("SECURITY.md", "Threat model"), ("CHANGELOG.md", "Changelog")):
        r = c.get(f"/docs/{name}")
        assert r.status_code == 200 and r.mimetype == "text/plain", name
        assert marker in r.get_data(as_text=True)
        assert r.headers["X-Content-Type-Options"] == "nosniff"             # never sniffed as HTML
    for bad in ("app.py", ".env", "config.py", "readme.md", "README", "..%2Fapp.py", "%2e%2e%2fapp.py"):
        assert c.get(f"/docs/{bad}").status_code == 404, bad
    assert c.get("/docs/../app.py").status_code == 404
    assert c.post("/docs/README.md").status_code == 405


def test_app_version_is_the_ui_polish_release():
    import app
    assert app.APP_VERSION == "0.9.1 (Phases 1-9, UI polish)"

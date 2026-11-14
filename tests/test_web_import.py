from fastapi.testclient import TestClient


def test_web_import_in_tmp_and_get_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import bormochecker.web

    client = TestClient(bormochecker.web.app)
    response = client.get("/")
    assert response.status_code == 200

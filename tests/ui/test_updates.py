import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError

import pytest

from edopal.core import updates


class Response(io.BytesIO):
    headers = {"ETag": '"release-1"'}


def test_release_check_reuses_etag_and_recompares_after_upgrade(monkeypatch, tmp_path):
    requests = []

    def fetch(request, **kwargs):
        requests.append(request)
        if len(requests) > 1:
            raise HTTPError(request.full_url, 304, "Not Modified", {}, io.BytesIO())
        return Response(json.dumps({"tag_name": "v6.0.0"}).encode())

    monkeypatch.setattr(updates, "urlopen", fetch)
    initial = updates.check_for_updates(tmp_path, "6.0.0.dev0")
    assert initial.available and initial.url.endswith("/tag/v6.0.0")
    assert requests[0].full_url == "https://api.github.com/repos/cntrl-alt-lenny/EDOPal/releases/latest"
    assert not updates.check_for_updates(tmp_path, "6.0.0+linux").available
    assert len(requests) == 2
    assert updates.check_for_updates(tmp_path, "6.0.0rc1").available
    assert requests[1].get_header("If-none-match") == '"release-1"'
    assert len(requests) == 3


def test_simultaneous_launches_each_check_github(monkeypatch, tmp_path):
    calls = []

    def unavailable(request, **kwargs):
        calls.append(request)
        raise URLError("offline")

    monkeypatch.setattr(updates, "urlopen", unavailable)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: updates.check_for_updates(tmp_path), range(3)))
    assert all(result.failed and result.latest is None for result in results)
    assert len(calls) == 3


def test_bad_release_keeps_last_good_version(monkeypatch, tmp_path):
    responses = iter([b'{"tag_name":"v6.3.0"}', b'{"tag_name":"not a release"}'])
    monkeypatch.setattr(updates, "urlopen", lambda req, **kwargs: Response(next(responses)))
    assert updates.check_for_updates(tmp_path).available
    stale = updates.check_for_updates(tmp_path)
    assert stale.failed and stale.available and stale.latest == "v6.3.0"


def test_semver_precedence_and_metadata():
    sequence = ["alpha", "alpha.1", "alpha.beta", "beta", "beta.2", "beta.11", "rc.1"]
    keys = [updates._version_key("v6.0.0-" + suffix) for suffix in sequence] + [updates._version_key("6.0.0")]
    assert keys == sorted(set(keys))
    assert updates._version_key("6.0.0+build.15") == keys[-1]
    assert updates._installed_key("6.0.0.dev0") < updates._installed_key("6.0.0a4") < keys[-1]
    with pytest.raises(ValueError):
        updates._version_key("6.0.0-beta.01")


def test_legacy_daily_cache_is_revalidated(monkeypatch, tmp_path):
    with sqlite3.connect(tmp_path / "releases.sqlite3") as db:
        db.execute("CREATE TABLE latest (id INTEGER PRIMARY KEY, checked REAL, tag TEXT, etag TEXT, failed INT)")
        db.execute("INSERT INTO latest VALUES (1, 120000, 'v6.0.0', 'legacy-tag', 0)")

    def fetch(request, **kwargs):
        assert request.get_header("If-none-match") == "legacy-tag"
        return Response(b'{"tag_name":"v6.5.0"}')

    monkeypatch.setattr(updates, "urlopen", fetch)
    result = updates.check_for_updates(tmp_path, "6.0.0")
    assert result.available and result.latest == "v6.5.0"

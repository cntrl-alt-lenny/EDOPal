import io
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError

import pytest

from edopal.core import updates


class Response(io.BytesIO):
    headers = {"ETag": '"release-1"'}


def test_release_check_reuses_etag_and_recompares_after_upgrade(monkeypatch, tmp_path):
    requests = []
    now = 120_000

    def fetch(request, **kwargs):
        requests.append(request)
        if len(requests) == 2:
            raise HTTPError(request.full_url, 304, "Not Modified", {}, io.BytesIO())
        return Response(json.dumps({"tag_name": "v6.0.0"}).encode())

    monkeypatch.setattr(updates, "urlopen", fetch)
    monkeypatch.setattr(updates.time, "time", lambda: now)
    initial = updates.check_for_updates(tmp_path, "6.0.0.dev0")
    assert initial.available and initial.url.endswith("/tag/v6.0.0")
    assert requests[0].full_url == "https://api.github.com/repos/cntrl-alt-lenny/EDOPal/releases/latest"
    now += 86_399
    assert not updates.check_for_updates(tmp_path, "6.0.0+linux").available
    assert len(requests) == 1
    now += 1
    assert updates.check_for_updates(tmp_path, "6.0.0rc1").available
    assert requests[1].get_header("If-none-match") == '"release-1"'
    assert len(requests) == 2


def test_simultaneous_requests_share_daily_failure(monkeypatch, tmp_path):
    calls = []

    def unavailable(request, **kwargs):
        calls.append(request)
        raise URLError("offline")

    monkeypatch.setattr(updates, "urlopen", unavailable)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: updates.check_for_updates(tmp_path), range(3)))
    assert all(result.failed and result.latest is None for result in results)
    assert len(calls) == 1


def test_bad_release_keeps_last_good_version(monkeypatch, tmp_path):
    responses = iter([b'{"tag_name":"v6.3.0"}', b'{"tag_name":"not a release"}'])
    monkeypatch.setattr(updates, "urlopen", lambda req, **kwargs: Response(next(responses)))
    now = 123_456
    monkeypatch.setattr(updates.time, "time", lambda: now)
    assert updates.check_for_updates(tmp_path).available
    now += 86_400
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

import pytest

from ou25_pipeline.webapp import render_client


class _FakeResponse:
    """Only the two-happy-path tests below exercise this — error handling
    is httpx's own `raise_for_status`, not logic this module adds, so no
    need to simulate a failing response here."""

    def __init__(self, json_body: dict):
        self._json = json_body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json


def test_trigger_job_posts_the_exact_start_command(mocker):
    post = mocker.patch.object(render_client.httpx, "post", return_value=_FakeResponse({"id": "job_1", "status": "running"}))

    result = render_client.trigger_job("key123", "srv_1", "uv run ou25-pipeline backfill --competition comp_1 --season 24/25")

    assert result == {"id": "job_1", "status": "running"}
    call_kwargs = post.call_args.kwargs
    assert call_kwargs["json"] == {"startCommand": "uv run ou25-pipeline backfill --competition comp_1 --season 24/25"}
    assert call_kwargs["headers"]["Authorization"] == "Bearer key123"
    assert "srv_1" in post.call_args.args[0]


def test_trigger_job_raises_when_api_key_missing():
    with pytest.raises(render_client.RenderNotConfiguredError):
        render_client.trigger_job(None, "srv_1", "uv run ...")


def test_trigger_job_raises_when_service_id_missing():
    with pytest.raises(render_client.RenderNotConfiguredError):
        render_client.trigger_job("key123", None, "uv run ...")


def test_get_job_status_returns_parsed_response(mocker):
    mocker.patch.object(
        render_client.httpx, "get",
        return_value=_FakeResponse({"status": "succeeded", "startedAt": "t1", "finishedAt": "t2"}),
    )
    result = render_client.get_job_status("key123", "job_1")
    assert result["status"] == "succeeded"


def test_get_job_status_raises_when_api_key_missing():
    with pytest.raises(render_client.RenderNotConfiguredError):
        render_client.get_job_status(None, "job_1")

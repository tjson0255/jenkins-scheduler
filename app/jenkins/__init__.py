from __future__ import annotations

from app.config import Settings
from app.jenkins.base import JenkinsClientProtocol


def make_client(settings: Settings) -> JenkinsClientProtocol:
    if settings.jenkins_mock:
        from app.jenkins.mock import MockJenkinsClient

        return MockJenkinsClient(
            fixtures_dir=settings.jenkins_mock_fixtures,
            state_file=settings.data_path / "mock_state.json",
            min_seconds=settings.jenkins_mock_min_seconds,
            max_seconds=settings.jenkins_mock_max_seconds,
        )
    from app.jenkins.client import JenkinsClient

    return JenkinsClient(
        base_url=settings.jenkins_url,
        user=settings.jenkins_user,
        token=settings.resolve_jenkins_token(),
        ca_bundle=settings.jenkins_ca_bundle,
    )

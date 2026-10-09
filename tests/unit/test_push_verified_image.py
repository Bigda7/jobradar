"""Publication retries must reuse the exact verified candidate."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import push_verified_image as publisher

TAG = "ghcr.io/bigda7/jobradar:v1.2.22"
DIGEST = "sha256:" + "a" * 64
DOCKER = str(Path("/usr/bin/docker").resolve())


def _mock_commands(monkeypatch, outcomes, identities=None):
    inspected = iter(identities or [DIGEST] * 3)
    pushes = iter(outcomes)

    def command(args, **kwargs):
        if args[2] == "inspect":
            assert kwargs["check"] is True
            return subprocess.CompletedProcess(args, 0, stdout=next(inspected) + "\n")
        assert args == [DOCKER, "image", "push", TAG]
        assert kwargs["timeout"] == 300
        outcome = next(pushes)
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(args, outcome)

    run = Mock(side_effect=command)
    sleep = Mock()
    monkeypatch.setattr(publisher.subprocess, "run", run)
    monkeypatch.setattr(publisher.time, "sleep", sleep)
    monkeypatch.setattr(publisher.shutil, "which", lambda executable: DOCKER)
    return run, sleep


def test_success_requires_no_retry(monkeypatch) -> None:
    run, sleep = _mock_commands(monkeypatch, [0])
    publisher.push_verified_image(TAG, DIGEST)
    assert run.call_count == 2
    sleep.assert_not_called()


def test_network_failure_and_timeout_retry_same_image(monkeypatch) -> None:
    run, sleep = _mock_commands(monkeypatch, [1, subprocess.TimeoutExpired("docker", 300), 0])
    publisher.push_verified_image(TAG, DIGEST)
    assert run.call_count == 6
    assert [call.args[0] for call in sleep.call_args_list] == [10, 30]


def test_failures_exhaust_exactly_three_attempts(monkeypatch) -> None:
    run, sleep = _mock_commands(monkeypatch, [1, 1, 1])
    with pytest.raises(RuntimeError, match="three bounded"):
        publisher.push_verified_image(TAG, DIGEST)
    assert run.call_count == 6
    assert sleep.call_count == 2


@pytest.mark.parametrize("identities", [["sha256:" + "b" * 64], [DIGEST, "sha256:" + "b" * 64]])
def test_retagged_candidate_stops_before_next_push(monkeypatch, identities) -> None:
    run, _ = _mock_commands(monkeypatch, [1], identities)
    with pytest.raises(ValueError, match="differs"):
        publisher.push_verified_image(TAG, DIGEST)
    assert sum(call.args[0][2] == "push" for call in run.call_args_list) == len(identities) - 1


@pytest.mark.parametrize(
    ("tag", "digest"),
    [("other.example/image:v1.2.22", DIGEST), (TAG + "; echo unsafe", DIGEST), (TAG, "latest")],
)
def test_invalid_identity_never_runs_docker(monkeypatch, tag, digest) -> None:
    run = Mock()
    monkeypatch.setattr(publisher.subprocess, "run", run)
    with pytest.raises(ValueError):
        publisher.push_verified_image(tag, digest)
    run.assert_not_called()


def test_missing_docker_stops_before_publication(monkeypatch) -> None:
    run = Mock()
    monkeypatch.setattr(publisher.subprocess, "run", run)
    monkeypatch.setattr(publisher.shutil, "which", lambda executable: None)
    with pytest.raises(RuntimeError, match="not available"):
        publisher.push_verified_image(TAG, DIGEST)
    run.assert_not_called()

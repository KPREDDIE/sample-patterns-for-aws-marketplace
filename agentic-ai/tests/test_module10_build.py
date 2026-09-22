"""Image builds must succeed before a deployable digest is reported."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module10.deployment import build_image


ROOT = Path(__file__).resolve().parents[1]


class ImageNotFound(Exception):
    pass


def ecr_client():
    client = Mock()
    client.exceptions = SimpleNamespace(ImageNotFoundException=ImageNotFound)
    return client


def test_waits_for_success(monkeypatch):
    client = Mock()
    client.batch_get_builds.side_effect = [
        {"builds": [{"buildStatus": "IN_PROGRESS"}]},
        {"builds": [{"buildStatus": "SUCCEEDED"}]},
    ]
    monkeypatch.setattr(build_image.time, "sleep", Mock())
    build_image.wait_for_build(client, "test-build")
    assert client.batch_get_builds.call_count == 2


@pytest.mark.parametrize("status", ["FAILED", "FAULT", "STOPPED", "TIMED_OUT"])
def test_failed_build_cannot_produce_deployable_report(status):
    client = Mock()
    client.batch_get_builds.return_value = {"builds": [{"buildStatus": status}]}
    with pytest.raises(RuntimeError, match=status):
        build_image.wait_for_build(client, "test-build")


def test_wait_has_a_deadline(monkeypatch):
    monkeypatch.setattr(build_image.time, "monotonic", Mock(side_effect=[0, 2]))
    with pytest.raises(TimeoutError, match="test-build"):
        build_image.wait_for_build(Mock(), "test-build", timeout=1)


@pytest.mark.parametrize("existing", [True, False])
def test_build_reports_digest_and_reuses_immutable_image(tmp_path, monkeypatch, existing):
    outputs = tmp_path / "outputs.json"
    outputs.write_text(json.dumps({
        "repositoryName": "test-repo", "artifactBucket": "test-artifacts", "buildProject": "test-project",
    }))
    report = tmp_path / "image.json"
    ecr = ecr_client()
    image = {"imageDetails": [{"imageDigest": "sha256:" + "a" * 64}]}
    ecr.describe_images.side_effect = [image] if existing else [ImageNotFound(), image]
    builder = Mock()
    builder.start_build.return_value = {"build": {"id": "test-build"}}
    builder.batch_get_builds.return_value = {"builds": [{"buildStatus": "SUCCEEDED"}]}
    s3 = Mock()
    monkeypatch.setattr(build_image.boto3, "client",
                        lambda name: {"s3": s3, "codebuild": builder, "ecr": ecr}[name])
    monkeypatch.setattr(build_image, "source_archive", lambda root: b"test-source")
    monkeypatch.setattr(build_image.sys, "argv", [
        "build_image", "--outputs", str(outputs), "--wait", "--report", str(report),
    ])
    build_image.main()
    assert json.loads(report.read_text())["image_digest"] == "sha256:" + "a" * 64
    assert builder.start_build.call_count == (0 if existing else 1)
    assert s3.put_object.call_count == (0 if existing else 1)


def test_registry_permission_errors_are_not_treated_as_missing_images():
    ecr = ecr_client()
    ecr.describe_images.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        build_image.image_digest(ecr, "test-repo", "tag")


def test_portkey_build_refreshes_and_patches_base_image():
    dockerfile = (ROOT / "module10/gateway/Dockerfile").read_text()
    infrastructure = (ROOT / "module10/gateway/pulumi/__main__.py").read_text()
    assert dockerfile.count("apt-get upgrade -y") == 2
    assert "docker build --pull --platform linux/arm64" in infrastructure

"""Upload an allowlisted build context and start an ARM CodeBuild image build."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import zipfile

import boto3

from .paths import deployment_path


def image_digest(ecr, repository, tag):
    """Return an existing immutable image, or None before its first build."""
    try:
        images = ecr.describe_images(
            repositoryName=repository, imageIds=[{"imageTag": tag}])["imageDetails"]
    except ecr.exceptions.ImageNotFoundException:
        return None
    return images[0]["imageDigest"] if images else None


def wait_for_build(codebuild, build_id, *, timeout=2400):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        builds = codebuild.batch_get_builds(ids=[build_id])["builds"]
        if not builds:
            raise RuntimeError(f"CodeBuild could not find build {build_id}")
        status = builds[0]["buildStatus"]
        if status == "SUCCEEDED":
            return
        if status != "IN_PROGRESS":
            raise RuntimeError(f"CodeBuild {build_id} ended with {status}; inspect its build logs")
        time.sleep(10)
    raise TimeoutError(f"Still waiting for CodeBuild {build_id}; inspect its status before retrying")


def source_archive(root):
    root = root.resolve()
    # Only application sources and named, non-secret configuration belong in an
    # image. A suffix-only filter also uploads credentials.json or stack.yaml
    # accidentally left beside source files.
    source_types = {
        "module10": {".py"},
        "module10/deployment": {".py"},
        "module10/deployment/lambda": {".mjs"},
        "module10/gateway": {".py", ".mjs", ".js"},
    }
    data_files = {
        "module10/flags.json", "module10/collector.yaml",
        "module10/fixtures/deployment.json", "module10/fixtures/incident.json",
        "module10/requirements.txt", "module10/requirements-review.txt",
        "module10/requirements-telemetry.txt",
        "module10/deployment/requirements-runtime.txt", "module10/deployment/Dockerfile",
        "module10/gateway/Dockerfile", "module10/gateway/routing.json",
        "module10/gateway/package.json", "module10/gateway/package-lock.json",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def write(name, data):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

        # Do not traverse local state, virtual environments, or cached packages.
        candidates = {root / name for name in data_files}
        for directory, suffixes in source_types.items():
            parent = root / directory
            if parent.is_dir() and not parent.is_symlink():
                candidates.update(path for path in parent.iterdir() if path.suffix in suffixes)
        for path in sorted(candidates):
            rel = path.relative_to(root)
            if not path.is_file() or any(parent.is_symlink() for parent in (path, *path.parents)):
                continue
            if any(p.startswith(".") for p in rel.parts):
                continue
            if (rel.as_posix() not in data_files
                    and path.suffix not in source_types.get(rel.parent.as_posix(), set())):
                continue
            write(str(rel), path.read_bytes())
        write(".dockerignore", (root / ".dockerignore").read_bytes())
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, required=True, help="Pulumi stack output JSON")
    parser.add_argument("--wait", action="store_true", help="Wait for success and resolve the immutable ECR image digest")
    parser.add_argument("--report", type=deployment_path, help="Write build JSON in module10/.local/ or outside Git (requires --wait)")
    args = parser.parse_args()
    if args.report and not args.wait:
        parser.error("--report requires --wait")
    outputs = json.loads(args.outputs.read_text())
    source = source_archive(Path(__file__).resolve().parents[2])
    digest = hashlib.sha256(source).hexdigest()
    ecr = boto3.client("ecr")
    existing = image_digest(ecr, outputs["repositoryName"], digest)
    report = {"image_tag": digest}
    if existing:
        report.update(image_digest=existing, reused=True)
    else:
        key = f"build/{digest}.zip"
        boto3.client("s3").put_object(Bucket=outputs["artifactBucket"], Key=key, Body=source)
        codebuild = boto3.client("codebuild")
        result = codebuild.start_build(projectName=outputs["buildProject"],
            sourceLocationOverride=f'{outputs["artifactBucket"]}/{key}',
            environmentVariablesOverride=[{"name": "IMAGE_TAG", "value": digest, "type": "PLAINTEXT"}])
        report["build_id"] = result["build"]["id"]
        if args.wait:
            print(f"Waiting for CodeBuild {report['build_id']}", file=sys.stderr, flush=True)
            wait_for_build(codebuild, report["build_id"])
            resolved = image_digest(ecr, outputs["repositoryName"], digest)
            if not resolved:
                raise RuntimeError("Build succeeded but ECR has no matching image; inspect the build logs")
            report["image_digest"] = resolved
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()

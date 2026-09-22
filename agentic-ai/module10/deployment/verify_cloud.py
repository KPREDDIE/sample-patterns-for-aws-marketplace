"""Exercise deployed admission and streaming controls with one real investigation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from module10.cloud_client import CloudClient
from module10.deployment.paths import deployment_path


def verify(client):
    summary = json.loads((Path(__file__).resolve().parents[1] / "fixtures/deployment.json").read_text())["change_summary"]
    checks = {}

    def check(name, response, status):
        checks[name] = response.status_code
        if response.status_code != status:
            raise AssertionError(f"{name}: expected {status}, received {response.status_code}: {response.body}")

    check("missing_token", client.call("/invoke", team=None, body={"change_summary": summary}), 401)
    check("denied_identity", client.call("/probe", team="blocked"), 403)
    token = client.token("platform")
    # Alter the signed payload, not an insignificant base64 padding bit.
    pieces = token.split(".")
    import base64
    payload = json.loads(base64.urlsafe_b64decode(pieces[1] + "=="))
    payload["sub"] = "forged-user"
    pieces[1] = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    check("tampered_token", client.call("/probe", team=None,
        headers={"Authorization": "Bearer " + ".".join(pieces)}), 401)
    check("spoofed_team", client.invoke("payments", summary, headers={"X-Demo-Team": "platform"}), 400)
    check("unauthorized_control", client.call("/control", team="payments", body={
        "session": "access-check", "section": 0, "backend": "bedrock",
    }), 403)
    session = "access-" + uuid4().hex[:12]
    client.configure(session, 0, "bedrock")
    request_id = uuid4().hex
    accepted = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.invoke, "platform", summary, request_id,
            on_event=lambda event: accepted.set() if event["event"] == "accepted" else None)
        if not accepted.wait(timeout=90):
            raise AssertionError("The hosted investigation did not emit an acceptance event in time")
        competing = client.invoke("platform", summary, uuid4().hex)
        check("team_concurrency", competing, 429)
        assert competing.body.get("source") == "team-concurrency", competing.body
        response = pending.result(timeout=215)
    check("investigation", response, 200)
    assert response.body["status"] == "completed"
    assert response.body["team"] == "platform"
    assert response.body["model_calls"]
    assert response.body["routing"]["gateway"] == "portkey-ecs"
    assert all(call.get("gateway") == "portkey-ecs" and call.get("attempts")
               for call in response.body["model_calls"])
    checks["model_gateway"] = "portkey-ecs"
    assert client.last_events[0]["event"] == "accepted"
    assert client.last_events[-1]["event"] == "result"
    checks["time_to_first_event_seconds"] = round(client.last_events[0]["seconds"], 3)
    checks["total_seconds"] = round(client.last_events[-1]["seconds"], 3)
    checks["trace_id"] = response.body["trace_id"]
    check("owner_result", client.call(f"/requests/{request_id}"), 200)
    check("other_team_result", client.call(f"/requests/{request_id}", team="payments"), 404)
    check("same_request_replay", client.invoke("platform", summary, request_id), 202)
    check("conflicting_replay", client.invoke("platform", summary + " different", request_id), 409)
    try:
        boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=client.outputs["runtimeArn"], qualifier=client.outputs["endpointName"],
            runtimeSessionId=str(uuid4()), payload=b"{}", contentType="application/json")
        raise AssertionError("Direct Runtime invocation unexpectedly succeeded")
    except ClientError as error:
        status = error.response["ResponseMetadata"]["HTTPStatusCode"]
        assert status == 403, error.response["Error"]["Code"]
        checks["direct_runtime_bypass"] = status
    # This dedicated method makes no model calls. Gateway quotas are best effort.
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(lambda _: client.call("/probe").status_code, range(30)))
    assert 429 in statuses, f"No gateway throttle observed: {statuses}"
    assert all(s in {200, 429} for s in statuses), statuses
    checks["gateway_probe_throttles"] = statuses.count(429)
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report:
        try:
            args.report = deployment_path(args.report)
        except ValueError as error:
            parser.error(str(error))
    report = verify(CloudClient(args.outputs, args.credentials_file))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

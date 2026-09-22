"""Remote-only Portkey adapter for hosted agents; discovery is trusted through IAM."""
import ipaddress
import json
import os
import re
import ssl
import time
from urllib.parse import urlsplit
import uuid

from .agent_runner import AgentRunError
from .control_planes import IntegrationUnavailable
from .reviewer import BedrockReviewer


def discovery_context(value):
    if value.get("status") != "ready":
        raise IntegrationUnavailable("Portkey environment is stopped; start it before invoking agents.")
    url = value.get("url", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.port != 8443 or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise IntegrationUnavailable("Invalid Portkey discovery endpoint")
    ipaddress.IPv4Address(parsed.hostname)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=value["certificate"])
    return url, context


class RemotePortkeyReviewer(BedrockReviewer):
    def __init__(self, client, *, s3, bucket, identity):
        super().__init__(client, "devops-companion")
        self.s3, self.bucket, self.identity = s3, bucket, identity
        self.gateway = self  # Existing cloud invocation's drill/evidence boundary.
        self.drill_armed = False
        self.records = []

    @classmethod
    def from_env(cls, identity):
        import boto3
        from botocore.config import Config
        import httpx2
        from openai import OpenAI

        names = ("MODULE10_PORTKEY_DISCOVERY", "MODULE10_PORTKEY_SECRET", "MODULE10_PORTKEY_EVIDENCE_BUCKET")
        if any(not os.getenv(name) for name in names):
            raise IntegrationUnavailable("Hosted Portkey configuration is required")
        ssm = boto3.client("ssm")
        session = json.loads(ssm.get_parameter(Name=os.environ[names[0]] + "/session")["Parameter"]["Value"])
        if session.get("status") != "active" or session.get("expires_at", 0) <= time.time():
            raise IntegrationUnavailable("Portkey environment is stopped; start it before invoking agents.")
        value = json.loads(ssm.get_parameter(Name=os.environ[names[0]])["Parameter"]["Value"])
        url, context = discovery_context(value)
        token = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ[names[1]])["SecretString"]
        client = OpenAI(api_key=token, base_url=url + "/v1/openai/v1", max_retries=0,
            timeout=60, http_client=httpx2.Client(verify=context, trust_env=False))
        return cls(client, s3=boto3.client("s3", config=Config(connect_timeout=2, read_timeout=3,
            retries={"total_max_attempts": 1})), bucket=os.environ[names[2]], identity=identity)

    def set_drill(self, armed):
        self.drill_armed = bool(armed)

    def console_evidence(self):
        return list(self.records)

    def create_response(self, *, agent, evidence_ready, record, deadline, **params):
        from openai import APIStatusError
        from .observability import correlation

        role = {"DevOps Companion": "devops-companion", "Recovery Planner": "recovery-planner"}[agent]
        remaining = min(60.0, deadline - time.monotonic())
        if remaining <= 5:
            raise AgentRunError("Insufficient time remains for a model request")
        call_id = record.get("call_id") or uuid.uuid4().hex
        record.update(call_id=call_id, requested_model=role, evidence_ready=evidence_ready,
                      attempts=[], fallback_used=False, gateway="portkey-ecs")
        drill = self.drill_armed and role == "recovery-planner" and evidence_ready
        if drill:
            self.drill_armed = False
        params.update(model=role, timeout=remaining, extra_headers={
            "x-portkey-trace-id": call_id,
            "x-portkey-metadata": json.dumps({**correlation(), **self.identity,
                "agent_role": role, "evidence_ready": str(evidence_ready).lower()}),
            "x-module10-timeout-ms": str(int(min(25, (remaining - 5) / 2) * 1000)),
            "x-module10-drill": str(bool(drill)).lower(),
        })

        def capture(headers):
            attempts = json.loads(headers.get("x-module10-attempts", "[]"))
            record.update(attempts=attempts, gateway_target=headers.get("x-portkey-last-used-option-index"),
                fallback_used=len(attempts) > 1 and 200 <= attempts[-1]["status_code"] < 300)
            key = headers.get("x-module10-evidence-key")
            expected = "evidence/{team}/{principal}/{request_id}/".format(**self.identity) + call_id + ".json"
            if key:
                if key != expected or not re.fullmatch(r"[A-Za-z0-9_/-]+\.json", key):
                    raise AgentRunError("Invalid gateway evidence identity")
                item = self.s3.get_object(Bucket=self.bucket, Key=key)
                self.records.append(json.loads(item["Body"].read()))

        try:
            raw = self.client.responses.with_raw_response.create(**params)
        except APIStatusError as exc:
            capture(exc.response.headers)
            raise
        capture(raw.headers)
        if not record["attempts"] or not record["gateway_target"] or not raw.headers.get("x-module10-evidence-key"):
            raise AgentRunError("Portkey returned no verifiable routing evidence")
        return raw.parse()

"""Copy only telemetry shipping settings into the stack's Secrets Manager secret."""
import argparse
import json
import os
from pathlib import Path

import boto3


def main():
    from module10.observability import load_environment
    load_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, required=True)
    args = parser.parse_args()
    outputs = json.loads(args.outputs.read_text())
    secret = outputs.get("telemetrySecretArn")
    if not secret:
        parser.error("Deploy with enableCloudIntegrations first, then refresh the outputs.")
    keys = ("LOGZIO_REGION", "LOGZIO_LOGS_TOKEN", "LOGZIO_TRACES_TOKEN")
    settings = {key: os.getenv(key, "") for key in keys}
    missing = [key for key, value in settings.items() if not value]
    if missing:
        parser.error("Missing shipping settings: " + ", ".join(missing))
    boto3.client("secretsmanager").put_secret_value(
        SecretId=secret, SecretString=json.dumps(settings))
    print("Telemetry shipping settings stored in Secrets Manager. No values written to Pulumi configuration.")


if __name__ == "__main__":
    main()

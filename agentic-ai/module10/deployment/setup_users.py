"""Create isolated demo identities without sending invitation messages."""
import argparse
import json
import os
from pathlib import Path
import secrets

import boto3
from module10.deployment.paths import deployment_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        destination = deployment_path(args.credentials_file)
    except ValueError as error:
        parser.error(str(error))
    outputs = json.loads(args.outputs.read_text())
    credentials = json.loads(destination.read_text()) if destination.exists() else {}
    if credentials and credentials.get("user_pool_id") != outputs["userPoolId"]:
        parser.error("The existing credential file belongs to another user pool")
    credentials.update(user_pool_id=outputs["userPoolId"], client_id=outputs["userPoolClientId"])
    credentials.setdefault("users", {})
    cognito = boto3.client("cognito-idp")
    database = boto3.resource("dynamodb").Table(outputs["stateTable"])
    for team, allowed, operator in [("platform", True, True), ("payments", True, False), ("blocked", False, False)]:
        user = credentials["users"].setdefault(team, {
            "username": f"{team}-operator", "password": "Aa1!" + secrets.token_urlsafe(24),
        })
        try:
            cognito.admin_create_user(UserPoolId=outputs["userPoolId"], Username=user["username"], MessageAction="SUPPRESS")
        except cognito.exceptions.UsernameExistsException:
            pass
        cognito.admin_set_user_password(UserPoolId=outputs["userPoolId"], Username=user["username"],
            Password=user["password"], Permanent=True)
        attributes = cognito.admin_get_user(UserPoolId=outputs["userPoolId"], Username=user["username"])["UserAttributes"]
        user["principal"] = next(a["Value"] for a in attributes if a["Name"] == "sub")
        database.put_item(Item={"pk": f"USER#{user['principal']}", "sk": "PROFILE",
            "team": team, "canInvoke": allowed, "operator": operator})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
        os.fchmod(file.fileno(), 0o600)
        json.dump(credentials, file, indent=2)
    print("Created platform, payments, and denied-access demo identities. Credentials saved to the deployment directory.")


if __name__ == "__main__":
    main()

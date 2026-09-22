"""Consumer API and identity infrastructure; enabled after Runtime verification."""
import hashlib
import json
from pathlib import Path

import pulumi
import pulumi_aws as aws


def deploy_edge(agent, endpoint, bucket, *, region, account, partition, tags, role, policy, config):
    source = Path(__file__).resolve().parents[1] / "lambda"
    table = aws.dynamodb.Table("request-state", billing_mode="PAY_PER_REQUEST",
        hash_key="pk", range_key="sk", attributes=[{"name": "pk", "type": "S"}, {"name": "sk", "type": "S"}],
        ttl={"attribute_name": "expiresAt", "enabled": True}, tags=tags)
    api = aws.apigateway.RestApi("consumer-api", api_key_source="AUTHORIZER",
        endpoint_configuration={"types": "REGIONAL"}, tags=tags)
    site = None
    if config.get_bool("enableUI"):
        from ui import create_site
        site = create_site(tags, api.id.apply(lambda a: f"https://{a}.execute-api.{region}.amazonaws.com"))
    api_keys = {team: aws.apigateway.ApiKey(f"{team}-usage-key", enabled=True)
                for team in ("platform", "payments")}
    functions = {}

    def function(name, code, statements, environment=None, timeout=10):
        execution = role(name + "-execution", "lambda.amazonaws.com")
        logs = aws.cloudwatch.LogGroup(name + "-logs", name=f"/aws/lambda/module10-{pulumi.get_stack()}-{name}",
            retention_in_days=7, tags=tags)
        permissions = aws.iam.RolePolicy(name + "-permissions", role=execution.id, policy=policy([
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": logs.arn.apply(lambda a: a + ":*")},
            {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"], "Resource": "*"},
            *statements,
        ]))
        fn = aws.lambda_.Function(name, name=logs.name.apply(lambda n: n.split("/")[-1]),
            role=execution.arn, runtime="nodejs24.x", handler="index.handler", architectures=["arm64"],
            code=pulumi.AssetArchive({"index.mjs": pulumi.FileAsset(str(source / code)),
                                     "jwt.mjs": pulumi.FileAsset(str(source / "jwt.mjs")),
                                     **({"quota.mjs": pulumi.FileAsset(str(source / "quota.mjs"))} if code == "authorizer.mjs" else {}),
                                     **({"planning.mjs": pulumi.FileAsset(str(source / "planning.mjs"))} if code == "control.mjs" else {}),
                                     **({"resilience.mjs": pulumi.FileAsset(str(source / "resilience.mjs"))} if code in {"control.mjs", "adapter.mjs"} else {}),
                                     "cors.mjs": pulumi.FileAsset(str(source / "cors.mjs"))}),
            timeout=timeout, memory_size=256, environment={"variables": environment or {}}, tags=tags,
            tracing_config={"mode": "Active"},
            opts=pulumi.ResourceOptions(depends_on=[permissions]))
        functions[name] = fn
        return fn, execution

    token, _ = function("token-scope", "token.mjs", [])
    pool = aws.cognito.UserPool("demo-users", user_pool_tier="ESSENTIALS",
        admin_create_user_config={"allow_admin_create_user_only": True},
        password_policy={"minimum_length": 16, "require_lowercase": True, "require_uppercase": True,
                         "require_numbers": True, "require_symbols": True},
        lambda_config={"pre_token_generation_config": {"lambda_arn": token.arn, "lambda_version": "V2_0"}},
        tags=tags)
    aws.lambda_.Permission("cognito-token-trigger", action="lambda:InvokeFunction", function=token.name,
        principal="cognito-idp.amazonaws.com", source_arn=pool.arn, source_account=account)
    client = aws.cognito.UserPoolClient("demo-client", user_pool_id=pool.id, generate_secret=False,
        explicit_auth_flows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        prevent_user_existence_errors="ENABLED", enable_token_revocation=True,
        access_token_validity=30, id_token_validity=30,
        token_validity_units={"access_token": "minutes", "id_token": "minutes"})
    browser = None
    if site:
        from ui import browser_auth
        browser, auth_url = browser_auth(pool, site[1], region)
    authorizer_function, _ = function("authorizer", "authorizer.mjs", [
        {"Effect": "Allow", "Action": ["dynamodb:GetItem"], "Resource": table.arn},
    ], {
        "TABLE_NAME": table.name,
        "ISSUER": pool.id.apply(lambda p: f"https://cognito-idp.{region}.amazonaws.com/{p}"),
        "CLIENT_ID": client.id,
        **({"CLIENT_IDS": pulumi.Output.json_dumps([client.id, browser.id])} if browser else {}),
        "USAGE_KEYS": pulumi.Output.json_dumps({team: key.value for team, key in api_keys.items()}),
    })
    authorizer = aws.apigateway.Authorizer("caller-identity", rest_api=api.id, type="TOKEN",
        authorizer_uri=authorizer_function.invoke_arn, authorizer_result_ttl_in_seconds=0,
        identity_source="method.request.header.Authorization")
    aws.lambda_.Permission("gateway-authorizer", action="lambda:InvokeFunction", function=authorizer_function.name,
        principal="apigateway.amazonaws.com", source_account=account,
        source_arn=api.execution_arn.apply(lambda a: a + "/authorizers/*"))

    data_permissions = [
        {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
         "Resource": table.arn},
        {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": bucket.arn.apply(lambda a: a + "/results/*")},
    ]
    env = {"TABLE_NAME": table.name, "ARTIFACT_BUCKET": bucket.bucket,
           "RUNTIME_ARN": agent.agent_runtime_arn, "RUNTIME_ENDPOINT": endpoint.name,
           **({"UI_ORIGIN": site[1]} if site else {})}
    adapter, adapter_role = function("stream-adapter", "adapter.mjs", [
        *data_permissions,
        {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
         "Resource": [agent.agent_runtime_arn, endpoint.agent_runtime_endpoint_arn]},
    ], env, timeout=210)
    control, _ = function("demo-control", "control.mjs", [
        *data_permissions,
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": bucket.arn.apply(lambda a: a + "/config/flags.json")},
        {"Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": bucket.arn.apply(lambda a: a + "/manifests/*")},
        {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": bucket.arn,
         "Condition": {"StringLike": {"s3:prefix": "manifests/*"}}},
    ], env)
    # Both IAM resources are protected. Even an account role with broad invoke
    # permissions cannot bypass the consumer API's adapter.
    for name, arn in [("runtime", agent.agent_runtime_arn), ("endpoint", endpoint.agent_runtime_endpoint_arn)]:
        aws.bedrock.AgentcoreResourcePolicy(f"{name}-admission-policy", resource_arn=arn, policy=policy([
            {"Effect": "Allow", "Principal": {"AWS": adapter_role.arn},
             "Action": "bedrock-agentcore:InvokeAgentRuntime", "Resource": arn},
            {"Effect": "Deny", "Principal": "*", "Action": "bedrock-agentcore:InvokeAgentRuntime",
             "Resource": arn, "Condition": {"ArnNotEquals": {"aws:PrincipalArn": adapter_role.arn}}},
        ]))
    validator = aws.apigateway.RequestValidator("json-body", rest_api=api.id, validate_request_body=True)
    model = aws.apigateway.Model("invocation-schema", rest_api=api.id, name="InvocationRequest",
        content_type="application/json", schema=json.dumps({
            "$schema": "http://json-schema.org/draft-04/schema#", "type": "object",
            "additionalProperties": False, "required": ["change_summary"],
            "properties": {"change_summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                           "request_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}},
        }))
    integrations = []
    cors_parameters = {
        "Access-Control-Allow-Origin": site[1].apply(lambda u: f"'{u}'"),
        "Access-Control-Allow-Headers": "'Authorization,Content-Type'",
        "Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
    } if site else {}

    def preflight(name, resource):
        options = aws.apigateway.Method(name + "-options", rest_api=api.id, resource_id=resource.id,
            http_method="OPTIONS", authorization="NONE", api_key_required=False)
        integration = aws.apigateway.Integration(name + "-options", rest_api=api.id,
            resource_id=resource.id, http_method=options.http_method, type="MOCK",
            request_templates={"application/json": '{"statusCode":200}'})
        response = aws.apigateway.MethodResponse(name + "-options", rest_api=api.id,
            resource_id=resource.id, http_method=options.http_method, status_code="200",
            response_parameters={f"method.response.header.{k}": True for k in cors_parameters})
        reply = aws.apigateway.IntegrationResponse(name + "-options", rest_api=api.id,
            resource_id=resource.id, http_method=options.http_method, status_code=response.status_code,
            response_parameters={f"method.response.header.{k}": v for k, v in cors_parameters.items()},
            opts=pulumi.ResourceOptions(depends_on=[integration]))
        integrations.extend([options, integration, response, reply])

    if site:
        for response_type in ("DEFAULT_4XX", "DEFAULT_5XX"):
            integrations.append(aws.apigateway.Response("ui-" + response_type.lower(),
                rest_api_id=api.id, response_type=response_type,
                response_templates={"application/json": '{"message":$context.error.messageString,"type":"$context.error.responseType"}'},
                response_parameters={f"gatewayresponse.header.{k}": v for k, v in cors_parameters.items()}))

    def route(name, path, method, fn=None, streaming=False, parent=None, validate=False, resource=None, metered=True):
        new_resource = resource is None
        if new_resource:
            resource = aws.apigateway.Resource(name, rest_api=api.id, parent_id=parent or api.root_resource_id, path_part=path)
        if site and new_resource:
            preflight(name, resource)
        api_method = aws.apigateway.Method(name, rest_api=api.id, resource_id=resource.id, http_method=method,
            authorization="CUSTOM", authorizer_id=authorizer.id, api_key_required=metered,
            request_validator_id=validator.id if validate else None,
            request_models={"application/json": model.name} if validate else None)
        if fn:
            uri = fn.arn.apply(lambda a: f"arn:{partition}:apigateway:{region}:lambda:path/2021-11-15/functions/{a}/response-streaming-invocations") if streaming else fn.invoke_arn
            integration = aws.apigateway.Integration(name, rest_api=api.id, resource_id=resource.id,
                http_method=api_method.http_method, integration_http_method="POST", type="AWS_PROXY", uri=uri,
                response_transfer_mode="STREAM" if streaming else "BUFFERED",
                timeout_milliseconds=210000 if streaming else 29000)
            aws.lambda_.Permission(name + "-invoke", action="lambda:InvokeFunction", function=fn.name,
                principal="apigateway.amazonaws.com", source_account=account,
                source_arn=api.execution_arn.apply(lambda a: a + "/*"))
        else:
            integration = aws.apigateway.Integration(name, rest_api=api.id, resource_id=resource.id,
                http_method=api_method.http_method, type="MOCK", request_templates={"application/json": '{"statusCode":200}'})
            response = aws.apigateway.MethodResponse(name, rest_api=api.id, resource_id=resource.id,
                http_method=api_method.http_method, status_code="200", response_models={"application/json": "Empty"},
                response_parameters={f"method.response.header.{k}": True for k in cors_parameters})
            integrations.append(aws.apigateway.IntegrationResponse(name, rest_api=api.id, resource_id=resource.id,
                http_method=api_method.http_method, status_code=response.status_code,
                response_parameters={f"method.response.header.{k}": v for k, v in cors_parameters.items()},
                response_templates={"application/json": '{"status":"ok","purpose":"admission-probe","model_calls":0}'},
                opts=pulumi.ResourceOptions(depends_on=[integration])))
        integrations.extend([resource, api_method, integration])
        return resource

    route("invoke", "invoke", "POST", adapter, streaming=True, validate=True)
    route("control", "control", "POST", control)
    route("probe", "probe", "GET")
    if site:
        route("identity", "me", "GET", control)
        # Polling remains authenticated and stage-throttled without consuming
        # the team's daily investigation/demo quota.
        planning_resource = route("planning-status", "planning", "GET", control, metered=False)
        route("planning-update", "planning", "POST", control, resource=planning_resource)
        resilience_resource = route("resilience-status", "resilience", "GET", control, metered=False)
        route("resilience-update", "resilience", "POST", control, resource=resilience_resource)
    requests = aws.apigateway.Resource("requests", rest_api=api.id, parent_id=api.root_resource_id, path_part="requests")
    route("result", "{id}", "GET", control, parent=requests.id)
    captures = aws.apigateway.Resource("captures", rest_api=api.id, parent_id=api.root_resource_id, path_part="captures")
    route("capture", "{id}", "GET", control, parent=captures.id)
    logs = aws.cloudwatch.LogGroup("api-access", retention_in_days=7, tags=tags)
    logging_dependencies = []
    if config.get_bool("manageApiGatewayLogging"):
        log_role = role("gateway-logs", "apigateway.amazonaws.com")
        log_policy = aws.iam.RolePolicy("gateway-logs-policy", role=log_role.id, policy=policy([
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:DescribeLogGroups",
             "logs:DescribeLogStreams", "logs:PutLogEvents", "logs:GetLogEvents", "logs:FilterLogEvents"], "Resource": "*"},
        ]))
        logging_dependencies.append(aws.apigateway.Account("gateway-logging-account", cloudwatch_role_arn=log_role.arn,
            opts=pulumi.ResourceOptions(depends_on=[log_policy], retain_on_delete=True)))
    deployment = aws.apigateway.Deployment("api-deployment", rest_api=api.id,
        triggers={"definition": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "ui": site[1] if site else "disabled"},
        opts=pulumi.ResourceOptions(depends_on=integrations))
    stage = aws.apigateway.Stage("api-stage", rest_api=api.id, deployment=deployment.id, stage_name="demo", xray_tracing_enabled=True,
        access_log_settings={"destination_arn": logs.arn, "format": json.dumps({
            "request_id": "$context.requestId", "status": "$context.status",
            "team": "$context.authorizer.team", "method": "$context.httpMethod",
            "path": "$context.resourcePath", "latency_ms": "$context.responseLatency",
        })}, tags=tags, opts=pulumi.ResourceOptions(depends_on=logging_dependencies))
    aws.apigateway.MethodSettings("api-throttle", rest_api=api.id, stage_name=stage.stage_name, method_path="*/*",
        settings={"metrics_enabled": True, "throttling_rate_limit": 10, "throttling_burst_limit": 20})
    for team, key in api_keys.items():
        plan = aws.apigateway.UsagePlan(team + "-usage", api_stages=[{
            "api_id": api.id, "stage": stage.stage_name,
            "throttles": [{"path": "/probe/GET", "rate_limit": 1, "burst_limit": 2}],
        }], throttle_settings={"rate_limit": 5, "burst_limit": 10},
            quota_settings={"limit": 500, "period": "DAY"}, tags=tags)
        aws.apigateway.UsagePlanKey(team + "-usage-binding", key_id=key.id, key_type="API_KEY", usage_plan_id=plan.id)
    pulumi.export("apiUrl", stage.invoke_url)
    pulumi.export("userPoolId", pool.id)
    pulumi.export("userPoolClientId", client.id)
    pulumi.export("stateTable", table.name)
    pulumi.export("apiAccessLogGroup", logs.name)
    pulumi.export("adapterFunction", adapter.name)
    pulumi.export("authorizerFunction", authorizer_function.name)
    if site:
        from ui import publish_site
        publish_site(site[0], site[1], browser, auth_url, stage.invoke_url)

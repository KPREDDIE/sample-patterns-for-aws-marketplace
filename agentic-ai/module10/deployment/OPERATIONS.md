# Operations

Run commands from `agentic-ai/` with your AWS credentials set.

## Costs

Approximate USD rates for US East (N. Virginia), excluding free-tier credits:

| Item | Estimate |
|---|---|
| Active Portkey task and public IPv4 | **$0.025/hour**, about **$0.05 for two hours** |
| Three secrets, 2 GB ECR images, and 1 GB S3 data | **$1.42/month**, plus logs and other retained data |
| ARM CodeBuild | **$0.03–$0.07** per 10–20 minute image build |
| AgentCore | Budget **$0.13/active vCPU-hour + $0.017/GB-hour** |
| Models, API, Lambda, telemetry, and data transfer | Additional usage charges |

Confirm current rates with [Fargate pricing](https://aws.amazon.com/fargate/pricing/),
[IPv4 pricing](https://aws.amazon.com/vpc/pricing/),
[AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/), and the
[AWS Pricing Calculator](https://calculator.aws/). Close browser tabs and stop
Portkey after use.

## Restore the deployment environment

```bash
export MODULE10_DEPLOYMENT_DIR="$PWD/module10/.local/workshop"
export PULUMI_PYTHON_CMD="$MODULE10_DEPLOYMENT_DIR/pulumi-venv/bin/python"
export PULUMI_BACKEND_URL="file://$MODULE10_DEPLOYMENT_DIR/state"
pulumi login "$PULUMI_BACKEND_URL"
```

Use the passphrase chosen at initialization.

## Updates

1. Stop Portkey using the command below.
2. For application/gateway source changes, repeat the image build, `imageDigest`,
   and `up` commands in the [deployment guide](README.md): gateway first, then
   application. Skip stack initialization and the bootstrap `up` commands.
3. Refresh both outputs files and rerun `setup_users` with the existing
   credentials file.
4. Repeat **Start and verify** from the deployment guide.

If `runtimeVersion` pins an old version, remove that override before updating:

```bash
pulumi -C module10/deployment/pulumi config rm runtimeVersion \
  --stack workshop --config-file "$MODULE10_DEPLOYMENT_DIR/stack.yaml"
```

For UI/Lambda-only changes:

```bash
pulumi -C module10/deployment/pulumi up --stack workshop \
  --config-file "$MODULE10_DEPLOYMENT_DIR/stack.yaml"
```

## Stop

```bash
.venv/bin/python -m module10.gateway.lifecycle stop \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json"
.venv/bin/python -m module10.gateway.lifecycle status \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json"
```

Confirm desired, running, and pending counts are zero.

## Permanent cleanup

Stop Portkey and restore the deployment environment above. Save any evidence you
want to keep before deleting data.

1. Read `artifactBucket` and `repositoryName` from each stack's outputs file.
2. In S3, **Empty** both artifact buckets, including all versions and delete
   markers in the versioned agent bucket.
3. In ECR, delete all images in both repositories. Leave the empty buckets and
   repositories for Pulumi to delete.
4. Destroy the application stack, then the gateway stack:

```bash
pulumi -C module10/deployment/pulumi preview --destroy --stack workshop \
  --config-file "$MODULE10_DEPLOYMENT_DIR/stack.yaml"
pulumi -C module10/deployment/pulumi destroy --stack workshop \
  --config-file "$MODULE10_DEPLOYMENT_DIR/stack.yaml"
pulumi -C module10/gateway/pulumi preview --destroy --stack workshop \
  --config-file "$MODULE10_DEPLOYMENT_DIR/portkey-stack.yaml"
pulumi -C module10/gateway/pulumi destroy --stack workshop \
  --config-file "$MODULE10_DEPLOYMENT_DIR/portkey-stack.yaml"
```

Remove any manually uploaded UI-bucket objects if deletion reports it nonempty.
Check separately for Runtime-created CloudWatch log groups. Secrets Manager
deletion has a seven-day recovery window. If the stack created the regional API
Gateway logging role, check whether other APIs need it before deleting the role.
Keep Pulumi state until both destroys succeed, then remove local state and
credentials when no longer needed.

## Local checks

After [local setup](../README.md#local):

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/test_module10_*.py tests/test_portkey_lifecycle.py -q
node --test tests/test_module10_*.mjs
node --import ./module10/.cache/portkey/node_modules/tsx/dist/loader.mjs \
  --test tests/test_portkey_hosted.mjs
```

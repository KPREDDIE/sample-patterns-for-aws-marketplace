"""Independent, normally stopped Portkey environment. No NAT or load balancer."""
import json
import pulumi
import pulumi_aws as aws
import pulumi_random as random

config = pulumi.Config()
prefix = f"module10-portkey-{pulumi.get_stack()}"
region = aws.get_region().region
account = aws.get_caller_identity().account_id
partition = aws.get_partition().partition
tags = {"Project": "module10-portkey", "Stack": pulumi.get_stack()}


def policy(statements):
    return pulumi.Output.json_dumps({"Version": "2012-10-17", "Statement": statements})


def role(name, principal, conditions=None):
    statement = {"Effect": "Allow", "Principal": {"Service": principal}, "Action": "sts:AssumeRole"}
    if conditions:
        statement["Condition"] = conditions
    return aws.iam.Role(name, assume_role_policy=json.dumps({
        "Version": "2012-10-17", "Statement": [statement]}), tags=tags)


vpc = aws.ec2.Vpc("gateway-vpc", cidr_block="10.84.0.0/24", enable_dns_support=True,
    enable_dns_hostnames=True, tags=tags)
igw = aws.ec2.InternetGateway("gateway-internet", vpc_id=vpc.id, tags=tags)
subnet = aws.ec2.Subnet("gateway-public", vpc_id=vpc.id, cidr_block="10.84.0.0/25",
    availability_zone=aws.get_availability_zones(state="available").names[0], tags=tags)
routes = aws.ec2.RouteTable("gateway-routes", vpc_id=vpc.id,
    routes=[{"cidr_block": "0.0.0.0/0", "gateway_id": igw.id}], tags=tags)
association = aws.ec2.RouteTableAssociation("gateway-public-route", subnet_id=subnet.id, route_table_id=routes.id)
security = aws.ec2.SecurityGroup("gateway-https", vpc_id=vpc.id,
    ingress=[{"protocol": "tcp", "from_port": 8443, "to_port": 8443, "cidr_blocks": ["0.0.0.0/0"]}],
    egress=[{"protocol": "-1", "from_port": 0, "to_port": 0, "cidr_blocks": ["0.0.0.0/0"]}], tags=tags)
cluster = aws.ecs.Cluster("gateway-cluster", name=prefix, tags=tags)
logs = aws.cloudwatch.LogGroup("gateway-logs", retention_in_days=7, tags=tags)
bucket = aws.s3.BucketV2("gateway-artifacts", tags=tags)
aws.s3.BucketPublicAccessBlock("gateway-artifacts-private", bucket=bucket.id,
    block_public_acls=True, block_public_policy=True, ignore_public_acls=True, restrict_public_buckets=True)
aws.s3.BucketServerSideEncryptionConfigurationV2("gateway-artifacts-encrypted", bucket=bucket.id,
    rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}])
aws.s3.BucketLifecycleConfigurationV2("gateway-evidence-retention", bucket=bucket.id,
    rules=[{"id": "evidence", "status": "Enabled", "filter": {"prefix": "evidence/"},
            "expiration": {"days": 7}}])
repository = aws.ecr.Repository("gateway-image", image_tag_mutability="IMMUTABLE",
    image_scanning_configuration={"scan_on_push": True}, tags=tags)
discovery = aws.ssm.Parameter("gateway-discovery", name=f"/{prefix}/discovery", type="String",
    value=json.dumps({"status": "stopped"}), tags=tags,
    opts=pulumi.ResourceOptions(ignore_changes=["value"]))
session = aws.ssm.Parameter("gateway-session", name=f"/{prefix}/discovery/session", type="String",
    value=json.dumps({"status": "stopped", "expires_at": 0}), tags=tags,
    opts=pulumi.ResourceOptions(ignore_changes=["value"]))
secrets = {}
versions = []
for purpose in ("service", "console"):
    secret = aws.secretsmanager.Secret("gateway-" + purpose, recovery_window_in_days=7, tags=tags)
    value = random.RandomPassword("gateway-" + purpose + "-value", length=48, special=False)
    versions.append(aws.secretsmanager.SecretVersion("gateway-" + purpose + "-version",
        secret_id=secret.id, secret_string=value.result))
    secrets[purpose] = secret

builder_role = role("gateway-builder", "codebuild.amazonaws.com")
build_logs = aws.cloudwatch.LogGroup("gateway-build-logs", retention_in_days=7, tags=tags)
build_policy = aws.iam.RolePolicy("gateway-build-permissions", role=builder_role.id, policy=policy([
    {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": bucket.arn.apply(lambda a: a + "/build/*")},
    {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"], "Resource": repository.arn},
    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
     "Resource": build_logs.arn.apply(lambda a: a + ":*")},
]))
builder = aws.codebuild.Project("gateway-build", service_role=builder_role.arn, build_timeout=30,
    artifacts={"type": "NO_ARTIFACTS"},
    environment={"compute_type": "BUILD_GENERAL1_SMALL", "type": "ARM_CONTAINER",
        "image": "aws/codebuild/amazonlinux-aarch64-standard:3.0", "privileged_mode": True,
        "environment_variables": [
            {"name": "ECR_REPOSITORY", "value": repository.repository_url},
            {"name": "ECR_REGISTRY", "value": repository.repository_url.apply(lambda u: u.split("/")[0])}]},
    source={"type": "S3", "location": bucket.bucket.apply(lambda b: b + "/build/source.zip"),
        "buildspec": """version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR_REGISTRY"
  build:
    commands:
      - docker build --pull --platform linux/arm64 -f module10/gateway/Dockerfile -t "$ECR_REPOSITORY:$IMAGE_TAG" .
  post_build:
    commands:
      - docker push "$ECR_REPOSITORY:$IMAGE_TAG"
"""},
    logs_config={"cloudwatch_logs": {"group_name": build_logs.name}}, tags=tags,
    opts=pulumi.ResourceOptions(depends_on=[build_policy]))

pulumi.export("artifactBucket", bucket.bucket)
pulumi.export("evidenceBucket", bucket.bucket)
pulumi.export("repositoryUrl", repository.repository_url)
pulumi.export("repositoryName", repository.name)
pulumi.export("buildProject", builder.name)
pulumi.export("discoveryParameter", discovery.name)
pulumi.export("discoveryParameterArn", discovery.arn)
pulumi.export("sessionParameterArn", session.arn)
pulumi.export("serviceSecretArn", secrets["service"].arn)
pulumi.export("consoleSecretArn", secrets["console"].arn)
pulumi.export("clusterArn", cluster.arn)
pulumi.export("logGroup", logs.name)

digest = config.get("imageDigest")
if digest:
    import re
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("imageDigest must be an immutable sha256 digest")
    execution = role("gateway-task-execution", "ecs-tasks.amazonaws.com")
    execution_policy = aws.iam.RolePolicy("gateway-task-execution-permissions", role=execution.id, policy=policy([
        {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], "Resource": repository.arn},
        {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": logs.arn.apply(lambda a: a + ":*")},
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": [s.arn for s in secrets.values()]},
    ]))
    task_role = role("gateway-task", "ecs-tasks.amazonaws.com", {
        "StringEquals": {"aws:SourceAccount": account},
        "ArnLike": {"aws:SourceArn": f"arn:{partition}:ecs:{region}:{account}:*"}})
    task_policy = aws.iam.RolePolicy("gateway-task-permissions", role=task_role.id, policy=policy([
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": [
            f"arn:{partition}:bedrock:*::foundation-model/openai.gpt-5.6-luna*",
            f"arn:{partition}:bedrock:*::foundation-model/openai.gpt-5.6-terra*",
            f"arn:{partition}:bedrock:{region}:{account}:inference-profile/us.openai.gpt-5.6-luna",
            f"arn:{partition}:bedrock:{region}:{account}:inference-profile/us.openai.gpt-5.6-terra",
            f"arn:{partition}:bedrock:{region}:{account}:project/default"]},
        {"Effect": "Allow", "Action": ["ecs:DescribeTasks"],
         "Resource": f"arn:{partition}:ecs:{region}:{account}:task/{prefix}/*"},
        {"Effect": "Allow", "Action": ["ec2:DescribeNetworkInterfaces"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["ssm:PutParameter"], "Resource": discovery.arn},
        {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": bucket.arn.apply(lambda a: a + "/evidence/*")},
    ]))
    definition = aws.ecs.TaskDefinition("gateway-task-definition", family=prefix,
        cpu="512", memory="1024", network_mode="awsvpc", requires_compatibilities=["FARGATE"],
        runtime_platform={"cpu_architecture": "ARM64", "operating_system_family": "LINUX"},
        task_role_arn=task_role.arn, execution_role_arn=execution.arn,
        container_definitions=pulumi.Output.json_dumps([{
            "name": "portkey", "image": repository.repository_url.apply(lambda u: f"{u}@{digest}"),
            "essential": True, "stopTimeout": 70,
            "portMappings": [{"containerPort": 8443, "protocol": "tcp"}],
            "environment": [{"name": "AWS_REGION", "value": region},
                {"name": "PORTKEY_DISCOVERY_PARAMETER", "value": discovery.name},
                {"name": "PORTKEY_EVIDENCE_BUCKET", "value": bucket.bucket}],
            "secrets": [{"name": "PORTKEY_SERVICE_TOKEN", "valueFrom": secrets["service"].arn},
                {"name": "PORTKEY_CONSOLE_TOKEN", "valueFrom": secrets["console"].arn}],
            "healthCheck": {"command": ["CMD-SHELL",
                "python -c \"import socket; socket.create_connection(('127.0.0.1',8443),2).close()\""],
                "interval": 30, "timeout": 5, "retries": 3, "startPeriod": 60},
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": logs.name, "awslogs-region": region, "awslogs-stream-prefix": "portkey"}},
        }]), tags=tags, opts=pulumi.ResourceOptions(depends_on=[execution_policy, task_policy, *versions]))
    service = aws.ecs.Service("gateway-service", name=prefix, cluster=cluster.arn,
        task_definition=definition.arn, desired_count=0, launch_type="FARGATE", platform_version="1.4.0",
        deployment_minimum_healthy_percent=0, deployment_maximum_percent=100,
        deployment_circuit_breaker={"enable": True, "rollback": True},
        network_configuration={"subnets": [subnet.id], "security_groups": [security.id], "assign_public_ip": True},
        tags=tags, opts=pulumi.ResourceOptions(ignore_changes=["desired_count"], depends_on=[association]))
    schedule_group = aws.scheduler.ScheduleGroup("gateway-schedules", name=prefix, tags=tags)
    scheduler_role = role("gateway-expiry", "scheduler.amazonaws.com", {
        "StringEquals": {"aws:SourceAccount": account},
        "ArnEquals": {"aws:SourceArn": f"arn:{partition}:scheduler:{region}:{account}:schedule-group/{prefix}"}})
    aws.iam.RolePolicy("gateway-expiry-permissions", role=scheduler_role.id, policy=policy([
        {"Effect": "Allow", "Action": ["ecs:UpdateService"], "Resource": service.id}]))
    pulumi.export("serviceName", service.name)
    pulumi.export("serviceArn", service.id)
    pulumi.export("scheduleGroup", schedule_group.name)
    pulumi.export("schedulerRoleArn", scheduler_role.arn)
    pulumi.export("imageDigest", digest)

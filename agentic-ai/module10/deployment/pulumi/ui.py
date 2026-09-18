"""Private static origin, browser authentication, and public UI configuration."""
import json
from pathlib import Path

import pulumi
import pulumi_aws as aws


def create_site(tags, api_origin):
    bucket = aws.s3.Bucket("ui-assets", tags=tags)
    private = aws.s3.BucketPublicAccessBlock("ui-private", bucket=bucket.id,
        block_public_acls=True, block_public_policy=True,
        ignore_public_acls=True, restrict_public_buckets=True)
    aws.s3.BucketServerSideEncryptionConfiguration("ui-encryption", bucket=bucket.id,
        rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}])
    access = aws.cloudfront.OriginAccessControl("ui-origin-access",
        origin_access_control_origin_type="s3", signing_behavior="always", signing_protocol="sigv4")
    headers = aws.cloudfront.ResponseHeadersPolicy("ui-security-headers",
        security_headers_config={
            "content_type_options": {"override": True},
            "frame_options": {"frame_option": "DENY", "override": True},
            "referrer_policy": {"referrer_policy": "no-referrer", "override": True},
            "strict_transport_security": {"access_control_max_age_sec": 31536000, "override": True},
            "content_security_policy": {"override": True, "content_security_policy": api_origin.apply(lambda api:
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                f"connect-src 'self' {api} https://*.amazoncognito.com; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")},
        })
    distribution = aws.cloudfront.Distribution("companion-ui", enabled=True,
        default_root_object="index.html", price_class="PriceClass_100",
        origins=[{"origin_id": "ui", "domain_name": bucket.bucket_regional_domain_name,
                  "origin_access_control_id": access.id}],
        default_cache_behavior={"target_origin_id": "ui", "viewer_protocol_policy": "redirect-to-https",
            "allowed_methods": ["GET", "HEAD", "OPTIONS"], "cached_methods": ["GET", "HEAD"],
            "compress": True, "min_ttl": 0, "default_ttl": 0, "max_ttl": 0,
            "forwarded_values": {"query_string": False, "cookies": {"forward": "none"}},
            "response_headers_policy_id": headers.id},
        restrictions={"geo_restriction": {"restriction_type": "none"}},
        viewer_certificate={"cloudfront_default_certificate": True}, tags=tags)
    aws.s3.BucketPolicy("ui-origin-policy", bucket=bucket.id,
        policy=pulumi.Output.json_dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject", "Resource": bucket.arn.apply(lambda a: a + "/*"),
            "Condition": {"StringEquals": {"AWS:SourceArn": distribution.arn}},
        }]}), opts=pulumi.ResourceOptions(depends_on=[private]))
    origin = distribution.domain_name.apply(lambda name: f"https://{name}")
    pulumi.export("uiUrl", origin)
    pulumi.export("uiBucket", bucket.bucket)
    return bucket, origin


def browser_auth(pool, origin, region):
    server = aws.cognito.ResourceServer("companion-scope", user_pool_id=pool.id,
        identifier="module10", name="DevOps Companion",
        scopes=[{"scope_name": "invoke", "scope_description": "Invoke the DevOps Companion"}])
    client = aws.cognito.UserPoolClient("browser-client", user_pool_id=pool.id, generate_secret=False,
        allowed_oauth_flows_user_pool_client=True, allowed_oauth_flows=["code"],
        allowed_oauth_scopes=["openid", "module10/invoke"], supported_identity_providers=["COGNITO"],
        callback_urls=[origin.apply(lambda u: u + "/")], logout_urls=[origin.apply(lambda u: u + "/")],
        explicit_auth_flows=["ALLOW_REFRESH_TOKEN_AUTH"], prevent_user_existence_errors="ENABLED",
        enable_token_revocation=True, access_token_validity=30, id_token_validity=30,
        token_validity_units={"access_token": "minutes", "id_token": "minutes"},
        opts=pulumi.ResourceOptions(depends_on=[server]))
    domain = aws.cognito.UserPoolDomain("browser-login", user_pool_id=pool.id,
        domain=pool.id.apply(lambda p: "companion-" + p.lower().replace("_", "-")), managed_login_version=2)
    aws.cognito.ManagedLoginBranding("browser-login-style", user_pool_id=pool.id,
        client_id=client.id, use_cognito_provided_values=True,
        opts=pulumi.ResourceOptions(depends_on=[domain]))
    auth_url = domain.domain.apply(lambda d: f"https://{d}.auth.{region}.amazoncognito.com")
    pulumi.export("browserClientId", client.id)
    pulumi.export("browserAuthUrl", auth_url)
    return client, auth_url


def publish_site(bucket, origin, client, auth_url, api_url):
    root = Path(__file__).resolve().parents[2] / "ui"
    types = {".html": "text/html", ".css": "text/css", ".mjs": "text/javascript", ".svg": "image/svg+xml"}
    for path in sorted(root.iterdir()):
        if path.suffix in types:
            aws.s3.BucketObject("ui-" + path.name.replace(".", "-"), bucket=bucket.id,
                key=path.name, source=pulumi.FileAsset(str(path)),
                content_type=types[path.suffix], cache_control="no-store")
    example = json.loads((root.parent / "fixtures/deployment.json").read_text())["change_summary"]
    aws.s3.BucketObject("ui-config", bucket=bucket.id, key="config.json",
        content_type="application/json", cache_control="no-store",
        content=pulumi.Output.json_dumps({"apiUrl": api_url, "clientId": client.id,
            "authUrl": auth_url, "redirectUri": origin.apply(lambda u: u + "/"),
            "example": example, "logzioUrl": "https://app.logz.io"}))

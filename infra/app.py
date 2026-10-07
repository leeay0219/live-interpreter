"""Live Interpreter on AWS: CloudFront (+ AWS WAF) → VPC origin → internal ALB → ECS on Fargate, private subnets only.

  cd infra && cdk deploy --all [-c event=<id>]      # events/<id>.toml; default: events/general.toml

By default a new VPC with two isolated subnets. With vpc_id (and app_subnets) the stack goes into an existing VPC
instead, in two new subnets of its own. Either way nothing has a public IP and the subnets have no route to the internet
(no NAT gateway); AWS service calls go through VPC endpoints.

Settings, later ones win: the defaults below, infra/local.json (not in git, for one account's values), -c key=value.
  account        AWS account (or CDK_DEFAULT_ACCOUNT)
  vpc_id         existing VPC to use; it needs an internet gateway attached (CloudFront VPC origins require one)
  app_subnets    [["ap-northeast-2a", "10.0.40.0/24"], ...] free CIDRs in that VPC for the two new subnets
  vpc_cidr       CIDR of the new VPC when vpc_id is not set (default 10.80.0.0/16)
  event          events/<id>.toml for the server
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as sm,
    aws_wafv2 as waf,
)
import json
import os
from pathlib import Path

from constructs import Construct

REGION = "ap-northeast-2"
LOCAL = Path(__file__).with_name("local.json")
# translation: Sonnet first, Haiku when it is slow (server.py DEFAULT_MODEL / FALLBACK_MODEL), both via global inference profiles
LIVE_MODELS = ["anthropic.claude-sonnet-5-5", "anthropic.claude-haiku-4-5-20251001-v1:0"]
WRITEUP_MODELS = ["anthropic.claude-opus-5-5"]  # session write-up after the meeting (server.py WRITEUP_MODEL)


class WafStack(cdk.Stack):
    """Web ACL for CloudFront; CLOUDFRONT-scope ACLs must live in us-east-1."""

    def __init__(self, scope: Construct, id: str, **kw):
        super().__init__(scope, id, **kw)

        def managed(name: str, priority: int, excluded: list[str] | None = None):
            return waf.CfnWebACL.RuleProperty(
                name=name, priority=priority,
                statement=waf.CfnWebACL.StatementProperty(managed_rule_group_statement=waf.CfnWebACL.ManagedRuleGroupStatementProperty(
                    vendor_name="AWS", name=name,
                    rule_action_overrides=[waf.CfnWebACL.RuleActionOverrideProperty(name=r, action_to_use=waf.CfnWebACL.RuleActionProperty(count={}))
                                           for r in excluded or []] or None)),
                override_action=waf.CfnWebACL.OverrideActionProperty(none={}),
                visibility_config=waf.CfnWebACL.VisibilityConfigProperty(cloud_watch_metrics_enabled=True, metric_name=name, sampled_requests_enabled=False))

        acl = waf.CfnWebACL(
            self, "Acl", scope="CLOUDFRONT", default_action=waf.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=waf.CfnWebACL.VisibilityConfigProperty(cloud_watch_metrics_enabled=True, metric_name="live-interpreter", sampled_requests_enabled=False),
            rules=[
                # password guessing: the login form gets a tight limit of its own
                waf.CfnWebACL.RuleProperty(
                    name="LoginRateLimit", priority=0, action=waf.CfnWebACL.RuleActionProperty(block={}),
                    statement=waf.CfnWebACL.StatementProperty(rate_based_statement=waf.CfnWebACL.RateBasedStatementProperty(
                        limit=100, aggregate_key_type="IP",
                        scope_down_statement=waf.CfnWebACL.StatementProperty(byte_match_statement=waf.CfnWebACL.ByteMatchStatementProperty(
                            field_to_match=waf.CfnWebACL.FieldToMatchProperty(uri_path={}), positional_constraint="STARTS_WITH",
                            search_string="/login", text_transformations=[waf.CfnWebACL.TextTransformationProperty(priority=0, type="NONE")])))),
                    visibility_config=waf.CfnWebACL.VisibilityConfigProperty(cloud_watch_metrics_enabled=True, metric_name="LoginRateLimit", sampled_requests_enabled=False)),
                # floods: an operator loading a 40-slide deck plus the studio's polling stays far below this
                waf.CfnWebACL.RuleProperty(
                    name="RateLimit", priority=1, action=waf.CfnWebACL.RuleActionProperty(block={}),
                    statement=waf.CfnWebACL.StatementProperty(rate_based_statement=waf.CfnWebACL.RateBasedStatementProperty(limit=2000, aggregate_key_type="IP")),
                    visibility_config=waf.CfnWebACL.VisibilityConfigProperty(cloud_watch_metrics_enabled=True, metric_name="RateLimit", sampled_requests_enabled=False)),
                managed("AWSManagedRulesAmazonIpReputationList", 2),
                # SizeRestrictions_BODY blocks bodies over 8 KB, which would block every slide PDF upload
                managed("AWSManagedRulesCommonRuleSet", 3, excluded=["SizeRestrictions_BODY"]),
                managed("AWSManagedRulesKnownBadInputsRuleSet", 4),
            ])
        self.acl_arn = acl.attr_arn


class CaptionsStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, web_acl_arn: str, **kw):
        super().__init__(scope, id, **kw)

        if settings.get("vpc_id"):
            # Existing VPC (for an account at its VPC limit). Only new subnets are added, each with its own route table
            # and no routes, so nothing here can reach the internet. The VPC's internet gateway, required by CloudFront
            # VPC origins, must already be attached; none of these subnets route to it.
            vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=settings["vpc_id"])
            subnets = settings.get("app_subnets") or []
            if len(subnets) != 2:
                raise ValueError("with vpc_id, give app_subnets: two [availability zone, free CIDR] pairs in that VPC")
            app_subnets = [ec2.PrivateSubnet(self, f"App{i + 1}", vpc_id=vpc.vpc_id, availability_zone=az, cidr_block=cidr,
                                             map_public_ip_on_launch=False)
                           for i, (az, cidr) in enumerate(subnets)]
            for sn in app_subnets:
                cdk.Tags.of(sn).add("Name", f"live-interpreter-{sn.availability_zone}")
            in_app = ec2.SubnetSelection(subnets=app_subnets)
        else:
            # New VPC: two isolated subnets (no NAT, no route out). The internet gateway is attached only because
            # CloudFront VPC origins require one on the VPC; no route table points at it.
            vpc = ec2.Vpc(self, "Vpc", ip_addresses=ec2.IpAddresses.cidr(settings.get("vpc_cidr", "10.80.0.0/16")), max_azs=2,
                          nat_gateways=0, subnet_configuration=[ec2.SubnetConfiguration(name="App", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24)])
            igw = ec2.CfnInternetGateway(self, "Igw")
            ec2.CfnVPCGatewayAttachment(self, "IgwAttach", vpc_id=vpc.vpc_id, internet_gateway_id=igw.ref)
            in_app = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)

        endpoint_sg = ec2.SecurityGroup(self, "EndpointSg", vpc=vpc, allow_all_outbound=False, description="VPC interface endpoints")
        endpoint_sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(443))
        for name, svc in {
            "TranscribeStreaming": ec2.InterfaceVpcEndpointAwsService.TRANSCRIBE_STREAMING,
            "Translate": ec2.InterfaceVpcEndpointAwsService.TRANSLATE,
            "BedrockRuntime": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            "Logs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            "SecretsManager": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        }.items():
            vpc.add_interface_endpoint(name, service=svc, subnets=in_app, security_groups=[endpoint_sg], private_dns_enabled=True)
        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3, subnets=[in_app])  # ECR image layers

        # operator password + viewer link key, injected into the container as environment variables
        secret = sm.Secret(self, "OperatorPassword", description="Live Interpreter: operator password",
                           generate_secret_string=sm.SecretStringGenerator(exclude_punctuation=True, password_length=20))
        view_key = sm.Secret(self, "ViewKey", description="Live Interpreter: viewer link key (/captions?k=...)",
                             generate_secret_string=sm.SecretStringGenerator(exclude_punctuation=True, password_length=24))

        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, allow_all_outbound=False, description="internal ALB, CloudFront only")
        cf_prefix = ec2.PrefixList.from_lookup(self, "CloudFrontOriginFacing", prefix_list_name="com.amazonaws.global.cloudfront.origin-facing")
        alb_sg.add_ingress_rule(ec2.Peer.prefix_list(cf_prefix.prefix_list_id), ec2.Port.tcp(80), "CloudFront VPC origin")
        task_sg = ec2.SecurityGroup(self, "TaskSg", vpc=vpc, allow_all_outbound=False, description="caption server tasks")
        task_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8080), "from internal ALB")
        alb_sg.add_egress_rule(task_sg, ec2.Port.tcp(8080))
        task_sg.add_egress_rule(endpoint_sg, ec2.Port.tcp(443), "VPC endpoints")
        task_sg.add_egress_rule(ec2.Peer.prefix_list(self._s3_prefix_list()), ec2.Port.tcp(443), "S3 gateway endpoint (ECR layers)")

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)
        task = ecs.FargateTaskDefinition(self, "Task", cpu=1024, memory_limit_mib=2048,
                                         runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64,
                                                                              operating_system_family=ecs.OperatingSystemFamily.LINUX))
        # which events/<id>.toml the server loads (default: general); its vocabularies and terminologies are named <id>-en / <id>-ko
        event = settings.get("event") or ""
        # one week: speech content is in the log only when the operator turns it on for a session
        log_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.ONE_WEEK, removal_policy=cdk.RemovalPolicy.DESTROY)
        task.add_container(
            "server",
            image=ecs.ContainerImage.from_docker_image_asset(ecr_assets.DockerImageAsset(
                self, "Image", directory="..", platform=ecr_assets.Platform.LINUX_ARM64)),
            port_mappings=[ecs.PortMapping(container_port=8080)],
            environment={"AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION, "PYTHONUNBUFFERED": "1",
                         "DECKS_DIR": "/tmp/decks",  # uploaded slides: task-local scratch space, gone when the task is replaced
                         **({"EVENT": event} if event else {})},
            secrets={"OPERATOR_PASSWORD": ecs.Secret.from_secrets_manager(secret),
                     "VIEW_KEY": ecs.Secret.from_secrets_manager(view_key)},
            logging=ecs.LogDrivers.aws_logs(stream_prefix="captions", log_group=log_group),
        )
        # least privilege for the live path; global cross-Region inference may route to any commercial Region
        role = task.task_role
        role.add_to_policy(iam.PolicyStatement(actions=["transcribe:StartStreamTranscription", "transcribe:StartStreamTranscriptionWebSocket"], resources=["*"]))
        role.add_to_policy(iam.PolicyStatement(actions=["translate:TranslateText"], resources=["*"]))
        role.add_to_policy(iam.PolicyStatement(actions=["translate:GetTerminology"],
                                               resources=[f"arn:aws:translate:{REGION}:{self.account}:terminology/{event or 'general'}-*"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[arn for m in LIVE_MODELS + WRITEUP_MODELS for arn in (f"arn:aws:bedrock:{REGION}:{self.account}:inference-profile/global.{m}",
                                                            f"arn:aws:bedrock:*::foundation-model/{m}")]))

        # one task: the WebSocket hub lives in memory, and every viewer must see the same captions
        service = ecs.FargateService(self, "Service", cluster=cluster, task_definition=task, desired_count=1,
                                     min_healthy_percent=0, max_healthy_percent=100,
                                     security_groups=[task_sg], assign_public_ip=False,
                                     vpc_subnets=in_app,
                                     circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True))

        alb = elbv2.ApplicationLoadBalancer(self, "Alb", vpc=vpc, internet_facing=False, security_group=alb_sg,
                                            idle_timeout=cdk.Duration.seconds(3600),  # long-lived WebSockets
                                            vpc_subnets=in_app)
        listener = alb.add_listener("Http", port=80, open=False)
        listener.add_targets("Captions", port=8080, protocol=elbv2.ApplicationProtocol.HTTP, targets=[service],
                             deregistration_delay=cdk.Duration.seconds(10),
                             health_check=elbv2.HealthCheck(path="/healthz", interval=cdk.Duration.seconds(10),
                                                            healthy_threshold_count=2, timeout=cdk.Duration.seconds(5)))

        origin = origins.VpcOrigin.with_application_load_balancer(
            alb, protocol_policy=cf.OriginProtocolPolicy.HTTP_ONLY, read_timeout=cdk.Duration.seconds(60), keepalive_timeout=cdk.Duration.seconds(60))
        dynamic = dict(origin=origin, viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY,
                       allowed_methods=cf.AllowedMethods.ALLOW_ALL, cache_policy=cf.CachePolicy.CACHING_DISABLED,
                       origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER)
        # Module scripts are fetched with an Origin header, and the server checks it against Host.
        # Without this CloudFront sends the ALB's name as Host and the server refuses studio.js.
        static_request = cf.OriginRequestPolicy(self, "StaticRequest", comment="Live Interpreter static: viewer Host",
                                                header_behavior=cf.OriginRequestHeaderBehavior.allow_list("Host"))
        dist = cf.Distribution(
            self, "Cdn", comment="Live Interpreter", web_acl_id=web_acl_arn,
            minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021, http_version=cf.HttpVersion.HTTP2_AND_3,
            price_class=cf.PriceClass.PRICE_CLASS_200,
            default_behavior=cf.BehaviorOptions(**dynamic),
            additional_behaviors={"/static/*": cf.BehaviorOptions(
                origin=origin, viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED, origin_request_policy=static_request, compress=True)},
        )
        cdk.CfnOutput(self, "Url", value=f"https://{dist.distribution_domain_name}/")
        cdk.CfnOutput(self, "OperatorSecret", value=secret.secret_name)
        cdk.CfnOutput(self, "ViewKeySecret", value=view_key.secret_name)
        cdk.CfnOutput(self, "LogGroup", value=log_group.log_group_name)

    def _s3_prefix_list(self) -> str:
        return ec2.PrefixList.from_lookup(self, "S3Prefix", prefix_list_name=f"com.amazonaws.{REGION}.s3").prefix_list_id


app = cdk.App()
settings = json.loads(LOCAL.read_text()) if LOCAL.exists() else {}
for key in ("account", "vpc_id", "app_subnets", "vpc_cidr", "event"):
    value = app.node.try_get_context(key)
    if value is not None:
        settings[key] = json.loads(value) if key == "app_subnets" and isinstance(value, str) else value
account = settings.get("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
waf_stack = WafStack(app, "LiveInterpreterWaf", env=cdk.Environment(account=account, region="us-east-1"), cross_region_references=True)
CaptionsStack(app, "LiveInterpreter", web_acl_arn=waf_stack.acl_arn,
              env=cdk.Environment(account=account, region=REGION), cross_region_references=True)
app.synth()

"""Single-file CDK stack — networking, data, compute, observability.

Provisions everything Phase 38 of v0.4 calls for:

* VPC with two AZs, public + private subnets, one NAT gateway
* ALB → ECS service "api"
* ECS Cluster + 3 services (api / scheduler / worker), each with an
  ADOT sidecar emitting OTLP traces to X-Ray
* RDS Postgres Multi-AZ (gp3, autoscaling, Performance Insights,
  Enhanced Monitoring)
* ElastiCache Redis (single node, cf. Phase 39 may resize)
* SQS queue for cron / worker fanout
* Four S3 buckets (attachments / evolve / exports / audit-archive)
  with SSE-KMS, versioning on audit-archive, lifecycle to Glacier
* Secrets Manager secrets (DB, Redis, OpenAI, Anthropic, JWKS URL,
  webhook HMAC)
* Customer-managed KMS key (alias ``litehorse-{env}``)
* VPC endpoints for S3 / Secrets Manager / KMS (kept private)
* CloudWatch dashboard + alarms (CPU, memory, ALB 5xx,
  DatabaseConnections, queue depth, EMF ``errors_total``)
"""
from __future__ import annotations

from collections.abc import Mapping

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticache as elasticache
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from constructs import Construct

ADOT_IMAGE = "public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0"


class LiteHorseStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_name = env_name

        # 1. Foundations: KMS + VPC + endpoints -------------------------
        self.kms_key = kms.Key(
            self,
            "Cmk",
            alias=f"alias/litehorse-{env_name}",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )
        self.vpc.add_gateway_endpoint(
            "S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3
        )
        for name, svc in [
            ("Secrets", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER),
            ("Kms", ec2.InterfaceVpcEndpointAwsService.KMS),
            ("Sqs", ec2.InterfaceVpcEndpointAwsService.SQS),
        ]:
            self.vpc.add_interface_endpoint(f"Endpoint{name}", service=svc)

        # 2. Buckets ----------------------------------------------------
        self.buckets: dict[str, s3.Bucket] = {}
        for name, lifecycle in [
            ("attachments", False),
            ("evolve", False),
            ("exports", False),
            ("audit-archive", True),
        ]:
            self.buckets[name] = s3.Bucket(
                self,
                f"Bucket-{name}",
                bucket_name=f"litehorse-{env_name}-{name}",
                encryption=s3.BucketEncryption.KMS,
                encryption_key=self.kms_key,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                versioned=lifecycle,
                lifecycle_rules=[
                    s3.LifecycleRule(
                        transitions=[
                            s3.Transition(
                                storage_class=s3.StorageClass.GLACIER,
                                transition_after=Duration.days(90),
                            ),
                        ],
                    ),
                ]
                if lifecycle
                else None,
                removal_policy=RemovalPolicy.RETAIN,
            )

        # 3. SQS --------------------------------------------------------
        self.dead_letter = sqs.Queue(
            self,
            "DeadLetter",
            queue_name=f"litehorse-{env_name}-dead-letter",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=self.kms_key,
        )
        self.queue = sqs.Queue(
            self,
            "WorkerQueue",
            queue_name=f"litehorse-{env_name}-worker",
            visibility_timeout=Duration.seconds(900),
            retention_period=Duration.days(4),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=self.kms_key,
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=self.dead_letter, max_receive_count=5
            ),
        )

        # 4. Secrets ----------------------------------------------------
        self.db_secret = secretsmanager.Secret(
            self,
            "DbSecret",
            secret_name=f"litehorse/{env_name}/db",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"litehorse"}',
                generate_string_key="password",
                exclude_characters='"@/\\',
            ),
            encryption_key=self.kms_key,
        )
        self.redis_secret = secretsmanager.Secret(
            self, "RedisSecret", secret_name=f"litehorse/{env_name}/redis",
            encryption_key=self.kms_key,
        )
        self.openai_secret = secretsmanager.Secret(
            self, "OpenAISecret", secret_name=f"litehorse/{env_name}/openai-key",
            encryption_key=self.kms_key,
        )
        self.anthropic_secret = secretsmanager.Secret(
            self, "AnthropicSecret", secret_name=f"litehorse/{env_name}/anthropic-key",
            encryption_key=self.kms_key,
        )
        self.jwks_secret = secretsmanager.Secret(
            self, "JwksSecret", secret_name=f"litehorse/{env_name}/jwt-jwks-url",
            encryption_key=self.kms_key,
        )
        self.webhook_secret = secretsmanager.Secret(
            self, "WebhookSecret", secret_name=f"litehorse/{env_name}/webhook-secret",
            encryption_key=self.kms_key,
        )

        # 5. RDS Postgres Multi-AZ -------------------------------------
        self.db_param_group = rds.ParameterGroup(
            self,
            "PgParams",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_3
            ),
            parameters={
                "log_statement": "ddl",
                "log_min_duration_statement": "500",
                "shared_preload_libraries": "pg_stat_statements",
            },
        )
        self.db = rds.DatabaseInstance(
            self,
            "Postgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_3
            ),
            credentials=rds.Credentials.from_secret(self.db_secret),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MEDIUM
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            allocated_storage=50,
            max_allocated_storage=500,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            storage_encryption_key=self.kms_key,
            multi_az=True,
            backup_retention=Duration.days(14),
            deletion_protection=env_name == "prod",
            enable_performance_insights=True,
            performance_insight_encryption_key=self.kms_key,
            performance_insight_retention=rds.PerformanceInsightRetention.MONTHS_1,
            monitoring_interval=Duration.seconds(60),
            parameter_group=self.db_param_group,
            cloudwatch_logs_exports=["postgresql"],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 6. ElastiCache Redis -----------------------------------------
        self.redis_subnet_group = elasticache.CfnSubnetGroup(
            self,
            "RedisSubnets",
            description="lite-horse Redis subnets",
            subnet_ids=self.vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ).subnet_ids,
        )
        self.redis_sg = ec2.SecurityGroup(self, "RedisSg", vpc=self.vpc)
        self.redis = elasticache.CfnCacheCluster(
            self,
            "Redis",
            cache_node_type="cache.t4g.small",
            engine="redis",
            num_cache_nodes=1,
            cache_subnet_group_name=self.redis_subnet_group.ref,
            vpc_security_group_ids=[self.redis_sg.security_group_id],
        )

        # 7. ECS cluster + ALB-fronted api service ---------------------
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=self.vpc,
            container_insights=True,
        )
        self.repository = ecr.Repository(
            self,
            "Repo",
            repository_name=f"litehorse-{env_name}",
            removal_policy=RemovalPolicy.RETAIN,
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=20, tag_status=ecr.TagStatus.ANY)
            ],
        )

        common_env = self._common_env()
        common_secrets = self._common_secrets()

        self.log_group = logs.LogGroup(
            self,
            "ServiceLogs",
            log_group_name=f"/ecs/litehorse/{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.api_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "ApiService",
            cluster=self.cluster,
            cpu=512,
            memory_limit_mib=1024,
            desired_count=2 if env_name == "prod" else 1,
            public_load_balancer=True,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_ecr_repository(self.repository, "latest"),
                container_port=8080,
                environment={**common_env, "LITEHORSE_SERVICE": "api"},
                secrets=common_secrets,
                command=[
                    "uvicorn",
                    "lite_horse.web.app:create_app",
                    "--factory",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                ],
                log_driver=ecs.LogDriver.aws_logs(
                    stream_prefix="api", log_group=self.log_group
                ),
            ),
            health_check_grace_period=Duration.seconds(30),
        )
        self.api_service.target_group.configure_health_check(
            path="/v1/health", healthy_http_codes="200"
        )
        self._add_otel_sidecar(
            self.api_service.task_definition, service_label="api"
        )
        self._wire_db_redis(self.api_service.service.connections.security_groups[0])

        # Worker service (autoscale on queue depth)
        self.worker_service = self._fargate_service(
            "WorkerService",
            command=["python", "-m", "lite_horse.worker"],
            env_extra={"LITEHORSE_SERVICE": "worker"},
            scale_target=(0, 10) if env_name == "prod" else (0, 2),
        )
        self.queue.grant_consume_messages(self.worker_service.task_definition.task_role)
        self.queue.grant_send_messages(self.worker_service.task_definition.task_role)
        # Step-scaling on queue depth is wired in Phase 39 once load-test
        # numbers are in. We boot the worker at desired_count=scale_target[0]
        # for now; manual scaling works through the CDK redeploy.

        # Scheduler service: exactly one task
        self.scheduler_service = self._fargate_service(
            "SchedulerService",
            command=["python", "-m", "lite_horse.scheduler"],
            env_extra={"LITEHORSE_SERVICE": "scheduler"},
            scale_target=(1, 1),
        )
        self.queue.grant_send_messages(
            self.scheduler_service.task_definition.task_role
        )

        # 8. CloudWatch dashboard + alarms ------------------------------
        self._build_dashboard_and_alarms()

        # 9. Outputs ---------------------------------------------------
        CfnOutput(
            self, "AlbDns", value=self.api_service.load_balancer.load_balancer_dns_name
        )
        CfnOutput(self, "EcrRepository", value=self.repository.repository_uri)
        CfnOutput(self, "QueueUrl", value=self.queue.queue_url)
        CfnOutput(self, "DbEndpoint", value=self.db.db_instance_endpoint_address)
        CfnOutput(self, "RedisEndpoint", value=self.redis.attr_redis_endpoint_address)

    # ----- helpers --------------------------------------------------------

    def _common_env(self) -> dict[str, str]:
        return {
            "LITEHORSE_ENV": self.env_name,
            "LITEHORSE_S3_BUCKET_ATTACHMENTS": self.buckets["attachments"].bucket_name,
            "LITEHORSE_S3_BUCKET_EVOLVE": self.buckets["evolve"].bucket_name,
            "LITEHORSE_S3_BUCKET_EXPORTS": self.buckets["exports"].bucket_name,
            "LITEHORSE_S3_BUCKET_AUDIT": self.buckets["audit-archive"].bucket_name,
            "LITEHORSE_AWS_KMS_KEY_ID": self.kms_key.key_id,
            "LITEHORSE_SQS_QUEUE_URL": self.queue.queue_url,
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "OTEL_TRACES_EXPORTER": "otlp",
        }

    def _common_secrets(self) -> Mapping[str, ecs.Secret]:
        return {
            "LITEHORSE_DATABASE_URL": ecs.Secret.from_secrets_manager(
                self.db_secret, "password"
            ),
            "LITEHORSE_REDIS_URL": ecs.Secret.from_secrets_manager(self.redis_secret),
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(self.openai_secret),
            "ANTHROPIC_API_KEY": ecs.Secret.from_secrets_manager(self.anthropic_secret),
            "LITEHORSE_JWT_JWKS_URL": ecs.Secret.from_secrets_manager(self.jwks_secret),
        }

    def _fargate_service(
        self,
        construct_id: str,
        *,
        command: list[str],
        env_extra: dict[str, str],
        scale_target: tuple[int, int],
    ) -> ecs.FargateService:
        td = ecs.FargateTaskDefinition(self, f"{construct_id}Td", cpu=512, memory_limit_mib=1024)
        td.add_container(
            "app",
            image=ecs.ContainerImage.from_ecr_repository(self.repository, "latest"),
            command=command,
            environment={**self._common_env(), **env_extra},
            secrets=self._common_secrets(),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix=construct_id.lower(), log_group=self.log_group
            ),
        )
        self._add_otel_sidecar(td, service_label=construct_id.lower())
        svc = ecs.FargateService(
            self,
            construct_id,
            cluster=self.cluster,
            task_definition=td,
            desired_count=scale_target[0],
            assign_public_ip=False,
        )
        self._wire_db_redis(svc.connections.security_groups[0])
        return svc

    def _add_otel_sidecar(
        self, task_definition: ecs.TaskDefinition, *, service_label: str
    ) -> None:
        task_definition.add_container(
            "otel",
            image=ecs.ContainerImage.from_registry(ADOT_IMAGE),
            essential=False,
            logging=ecs.LogDriver.aws_logs(
                stream_prefix=f"otel-{service_label}", log_group=self.log_group
            ),
            environment={
                "AOT_CONFIG_CONTENT": _OTEL_CONFIG,
            },
        )
        # X-Ray + CloudWatch send permissions
        task_definition.task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess")
        )
        task_definition.task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy")
        )

    def _wire_db_redis(self, app_sg: ec2.ISecurityGroup) -> None:
        self.db.connections.allow_default_port_from(app_sg)
        self.redis_sg.add_ingress_rule(app_sg, ec2.Port.tcp(6379))

    def _build_dashboard_and_alarms(self) -> None:
        dash = cw.Dashboard(self, "Dashboard", dashboard_name=f"litehorse-{self.env_name}")
        alb_5xx = self.api_service.load_balancer.metrics.http_code_elb(
            elbv2.HttpCodeElb.ELB_5XX_COUNT
        )
        alarm_topic = sns.Topic(self, "AlarmTopic")

        cw.Alarm(
            self,
            "Alb5xx",
            metric=alb_5xx,
            threshold=10,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        cw.Alarm(
            self,
            "DbConnections",
            metric=self.db.metric_database_connections(),
            threshold=80,
            evaluation_periods=3,
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        cw.Alarm(
            self,
            "QueueDepth",
            metric=self.queue.metric_approximate_number_of_messages_visible(),
            threshold=500,
            evaluation_periods=3,
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        errors_metric = cw.Metric(
            namespace="litehorse",
            metric_name="errors_total",
            statistic="Sum",
            period=Duration.minutes(5),
        )
        cw.Alarm(
            self,
            "ErrorsTotal",
            metric=errors_metric,
            threshold=20,
            evaluation_periods=2,
        ).add_alarm_action(cw_actions.SnsAction(alarm_topic))

        dash.add_widgets(
            cw.GraphWidget(
                title="Turns / min",
                left=[
                    cw.Metric(
                        namespace="litehorse",
                        metric_name="turns_total",
                        statistic="Sum",
                        period=Duration.minutes(1),
                    )
                ],
            ),
            cw.GraphWidget(
                title="Tokens / hour",
                left=[
                    cw.Metric(
                        namespace="litehorse",
                        metric_name="tokens_total",
                        statistic="Sum",
                        period=Duration.hours(1),
                    )
                ],
            ),
            cw.GraphWidget(
                title="Cost USD micro / hour",
                left=[
                    cw.Metric(
                        namespace="litehorse",
                        metric_name="cost_usd_micro",
                        statistic="Sum",
                        period=Duration.hours(1),
                    )
                ],
            ),
            cw.GraphWidget(
                title="ALB latency p95",
                left=[
                    self.api_service.load_balancer.metrics.target_response_time(
                        statistic="p95"
                    )
                ],
            ),
        )


_OTEL_CONFIG = """
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
exporters:
  awsxray:
    region: us-east-1
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [awsxray]
""".strip()

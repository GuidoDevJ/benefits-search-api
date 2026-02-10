from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    CfnOutput,
)
from constructs import Construct


class ComputeStack(Stack):
    """ECS Fargate cluster with ALB.

    Redis is external (user-managed).
    Connection details are stored in Secrets Manager.
    Uses Fargate Spot for cost optimization (~70% savings).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        secret: secretsmanager.ISecret,
        logs_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        project_name = self.node.try_get_context("project_name") or "ia-comafi"
        image_tag = self.node.try_get_context("image_tag") or "latest"
        fargate_cpu = int(self.node.try_get_context("fargate_cpu") or 512)
        fargate_memory = int(
            self.node.try_get_context("fargate_memory") or 1024
        )

        # --- ECR Repository ---
        self.repository = ecr.Repository(
            self,
            "Repository",
            repository_name=f"{project_name}-api",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    max_image_count=10,
                    description="Keep last 10 images",
                ),
            ],
        )

        # --- ECS Cluster ---
        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            cluster_name=f"{project_name}-cluster",
            container_insights=ecs.ContainerInsights.DISABLED,
            enable_fargate_capacity_providers=True,
        )

        # --- IAM: Task Execution Role ---
        execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )
        secret.grant_read(execution_role)

        # --- IAM: Task Role (runtime permissions) ---
        task_role = iam.Role(
            self,
            "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Bedrock invoke
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        # S3 for unhandled queries
        logs_bucket.grant_write(task_role)

        # --- Task Definition ---
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=fargate_cpu,
            memory_limit_mib=fargate_memory,
            execution_role=execution_role,
            task_role=task_role,
        )

        # --- API Container ---
        api_container = task_definition.add_container(
            "api",
            image=ecs.ContainerImage.from_ecr_repository(
                self.repository, tag=image_tag
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="api",
                log_group=logs.LogGroup(
                    self,
                    "ApiLogGroup",
                    log_group_name=f"/ecs/{project_name}/api",
                    retention=logs.RetentionDays.TWO_WEEKS,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            ),
            environment={
                "AWS_DEFAULT_REGION": self.region,
                "BEDROCK_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
                "CACHE_TTL_DEFAULT": "86400",
                "CACHE_TTL_BENEFITS": "86400",
                "CACHE_ENABLED": "true",
                "S3_BUCKET_UNHANDLED": logs_bucket.bucket_name,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": f"{project_name}-prod",
            },
            # Secrets from Secrets Manager
            # Set values after first deploy via AWS CLI
            secrets={
                "LANGCHAIN_API_KEY": ecs.Secret.from_secrets_manager(
                    secret, "LANGCHAIN_API_KEY"
                ),
                "REDIS_HOST": ecs.Secret.from_secrets_manager(
                    secret, "REDIS_HOST"
                ),
                "REDIS_PORT": ecs.Secret.from_secrets_manager(
                    secret, "REDIS_PORT"
                ),
                "REDIS_PASSWORD": ecs.Secret.from_secrets_manager(
                    secret, "REDIS_PASSWORD"
                ),
            },
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "python -c \"import urllib.request;"
                    " urllib.request.urlopen("
                    "'http://localhost:8000/')\"",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(90),
            ),
            essential=True,
        )

        api_container.add_port_mappings(
            ecs.PortMapping(container_port=8000, protocol=ecs.Protocol.TCP)
        )

        # --- ALB + Fargate Service ---
        alb_fargate = ecs_patterns.ApplicationLoadBalancedFargateService
        self.fargate_service = alb_fargate(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            public_load_balancer=True,
            assign_public_ip=False,
            task_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE_SPOT",
                    weight=2,
                ),
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE",
                    weight=1,
                ),
            ],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            health_check_grace_period=Duration.seconds(120),
        )

        # ALB health check
        self.fargate_service.target_group.configure_health_check(
            path="/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(10),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # --- Auto Scaling ---
        scaling = self.fargate_service.service.auto_scale_task_count(
            min_capacity=int(self.node.try_get_context("min_capacity") or 1),
            max_capacity=int(self.node.try_get_context("max_capacity") or 3),
        )

        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.seconds(300),
            scale_out_cooldown=Duration.seconds(60),
        )

        scaling.scale_on_request_count(
            "RequestScaling",
            requests_per_target=500,
            target_group=self.fargate_service.target_group,
            scale_in_cooldown=Duration.seconds(300),
            scale_out_cooldown=Duration.seconds(60),
        )

        # --- Expose ALB for WAF ---
        self.alb = self.fargate_service.load_balancer
        self.alb_arn = self.alb.load_balancer_arn

        # --- Outputs ---
        CfnOutput(
            self,
            "ServiceUrl",
            value=f"http://{self.alb.load_balancer_dns_name}",
        )
        CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.repository.repository_uri,
        )
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(
            self,
            "ServiceName",
            value=self.fargate_service.service.service_name,
        )

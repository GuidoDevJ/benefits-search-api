from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_secretsmanager as secretsmanager,
    aws_s3 as s3,
    CfnOutput,
)
from constructs import Construct


class DataStack(Stack):
    """Secrets Manager + S3 bucket for unhandled queries.

    Redis runs as a sidecar in ECS (no ElastiCache needed).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        project_name = self.node.try_get_context("project_name") or "ia-comafi"

        # --- Secrets Manager ---
        # After deploy, set real values via AWS CLI:
        #   aws secretsmanager put-secret-value \
        #     --secret-id ia-comafi/app-secrets \
        #     --secret-string '{"LANGCHAIN_API_KEY":"your-key-here"}'
        self.secret = secretsmanager.Secret(
            self,
            "AppSecrets",
            secret_name=f"{project_name}/app-secrets",
            description="Application secrets for ia-comafi API",
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- S3 Bucket for unhandled queries ---
        self.logs_bucket = s3.Bucket(
            self,
            "UnhandledQueriesBucket",
            bucket_name=f"{project_name}-ai-logs-{self.account}",
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration_after_days=90,
                    description="Delete logs after 90 days",
                ),
            ],
        )

        # --- Outputs ---
        CfnOutput(self, "SecretArn", value=self.secret.secret_arn)
        CfnOutput(self, "LogsBucketName", value=self.logs_bucket.bucket_name)

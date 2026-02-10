from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    CfnOutput,
)
from constructs import Construct


class NetworkStack(Stack):
    """VPC with public and private subnets across 2 AZs.

    Uses 1 NAT Gateway (cost-optimized) instead of 2 (HA).
    Trade-off: single AZ failure could impact egress temporarily.
    For full HA, set nat_gateways=2 in cdk.json.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        nat_gateways = int(self.node.try_get_context("nat_gateways") or 1)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
            restrict_default_security_group=True,
        )

        # VPC Flow Logs (best practice for security auditing)
        self.vpc.add_flow_log("FlowLogCloudWatch")

        # --- Outputs ---
        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(
            self,
            "PrivateSubnets",
            value=",".join(s.subnet_id for s in self.vpc.private_subnets),
        )
        CfnOutput(
            self,
            "PublicSubnets",
            value=",".join(s.subnet_id for s in self.vpc.public_subnets),
        )

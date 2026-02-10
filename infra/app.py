#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.data_stack import DataStack
from stacks.compute_stack import ComputeStack
from stacks.waf_stack import WafStack

app = cdk.App()

project_name = app.node.try_get_context("project_name") or "ia-comafi"

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

tags = {
    "Project": project_name,
    "ManagedBy": "cdk",
    "Environment": app.node.try_get_context("environment") or "prod",
}

# --- Stacks ---

network_stack = NetworkStack(
    app,
    f"{project_name}-network",
    env=env,
)

data_stack = DataStack(
    app,
    f"{project_name}-data",
    vpc=network_stack.vpc,
    env=env,
)

compute_stack = ComputeStack(
    app,
    f"{project_name}-compute",
    vpc=network_stack.vpc,
    secret=data_stack.secret,
    logs_bucket=data_stack.logs_bucket,
    env=env,
)

waf_stack = WafStack(
    app,
    f"{project_name}-waf",
    alb_arn=compute_stack.alb_arn,
    env=env,
)

# Apply tags to all stacks
for stack in [network_stack, data_stack, compute_stack, waf_stack]:
    for key, value in tags.items():
        cdk.Tags.of(stack).add(key, value)

app.synth()

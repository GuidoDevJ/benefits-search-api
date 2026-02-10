from aws_cdk import (
    Stack,
    aws_wafv2 as wafv2,
    CfnOutput,
)
from constructs import Construct


class WafStack(Stack):
    """WAF WebACL attached to the ALB.

    Rules:
    1. Rate limiting (2000 req / 5 min per IP)
    2. AWS Managed - Common Rule Set (XSS, path traversal, etc.)
    3. AWS Managed - Known Bad Inputs (Log4j, etc.)
    4. AWS Managed - IP Reputation List (botnets, scanners)

    Cost: ~$10/month (WebACL $5 + rules $1 each + $0.60/M requests)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        alb_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        project_name = self.node.try_get_context("project_name") or "ia-comafi"
        rate_limit = int(self.node.try_get_context("waf_rate_limit") or 2000)

        def _visibility(name: str) -> wafv2.CfnWebACL.VisibilityConfigProperty:
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{project_name}-{name}",
                sampled_requests_enabled=True,
            )

        # --- WebACL ---
        self.web_acl = wafv2.CfnWebACL(
            self,
            "WebACL",
            name=f"{project_name}-waf",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(
                allow=wafv2.CfnWebACL.AllowActionProperty()
            ),
            scope="REGIONAL",
            visibility_config=_visibility("global"),
            rules=[
                # ---- Rule 1: Rate Limiting ----
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitPerIP",
                    priority=1,
                    action=wafv2.CfnWebACL.RuleActionProperty(
                        block=wafv2.CfnWebACL.BlockActionProperty()
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=rate_limit,
                            aggregate_key_type="IP",
                        ),
                    ),
                    visibility_config=_visibility("rate-limit"),
                ),
                # ---- Rule 2: AWS Common Rule Set ----
                # Protects against XSS, path traversal, local file inclusion
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSCommonRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        none={}
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        ),
                    ),
                    visibility_config=_visibility("common-rules"),
                ),
                # ---- Rule 3: Known Bad Inputs ----
                # Protects against Log4j, Java deserialization, etc.
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSKnownBadInputs",
                    priority=3,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        none={}
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesKnownBadInputsRuleSet",
                        ),
                    ),
                    visibility_config=_visibility("bad-inputs"),
                ),
                # ---- Rule 4: IP Reputation List ----
                # Blocks requests from IPs known for malicious activity
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSIPReputationList",
                    priority=4,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        none={}
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesAmazonIpReputationList",
                        ),
                    ),
                    visibility_config=_visibility("ip-reputation"),
                ),
            ],
        )

        # --- Associate WAF with ALB ---
        wafv2.CfnWebACLAssociation(
            self,
            "WebACLAssociation",
            resource_arn=alb_arn,
            web_acl_arn=self.web_acl.attr_arn,
        )

        # --- Outputs ---
        CfnOutput(self, "WebACLArn", value=self.web_acl.attr_arn)
        CfnOutput(self, "WebACLName", value=f"{project_name}-waf")

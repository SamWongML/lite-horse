"""CDK entry-point for the lite-horse v0.4 cloud stack.

Run ``cdk synth --context env=dev`` (or ``staging`` / ``prod``) to
generate one CloudFormation template per environment.
"""
from __future__ import annotations

import os

import aws_cdk as cdk

from lite_horse_stack import LiteHorseStack

app = cdk.App()
env_name = app.node.try_get_context("env") or os.environ.get("LITEHORSE_ENV", "dev")
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
region = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")

LiteHorseStack(
    app,
    f"LiteHorse-{env_name}",
    env_name=env_name,
    env=cdk.Environment(account=account, region=region),
)

app.synth()

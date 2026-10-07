"""Print the operator link of the AWS deployment: open it once and bookmark it, no password needed.

    .venv/bin/python tools/operator_link.py            # stack LiveInterpreter in ap-northeast-2

The key is derived from the operator password and the view key (server.py Auth.operator_key), so it changes
whenever either secret changes. Treat the link like the password.
"""
import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import Auth  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--stack", default="LiveInterpreter")
ap.add_argument("--region", default="ap-northeast-2")
ap.add_argument("--profile", default=None)
a = ap.parse_args()
session = boto3.Session(profile_name=a.profile, region_name=a.region)
outputs = {o["OutputKey"]: o["OutputValue"]
           for o in session.client("cloudformation").describe_stacks(StackName=a.stack)["Stacks"][0]["Outputs"]}
sm = session.client("secretsmanager")
password = sm.get_secret_value(SecretId=outputs["OperatorSecret"])["SecretString"]
view_key = sm.get_secret_value(SecretId=outputs["ViewKeySecret"])["SecretString"]
print(f"{outputs['Url'].rstrip('/')}/?k={Auth(password, view_key).operator_key}")

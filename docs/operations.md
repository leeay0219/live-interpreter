# Deployment and operations

## Deploy to AWS

This procedure is written so that a person or a coding agent can follow it step by step. Replace `<account>`, `<region>` and `<event>` with your values. The defaults are `ap-northeast-2` and `general`.

Rules for an agent following it:

- Do not run `setup_event.py` without `--dry-run`, `cdk bootstrap`, `cdk deploy`, `cdk destroy` or any delete command until the user has confirmed that step.
- Do not run commands that print secret values. Give the user those commands to run themselves.
- Do not redeploy or destroy while a session is live.

### 1. Check the tools

Run each check. Install or update anything that fails.

| Tool | Check | Needed |
| --- | --- | --- |
| AWS CLI v2 | `aws --version` | 2.32.0 or later, which has `aws login` |
| Node.js | `node --version` | 20 or later |
| AWS CDK CLI | `cdk --version` | 2.1108.0 or later, which reads the output of `aws-cdk-lib` 2.255.0 in `infra/requirements.txt`. Install with `npm i -g aws-cdk` |
| Docker | `docker info` | The daemon must be running. Finch also works with `CDK_DOCKER=finch` |
| ARM64 builds | `docker buildx ls` | `linux/arm64` must be listed. On an x86 machine install QEMU with `docker run --privileged --rm tonistiigi/binfmt --install arm64` |
| Python and uv | `uv --version` and `uv python find 3.12` | Python 3.12 |
| Pandoc | `pandoc --version` | Needed by `tools/bootstrap.sh` for the local environment |

### 2. Sign in and check the account

```bash
aws login
aws sts get-caller-identity --query Account --output text
```

The second command prints the account ID. Use it as `<account>`. The credentials need permission to create the resources in `infra/app.py` and to run CloudFormation.

### 3. Check the region

The region needs Transcribe streaming, Translate, Bedrock runtime and their VPC endpoints. This read only command must list seven services:

```bash
aws ec2 describe-vpc-endpoint-services --region <region> \
  --service-names com.amazonaws.<region>.transcribestreaming com.amazonaws.<region>.translate \
  com.amazonaws.<region>.bedrock-runtime com.amazonaws.<region>.ecr.api com.amazonaws.<region>.ecr.dkr \
  com.amazonaws.<region>.logs com.amazonaws.<region>.secretsmanager \
  --query "ServiceNames" --output text
```

### 4. Check Bedrock model access

The model IDs come from `settings.py`: `DEFAULT_MODEL`, `FALLBACK_MODEL` and `WRITEUP_MODEL`. All three are global inference profiles.

1. Anthropic models need a one-time use case form per account. In the Bedrock console for `<region>`, open the model catalog, choose a Claude model and submit the form if the console asks for it.
2. Check that the global inference profiles exist in the region. This read only command must list all three IDs:

   ```bash
   aws bedrock list-inference-profiles --region <region> --type-equals SYSTEM_DEFINED \
     --query "inferenceProfileSummaries[?starts_with(inferenceProfileId, 'global.anthropic')].inferenceProfileId" --output text
   ```

3. Check that the account may use each model. Run this for each ID without the `global.` prefix. `authorizationStatus` must be `AUTHORIZED` and `entitlementAvailability` must be `AVAILABLE`:

   ```bash
   aws bedrock get-foundation-model-availability --region <region> --model-id anthropic.claude-sonnet-5-5
   ```

4. The task role cannot accept a Marketplace subscription. If `entitlementAvailability` is not `AVAILABLE`, invoke the model once from the Bedrock console playground as a user with Marketplace permissions, then check again.

### 5. Prepare the local environment

```bash
tools/bootstrap.sh
cd infra
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cd ..
```

### 6. Choose the settings

Settings are read in this order, later ones win: the defaults in `infra/app.py`, `infra/local.json`, then `-c key=value` on the `cdk` command line. `infra/local.json` is ignored by Git and the release builder.

| Key | Default | Meaning |
| --- | --- | --- |
| `account` | `CDK_DEFAULT_ACCOUNT` | AWS account ID |
| `region` | `ap-northeast-2` | Region of the application stack. The WAF stack is always in `us-east-1` |
| `event` | `general` | `events/<event>.toml` that the server loads |
| `vocabulary` | `true` | Use the event's Transcribe vocabularies and Translate terminologies. With `false` the task runs with `--no-vocabulary` |
| `vpc_cidr` | `10.80.0.0/16` | CIDR of the new VPC. Used only without `vpc_id` |
| `vpc_id` | none | Existing VPC to use. It needs an internet gateway attached, because CloudFront VPC origins require one |
| `app_subnets` | none | With `vpc_id`: two `[availability zone, free CIDR]` pairs for the two new subnets |
| `existing_endpoints` | `false` | With `vpc_id`: the VPC already has the interface endpoints, so the stack creates none |
| `rate_limit` | `2000` | Requests per IP per 5 minutes before WAF blocks that IP |
| `alarm_email` | none | Optional. Email for alarms and the monthly budget. Without it the stack creates no alarms and no budget |
| `monthly_budget_usd` | `300` | Optional. Monthly cost budget, used only with `alarm_email` |

New VPC: copy `infra/local.new-vpc.example.json` to `infra/local.json` and set your account. You can also skip the file and pass `-c` values.

Existing VPC: copy `infra/local.existing-vpc.example.json` to `infra/local.json` and set the account, VPC ID and two free CIDRs in two availability zones of that VPC. Use this when the account is at its VPC limit.

With `existing_endpoints` set to `true`, the VPC must already have interface endpoints with private DNS for all seven services in step 3. Their security groups must accept TCP 443 from the two new subnets. Check them with:

```bash
aws ec2 describe-vpc-endpoints --region <region> --filters Name=vpc-id,Values=<vpc_id> \
  --query "VpcEndpoints[].[ServiceName,VpcEndpointType,PrivateDnsEnabled]" --output table
```

WAF counts requests per source IP. Attendees on one venue Wi-Fi or behind one NAT share one IP, so they share one limit. For a large room raise `rate_limit`, for example to `10000`. The login form keeps its own limit of 100 requests per IP per 5 minutes.

Alarms are off by default and most deployments do not need them. Add `alarm_email` only if you want them. With it, the stack creates an SNS topic with an email subscription and three alarms: no running task, an unhealthy ALB target, and 5 or more target 5xx responses per minute for 3 minutes. AWS sends a confirmation email that must be accepted before alarms arrive. The budget covers the whole account, not only this stack. It emails at 80 percent of actual cost and 100 percent of forecast cost.

### 7. Create the event resources

With `vocabulary` on, the vocabularies and terminologies must exist in the same region, for the same event, before the server starts. Otherwise transcription fails to start. Check the content first, then create them:

```bash
.venv/bin/python setup_event.py <event> --dry-run
.venv/bin/python setup_event.py <event> --region <region>
```

The second command waits until both vocabularies are ready. Each one must end in `READY`. Run it again whenever the event file changes. Pass the same event to CDK with `-c event=<event>`.

To deploy before the vocabularies exist, pass `-c vocabulary=false` and skip this step.

### 8. Bootstrap CDK

Bootstrap creates a `CDKToolkit` stack, an S3 bucket and an ECR repository in each region. Both regions are needed because WAF for CloudFront lives in `us-east-1`. Ask the user before running it. Skip it if the account was bootstrapped before.

```bash
cd infra
cdk bootstrap aws://<account>/<region> aws://<account>/us-east-1
```

### 9. Synthesize and review

`cdk synth` looks up the VPC and AWS prefix lists, so it needs credentials. The results are cached in `infra/cdk.context.json`, which is ignored by Git.

```bash
CDK_DEFAULT_ACCOUNT=<account> cdk synth -c event=<event> -c region=<region>
CDK_DEFAULT_ACCOUNT=<account> cdk diff --all -c event=<event> -c region=<region>
```

Show the user the `cdk diff` output and this cost reminder before asking to deploy:

- Fargate, the internal ALB, WAF, seven interface endpoints in two subnets and CloudFront are charged while the application is idle.
- Transcribe, Translate and Bedrock calls add usage charges during sessions.
- Check pricing for the chosen region and models.

Deploy only after the user confirms.

### 10. Deploy

```bash
CDK_DEFAULT_ACCOUNT=<account> cdk deploy --all -c event=<event> -c region=<region>
```

Use the same `-c` values as in step 9. The first deployment takes about 15 minutes. CDK asks to approve IAM and security group changes.

### 11. Check the deployment

The outputs are `Url`, `OperatorSecret`, `ViewKeySecret` and `LogGroup`. `OperatorSecret` and `ViewKeySecret` are secret names, not values. Read the outputs again with:

```bash
aws cloudformation describe-stacks --region <region> --stack-name LiveInterpreter \
  --query "Stacks[0].Outputs" --output table
```

A new CloudFront distribution can take several minutes to answer everywhere. Retry until the health check returns `ok`:

```bash
curl -fsS <Url>healthz
```

If it keeps failing, check the service events and the `LogGroup` output in CloudWatch.

### 12. Give the user operator access

The operator password and operator link control the session. An agent must not retrieve them. The user runs one of these from the repository root:

- Operator link, to open once and bookmark: `.venv/bin/python tools/operator_link.py --region <region>`
- Password: `aws secretsmanager get-secret-value --region <region> --secret-id <OperatorSecret> --query SecretString --output text`
- Console: Secrets Manager in `<region>`, open the secret named by `OperatorSecret`, then choose Retrieve secret value.

Do not paste these into chats, logs, issues or screenshots.

### 13. Attendee links

Attendees get a caption link with the view key, not the operator link. The operator opens the share menu in the studio to copy the attendee link or show its QR code. The view key changes only when its secret changes or the stack is recreated.

### 14. Before an event

Verify audio in a private rehearsal and confirm captions on an attendee device. Use a separate session for any broadcast test.

## Redeploy

The service runs one task, and a deployment replaces it. Every redeploy that changes the task, including a changed event, region setting or image, ends the live session and clears its records, uploads and jobs. The alarms for no running task may fire during the switch.

Redeploy only between sessions. Download required records first, then run step 9 and step 10 again with the same settings.

## Records and data flow

Audio is sent to Transcribe. Recognized text and translation context are sent to the selected translation service. Uploaded PDF page images and text are sent to Bedrock for analysis. Generating a write-up sends the session transcript to Bedrock.

Session records and generated documents remain in server memory. Rendered PDFs use temporary server storage. There is no durable recovery after a process restart. Download required records before preparing a new session or restarting.

The operator can opt into logging conversation content. Leave this disabled unless the session requires it. The deployed log group retains logs for seven days.

Global inference profiles may process requests outside the configured region. Choose a supported inference configuration appropriate for the data before use.

## Restart and authentication

Stop or end the current session before restarting. Export required records, restart the process, reload the operator page and upload materials again.

For local AWS authentication errors, refresh credentials with `aws login`. If the running stream still uses expired credentials, end the session and restart the local server after saving its records.

`OPERATOR_PASSWORD` and `VIEW_KEY` enable local access control. In AWS these come from Secrets Manager. Do not include their values or operator links in screenshots, logs, issues or shared files.

Without a password, bind only to a loopback address. The server also validates browser origins and local Host headers, including for WebSocket connections. Foreign or opaque browser origins are rejected. Command-line clients without browser origin headers can still use the local service.

Application access logs record route templates and status, without query strings or Referer headers. Responses set `Referrer-Policy: no-referrer`. The CDK configuration disables WAF request sampling, and does not enable CloudFront or ALB access logging. If you add another proxy or logging layer, exclude bearer links and request bodies there as well.

Older versions may have recorded operator links. Review existing log access and retention before sharing diagnostics; rotate exposed credentials through your normal approved process. Updating source does not remove historical logs or rotate credentials.

## Cost and teardown

Fargate, ALB, WAF and interface endpoints can incur charges while the application is idle. Model and recognition calls add usage charges. Review pricing for the chosen region and models in your account.

Teardown deletes the application and its records. Save required records first and ask the user to confirm. Use the same settings as the deployment:

```bash
cd infra
CDK_DEFAULT_ACCOUNT=<account> cdk destroy --all -c event=<event> -c region=<region>
```

Recreating the environment changes addresses and generated credentials.

These remain after `cdk destroy`. Remove each only after the user confirms, and only if nothing else uses it.

| What remains | Where | How to remove |
| --- | --- | --- |
| Transcribe vocabularies `<event>-en` and `<event>-ko` | `<region>` | `aws transcribe delete-vocabulary --region <region> --vocabulary-name <event>-en`, then the same for `<event>-ko` |
| Translate terminologies `<event>-en` and `<event>-ko` | `<region>` | `aws translate delete-terminology --region <region> --name <event>-en`, then the same for `<event>-ko` |
| `CDKToolkit` stack, its S3 bucket `cdk-hnb659fds-assets-<account>-<region>` and ECR repository `cdk-hnb659fds-container-assets-<account>-<region>` | `<region>` and `us-east-1` | Other CDK apps in the account may use them. Empty the bucket in the S3 console, including old versions. Delete the stack with `aws cloudformation delete-stack --region <region> --stack-name CDKToolkit`. Then delete the bucket, and the repository with `aws ecr delete-repository --region <region> --repository-name <name> --force` if it is still there |
| Log groups of Container Insights and of the stacks' helper functions | `<region>` and `us-east-1` | List them with `aws logs describe-log-groups --region <region> --log-group-name-prefix /aws/ecs/containerinsights/` and `--log-group-name-prefix /aws/lambda/LiveInterpreter`, then `aws logs delete-log-group --region <region> --log-group-name <name>` |
| Operator password and view key secrets | `<region>` | Secrets Manager keeps deleted secrets for a recovery window. List them with `aws secretsmanager list-secrets --region <region> --include-planned-deletion --query "SecretList[?DeletedDate].Name"`. To remove one at once: `aws secretsmanager delete-secret --region <region> --secret-id <name> --force-delete-without-recovery` |
| Local CDK files | `infra/cdk.context.json`, `infra/cdk.out/` | Delete them before deploying to another account |

The application log group, the SNS topic, the alarms and the budget are deleted with the stack.

# Deployment and operations

## Prerequisites

- AWS credentials with permissions for the services in `infra/app.py`.
- Access to the model IDs configured in `settings.py` and `postprocess/process_recording.py`.
- Python 3.12, uv, Pandoc, Node.js and the AWS CDK CLI.
- An ARM64-capable container builder. Docker Desktop is the default; Finch can be selected with `CDK_DOCKER=finch`.

## Deploy

Review the generated infrastructure before deployment.

```bash
tools/bootstrap.sh
aws login
.venv/bin/python setup_event.py
cd infra
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cdk bootstrap aws://<account>/ap-northeast-2 aws://<account>/us-east-1
CDK_DEFAULT_ACCOUNT=<account> cdk synth
CDK_DEFAULT_ACCOUNT=<account> cdk deploy --all
```

The output `Url` identifies the application. `OperatorSecret` identifies the secret containing the operator password. The operator link can also be generated with `tools/operator_link.py`.

The default creates a new VPC. To use an existing one, copy `infra/local.example.json` to `infra/local.json` and supply your own account, VPC and free subnet CIDRs. The local file is ignored by Git and the release builder.

Before an event, verify audio in a private rehearsal and confirm captions on an attendee device. Use a separate session for any broadcast test.

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

When the environment is no longer needed, save required records and review the resources before running:

```bash
cd infra
cdk destroy --all
```

Recreating the environment changes addresses and generated credentials.

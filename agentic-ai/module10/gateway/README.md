# Portkey commands

Run from `agentic-ai/` after [cloud deployment](../deployment/README.md).

```bash
export MODULE10_DEPLOYMENT_DIR="$PWD/module10/.local/workshop"
```

Start and retrieve the console token:

```bash
.venv/bin/python -m module10.gateway.lifecycle start \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json"
.venv/bin/python -m module10.gateway.lifecycle console-login \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json"
```

Open the printed HTTPS URL, accept the self-signed certificate warning, and enter
the console token. After a restart, use the newly printed URL.

Check status, extend for two hours, or stop:

```bash
.venv/bin/python -m module10.gateway.lifecycle status \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json"
.venv/bin/python -m module10.gateway.lifecycle extend \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json" --hours 2
.venv/bin/python -m module10.gateway.lifecycle stop \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json"
```

Verify HTTPS, console authentication, and a real model fallback:

```bash
.venv/bin/python -m module10.gateway.verify \
  --outputs "$MODULE10_DEPLOYMENT_DIR/portkey-outputs.json" \
  --report "$MODULE10_DEPLOYMENT_DIR/portkey-verification.json" --model
```

[Costs and cleanup](../deployment/OPERATIONS.md) · [Local demo](../README.md#local)

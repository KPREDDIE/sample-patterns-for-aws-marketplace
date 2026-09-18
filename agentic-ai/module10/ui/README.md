# Browser client

Static HTML, CSS, and JavaScript, published during cloud deployment.

- [Deploy](../deployment/README.md)
- [Open the UI and retrieve credentials](../README.md#open-the-uis)
- [Start Portkey](../gateway/README.md)

After editing UI files, publish them with:

```bash
pulumi -C module10/deployment/pulumi up --stack workshop \
  --config-file "$MODULE10_DEPLOYMENT_DIR/stack.yaml"
```

Use the AWS credentials and Pulumi environment from the deployment guide.

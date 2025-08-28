# Common Fixes

## Redis Connection Failed

- Update backend secret with correct Redis password from Helm secret

## Database Init Failed

- Use env vars instead of kubectl in job container

## Workflow Failed

- Check ACR secrets in GitHub (ACR_USERNAME, ACR_PASSWORD, ACR_LOGIN_SERVER)

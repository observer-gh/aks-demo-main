# Quick Setup

## Local (Rancher Desktop)

```bash
./k8s/simple-deploy.sh
```

## CI/CD

- Push to main → builds and pushes to ACR
- Images: tk-backend:latest, tk-frontend:latest
- Security scan with Trivy (fails on HIGH/CRITICAL)

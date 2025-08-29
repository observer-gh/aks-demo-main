#!/bin/bash


# This script deploys a complete application stack with MariaDB, Backend, and Frontend

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
print_status "Checking prerequisites..."

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    print_error "kubectl is not installed. Please install kubectl first."
    exit 1
fi

# Check kubectl context
print_status "Checking kubectl context..."
CURRENT_CONTEXT=$(kubectl config current-context)
print_status "Current kubectl context: $CURRENT_CONTEXT"

echo -n "Do you want to continue with this context? (y/N): "
read -r response
if [[ ! "$response" =~ ^[Yy]$ ]]; then
    print_error "Deployment cancelled by user"
    exit 1
fi

# Check if helm is installed
if ! command -v helm &> /dev/null; then
    print_error "helm is not installed. Please install helm first."
    exit 1
fi

print_status "Prerequisites check completed successfully!"

# Section 1: Add Bitnami Helm Repository
print_status "Adding Bitnami Helm repository..."
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Section 2: Create namespace
print_status "Checking and creating tk namespace..."
if ! kubectl get namespace tk &> /dev/null; then
    print_status "Creating namespace tk..."
    kubectl create namespace tk
else
    print_status "Namespace tk already exists"
fi

# Section 3: Deploy Redis
print_status "Deploying Redis cluster..."

# Check if Redis already exists
if helm list -n tk | grep -q "my-redis"; then
    print_warning "Redis cluster already exists, upgrading..."
    helm upgrade my-redis bitnami/redis -f k8s_local/redis-values.yaml -n tk
else
    helm install my-redis bitnami/redis -f k8s_local/redis-values.yaml -n tk
fi
print_status "Waiting for Redis pods to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=my-redis -n tk --timeout=300s

print_status "Redis cluster deployed successfully!"

# Section 4: Deploy Kafka
print_status "Deploying Kafka cluster..."

# Check if Kafka already exists
if helm list -n tk | grep -q "my-kafka"; then
    print_warning "Kafka cluster already exists, upgrading..."
    helm upgrade my-kafka bitnami/kafka -f k8s_local/kafka-values.yaml -n tk
else
    helm install my-kafka bitnami/kafka -f k8s_local/kafka-values.yaml -n tk
fi
print_status "Waiting for Kafka pods to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=my-kafka -n tk --timeout=300s

print_status "Kafka cluster deployed successfully!"

# Section 5: Deploy MariaDB
print_status "Deploying basic MariaDB..."

# Check if MariaDB already exists
if helm list -n tk | grep -q "tk-mariadb"; then
    print_warning "MariaDB Helm release already exists, upgrading..."
    helm upgrade tk-mariadb bitnami/mariadb -f k8s_local/mariadb-values.yaml -n tk
else
    helm install tk-mariadb bitnami/mariadb -f k8s_local/mariadb-values.yaml -n tk
fi
print_status "Waiting for MariaDB pods to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=tk-mariadb -n tk --timeout=300s

print_status "MariaDB deployed successfully!"

# Section 6: Initialize Database
print_status "Creating database initialization ConfigMap..."
kubectl create configmap db-init-script --from-file=db/init.sql -n tk --dry-run=client -o yaml | kubectl apply -f -

print_status "Running database initialization job..."

# Check if init job already exists and completed
if kubectl get job db-init-job -n tk &> /dev/null; then
    JOB_STATUS=$(kubectl get job db-init-job -n tk -o jsonpath='{.status.conditions[0].type}' 2>/dev/null || echo "Unknown")
    if [[ "$JOB_STATUS" == "Complete" ]]; then
        print_warning "Database initialization job already completed, skipping"
    else
        print_status "Database initialization job exists but not complete, waiting..."
        kubectl wait --for=condition=complete job/db-init-job -n tk --timeout=300s
    fi
else
    kubectl apply -f k8s_local/db-init-job.yaml
    print_status "Waiting for database initialization to complete..."
    kubectl wait --for=condition=complete job/db-init-job -n tk --timeout=300s
fi

print_status "Database initialization completed successfully!"

# Section 7: Deploy OpenTelemetry Collector
print_status "Deploying OpenTelemetry Collector..."

# Check if OTel Collector already exists
if kubectl get deployment otelcol -n tk &> /dev/null; then
    print_warning "OpenTelemetry Collector already exists, skipping deployment"
else
    print_warning "using remote otel colector"
    # kubectl apply -f k8s_local/otel-collector.yaml
    # print_status "Waiting for OpenTelemetry Collector to be ready..."
    # kubectl wait --for=condition=ready pod -l app=otelcol -n tk --timeout=300s
fi

# print_status "OpenTelemetry Collector deployed successfully!"

# Section 8: Deploy Backend
print_status "Deploying backend application..."

# Check if backend already exists
if kubectl get deployment backend -n tk &> /dev/null; then
    print_warning "Backend deployment already exists, skipping deployment"
else
    kubectl apply -f k8s_local/backend-secret.yaml
    kubectl apply -f k8s_local/backend-deployment.yaml
    print_status "Waiting for backend to be ready..."
    kubectl wait --for=condition=ready pod -l app=backend -n tk --timeout=300s
fi

print_status "Backend deployed successfully!"

# Section 9: Deploy Frontend
print_status "Deploying frontend application..."

# Check if frontend already exists
if kubectl get deployment frontend -n tk &> /dev/null; then
    print_warning "Frontend deployment already exists, skipping deployment"
else
    kubectl apply -f k8s_local/frontend-deployment.yaml
    print_status "Waiting for frontend to be ready..."
    kubectl wait --for=condition=ready pod -l app=frontend -n tk --timeout=300s
fi

print_status "Frontend deployed successfully!"

# Section 10: Final Status
print_status "Deployment completed successfully!"
print_status "Checking final status..."

kubectl get pods -n tk

print_status "All services are deployed and ready!"
print_status "To access the application:"
print_status "  Frontend: kubectl port-forward -n tk svc/frontend-service 8080:80"
print_status "  Backend:  kubectl port-forward -n tk svc/backend-service 5000:5000"
print_status ""
# print_status "=== OPENTELEMETRY COLLECTOR ==="
# kubectl get pods -l app=otelcol -n tk
# print_status ""
# print_status "=== OPENTELEMETRY LOGS ==="
# print_status "To view OpenTelemetry Collector logs:"
# print_status "kubectl -n tk logs deploy/otelcol | tail -20"

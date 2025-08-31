#!/bin/bash

# MariaDB Deployment Script
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

# Section 3: Check Redis in default namespace
print_status "Checking Redis cluster in default namespace..."

# Check if Redis service exists
if ! kubectl get svc redis-master -n default &> /dev/null; then
    print_error "Redis service not found in default namespace. Please deploy Redis first."
    exit 1
fi

# Check if Redis pods are ready
if ! kubectl wait -n default --for=condition=ready pod -l app.kubernetes.io/name=redis --timeout=30s &> /dev/null; then
    print_error "Redis cluster is not ready. Please ensure Redis is running and ready."
    exit 1
fi

print_status "Redis cluster is ready!"

# Section 4: Check Kafka in default namespace
print_status "Checking Kafka cluster in default namespace..."

# Check if Kafka service exists
print_status "Checking Kafka service..."
if ! kubectl get svc team-kafka -n default &> /dev/null; then
    print_error "Kafka service not found in default namespace. Please deploy Kafka first."
    exit 1
fi
print_status "Kafka service found"

# Check if Kafka pods are ready
print_status "Checking Kafka pods..."
kubectl get pods -n default -l app.kubernetes.io/name=kafka
print_status "Running kubectl wait command..."
if ! kubectl wait -n default --for=condition=ready pod -l app.kubernetes.io/name=kafka --timeout=60s &> /dev/null; then
    print_error "Kafka cluster is not ready. Please ensure Kafka is running and ready."
    print_status "Debug: Checking pod status again..."
    kubectl get pods -n default -l app.kubernetes.io/name=kafka
    exit 1
fi

print_status "Kafka cluster is ready!"

# Section 5: Deploy MariaDB
print_status "Deploying basic MariaDB..."

# Check if MariaDB already exists
if kubectl get svc tk-mariadb -n tk &> /dev/null; then
    print_warning "MariaDB already exists, skipping deployment"
else
    helm install tk-mariadb bitnami/mariadb -f k8s/mariadb-values.yaml -n tk
    print_status "Waiting for MariaDB pods to be ready..."
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=tk-mariadb -n tk --timeout=300s
fi

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
    kubectl apply -f k8s/db-init-job.yaml
    print_status "Waiting for database initialization to complete..."
    kubectl wait --for=condition=complete job/db-init-job -n tk --timeout=300s
fi

print_status "Database initialization completed successfully!"

# Section 7: Deploy Backend
print_status "Deploying backend application..."

# Check if backend already exists
if kubectl get deployment backend -n tk &> /dev/null; then
    print_warning "Backend deployment already exists, skipping deployment"
else
    kubectl apply -f k8s/backend-secret.yaml
    kubectl apply -f k8s/backend-deployment.yaml
    print_status "Waiting for backend to be ready..."
    kubectl wait --for=condition=ready pod -l app=backend -n tk --timeout=300s
fi

print_status "Backend deployed successfully!"

# Section 8: Deploy Frontend
print_status "Deploying frontend application..."

# Check if frontend already exists
if kubectl get deployment frontend -n tk &> /dev/null; then
    print_warning "Frontend deployment already exists, skipping deployment"
else
    kubectl apply -f k8s/frontend-deployment.yaml
    print_status "Waiting for frontend to be ready..."
    kubectl wait --for=condition=ready pod -l app=frontend -n tk --timeout=300s
fi

print_status "Frontend deployed successfully!"

# Section 9: Final Status
print_status "Deployment completed successfully!"
print_status "Checking final status..."

kubectl get pods -n tk

print_status "All services are deployed and ready!"
print_status "To access the application:"
print_status "  Frontend: kubectl port-forward -n tk svc/frontend-service 8080:80"
print_status "  Backend:  kubectl port-forward -n tk svc/backend-service 5000:5000"

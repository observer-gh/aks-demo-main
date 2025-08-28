# Kafka Debugging Guide

## Quick Status

```bash
kubectl get pods -n default | grep team-kafka
kubectl get services -n default | grep team-kafka
```

## Health Checks

```bash
# Test connectivity
kubectl run test --image=curlimages/curl --rm -it --restart=Never -n default -- curl -v telnet://team-kafka.default.svc.cluster.local:9092

# Check process
kubectl exec team-kafka-broker-0 -n default -- pgrep -f kafka
```

## Resource Usage

```bash
kubectl top pods -n default | grep team-kafka
kubectl top nodes
```

## KRaft Mode (Modern Kafka)

```bash
# Check KRaft cluster status
kubectl logs team-kafka-controller-0 -n default | grep -i "kraft\|controller"

# Verify controller quorum (must be odd number: 1,3,5...)
kubectl get pods -n default | grep controller | wc -l
```

**KRaft Rules:**

- **Controllers**: Odd number only (3,5...)
- **Even numbers**: Won't form quorum
- **3+ controllers**: minimum

## Replica Configuration

```bash
# Check current replicas
kubectl get statefulset -n default | grep team-kafka

# Scale controllers (must be odd)
kubectl scale statefulset team-kafka-controller --replicas=3 -n default
```

## Common Issues

### Pods Pending

```bash
kubectl describe pod team-kafka-controller-0 -n default | grep -A 5 "Events:"
```

### Health Check Failures

```bash
kubectl describe pod team-kafka-broker-0 -n default | grep -A 5 "Events:"
```

### Logs

```bash
kubectl logs team-kafka-controller-0 -n default --tail=10
kubectl logs team-kafka-broker-0 -n default --tail=10
```

## Quick Test

```bash
kubectl run test --image=bitnami/kafka:4.0.0-debian-12-r10 --rm -it --restart=Never -n default -- bash -c "kafka-topics.sh --bootstrap-server team-kafka.default.svc.cluster.local:9092 --list"
```

## Healthy Metrics

- Pods: `1/1 Running`
- CPU: <50% allocated
- Memory: <80% allocated
- No ERROR/WARN in logs
- KRaft quorum formed
- Controllers: Odd number

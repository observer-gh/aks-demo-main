from flask import Flask, request, jsonify, session
from flask_cors import CORS
import redis
import mysql.connector
import json
from datetime import datetime
import os
from kafka import KafkaProducer, KafkaConsumer
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from threading import Thread
import logging
from random import randint

# OpenTelemetry imports
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

# ---- LOGS (manual) ----
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
# -----------------------

from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.mysql import MySQLInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_opentelemetry():
    # shared resource (ensure service.name set even if env not provided)
    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", "tk-backend")
    })

    # ---- Traces ----
    trace.set_tracer_provider(TracerProvider(resource=resource))
    trace.get_tracer_provider().add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter())  # uses env endpoints
    )

    # ---- Metrics ----
    metrics.set_meter_provider(MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
    ))

    # ---- Logs (manual, no agent) ----
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter())  # uses env endpoint
    )
    # inject trace_id/span_id into stdlib logs
    LoggingInstrumentor().instrument(set_logging_format=True)
    # bridge stdlib logging -> OTEL logs
    root = logging.getLogger()
    root.addHandler(LoggingHandler(level=logging.INFO,
                    logger_provider=logger_provider))

    # (optional) visibility
    logger.info("OTLP traces -> %s",
                os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
    logger.info("OTLP metrics -> %s",
                os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"))
    logger.info("OTLP logs   -> %s",
                os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"))


# Initialize OpenTelemetry
setup_opentelemetry()

# Setup instrumentation
FlaskInstrumentor().instrument_app(app)
MySQLInstrumentor().instrument()
RedisInstrumentor().instrument()

# Acquire tracer and meter
tracer = trace.get_tracer("tk-backend.tracer")
meter = metrics.get_meter("tk-backend.meter")

# Create counter instrument for API calls
api_counter = meter.create_counter(
    "api.calls",
    description="The number of API calls by endpoint and method",
)

# Create counter instrument for database operations
db_counter = meter.create_counter(
    "db.operations",
    description="The number of database operations by type",
)

# # 스레드 풀 생성
# thread_pool = ThreadPoolExecutor(max_workers=5)

# MariaDB 연결 함수


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('MYSQL_HOST', 'my-mariadb'),
        user=os.getenv('MYSQL_USER', 'testuser'),
        password=os.getenv('MYSQL_PASSWORD', '').strip(),
        database=os.getenv('MYSQL_DATABASE', 'my_database'),
        connect_timeout=15
    )

# Redis 연결 함수


def get_redis_connection():
    import time
    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'my-redis-master'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                password=os.getenv('REDIS_PASSWORD'),
                decode_responses=True,
                db=0,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True
            )
            # Test the connection
            redis_client.ping()
            return redis_client
        except Exception as e:
            if attempt < max_retries - 1:
                print(
                    f"Redis connection attempt {attempt + 1} failed: {str(e)}. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print(
                    f"Redis connection failed after {max_retries} attempts: {str(e)}")
                raise e

# Kafka Producer 설정


def get_kafka_producer():
    return KafkaProducer(
        bootstrap_servers=os.getenv('KAFKA_SERVERS', 'my-kafka:9092'),
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        security_protocol='PLAINTEXT'
    )

# 로깅 함수


def log_to_redis(action, details):
    try:
        redis_client = get_redis_connection()
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details
        }
        redis_client.lpush('api_logs', json.dumps(log_entry))
        redis_client.ltrim('api_logs', 0, 99)  # 최근 100개 로그만 유지
        redis_client.close()
    except Exception as e:
        print(f"Redis logging error: {str(e)}")

# API 통계 로깅을 비동기로 처리하는 함수


def async_log_api_stats(endpoint, method, status, user_id):
    def _log():
        try:
            producer = get_kafka_producer()
            log_data = {
                'timestamp': datetime.now().isoformat(),
                'endpoint': endpoint,
                'method': method,
                'status': status,
                'user_id': user_id,
                'message': f"{user_id}가 {method} {endpoint} 호출 ({status})"
            }
            producer.send('api-logs', log_data)
            producer.flush()
        except Exception as e:
            print(f"Kafka logging error: {str(e)}")

    # 새로운 스레드에서 로깅 실행
    Thread(target=_log).start()

    #  # 스레드 풀을 사용하여 작업 실행
    # thread_pool.submit(_log)

# 로그인 데코레이터


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"status": "error", "message": "로그인이 필요합니다"}), 401
        return f(*args, **kwargs)
    return decorated_function

# MariaDB 엔드포인트


@app.route('/db/message', methods=['POST'])
@login_required
def save_to_db():
    with tracer.start_as_current_span("save_message") as span:
        try:
            user_id = session['user_id']
            span.set_attribute("user.id", user_id)

            db = get_db_connection()
            data = request.json
            message = data['message']
            span.set_attribute("message.length", len(message))

            cursor = db.cursor()
            sql = "INSERT INTO messages (message, created_at) VALUES (%s, %s)"
            cursor.execute(sql, (message, datetime.now()))
            db.commit()
            cursor.close()
            db.close()

            # Increment counters
            api_counter.add(1, {"endpoint": "/db/message",
                            "method": "POST", "status": "success"})
            db_counter.add(1, {"operation": "insert", "table": "messages"})

            # 로깅
            log_to_redis('db_insert', f"Message saved: {message[:30]}...")
            logger.info(f"User {user_id} saved message: {message[:30]}...")

            async_log_api_stats('/db/message', 'POST', 'success', user_id)
            return jsonify({"status": "success"})
        except Exception as e:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))

            api_counter.add(1, {"endpoint": "/db/message",
                            "method": "POST", "status": "error"})
            logger.error(f"Error saving message: {str(e)}")

            async_log_api_stats('/db/message', 'POST', 'error', user_id)
            log_to_redis('db_insert_error', str(e))
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/db/messages', methods=['GET'])
@login_required
def get_from_db():
    try:
        user_id = session['user_id']
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM messages ORDER BY created_at DESC")
        messages = cursor.fetchall()
        cursor.close()
        db.close()

        # 비동기 로깅으로 변경
        async_log_api_stats('/db/messages', 'GET', 'success', user_id)

        return jsonify(messages)
    except Exception as e:
        if 'user_id' in session:
            async_log_api_stats('/db/messages', 'GET',
                                'error', session['user_id'])
        return jsonify({"status": "error", "message": str(e)}), 500

# Redis 로그 조회


@app.route('/logs/redis', methods=['GET'])
def get_redis_logs():
    with tracer.start_as_current_span("get_redis_logs") as span:
        try:
            redis_client = get_redis_connection()

            # optional: check key type & length
            key_type = redis_client.type('api_logs')
            span.set_attribute("redis.key_type", key_type)
            total = redis_client.llen('api_logs') or 0
            span.set_attribute("redis.len", total)

            # fetch most recent 100
            raw = redis_client.lrange('api_logs', -100, -1) or []

            out = []
            for item in raw:
                try:
                    out.append(json.loads(item))
                except Exception:
                    out.append({"raw": item})  # keep it, don’t crash

            # newest first
            out.reverse()

            return jsonify(out)
        except Exception as e:
            logger.exception("redis logs error")
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            return jsonify({"status": "error", "message": str(e)}), 500

# 회원가입 엔드포인트


@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return jsonify({"status": "error", "message": "사용자명과 비밀번호는 필수입니다"}), 400

        # 비밀번호 해시화
        hashed_password = generate_password_hash(password)

        db = get_db_connection()
        cursor = db.cursor()

        # 사용자명 중복 체크
        cursor.execute(
            "SELECT username FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return jsonify({"status": "error", "message": "이미 존재하는 사용자명입니다"}), 400

        # 사용자 정보 저장
        sql = "INSERT INTO users (username, password) VALUES (%s, %s)"
        cursor.execute(sql, (username, hashed_password))
        db.commit()
        cursor.close()
        db.close()

        return jsonify({"status": "success", "message": "회원가입이 완료되었습니다"})
    except Exception as e:
        print(f"Register error: {str(e)}")  # Add explicit logging
        return jsonify({"status": "error", "message": str(e)}), 500

# 로그인 엔드포인트


@app.route('/login', methods=['POST'])
def login():
    with tracer.start_as_current_span("login") as span:
        try:
            data = request.json
            username = data.get('username')
            password = data.get('password')

            span.set_attribute("user.username", username)

            if not username or not password:
                api_counter.add(
                    1, {"endpoint": "/login", "method": "POST", "status": "error"})
                return jsonify({"status": "error", "message": "사용자명과 비밀번호는 필수입니다"}), 400

            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            cursor.close()
            db.close()

            db_counter.add(1, {"operation": "select", "table": "users"})

            if user and check_password_hash(user['password'], password):
                session['user_id'] = username

                # Redis 세션 저장
                try:
                    redis_client = get_redis_connection()
                    session_data = {
                        'user_id': username,
                        'login_time': datetime.now().isoformat()
                    }
                    redis_client.set(
                        f"session:{username}", json.dumps(session_data))
                    redis_client.expire(f"session:{username}", 3600)
                except Exception as redis_error:
                    logger.warning(f"Redis session error: {str(redis_error)}")

                api_counter.add(
                    1, {"endpoint": "/login", "method": "POST", "status": "success"})
                logger.info(f"User {username} logged in successfully")

                return jsonify({
                    "status": "success",
                    "message": "로그인 성공",
                    "username": username
                })

            api_counter.add(
                1, {"endpoint": "/login", "method": "POST", "status": "error"})
            logger.warning(f"Failed login attempt for user: {username}")
            return jsonify({"status": "error", "message": "잘못된 인증 정보"}), 401

        except Exception as e:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))

            api_counter.add(
                1, {"endpoint": "/login", "method": "POST", "status": "error"})
            logger.error(f"Login error: {str(e)}")
            return jsonify({"status": "error", "message": "로그인 처리 중 오류가 발생했습니다"}), 500

# 로그아웃 엔드포인트


@app.route('/logout', methods=['POST'])
def logout():
    try:
        if 'user_id' in session:
            username = session['user_id']
            redis_client = get_redis_connection()
            redis_client.delete(f"session:{username}")
            session.pop('user_id', None)
        return jsonify({"status": "success", "message": "로그아웃 성공"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# 메시지 검색 (DB에서 검색)


@app.route('/db/messages/search', methods=['GET'])
@login_required
def search_messages():
    try:
        query = request.args.get('q', '')
        user_id = session['user_id']

        # DB에서 검색
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        sql = "SELECT * FROM messages WHERE message LIKE %s ORDER BY created_at DESC"
        cursor.execute(sql, (f"%{query}%",))
        results = cursor.fetchall()
        cursor.close()
        db.close()

        # 검색 이력을 Kafka에 저장
        async_log_api_stats('/db/messages/search', 'GET', 'success', user_id)

        return jsonify(results)
    except Exception as e:
        if 'user_id' in session:
            async_log_api_stats('/db/messages/search', 'GET',
                                'error', session['user_id'])
        return jsonify({"status": "error", "message": str(e)}), 500

# Kafka 로그 조회 엔드포인트


@app.route('/logs/kafka', methods=['GET'])
@login_required
def get_kafka_logs():
    try:
        consumer = KafkaConsumer(
            'api-logs',
            bootstrap_servers=os.getenv('KAFKA_SERVERS', 'my-kafka:9092'),
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            security_protocol='PLAINTEXT',
            group_id='api-logs-viewer',
            auto_offset_reset='earliest',
            consumer_timeout_ms=5000
        )

        logs = []
        try:
            for message in consumer:
                logs.append({
                    'timestamp': message.value['timestamp'],
                    'endpoint': message.value['endpoint'],
                    'method': message.value['method'],
                    'status': message.value['status'],
                    'user_id': message.value['user_id'],
                    'message': message.value['message']
                })
                if len(logs) >= 100:
                    break
        finally:
            consumer.close()

        # 시간 역순으로 정렬
        logs.sort(key=lambda x: x['timestamp'], reverse=True)
        return jsonify(logs)
    except Exception as e:
        print(f"Kafka log retrieval error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

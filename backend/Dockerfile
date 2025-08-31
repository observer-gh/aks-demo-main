FROM python:3.12-slim-bookworm


RUN apt-get update && apt-get -y dist-upgrade && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt

# 먼저 전체 소스 코드 복사
COPY . .

# Secret Key 생성 및 환경변수 설정
RUN python generate_secret.py > /app/.env
RUN echo "FLASK_SECRET_KEY=$(cat /app/.env)" > /app/.env

EXPOSE 5000
CMD ["python", "app.py"] 
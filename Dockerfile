FROM python:3.11-alpine

RUN apk add --no-cache bash gcc musl-dev libffi-dev
RUN apk add curl proot --repository=http://dl-cdn.alpinelinux.org/alpine/edge/community

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY static/ static/

RUN mkdir -p shared containers rootfs

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

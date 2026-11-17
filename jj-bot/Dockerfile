FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jjbot ./jjbot
ENV DB_PATH=/data/jjbot.sqlite3
VOLUME /data
CMD ["python", "-m", "jjbot"]

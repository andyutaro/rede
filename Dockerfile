FROM python:3.12-slim
WORKDIR /app
COPY scribe_relay.py scribe_live.py scribe_watch.html ./
EXPOSE 8080
CMD ["python", "scribe_relay.py"]

FROM metacubex/mihomo:v1.19.32 AS core
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 MIHOMO_BIN=/usr/local/bin/mihomo PROXY_MANAGER_HOST=0.0.0.0 PROXY_MANAGER_DATA=/app/data
WORKDIR /app
COPY --from=core /mihomo /usr/local/bin/mihomo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home route
COPY proxy_manager ./proxy_manager
COPY static ./static
COPY run.py .
COPY THIRD_PARTY_NOTICES.md .
COPY third_party ./third_party
RUN mkdir -p /app/data && chown -R route:route /app
USER route
EXPOSE 8787 7891
CMD ["python", "run.py"]

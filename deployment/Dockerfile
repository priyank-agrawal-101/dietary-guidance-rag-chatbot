# Optional persistent Linux host; Community Cloud uses requirements.txt directly.
FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 ANONYMIZED_TELEMETRY=False TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
WORKDIR /app
COPY requirements.txt /app/
COPY requirements/linux-streamlit.lock /app/requirements/linux-streamlit.lock
RUN python -m pip install --no-cache-dir -r requirements.txt && python -m pip check
COPY config /app/config
COPY deployment/artifacts /app/deployment/artifacts
COPY src /app/src
COPY streamlit_app.py /app/
COPY .streamlit/config.toml /app/.streamlit/config.toml
RUN useradd --create-home --uid 10001 app && mkdir /state && chown app:app /state
USER app
ENV DIETARY_STREAMLIT_CACHE_DIR=/state/runtime DIETARY_STREAMLIT_USAGE_DIR=/state/accounting
VOLUME ["/state"]
EXPOSE 8501
HEALTHCHECK --interval=30s --start-period=300s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5)"
CMD ["python", "-m", "streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]

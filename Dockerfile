FROM python:3.11-slim

RUN pip install uv

WORKDIR /app

COPY pyproject.toml ./
COPY *.py ./

RUN uv sync --no-dev

RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["uv", "run", "main.py"]
FROM python:3.12-slim

WORKDIR /app

# 依存関係を先にコピーしてレイヤーキャッシュを最大活用
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードをコピー
COPY . .

# 非rootユーザーでセキュアに実行
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run は $PORT 環境変数でポートを渡す（デフォルト 8080）
EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py \
  --server.port=${PORT:-8080} \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false"]

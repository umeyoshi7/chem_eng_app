FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py engine.py solvents.py ui_lle.py ui_vp.py ui_vle.py ui_conc.py ui_logic.py ./

EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true"]

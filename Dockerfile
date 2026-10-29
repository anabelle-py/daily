FROM python:3.9

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY . .

EXPOSE 9000

CMD ["uvicorn","api:app","--host","0.0.0.0","--port","9000"]

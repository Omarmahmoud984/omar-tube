FROM python:3.11-slim

# Install ffmpeg (needed for merging video+audio and audio conversion)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create downloads directory
RUN mkdir -p /app/downloads

EXPOSE 5000

CMD ["python", "app.py"]

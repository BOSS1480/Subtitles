# השתמש בתמונה בסיסית של פייתון
FROM python:3.11-slim-buster

# הגדר את משתני הסביבה
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PORT 8000  # הגדר את הפורט ל-8000

# התקן תלויות מערכת כולל ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# העתק את קבצי הפרויקט לסביבת העבודה בתוך הדוקר
WORKDIR /app
COPY . /app

# התקן את התלויות של פייתון
RUN pip install --no-cache-dir -r requirements.txt

# חשוף את הפורט
EXPOSE 8000

# הגדר בדיקת בריאות עבור Koyeb
HEALTHCHECK --test=nc -z 0.0.0.0:$PORT --timeout=10s

# הפעל את היישום
CMD ["python", "app.py"]


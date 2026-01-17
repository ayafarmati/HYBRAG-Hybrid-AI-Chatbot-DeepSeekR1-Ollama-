import os
from dotenv import load_dotenv

load_dotenv()

# Database
DATABASE_URL = os.getenv("DATABASE_URL")

# Embeddings (tu peux garder Ollama embeddings pour l’instant)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")

# OpenRouter / DeepSeek (chat)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-r1-0528:free")
YOUR_SITE_URL = os.getenv("YOUR_SITE_URL", "http://localhost:8000")
YOUR_SITE_NAME = os.getenv("YOUR_SITE_NAME", "Student Chatbot")

# Chroma
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "ensa_chatbot")
DATABASE_LOCATION = os.getenv("DATABASE_LOCATION", "./chroma_db")

# Auth
SECRET_KEY = os.getenv("SECRET_KEY", "ayaayaaya")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 150  

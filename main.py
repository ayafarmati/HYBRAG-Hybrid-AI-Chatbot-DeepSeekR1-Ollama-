import os
import shutil
from typing import Optional, List
from datetime import datetime, timedelta

from fastapi import (
    FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, status, Query
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlmodel import Session, select

from database import init_db, get_session
# MODIFICATION ICI : On a retiré RefreshToken de l'import
from models import User, Message, Conversation 

from utils import (
    create_conversation,
    list_conversations,
    get_conversation,
    get_history_texts,
)

from rag import ingest_pdf, rag_answer
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

app = FastAPI(title="Chatbot Étudiant ENSA - Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En production, remplace * par l'URL de ton frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# =========================
# Schemas
# =========================

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserLogin(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str


class MessageOut(BaseModel):
    id: int
    sender: str
    content: str
    created_at: datetime


class ConversationCreate(BaseModel):
    title: Optional[str] = None


# =========================
# Auth helpers
# =========================

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_user_by_email(session: Session, email: str) -> Optional[User]:
    statement = select(User).where(User.email == email)
    return session.exec(statement).first()

def authenticate_user(session: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(session, email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user

def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalide ou expiré",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id = int(sub)
    except (JWTError, ValueError):
        raise credentials_exception

    user = session.get(User, user_id)
    if user is None:
        raise credentials_exception
    return user


# =========================
# Auth routes
# =========================

@app.post("/auth/register", response_model=Token)
def register(user_in: UserCreate, session: Session = Depends(get_session)):
    existing = get_user_by_email(session, user_in.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email déjà enregistré")

    user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        password_hash=get_password_hash(user_in.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    access_token = create_access_token({"sub": str(user.id)})
    return Token(access_token=access_token)


@app.post("/auth/login", response_model=Token)
def login(user_in: UserLogin, session: Session = Depends(get_session)):
    user = authenticate_user(session, user_in.email, user_in.password)
    if not user:
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")

    access_token = create_access_token({"sub": str(user.id)})
    return Token(access_token=access_token)


# =========================
# Upload / ingestion
# =========================

@app.post("/ingest-pdf")
async def ingest_pdf_endpoint(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user) # Sécurité ajoutée : il faut être connecté
):
    temp_dir = "uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)

    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    nb_chunks = ingest_pdf(temp_path, source_name=file.filename)
    return {"status": "ok", "file": file.filename, "chunks": nb_chunks}


# =========================
# Conversations API
# =========================

@app.get("/conversations")
def api_list_conversations(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    convs = list_conversations(session, current_user.id)
    return [{"id": c.id, "title": c.title} for c in convs]


@app.post("/conversations")
def api_create_conversation(
    payload: ConversationCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    title = (payload.title or "").strip() or "Nouvelle conversation"
    conv = create_conversation(session, current_user.id, title=title)
    return {"id": conv.id, "title": conv.title}


@app.get("/conversations/{conversation_id}/messages", response_model=List[MessageOut])
def api_get_conversation_messages(
    conversation_id: int,
    limit: int = 200,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    conv = get_conversation(session, current_user.id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable")

    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    messages = session.exec(statement).all()

    return [
        MessageOut(
            id=m.id,
            sender=m.sender,
            content=m.content,
            created_at=m.created_at,
        )
        for m in messages
    ]


@app.delete("/conversations/{conversation_id}")
def api_delete_conversation(
    conversation_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    conv = get_conversation(session, current_user.id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable")

    # Supprimer messages liés
    msgs = session.exec(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all()
    for m in msgs:
        session.delete(m)

    # Supprimer conversation
    session.delete(conv)
    session.commit()

    return {"ok": True}


# =========================
# WebSocket (conversation_id)
# =========================

@app.websocket("/ws/chat")
async def chat_websocket(
    websocket: WebSocket,
    token: str = Query(...),
    conversation_id: int = Query(...),
    session: Session = Depends(get_session),
):
    # decode token manually (WebSocket doesn't support Depends for OAuth2)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            await websocket.close(code=1008)
            return
        user_id = int(sub)
    except Exception:
        await websocket.close(code=1008)
        return

    user = session.get(User, user_id)
    if user is None:
        await websocket.close(code=1008)
        return

    # validate conversation belongs to user
    conversation = session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        while True:
            question = await websocket.receive_text()

            # Save User Message
            user_msg = Message(
                conversation_id=conversation.id,
                sender="user",
                content=question,
            )
            session.add(user_msg)
            session.commit()

            # RAG
            history_texts = get_history_texts(session, conversation.id)

            full_answer = ""
            async for chunk in rag_answer(question=question, history=history_texts):
                full_answer += chunk
                await websocket.send_text(chunk)

            # Save Bot Message
            bot_msg = Message(
                conversation_id=conversation.id,
                sender="assistant",
                content=full_answer,
            )
            session.add(bot_msg)
            session.commit()

    except WebSocketDisconnect:
        print(f"User {user_id} disconnected")
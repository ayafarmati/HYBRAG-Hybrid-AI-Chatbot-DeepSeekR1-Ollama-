from typing import List, Optional
from sqlmodel import Session, select

from models import Conversation, Message

def create_conversation(session: Session, user_id: int, title: str = "Nouvelle conversation") -> Conversation:
    conv = Conversation(user_id=user_id, title=title)
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv

def list_conversations(session: Session, user_id: int) -> List[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.id.desc())
    )
    return session.exec(statement).all()

def get_conversation(session: Session, user_id: int, conversation_id: int) -> Optional[Conversation]:
    conv = session.get(Conversation, conversation_id)
    if not conv or conv.user_id != user_id:
        return None
    return conv

def get_history_texts(session: Session, conversation_id: int, limit: int = 200) -> List[str]:
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    messages = session.exec(statement).all()
    return [f"{m.sender}: {m.content}" for m in messages]

import os
import re
from typing import AsyncGenerator, List, Tuple, Optional

from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    PyPDFLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredPowerPointLoader,
)
from langchain_core.documents import Document

from openai import AsyncOpenAI

from config import (
    EMBEDDING_MODEL,
    COLLECTION_NAME,
    DATABASE_LOCATION,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    YOUR_SITE_URL,
    YOUR_SITE_NAME,
)

###############################
# CLIENT OPENROUTER / DEEPSEEK
###############################

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

###############################
# INITIALISATION RAG
###############################

embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL)

vector_store = Chroma(
    collection_name=COLLECTION_NAME,
    embedding_function=embeddings,
    persist_directory=DATABASE_LOCATION,
)

###############################
# HELPERS (INTENTS)
###############################

_SMALLTALK = {
    "hi", "hello", "hey",
    "bonjour", "salut", "coucou",
    "merci", "thanks",
}

def _is_smalltalk(text: str) -> bool:
    return text.strip().lower() in _SMALLTALK

def _is_format_instruction(text: str) -> bool:
    """
    Instructions de forme uniquement (ex: "réponds en 2 lignes", "résume en 3 lignes").
    """
    t = text.strip().lower()
    patterns = [
        r"^ré?ponds?\s+en\s+\d+\s+lignes?$",
        r"^reponds?\s+en\s+\d+\s+lignes?$",
        r"^en\s+\d+\s+lignes?$",
        r"^résume\s+en\s+\d+\s+lignes?$",
        r"^resume\s+en\s+\d+\s+lignes?$",
    ]
    return any(re.match(p, t) for p in patterns)

def _extract_line_limit(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*lignes?", text.lower())
    return int(m.group(1)) if m else None

def _wants_sources(text: str) -> bool:
    """
    Détecte si l'utilisateur demande explicitement la source ou la traçabilité.
    """
    keywords = [
        "source", "référence", "reference", "fichier", "document", 
        "d'ou", "d'où", "prouve", "trace", "origine", "lien"
    ]
    t = text.strip().lower()
    return any(k in t for k in keywords)

###############################
# HELPERS (LLM CALLS)
###############################

async def _stream_llm(system_prompt: str, user_prompt: str, temperature: float) -> AsyncGenerator[str, None]:
    stream = await client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        stream=True,
        extra_headers={
            "HTTP-Referer": YOUR_SITE_URL,
            "X-Title": YOUR_SITE_NAME,
        },
    )

    async for event in stream:
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content

async def _fallback_chat(question: str, history: List[str]) -> AsyncGenerator[str, None]:
    """
    Réponse normale (style ChatGPT) quand pas de contexte pertinent.
    """
    system_prompt = (
        "Tu es un assistant qui aide les éléves ingénieurs . "
        "Réponds clairement, de façon utile et structurée."
    )
    history_text = "\n".join([f"- {msg}" for msg in history[-6:]])

    user_prompt = f"""
[HISTORIQUE RÉCENT]
{history_text}

[QUESTION]
{question}
""".strip()

    async for tok in _stream_llm(system_prompt, user_prompt, temperature=0.4):
        yield tok

###############################
# INGESTION DE FICHIERS
###############################

def ingest_pdf(file_path: str, source_name: str) -> int:
    ext = os.path.splitext(source_name)[1].lower()

    if ext == ".pdf":
        loader = PyPDFLoader(file_path)
    elif ext in [".doc", ".docx"]:
        loader = UnstructuredWordDocumentLoader(file_path)
    elif ext in [".ppt", ".pptx"]:
        loader = UnstructuredPowerPointLoader(file_path)
    else:
        raise ValueError(f"Extension de fichier non supportée: {ext}")

    docs = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    splits = splitter.split_documents(docs)

    for d in splits:
        d.metadata["source"] = source_name

    total_inserted = 0
    for idx, doc in enumerate(splits):
        try:
            vector_store.add_documents([doc])
            total_inserted += 1
            print(f"Chunk {idx+1}/{len(splits)} OK (total={total_inserted})")
        except Exception as e:
            print(f"❌ Erreur sur le chunk {idx+1}/{len(splits)}: {e}")

    print(f"Ingestion terminée : {total_inserted} chunks insérés sur {len(splits)}")
    return total_inserted

###############################
# RAG ANSWER (STREAMING)
###############################

async def rag_answer(
    question: str,
    history: List[str],
    k: int = 4,
) -> AsyncGenerator[str, None]:

    if not OPENROUTER_API_KEY:
        yield "❌ OPENROUTER_API_KEY manquant dans .env"
        return

    q = question.strip()

    # 0) Salutations -> réponse normale
    if _is_smalltalk(q):
        async for tok in _fallback_chat(q, history):
            yield tok
        return

    # 1) Instruction de forme
    if _is_format_instruction(q):
        n = _extract_line_limit(q) or 2
        yield f"D’accord ✅ Pose ta question, je répondrai en **{n} lignes**."
        return

    # 2) Retrieval avec score
    docs_scores: List[Tuple[Document, float]] = vector_store.similarity_search_with_score(q, k=k)

    # Vérification : si aucun doc ou score trop mauvais -> Mode "Naturel" sans docs
    SCORE_THRESHOLD = 1.2  #(Ollama Embeddings varient souvent entre 0.2 et 1.5)
    
    if not docs_scores:
        async for tok in _fallback_chat(q, history):
            yield tok
        return

    docs_scores_sorted = sorted(docs_scores, key=lambda x: x[1])
    best_score = docs_scores_sorted[0][1]

    if best_score > SCORE_THRESHOLD:
        # Le doc trouvé est trop éloigné -> on répond naturellement sans le RAG
        async for tok in _fallback_chat(q, history):
            yield tok
        return

    # 3) Construction du Contexte AVEC Métadonnées (Source)
    top_docs = [d for d, s in docs_scores_sorted[:k]]
    
    context_parts = []
    for doc in top_docs:
        source_name = doc.metadata.get("source", "Inconnu")
        # On injecte explicitement la source pour que le LLM puisse la lire
        context_parts.append(f"--- SOURCE: {source_name} ---\nCONTENU: {doc.page_content}")
    
    context_text = "\n\n".join(context_parts)
    history_text = "\n".join([f"- {msg}" for msg in history[-6:]])

    # 4) Détection de l'intention "Traçabilité"
    user_wants_sources = _wants_sources(q)

    # 5) Prompt Dynamique
    if user_wants_sources:
        # SCÉNARIO A : L'utilisateur VEUT la traçabilité
        system_prompt = (
            "Tu es un assistant précis basé sur des documents. "
            "Réponds à la question en utilisant UNIQUEMENT les informations du CONTEXTE DOCUMENTAIRE ci-dessous. "
            "À la fin de ta réponse, tu DOIS lister explicitement les fichiers sources utilisés sous la forme : "
            "'Sources : nom_du_fichier.pdf'. "
            "Si l'information n'est pas dans le contexte, dis simplement que tu ne trouves pas l'information dans les documents."
        )
    else:
        # SCÉNARIO B : Réponse Naturelle (Par défaut)
        system_prompt = (
            "Tu es un assistant pédagogique expert. "
            "Utilise les informations du CONTEXTE DOCUMENTAIRE pour construire ta réponse, "
            "MAIS réponds de manière totalement naturelle et fluide, comme un humain. "
            "Ne mentionne PAS 'selon le document' ou 'dans le contexte'. "
            "Ne cite PAS les noms de fichiers ou les sources. Donne juste la réponse directe. "
            "Si l'information n'est pas dans le contexte, réponds avec tes connaissances générales."
        )

    user_prompt = f"""
[HISTORIQUE]
{history_text}

[CONTEXTE DOCUMENTAIRE]
{context_text}

[QUESTION]
{q}
""".strip()

    # 6) Envoi au LLM
    async for tok in _stream_llm(system_prompt, user_prompt, temperature=0.2):
        yield tok
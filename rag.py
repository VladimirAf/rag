from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
import config
from typing import List, Optional, Dict
from pydantic import BaseModel
import os
from contextlib import contextmanager
import fcntl


# Используем OPENAI_API_KEY или LLM_API_KEY для embeddings
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
embeddings = OpenAIEmbeddings(
    model=config.OPENAI_EMBEDDINGS_NAME,
    api_key=api_key
)
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.SPLITTER_CHUNK_SIZE,
    chunk_overlap=config.SPLITTER_CHUNK_OVERLAP,
    length_function=len,
    add_start_index=False
)

def get_vector_store(collection_name: str):
    return Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=config.CHROMA_PATH
    )

@contextmanager
def chroma_lock(name: str):
    """
    Межпроцессная блокировка для операций с ChromaDB persistent storage.
    Важно при запуске uvicorn с несколькими workers (несколько процессов пишут в один CHROMA_PATH).
    """
    lock_path = config.DATA_PATH / f"chroma_{name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def delete_files_collection():
    """
    Удаляет коллекцию 'files' в ChromaDB напрямую (без использования LangChain store).
    Вызывать при повреждении коллекции; после этого следующий доступ к store создаст пустую коллекцию.
    """
    import chromadb
    with chroma_lock("files"):
        path = str(config.CHROMA_PATH)
        client = chromadb.PersistentClient(path=path)
        try:
            client.delete_collection("files")
        except Exception:
            # коллекции может не быть — это ок
            pass


def fix_metadata_value_types(
        data: dict
):
    keys = list(data.keys())
    for key in keys:
        value = data[key]
        if isinstance(value, (list, tuple)):
            values = list(map(str, value))
            value = ", ".join(values)
        elif not isinstance(value, (int, str, float, bool)) and value is not None:
            value = str(value)
        data[key] = value
    return data
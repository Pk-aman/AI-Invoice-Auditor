# ================================================================
# AI INVOICE AUDITOR - RAG MODULE: SHARED SETUP
# LLM, embeddings, Qdrant, PostgreSQL and Redis used by every RAG node.
# ================================================================

# The values come from the project's settings (agent-service/.env), and the
# Qdrant / Redis / PostgreSQL clients are the ones the agent already created
# at startup. init() is called once by RagService.

from langchain_litellm import ChatLiteLLM, LiteLLMEmbeddings

from app.core.config import Settings

# ================================================================
# STEP 1: SETTINGS
# ================================================================

settings: Settings = None

COLLECTION_NAME = "invoices"

RETRIEVAL_K = 10

FINAL_CONTEXT_K = 4

MAX_RETRIES = 1

# ================================================================
# STEP 2-4: LLM, EMBEDDINGS, QDRANT, POSTGRESQL, REDIS
# ================================================================

llm = None

embeddings = None

qdrant_client = None

redis_client = None

_pool = None


def init(ctx):

    global settings, COLLECTION_NAME, RETRIEVAL_K, FINAL_CONTEXT_K, MAX_RETRIES
    global llm, embeddings, qdrant_client, redis_client, _pool

    settings = ctx.settings

    COLLECTION_NAME = settings.qdrant_collection

    RETRIEVAL_K = settings.rag_top_k

    FINAL_CONTEXT_K = settings.rag_final_context_k

    MAX_RETRIES = settings.rag_max_retries

    # Generation and reasoning LLM through LiteLLM.
    # The model is LLM_GENERATION_MODEL from agent-service/.env
    # (Bedrock Nova Lite, or an Ollama model for local development).

    llm = ChatLiteLLM(
        model=settings.llm_generation_model,
        temperature=settings.llm_temperature,
        request_timeout=settings.llm_timeout_s,
        api_base=_api_base(settings.llm_generation_model),
        # Some providers (Ollama) reject parameters such as `n` that RAGAS sends.
        model_kwargs={"drop_params": True}
    )

    # Same embedding model must be used for indexing and retrieval.
    # The indexing agent embeds with EMBEDDING_MODEL through LiteLLM too.

    embeddings = LiteLLMEmbeddings(
        model=settings.embedding_model,
        api_base=_api_base(settings.embedding_model),
        request_timeout=settings.llm_timeout_s
    )

    # Clients created at startup (same URLs, timeouts and health checks).

    qdrant_client = ctx.qdrant

    redis_client = ctx.redis

    _pool = ctx.pool


def _api_base(model):

    if model.startswith("ollama"):
        return settings.ollama_base_url

    return None


def get_db_connection():

    # Connection from the agent's pool (role agent_rw, rows as dicts).

    return _pool.connection()

"""Real per-store ``Vector_Transport`` clients for GW30, one module per store.

Each transport is injected behind the ``Vector_Transport`` protocol
(``dispatch/vector_connectors.py``) by a deployment or the L30 live-gate harness; nothing on the
production import path imports this package yet (GW31 wires it). Store SDKs are imported lazily
inside the transports, so importing the package needs none of them.

Every transport maps a connection or store failure to ``VectorTransportError`` (so
``run_bounded_operation`` answers ``provider_unreachable``, never an empty success), and
``create_collection`` never deletes or resets an existing collection: it answers the declared
``collection_exists`` (R2.9).
"""

from gateway_v2.dispatch.vector_transports._common import connection_params_repr
from gateway_v2.dispatch.vector_transports.chroma_transport import (
    ChromaConnectionParams,
    ChromaTransport,
)
from gateway_v2.dispatch.vector_transports.elastic_transport import (
    ElasticsearchConnectionParams,
    ElasticsearchTransport,
    OpenSearchConnectionParams,
    OpenSearchTransport,
)
from gateway_v2.dispatch.vector_transports.mongo_transport import (
    MongoAtlasConnectionParams,
    MongoAtlasTransport,
)
from gateway_v2.dispatch.vector_transports.pgvector_transport import (
    PgVectorConnectionParams,
    PgVectorTransport,
)
from gateway_v2.dispatch.vector_transports.qdrant_transport import (
    QdrantConnectionParams,
    QdrantTransport,
)
from gateway_v2.dispatch.vector_transports.redis_transport import (
    RedisConnectionParams,
    RedisVectorTransport,
)
from gateway_v2.dispatch.vector_transports.weaviate_transport import (
    WeaviateConnectionParams,
    WeaviateTransport,
)

__all__ = (
    "ChromaConnectionParams",
    "ChromaTransport",
    "ElasticsearchConnectionParams",
    "ElasticsearchTransport",
    "MongoAtlasConnectionParams",
    "MongoAtlasTransport",
    "OpenSearchConnectionParams",
    "OpenSearchTransport",
    "PgVectorConnectionParams",
    "PgVectorTransport",
    "QdrantConnectionParams",
    "QdrantTransport",
    "RedisConnectionParams",
    "RedisVectorTransport",
    "WeaviateConnectionParams",
    "WeaviateTransport",
    "connection_params_repr",
)

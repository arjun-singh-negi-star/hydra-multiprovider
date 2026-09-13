from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SubmitQueryRequest(_message.Message):
    __slots__ = ("query", "session_id", "user_id", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    QUERY_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    query: str
    session_id: str
    user_id: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, query: _Optional[str] = ..., session_id: _Optional[str] = ..., user_id: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class SubmitQueryResponse(_message.Message):
    __slots__ = ("request_id", "token", "final", "error", "escalating")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ESCALATING_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    token: str
    final: QueryMetadata
    error: QueryError
    escalating: EscalationNotice
    def __init__(self, request_id: _Optional[str] = ..., token: _Optional[str] = ..., final: _Optional[_Union[QueryMetadata, _Mapping]] = ..., error: _Optional[_Union[QueryError, _Mapping]] = ..., escalating: _Optional[_Union[EscalationNotice, _Mapping]] = ...) -> None: ...

class EscalationNotice(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class QueryMetadata(_message.Message):
    __slots__ = ("agent_name", "model_used", "cached", "escalated", "latency_ms", "route_confidence", "route_method")
    AGENT_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_USED_FIELD_NUMBER: _ClassVar[int]
    CACHED_FIELD_NUMBER: _ClassVar[int]
    ESCALATED_FIELD_NUMBER: _ClassVar[int]
    LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    ROUTE_CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    ROUTE_METHOD_FIELD_NUMBER: _ClassVar[int]
    agent_name: str
    model_used: str
    cached: bool
    escalated: bool
    latency_ms: float
    route_confidence: float
    route_method: str
    def __init__(self, agent_name: _Optional[str] = ..., model_used: _Optional[str] = ..., cached: _Optional[bool] = ..., escalated: _Optional[bool] = ..., latency_ms: _Optional[float] = ..., route_confidence: _Optional[float] = ..., route_method: _Optional[str] = ...) -> None: ...

class QueryError(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("status", "dependencies")
    class DependenciesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: bool
        def __init__(self, key: _Optional[str] = ..., value: _Optional[bool] = ...) -> None: ...
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DEPENDENCIES_FIELD_NUMBER: _ClassVar[int]
    status: str
    dependencies: _containers.ScalarMap[str, bool]
    def __init__(self, status: _Optional[str] = ..., dependencies: _Optional[_Mapping[str, bool]] = ...) -> None: ...

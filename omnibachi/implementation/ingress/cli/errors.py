# ingress/errors.py

class TransportError(Exception):
    """Base class for transport-layer failures."""


class ProtocolLoadError(TransportError):
    pass


class DagBuildError(TransportError):
    pass


class ExecutionError(TransportError):
    pass

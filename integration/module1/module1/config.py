from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 5000
    chunk_size: int = 64 * 1024
    max_file_size: int = 100 * 1024 * 1024
    max_json_line: int = 1024 * 1024
    socket_timeout: float = 30.0

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host must not be empty")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.chunk_size <= 0 or self.max_file_size < 0:
            raise ValueError("chunk_size and max_file_size must be non-negative")
        if self.max_json_line <= 0 or self.socket_timeout <= 0:
            raise ValueError("max_json_line and socket_timeout must be positive")

"""One model and one active inference session per server process."""
import threading
from time import perf_counter

from .providers import create_provider
from .service import STTService


class ModelRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.provider = None
        self.config = None

    def create_service(self, config):
        """Caller MUST hold lock until the entire session has finished."""
        started = perf_counter()
        hit = config.provider == "faster-whisper" and self.config == config
        if not hit:
            self.provider = None
            self.config = None
            provider = create_provider(config)
            if config.provider == "faster-whisper":
                # Warm up before caching: failures must remain retryable.
                from .audio import Audio
                provider.transcribe(Audio(bytes(32000)))
                self.provider, self.config = provider, config
        else:
            provider = self.provider
        service = STTService(config, provider=provider)
        service.initialization_ms = (perf_counter() - started) * 1000
        return service, hit


runtime = ModelRuntime()

"""Background key-capture and Explorer-autosize workers."""

from fenestra.workers.key_capture import KeyCaptureSession

try:
    from fenestra.workers.key_capture import KeyCaptureWorker
except ImportError:
    pass

try:
    from fenestra.workers.explorer import ExplorerAutosizeWorker
except ImportError:
    pass

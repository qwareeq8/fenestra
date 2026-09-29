def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requires_qt: marks tests requiring a real PySide6/Qt runtime"
    )

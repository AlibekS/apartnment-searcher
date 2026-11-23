"""Модули площадок. Чтобы добавить новую площадку:
1. создайте файл scraper/sources/<name>.py с классом-наследником BaseSource;
2. добавьте его в словарь SOURCES ниже;
3. добавьте секцию "<name>" в config/sources.json.
"""
from .krisha import KrishaSource
from .olx import OlxSource

SOURCES = {
    "krisha": KrishaSource,
    "olx": OlxSource,
}

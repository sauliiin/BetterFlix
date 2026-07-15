# -*- coding: utf-8 -*-
import os
import sqlite3
import threading
import xbmcvfs


class FineTuning:
    """Parâmetros de banco e armazenamento local."""
    CACHE_BASE_PATH = "special://profile/addon_data/script.showimdb/"
    CACHE_DIR_NAME = "cache"
    DB_FILENAME = "addon_cache.db"
    SQLITE_TIMEOUT = 30
    SQLITE_JOURNAL_MODE = "WAL"
    SQLITE_SYNCHRONOUS = "NORMAL"
    SQLITE_TEMP_STORE = "MEMORY"


CACHE_DIR = os.path.join(
    xbmcvfs.translatePath(FineTuning.CACHE_BASE_PATH),
    FineTuning.CACHE_DIR_NAME,
)
if not xbmcvfs.exists(CACHE_DIR):
    xbmcvfs.mkdirs(CACHE_DIR)
DB_FILE = os.path.join(CACHE_DIR, FineTuning.DB_FILENAME)


class DatabaseManager:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(DatabaseManager, cls).__new__(cls)
                    cls._instance._init_connection()
        return cls._instance

    def _init_connection(self):
        """Inicializa a conexão única com thread safety."""
        self.conn_lock = threading.RLock()
        self.conn = sqlite3.connect(DB_FILE, timeout=FineTuning.SQLITE_TIMEOUT, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=%s" % FineTuning.SQLITE_JOURNAL_MODE)
        self.conn.execute("PRAGMA synchronous=%s" % FineTuning.SQLITE_SYNCHRONOUS)
        self.conn.execute("PRAGMA temp_store=%s" % FineTuning.SQLITE_TEMP_STORE)
        self._create_tables()

    def _create_tables(self):
        """Cria todas as tabelas usadas pelos módulos."""
        with self.conn_lock:
            cursor = self.conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS trakt_reviews (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tmdb_ids (cache_key TEXT PRIMARY KEY, imdb_id TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tmdb_trailers (cache_key TEXT PRIMARY KEY, trailer_url TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tmdb_keyword_badges (cache_key TEXT PRIMARY KEY, badges TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS imdb_trailer_ids (cache_key TEXT PRIMARY KEY, video_id TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS imdb_trailer_urls (cache_key TEXT PRIMARY KEY, playback_url TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS mdlist_data (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS badges_data (imdb_id TEXT PRIMARY KEY, oscars TEXT, badges TEXT, timestamp REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS middle_cache (imdb_id TEXT PRIMARY KEY, middle_rating TEXT, timestamp REAL)")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS youtube_streams (video_id TEXT PRIMARY KEY, url TEXT, info TEXT, timestamp REAL)"
            )
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS youtube_search_cache (query TEXT PRIMARY KEY, plugin_url TEXT, timestamp REAL)"
            )
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS ui_bundle_cache (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)"
            )
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS recommendations (cache_key TEXT PRIMARY KEY, data TEXT, timestamp REAL)"
            )
            self.conn.commit()

    def execute_query(self, query, params=()):
        """Executa uma query de escrita (INSERT, UPDATE, DELETE)."""
        with self.conn_lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(query, params)
                self.conn.commit()
            except Exception:
                pass

    def fetch_one(self, query, params=()):
        """Busca um único resultado (SELECT)."""
        with self.conn_lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchone()
            except Exception:
                return None

    def fetch_all(self, query, params=()):
        """Busca todos os resultados."""
        with self.conn_lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
            except Exception:
                return []

    def close(self):
        """Fecha a conexão (geralmente não necessário até o shutdown do Kodi)."""
        try:
            self.conn.close()
        except Exception:
            pass


db = DatabaseManager()

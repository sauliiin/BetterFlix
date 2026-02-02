# -*- coding: utf-8 -*-
# ARQUIVO: database.py
import sqlite3
import threading
import os
import xbmcvfs
import time

# Configurações Globais
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
if not xbmcvfs.exists(CACHE_DIR):
    xbmcvfs.mkdirs(CACHE_DIR)
DB_FILE = os.path.join(CACHE_DIR, 'addon_cache.db')

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
        # check_same_thread=False permite que múltiplas threads usem a mesma conexão
        # desde que protejamos com Lock (o que faremos).
        self.conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
        
        # Otimizações globais
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        
        self._create_tables()

    def _create_tables(self):
        """Cria todas as tabelas usadas pelos módulos."""
        with self.conn_lock:
            cursor = self.conn.cursor()
            
            # Tabela Trakt (Reviews)
            cursor.execute('CREATE TABLE IF NOT EXISTS trakt_reviews (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)')
            
            # Tabelas TMDb (IDs e Trailers)
            cursor.execute('CREATE TABLE IF NOT EXISTS tmdb_ids (cache_key TEXT PRIMARY KEY, imdb_id TEXT, timestamp REAL)')
            cursor.execute('CREATE TABLE IF NOT EXISTS tmdb_trailers (cache_key TEXT PRIMARY KEY, trailer_url TEXT, timestamp REAL)')
            
            # Tabela MDbList (Ratings)
            cursor.execute('CREATE TABLE IF NOT EXISTS mdlist_data (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)')
            
            self.conn.commit()

    def execute_query(self, query, params=()):
        """Executa uma query de escrita (INSERT, UPDATE, DELETE)."""
        with self.conn_lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(query, params)
                self.conn.commit()
            except Exception as e:
                # Log opcional aqui
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
        except:
            pass

# Instância global para ser importada
db = DatabaseManager()
"""
Módulo de Google Drive - Lectura de PDFs
=========================================
Este módulo maneja la conexión con Google Drive y extracción de texto de PDFs.
Lee TODOS los PDFs de la carpeta de forma dinámica.
"""

import os
import io
import json
import time
from typing import Dict, List, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PyPDF2 import PdfReader

from config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OAUTH_REDIRECT_URI,
    TOKEN_FILE,
    GOOGLE_DRIVE_FOLDER_ID,
    CACHE_FOLDER,
    CACHE_REFRESH_INTERVAL
)

SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets'
]


def get_authorization_url() -> str:
    """Genera la URL para que el usuario autorice la aplicación."""
    from urllib.parse import urlencode
    
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': OAUTH_REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'consent'
    }
    
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code_for_tokens(authorization_code: str) -> dict:
    """Intercambia el código de autorización por tokens de acceso."""
    import requests
    
    token_url = "https://oauth2.googleapis.com/token"
    
    data = {
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'code': authorization_code,
        'grant_type': 'authorization_code',
        'redirect_uri': OAUTH_REDIRECT_URI
    }
    
    response = requests.post(token_url, data=data)
    tokens = response.json()
    
    if 'access_token' in tokens:
        with open(TOKEN_FILE, 'w') as f:
            json.dump(tokens, f)
        print("[Google Drive] Tokens guardados exitosamente")
    
    return tokens


def get_credentials() -> Optional[Credentials]:
    """Obtiene las credenciales de Google, refrescándolas si es necesario."""
    if not os.path.exists(TOKEN_FILE):
        return None
    
    try:
        with open(TOKEN_FILE, 'r') as f:
            token_data = json.load(f)
        
        creds = Credentials(
            token=token_data.get('access_token'),
            refresh_token=token_data.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=SCOPES
        )
        
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_data['access_token'] = creds.token
            with open(TOKEN_FILE, 'w') as f:
                json.dump(token_data, f)
            print("[Google Drive] Token refrescado")
        
        return creds
        
    except Exception as e:
        print(f"[Google Drive] Error al obtener credenciales: {e}")
        return None


def is_authenticated() -> bool:
    """Verifica si hay una sesión autenticada válida."""
    creds = get_credentials()
    return creds is not None and creds.valid


class GoogleDriveManager:
    """Clase para manejar la conexión y lectura de archivos de Google Drive."""
    
    def __init__(self):
        self.service = None
        self.pdf_cache: Dict[str, Dict] = {}  # {file_id: {text, modified_time, cached_at}}
        self.files_list_cache: List[Dict] = []
        self.files_list_cached_at: float = 0
        self.all_documents_text: str = ""
        self.all_documents_cached_at: float = 0
        self._ensure_cache_folder()
        self._load_cache_from_disk()
        
        creds = get_credentials()
        if creds:
            self.service = build('drive', 'v3', credentials=creds)
            print("[Google Drive] Conectado exitosamente")
    
    def _ensure_cache_folder(self):
        """Crea la carpeta de cache si no existe."""
        if not os.path.exists(CACHE_FOLDER):
            os.makedirs(CACHE_FOLDER)
    
    def _load_cache_from_disk(self):
        """Carga el cache de PDFs desde disco."""
        cache_file = os.path.join(CACHE_FOLDER, "pdf_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.pdf_cache = data.get('pdfs', {})
                    self.all_documents_text = data.get('all_text', '')
                    self.all_documents_cached_at = data.get('all_cached_at', 0)
                print(f"[Google Drive] Cache cargado: {len(self.pdf_cache)} PDFs")
            except Exception as e:
                print(f"[Google Drive] Error cargando cache: {e}")
    
    def _save_cache_to_disk(self):
        """Guarda el cache de PDFs en disco."""
        cache_file = os.path.join(CACHE_FOLDER, "pdf_cache.json")
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'pdfs': self.pdf_cache,
                    'all_text': self.all_documents_text,
                    'all_cached_at': self.all_documents_cached_at
                }, f, ensure_ascii=False)
            print("[Google Drive] Cache guardado en disco")
        except Exception as e:
            print(f"[Google Drive] Error guardando cache: {e}")
    
    def is_ready(self) -> bool:
        """Verifica si el servicio está listo para usar."""
        return self.service is not None
    
    def reconnect(self):
        """Reconecta con nuevas credenciales."""
        creds = get_credentials()
        if creds:
            self.service = build('drive', 'v3', credentials=creds)
            print("[Google Drive] Reconectado exitosamente")
            return True
        return False
    
    def list_pdf_files(self, force_refresh: bool = False) -> List[Dict]:
        """
        Lista TODOS los archivos PDF en la carpeta configurada.
        
        Args:
            force_refresh: Si True, ignora el cache de la lista
        """
        if not self.is_ready():
            print("[Google Drive] Servicio no disponible, intentando reconectar...")
            if not self.reconnect():
                return []
        
        # Verificar cache de la lista
        cache_age = time.time() - self.files_list_cached_at
        if not force_refresh and self.files_list_cache and cache_age < CACHE_REFRESH_INTERVAL:
            print(f"[Google Drive] Usando lista en cache ({len(self.files_list_cache)} archivos)")
            return self.files_list_cache
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                query = f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false"
                
                all_files = []
                page_token = None
                
                while True:
                    results = self.service.files().list(
                        q=query,
                        fields="nextPageToken, files(id, name, modifiedTime, size)",
                        orderBy="name",
                        pageSize=100,
                        pageToken=page_token
                    ).execute()
                    
                    files = results.get('files', [])
                    all_files.extend(files)
                    
                    page_token = results.get('nextPageToken')
                    if not page_token:
                        break
                
                self.files_list_cache = all_files
                self.files_list_cached_at = time.time()
                
                print(f"[Google Drive] Encontrados {len(all_files)} archivos PDF")
                return all_files
                
            except Exception as e:
                print(f"[Google Drive] Error al listar archivos (intento {attempt+1}/{max_retries}): {e}")
                if "401" in str(e) or "invalid_grant" in str(e).lower():
                    print("[Google Drive] Token expirado o inválido, reconectando...")
                    self.reconnect()
                time.sleep(1) # Esperar un poco antes de reintentar
        
        return self.files_list_cache if self.files_list_cache else []
    
    def download_pdf(self, file_id: str, file_name: str, modified_time: str = None) -> Optional[str]:
        """
        Descarga un PDF y extrae su texto.
        
        Args:
            file_id: ID del archivo en Drive
            file_name: Nombre del archivo
            modified_time: Fecha de modificación para verificar cache
        """
        if not self.is_ready():
            if not self.reconnect():
                return None
        
        # Verificar cache
        if file_id in self.pdf_cache:
            cached = self.pdf_cache[file_id]
            # Si el archivo no ha sido modificado, usar cache
            if modified_time and cached.get('modified_time') == modified_time:
                # print(f"[Google Drive] Cache válido para: {file_name}")
                return cached.get('text')
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                request = self.service.files().get_media(fileId=file_id)
                file_buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(file_buffer, request)
                
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                
                file_buffer.seek(0)
                reader = PdfReader(file_buffer)
                
                text_parts = []
                for page_num, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        text_parts.append(f"--- Página {page_num + 1} ---\n{text}")
                
                full_text = "\n\n".join(text_parts)
                
                # Guardar en cache
                self.pdf_cache[file_id] = {
                    'text': full_text,
                    'modified_time': modified_time,
                    'cached_at': time.time(),
                    'name': file_name
                }
                
                print(f"[Google Drive] Extraído: {file_name} ({len(full_text)} caracteres)")
                return full_text
                
            except Exception as e:
                print(f"[Google Drive] Error al descargar {file_name} (intento {attempt+1}/{max_retries}): {e}")
                if "401" in str(e) or "invalid_grant" in str(e).lower():
                    print("[Google Drive] Token expirado o inválido, reconectando...")
                    self.reconnect()
                time.sleep(1)
        
        # Intentar devolver cache antiguo si existe
        if file_id in self.pdf_cache:
            print(f"[Google Drive] Usando cache antiguo para {file_name} debido a error")
            return self.pdf_cache[file_id].get('text')
        return None
    
    def get_all_documents_text(self, force_refresh: bool = False) -> str:
        """
        Obtiene el texto de TODOS los PDFs en la carpeta.
        
        Args:
            force_refresh: Si True, descarga todos los PDFs de nuevo
        """
        # Verificar si hay cache válido
        cache_age = time.time() - self.all_documents_cached_at
        if not force_refresh and self.all_documents_text and cache_age < CACHE_REFRESH_INTERVAL:
            print("[Google Drive] Usando cache de todos los documentos")
            return self.all_documents_text
        
        files = self.list_pdf_files(force_refresh)
        
        all_texts = []
        for file in files:
            text = self.download_pdf(
                file['id'], 
                file['name'], 
                file.get('modifiedTime')
            )
            if text:
                all_texts.append(
                    f"\n{'='*60}\n"
                    f"DOCUMENTO: {file['name']}\n"
                    f"{'='*60}\n"
                    f"{text}"
                )
        
        self.all_documents_text = "\n\n".join(all_texts)
        self.all_documents_cached_at = time.time()
        self._save_cache_to_disk()
        
        print(f"[Google Drive] Total documentos procesados: {len(all_texts)}")
        return self.all_documents_text
    
    def search_in_documents(self, query: str) -> str:
        """
        Busca información en TODOS los documentos según la consulta.
        Ya no depende de un mapeo fijo de palabras clave.
        
        Args:
            query: Consulta del usuario
            
        Returns:
            Texto de todos los documentos para que la IA busque
        """
        # Obtener todos los documentos (usa cache si está disponible)
        all_text = self.get_all_documents_text()
        
        if not all_text:
            return "No se pudieron cargar los documentos. Por favor, intenta más tarde."
        
        return all_text
    
    def refresh_cache(self):
        """Fuerza la actualización del cache de documentos."""
        print("[Google Drive] Refrescando cache de documentos...")
        self.get_all_documents_text(force_refresh=True)
        print("[Google Drive] Cache actualizado")


# Instancia global (singleton)
_drive_manager = None

def get_drive_manager() -> GoogleDriveManager:
    """Obtiene la instancia del manejador de Google Drive."""
    global _drive_manager
    if _drive_manager is None:
        _drive_manager = GoogleDriveManager()
    return _drive_manager

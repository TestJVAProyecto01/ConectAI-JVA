"""
Módulo de Google Sheets - Registro de Consultas
================================================
Este módulo maneja el registro de consultas en Google Sheets.
Usa OAuth 2.0 para Aplicación Web (sin archivo credentials.json).
"""

import os
from datetime import datetime
from typing import Optional
from googleapiclient.discovery import build

from config import GOOGLE_SHEET_ID

from google_drive import get_credentials, is_authenticated


class GoogleSheetsManager:
    """Clase para manejar el registro de consultas en Google Sheets."""
    
    def __init__(self):
        self.service = None
        
        # Intentar conectar si hay credenciales
        creds = get_credentials()
        if creds:
            self.service = build('sheets', 'v4', credentials=creds)
            self._ensure_headers()
            print("[Google Sheets] Conectado exitosamente")
    
    def is_ready(self) -> bool:
        """Verifica si el servicio está listo para usar."""
        return self.service is not None
    
    def reconnect(self):
        """Reconecta con nuevas credenciales."""
        creds = get_credentials()
        if creds:
            self.service = build('sheets', 'v4', credentials=creds)
            self._ensure_headers()
            print("[Google Sheets] Reconectado exitosamente")
            return True
        return False
    
    def _ensure_headers(self):
        """Asegura que la hoja tenga los encabezados correctos."""
        if not self.is_ready():
            return
        
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID,
                range='A1:I1'  # Ampliado para incluir columnas de feedback
            ).execute()
            
            values = result.get('values', [])
            
            if not values or len(values[0]) < 9:
                headers = [[
                    'Fecha',
                    'Hora',
                    'Consulta del Usuario',
                    'Respuesta del Bot',
                    'Tipo de Consulta',
                    'Estado',
                    'Feedback',           # Nueva columna
                    'Comentario Feedback', # Nueva columna
                    'ID Mensaje'           # Nueva columna
                ]]
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=GOOGLE_SHEET_ID,
                    range='A1:I1',
                    valueInputOption='RAW',
                    body={'values': headers}
                ).execute()
                
                print("[Google Sheets] Encabezados actualizados con columnas de feedback")
        except Exception as e:
            print(f"[Google Sheets] Error al verificar encabezados: {e}")
    
    def find_recent_duplicate(self, user_query: str, time_window_seconds: int = 60) -> int:
        """Busca una fila reciente con la misma consulta del usuario.
        Retorna el número de fila si encuentra un duplicado, 0 si no."""
        if not self.is_ready():
            return 0
        
        try:
            from datetime import datetime, timedelta
            
            # Obtener todas las filas recientes
            result = self.service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID,
                range='A:F'  # Fecha, Hora, Consulta, Respuesta, Tipo, Estado
            ).execute()
            
            values = result.get('values', [])
            if len(values) <= 1:  # Solo encabezados o vacío
                return 0
            
            now = datetime.now()
            cutoff_time = now - timedelta(seconds=time_window_seconds)
            
            # Buscar desde el final (filas más recientes)
            for i in range(len(values) - 1, 0, -1):  # Empezar desde la última fila
                row = values[i]
                if len(row) < 3:  # Necesitamos al menos fecha, hora, consulta
                    continue
                
                try:
                    # Parsear fecha y hora
                    fecha_str = row[0]  # YYYY-MM-DD
                    hora_str = row[1]   # HH:MM:SS
                    consulta = row[2] if len(row) > 2 else ""
                    
                    row_datetime = datetime.strptime(f"{fecha_str} {hora_str}", "%Y-%m-%d %H:%M:%S")
                    
                    # Si la fila es muy antigua, dejamos de buscar
                    if row_datetime < cutoff_time:
                        break
                    
                    # Comparar consultas (normalizar espacios y mayúsculas)
                    if consulta.strip().lower() == user_query[:1000].strip().lower():
                        row_number = i + 1  # +1 porque las filas empiezan en 1
                        print(f"[Google Sheets] Duplicado encontrado en fila {row_number}")
                        return row_number
                        
                except Exception as e:
                    # Si hay error parseando esta fila, continuar con la siguiente
                    continue
            
            return 0
            
        except Exception as e:
            print(f"[Google Sheets] Error al buscar duplicados: {e}")
            return 0
    
    def log_consultation(
        self,
        user_query: str,
        bot_response: str,
        query_type: str = "general",
        status: str = "completado"
    ) -> int:
        """Registra una consulta y devuelve el número de fila insertada (0 si falla).
        Si encuentra un duplicado reciente, actualiza esa fila en lugar de crear una nueva."""
        if not self.is_ready():
            print("[Google Sheets] Servicio no disponible")
            return False
        
        try:
            # Primero, buscar si hay un duplicado reciente
            duplicate_row = self.find_recent_duplicate(user_query, time_window_seconds=60)
            
            if duplicate_row > 0:
                # Actualizar la fila existente en lugar de crear una nueva
                print(f"[Google Sheets] Actualizando fila duplicada {duplicate_row}")
                success = self.update_consultation(
                    row_number=duplicate_row,
                    user_query=user_query,
                    bot_response=bot_response,
                    query_type=query_type,
                    status=status
                )
                return duplicate_row if success else 0
            
            # No hay duplicado, crear nueva fila
            now = datetime.now()
            
            row = [[
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                user_query[:1000],  # Aumentado de 500 a 1000
                bot_response[:50000],  # Aumentado de 5000 a 50000 (límite de Google Sheets)
                query_type,
                status,
                "",  # Feedback (se llenará después)
                "",  # Comentario feedback
                ""   # ID Mensaje
            ]]
            
            result = self.service.spreadsheets().values().append(
                spreadsheetId=GOOGLE_SHEET_ID,
                range='A:I',
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body={'values': row}
            ).execute()
            
            # Extraer el rango actualizado para obtener el número de fila
            # Ejemplo de updatedRange: "Hoja 1!A108:I108"
            updated_range = result.get('updates', {}).get('updatedRange', '')
            row_number = 0
            if updated_range:
                try:
                    # Extraer el número después de la última letra y antes de : o final
                    import re
                    match = re.search(r'!A(\d+):', updated_range)
                    if match:
                        row_number = int(match.group(1))
                except:
                    pass
            
            print(f"[Google Sheets] Consulta registrada en fila {row_number}: {query_type}")
            return row_number
            
        except Exception as e:
            print(f"[Google Sheets] Error al registrar consulta: {e}")
            return 0

    def update_consultation(
        self,
        row_number: int,
        user_query: str,
        bot_response: str,
        query_type: str = "general",
        status: str = "completado"
    ) -> bool:
        """Actualiza una consulta existente en la fila especificada."""
        if not self.is_ready() or row_number <= 0:
            return False
            
        try:
            now = datetime.now()
            
            # Actualizamos columnas A a F (Fecha, Hora, Consulta, Respuesta, Tipo, Estado)
            # Mantenemos el feedback si existía (columnas G, H, I no se tocan aquí)
            range_name = f"A{row_number}:F{row_number}"
            
            row = [[
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                user_query[:1000],  # Aumentado de 500 a 1000
                bot_response[:50000],  # Aumentado de 5000 a 50000 (límite de Google Sheets)
                query_type,
                status
            ]]
            
            self.service.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=range_name,
                valueInputOption='RAW',
                body={'values': row}
            ).execute()
            
            print(f"[Google Sheets] Consulta actualizada en fila {row_number}")
            return True
            
        except Exception as e:
            print(f"[Google Sheets] Error al actualizar consulta en fila {row_number}: {e}")
            return False
    
    def update_feedback(
        self,
        row_number: int,
        feedback_type: str,
        comment: str = ""
    ) -> bool:
        """Actualiza el feedback en una fila existente."""
        if not self.is_ready() or row_number <= 0:
            return False
            
        try:
            # Determinar el valor a mostrar
            if feedback_type == "like":
                feedback_display = "👍 Útil"
            elif feedback_type == "dislike":
                feedback_display = "👎 No útil"
            else:
                feedback_display = "" # Limpiar si es 'none' u otro
            
            # Columnas G (7) y H (8) corresponden a Feedback y Comentario
            # En notación A1, G{row}:H{row}
            range_name = f"G{row_number}:H{row_number}"
            
            values = [[feedback_display, comment]]
            
            self.service.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=range_name,
                valueInputOption='RAW',
                body={'values': values}
            ).execute()
            
            print(f"[Google Sheets] Feedback actualizado en fila {row_number}: {feedback_type}")
            return True
            
        except Exception as e:
            print(f"[Google Sheets] Error al actualizar feedback: {e}")
            return False
    
    def log_feedback(
        self,
        user_query: str,
        bot_response: str,
        feedback_type: str,
        comment: str = "",
        message_id: str = ""
    ) -> bool:
        """Registra el feedback del usuario sobre una respuesta."""
        if not self.is_ready():
            print("[Google Sheets] Servicio no disponible")
            return False
        
        try:
            now = datetime.now()
            
            # Determinar el emoji/texto para el tipo de feedback
            feedback_display = "👍 Útil" if feedback_type == "like" else "👎 No útil"
            
            row = [[
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                user_query[:500],
                bot_response[:5000],
                "feedback",
                "registrado",
                feedback_display,
                comment[:1000] if comment else "",
                str(message_id)
            ]]
            
            self.service.spreadsheets().values().append(
                spreadsheetId=GOOGLE_SHEET_ID,
                range='A:I',
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body={'values': row}
            ).execute()
            
            print(f"[Google Sheets] Feedback registrado: {feedback_type}")
            return True
            
        except Exception as e:
            print(f"[Google Sheets] Error al registrar feedback: {e}")
            return False
    
    def get_statistics(self) -> dict:
        """Obtiene estadísticas de las consultas registradas."""
        if not self.is_ready():
            return {"total": 0, "por_tipo": {}, "error": "Servicio no disponible"}
        
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID,
                range='A:F'
            ).execute()
            
            values = result.get('values', [])
            
            if len(values) <= 1:
                return {"total": 0, "por_tipo": {}}
            
            tipo_count = {}
            for row in values[1:]:
                if len(row) >= 5:
                    tipo = row[4]
                    tipo_count[tipo] = tipo_count.get(tipo, 0) + 1
            
            return {
                "total": len(values) - 1,
                "por_tipo": tipo_count
            }
            
        except Exception as e:
            print(f"[Google Sheets] Error al obtener estadísticas: {e}")
            return {"total": 0, "por_tipo": {}, "error": str(e)}


# Instancia global (singleton)
_sheets_manager = None

def get_sheets_manager() -> GoogleSheetsManager:
    """Obtiene la instancia del manejador de Google Sheets."""
    global _sheets_manager
    if _sheets_manager is None:
        _sheets_manager = GoogleSheetsManager()
    return _sheets_manager

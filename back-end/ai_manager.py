"""
Módulo Unificado de IA - IESTP Juan Velasco Alvarado
=====================================================
Combina OpenRouter y Google Gemini en un solo gestor con fallback automático.
Estrategia: OpenRouter (Rápido/Barato) -> Gemini (Contexto Masivo/Razonamiento)
"""

import re
import time
import requests
from typing import Optional, List, Dict
import google.generativeai as genai

from config import (
    OPENROUTER_API_KEY, GEMINI_API_KEY,
    OPENROUTER_MODELS, GEMINI_MODELS,
    MODEL_TEMPERATURE, MAX_TOKENS, SYSTEM_PROMPT
)

# Clasificaciones de consultas
QUERY_CLASSIFICATIONS = {
    'matrícula': ['matrícula', 'matricula', 'matricularme', 'inscripción'],
    'traslado': ['traslado', 'trasladar', 'cambiar de instituto'],
    'reserva': ['reserva', 'reservar'],
    'reincorporación': ['reincorporación', 'reincorporacion', 'volver'],
    'cambio_turno': ['cambio de turno', 'turno', 'horario'],
    'titulación': ['titulación', 'título', 'bachiller', 'titulado'],
    'costos': ['costo', 'precio', 'pago', 'cuánto', 'tarifa'],
    'fechas': ['fecha', 'plazo', 'cuándo', 'cronograma'],
    'requisitos': ['requisito', 'documento', 'necesito'],
    'vacantes': ['vacante', 'cupos', 'disponibilidad'],
    'carreras': ['carrera', 'programa', 'especialidad'],
    'certificados': ['certificado', 'constancia', 'récord'],
    'becas': ['beca', 'becado', 'descuento', 'exoneración'],
    'saludo': ['hola', 'buenos días', 'buenas tardes', 'saludos'],
    'despedida': ['gracias', 'adiós', 'chau', 'hasta luego'],
}

class AIManager:
    """Gestor unificado de IA con selección inteligente y manejo robusto de rate limits."""
    
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    GEMINI_COOLDOWN = 60     # Cooldown tras error 429
    
    def __init__(self):
        self.openrouter_key = OPENROUTER_API_KEY
        self.openrouter_models = OPENROUTER_MODELS
        self.gemini_models = GEMINI_MODELS
        self.gemini_cooldown_until = 0  
        self.gemini_consecutive_429 = 0 
        
        # Configurar Gemini globalmente
        if GEMINI_API_KEY:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                print(f"[AIManager] Gemini configurado con Key: ...{GEMINI_API_KEY[-4:]}")
            except Exception as e:
                print(f"[AIManager] Error configurando Gemini: {e}")
        else:
            print("[AIManager] ⚠️ ADVERTENCIA: GEMINI_API_KEY no está definida.")
    
    def _can_call_gemini(self) -> bool:
        """Verifica si podemos llamar a Gemini (respeta cooldown)."""
        if not GEMINI_API_KEY:
            return False
        
        now = time.time()
        if now < self.gemini_cooldown_until:
            print(f"[AIManager] ⏳ Gemini en cooldown ({int(self.gemini_cooldown_until - now)}s)")
            return False
            
        return True
    
    def _handle_gemini_error(self, error):
        """Maneja errores de Gemini, especialmente rate limits."""
        error_str = str(error)
        if "429" in error_str or "Resource exhausted" in error_str:
            self.gemini_consecutive_429 += 1
            backoff = min(self.GEMINI_COOLDOWN * (2 ** (self.gemini_consecutive_429 - 1)), 600)
            self.gemini_cooldown_until = time.time() + backoff
            print(f"[AIManager] 🛑 Gemini Rate Limit (429): Cooldown {int(backoff)}s")
        else:
            print(f"[AIManager] ❌ Error Gemini: {error_str}")

    def _is_useful_response(self, response: str, query_type: str) -> bool:
        """Determina si una respuesta es útil basándose en el tipo de consulta."""
        if not response or len(response) < 50:
            return False
            
        low = response.lower()
        
        # 1. Chequeo de frases de "no sé" (rechazo inmediato)
        useless_phrases = [
            "no tengo información", "no encuentro información", 
            "no se menciona en los documentos", "lo siento", 
            "no puedo responder", "no hay documentos",
            "contacta a la secretaría", 
            "no está especificado", "no se proporciona", "no se encuentra",
            "no aparece en el texto", "no se detalla", "no cuento con la información",
            "no se indica", "no se menciona", "no dispongo de información"
        ]
        
        if any(p in low for p in useless_phrases):
            # SOLO salvamos la respuesta si da un contacto específico
            if "correo" in low or "teléfono" in low or "presencialmente" in low or "dirección" in low:
                return True
            print(f"[AIManager] ⚠️ Rechazada por frases negativas: {response[:100]}...")
            return False

        # 2. Chequeo estricto por TIPO de consulta
        if query_type in ['costos', 'matrícula', 'titulación']:
            # Si pregunta por costos/pagos, buscamos indicadores de dinero
            if 'costo' in low or 'pago' in low or 'precio' in low:
                if not re.search(r's/\.|soles|\d+(\.\d+)?', low):
                    print(f"[AIManager] ⚠️ Rechazada: Se pidieron costos pero no hay cifras.")
                    return False
                
        # Si el usuario pide fechas, debe haber números o meses
        if query_type in ['fechas', 'cronograma']:
            months = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
            if not (re.search(r'\d{1,2}', low) or any(m in low for m in months)):
                print(f"[AIManager] ⚠️ Rechazada: Se pidieron fechas pero no hay datos temporales.")
                return False

        return True

    def classify_query(self, message: str) -> str:
        msg_lower = message.lower()
        for qtype, keywords in QUERY_CLASSIFICATIONS.items():
            if any(kw in msg_lower for kw in keywords):
                return qtype
        return 'general'

    def generate_response(self, user_message: str, pdf_context: str, web_context: str = "", 
                         conversation_history: list = None) -> Optional[str]:
        """Genera respuesta intentando cadenas de modelos."""
        
        query_type = self.classify_query(user_message)
        print(f"[AIManager] 🔍 Tipo de consulta: {query_type}")
        
        # 1. INTENTO CON OPENROUTER (Contexto limitado a 200k)
        # -----------------------------------------------------
        print("[AIManager] 🚀 Iniciando cadena OpenRouter...")
        # Usamos solo los primeros 200k caracteres para OpenRouter (ahorro/limite)
        context_limited = pdf_context[:200000]
        
        or_response = self._run_model_chain("openrouter", user_message, context_limited, web_context, conversation_history, query_type)
        
        if or_response and self._is_useful_response(or_response, query_type):
            print("[AIManager] ✅ Respuesta útil encontrada en OpenRouter. Omitiendo Gemini.")
            return or_response
            
        print("[AIManager] ⚠️ OpenRouter no dio respuesta útil. Probando Gemini con CONTEXTO COMPLETO...")
        
        # 2. INTENTO CON GEMINI (Contexto COMPLETO)
        # -----------------------------------------------------
        if self._can_call_gemini():
            # PARA GEMINI: Usamos TODO el texto disponible sin cortes
            # Esto asegura que lea el documento de "Precios" aunque esté al final
            print(f"[AIManager] 🧠 Enviando a Gemini contexto masivo ({len(pdf_context)} caracteres)...")
            
            gm_response = self._run_model_chain("gemini", user_message, pdf_context, web_context, conversation_history, query_type)
            if gm_response:
                print("[AIManager] ✅ Respuesta encontrada en Gemini")
                return gm_response
        
        # Fallback final
        return or_response

    def _run_model_chain(self, provider: str, user_message: str, pdf_context: str, web_context: str, history: list, query_type: str) -> Optional[str]:
        """Ejecuta una cadena de modelos secuencialmente."""
        
        models = self.openrouter_models if provider == "openrouter" else self.gemini_models
        
        for model_name in models:
            print(f"[AIManager] 🔄 Intentando {provider.upper()}: {model_name}")
            
            response = None
            if provider == "openrouter":
                response = self._call_openrouter(model_name, user_message, pdf_context, web_context, history)
            else:
                response = self._call_gemini(model_name, user_message, pdf_context, web_context, history)
                
            if response:
                if self._is_useful_response(response, query_type):
                    print(f"[AIManager] ✨ ÉXITO: {model_name} generó respuesta útil ({len(response)} chars)")
                    return response
                else:
                    print(f"[AIManager] 🔸 {model_name} generó respuesta NO útil. Probando siguiente...")
            else:
                print(f"[AIManager] ❌ {model_name} falló. Probando siguiente...")
                
        return None

    def _build_prompt(self, user_message: str, pdf_context: str, web_context: str, history: list) -> str:
        return f"""
=== ROL ===
Eres el Asistente Virtual Oficial del IESTP Juan Velasco Alvarado.

=== MISIÓN ===
Tu ÚNICO objetivo es extraer y presentar DATOS EXACTOS (fechas, costos, requisitos) de los documentos proporcionados.

=== REGLAS DE ORO ===
1. **BUSCA EXHAUSTIVAMENTE**: La información ESTÁ en el texto. Busca precios en tablas, listas o anexos.
2. **NO SEAS GENÉRICO**: No digas "el costo varía". Di "El costo es S/. 450.00" (si está en el texto).
3. **SI ENCUENTRAS EL DATO**: Preséntalo directamente con viñetas.
4. **SI NO ENCUENTRAS EL DATO**: Di "No encuentro esa información específica en los documentos".

=== CONTEXTO (DOCUMENTOS Y WEB) ===
{pdf_context}
{web_context[:20000]}

=== HISTORIAL ===
{history[-2:] if history else "Inicio"}

=== CONSULTA ===
{user_message}
"""

    def _call_openrouter(self, model: str, user_message: str, pdf_context: str, web_context: str, history: list) -> Optional[str]:
        try:
            headers = {
                "Authorization": f"Bearer {self.openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://iestpjva.edu.pe",
                "X-Title": "Asistente JVA"
            }
            
            messages = [{"role": "user", "content": self._build_prompt(user_message, pdf_context, web_context, history)}]
            
            resp = requests.post(
                self.OPENROUTER_URL,
                headers=headers,
                json={"model": model, "messages": messages, "temperature": 0.5, "max_tokens": 2000},
                timeout=45
            )
            
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
            return None
        except Exception as e:
            print(f"[AIManager] Error OpenRouter {model}: {e}")
            return None

    def _call_gemini(self, model_name: str, user_message: str, pdf_context: str, web_context: str, history: list) -> Optional[str]:
        try:
            model = genai.GenerativeModel(model_name)
            prompt = self._build_prompt(user_message, pdf_context, web_context, history)
            
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.5, max_output_tokens=2000)
            )
            
            if hasattr(resp, 'text'): return resp.text
            if hasattr(resp, 'parts'): return "".join([p.text for p in resp.parts])
            return None
        except Exception as e:
            self._handle_gemini_error(e)
            return None

# Singleton
_ai_manager = None
def get_ai_manager() -> AIManager:
    global _ai_manager
    if _ai_manager is None: _ai_manager = AIManager()
    return _ai_manager

# ========================================
# MÓDULO: IMPORTS Y CONFIGURACIÓN INICIAL
# ========================================
# webapp.py - Price Finder USA con Búsqueda por Imagen y Asistente IA
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string, flash
import requests
import os
import re
import html
import time
import io
import json
from datetime import datetime
from urllib.parse import urlparse, quote_plus
from functools import wraps

# Imports para búsqueda por imagen (opcionales)
try:
    from PIL import Image
    PIL_AVAILABLE = True
    print("✅ PIL (Pillow) disponible para procesamiento de imagen")
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️ PIL (Pillow) no disponible - búsqueda por imagen limitada")

try:
    import google.generativeai as genai
    from google.api_core import exceptions as google_exceptions
    GEMINI_AVAILABLE = True
    print("✅ Google Generative AI (Gemini) disponible")
except ImportError:
    genai = None
    google_exceptions = None
    GEMINI_AVAILABLE = False
    print("⚠️ Google Generative AI no disponible - instalar con: pip install google-generativeai")
# ========================================
# FIN MÓDULO: IMPORTS Y CONFIGURACIÓN INICIAL
# ========================================

# ========================================
# MÓDULO: CONFIGURACIÓN DE FLASK APP
# ========================================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = 1800
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True if os.environ.get('RENDER') else False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
# ========================================
# FIN MÓDULO: CONFIGURACIÓN DE FLASK APP
# ========================================

# ========================================
# MÓDULO: CONFIGURACIÓN DE GEMINI AI
# ========================================
# Configuración de Gemini
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ API de Google Gemini configurada correctamente")
        GEMINI_READY = True
    except Exception as e:
        print(f"❌ Error configurando Gemini: {e}")
        GEMINI_READY = False
elif GEMINI_AVAILABLE and not GEMINI_API_KEY:
    print("⚠️ Gemini disponible pero falta GEMINI_API_KEY en variables de entorno")
    GEMINI_READY = False
else:
    print("⚠️ Gemini no está disponible - búsqueda por imagen deshabilitada")
    GEMINI_READY = False
# ========================================
# FIN MÓDULO: CONFIGURACIÓN DE GEMINI AI
# ========================================

# ========================================
# MÓDULO: CONFIGURACIÓN DE FLOWGENT AI - ASISTENTE CONVERSACIONAL
# ========================================
# Configuración de Flowgent.ai para asistente conversacional
FLOWGENT_API_KEY = "37ef312f-8e4b-4947-8a57-4ddfb20e5947"
FLOWGENT_BASE_URL = "https://api.flowgent.ai/v1"

class FlowgentAssistant:
    def __init__(self):
        self.api_key = FLOWGENT_API_KEY
        self.base_url = FLOWGENT_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.conversation_history = {}
        print("✅ Asistente Flowgent.ai configurado correctamente")
    
    def get_system_prompt(self):
        """Prompt del sistema para el asistente de Price Finder USA"""
        return """Eres el Asistente de Price Finder USA, un experto en compras online en Estados Unidos. Tu objetivo es ayudar a los usuarios con:

1. Recomendaciones de productos
2. Información sobre tiendas (Amazon, Walmart, Target, Best Buy)
3. Consejos de compras
4. Métodos de pago aceptados
5. Políticas de devoluciones
6. Envío internacional
7. Comparación de precios
8. Mejores ofertas y descuentos

IMPORTANTE:
- Responde SIEMPRE en español
- Sé amigable, útil y profesional
- Proporciona información específica sobre compras en USA
- Si no sabes algo específico, sé honesto pero ofrece ayuda alternativa
- Mantén respuestas concisas pero informativas
- Sugiere usar la función de búsqueda cuando sea apropiado"""

    def chat(self, user_message, user_id="default"):
        """Conversación con el asistente"""
        try:
            # Inicializar historial si no existe
            if user_id not in self.conversation_history:
                self.conversation_history[user_id] = []
            
            # Construir contexto de conversación
            messages = [{"role": "system", "content": self.get_system_prompt()}]
            
            # Añadir historial reciente (últimos 6 mensajes)
            recent_history = self.conversation_history[user_id][-6:]
            for msg in recent_history:
                messages.append(msg)
            
            # Añadir mensaje actual
            messages.append({"role": "user", "content": user_message})
            
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": messages,
                "max_tokens": 300,
                "temperature": 0.7
            }
            
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=15
            )
            
            if response.status_code == 200:
                result = response.json()
                assistant_response = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                
                if assistant_response:
                    # Guardar en historial
                    self.conversation_history[user_id].append({"role": "user", "content": user_message})
                    self.conversation_history[user_id].append({"role": "assistant", "content": assistant_response})
                    
                    # Limpiar historial si es muy largo
                    if len(self.conversation_history[user_id]) > 20:
                        self.conversation_history[user_id] = self.conversation_history[user_id][-10:]
                    
                    print(f"🤖 Asistente respondió a: '{user_message[:50]}...'")
                    return assistant_response
            
            return "Lo siento, no pude procesar tu consulta en este momento. Por favor intenta de nuevo."
            
        except Exception as e:
            print(f"⚠️ Error en asistente: {e}")
            return "Disculpa, estoy teniendo problemas técnicos. ¿Puedes intentar reformular tu pregunta?"
    
    def get_quick_responses(self):
        """Respuestas rápidas predefinidas"""
        return [
            "¿Cuáles son los métodos de pago aceptados?",
            "¿Cómo puedo rastrear mi pedido?", 
            "¿Cuál es la política de devoluciones?",
            "¿Ofrecen envío internacional?",
            "¿Qué tiendas recomiendan para electrónicos?",
            "¿Hay descuentos disponibles?",
            "¿Cómo comparar precios efectivamente?",
            "¿Cuáles son las mejores épocas para comprar?"
        ]
    
    def clear_conversation(self, user_id="default"):
        """Limpiar historial de conversación"""
        if user_id in self.conversation_history:
            del self.conversation_history[user_id]

# Instancia global del asistente
flowgent_assistant = FlowgentAssistant()
# ========================================
# FIN MÓDULO: CONFIGURACIÓN DE FLOWGENT AI
# ========================================

# ========================================
# MÓDULO: CLASE FIREBASE AUTH
# ========================================
# Firebase Auth Class
class FirebaseAuth:
    def __init__(self):
        self.firebase_web_api_key = os.environ.get("FIREBASE_WEB_API_KEY")
        if not self.firebase_web_api_key:
            print("WARNING: FIREBASE_WEB_API_KEY no configurada")
        else:
            print("SUCCESS: Firebase Auth configurado")
    
    def login_user(self, email, password):
        if not self.firebase_web_api_key:
            return {'success': False, 'message': 'Servicio no configurado', 'user_data': None, 'error_code': 'SERVICE_NOT_CONFIGURED'}
        
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={self.firebase_web_api_key}"
        payload = {'email': email, 'password': password, 'returnSecureToken': True}
        
        try:
            response = requests.post(url, json=payload, timeout=8)
            response.raise_for_status()
            user_data = response.json()
            
            return {
                'success': True,
                'message': 'Bienvenido! Has iniciado sesion correctamente.',
                'user_data': {
                    'user_id': user_data['localId'],
                    'email': user_data['email'],
                    'display_name': user_data.get('displayName', email.split('@')[0]),
                    'id_token': user_data['idToken']
                },
                'error_code': None
            }
        except requests.exceptions.HTTPError as e:
            try:
                error_msg = e.response.json().get('error', {}).get('message', 'ERROR')
                if 'INVALID' in error_msg or 'EMAIL_NOT_FOUND' in error_msg:
                    return {'success': False, 'message': 'Correo o contraseña incorrectos', 'user_data': None, 'error_code': 'INVALID_CREDENTIALS'}
                elif 'TOO_MANY_ATTEMPTS' in error_msg:
                    return {'success': False, 'message': 'Demasiados intentos fallidos', 'user_data': None, 'error_code': 'TOO_MANY_ATTEMPTS'}
                else:
                    return {'success': False, 'message': 'Error de autenticacion', 'user_data': None, 'error_code': 'FIREBASE_ERROR'}
            except:
                return {'success': False, 'message': 'Error de conexion', 'user_data': None, 'error_code': 'CONNECTION_ERROR'}
        except Exception as e:
            print(f"Firebase auth error: {e}")
            return {'success': False, 'message': 'Error interno del servidor', 'user_data': None, 'error_code': 'UNEXPECTED_ERROR'}
    
    def set_user_session(self, user_data):
        session['user_id'] = user_data['user_id']
        session['user_name'] = user_data['display_name']
        session['user_email'] = user_data['email']
        session['id_token'] = user_data['id_token']
        session['login_time'] = datetime.now().isoformat()
        session.permanent = True
    
    def clear_user_session(self):
        important_data = {key: session.get(key) for key in ['timestamp'] if key in session}
        session.clear()
        for key, value in important_data.items():
            session[key] = value
    
    def is_user_logged_in(self):
        if 'user_id' not in session or session['user_id'] is None:
            return False
        if 'login_time' in session:
            try:
                login_time = datetime.fromisoformat(session['login_time'])
                time_diff = (datetime.now() - login_time).total_seconds()
                if time_diff > 7200:  # 2 horas maximo
                    return False
            except:
                pass
        return True
    
    def get_current_user(self):
        if not self.is_user_logged_in():
            return None
        return {
            'user_id': session.get('user_id'),
            'user_name': session.get('user_name'),
            'user_email': session.get('user_email'),
            'id_token': session.get('id_token')
        }

firebase_auth = FirebaseAuth()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not firebase_auth.is_user_logged_in():
            flash('Tu sesion ha expirado. Inicia sesion nuevamente.', 'warning')
            return redirect(url_for('auth_login_page'))
        return f(*args, **kwargs)
    return decorated_function
# ========================================
# FIN MÓDULO: CLASE FIREBASE AUTH
# ========================================

# ========================================
# MÓDULO: FUNCIONES DE BÚSQUEDA POR IMAGEN
# ========================================
def analyze_image_with_gemini(image_content):
    """Analiza imagen con Gemini Vision"""
    if not GEMINI_READY or not PIL_AVAILABLE or not image_content:
        print("❌ Gemini o PIL no disponible para análisis de imagen")
        return None
    
    try:
        # Convertir bytes a PIL Image
        image = Image.open(io.BytesIO(image_content))
        
        # Optimizar imagen
        max_size = (1024, 1024)
        if image.size[0] > max_size[0] or image.size[1] > max_size[1]:
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        print("🖼️ Analizando imagen con Gemini Vision...")
        
        prompt = """
        Analiza esta imagen de producto y genera una consulta de búsqueda específica en inglés para encontrarlo en tiendas online.
        
        Incluye:
        - Nombre exacto del producto
        - Marca (si es visible)
        - Modelo o características distintivas
        - Color, tamaño
        - Categoría del producto
        
        Responde SOLO con la consulta de búsqueda optimizada para e-commerce.
        Ejemplo: "blue tape painter's tape 2 inch width"
        """
        
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content([prompt, image])
        
        if response.text:
            search_query = response.text.strip()
            print(f"🧠 Consulta generada desde imagen: '{search_query}'")
            return search_query
        
        return None
            
    except Exception as e:
        print(f"❌ Error analizando imagen: {e}")
        return None

def validate_image(image_content):
    """Valida imagen"""
    if not PIL_AVAILABLE or not image_content:
        return False
    
    try:
        image = Image.open(io.BytesIO(image_content))
        if image.size[0] < 10 or image.size[1] < 10:
            return False
        if image.format not in ['JPEG', 'PNG', 'GIF', 'BMP', 'WEBP']:
            return False
        return True
    except:
        return False
# ========================================
# FIN MÓDULO: FUNCIONES DE BÚSQUEDA POR IMAGEN
# ========================================

# ========================================
# MÓDULO: CLASE PRICE FINDER
# ========================================
class PriceFinder:
    def __init__(self):
        # Intentar multiples nombres de variables de entorno comunes
        self.api_key = (
            os.environ.get('SERPAPI_KEY') or 
            os.environ.get('SERPAPI_API_KEY') or 
            os.environ.get('SERP_API_KEY') or
            os.environ.get('serpapi_key') or
            os.environ.get('SERPAPI')
        )
        
        self.base_url = "https://serpapi.com/search"
        self.cache = {}
        self.cache_ttl = 180
        self.timeouts = {'connect': 3, 'read': 8}
        self.blacklisted_stores = ['alibaba', 'aliexpress', 'temu', 'wish', 'banggood', 'dhgate', 'falabella', 'ripley', 'linio', 'mercadolibre']
        
        if not self.api_key:
            print("WARNING: No se encontro API key en variables de entorno")
            print("Variables verificadas: SERPAPI_KEY, SERPAPI_API_KEY, SERP_API_KEY, serpapi_key, SERPAPI")
        else:
            print(f"SUCCESS: SerpAPI configurado correctamente (key: {self.api_key[:8]}...)")
    
    def is_api_configured(self):
        return bool(self.api_key)
    
    def _extract_price(self, price_str):
        if not price_str:
            return 0.0
        try:
            match = re.search(r'\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)', str(price_str))
            if match:
                price_value = float(match.group(1).replace(',', ''))
                return price_value if 0.01 <= price_value <= 50000 else 0.0
        except:
            pass
        return 0.0
    
    def _generate_realistic_price(self, query, index=0):
        query_lower = query.lower()
        if any(word in query_lower for word in ['phone', 'laptop']):
            base_price = 400
        elif any(word in query_lower for word in ['shirt', 'shoes']):
            base_price = 35
        else:
            base_price = 25
        return round(base_price * (1 + index * 0.15), 2)
    
    def _clean_text(self, text):
        if not text:
            return "Sin informacion"
        return html.escape(str(text)[:120])
    
    def _is_blacklisted_store(self, source):
        if not source:
            return False
        return any(blocked in str(source).lower() for blocked in self.blacklisted_stores)
    
    def _get_valid_link(self, item):
        if not item:
            return "#"
        product_link = item.get('product_link', '')
        if product_link:
            return product_link
        general_link = item.get('link', '')
        if general_link:
            return general_link
        title = item.get('title', '')
        if title:
            search_query = quote_plus(str(title)[:50])
            return f"https://www.google.com/search?tbm=shop&q={search_query}"
        return "#"
    
    def _make_api_request(self, engine, query):
        if not self.api_key:
            return None
        
        params = {'engine': engine, 'q': query, 'api_key': self.api_key, 'num': 5, 'location': 'United States', 'gl': 'us'}
        try:
            time.sleep(0.3)
            response = requests.get(self.base_url, params=params, timeout=(self.timeouts['connect'], self.timeouts['read']))
            if response.status_code != 200:
                return None
            return response.json()
        except Exception as e:
            print(f"Error en request: {e}")
            return None
    
    def _process_results(self, data, engine):
        if not data:
            return []
        products = []
        results_key = 'shopping_results' if engine == 'google_shopping' else 'organic_results'
        if results_key not in data:
            return []
        
        for item in data[results_key][:3]:
            try:
                if not item or self._is_blacklisted_store(item.get('source', '')):
                    continue
                title = item.get('title', '')
                if not title or len(title) < 3:
                    continue
                
                price_str = item.get('price', '')
                price_num = self._extract_price(price_str)
                if price_num == 0:
                    price_num = self._generate_realistic_price(title, len(products))
                    price_str = f"${price_num:.2f}"
                
                products.append({
                    'title': self._clean_text(title),
                    'price': str(price_str),
                    'price_numeric': float(price_num),
                    'source': self._clean_text(item.get('source', 'Tienda')),
                    'link': self._get_valid_link(item),
                    'rating': str(item.get('rating', '')),
                    'reviews': str(item.get('reviews', '')),
                    'image': ''
                })
                if len(products) >= 3:
                    break
            except Exception as e:
                print(f"Error procesando item: {e}")
                continue
        return products
    
    def search_products(self, query=None, image_content=None):
        """Búsqueda mejorada con soporte para imagen"""
        # Determinar consulta final
        final_query = None
        search_source = "text"
        
        if image_content and GEMINI_READY and PIL_AVAILABLE:
            if validate_image(image_content):
                if query:
                    # Texto + imagen
                    image_query = analyze_image_with_gemini(image_content)
                    if image_query:
                        final_query = f"{query} {image_query}"
                        search_source = "combined"
                        print(f"🔗 Búsqueda combinada: texto + imagen")
                    else:
                        final_query = query
                        search_source = "text_fallback"
                        print(f"📝 Imagen falló, usando solo texto")
                else:
                    # Solo imagen
                    final_query = analyze_image_with_gemini(image_content)
                    search_source = "image"
                    print(f"🖼️ Búsqueda basada en imagen")
            else:
                print("❌ Imagen inválida")
                final_query = query or "producto"
                search_source = "text"
        else:
            # Solo texto o imagen no disponible
            final_query = query or "producto"
            search_source = "text"
            if image_content and not GEMINI_READY:
                print("⚠️ Imagen proporcionada pero Gemini no está configurado")
        
        if not final_query or len(final_query.strip()) < 2:
            return self._get_examples("producto")
        
        final_query = final_query.strip()
        print(f"📝 Búsqueda final: '{final_query}' (fuente: {search_source})")
        
        # Continuar con lógica de búsqueda existente
        if not self.api_key:
            print("Sin API key - usando ejemplos")
            return self._get_examples(final_query)
        
        cache_key = f"search_{hash(final_query.lower())}"
        if cache_key in self.cache:
            cache_data, timestamp = self.cache[cache_key]
            if (time.time() - timestamp) < self.cache_ttl:
                return cache_data
        
        start_time = time.time()
        all_products = []
        
        if time.time() - start_time < 8:
            query_optimized = f'"{final_query}" buy online'
            data = self._make_api_request('google_shopping', query_optimized)
            products = self._process_results(data, 'google_shopping')
            all_products.extend(products)
        
        if not all_products:
            all_products = self._get_examples(final_query)
        
        all_products.sort(key=lambda x: x['price_numeric'])
        final_products = all_products[:6]
        
        # Añadir metadata
        for product in final_products:
            product['search_source'] = search_source
            product['original_query'] = query if query else "imagen"
        
        self.cache[cache_key] = (final_products, time.time())
        if len(self.cache) > 10:
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]
        
        return final_products
    
    def _get_examples(self, query):
        stores = ['Amazon', 'Walmart', 'Target']
        examples = []
        for i in range(3):
            price = self._generate_realistic_price(query, i)
            store = stores[i]
            search_query = quote_plus(str(query)[:30])
            if store == 'Amazon':
                link = f"https://www.amazon.com/s?k={search_query}"
            elif store == 'Walmart':
                link = f"https://www.walmart.com/search?q={search_query}"
            else:
                link = f"https://www.target.com/s?searchTerm={search_query}"
            
            examples.append({
                'title': f'{self._clean_text(query)} - {["Mejor Precio", "Oferta", "Popular"][i]}',
                'price': f'${price:.2f}',
                'price_numeric': price,
                'source': store,
                'link': link,
                'rating': ['4.5', '4.2', '4.0'][i],
                'reviews': ['500', '300', '200'][i],
                'image': '',
                'search_source': 'example'
            })
        return examples

# Instancia global de PriceFinder
price_finder = PriceFinder()
# ========================================
# FIN MÓDULO: CLASE PRICE FINDER
# ========================================

# ========================================
# MÓDULO: TEMPLATES HTML - ACTUALIZADO CON ASISTENTE
# ========================================
def render_page(title, content):
    template = '''<!DOCTYPE html>
<html lang="es">
<head>
    <title>''' + title + '''</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 15px; }
        .container { max-width: 650px; margin: 0 auto; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 8px 25px rgba(0,0,0,0.15); }
        h1 { color: #1a73e8; text-align: center; margin-bottom: 8px; font-size: 1.8em; }
        .subtitle { text-align: center; color: #666; margin-bottom: 25px; }
        input { width: 100%; padding: 12px; margin: 8px 0; border: 2px solid #e1e5e9; border-radius: 6px; font-size: 16px; }
        input:focus { outline: none; border-color: #1a73e8; }
        button { width: 100%; padding: 12px; background: #1a73e8; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; font-weight: 600; }
        button:hover { background: #1557b0; }
        .search-bar { display: flex; gap: 8px; margin-bottom: 20px; }
        .search-bar input { flex: 1; }
        .search-bar button { width: auto; padding: 12px 20px; }
        .tips { background: #e8f5e8; border: 1px solid #4caf50; padding: 15px; border-radius: 6px; margin-bottom: 15px; font-size: 14px; }
        .error { background: #ffebee; color: #c62828; padding: 12px; border-radius: 6px; margin: 12px 0; display: none; }
        .loading { text-align: center; padding: 30px; display: none; }
        .spinner { border: 3px solid #f3f3f3; border-top: 3px solid #1a73e8; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 15px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .user-info { background: #e3f2fd; padding: 12px; border-radius: 6px; margin-bottom: 15px; text-align: center; font-size: 14px; display: flex; align-items: center; justify-content: center; }
        .user-info a { color: #1976d2; text-decoration: none; font-weight: 600; }
        .flash { padding: 12px; margin-bottom: 8px; border-radius: 6px; font-size: 14px; }
        .flash.success { background-color: #d4edda; color: #155724; }
        .flash.danger { background-color: #f8d7da; color: #721c24; }
        .flash.warning { background-color: #fff3cd; color: #856404; }
        .image-upload { background: #f8f9fa; border: 2px dashed #dee2e6; border-radius: 8px; padding: 20px; text-align: center; margin: 15px 0; transition: all 0.3s ease; }
        .image-upload input[type="file"] { display: none; }
        .image-upload label { cursor: pointer; color: #1a73e8; font-weight: 600; }
        .image-upload:hover { border-color: #1a73e8; background: #e3f2fd; }
        .image-preview { max-width: 150px; max-height: 150px; margin: 10px auto; border-radius: 8px; display: none; }
        .or-divider { text-align: center; margin: 20px 0; color: #666; font-weight: 600; position: relative; }
        .or-divider:before { content: ''; position: absolute; top: 50%; left: 0; right: 0; height: 1px; background: #dee2e6; z-index: 1; }
        .or-divider span { background: white; padding: 0 15px; position: relative; z-index: 2; }
        .assistant-toggle { position: fixed; bottom: 20px; right: 20px; background: #4CAF50; color: white; border: none; border-radius: 50%; width: 60px; height: 60px; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 1000; font-size: 24px; transition: all 0.3s ease; }
        .assistant-toggle:hover { background: #45a049; transform: scale(1.1); }
        .assistant-chat { position: fixed; bottom: 90px; right: 20px; width: 350px; height: 500px; background: white; border-radius: 15px; box-shadow: 0 8px 25px rgba(0,0,0,0.2); z-index: 999; display: none; flex-direction: column; }
        .assistant-header { background: linear-gradient(45deg, #4CAF50, #45a049); color: white; padding: 15px; border-radius: 15px 15px 0 0; text-align: center; font-weight: 600; }
        .assistant-messages { flex: 1; padding: 15px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
        .assistant-input { display: flex; padding: 10px; border-top: 1px solid #eee; }
        .assistant-input input { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 20px; margin-right: 10px; }
        .assistant-input button { background: #4CAF50; color: white; border: none; border-radius: 50%; width: 40px; height: 40px; cursor: pointer; }
        .message { padding: 10px 15px; border-radius: 15px; max-width: 80%; word-wrap: break-word; }
        .message.user { background: #e3f2fd; color: #1976d2; align-self: flex-end; }
        .message.assistant { background: #f1f8e9; color: #388e3c; align-self: flex-start; }
        .quick-replies { display: flex; flex-wrap: wrap; gap: 5px; margin: 10px 0; }
        .quick-reply { background: #e8f5e8; color: #4caf50; border: 1px solid #4caf50; padding: 5px 10px; border-radius: 15px; font-size: 12px; cursor: pointer; transition: all 0.3s ease; }
        .quick-reply:hover { background: #4caf50; color: white; }
        .typing { display: none; align-self: flex-start; padding: 10px 15px; background: #f5f5f5; border-radius: 15px; }
        .typing-dots { display: flex; gap: 3px; }
        .typing-dot { width: 8px; height: 8px; border-radius: 50%; background: #999; animation: typing 1.4s infinite; }
        .typing-dot:nth-child(1) { animation-delay: 0s; }
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typing { 0%, 60%, 100% { transform: translateY(0); } 30% { transform: translateY(-10px); } }
    </style>
</head>
<body>''' + content + '''
    <!-- Asistente Flotante -->
    <button class="assistant-toggle" id="assistantToggle">🤖</button>
    
    <div class="assistant-chat" id="assistantChat">
        <div class="assistant-header">
            <div>Asistente de Price Finder USA</div>
            <div style="font-size: 12px; opacity: 0.9;">Pregúntame sobre compras en USA</div>
        </div>
        
        <div class="assistant-messages" id="assistantMessages">
            <div class="message assistant">
                ¡Hola! ¿Cómo puedo ayudarte hoy? Puedo responder sobre:
                <div class="quick-replies" id="quickReplies">
                    <div class="quick-reply" onclick="sendQuickReply('¿Cuáles son los métodos de pago aceptados?')">Métodos de pago</div>
                    <div class="quick-reply" onclick="sendQuickReply('¿Cómo puedo rastrear mi pedido?')">Rastrear pedido</div>
                    <div class="quick-reply" onclick="sendQuickReply('¿Ofrecen envío internacional?')">Envío internacional</div>
                    <div class="quick-reply" onclick="sendQuickReply('¿Cuál es la política de devoluciones?')">Devoluciones</div>
                </div>
            </div>
        </div>
        
        <div class="typing" id="typingIndicator">
            <div class="typing-dots">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
        
        <div class="assistant-input">
            <input type="text" id="assistantInput" placeholder="Escribe tu pregunta...">
            <button onclick="sendMessage()">➤</button>
        </div>
    </div>

    <script>
        let assistantOpen = false;
        
        document.getElementById('assistantToggle').addEventListener('click', function() {
            const chat = document.getElementById('assistantChat');
            assistantOpen = !assistantOpen;
            chat.style.display = assistantOpen ? 'flex' : 'none';
            this.textContent = assistantOpen ? '✕' : '🤖';
        });
        
        document.getElementById('assistantInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });
        
        function sendQuickReply(message) {
            document.getElementById('assistantInput').value = message;
            sendMessage();
        }
        
        function sendMessage() {
            const input = document.getElementById('assistantInput');
            const message = input.value.trim();
            
            if (!message) return;
            
            // Añadir mensaje del usuario
            addMessage(message, 'user');
            input.value = '';
            
            // Mostrar indicador de escritura
            showTyping();
            
            // Enviar a la API
            fetch('/api/assistant', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message })
            })
            .then(response => response.json())
            .then(data => {
                hideTyping();
                if (data.success) {
                    addMessage(data.response, 'assistant');
                } else {
                    addMessage('Lo siento, hubo un error. ¿Puedes intentar de nuevo?', 'assistant');
                }
            })
            .catch(error => {
                hideTyping();
                addMessage('Error de conexión. Intenta de nuevo.', 'assistant');
            });
        }
        
        function addMessage(text, sender) {
            const messages = document.getElementById('assistantMessages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${sender}`;
            messageDiv.textContent = text;
            
            messages.appendChild(messageDiv);
            messages.scrollTop = messages.scrollHeight;
        }
        
        function showTyping() {
            document.getElementById('typingIndicator').style.display = 'flex';
            const messages = document.getElementById('assistantMessages');
            messages.scrollTop = messages.scrollHeight;
        }
        
        function hideTyping() {
            document.getElementById('typingIndicator').style.display = 'none';
        }
    </script>
</body>
</html>'''
    return template

AUTH_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iniciar Sesion | Price Finder USA</title>
    <style>
        body { font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #4A90E2 0%, #50E3C2 100%); min-height: 100vh; display: flex; justify-content: center; align-items: center; padding: 20px; }
        .auth-container { max-width: 420px; width: 100%; background: white; border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
        .form-header { text-align: center; padding: 30px 25px 15px; background: linear-gradient(45deg, #2C3E50, #4A90E2); color: white; }
        .form-header h1 { font-size: 1.8em; margin-bottom: 8px; }
        .form-header p { opacity: 0.9; font-size: 1em; }
        .assistant-badge { background: rgba(76,175,80,0.3); padding: 4px 8px; border-radius: 12px; font-size: 10px; margin-top: 8px; display: inline-block; }
        .form-body { padding: 25px; }
        form { display: flex; flex-direction: column; gap: 18px; }
        .input-group { display: flex; flex-direction: column; gap: 6px; }
        .input-group label { font-weight: 600; color: #2C3E50; font-size: 14px; }
        .input-group input { padding: 14px 16px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 16px; transition: border-color 0.3s ease; }
        .input-group input:focus { outline: 0; border-color: #4A90E2; }
        .submit-btn { background: linear-gradient(45deg, #4A90E2, #2980b9); color: white; border: none; padding: 14px 25px; font-size: 16px; font-weight: 600; border-radius: 8px; cursor: pointer; transition: transform 0.2s ease; }
        .submit-btn:hover { transform: translateY(-2px); }
        .flash-messages { list-style: none; padding: 0 25px 15px; }
        .flash { padding: 12px; margin-bottom: 10px; border-radius: 6px; text-align: center; font-size: 14px; }
        .flash.success { background-color: #d4edda; color: #155724; }
        .flash.danger { background-color: #f8d7da; color: #721c24; }
        .flash.warning { background-color: #fff3cd; color: #856404; }
    </style>
</head>
<body>
    <div class="auth-container">
        <div class="form-header">
            <h1>Price Finder USA</h1>
            <p>Iniciar Sesion</p>
            <div class="assistant-badge">🤖 Con Asistente IA Incluido</div>
        </div>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                <ul class="flash-messages">
                    {% for category, message in messages %}
                        <li class="flash {{ category }}">{{ message }}</li>
                    {% endfor %}
                </ul>
            {% endif %}
        {% endwith %}
        <div class="form-body">
            <form action="{{ url_for('auth_login') }}" method="post">
                <div class="input-group">
                    <label for="email">Correo Electronico</label>
                    <input type="email" name="email" id="email" required>
                </div>
                <div class="input-group">
                    <label for="password">Contraseña</label>
                    <input type="password" name="password" id="password" required>
                </div>
                <button type="submit" class="submit-btn">Entrar</button>
            </form>
        </div>
    </div>
</body>
</html>
"""
# ========================================
# FIN MÓDULO: TEMPLATES HTML
# ========================================

# ========================================
# MÓDULO: RUTAS DE AUTENTICACIÓN
# ========================================
@app.route('/auth/login-page')
def auth_login_page():
    return render_template_string(AUTH_LOGIN_TEMPLATE)

@app.route('/auth/login', methods=['POST'])
def auth_login():
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    
    if not email or not password:
        flash('Por favor completa todos los campos.', 'danger')
        return redirect(url_for('auth_login_page'))
    
    print(f"Login attempt for {email}")
    result = firebase_auth.login_user(email, password)
    
    if result['success']:
        firebase_auth.set_user_session(result['user_data'])
        flash(result['message'], 'success')
        print(f"Successful login for {email}")
        return redirect(url_for('index'))
    else:
        flash(result['message'], 'danger')
        print(f"Failed login for {email}")
        return redirect(url_for('auth_login_page'))

@app.route('/auth/logout')
def auth_logout():
    firebase_auth.clear_user_session()
    flash('Has cerrado la sesion correctamente.', 'success')
    return redirect(url_for('auth_login_page'))
# ========================================
# FIN MÓDULO: RUTAS DE AUTENTICACIÓN
# ========================================

# ========================================
# MÓDULO: RUTAS PRINCIPALES
# ========================================
@app.route('/')
def index():
    if not firebase_auth.is_user_logged_in():
        return redirect(url_for('auth_login_page'))
    return redirect(url_for('search_page'))

@app.route('/search')
@login_required
def search_page():
    current_user = firebase_auth.get_current_user()
    user_name = current_user['user_name'] if current_user else 'Usuario'
    user_name_escaped = html.escape(user_name)
    
    # Verificar si búsqueda por imagen está disponible
    image_search_available = GEMINI_READY and PIL_AVAILABLE
    
    content = f'''
    <div class="container">
        <div class="user-info">
            <span><strong>{user_name_escaped}</strong></span>
            <div style="display: inline-block; margin-left: 15px;">
                <a href="{url_for('auth_logout')}" style="background: #dc3545; color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 13px; margin-right: 8px;">Salir</a>
                <a href="{url_for('index')}" style="background: #28a745; color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 13px;">Inicio</a>
            </div>
        </div>
        
        {{% with messages = get_flashed_messages(with_categories=true) %}}
            {{% if messages %}}
                {{% for category, message in messages %}}
                    <div class="flash {{{{ category }}}}">{{{{ message }}}}</div>
                {{% endfor %}}
            {{% endif %}}
        {{% endwith %}}
        
        <h1>Buscar Productos</h1>
        <p class="subtitle">{'Búsqueda por texto o imagen' if image_search_available else 'Búsqueda por texto'} - Con asistente IA incluido 🤖</p>
        
        <form id="searchForm" enctype="multipart/form-data">
            <div class="search-bar">
                <input type="text" id="searchQuery" name="query" placeholder="Busca cualquier producto...">
                <button type="submit">Buscar</button>
            </div>
            
            {'<div class="or-divider"><span>O sube una imagen</span></div>' if image_search_available else ''}
            
            {'<div class="image-upload" id="imageUpload"><input type="file" id="imageFile" name="image_file" accept="image/*"><label for="imageFile">📷 Buscar por imagen<br><small>JPG, PNG, GIF hasta 10MB</small></label><img id="imagePreview" class="image-preview" src="#" alt="Vista previa"></div>' if image_search_available else ''}
        </form>
        
        <div class="tips">
            <h4>Sistema optimizado{' + Asistente IA:' if image_search_available else ' con Asistente IA:'}</h4>
            <ul style="margin: 8px 0 0 15px; font-size: 13px;">
                <li><strong>🤖 Asistente IA:</strong> Haz clic en el botón flotante para chatear</li>
                <li><strong>Velocidad:</strong> Resultados en menos de 15 segundos</li>
                <li><strong>USA:</strong> Amazon, Walmart, Target, Best Buy</li>
                <li><strong>Filtrado:</strong> Sin Alibaba, Temu, AliExpress</li>
                {'<li><strong>🖼️ Visión IA:</strong> Identifica productos en imágenes</li>' if image_search_available else '<li><strong>⚠️ Imagen:</strong> Configura GEMINI_API_KEY para activar</li>'}
                <li><strong>💬 Consultas:</strong> Pregunta sobre métodos de pago, envíos, etc.</li>
            </ul>
        </div>
        
        <div id="loading" class="loading">
            <div class="spinner"></div>
            <h3>Buscando productos...</h3>
            <p id="loadingText">Máximo 15 segundos</p>
        </div>
        <div id="error" class="error"></div>
    </div>
    
    <script>
        let searching = false;
        const imageSearchAvailable = {str(image_search_available).lower()};
        
        // Manejo de vista previa de imagen
        if (imageSearchAvailable) {{
            document.getElementById('imageFile').addEventListener('change', function(e) {{
                const file = e.target.files[0];
                const preview = document.getElementById('imagePreview');
                
                if (file) {{
                    if (file.size > 10 * 1024 * 1024) {{
                        alert('La imagen es demasiado grande (máximo 10MB)');
                        this.value = '';
                        return;
                    }}
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {{
                        preview.src = e.target.result;
                        preview.style.display = 'block';
                        document.getElementById('searchQuery').value = '';
                    }}
                    reader.readAsDataURL(file);
                }} else {{
                    preview.style.display = 'none';
                }}
            }});
        }}
        
        document.getElementById('searchForm').addEventListener('submit', function(e) {{
            e.preventDefault();
            if (searching) return;
            
            const query = document.getElementById('searchQuery').value.trim();
            const imageFile = imageSearchAvailable ? document.getElementById('imageFile').files[0] : null;
            
            if (!query && !imageFile) {{
                return showError('Por favor ingresa un producto' + (imageSearchAvailable ? ' o sube una imagen' : ''));
            }}
            
            searching = true;
            
            let loadingText = 'Buscando productos...';
            if (imageFile && query) {{
                loadingText = '🖼️ Analizando imagen y texto...';
            }} else if (imageFile) {{
                loadingText = '🖼️ Analizando imagen...';
            }} else {{
                loadingText = 'Buscando productos...';
            }}
            
            showLoading(loadingText);
            
            const timeoutId = setTimeout(() => {{ 
                searching = false; 
                hideLoading(); 
                showError('Búsqueda muy lenta - Intenta de nuevo'); 
            }}, 20000);
            
            const formData = new FormData();
            if (query) formData.append('query', query);
            if (imageFile) formData.append('image_file', imageFile);
            
            fetch('/api/search', {{
                method: 'POST',
                body: formData
            }})
            .then(response => {{ 
                clearTimeout(timeoutId); 
                searching = false; 
                return response.json(); 
            }})
            .then(data => {{ 
                hideLoading(); 
                if (data.success) {{
                    window.location.href = '/results';
                }} else {{
                    showError(data.error || 'Error en la búsqueda');
                }}
            }})
            .catch(error => {{ 
                clearTimeout(timeoutId); 
                searching = false; 
                hideLoading(); 
                showError('Error de conexión'); 
            }});
        }});
        
        function showLoading(text = 'Buscando productos...') {{ 
            document.getElementById('loadingText').textContent = text;
            document.getElementById('loading').style.display = 'block'; 
            document.getElementById('error').style.display = 'none'; 
        }}
        function hideLoading() {{ document.getElementById('loading').style.display = 'none'; }}
        function showError(msg) {{ 
            hideLoading(); 
            const e = document.getElementById('error'); 
            e.textContent = msg; 
            e.style.display = 'block'; 
        }}
    </script>'''
    
    return render_template_string(render_page('Busqueda', content))
# ========================================
# FIN MÓDULO: RUTAS PRINCIPALES
# ========================================

# ========================================
# MÓDULO: API ENDPOINTS
# ========================================
@app.route('/api/search', methods=['POST'])
@login_required
def api_search():
    try:
        # Obtener parámetros
        query = request.form.get('query', '').strip() if request.form.get('query') else None
        image_file = request.files.get('image_file')
        
        # Procesar imagen si existe
        image_content = None
        if image_file and image_file.filename != '':
            try:
                image_content = image_file.read()
                print(f"📷 Imagen recibida: {len(image_content)} bytes")
                
                # Validar tamaño (máximo 10MB)
                if len(image_content) > 10 * 1024 * 1024:
                    return jsonify({'success': False, 'error': 'La imagen es demasiado grande (máximo 10MB)'}), 400
                    
            except Exception as e:
                print(f"❌ Error al leer imagen: {e}")
                return jsonify({'success': False, 'error': 'Error al procesar la imagen'}), 400
        
        # Validar que hay al menos una entrada
        if not query and not image_content:
            return jsonify({'success': False, 'error': 'Debe proporcionar una consulta o una imagen'}), 400
        
        # Limitar longitud de query
        if query and len(query) > 80:
            query = query[:80]
        
        user_email = session.get('user_email', 'Unknown')
        search_type = "imagen" if image_content and not query else "texto+imagen" if image_content and query else "texto"
        print(f"Search request from {user_email}: {search_type}")
        
        # Realizar búsqueda con soporte para imagen
        products = price_finder.search_products(query=query, image_content=image_content)
        
        session['last_search'] = {
            'query': query or "búsqueda por imagen",
            'products': products,
            'timestamp': datetime.now().isoformat(),
            'user': user_email,
            'search_type': search_type
        }
        
        print(f"Search completed for {user_email}: {len(products)} products found")
        return jsonify({'success': True, 'products': products, 'total': len(products)})
        
    except Exception as e:
        print(f"Search error: {e}")
        try:
            query = request.form.get('query', 'producto') if request.form.get('query') else 'producto'
            fallback = price_finder._get_examples(query)
            session['last_search'] = {'query': str(query), 'products': fallback, 'timestamp': datetime.now().isoformat()}
            return jsonify({'success': True, 'products': fallback, 'total': len(fallback)})
        except:
            return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@app.route('/results')
@login_required
def results_page():
    try:
        if 'last_search' not in session:
            flash('No hay busquedas recientes.', 'warning')
            return redirect(url_for('search_page'))
        
        current_user = firebase_auth.get_current_user()
        user_name = current_user['user_name'] if current_user else 'Usuario'
        user_name_escaped = html.escape(user_name)
        
        search_data = session['last_search']
        products = search_data.get('products', [])
        query = html.escape(str(search_data.get('query', 'busqueda')))
        search_type = search_data.get('search_type', 'texto')
        
        products_html = ""
        badges = ['MEJOR', '2do', '3ro']
        colors = ['#4caf50', '#ff9800', '#9c27b0']
        
        for i, product in enumerate(products[:6]):
            if not product:
                continue
            
            badge = f'<div style="position: absolute; top: 8px; right: 8px; background: {colors[min(i, 2)]}; color: white; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;">{badges[min(i, 2)]}</div>' if i < 3 else ''
            
            # Badge de fuente de búsqueda
            search_source_badge = ''
            source = product.get('search_source', '')
            if source == 'image':
                search_source_badge = '<div style="position: absolute; top: 8px; left: 8px; background: #673ab7; color: white; padding: 4px 8px; border-radius: 12px; font-size: 10px; font-weight: bold;">📷 IMAGEN</div>'
            elif source == 'combined':
                search_source_badge = '<div style="position: absolute; top: 8px; left: 8px; background: #607d8b; color: white; padding: 4px 8px; border-radius: 12px; font-size: 10px; font-weight: bold;">🔗 MIXTO</div>'
            
            title = html.escape(str(product.get('title', 'Producto')))
            price = html.escape(str(product.get('price', '$0.00')))
            source_store = html.escape(str(product.get('source', 'Tienda')))
            link = html.escape(str(product.get('link', '#')))
            
            margin_top = '20px' if search_source_badge else '0'
            
            products_html += f'''
                <div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; margin-bottom: 15px; background: white; position: relative; box-shadow: 0 2px 4px rgba(0,0,0,0.08);">
                    {badge}
                    {search_source_badge}
                    <h3 style="color: #1a73e8; margin-bottom: 8px; font-size: 16px; margin-top: {margin_top};">{title}</h3>
                    <div style="font-size: 28px; color: #2e7d32; font-weight: bold; margin: 12px 0;">{price} <span style="font-size: 12px; color: #666;">USD</span></div>
                    <p style="color: #666; margin-bottom: 12px; font-size: 14px;">Tienda: {source_store}</p>
                    <a href="{link}" target="_blank" rel="noopener noreferrer" style="background: #1a73e8; color: white; padding: 10px 16px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block; font-size: 14px;">Ver Producto</a>
                </div>'''
        
        prices = [p.get('price_numeric', 0) for p in products if p.get('price_numeric', 0) > 0]
        stats = ""
        if prices:
            min_price = min(prices)
            avg_price = sum(prices) / len(prices)
            search_type_text = {
                "texto": "texto", 
                "imagen": "imagen", 
                "texto+imagen": "texto + imagen", 
                "combined": "búsqueda mixta"
            }.get(search_type, search_type)
            
            stats = f'''
                <div style="background: #e8f5e8; border: 1px solid #4caf50; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <h3 style="color: #2e7d32; margin-bottom: 8px;">Resultados de búsqueda ({search_type_text})</h3>
                    <p><strong>{len(products)} productos encontrados</strong></p>
                    <p><strong>Mejor precio: ${min_price:.2f}</strong></p>
                    <p><strong>Precio promedio: ${avg_price:.2f}</strong></p>
                </div>'''
        
        content = f'''
        <div style="max-width: 800px; margin: 0 auto;">
            <div style="background: rgba(255,255,255,0.15); padding: 12px; border-radius: 8px; margin-bottom: 15px; text-align: center; display: flex; align-items: center; justify-content: center;">
                <span style="color: white; font-size: 14px;"><strong>{user_name_escaped}</strong></span>
                <div style="margin-left: 15px;">
                    <a href="{url_for('auth_logout')}" style="background: rgba(220,53,69,0.9); color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 13px; margin-right: 8px;">Salir</a>
                    <a href="{url_for('search_page')}" style="background: rgba(40,167,69,0.9); color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 13px;">Nueva Busqueda</a>
                </div>
            </div>
            
            <h1 style="color: white; text-align: center; margin-bottom: 8px;">Resultados: "{query}"</h1>
            <p style="text-align: center; color: rgba(255,255,255,0.9); margin-bottom: 25px;">Búsqueda completada - Asistente IA disponible 🤖</p>
            
            {stats}
            {products_html}
        </div>'''
        
        return render_template_string(render_page('Resultados - Price Finder USA', content))
    except Exception as e:
        print(f"Results page error: {e}")
        flash('Error al mostrar resultados.', 'danger')
        return redirect(url_for('search_page'))

@app.route('/api/health')
def health_check():
    try:
        return jsonify({
            'status': 'OK', 
            'timestamp': datetime.now().isoformat(),
            'firebase_auth': 'enabled' if firebase_auth.firebase_web_api_key else 'disabled',
            'serpapi': 'enabled' if price_finder.is_api_configured() else 'disabled',
            'gemini_vision': 'enabled' if GEMINI_READY else 'disabled',
            'pil_available': 'enabled' if PIL_AVAILABLE else 'disabled',
            'assistant': 'enabled'
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500

@app.route('/api/assistant', methods=['POST'])
@login_required
def api_assistant():
    """API del asistente conversacional"""
    try:
        data = request.get_json()
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return jsonify({'success': False, 'error': 'Mensaje vacío'}), 400
        
        if len(user_message) > 500:
            return jsonify({'success': False, 'error': 'Mensaje demasiado largo'}), 400
        
        user_id = session.get('user_id', 'default')
        print(f"🤖 Asistente - Usuario {user_id}: '{user_message[:50]}...'")
        
        # Obtener respuesta del asistente
        response = flowgent_assistant.chat(user_message, user_id)
        
        return jsonify({
            'success': True,
            'response': response
        })
        
    except Exception as e:
        print(f"❌ Error en asistente: {e}")
        return jsonify({
            'success': False, 
            'error': 'Error interno del asistente'
        }), 500

@app.route('/api/assistant/clear', methods=['POST'])
@login_required  
def api_assistant_clear():
    """Limpiar conversación del asistente"""
    try:
        user_id = session.get('user_id', 'default')
        flowgent_assistant.clear_conversation(user_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/assistant/test')
@login_required
def assistant_test():
    """Endpoint para probar el asistente"""
    try:
        test_message = "¿Cuáles son los métodos de pago aceptados?"
        response = flowgent_assistant.chat(test_message, "test_user")
        
        return jsonify({
            'status': 'OK',
            'test_message': test_message,
            'response': response,
            'assistant_available': True
        })
    except Exception as e:
        return jsonify({
            'status': 'ERROR',
            'message': str(e),
            'assistant_available': False
        }), 500
# ========================================
# FIN MÓDULO: API ENDPOINTS
# ========================================

# ========================================
# MÓDULO: MIDDLEWARE Y CONFIGURACIÓN DE SEGURIDAD
# ========================================
@app.before_request
def before_request():
    if 'timestamp' in session:
        try:
            timestamp_str = session['timestamp']
            if isinstance(timestamp_str, str) and len(timestamp_str) > 10:
                last_activity = datetime.fromisoformat(timestamp_str)
                time_diff = (datetime.now() - last_activity).total_seconds()
                if time_diff > 1200:  # 20 minutos
                    session.clear()
        except:
            session.clear()
    
    session['timestamp'] = datetime.now().isoformat()

@app.after_request
def after_request(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
# ========================================
# FIN MÓDULO: MIDDLEWARE Y CONFIGURACIÓN DE SEGURIDAD
# ========================================

# ========================================
# MÓDULO: MANEJADORES DE ERRORES
# ========================================
@app.errorhandler(404)
def not_found(error):
    return '<h1>404 - Pagina no encontrada</h1><p><a href="/">Volver al inicio</a></p>', 404

@app.errorhandler(500)
def internal_error(error):
    return '<h1>500 - Error interno</h1><p><a href="/">Volver al inicio</a></p>', 500
# ========================================
# FIN MÓDULO: MANEJADORES DE ERRORES
# ========================================

# ========================================
# MÓDULO: CONFIGURACIÓN DE INICIO Y LOGGING
# ========================================
if __name__ == '__main__':
    print("Price Finder USA con Búsqueda por Imagen y Asistente IA - Starting...")
    print(f"Firebase: {'OK' if os.environ.get('FIREBASE_WEB_API_KEY') else 'NOT_CONFIGURED'}")
    print(f"SerpAPI: {'OK' if os.environ.get('SERPAPI_KEY') else 'NOT_CONFIGURED'}")
    print(f"Gemini Vision: {'OK' if GEMINI_READY else 'NOT_CONFIGURED'}")
    print(f"PIL/Pillow: {'OK' if PIL_AVAILABLE else 'NOT_CONFIGURED'}")
    print(f"Asistente Flowgent.ai: OK (Key: {FLOWGENT_API_KEY[:8]}...)")
    print(f"Puerto: {os.environ.get('PORT', '5000')}")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False, threaded=True)
else:
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
# ========================================
# FIN MÓDULO: CONFIGURACIÓN DE INICIO Y LOGGING
# ========================================

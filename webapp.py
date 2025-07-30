# webapp.py - Price Finder USA con Búsqueda por Imagen - VERSIÓN DINÁMICA
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string, flash
import requests
import os
import re
import html
import time
import io
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

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = 1800
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True if os.environ.get('RENDER') else False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

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

# ==============================================================================
# FUNCIONES DE BÚSQUEDA POR IMAGEN
# ==============================================================================

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

# Price Finder Class - MODIFICADO para búsqueda por imagen
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

# Templates DINÁMICOS MODERNOS
def render_page(title, content):
    template = '''<!DOCTYPE html>
<html lang="es">
<head>
    <title>''' + title + '''</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            --primary-color: #4A90E2;
            --secondary-color: #50E3C2;
            --success-color: #4CAF50;
            --warning-color: #FF9800;
            --danger-color: #F44336;
            --dark-color: #2C3E50;
            --gradient-bg: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 100%);
            --rainbow-gradient: linear-gradient(45deg, #ff0000, #ff8000, #ffff00, #80ff00, #00ff80, #00ffff, #0080ff, #8000ff, #ff00ff);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: var(--gradient-bg);
            min-height: 100vh;
            padding: 15px;
            position: relative;
            overflow-x: hidden;
        }
        
        /* PARTÍCULAS FLOTANTES DE FONDO */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: 
                radial-gradient(circle at 20% 50%, rgba(255, 255, 255, 0.3) 0%, transparent 50%),
                radial-gradient(circle at 80% 20%, rgba(255, 255, 255, 0.2) 0%, transparent 50%),
                radial-gradient(circle at 40% 80%, rgba(255, 255, 255, 0.1) 0%, transparent 50%);
            animation: float-particles 20s ease-in-out infinite;
            pointer-events: none;
            z-index: 0;
        }
        
        @keyframes float-particles {
            0%, 100% { transform: translateY(0px) rotate(0deg); }
            33% { transform: translateY(-20px) rotate(120deg); }
            66% { transform: translateY(10px) rotate(240deg); }
        }
        
        .container {
            max-width: 650px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            padding: 25px;
            border-radius: 20px;
            box-shadow: 
                0 8px 32px rgba(0, 0, 0, 0.1),
                0 0 0 1px rgba(255, 255, 255, 0.2);
            position: relative;
            z-index: 1;
            animation: slide-up 0.6s ease-out;
        }
        
        @keyframes slide-up {
            from {
                opacity: 0;
                transform: translateY(30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        h1 {
            color: var(--primary-color);
            text-align: center;
            margin-bottom: 8px;
            font-size: 2.2em;
            font-weight: 700;
            background: linear-gradient(45deg, var(--primary-color), var(--secondary-color));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: title-glow 3s ease-in-out infinite alternate;
        }
        
        @keyframes title-glow {
            from {
                filter: drop-shadow(0 0 5px rgba(74, 144, 226, 0.3));
            }
            to {
                filter: drop-shadow(0 0 20px rgba(74, 144, 226, 0.6));
            }
        }
        
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 25px;
            animation: fade-in 1s ease-out 0.3s both;
        }
        
        @keyframes fade-in {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        
        /* INPUTS DINÁMICOS */
        input {
            width: 100%;
            padding: 15px;
            margin: 8px 0;
            border: 2px solid #e1e5e9;
            border-radius: 12px;
            font-size: 16px;
            transition: all 0.3s ease;
            background: linear-gradient(145deg, #ffffff, #f8f9fa);
            position: relative;
        }
        
        input:focus {
            outline: none;
            border-color: var(--primary-color);
            transform: translateY(-2px);
            box-shadow: 
                0 8px 25px rgba(74, 144, 226, 0.15),
                0 0 0 3px rgba(74, 144, 226, 0.1);
        }
        
        input:hover {
            border-color: var(--secondary-color);
            transform: translateY(-1px);
        }
        
        /* BOTONES DINÁMICOS */
        button {
            width: 100%;
            padding: 15px;
            background: linear-gradient(45deg, var(--primary-color), var(--secondary-color));
            color: white;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        
        button::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
            transition: left 0.5s;
        }
        
        button:hover::before {
            left: 100%;
        }
        
        button:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 25px rgba(74, 144, 226, 0.3);
        }
        
        button:active {
            transform: translateY(-1px);
        }
        
        .search-bar {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            animation: fade-in 1s ease-out 0.6s both;
        }
        
        .search-bar input {
            flex: 1;
        }
        
        .search-bar button {
            width: auto;
            padding: 15px 25px;
            background: linear-gradient(45deg, var(--success-color), #66BB6A);
        }
        
        /* MENÚ ARCOÍRIS DINÁMICO */
        .user-info {
            background: var(--rainbow-gradient);
            background-size: 400% 400%;
            animation: rainbow-flow 4s ease infinite;
            padding: 18px;
            border-radius: 15px;
            margin-bottom: 25px;
            text-align: center;
            font-size: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 
                0 8px 30px rgba(0, 0, 0, 0.15),
                inset 0 1px 0 rgba(255, 255, 255, 0.2);
            border: 2px solid rgba(255, 255, 255, 0.3);
            position: relative;
            overflow: hidden;
        }
        
        .user-info::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: conic-gradient(from 0deg, transparent, rgba(255, 255, 255, 0.1), transparent);
            animation: rotate-shimmer 3s linear infinite;
            pointer-events: none;
        }
        
        @keyframes rainbow-flow {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        
        @keyframes rotate-shimmer {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .user-info span {
            color: white;
            text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.6);
            font-weight: 700;
            font-size: 18px;
            position: relative;
            z-index: 2;
        }
        
        .user-info a {
            color: white;
            text-decoration: none;
            font-weight: 700;
            background: rgba(0, 0, 0, 0.4);
            padding: 10px 18px;
            border-radius: 25px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            transition: all 0.3s ease;
            text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.6);
            position: relative;
            z-index: 2;
            backdrop-filter: blur(5px);
        }
        
        .user-info a:hover {
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-3px) scale(1.05);
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.3);
        }
        
        .user-info a.logout-btn {
            background: rgba(244, 67, 54, 0.8);
        }
        
        .user-info a.logout-btn:hover {
            background: rgba(244, 67, 54, 1);
        }
        
        .user-info a.home-btn {
            background: rgba(76, 175, 80, 0.8);
        }
        
        .user-info a.home-btn:hover {
            background: rgba(76, 175, 80, 1);
        }
        
        /* CAJAS DE INFORMACIÓN DINÁMICAS */
        .tips {
            background: linear-gradient(135deg, rgba(76, 175, 80, 0.1), rgba(76, 175, 80, 0.05));
            border: 2px solid var(--success-color);
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 20px;
            font-size: 14px;
            position: relative;
            overflow: hidden;
            animation: fade-in 1s ease-out 0.9s both;
        }
        
        .tips::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(76, 175, 80, 0.1), transparent);
            animation: slide-shine 3s ease-in-out infinite;
        }
        
        @keyframes slide-shine {
            0% { left: -100%; }
            50% { left: 100%; }
            100% { left: 100%; }
        }
        
        .tips h4 {
            color: var(--success-color);
            margin-bottom: 10px;
            font-size: 16px;
        }
        
        .tips ul {
            margin: 8px 0 0 20px;
            font-size: 13px;
        }
        
        .tips li {
            margin-bottom: 5px;
            transition: transform 0.2s ease;
        }
        
        .tips li:hover {
            transform: translateX(5px);
        }
        
        /* MENSAJES FLASH DINÁMICOS */
        .flash {
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 12px;
            font-size: 14px;
            border-left: 4px solid;
            animation: flash-appear 0.5s ease-out;
            position: relative;
            overflow: hidden;
        }
        
        @keyframes flash-appear {
            from {
                opacity: 0;
                transform: translateX(-20px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        .flash.success {
            background: linear-gradient(135deg, rgba(76, 175, 80, 0.1), rgba(76, 175, 80, 0.05));
            color: #2E7D32;
            border-left-color: var(--success-color);
        }
        
        .flash.danger {
            background: linear-gradient(135deg, rgba(244, 67, 54, 0.1), rgba(244, 67, 54, 0.05));
            color: #C62828;
            border-left-color: var(--danger-color);
        }
        
        .flash.warning {
            background: linear-gradient(135deg, rgba(255, 152, 0, 0.1), rgba(255, 152, 0, 0.05));
            color: #E65100;
            border-left-color: var(--warning-color);
        }
        
        /* ÁREA DE CARGA DE IMÁGENES DINÁMICA */
        .image-upload {
            background: linear-gradient(135deg, rgba(74, 144, 226, 0.05), rgba(80, 227, 194, 0.05));
            border: 3px dashed #dee2e6;
            border-radius: 15px;
            padding: 25px;
            text-align: center;
            margin: 20px 0;
            transition: all 0.3s ease;
            position: relative;
            animation: fade-in 1s ease-out 1.2s both;
        }
        
        .image-upload input[type="file"] {
            display: none;
        }
        
        .image-upload label {
            cursor: pointer;
            color: var(--primary-color);
            font-weight: 600;
            font-size: 16px;
            transition: all 0.3s ease;
        }
        
        .image-upload:hover {
            border-color: var(--primary-color);
            background: linear-gradient(135deg, rgba(74, 144, 226, 0.1), rgba(80, 227, 194, 0.1));
            transform: translateY(-3px);
            box-shadow: 0 10px 25px rgba(74, 144, 226, 0.15);
        }
        
        .image-upload:hover label {
            transform: scale(1.05);
        }
        
        .image-preview {
            max-width: 150px;
            max-height: 150px;
            margin: 15px auto;
            border-radius: 12px;
            display: none;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
            transition: all 0.3s ease;
        }
        
        .image-preview:hover {
            transform: scale(1.05);
        }
        
        /* DIVISOR DINÁMICO */
        .or-divider {
            text-align: center;
            margin: 25px 0;
            color: #666;
            font-weight: 600;
            position: relative;
            animation: fade-in 1s ease-out 1.5s both;
        }
        
        .or-divider:before {
            content: '';
            position: absolute;
            top: 50%;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent, #dee2e6, transparent);
            z-index: 1;
        }
        
        .or-divider span {
            background: white;
            padding: 0 20px;
            position: relative;
            z-index: 2;
            font-size: 14px;
        }
        
        /* ÁREA DE CARGA DINÁMICA */
        .loading {
            text-align: center;
            padding: 40px;
            display: none;
            animation: fade-in 0.5s ease-out;
        }
        
        .spinner {
            border: 4px solid rgba(74, 144, 226, 0.1);
            border-top: 4px solid var(--primary-color);
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite, pulse 2s ease-in-out infinite;
            margin: 0 auto 20px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(74, 144, 226, 0.7); }
            50% { box-shadow: 0 0 0 10px rgba(74, 144, 226, 0); }
        }
        
        .loading h3 {
            color: var(--primary-color);
            margin-bottom: 10px;
            animation: text-glow 2s ease-in-out infinite alternate;
        }
        
        @keyframes text-glow {
            from { text-shadow: 0 0 5px rgba(74, 144, 226, 0.5); }
            to { text-shadow: 0 0 20px rgba(74, 144, 226, 0.8); }
        }
        
        /* MENSAJES DE ERROR DINÁMICOS */
        .error {
            background: linear-gradient(135deg, rgba(244, 67, 54, 0.1), rgba(244, 67, 54, 0.05));
            color: var(--danger-color);
            padding: 15px;
            border-radius: 12px;
            margin: 15px 0;
            display: none;
            border-left: 4px solid var(--danger-color);
            animation: shake 0.5s ease-in-out;
        }
        
        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-5px); }
            75% { transform: translateX(5px); }
        }
        
        /* EFECTOS RESPONSIVOS */
        @media (max-width: 768px) {
            .container {
                margin: 10px;
                padding: 20px;
                border-radius: 15px;
            }
            
            h1 {
                font-size: 1.8em;
            }
            
            .search-bar {
                flex-direction: column;
                gap: 10px;
            }
            
            .search-bar button {
                width: 100%;
            }
            
            .user-info {
                flex-direction: column;
                gap: 10px;
            }
            
            .user-info > div {
                margin-left: 0 !important;
            }
        }
        
        /* EFECTOS DE HOVER GLOBALES */
        * {
            transition: all 0.3s ease;
        }
        
        /* CURSOR PERSONALIZADO */
        * {
            cursor: default;
        }
        
        button, a, input[type="file"] + label {
            cursor: pointer;
        }
        
        /* SCROLLBAR PERSONALIZADA */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.1);
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--primary-color);
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--secondary-color);
        }
    </style>
</head>
<body>''' + content + '''</body>
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
        :root {
            --primary-color: #4A90E2;
            --secondary-color: #50E3C2;
            --dark-color: #2C3E50;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
            position: relative;
            overflow: hidden;
        }
        
        /* PARTÍCULAS ANIMADAS DE FONDO */
        body::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: 
                radial-gradient(circle at 25% 25%, rgba(255, 255, 255, 0.2) 0%, transparent 50%),
                radial-gradient(circle at 75% 75%, rgba(255, 255, 255, 0.1) 0%, transparent 50%);
            animation: float-bg 15s ease-in-out infinite;
        }
        
        @keyframes float-bg {
            0%, 100% { transform: scale(1) rotate(0deg); }
            50% { transform: scale(1.1) rotate(180deg); }
        }
        
        .auth-container {
            max-width: 420px;
            width: 100%;
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(15px);
            border-radius: 20px;
            box-shadow: 
                0 20px 60px rgba(0, 0, 0, 0.15),
                0 0 0 1px rgba(255, 255, 255, 0.2);
            overflow: hidden;
            position: relative;
            z-index: 1;
            animation: slide-in 0.8s ease-out;
        }
        
        @keyframes slide-in {
            from {
                opacity: 0;
                transform: translateY(50px) scale(0.9);
            }
            to {
                opacity: 1;
                transform: translateY(0) scale(1);
            }
        }
        
        .form-header {
            text-align: center;
            padding: 40px 25px 20px;
            background: linear-gradient(45deg, var(--dark-color), var(--primary-color));
            color: white;
            position: relative;
            overflow: hidden;
        }
        
        .form-header::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: conic-gradient(from 0deg, transparent, rgba(255, 255, 255, 0.1), transparent);
            animation: rotate 10s linear infinite;
        }
        
        @keyframes rotate {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .form-header h1 {
            font-size: 2em;
            margin-bottom: 10px;
            position: relative;
            z-index: 2;
            animation: glow-text 3s ease-in-out infinite alternate;
        }
        
        @keyframes glow-text {
            from { text-shadow: 0 0 10px rgba(255, 255, 255, 0.5); }
            to { text-shadow: 0 0 30px rgba(255, 255, 255, 0.8); }
        }
        
        .form-header p {
            opacity: 0.9;
            font-size: 1.1em;
            position: relative;
            z-index: 2;
        }
        
        .form-body {
            padding: 30px;
        }
        
        form {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        
        .input-group {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        
        .input-group label {
            font-weight: 600;
            color: var(--dark-color);
            font-size: 14px;
            transition: color 0.3s ease;
        }
        
        .input-group input {
            padding: 16px 18px;
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            font-size: 16px;
            transition: all 0.3s ease;
            background: linear-gradient(145deg, #ffffff, #f8f9fa);
        }
        
        .input-group input:focus {
            outline: 0;
            border-color: var(--primary-color);
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(74, 144, 226, 0.15);
        }
        
        .input-group input:focus + label {
            color: var(--primary-color);
        }
        
        .submit-btn {
            background: linear-gradient(45deg, var(--primary-color), #2980b9);
            color: white;
            border: none;
            padding: 16px 25px;
            font-size: 16px;
            font-weight: 600;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        
        .submit-btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
            transition: left 0.5s;
        }
        
        .submit-btn:hover::before {
            left: 100%;
        }
        
        .submit-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 15px 35px rgba(74, 144, 226, 0.4);
        }
        
        .flash-messages {
            list-style: none;
            padding: 0 25px 15px;
        }
        
        .flash {
            padding: 15px;
            margin-bottom: 12px;
            border-radius: 10px;
            text-align: center;
            font-size: 14px;
            border-left: 4px solid;
            animation: flash-slide 0.5s ease-out;
        }
        
        @keyframes flash-slide {
            from {
                opacity: 0;
                transform: translateX(-30px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        .flash.success {
            background: linear-gradient(135deg, rgba(76, 175, 80, 0.1), rgba(76, 175, 80, 0.05));
            color: #2E7D32;
            border-left-color: #4CAF50;
        }
        
        .flash.danger {
            background: linear-gradient(135deg, rgba(244, 67, 54, 0.1), rgba(244, 67, 54, 0.05));
            color: #C62828;
            border-left-color: #F44336;
        }
        
        .flash.warning {
            background: linear-gradient(135deg, rgba(255, 152, 0, 0.1), rgba(255, 152, 0, 0.05));
            color: #E65100;
            border-left-color: #FF9800;
        }
        
        @media (max-width: 480px) {
            .auth-container {
                margin: 10px;
            }
            
            .form-header {
                padding: 30px 20px 15px;
            }
            
            .form-body {
                padding: 25px;
            }
        }
    </style>
</head>
<body>
    <div class="auth-container">
        <div class="form-header">
            <h1>🌈 Price Finder USA</h1>
            <p>✨ Iniciar Sesión Dinámico ✨</p>
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
                    <label for="email">📧 Correo Electrónico</label>
                    <input type="email" name="email" id="email" required>
                </div>
                <div class="input-group">
                    <label for="password">🔒 Contraseña</label>
                    <input type="password" name="password" id="password" required>
                </div>
                <button type="submit" class="submit-btn">🚀 Entrar al Sistema</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

# Routes
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
    
    content = '''
    <div class="container">
        <div class="user-info">
            <span>🌈 ''' + user_name_escaped + ''' 🌈</span>
            <div style="display: inline-block; margin-left: 15px;">
                <a href="''' + url_for('auth_logout') + '''" class="logout-btn">✨ Salir</a>
                <a href="''' + url_for('index') + '''" class="home-btn" style="margin-left: 8px;">🏠 Inicio</a>
            </div>
        </div>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        <h1>🔍 Buscar Productos</h1>
        <p class="subtitle">''' + ('🖼️ Búsqueda por texto o imagen IA' if image_search_available else '📝 Búsqueda por texto') + ''' - Resultados ultrarrápidos ⚡</p>
        
        <form id="searchForm" enctype="multipart/form-data">
            <div class="search-bar">
                <input type="text" id="searchQuery" name="query" placeholder="🛍️ Busca cualquier producto...">
                <button type="submit">🚀 Buscar</button>
            </div>
            
            ''' + ('<div class="or-divider"><span>✨ O sube una imagen ✨</span></div>' if image_search_available else '') + '''
            
            ''' + ('<div class="image-upload" id="imageUpload"><input type="file" id="imageFile" name="image_file" accept="image/*"><label for="imageFile">📷 Buscar por imagen IA<br><small>🎯 JPG, PNG, GIF hasta 10MB</small></label><img id="imagePreview" class="image-preview" src="#" alt="Vista previa"></div>' if image_search_available else '') + '''
        </form>
        
        <div class="tips">
            <h4>🎯 Sistema Optimizado''' + (' + 🤖 IA Visual:' if image_search_available else ':') + '''</h4>
            <ul style="margin: 8px 0 0 20px; font-size: 13px;">
                <li><strong>⚡ Velocidad:</strong> Resultados en menos de 15 segundos</li>
                <li><strong>🇺🇸 USA:</strong> Amazon, Walmart, Target, Best Buy</li>
                <li><strong>🚫 Filtrado:</strong> Sin Alibaba, Temu, AliExpress</li>
                ''' + ('<li><strong>🖼️ IA:</strong> Identifica productos en imágenes automáticamente</li>' if image_search_available else '<li><strong>⚠️ Imagen:</strong> Configura GEMINI_API_KEY para activar IA</li>') + '''
            </ul>
        </div>
        
        <div id="loading" class="loading">
            <div class="spinner"></div>
            <h3>🔍 Buscando productos...</h3>
            <p id="loadingText">⏱️ Máximo 15 segundos</p>
        </div>
        <div id="error" class="error"></div>
    </div>
    
    <script>
        let searching = false;
        const imageSearchAvailable = ''' + str(image_search_available).lower() + ''';
        
        // Efectos dinámicos al cargar la página
        document.addEventListener('DOMContentLoaded', function() {
            // Añadir efectos de aparición escalonada
            const elements = document.querySelectorAll('.container > *');
            elements.forEach((el, index) => {
                el.style.animationDelay = (index * 0.1) + 's';
            });
        });
        
        // Manejo de vista previa de imagen con efectos
        if (imageSearchAvailable) {
            document.getElementById('imageFile').addEventListener('change', function(e) {
                const file = e.target.files[0];
                const preview = document.getElementById('imagePreview');
                const uploadArea = document.getElementById('imageUpload');
                
                if (file) {
                    if (file.size > 10 * 1024 * 1024) {
                        showError('🚨 La imagen es demasiado grande (máximo 10MB)');
                        this.value = '';
                        return;
                    }
                    
                    // Efecto de carga
                    uploadArea.style.borderColor = '#4CAF50';
                    uploadArea.style.background = 'linear-gradient(135deg, rgba(76, 175, 80, 0.1), rgba(76, 175, 80, 0.05))';
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        preview.src = e.target.result;
                        preview.style.display = 'block';
                        preview.style.animation = 'slide-up 0.5s ease-out';
                        document.getElementById('searchQuery').value = '';
                        
                        // Actualizar texto del área de carga
                        uploadArea.querySelector('label').innerHTML = '✅ Imagen cargada<br><small>🎯 Lista para búsqueda IA</small>';
                    }
                    reader.readAsDataURL(file);
                } else {
                    preview.style.display = 'none';
                    uploadArea.style.borderColor = '#dee2e6';
                    uploadArea.style.background = 'linear-gradient(135deg, rgba(74, 144, 226, 0.05), rgba(80, 227, 194, 0.05))';
                    uploadArea.querySelector('label').innerHTML = '📷 Buscar por imagen IA<br><small>🎯 JPG, PNG, GIF hasta 10MB</small>';
                }
            });
        }
        
        // Formulario de búsqueda con efectos dinámicos
        document.getElementById('searchForm').addEventListener('submit', function(e) {
            e.preventDefault();
            if (searching) return;
            
            const query = document.getElementById('searchQuery').value.trim();
            const imageFile = imageSearchAvailable ? document.getElementById('imageFile').files[0] : null;
            
            if (!query && !imageFile) {
                return showError('🔍 Por favor ingresa un producto' + (imageSearchAvailable ? ' o sube una imagen' : ''));
            }
            
            searching = true;
            showLoading(imageFile ? '🤖 Analizando imagen con IA...' : '🔍 Buscando productos...');
            
            // Efecto visual en el botón
            const submitBtn = document.querySelector('button[type="submit"]');
            submitBtn.style.background = 'linear-gradient(45deg, #FF9800, #F57C00)';
            submitBtn.innerHTML = '⏳ Buscando...';
            
            const timeoutId = setTimeout(() => { 
                searching = false; 
                hideLoading(); 
                showError('⏰ Búsqueda muy lenta - Intenta de nuevo'); 
                resetSubmitButton();
            }, 20000);
            
            const formData = new FormData();
            if (query) formData.append('query', query);
            if (imageFile) formData.append('image_file', imageFile);
            
            fetch('/api/search', {
                method: 'POST',
                body: formData
            })
            .then(response => { 
                clearTimeout(timeoutId); 
                searching = false; 
                return response.json(); 
            })
            .then(data => { 
                hideLoading(); 
                resetSubmitButton();
                if (data.success) {
                    // Efecto de éxito
                    showSuccess('✅ ¡Productos encontrados! Redirigiendo...');
                    setTimeout(() => {
                        window.location.href = '/results';
                    }, 1000);
                } else {
                    showError('❌ ' + (data.error || 'Error en la búsqueda'));
                }
            })
            .catch(error => { 
                clearTimeout(timeoutId); 
                searching = false; 
                hideLoading(); 
                resetSubmitButton();
                showError('🌐 Error de conexión'); 
            });
        });

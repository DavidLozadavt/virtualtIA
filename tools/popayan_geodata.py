"""
tools/popayan_geodata.py — Base de conocimiento geográfico completo de Popayán, Cauca, Colombia.

Contiene:
  1. Coordenadas de referencia para TODOS los barrios por comuna (9 comunas)
  2. Puntos de interés (landmarks, centros comerciales, hospitales, etc.)
  3. Corregimientos y zonas rurales cercanas
  4. Sistema de geocodificación local como fallback cuando Nominatim falla
  5. Normalización avanzada de direcciones colombianas
  6. Validación de existencia de ubicaciones en Popayán

Fuentes: OpenStreetMap, Colombia en Mapas, Alcaldía de Popayán (POT),
          Google Maps, datos cartográficos WGS84.

Coordenadas: Todas en formato (latitud, longitud) decimal WGS84.
"""

import logging
import math
import re
import unicodedata
from typing import Optional, Tuple, Dict, List

from tools.shared.utils import normalize_text as _normalize_shared, haversine as _haversine_shared

logger = logging.getLogger("lyra.tools.popayan_geodata")

# ── Ciudad de Popayán: centro y bounding box ──────────────────────────────────

POPAYAN_CENTER = (2.4419, -76.6063)  # Parque Caldas
POPAYAN_BBOX = {
    "min_lat": 2.32,
    "max_lat": 2.58,
    "min_lng": -76.82,
    "max_lng": -76.42,
}

# ── Ejes de referencia para cálculo de coordenadas por nomenclatura ───────────
# En Popayán:
#   - Calles: orientación oriente-occidente, numeración crece hacia el norte
#   - Carreras: orientación norte-sur, numeración crece hacia el oriente
#
# Punto de origen de nomenclatura (aprox. Calle 1 / Carrera 1):
#   Lat: ~2.430, Lng: ~-76.620 (zona sur-occidental del centro)
#
# Escala: ~1 cuadra ≈ 90-110m
#   En latitud: 1° ≈ 111,320 m → 100m ≈ 0.000898°
#   En longitud a lat 2.44°: 1° ≈ 111,200 m → 100m ≈ 0.000899°

NOMENCLATURA_ORIGIN = (2.4250, -76.6200)   # Calle 0 / Carrera 0 aprox.
BLOCK_LAT = 0.00090    # ~100m por cuadra en latitud (norte-sur)
BLOCK_LNG = 0.00090    # ~100m por cuadra en longitud (este-oeste)

# ── BARRIOS POR COMUNA ────────────────────────────────────────────────────────
# Cada barrio: (lat, lng) centroide aproximado
# Fuentes: Google Maps, OpenStreetMap, Colombia en Mapas, Mapcarta

BARRIOS_COMUNA_1: Dict[str, Tuple[float, float]] = {
    "Modelo": (2.4560, -76.6140),
    "Loma Linda": (2.4580, -76.6120),
    "Prados del Norte": (2.4600, -76.6080),
    "La Cabaña": (2.4570, -76.6100),
    "Santa Clara": (2.4540, -76.6150),
    "Casas Fiscales": (2.4530, -76.6130),
    "Nueva Granada": (2.4550, -76.6060),
    "Machángara": (2.4590, -76.6050),
    "La Playa": (2.4610, -76.6040),
    "Campamento": (2.4520, -76.6170),
    "Puerta de Hierro": (2.4600, -76.6020),
    "Pubenza": (2.4570, -76.6070),
    "Antonio Nariño": (2.4585, -76.6030),
    "Villa Paula": (2.4595, -76.6010),
    "Campobello": (2.4605, -76.5990),
    "El Recuerdo": (2.4615, -76.5970),
    "La Villa": (2.4625, -76.5960),
    "Bloques de Pubenza": (2.4575, -76.6075),
    "Belalcázar": (2.4545, -76.6110),
    "Los Laureles": (2.4555, -76.6090),
    "Los Rosales": (2.4535, -76.6065),
    "Alcalá": (2.4565, -76.6050),
    "Monterrosales": (2.4620, -76.5980),
    "Fancal": (2.4630, -76.5950),
    "Ciudad Capri": (2.4640, -76.5940),
    "Puerta del Sol": (2.4650, -76.5930),
}

BARRIOS_COMUNA_2: Dict[str, Tuple[float, float]] = {
    "Pino Pardo": (2.4700, -76.5900),
    "Balcón del Norte": (2.4720, -76.5880),
    "María Paz": (2.4710, -76.5870),
    "Zuldemaida": (2.4730, -76.5850),
    "Santiago de Cali": (2.4740, -76.5830),
    "Destechados del Norte": (2.4750, -76.5810),
    "Morinda": (2.4760, -76.5790),
    "El Tablazo": (2.4680, -76.5920),
    "Vereda González": (2.4770, -76.5770),
    "La Florida": (2.4690, -76.5910),
    "La Aldea": (2.4670, -76.5930),
    "Rinconcito Primaveral": (2.4660, -76.5940),
    "La Primavera": (2.4650, -76.5950),
    "Villa del Norte": (2.4685, -76.5905),
    "El Placer": (2.4695, -76.5895),
    "Bello Horizonte": (2.4705, -76.5885),
    "Río Vista": (2.4715, -76.5875),
    "Cruz Roja": (2.4725, -76.5865),
    "El Bambú": (2.4735, -76.5855),
    "Bella Vista": (2.4745, -76.5845),
    "San Ignacio": (2.4540, -76.6030),
    "La Arboleda": (2.4680, -76.5925),
    "Villa Andrés": (2.4665, -76.5935),
    "La Esperanza": (2.4655, -76.5945),
    "Villa Inés": (2.4645, -76.5955),
    "Canales de Brujas": (2.4675, -76.5915),
    "Canterbury": (2.4685, -76.5900),
    "Villa del Viento": (2.4695, -76.5890),
    "Cordillera": (2.4755, -76.5820),
    "Luna Blanca": (2.4765, -76.5800),
    "Los Cámbulos": (2.4720, -76.5870),
    "El Pinar": (2.4730, -76.5860),
    "Guayacanes del Río": (2.4740, -76.5850),
    "Villa Claudia": (2.4750, -76.5840),
    "Minuto de Dios": (2.4510, -76.6020),
    "Chamizal": (2.4520, -76.6010),
    "Matamoros": (2.4530, -76.6000),
    "Los Ángeles": (2.4540, -76.5990),
    "Pinares": (2.4550, -76.5980),
    "San Fernando": (2.4560, -76.5970),
    "Valle del Ortigal": (2.4620, -76.5740),
}

BARRIOS_COMUNA_3: Dict[str, Tuple[float, float]] = {
    "Bolívar": (2.4485, -76.6080),
    "Ciudad Jardín": (2.4470, -76.6060),
    "Periodistas": (2.4460, -76.6040),
    "Sotará": (2.4450, -76.6020),
    "Deportistas": (2.4440, -76.6000),
    "Los Hoyos": (2.4430, -76.5990),
    "Yambitará": (2.4420, -76.5980),
    "Villa Mercedes": (2.4410, -76.5970),
    "Yanaconas": (2.4400, -76.5960),
    "La Ximena": (2.4390, -76.5950),
    "Palace": (2.4480, -76.6070),
    "Pueblillo": (2.4370, -76.5930),
    "Vega de Prieto": (2.4360, -76.5920),
    "José Antonio Galán": (2.4350, -76.5910),
    "Las Tres Margaritas": (2.4340, -76.5900),
    "Torres del Río": (2.4330, -76.5890),
    "Galicia": (2.4475, -76.6050),
    "Nuevo Yambitará": (2.4415, -76.5975),
    "Alto Cauca": (2.4405, -76.5965),
    "Bajo Cauca": (2.4395, -76.5955),
    "La Virginia": (2.4385, -76.5945),
    "Provitec Los Hoyos": (2.4425, -76.5985),
    "Rincón de la Estancia": (2.4465, -76.6030),
    "Madres Solteras": (2.4455, -76.6015),
    "Altos del Jardín": (2.4445, -76.6005),
    "La Estancia": (2.4468, -76.6035),
    "Moravia": (2.4458, -76.6025),
    "Guayacanes": (2.4448, -76.6010),
    "Aida Lucía": (2.4438, -76.5995),
    "Alicante I": (2.4375, -76.5935),
    "Alicante II": (2.4380, -76.5940),
    "Acacias": (2.4365, -76.5925),
    "Rincón del Río": (2.4355, -76.5915),
}

BARRIOS_COMUNA_4: Dict[str, Tuple[float, float]] = {
    "Provitec II Etapa": (2.4430, -76.6120),
    "Bosques de Pomona": (2.4420, -76.6130),
    "Santa Teresita": (2.4435, -76.6115),
    "Vásquez Cobo": (2.4445, -76.6105),
    "El Prado": (2.4455, -76.6095),
    "Siglo XX": (2.4425, -76.6100),
    "Centro": (2.4414, -76.6065),
    "Los Álamos": (2.4440, -76.6085),
    "San Rafael Viejo": (2.4450, -76.6075),
    "El Refugio": (2.4460, -76.6065),
    "Liceo": (2.4448, -76.6080),
    "La Pamba": (2.4420, -76.6060),
    "Loma de Cartagena": (2.4440, -76.6055),
    "Fucha": (2.4430, -76.6050),
    "Hernando Lora": (2.4425, -76.6045),
    "El Empedrado": (2.4410, -76.6070),
    "San Camilo": (2.4405, -76.6075),
    "Caldas": (2.4419, -76.6063),
}

BARRIOS_COMUNA_5: Dict[str, Tuple[float, float]] = {
    "Avelino Ull": (2.4340, -76.5990),
    "Los Braceros": (2.4330, -76.5980),
    "El Lago": (2.4320, -76.5970),
    "Berlín": (2.4310, -76.5960),
    "Suizo": (2.4300, -76.5950),
    "Las Ferias I": (2.4290, -76.5940),
    "Las Ferias II": (2.4285, -76.5935),
    "La Campiña": (2.4350, -76.6000),
    "María Oriente": (2.4306, -76.6011),
    "Los Sauces": (2.4292, -76.6026),
    "Santa Mónica": (2.4280, -76.6010),
    "La Floresta": (2.4270, -76.6000),
    "Los Andes": (2.4260, -76.5990),
    "Colgate Palmolive": (2.4250, -76.5980),
    "La Alameda": (2.4360, -76.6010),
    "El Plateado": (2.4345, -76.5995),
    "Villa Oriente": (2.4335, -76.5985),
    "San Andrés": (2.4325, -76.5975),
    "Poblado de los Altos Sauces": (2.4295, -76.6020),
    "Portal de Santa Mónica": (2.4275, -76.6005),
    "Portal de las Ferias": (2.4282, -76.5932),
}

BARRIOS_COMUNA_6: Dict[str, Tuple[float, float]] = {
    "Alfonso López": (2.4380, -76.6110),
    "López": (2.4375, -76.6105),
    "Valparaíso": (2.4370, -76.6100),
    "Primero de Mayo": (2.4365, -76.6095),
    "Comuneros": (2.4360, -76.6090),
    "Loma de la Virgen": (2.4570, -76.6085),
    "Sindical I": (2.4355, -76.6085),
    "Sindical II": (2.4350, -76.6080),
    "Calicanto": (2.4345, -76.6100),
    "Deán Bajo": (2.4340, -76.6095),
    "Gabriel García Márquez": (2.4335, -76.6090),
    "Jorge E. Gaitán": (2.4330, -76.6120),
    "Limonar": (2.4325, -76.6115),
    "La Paz Sur": (2.4320, -76.6110),
    "La Gran Victoria": (2.4315, -76.6105),
    "Versalles": (2.4310, -76.6100),
    "Ladera": (2.4305, -76.6095),
    "Villa del Carmen": (2.4300, -76.6090),
    "La Colina": (2.4295, -76.6085),
    "Nuevo Japón": (2.4290, -76.6130),
    "Nuevo País": (2.4285, -76.6125),
    "Tejares de Otón": (2.4280, -76.6120),
    "Las Veraneras": (2.4390, -76.6115),
    "Panamericano": (2.4385, -76.6108),
    "Camino Real": (2.4395, -76.6100),
    "San José de los Tejares": (2.4275, -76.6115),
}

BARRIOS_COMUNA_7: Dict[str, Tuple[float, float]] = {
    "Nazaret": (2.4370, -76.6160),
    "Isabela": (2.4365, -76.6155),
    "Las Palmas I": (2.4360, -76.6150),
    "Las Palmas II": (2.4355, -76.6145),
    "Las Palmas": (2.4358, -76.6148),
    "Colombia II Etapa": (2.4350, -76.6140),
    "Los Campos": (2.4345, -76.6135),
    "Treinta y Uno de Marzo": (2.4340, -76.6155),
    "El Mirador": (2.4335, -76.6150),
    "Tomás Cipriano de Mosquera": (2.4330, -76.6145),
    "Las Vegas": (2.4325, -76.6170),
    "Solidaridad": (2.4320, -76.6165),
    "Chapinero": (2.4315, -76.6160),
    "Retiro Alto": (2.4310, -76.6175),
    "Nuevo Popayán": (2.4305, -76.6170),
    "La Unión": (2.4300, -76.6165),
    "La Libertad": (2.4295, -76.6160),
    "La Conquista": (2.4290, -76.6155),
    "Las Brisas": (2.4285, -76.6150),
    "Independencia": (2.4280, -76.6145),
    "Santa Librada": (2.4275, -76.6140),
    "Corsocial": (2.4270, -76.6135),
    "Villa Occidente": (2.4265, -76.6130),
    "Villa España": (2.4260, -76.6125),
}

BARRIOS_COMUNA_8: Dict[str, Tuple[float, float]] = {
    "Pandiguando": (2.4469, -76.6170),
    "La Esmeralda": (2.4438, -76.6158),
    "El Libertador": (2.4455, -76.6165),
    "El Triunfo": (2.4460, -76.6175),
    "Popular": (2.4450, -76.6180),
    "Zaguan": (2.4445, -76.6185),
    "La Cañada": (2.4480, -76.6195),
    "Llano Largo": (2.4475, -76.6190),
    "José María Obando": (2.4470, -76.6185),
    "Guayabal": (2.4490, -76.6200),
    "La Isla I": (2.4485, -76.6195),
    "La Isla II": (2.4482, -76.6192),
    "Esperanza Sur": (2.4465, -76.6180),
    "Camilo Torres": (2.4458, -76.6175),
    "Junín": (2.4453, -76.6170),
    "Santa Helena": (2.4448, -76.6165),
    "Asoprecovi": (2.4443, -76.6160),
    "Edificio Llano Largo": (2.4478, -76.6188),
    "Lomas de Granada": (2.4495, -76.6205),
    "Mis Ranchitos": (2.4500, -76.6210),
    "La Capitana": (2.4505, -76.6215),
    "San Antonio de Padua": (2.4510, -76.6220),
    "Kennedy": (2.4515, -76.6225),
    "San José": (2.4520, -76.6230),
    "La Sombrilla": (2.4525, -76.6235),
    "Carlos Primero": (2.4530, -76.6240),
    "Cinco de Abril": (2.4535, -76.6245),
    "María Occidente": (2.4500, -76.6200),
    "Los Naranjos": (2.4540, -76.6250),
    "Nuevo Hogar": (2.4508, -76.6218),
}

BARRIOS_COMUNA_9: Dict[str, Tuple[float, float]] = {
    "Pomona": (2.4400, -76.6140),
    "Lomas de Pomona": (2.4395, -76.6145),
    "Bosques de Pomona": (2.4390, -76.6150),
    "El Uvo": (2.4385, -76.6200),
    "Las Américas": (2.4380, -76.6195),
    "Santa Rosa": (2.4375, -76.6190),
    "Los Tejares": (2.4370, -76.6185),
    "El Cadillal": (2.4365, -76.6180),
    "Valencia": (2.4360, -76.6175),
    "Santa Inés": (2.4355, -76.6170),
    "La María": (2.4350, -76.6210),
    "El Sendero": (2.4345, -76.6205),
}

# ── Consolidar todos los barrios en un solo diccionario ───────────────────────

ALL_BARRIOS: Dict[str, Tuple[float, float]] = {}
BARRIO_TO_COMUNA: Dict[str, int] = {}

for _comuna_num, _barrios_dict in [
    (1, BARRIOS_COMUNA_1), (2, BARRIOS_COMUNA_2), (3, BARRIOS_COMUNA_3),
    (4, BARRIOS_COMUNA_4), (5, BARRIOS_COMUNA_5), (6, BARRIOS_COMUNA_6),
    (7, BARRIOS_COMUNA_7), (8, BARRIOS_COMUNA_8), (9, BARRIOS_COMUNA_9),
]:
    for _name, _coords in _barrios_dict.items():
        ALL_BARRIOS[_name] = _coords
        BARRIO_TO_COMUNA[_name] = _comuna_num


# ── PUNTOS DE INTERÉS (LANDMARKS) ────────────────────────────────────────────

LANDMARKS: Dict[str, Tuple[float, float]] = {
    # ── Centro Histórico ──
    "Parque Caldas": (2.4419, -76.6063),
    "Torre del Reloj": (2.4413, -76.6074),
    "Puente del Humilladero": (2.4407, -76.6080),
    "Catedral Basílica Nuestra Señora de la Asunción": (2.4422, -76.6058),
    "Iglesia San Francisco": (2.4430, -76.6055),
    "Iglesia Santo Domingo": (2.4416, -76.6068),
    "Iglesia La Ermita": (2.4405, -76.6085),
    "Santuario de Belén": (2.4395, -76.6095),
    "Morro de Tulcán": (2.4432, -76.6088),
    "Pueblito Patojo": (2.4408, -76.6050),
    "Teatro Municipal Guillermo Valencia": (2.4418, -76.6055),
    "Casa Museo Mosquera": (2.4420, -76.6060),
    "Panteón de los Próceres": (2.4415, -76.6062),

    # ── Centros Comerciales ──
    "Centro Comercial Campanario": (2.4645, -76.5985),
    "Centro Comercial Terra Plaza": (2.4865, -76.5605),
    "Centro Comercial Anarkos": (2.4425, -76.6050),
    "Centro Comercial Plaza Colonial": (2.4420, -76.6045),
    "Almacenes Éxito": (2.4500, -76.6030),

    # ── Universidades ──
    "Universidad del Cauca": (2.4445, -76.6065),
    "Fundación Universitaria de Popayán": (2.4435, -76.6050),
    "Universidad Autónoma del Cauca": (2.4455, -76.6040),
    "SENA Popayán": (2.4600, -76.5995),
    "Colegio Mayor del Cauca": (2.4430, -76.6070),
    "Universidad Antonio Nariño": (2.4460, -76.6020),
    "Fundación Universitaria María Cano": (2.4615, -76.5970),

    # ── Hospitales / Clínicas ──
    "Hospital Universitario San José": (2.4380, -76.6070),
    "Clínica La Estancia": (2.4600, -76.6000),
    "Clínica San Rafael": (2.4450, -76.6055),
    "Clínica Santa Gracia": (2.4440, -76.6060),
    "Hospital María Occidente": (2.4500, -76.6200),
    "Hospital Toribio Maya": (2.4350, -76.6040),
    "Cruz Roja Popayán": (2.4470, -76.6045),

    # ── Terminal / Aeropuerto ──
    "Terminal de Transporte": (2.4530, -76.5960),
    "Aeropuerto Guillermo León Valencia": (2.4550, -76.5850),

    # ── Deportes / Parques ──
    "Estadio Ciro López": (2.4475, -76.6095),
    "Coliseo": (2.4470, -76.6090),
    "Parque de las Aves": (2.4460, -76.6080),
    "Parque Mosquera": (2.4430, -76.6068),
    "Velódromo": (2.4490, -76.6085),
    "Piscina Olímpica": (2.4485, -76.6080),

    # ── Galerías / Mercados ──
    "Galería La Esmeralda": (2.4438, -76.6158),
    "Galería de Bolívar": (2.4485, -76.6080),
    "Plaza de Mercado del Norte": (2.4600, -76.5990),

    # ── Otros ──
    "Río Molino": (2.4400, -76.6060),
    "Río Ejido": (2.4380, -76.6050),
    "Río Cauca": (2.4350, -76.5920),
    "Gobernación del Cauca": (2.4418, -76.6058),
    "Alcaldía de Popayán": (2.4420, -76.6055),
    "CAM (Centro Administrativo Municipal)": (2.4422, -76.6052),
    "Bomberos Popayán": (2.4445, -76.6040),
    "Estación de Policía": (2.4425, -76.6065),
    "Registraduría": (2.4430, -76.6058),
    "Fiscalía": (2.4428, -76.6062),
    "Comfacauca": (2.4480, -76.6000),
    "Torres de Comfacauca": (2.4478, -76.5998),
    "Servientrega Centro": (2.4418, -76.6060),
    "Coordinadora Mercantil": (2.4460, -76.6010),
    "Coomeva": (2.4430, -76.6055),
    "Banco de Bogotá": (2.4418, -76.6065),
    "Bancolombia Centro": (2.4420, -76.6062),
    "Supermercado Olímpica": (2.4435, -76.6040),
}


# ── CORREGIMIENTOS Y ZONAS RURALES ────────────────────────────────────────────

CORREGIMIENTOS: Dict[str, Tuple[float, float]] = {
    "Julumito": (2.4150, -76.6300),
    "La Yunga": (2.4050, -76.6400),
    "San Bernardino": (2.4000, -76.5900),
    "Calibío": (2.5000, -76.5800),
    "Poblazón": (2.4100, -76.5700),
    "Quintana": (2.4200, -76.5600),
    "Las Guacas": (2.3800, -76.5800),
    "Los Cerrillos": (2.3900, -76.6100),
    "Pisojé": (2.4700, -76.6300),
    "Santa Rosa": (2.4000, -76.6200),
    "El Charco": (2.3950, -76.5950),
    "Torres": (2.4600, -76.6400),
    "La Rejoya": (2.4300, -76.6500),
    "Vereda El Hogar": (2.4200, -76.5800),
    "Coconuco": (2.3400, -76.4600),
    "La María": (2.4350, -76.6210),
    "Figueroa": (2.4800, -76.6200),
    "Samanga": (2.3600, -76.6000),
    "El Canelo": (2.3700, -76.5900),
    "Santa Bárbara": (2.4500, -76.6350),
    "San Rafael": (2.4650, -76.6250),
    "La Meseta": (2.4100, -76.6100),
    "Puelenje": (2.4250, -76.5700),
}


# ── Funciones de normalización ────────────────────────────────────────────────

def _normalize_text(s: str) -> str:
    """Thin wrapper — delegates to tools.shared.utils.normalize_text."""
    return _normalize_shared(s)


def _normalize_address_advanced(raw: str) -> str:
    """
    Normalización avanzada de direcciones colombianas para Popayán.
    Maneja formatos como:
      - "Cra. 40a #1a-2 a 1a-112"
      - "Cl 5 # 6-25"
      - "la 9 con 4"
      - "carrera 6 número 12-34"
      - "calle 5 entre carreras 6 y 7"
      - "Cra 9 con Cl 4 esquina"
    """
    t = raw.lower().strip()
    t = re.sub(r"\s+", " ", t)

    # Expand abbreviations
    t = re.sub(r"\bcalles?\b", "calle", t)
    t = re.sub(r"\bcarreras?\b", "carrera", t)
    t = re.sub(r"\bcll?\b\.?\s*", "calle ", t)
    t = re.sub(r"\bcra\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bkra?\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bkr\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bav\b\.?\s*", "avenida ", t)
    t = re.sub(r"\bdg\b\.?\s*", "diagonal ", t)
    t = re.sub(r"\btv\b\.?\s*", "transversal ", t)
    t = re.sub(r"\bnúmero\b", "#", t)
    t = re.sub(r"\bnumero\b", "#", t)
    t = re.sub(r"\bnum\b\.?\s*", "# ", t)
    t = re.sub(r"\bn°\s*", "# ", t)
    t = re.sub(r"\bno\.\s*", "# ", t)

    # "la 9 con 4" → "carrera 9 con calle 4" (common Popayán speech pattern)
    t = re.sub(r"\bla\s+(\d+)\s+con\s+(?:la\s+)?(\d+)\b", r"carrera \1 con calle \2", t)

    # Clean multiple spaces
    t = re.sub(r"\s+", " ", t).strip()

    return t


# ── Alias index para matching local ───────────────────────────────────────────

_BARRIO_ALIAS_INDEX: List[Tuple[str, str, Tuple[float, float]]] = []


def _build_barrio_alias_index():
    """Build normalized alias index for all barrios, landmarks, and corregimientos."""
    global _BARRIO_ALIAS_INDEX
    pairs = []

    # Barrios
    for name, coords in ALL_BARRIOS.items():
        norm = _normalize_text(name)
        pairs.append((norm, name, coords))
        # Add "barrio X" variant
        pairs.append((f"barrio {norm}", name, coords))

    # Landmarks
    for name, coords in LANDMARKS.items():
        norm = _normalize_text(name)
        pairs.append((norm, name, coords))

    # Corregimientos
    for name, coords in CORREGIMIENTOS.items():
        norm = _normalize_text(name)
        pairs.append((norm, name, coords))
        pairs.append((f"corregimiento {norm}", name, coords))
        pairs.append((f"vereda {norm}", name, coords))

    # Sort longest first for greedy matching
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    _BARRIO_ALIAS_INDEX = pairs


def _ensure_alias_index():
    if not _BARRIO_ALIAS_INDEX:
        _build_barrio_alias_index()


# ── Geocodificación por nomenclatura ──────────────────────────────────────────

# Patrones para extraer calle/carrera de una dirección colombiana
_STREET_PATTERNS = [
    # "carrera 9 con calle 4" o "calle 5 con carrera 6"
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)\s*"
        r"(?:con|y|esquina|esq)\s*"
        r"(?P<type2>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num2>\d+[a-z]?)",
        re.IGNORECASE,
    ),
    # "carrera 6 # 12-34" o "calle 5 # 6-25"
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)\s*"
        r"[#]\s*"
        r"(?P<num2>\d+[a-z]?)\s*"
        r"[-–]\s*"
        r"(?P<num3>\d+)",
        re.IGNORECASE,
    ),
    # "carrera 6 12-34" (sin #)
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)\s+"
        r"(?P<num2>\d+[a-z]?)\s*"
        r"[-–]\s*"
        r"(?P<num3>\d+)",
        re.IGNORECASE,
    ),
    # Solo "calle N" o "carrera N"
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)"
        r"(?:\s+(?:norte|sur|n|s))?",
        re.IGNORECASE,
    ),
]


def _estimate_coords_from_street(address: str) -> Optional[Tuple[float, float, str]]:
    """
    Estima coordenadas GPS a partir de la nomenclatura de la dirección.
    Usa el grid de Popayán: las calles son latitudes, las carreras son longitudes.

    En Popayán:
    - Calles corren oriente-occidente → su número define posición norte-sur (latitud)
    - Carreras corren norte-sur → su número define posición este-oeste (longitud)

    Rangos típicos:
    - Calles: 1 a ~80 (+ sufijo Norte/Sur para zonas de expansión)
    - Carreras: 1 a ~50
    """
    normalized = _normalize_address_advanced(address)

    for pattern in _STREET_PATTERNS:
        m = pattern.search(normalized)
        if not m:
            continue

        groups = m.groupdict()
        type1 = groups.get("type1", "").lower()
        num1_raw = groups.get("num1", "0")
        num1 = int(re.match(r"\d+", num1_raw).group()) if re.match(r"\d+", num1_raw) else 0

        # Determine latitude and longitude based on street types
        lat = NOMENCLATURA_ORIGIN[0]
        lng = NOMENCLATURA_ORIGIN[1]

        # Check for "norte" suffix
        is_norte = "norte" in normalized or " n " in f" {normalized} " or normalized.endswith(" n")

        if type1 in ("calle",):
            # Calle → defines latitude (N-S position)
            # In Popayán, higher calle numbers go north
            lat = NOMENCLATURA_ORIGIN[0] + num1 * BLOCK_LAT
            if is_norte:
                lat = NOMENCLATURA_ORIGIN[0] + num1 * BLOCK_LAT * 1.2  # Norte extends further

            type2 = groups.get("type2", "").lower()
            num2_raw = groups.get("num2", "0")
            num2 = int(re.match(r"\d+", num2_raw).group()) if num2_raw and re.match(r"\d+", num2_raw) else 0

            if type2 in ("carrera",) and num2 > 0:
                # Carrera → defines longitude
                lng = NOMENCLATURA_ORIGIN[1] + num2 * BLOCK_LNG
            elif num2 > 0 and not type2:
                # "#num2-num3" format: num2 is the cross street (carrera)
                lng = NOMENCLATURA_ORIGIN[1] + num2 * BLOCK_LNG

        elif type1 in ("carrera",):
            # Carrera → defines longitude (E-W position)
            lng = NOMENCLATURA_ORIGIN[1] + num1 * BLOCK_LNG

            type2 = groups.get("type2", "").lower()
            num2_raw = groups.get("num2", "0")
            num2 = int(re.match(r"\d+", num2_raw).group()) if num2_raw and re.match(r"\d+", num2_raw) else 0

            if type2 in ("calle",) and num2 > 0:
                lat = NOMENCLATURA_ORIGIN[0] + num2 * BLOCK_LAT
            elif num2 > 0 and not type2:
                lat = NOMENCLATURA_ORIGIN[0] + num2 * BLOCK_LAT

        elif type1 in ("avenida", "diagonal", "transversal"):
            # Estimate similarly to calles/carreras depending on context
            lat = NOMENCLATURA_ORIGIN[0] + num1 * BLOCK_LAT * 0.8
            lng = NOMENCLATURA_ORIGIN[1] + num1 * BLOCK_LNG * 0.5

        # Validate within Popayán bbox
        if (POPAYAN_BBOX["min_lat"] <= lat <= POPAYAN_BBOX["max_lat"] and
                POPAYAN_BBOX["min_lng"] <= lng <= POPAYAN_BBOX["max_lng"]):
            display = f"{address.strip()}, Popayán, Cauca, Colombia"
            logger.info(f"[GEODATA] Street estimation: {address!r} → ({lat:.5f}, {lng:.5f})")
            return (lat, lng, display)

    return None


# ── Geocodificación local por nombre de lugar ─────────────────────────────────

def geocode_local(query: str) -> Optional[Tuple[float, float, str]]:
    """
    Geocodifica una ubicación buscando en la base de datos local de Popayán.
    Retorna (lat, lng, display_name) o None si no encuentra.

    Orden de búsqueda:
    1. Match exacto en barrios/landmarks/corregimientos
    2. Match parcial (fuzzy) en los mismos
    3. Estimación por nomenclatura (calle/carrera)
    """
    _ensure_alias_index()

    if not query or len(query.strip()) < 2:
        return None

    query_norm = _normalize_text(query)

    # 1. Exact match in alias index
    for alias_norm, canonical, coords in _BARRIO_ALIAS_INDEX:
        if alias_norm == query_norm:
            display = f"{canonical}, Popayán, Cauca, Colombia"
            logger.info(f"[GEODATA] Exact match: {query!r} → {canonical} ({coords[0]:.5f}, {coords[1]:.5f})")
            return (coords[0], coords[1], display)

    # 2. Partial match (query contained in alias OR alias contained in query)
    best_match = None
    best_len = 0
    for alias_norm, canonical, coords in _BARRIO_ALIAS_INDEX:
        if len(alias_norm) < 3:
            continue
        if alias_norm in query_norm or query_norm in alias_norm:
            if len(alias_norm) > best_len:
                best_match = (canonical, coords)
                best_len = len(alias_norm)

    if best_match:
        canonical, coords = best_match
        display = f"{canonical}, Popayán, Cauca, Colombia"
        logger.info(f"[GEODATA] Partial match: {query!r} → {canonical} ({coords[0]:.5f}, {coords[1]:.5f})")
        return (coords[0], coords[1], display)

    # 3. Street nomenclature estimation
    street_result = _estimate_coords_from_street(query)
    if street_result:
        return street_result

    # Also try with normalized address
    normalized = _normalize_address_advanced(query)
    if normalized != query.lower().strip():
        street_result = _estimate_coords_from_street(normalized)
        if street_result:
            return street_result

    logger.info(f"[GEODATA] No local match for: {query!r}")
    return None


# ── Validación de existencia ──────────────────────────────────────────────────

def validate_location_exists(query: str) -> dict:
    """
    Valida si una ubicación existe en Popayán.
    Retorna un dict con info de validación:
    {
        "exists": bool,
        "confidence": "high" | "medium" | "low",
        "type": "barrio" | "landmark" | "corregimiento" | "street" | "unknown",
        "canonical_name": str | None,
        "comuna": int | None,
        "coords": (lat, lng) | None,
        "suggestion": str | None,   # sugerencia si no se encuentra
    }
    """
    _ensure_alias_index()

    if not query or len(query.strip()) < 2:
        return {
            "exists": False,
            "confidence": "low",
            "type": "unknown",
            "canonical_name": None,
            "comuna": None,
            "coords": None,
            "suggestion": "Indica una dirección, barrio o lugar conocido de Popayán.",
        }

    query_norm = _normalize_text(query)

    # Check barrios
    for name, coords in ALL_BARRIOS.items():
        if _normalize_text(name) == query_norm or query_norm in _normalize_text(name):
            return {
                "exists": True,
                "confidence": "high",
                "type": "barrio",
                "canonical_name": name,
                "comuna": BARRIO_TO_COMUNA.get(name),
                "coords": coords,
                "suggestion": None,
            }

    # Check landmarks
    for name, coords in LANDMARKS.items():
        if _normalize_text(name) == query_norm or query_norm in _normalize_text(name):
            return {
                "exists": True,
                "confidence": "high",
                "type": "landmark",
                "canonical_name": name,
                "comuna": None,
                "coords": coords,
                "suggestion": None,
            }

    # Check corregimientos
    for name, coords in CORREGIMIENTOS.items():
        if _normalize_text(name) == query_norm or query_norm in _normalize_text(name):
            return {
                "exists": True,
                "confidence": "high",
                "type": "corregimiento",
                "canonical_name": name,
                "comuna": None,
                "coords": coords,
                "suggestion": None,
            }

    # Check street patterns
    street_result = _estimate_coords_from_street(query)
    if street_result:
        return {
            "exists": True,
            "confidence": "medium",
            "type": "street",
            "canonical_name": query.strip(),
            "comuna": None,
            "coords": (street_result[0], street_result[1]),
            "suggestion": None,
        }

    # Fuzzy search for suggestions
    suggestions = _find_similar_places(query_norm)
    suggestion_text = None
    if suggestions:
        suggestion_text = f"¿Quisiste decir: {', '.join(suggestions[:3])}?"

    return {
        "exists": False,
        "confidence": "low",
        "type": "unknown",
        "canonical_name": None,
        "comuna": None,
        "coords": None,
        "suggestion": suggestion_text,
    }


def _find_similar_places(query_norm: str, max_results: int = 3) -> List[str]:
    """Find places with similar names using simple character overlap."""
    if len(query_norm) < 3:
        return []

    candidates = []

    all_places = {}
    all_places.update(ALL_BARRIOS)
    all_places.update(LANDMARKS)
    all_places.update(CORREGIMIENTOS)

    for name in all_places:
        name_norm = _normalize_text(name)
        # Simple similarity: shared character bigrams
        query_bigrams = set(query_norm[i:i+2] for i in range(len(query_norm)-1))
        name_bigrams = set(name_norm[i:i+2] for i in range(len(name_norm)-1))
        if not query_bigrams or not name_bigrams:
            continue
        overlap = len(query_bigrams & name_bigrams)
        total = len(query_bigrams | name_bigrams)
        similarity = overlap / total if total > 0 else 0
        if similarity > 0.3:
            candidates.append((similarity, name))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in candidates[:max_results]]


# ── Utilidad: obtener barrios cercanos ────────────────────────────────────────

def get_nearby_barrios(lat: float, lng: float, radius_km: float = 1.0) -> List[dict]:
    """Get barrios within radius_km of given coordinates."""
    results = []
    for name, (blat, blng) in ALL_BARRIOS.items():
        dist = _haversine(lat, lng, blat, blng)
        if dist <= radius_km:
            results.append({
                "name": name,
                "comuna": BARRIO_TO_COMUNA.get(name),
                "lat": blat,
                "lng": blng,
                "distance_km": round(dist, 3),
            })
    results.sort(key=lambda x: x["distance_km"])
    return results


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Thin wrapper — delegates to tools.shared.utils.haversine."""
    return _haversine_shared(lat1, lng1, lat2, lng2)


# ── Estadísticas ──────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Return statistics about the geodata database."""
    return {
        "total_barrios": len(ALL_BARRIOS),
        "total_landmarks": len(LANDMARKS),
        "total_corregimientos": len(CORREGIMIENTOS),
        "comunas": 9,
        "barrios_por_comuna": {
            i: len(d) for i, d in [
                (1, BARRIOS_COMUNA_1), (2, BARRIOS_COMUNA_2), (3, BARRIOS_COMUNA_3),
                (4, BARRIOS_COMUNA_4), (5, BARRIOS_COMUNA_5), (6, BARRIOS_COMUNA_6),
                (7, BARRIOS_COMUNA_7), (8, BARRIOS_COMUNA_8), (9, BARRIOS_COMUNA_9),
            ]
        },
    }

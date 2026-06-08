"""
tools/popayan_geodata.py — Base de conocimiento geográfico HIPERDETALLADA de Popayán, Cauca, Colombia.

Versión expandida para sistema de dispatch / taxis / logística tipo InDriver/Uber.

Contiene:
  1. Coordenadas de referencia para TODOS los barrios por comuna (9 comunas) — cobertura máxima
  2. Sistema masivo de aliases, variantes ortográficas, coloquialismos y errores STT
  3. Puntos de interés extremadamente densos (landmarks, comercio, iglesias, instituciones, etc.)
  4. Corregimientos, veredas, caseríos y zonas rurales profundas
  5. Parser avanzado de nomenclatura colombiana con heurísticas locales de Popayán
  6. Geocodificación local como fallback cuando Nominatim falla
  7. Normalización robusta para voz telefónica / Whisper STT / acento colombiano
  8. Fuzzy matching fonético y por bigrams para lenguaje cotidiano
  9. Sistema de sugerencias y ranking de similitud
  10. Validación espacial coherente con el POT de Popayán

Fuentes: OpenStreetMap, Colombia en Mapas, Alcaldía de Popayán (POT),
          Google Maps, Mapcarta, IGN Colombia, datos cartográficos WGS84.

Coordenadas: Todas en formato (latitud, longitud) decimal WGS84.
Bounding box urbano real: lat [2.38, 2.52], lng [-76.72, -76.54]
"""

import logging
import math
import re
import unicodedata
from typing import Optional, Tuple, Dict, List

logger = logging.getLogger("lyra.tools.popayan_geodata")

# ── Intentar importar desde shared utils; fallback inline si no existe ─────────
try:
    from tools.shared.utils import normalize_text as _normalize_shared, haversine as _haversine_shared
    _HAS_SHARED = True
except ImportError:
    _HAS_SHARED = False


# ══════════════════════════════════════════════════════════════════════════════
# PARÁMETROS GLOBALES
# ══════════════════════════════════════════════════════════════════════════════

POPAYAN_CENTER = (2.4419, -76.6063)   # Parque Caldas

POPAYAN_BBOX = {
    "min_lat": 2.32,
    "max_lat": 2.58,
    "min_lng": -76.82,
    "max_lng": -76.42,
}

# Bounding box urbano estricto (zona urbanizada real)
POPAYAN_URBAN_BBOX = {
    "min_lat": 2.38,
    "max_lat": 2.52,
    "min_lng": -76.72,
    "max_lng": -76.54,
}

# Origen de la nomenclatura urbana de Popayán
# Calle 0 / Carrera 0 ≈ intersección sur-occidental del casco urbano histórico
NOMENCLATURA_ORIGIN = (2.4250, -76.6200)
BLOCK_LAT = 0.00090    # ~100 m por cuadra en latitud  (calles → N-S)
BLOCK_LNG = 0.00090    # ~100 m por cuadra en longitud (carreras → E-O)

# ── Constantes de dirección ────────────────────────────────────────────────────
# Las calles corren oriente-occidente; números crecen hacia el norte.
# Las carreras corren norte-sur;       números crecen hacia el oriente.
# Sufijo "N" (Norte): indica zona de expansión al norte del eje principal.
# Sufijo "A", "B" indica bis (variante intermedia).


# ══════════════════════════════════════════════════════════════════════════════
# SISTEMA DE ALIASES MASIVO
# ══════════════════════════════════════════════════════════════════════════════

BARRIO_ALIASES: Dict[str, List[str]] = {
    # ── COMUNA 1 ──────────────────────────────────────────────────────────────
    "Modelo": ["modelo", "barrio modelo", "el modelo", "sector modelo", "modello"],
    "Loma Linda": ["loma linda", "lomalinda", "loma linda norte", "linda loma", "loma lindaa"],
    "Prados del Norte": ["prados del norte", "prados norte", "los prados", "prados", "prados nort"],
    "La Cabaña": ["la cabana", "cabana", "la cabaña", "cabaña", "la cavana", "barrio la cabana"],
    "Santa Clara": ["santa clara", "sta clara", "santaclara", "s clara"],
    "Casas Fiscales": ["casas fiscales", "fiscales", "las fiscales", "casa fiscal"],
    "Nueva Granada": ["nueva granada", "nuevagranada", "n granada", "nueva granadaa"],
    "Machángara": ["machangara", "machángara", "la machangara", "barrio machangara", "machagara"],
    "La Playa": ["la playa", "laplaya", "playa norte", "la playa norte"],
    "Campamento": ["campamento", "el campamento", "campamento norte", "campo norte"],
    "Puerta de Hierro": ["puerta de hierro", "puertahierro", "la puerta hierro", "hierro", "puerta hierro"],
    "Pubenza": ["pubenza", "la pubenza", "barrio pubenza", "pubbenza", "pubensa", "pubença"],
    "Antonio Nariño": ["antonio narino", "narino", "nariño", "antonio nariño", "a narino", "sector narino"],
    "Villa Paula": ["villa paula", "villapaula", "la villa paula", "paula"],
    "Campobello": ["campobello", "campo bello", "campobelo", "bello campo"],
    "El Recuerdo": ["el recuerdo", "recuerdo", "barrio recuerdo"],
    "La Villa": ["la villa", "lavilla", "villa norte", "sector villa"],
    "Bloques de Pubenza": ["bloques de pubenza", "los bloques", "bloques pubenza", "bloques", "blokes pubensa"],
    "Belalcázar": ["belalcazar", "belalcázar", "barrio belalcazar", "belalcasar"],
    "Los Laureles": ["los laureles", "laureles", "laureles norte", "los laúreles"],
    "Los Rosales": ["los rosales", "rosales", "barrio rosales", "rosales norte"],
    "Alcalá": ["alcala", "alcalá", "barrio alcala", "sector alcala"],
    "Monterrosales": ["monterrosales", "monter rosales", "monterros", "monte rosales"],
    "Fancal": ["fancal", "el fancal", "barrio fancal", "fancall"],
    "Ciudad Capri": ["ciudad capri", "capri", "ciudadcapri", "el capri", "barrio capri"],
    "Puerta del Sol": ["puerta del sol", "puertasol", "puerta sol", "el sol norte"],
    "Villa del Norte": ["villa del norte", "v del norte", "villa norte", "villanorte", "villa del nort"],
    "El Placer": ["el placer", "placer", "barrio placer"],
    "Bello Horizonte": ["bello horizonte", "bellohorizonte", "bello orizonte", "horizonte norte"],
    "Río Vista": ["rio vista", "riovista", "rio bista", "vista al rio"],
    "San Ignacio": ["san ignacio", "sanignacio", "s ignacio", "ignacio"],
    "La Arboleda": ["la arboleda", "arboleda", "la arboleda norte", "arboledas"],
    "Villa Andrés": ["villa andres", "villa andrés", "villaandres", "andres norte"],
    "La Esperanza": ["la esperanza", "esperanza", "la esperanza norte", "esperanza norte"],
    "Villa Inés": ["villa ines", "villa inés", "villaínes", "ines norte"],
    "Canales de Brujas": ["canales de brujas", "canales brujas", "las brujas", "brujas"],
    "Canterbury": ["canterbury", "canterburi", "canter bury", "la canterbury"],
    "Cordillera": ["cordillera", "la cordillera", "sector cordillera"],
    "Luna Blanca": ["luna blanca", "lunablanca", "blanca luna", "luna blancaa"],
    "Los Cámbulos": ["los cambulos", "los cámbulos", "cambulos", "los cambulos norte"],
    "El Pinar": ["el pinar", "pinar", "barrio pinar", "el pinar norte"],
    "Guayacanes del Río": ["guayacanes del rio", "guayacanes rio", "los guayacanes", "guayacanes"],
    "Villa Claudia": ["villa claudia", "villaclaudia", "claudia norte"],
    "Minuto de Dios": ["minuto de dios", "minuto dios", "el minuto", "minuto"],
    "Chamizal": ["chamizal", "el chamizal", "chamizal norte"],
    "Matamoros": ["matamoros", "el matamoros", "matamorros"],
    "Los Ángeles": ["los angeles", "los ángeles", "angeles", "barrio angeles"],
    "Pinares": ["pinares", "los pinares", "pinar norte"],
    "San Fernando": ["san fernando", "sanfernando", "s fernando", "fernando norte"],
    "Valle del Ortigal": ["valle del ortigal", "ortigal", "el ortigal", "valle ortigal"],
    "Santa Lucía": ["santa lucia", "santa lucía", "santalucia", "sta lucia"],
    "Pino Pardo": ["pino pardo", "pinopardo", "pino pardoo", "el pino pardo"],
    "Balcón del Norte": ["balcon del norte", "balcón del norte", "balcon norte", "el balcon"],
    "María Paz": ["maria paz", "maría paz", "mariapaz", "m paz"],
    "Zuldemaida": ["zuldemaida", "zulde maida", "sulde maida", "zuldemayda"],
    "Santiago de Cali": ["santiago de cali", "santiago cali", "stgo cali", "cali norte"],
    "Destechados del Norte": ["destechados del norte", "destechados norte", "destechados", "los destechados"],
    "Morinda": ["morinda", "la morinda", "barrio morinda"],
    "El Tablazo": ["el tablazo", "tablazo", "el tablaso"],
    "Rinconcito Primaveral": ["rinconcito primaveral", "rinconcito", "rincon primaveral"],
    "La Primavera": ["la primavera", "primavera", "barrio primavera", "la primavera norte"],
    "El Bambú": ["el bambu", "el bambú", "bambu", "barrio bambu"],
    "Bella Vista": ["bella vista", "bellavista", "bella bista", "vista hermosa norte"],
    "Cruz Roja": ["cruz roja", "cruzroja", "barrio cruz roja"],
    # "Rincón del Río" fue duplicado y asignado erróneamente a Comuna 1; pertenece a Comuna 3.
    # "Guayacanes" también duplicado; ahora solo en Comuna 3 (Oriente).
    # "El Plateado" era duplicado; permanece en Comuna 5.

    # ── COMUNA 3 ──────────────────────────────────────────────────────────────
    "Bolívar": ["bolivar", "bolívar", "barrio bolivar", "sector bolivar"],
    "Ciudad Jardín": ["ciudad jardin", "ciudad jardín", "ciudadjardin", "jardin", "ciudad jardin norte"],
    "Periodistas": ["periodistas", "los periodistas", "barrio periodistas"],
    "Sotará": ["sotara", "sotará", "barrio sotara", "sector sotara"],
    "Deportistas": ["deportistas", "los deportistas", "barrio deportistas"],
    "Los Hoyos": ["los hoyos", "hoyos", "barrio hoyos", "loshoyos"],
    "Yambitará": ["yambítara", "yambitara", "yambitar", "barrio yambitara", "yambita", "yanbitara"],
    "Villa Mercedes": ["villa mercedes", "villamercedes", "mercedes", "villa merced"],
    "Yanaconas": ["yanaconas", "yanaconas sector", "yanacona", "yanaconaz", "ianaconas", "anaconas", "yanacones"],
    "La Ximena": ["la ximena", "ximena", "laximena", "barrio ximena"],
    "Palace": ["palace", "el palace", "barrio palace"],
    "Pueblillo": ["pueblillo", "el pueblillo", "pueblilo"],
    "Vega de Prieto": ["vega de prieto", "vegaprieto", "la vega prieto", "prieto", "vega prieto"],
    "José Antonio Galán": ["jose antonio galan", "galan", "galán", "jose galan", "j galan", "antonio galan"],
    "Las Tres Margaritas": ["las tres margaritas", "tres margaritas", "margaritas", "las margaritas"],
    "Torres del Río": ["torres del rio", "torres río", "torres rio", "torres"],
    "Galicia": ["galicia", "la galicia", "barrio galicia", "sector galicia"],
    "Nuevo Yambitará": ["nuevo yambitara", "nuevo yambítara", "n yambitara", "yambitara nuevo"],
    "Alto Cauca": ["alto cauca", "altocauca", "el alto cauca"],
    "Bajo Cauca": ["bajo cauca", "bajocauca", "el bajo cauca"],
    "La Virginia": ["la virginia", "virginia", "barrio virginia", "la virgnia"],
    "Provitec Los Hoyos": ["provitec los hoyos", "provitec hoyos", "provitec", "los provitec"],
    "Rincón de la Estancia": ["rincon de la estancia", "rincón de la estancia", "la estancia rincon"],
    "Madres Solteras": ["madres solteras", "barrio madres solteras", "las madres"],
    "Altos del Jardín": ["altos del jardin", "altos del jardín", "altos jardin", "jardín alto"],
    "La Estancia": ["la estancia", "estancia", "barrio la estancia"],
    "Moravia": ["moravia", "la moravia", "barrio moravia"],
    "Guayacanes": ["guayacanes", "los guayacanes", "sector guayacanes"],
    "Aida Lucía": ["aida lucia", "aida lucía", "aidalucia", "barrio aida"],
    "Alicante I": ["alicante i", "alicante 1", "alicante uno", "primer alicante"],
    "Alicante II": ["alicante ii", "alicante 2", "alicante dos", "segundo alicante"],
    "Acacias": ["acacias", "las acacias", "barrio acacias"],
    "Rincón del Río": ["rincon del rio", "rincón del río", "rincon rio", "el rincon del rio"],
    "Urbanización La Aldea": ["urbanizacion la aldea", "urbanización la aldea", "Urbanización aldea", "urbaniz la aldea", "urb la aldea", "urbanizacion aldea"],
    "La Aldea": ["la aldea", "la aldea norte"],

    # ── COMUNA 4 ──────────────────────────────────────────────────────────────
    "Provitec II Etapa": ["provitec ii", "provitec 2", "provitec segunda", "provitec segunda etapa"],
    "Bosques de Pomona": ["bosques de pomona", "bosques pomona", "pomona bosques"],
    "Santa Teresita": ["santa teresita", "teresita", "sta teresita", "barrio teresita"],
    "Vásquez Cobo": ["vasquez cobo", "vásquez cobo", "vasquezcobo", "cobo"],
    "El Prado": ["el prado", "prado", "el prado centro", "barrio prado"],
    "Siglo XX": ["siglo xx", "siglo 20", "siglo veinte", "siglo veintiuno"],
    "Centro": ["centro", "el centro", "centro historico", "centro histórico", "casco historico", "casco histórico"],
    "Los Álamos": ["los alamos", "los álamos", "alamos", "barrio alamos"],
    "San Rafael Viejo": ["san rafael viejo", "san rafael", "sanrafael", "s rafael"],
    "El Refugio": ["el refugio", "refugio", "barrio refugio"],
    "Liceo": ["liceo", "el liceo", "barrio liceo"],
    "La Pamba": ["la pamba", "pamba", "barrio pamba", "la pambaa"],
    "Loma de Cartagena": ["loma de cartagena", "loma cartagena", "cartagena loma", "loma de cartag"],
    "Fucha": ["fucha", "la fucha", "barrio fucha", "el fucha"],
    "Hernando Lora": ["hernando lora", "lora", "barrio lora", "hernando"],
    "El Empedrado": ["el empedrado", "empedrado", "barrio empedrado"],
    "San Camilo": ["san camilo", "san camilo centro", "s camilo", "camilo"],
    "Caldas": ["caldas", "parque caldas", "el caldas", "sector caldas"],

    # ── COMUNA 5 ──────────────────────────────────────────────────────────────
    "Avelino Ull": ["avelino ull", "avelinoull", "avelino ul", "barrio avelino"],
    "Los Braceros": ["los braceros", "braceros", "barrio braceros"],
    "El Lago": ["el lago", "lago", "barrio lago", "el lago sur"],
    "Berlín": ["berlin", "berlín", "barrio berlin", "el berlin"],
    "Suizo": ["suizo", "el suizo", "barrio suizo", "sector suizo"],
    "Las Ferias I": ["las ferias i", "ferias 1", "las ferias uno", "primera ferias", "las ferias"],
    "Las Ferias II": ["las ferias ii", "ferias 2", "las ferias dos", "segunda ferias"],
    "La Campiña": ["la campina", "la campiña", "campina", "barrio campina"],
    "María Oriente": ["maria oriente", "maría oriente", "mariaoriente", "m oriente"],
    "Los Sauces": ["los sauces", "sauces", "los sauces sur", "barrio sauces"],
    "Santa Mónica": ["santa monica", "santa mónica", "santamonica", "s monica"],
    "La Floresta": ["la floresta", "floresta", "barrio floresta"],
    "Los Andes": ["los andes", "andes", "barrio andes"],
    "La Alameda": ["la alameda", "alameda", "barrio alameda", "la alameda sur"],
    "El Plateado": ["el plateado", "plateado", "barrio plateado"],
    "Villa Oriente": ["villa oriente", "villaoriente", "oriente villa"],
    "San Andrés": ["san andres", "san andrés", "sanandres", "s andres", "barrio san andres"],
    "Portal de Santa Mónica": ["portal de santa monica", "portal santa monica", "portal monica"],
    "Portal de las Ferias": ["portal de las ferias", "portal ferias", "portal las ferias"],
    "Poblado de los Altos Sauces": ["poblado altos sauces", "altos sauces", "los altos sauces"],

    # ── COMUNA 6 ──────────────────────────────────────────────────────────────
    "Alfonso López": ["alfonso lopez", "alfonso lópez", "alfonsol", "lopez", "barrio lopez", "alfonso"],
    "Valparaíso": ["valparaiso", "valparaíso", "barrio valparaiso", "valparaso"],
    "Primero de Mayo": ["primero de mayo", "1 de mayo", "uno de mayo", "barrio mayo"],
    "Comuneros": ["comuneros", "los comuneros", "barrio comuneros"],
    "Loma de la Virgen": ["loma de la virgen", "loma virgen", "la virgen loma", "virgen loma"],
    "Sindical I": ["sindical i", "sindical 1", "sindicato", "sindical uno"],
    "Sindical II": ["sindical ii", "sindical 2", "sindical dos"],
    "Calicanto": ["calicanto", "el calicanto", "barrio calicanto", "cali canto"],
    "Deán Bajo": ["dean bajo", "deán bajo", "deanbajo", "el dean", "barrio dean"],
    "Gabriel García Márquez": ["gabriel garcia marquez", "garcia marquez", "gabo", "g garcia", "gabriel garcia"],
    "Jorge E. Gaitán": ["jorge gaitan", "jorge e gaitan", "gaitán", "gaitan", "barrio gaitan"],
    "Limonar": ["limonar", "el limonar", "barrio limonar"],
    "La Paz Sur": ["la paz sur", "lapaz sur", "la paz", "barrio paz"],
    "La Gran Victoria": ["la gran victoria", "gran victoria", "victoria", "barrio victoria"],
    "Versalles": ["versalles", "versales", "barrio versalles", "el versalles"],
    "Ladera": ["ladera", "la ladera", "barrio ladera", "sector ladera"],
    "Villa del Carmen": ["villa del carmen", "villa carmen", "villacarmen", "carmen"],
    "Villa del Viento": ["villa del viento", "villa viento", "villadelviento"],
    "La Colina": ["la colina", "colina", "barrio colina", "la colina sur"],
    "Nuevo Japón": ["nuevo japon", "nuevo japón", "japon", "barrio japon"],
    "Nuevo País": ["nuevo pais", "nuevo país", "nuevopais", "barrio pais"],
    "Tejares de Otón": ["tejares de oton", "tejares oton", "oton", "los tejares", "tejares"],
    "Las Veraneras": ["las veraneras", "veraneras", "barrio veraneras"],
    "Panamericano": ["panamericano", "el panamericano", "barrio panamericano", "panam"],
    "Camino Real": ["camino real", "el camino real", "caminoreal", "camino"],
    "San José de los Tejares": ["san jose de los tejares", "san jose tejares", "tejares san jose"],

    # ── COMUNA 7 ──────────────────────────────────────────────────────────────
    "Nazaret": ["nazaret", "barrio nazaret", "nazareth", "naza"],
    "Isabela": ["isabela", "la isabela", "barrio isabela"],
    "Las Palmas I": ["las palmas i", "palmas 1", "las palmas uno", "primera palmas"],
    "Las Palmas II": ["las palmas ii", "palmas 2", "las palmas dos", "segunda palmas"],
    "Las Palmas": ["las palmas", "palmas", "barrio palmas", "las palmaz"],
    "Colombia II Etapa": ["colombia ii etapa", "colombia 2", "colombia segunda", "colombia etapa 2"],
    "Los Campos": ["los campos", "campos", "barrio campos"],
    "Treinta y Uno de Marzo": ["treinta y uno de marzo", "31 de marzo", "treinta uno marzo", "31 marzo"],
    "El Mirador": ["el mirador", "mirador", "barrio mirador"],
    "Tomás Cipriano de Mosquera": ["tomas cipriano mosquera", "mosquera", "cipriano mosquera", "t mosquera"],
    "Las Vegas": ["las vegas", "vegas", "barrio vegas", "barrio las vegas"],
    "Solidaridad": ["solidaridad", "la solidaridad", "barrio solidaridad"],
    "Chapinero": ["chapinero", "el chapinero", "barrio chapinero"],
    "Retiro Alto": ["retiro alto", "el retiro", "retiro", "barrio retiro"],
    "Nuevo Popayán": ["nuevo popayan", "nuevo popayán", "nuevopopayan", "n popayan"],
    "La Unión": ["la union", "la unión", "launion", "barrio union"],
    "La Libertad": ["la libertad", "libertad", "barrio libertad"],
    "La Conquista": ["la conquista", "conquista", "barrio conquista"],
    "Las Brisas": ["las brisas", "brisas", "barrio brisas"],
    "Independencia": ["independencia", "la independencia", "barrio independencia"],
    "Santa Librada": ["santa librada", "librada", "sta librada", "barrio librada"],
    "Corsocial": ["corsocial", "el corsocial", "barrio corsocial"],
    "Villa Occidente": ["villa occidente", "villaoccidente", "occidente", "villa occ"],
    "Villa España": ["villa espana", "villa españa", "villaespana", "españa"],

    # ── COMUNA 8 ──────────────────────────────────────────────────────────────
    "Pandiguando": ["pandiguando", "pandi guando", "el pandiguando", "barrio pandiguando", "pandiguando sector", "pandiguandoo"],
    "La Esmeralda": ["la esmeralda", "esmeralda", "barrio esmeralda", "la esmeraldaa", "esmeraldaa"],
    "El Libertador": ["el libertador", "libertador", "barrio libertador"],
    "El Triunfo": ["el triunfo", "triunfo", "barrio triunfo"],
    "Popular": ["popular", "el popular", "barrio popular"],
    "Zaguan": ["zaguan", "el zaguan", "barrio zaguan", "zaguán"],
    "La Cañada": ["la canada", "la cañada", "canada", "barrio canada"],
    "Llano Largo": ["llano largo", "llano", "el llano", "barrio llano largo"],
    "José María Obando": ["jose maria obando", "obando", "jose obando", "jose m obando", "jose maría", "barrio obando", "j m obando"],
    "Guayabal": ["guayabal", "el guayabal", "barrio guayabal"],
    "La Isla I": ["la isla i", "isla 1", "la isla uno", "la isla"],
    "La Isla II": ["la isla ii", "isla 2", "la isla dos"],
    "Esperanza Sur": ["esperanza sur", "esperanza", "barrio esperanza"],
    "Camilo Torres": ["camilo torres", "camilotorres", "torres camilo", "barrio camilo"],
    "Junín": ["junin", "junín", "barrio junin", "el junin"],
    "Santa Helena": ["santa helena", "helena", "barrio helena", "sta helena"],
    "Asoprecovi": ["asoprecovi", "el asoprecovi", "asoprocobi"],
    "Lomas de Granada": ["lomas de granada", "granada lomas", "barrio granada"],
    "Mis Ranchitos": ["mis ranchitos", "ranchitos", "los ranchitos", "barrio ranchitos"],
    "La Capitana": ["la capitana", "capitana", "barrio capitana"],
    "San Antonio de Padua": ["san antonio de padua", "san antonio padua", "padua", "san antonio", "s antonio padua"],
    "Kennedy": ["kennedy", "barrio kennedy", "kenedy", "el kennedy"],
    "San José": ["san jose", "san josé", "sanjose", "barrio sanjose", "s jose"],
    "La Sombrilla": ["la sombrilla", "sombrilla", "barrio sombrilla"],
    "Carlos Primero": ["carlos primero", "carlos 1", "carlos uno"],
    "Cinco de Abril": ["cinco de abril", "5 de abril", "cincodeabril", "barrio cinco abril"],
    "María Occidente": ["maria occidente", "maría occidente", "m occidente", "barrio maria occidente", "maria osidente"],
    "Los Naranjos": ["los naranjos", "naranjos", "barrio naranjos"],
    "Nuevo Hogar": ["nuevo hogar", "nuevhogar", "hogar nuevo"],

    # ── COMUNA 9 ──────────────────────────────────────────────────────────────
    "Pomona": ["pomona", "la pomona", "barrio pomona", "sector pomona"],
    "Lomas de Pomona": ["lomas de pomona", "lomas pomona", "pomona lomas"],
    "El Uvo": ["el uvo", "uvo", "barrio uvo", "el ubo"],
    "Las Américas": ["las americas", "las américas", "americas", "barrio americas"],
    "Los Tejares": ["los tejares", "tejares", "barrio tejares"],
    "El Cadillal": ["el cadillal", "cadillal", "barrio cadillal"],
    "Valencia": ["valencia", "barrio valencia", "el valencia"],
    "Santa Inés": ["santa ines", "santa inés", "santaines", "barrio ines"],
    "La María": ["la maria", "la maría", "lamaria", "barrio maria"],
    "El Sendero": ["el sendero", "sendero", "barrio sendero"],
}


# ══════════════════════════════════════════════════════════════════════════════
# BARRIOS POR COMUNA — COORDENADAS CENTROIDES
# ══════════════════════════════════════════════════════════════════════════════

# ── COMUNA 1 — Norte/Nororiente ───────────────────────────────────────────────
BARRIOS_COMUNA_1: Dict[str, Tuple[float, float]] = {
    "Modelo": (2.4560, -76.6140),
    "Loma Linda": (2.4582, -76.6122),
    "Prados del Norte": (2.4603, -76.6078),
    "La Cabaña": (2.4571, -76.6103),
    "Santa Clara": (2.4541, -76.6152),
    "Casas Fiscales": (2.4531, -76.6131),
    "Nueva Granada": (2.4553, -76.6062),
    "Machángara": (2.4592, -76.6053),
    "La Playa": (2.4612, -76.6042),
    "Campamento": (2.4522, -76.6168),
    "Puerta de Hierro": (2.4601, -76.6021),
    "Pubenza": (2.4572, -76.6071),
    "Antonio Nariño": (2.4586, -76.6032),
    "Villa Paula": (2.4597, -76.6012),
    "Campobello": (2.4607, -76.5991),
    "El Recuerdo": (2.4617, -76.5972),
    "La Villa": (2.4626, -76.5963),
    "Bloques de Pubenza": (2.4577, -76.6076),
    "Belalcázar": (2.4547, -76.6111),
    "Los Laureles": (2.4557, -76.6092),
    "Los Rosales": (2.4537, -76.6067),
    "Alcalá": (2.4567, -76.6052),
    "Monterrosales": (2.4621, -76.5981),
    "Fancal": (2.4632, -76.5952),
    "Ciudad Capri": (2.4641, -76.5941),
    "Puerta del Sol": (2.4652, -76.5932),
    "Villa del Norte": (2.4774064673324827, -76.55752155846807),
    "El Placer": (2.4697, -76.5896),
    "Bello Horizonte": (2.487592, -76.566679),
    "Río Vista": (2.4716, -76.5877),
    "Pino Pardo": (2.4701, -76.5901),
    "Balcón del Norte": (2.4721, -76.5881),
    "María Paz": (2.4711, -76.5871),
    "Zuldemaida": (2.4731, -76.5851),
    "Santiago de Cali": (2.4741, -76.5831),
    "Destechados del Norte": (2.4751, -76.5811),
    "Morinda": (2.4761, -76.5791),
    "El Tablazo": (2.4681, -76.5921),
    "La Florida": (2.4691, -76.5911),
    "Urbanización La Aldea": (2.4908566, -76.5626847),
    "La Aldea": (2.490858, -76.563088),
    "Rinconcito Primaveral": (2.4661, -76.5941),
    "La Primavera": (2.4651, -76.5951),
    "Cruz Roja": (2.4726, -76.5866),
    "El Bambú": (2.4736, -76.5856),
    "Bella Vista": (2.4746, -76.5846),
    "San Ignacio": (2.4541, -76.6031),
    "La Arboleda": (2.4681, -76.5926),
    "Villa Andrés": (2.4666, -76.5936),
    "La Esperanza": (2.4656, -76.5946),
    "Villa Inés": (2.4646, -76.5956),
    "Canales de Brujas": (2.4676, -76.5916),
    "Canterbury": (2.4686, -76.5901),
    "Cordillera": (2.4756, -76.5821),
    "Luna Blanca": (2.4766, -76.5801),
    "Los Cámbulos": (2.4721, -76.5871),
    "El Pinar": (2.4731, -76.5861),
    "Guayacanes del Río": (2.4741, -76.5851),
    "Villa Claudia": (2.4751, -76.5841),
    "Minuto de Dios": (2.4511, -76.6021),
    "Chamizal": (2.4521, -76.6011),
    "Matamoros": (2.4531, -76.6001),
    "Los Ángeles": (2.4541, -76.5991),
    "Pinares": (2.4551, -76.5981),
    "San Fernando": (2.4561, -76.5971),
    "Valle del Ortigal": (2.4603913005798788, -76.63971248137291),
    "Santa Lucía": (2.4651, -76.5801),
    "Villa del Viento": (2.4696, -76.5891),
    # Barrios eliminados de Comuna 1 (asignados a otras comunas):
    # "Rincón del Río" -> Comuna 3
    # "Guayacanes"     -> Comuna 3
    # "El Plateado"    -> Comuna 5
}

# ── COMUNA 2 — Norte/Noroccidente ─────────────────────────────────────────────
BARRIOS_COMUNA_2: Dict[str, Tuple[float, float]] = {
    "La Paz Norte": (2.4780, -76.6100),
    "El Progreso": (2.4790, -76.6090),
    "Villanueva": (2.4800, -76.6080),
    "El Tabor": (2.4810, -76.6070),
    "Villa del Lago": (2.4770, -76.6110),
    "Los Pinos": (2.4760, -76.6120),
    "Alto Nápoles": (2.4820, -76.6060),
    "Nápoles": (2.4815, -76.6055),
    "La Riviera": (2.4805, -76.6065),
    "San Carlos": (2.4795, -76.6075),
    "Villa de los Alpes": (2.4785, -76.6085),
    "Los Alpes": (2.4775, -76.6095),
    "Las Brisas del Norte": (2.4765, -76.6105),
    "Nuevo Milenio": (2.4755, -76.6115),
    "El Jordán": (2.4745, -76.6125),
    "San Martín": (2.4735, -76.6135),
    "Villa Verde": (2.4725, -76.6145),
    "Los Prados": (2.4715, -76.6155),
    "Vista Hermosa Norte": (2.4850, -76.6040),
    "El Horizonte": (2.4840, -76.6050),
    "Versalles Norte": (2.4830, -76.6060),
    "Urbanización Lomas": (2.4760, -76.6130),
    "El Bosque Norte": (2.4770, -76.6120),
    "Sector Las Granjas": (2.4780, -76.6110),
    "La Estación Norte": (2.4790, -76.6100),
    "San Judas": (2.4800, -76.6090),
    "Villa Rica": (2.4810, -76.6080),
    "El Paraíso Norte": (2.4820, -76.6070),
    "Loma del Viento": (2.4830, -76.6055),
    "Las Colinas Norte": (2.4840, -76.6045),
}

# ── COMUNA 3 — Oriente ────────────────────────────────────────────────────────
BARRIOS_COMUNA_3: Dict[str, Tuple[float, float]] = {
    "Bolívar": (2.4485, -76.6081),
    "Ciudad Jardín": (2.4471, -76.6061),
    "Periodistas": (2.4461, -76.6041),
    "Sotará": (2.4451, -76.6021),
    "Deportistas": (2.4441, -76.6001),
    "Los Hoyos": (2.4431, -76.5991),
    "Yambitará": (2.4421, -76.5981),
    "Villa Mercedes": (2.4411, -76.5971),
    "Yanaconas": (2.4401, -76.5961),
    "La Ximena": (2.4391, -76.5951),
    "Palace": (2.4481, -76.6071),
    "Pueblillo": (2.4371, -76.5931),
    "Vega de Prieto": (2.4361, -76.5921),
    "José Antonio Galán": (2.4351, -76.5911),
    "Las Tres Margaritas": (2.4341, -76.5901),
    "Torres del Río": (2.4331, -76.5891),
    "Galicia": (2.4476, -76.6051),
    "Nuevo Yambitará": (2.4416, -76.5976),
    "Alto Cauca": (2.4406, -76.5966),
    "Bajo Cauca": (2.4396, -76.5956),
    "La Virginia": (2.4386, -76.5946),
    "Provitec Los Hoyos": (2.4426, -76.5986),
    "Rincón de la Estancia": (2.4466, -76.6031),
    "Madres Solteras": (2.4456, -76.6016),
    "Altos del Jardín": (2.4446, -76.6006),
    "La Estancia": (2.4469, -76.6036),
    "Moravia": (2.4459, -76.6026),
    "Guayacanes": (2.4449, -76.6011),
    "Aida Lucía": (2.4439, -76.5996),
    "Alicante I": (2.4376, -76.5936),
    "Alicante II": (2.4381, -76.5941),
    "Acacias": (2.4366, -76.5926),
    "Rincón del Río": (2.4356, -76.5916),
    "Nuevo Amanecer": (2.4346, -76.5906),
    "Villa del Cauca": (2.4336, -76.5896),
    "La Esperanza Oriente": (2.4326, -76.5886),
    "Brisas del Cauca": (2.4316, -76.5876),
    "El Porvenir": (2.4306, -76.5866),
    "Sector El Triangulo": (2.4296, -76.5856),
    "Primaveral Oriente": (2.4286, -76.5846),
}

# ── COMUNA 4 — Centro ─────────────────────────────────────────────────────────
BARRIOS_COMUNA_4: Dict[str, Tuple[float, float]] = {
    "Centro": (2.4419, -76.6063),
    "Caldas": (2.4419, -76.6063),
    "El Prado": (2.4456, -76.6096),
    "Siglo XX": (2.4426, -76.6101),
    "Santa Teresita": (2.4436, -76.6116),
    "Vásquez Cobo": (2.4446, -76.6106),
    "Provitec II Etapa": (2.4431, -76.6121),
    "Bosques de Pomona": (2.4421, -76.6131),
    "Los Álamos": (2.4441, -76.6086),
    "San Rafael Viejo": (2.4451, -76.6076),
    "El Refugio": (2.4461, -76.6066),
    "Liceo": (2.4449, -76.6081),
    "La Pamba": (2.4421, -76.6061),
    "Loma de Cartagena": (2.4441, -76.6056),
    "Fucha": (2.4431, -76.6051),
    "Hernando Lora": (2.4426, -76.6046),
    "El Empedrado": (2.4411, -76.6071),
    "San Camilo": (2.4406, -76.6076),
    "El Vergel": (2.4400, -76.6045),
    "Santa Bárbara": (2.4410, -76.6082),
    "El Retiro Centro": (2.4420, -76.6091),
    "Sector Universidades": (2.4445, -76.6069),
    "Loma del Calvario": (2.4428, -76.6074),
    "El Catay": (2.4415, -76.6055),
}

# ── COMUNA 5 — Sur/Suroriental ────────────────────────────────────────────────
BARRIOS_COMUNA_5: Dict[str, Tuple[float, float]] = {
    "Avelino Ull": (2.4341, -76.5991),
    "Los Braceros": (2.4331, -76.5981),
    "El Lago": (2.4321, -76.5971),
    "Berlín": (2.4311, -76.5961),
    "Suizo": (2.4301, -76.5951),
    "Las Ferias I": (2.4291, -76.5941),
    "Las Ferias II": (2.4286, -76.5936),
    "La Campiña": (2.4351, -76.6001),
    "María Oriente": (2.4307, -76.6012),
    "Los Sauces": (2.4293, -76.6027),
    "Santa Mónica": (2.4281, -76.6011),
    "La Floresta": (2.4463047518720624, -76.64233271260449),
    "Los Andes": (2.4261, -76.5991),
    "La Alameda": (2.4361, -76.6011),
    "El Plateado": (2.4346, -76.5996),
    "Villa Oriente": (2.4336, -76.5986),
    "San Andrés": (2.4326, -76.5976),
    "Poblado de los Altos Sauces": (2.4296, -76.6021),
    "Portal de Santa Mónica": (2.4276, -76.6006),
    "Portal de las Ferias": (2.4283, -76.5933),
    "La Colina Sur": (2.4266, -76.5981),
    "Los Naranjos Sur": (2.4256, -76.5971),
    "Villa del Río Sur": (2.4246, -76.5961),
    "Barrio Nuevo Sur": (2.4236, -76.5951),
    "El Campestre": (2.4311, -76.6001),
    "Los Guaduales": (2.4321, -76.6011),
    "El Cortijo": (2.4331, -76.6021),
    "Villa Esperanza Sur": (2.4341, -76.6031),
    "Santa María Sur": (2.4351, -76.6041),
    "La Perla": (2.4361, -76.6051),
    "La Hacienda": (2.4371, -76.6061),
}

# ── COMUNA 6 — Suroccidente ───────────────────────────────────────────────────
BARRIOS_COMUNA_6: Dict[str, Tuple[float, float]] = {
    "Alfonso López": (2.4381, -76.6111),
    "Valparaíso": (2.4371, -76.6101),
    "Primero de Mayo": (2.4366, -76.6096),
    "Comuneros": (2.4361, -76.6091),
    "Loma de la Virgen": (2.4571, -76.6086),  # este barrio está en la zona alta, se mantiene su coordenada original
    "Sindical I": (2.4356, -76.6086),
    "Sindical II": (2.4351, -76.6081),
    "Calicanto": (2.4346, -76.6101),
    "Deán Bajo": (2.4341, -76.6096),
    "Gabriel García Márquez": (2.4336, -76.6091),
    "Jorge E. Gaitán": (2.4331, -76.6121),
    "Limonar": (2.4326, -76.6116),
    "La Paz Sur": (2.4321, -76.6111),
    "La Gran Victoria": (2.4316, -76.6106),
    "Versalles": (2.4311, -76.6101),
    "Ladera": (2.4306, -76.6096),
    "Villa del Carmen": (2.4301, -76.6091),
    "La Colina": (2.4296, -76.6086),
    "Nuevo Japón": (2.4291, -76.6131),
    "Nuevo País": (2.4286, -76.6126),
    "Tejares de Otón": (2.4281, -76.6121),
    "Las Veraneras": (2.4391, -76.6116),
    "Panamericano": (2.4386, -76.6109),
    "Camino Real": (2.4396, -76.6101),
    "San José de los Tejares": (2.4276, -76.6116),
    "El Retiro Sur": (2.4261, -76.6131),
    "Belén Sur": (2.4271, -76.6111),
    "El Carrizal": (2.4286, -76.6101),
    "Santa Rita": (2.4306, -76.6111),
    "El Reposo": (2.4326, -76.6141),
    "Villa Nueva Sur": (2.4341, -76.6131),
    "La Aurora": (2.4356, -76.6121),
    "Los Cerezos Sur": (2.4371, -76.6111),
    "La Loma": (2.4386, -76.6121),
    "El Cedral": (2.4396, -76.6131),
    "Villa del Sur": (2.4406, -76.6141),
    "Los Quindos": (2.4276, -76.6141),
    "Bajo López": (2.4266, -76.6121),
    "El Rosario Sur": (2.4256, -76.6111),
    "Mirador del Sur": (2.4246, -76.6101),
}

# ── COMUNA 7 — Occidente ──────────────────────────────────────────────────────
BARRIOS_COMUNA_7: Dict[str, Tuple[float, float]] = {
    "Nazaret": (2.4371, -76.6161),
    "Isabela": (2.4366, -76.6156),
    "Las Palmas I": (2.4361, -76.6151),
    "Las Palmas II": (2.4356, -76.6146),
    "Las Palmas": (2.4359, -76.6149),
    "Colombia II Etapa": (2.4351, -76.6141),
    "Los Campos": (2.4346, -76.6136),
    "Treinta y Uno de Marzo": (2.4341, -76.6156),
    "El Mirador": (2.4336, -76.6151),
    "Tomás Cipriano de Mosquera": (2.4331, -76.6146),
    "Las Vegas": (2.4326, -76.6171),
    "Solidaridad": (2.4321, -76.6166),
    "Chapinero": (2.4316, -76.6161),
    "Retiro Alto": (2.4311, -76.6176),
    "Nuevo Popayán": (2.4306, -76.6171),
    "La Unión": (2.4301, -76.6166),
    "La Libertad": (2.4296, -76.6161),
    "La Conquista": (2.4291, -76.6156),
    "Las Brisas": (2.4286, -76.6151),
    "Independencia": (2.4281, -76.6146),
    "Santa Librada": (2.4276, -76.6141),
    "Corsocial": (2.4271, -76.6136),
    "Villa Occidente": (2.4266, -76.6131),
    "Villa España": (2.4261, -76.6126),
    "El Paraíso Occidente": (2.4256, -76.6161),
    "La Esperanza Occidente": (2.4251, -76.6151),
    "Hogar Propio": (2.4246, -76.6141),
    "El Vergel Occidente": (2.4241, -76.6131),
    "Nuevo Amanecer Occidente": (2.4236, -76.6121),
    "San Miguel Occidente": (2.4296, -76.6181),
    "El Diviso": (2.4306, -76.6181),
    "Villa del Diviso": (2.4316, -76.6186),
    "La Palomera": (2.4276, -76.6171),
    "Caracolí": (2.4266, -76.6151),
}

# ── COMUNA 8 — Noroccidente ───────────────────────────────────────────────────
BARRIOS_COMUNA_8: Dict[str, Tuple[float, float]] = {
    "Pandiguando": (2.4470, -76.6171),
    "La Esmeralda": (2.4439, -76.6159),
    "El Libertador": (2.4456, -76.6166),
    "El Triunfo": (2.4461, -76.6176),
    "Popular": (2.4451, -76.6181),
    "Zaguan": (2.4446, -76.6186),
    "La Cañada": (2.4481, -76.6196),
    "Llano Largo": (2.4476, -76.6191),
    "José María Obando": (2.4471, -76.6186),
    "Guayabal": (2.4491, -76.6201),
    "La Isla I": (2.4486, -76.6196),
    "La Isla II": (2.4483, -76.6193),
    "Esperanza Sur": (2.4466, -76.6181),
    "Camilo Torres": (2.4459, -76.6176),
    "Junín": (2.4454, -76.6171),
    "Santa Helena": (2.4449, -76.6166),
    "Asoprecovi": (2.4444, -76.6161),
    "Lomas de Granada": (2.4496, -76.6206),
    "Mis Ranchitos": (2.4501, -76.6211),
    "La Capitana": (2.4506, -76.6216),
    "San Antonio de Padua": (2.4511, -76.6221),
    "Kennedy": (2.4516, -76.6226),
    "San José": (2.4521, -76.6231),
    "La Sombrilla": (2.4526, -76.6236),
    "Carlos Primero": (2.4531, -76.6241),
    "Cinco de Abril": (2.4536, -76.6246),
    "María Occidente": (2.4501, -76.6201),
    "Los Naranjos": (2.4541, -76.6251),
    "Nuevo Hogar": (2.4509, -76.6219),
    "El Retiro Occidente": (2.4546, -76.6256),
    "Los Comuneros Occidente": (2.4551, -76.6261),
    "El Paraíso 8": (2.4556, -76.6266),
    "Villa del Río": (2.4561, -76.6271),
    "Palmas del Norte": (2.4566, -76.6276),
    "El Rubí": (2.4571, -76.6281),
    "Villa Amparo": (2.4576, -76.6286),
    "La Estrella": (2.4466, -76.6196),
    "El Progreso Occidente": (2.4471, -76.6201),
    "El Milagro": (2.4476, -76.6206),
    "Sector Torres": (2.4481, -76.6211),
    "Urbanización Kennedy": (2.4521, -76.6236),
    "San Jorge": (2.4506, -76.6226),
    "Los Quindíos": (2.4496, -76.6216),
    "El Rosal": (2.4486, -76.6206),
    "Villa Esperanza 8": (2.4476, -76.6196),
}

# ── COMUNA 9 — Suroccidente/Pomona ────────────────────────────────────────────
BARRIOS_COMUNA_9: Dict[str, Tuple[float, float]] = {
    "Pomona": (2.4401, -76.6141),
    "Lomas de Pomona": (2.4396, -76.6146),
    "Bosques de Pomona": (2.4391, -76.6151),
    "El Uvo": (2.4386, -76.6201),
    "Las Américas": (2.4381, -76.6196),
    "Santa Rosa Sur": (2.4376, -76.6191),
    "Los Tejares": (2.4371, -76.6186),
    "El Cadillal": (2.4366, -76.6181),
    "Valencia": (2.4361, -76.6176),
    "Santa Inés": (2.4356, -76.6171),
    "La María": (2.4351, -76.6211),
    "El Sendero": (2.4346, -76.6206),
    # Eliminado "Sector Campanario Sur" (pertenece a zona nororiental)
    "Las Acacias Sur": (2.4341, -76.6196),
    "El Vergel Sur": (2.4336, -76.6186),
    "Villa Nariño": (2.4331, -76.6176),
    "La Palma Sur": (2.4326, -76.6166),
    "El Guadual": (2.4321, -76.6156),
    "Los Pinos Sur": (2.4316, -76.6146),
    "El Palmar": (2.4311, -76.6136),
    "Los Laureles Sur": (2.4306, -76.6126),
    "El Manantial": (2.4301, -76.6116),
    "La Pradera": (2.4296, -76.6106),
    "El Guamo": (2.4291, -76.6096),
    "Villa Luz": (2.4286, -76.6086),
}


# ── Consolidación de todos los barrios ────────────────────────────────────────

ALL_BARRIOS: Dict[str, Tuple[float, float]] = {}
BARRIO_TO_COMUNA: Dict[str, int] = {}

_COMUNA_DICTS = [
    (1, BARRIOS_COMUNA_1),
    (2, BARRIOS_COMUNA_2),
    (3, BARRIOS_COMUNA_3),
    (4, BARRIOS_COMUNA_4),
    (5, BARRIOS_COMUNA_5),
    (6, BARRIOS_COMUNA_6),
    (7, BARRIOS_COMUNA_7),
    (8, BARRIOS_COMUNA_8),
    (9, BARRIOS_COMUNA_9),
]

for _cn, _bd in _COMUNA_DICTS:
    for _nm, _co in _bd.items():
        ALL_BARRIOS[_nm] = _co
        BARRIO_TO_COMUNA[_nm] = _cn


# ══════════════════════════════════════════════════════════════════════════════
# LANDMARKS — PUNTOS DE INTERÉS HIPERDETALLADOS
# ══════════════════════════════════════════════════════════════════════════════

LANDMARKS: Dict[str, Tuple[float, float]] = {
    # NUEVAS UBICACIONES CLAVE
    "Hospital Susana López de Valencia": (2.4402, -76.6111),   # corregido según dirección Cl. 14 #18
    "Valle del Ortigal": (2.4603913005798788, -76.63971248137291),
    "SENA Norte": (2.4829669540145356, -76.56233437579733),
    "SENA Centro De Comercio Y Servicios, Cl. 4 #2-80, Centro, Popayán, Cauca": (2.441584217876181, -76.6028230716416),

    # CENTRO HISTÓRICO Y PATRIMONIO
    "Parque Caldas": (2.4419, -76.6063),
    "Torre del Reloj": (2.4413, -76.6074),
    "Puente del Humilladero": (2.4407, -76.6080),
    "Catedral Basílica Nuestra Señora de la Asunción": (2.4422, -76.6058),
    "Iglesia San Francisco": (2.4430, -76.6055),
    "Iglesia Santo Domingo": (2.4416, -76.6068),
    "Iglesia La Ermita": (2.4405, -76.6085),
    "Santuario de Belén": (2.4395, -76.6095),
    "Iglesia Nuestra Señora de Carmen": (2.4415, -76.6072),
    "Iglesia San Agustín": (2.4423, -76.6069),
    "Iglesia de San José": (2.4418, -76.6080),
    "Iglesia Santa Rosa de Lima": (2.4376, -76.6191),
    "Iglesia San Camilo": (2.4407, -76.6077),
    "Iglesia de la Encarnación": (2.4420, -76.6062),
    "Capilla de Jesús Nazareno": (2.4425, -76.6060),
    "Morro de Tulcán": (2.4432, -76.6088),
    "Pueblito Patojo": (2.4408, -76.6050),
    "Teatro Municipal Guillermo Valencia": (2.4418, -76.6055),
    "Casa Museo Mosquera": (2.4420, -76.6060),
    "Panteón de los Próceres": (2.4415, -76.6062),
    "Casa del Fundador": (2.4417, -76.6064),
    "Museo Negret y MEAI": (2.4416, -76.6066),
    "Museo de Historia Natural": (2.4445, -76.6066),
    "Biblioteca Nacional Julio Mario Santo Domingo": (2.4421, -76.6060),
    "Archivo Central del Cauca": (2.4418, -76.6058),
    "Plaza de San Francisco": (2.4430, -76.6057),
    "Claustro de la Encarnación": (2.4420, -76.6063),
    "Palacio Nacional": (2.4417, -76.6061),
    "Edificio Hernando Borrero Mutis": (2.4416, -76.6059),

    # CENTROS COMERCIALES Y SUPERMERCADOS
    "Centro Comercial Campanario": (2.459635441153488, -76.59421007333673),
    "CC Campanario": (2.459635441153488, -76.59421007333673),
    "Centro Comercial Terra Plaza": (2.4865, -76.5605),
    "Centro Comercial Anarkos": (2.4425, -76.6050),
    "Centro Comercial Plaza Colonial": (2.4420, -76.6045),
    "Centro Comercial Único Popayán": (2.4600, -76.5990),
    "Plaza de la 14": (2.4430, -76.6040),
    "Almacenes Éxito Centro": (2.4500, -76.6030),
    "Almacenes Éxito Campanario": (2.4640, -76.5980),
    "Supermercado Olímpica Centro": (2.4435, -76.6040),
    "Olímpica Sur": (2.4300, -76.6050),
    "Olímpica La Esmeralda": (2.4440, -76.6155),
    "D1 Centro": (2.4422, -76.6055),
    "D1 Campanario": (2.4642, -76.5988),
    "D1 Yanaconas": (2.4403, -76.5962),
    "Ara Popayán": (2.4435, -76.6052),
    "Lider Popayán": (2.4440, -76.6048),
    "Supermercado Comfacauca": (2.4481, -76.6001),
    "Justo y Bueno Centro": (2.4424, -76.6058),
    "Mercado Las Américas": (2.4380, -76.6195),
    "Supertiendas y Droguerías Olímpica": (2.4435, -76.6042),
    "Surtifamiliar": (2.4445, -76.6038),
    "Makro Popayán": (2.4660, -76.5960),
    "Homecenter Popayán": (2.4650, -76.5970),
    "Easy Popayán": (2.4648, -76.5978),

    # UNIVERSIDADES Y COLEGIOS
    "Universidad del Cauca": (2.4445, -76.6065),
    "Unicauca": (2.4445, -76.6065),
    "Fundación Universitaria de Popayán": (2.4435, -76.6050),
    "FUP": (2.4435, -76.6050),
    "Universidad Autónoma del Cauca": (2.4455, -76.6040),
    "Uniautónoma": (2.4455, -76.6040),
    "SENA Popayán": (2.4829669540145356, -76.56233437579733),
    "SENA Norte": (2.4829669540145356, -76.56233437579733),
    "SENA Centro De Comercio Y Servicios, Cl. 4 #2-80, Centro, Popayán, Cauca": (2.441584217876181, -76.6028230716416),
    "Colegio Mayor del Cauca": (2.4430, -76.6070),
    "Universidad Antonio Nariño": (2.4460, -76.6020),
    "Fundación Universitaria María Cano": (2.4615, -76.5970),
    "Universidad CEIPA": (2.4440, -76.6045),
    "Universidad Minuto de Dios": (2.4512, -76.6022),
    "Corporación Universitaria Comfacauca": (2.4478, -76.5999),
    "Instituto Técnico Industrial": (2.4450, -76.6060),
    "Colegio Champagnat": (2.4416, -76.6054),
    "Colegio Nacional Alejandro de Humboldt": (2.4440, -76.6042),
    "Colegio Normal Superior": (2.4430, -76.6048),
    "Colegio Diocesano": (2.4419, -76.6059),
    "Institución Educativa La Paz": (2.4320, -76.6110),
    "Colegio Comuneros": (2.4360, -76.6090),
    "IE Julio Caicedo Téllez": (2.4470, -76.6070),
    "IE Gabriela Mistral": (2.4380, -76.6160),
    "IE Ciudadela Comfacauca": (2.4640, -76.5975),
    "Colegio Nuestra Señora de la Encarnación": (2.4420, -76.6065),
    "Liceo de la Universidad del Cauca": (2.4448, -76.6062),
    "Institución Educativa Agustiniano": (2.4415, -76.6070),
    "IE Las Palmas": (2.4355, -76.6150),
    "IE San Isidoro": (2.4500, -76.6215),
    "Escuela Normal Superior Farallones": (2.4430, -76.6050),
    "INEM Popayán": (2.4480, -76.6050),

    # HOSPITALES, CLÍNICAS Y CENTROS DE SALUD
    "Hospital Universitario San José": (2.4380, -76.6070),
    "Clínica La Estancia": (2.4600, -76.6000),
    "Clínica San Rafael": (2.4450, -76.6055),
    "Clínica Santa Gracia": (2.4440, -76.6060),
    "Hospital María Occidente": (2.4500, -76.6200),
    "Hospital Toribio Maya": (2.4350, -76.6040),
    "Hospital Susana López de Valencia": (2.4402, -76.6111),
    "Cruz Roja Colombiana Seccional Cauca": (2.4470, -76.6045),
    "Clínica Mediláser": (2.4435, -76.6055),
    "Centro de Especialistas Popayán": (2.4425, -76.6060),
    "IPS Indígena": (2.4415, -76.6065),
    "Clínica La Buena Samaritana": (2.4455, -76.6045),
    "Clínica Carvajal": (2.4445, -76.6050),
    "Clínica El Rosario": (2.4460, -76.6040),
    "Clínica Chia Ltda": (2.4430, -76.6052),
    "Centro Hospitalario Crecer": (2.4615, -76.5975),
    "Clínica CES Popayán": (2.4440, -76.6057),
    "Sura EPS": (2.4430, -76.6062),
    "Nueva EPS": (2.4425, -76.6058),
    "Coosalud EPS": (2.4432, -76.6055),
    "Coomeva Medicina Prepagada": (2.4435, -76.6050),
    "Comfacauca EPS": (2.4480, -76.6000),
    "Salud Total EPS": (2.4428, -76.6060),
    "Emssanar EPS": (2.4422, -76.6063),
    "Centro de Salud Alfonso López": (2.4381, -76.6111),
    "Centro de Salud Pandiguando": (2.4470, -76.6170),
    "Centro de Salud Yanaconas": (2.4401, -76.5961),
    "Centro de Salud La Esmeralda": (2.4439, -76.6158),
    "Centro de Salud La María": (2.4351, -76.6210),
    "Unidad Materno Infantil San José": (2.4382, -76.6072),
    "SENA Centro de Salud": (2.4601, -76.5996),
    "Laboratorio Clínico Christus Sinergia": (2.4420, -76.6058),
    "Clínica CES Sede Norte": (2.4605, -76.5985),

    # TERMINAL, AEROPUERTO Y TRANSPORTE
    "Terminal de Transporte Popayán": (2.4530, -76.5960),
    "Terminal": (2.4530, -76.5960),
    "Terminal de Buses": (2.4530, -76.5960),
    "Aeropuerto Guillermo León Valencia": (2.4550, -76.5850),
    "Aeropuerto": (2.4550, -76.5850),
    "Satena Popayán": (2.4552, -76.5848),
    "Estación de Servicio Terminal": (2.4528, -76.5958),
    "Taxi Aeropuerto": (2.4548, -76.5852),

    # ENTIDADES GUBERNAMENTALES
    "Gobernación del Cauca": (2.4418, -76.6058),
    "Alcaldía de Popayán": (2.4420, -76.6055),
    "CAM Centro Administrativo Municipal": (2.4422, -76.6052),
    "Concejo Municipal de Popayán": (2.4421, -76.6060),
    "Notaría Primera de Popayán": (2.4417, -76.6062),
    "Notaría Segunda de Popayán": (2.4419, -76.6064),
    "Notaría Tercera de Popayán": (2.4416, -76.6060),
    "Notaría Cuarta de Popayán": (2.4415, -76.6058),
    "Registraduría Nacional": (2.4430, -76.6058),
    "Fiscalía General": (2.4428, -76.6062),
    "Juzgados Popayán": (2.4416, -76.6063),
    "Palacio de Justicia": (2.4415, -76.6062),
    "Tribunal del Cauca": (2.4413, -76.6060),
    "DIAN Popayán": (2.4418, -76.6055),
    "Cámara de Comercio del Cauca": (2.4422, -76.6057),
    "INVIAS Popayán": (2.4440, -76.6050),
    "Secretaría de Educación Cauca": (2.4416, -76.6066),
    "DANE Popayán": (2.4425, -76.6060),
    "ICBF Popayán": (2.4430, -76.6055),
    "Contraloría Departamental": (2.4417, -76.6059),
    "Procuraduría Provincial": (2.4420, -76.6061),
    "Personería Municipal": (2.4421, -76.6063),
    "Instituto Departamental de Salud del Cauca": (2.4415, -76.6070),
    "SENA Dirección Regional": (2.4600, -76.5995),
    "Superintendencia de Notariado": (2.4418, -76.6064),

    # SEGURIDAD
    "Estación de Policía Centro": (2.4425, -76.6065),
    "Departamento de Policía Cauca": (2.4432, -76.6068),
    "Bomberos Popayán": (2.4445, -76.6040),
    "CAI Pandiguando": (2.4470, -76.6172),
    "CAI La Esmeralda": (2.4440, -76.6160),
    "CAI Yanaconas": (2.4402, -76.5963),
    "CAI Alfonso López": (2.4382, -76.6112),
    "CAI Campanario": (2.4643, -76.5987),
    "CAI Terminal": (2.4532, -76.5962),
    "CAI Bolívar": (2.4486, -76.6082),
    "CAI María Occidente": (2.4502, -76.6202),
    "Sijín Popayán": (2.4426, -76.6064),
    "Bacrim Popayán": (2.4427, -76.6066),
    "Gaula Cauca": (2.4431, -76.6067),

    # BANCOS Y FINANCIERAS
    "Bancolombia Centro": (2.4420, -76.6062),
    "Bancolombia Campanario": (2.4643, -76.5986),
    "Banco de Bogotá Centro": (2.4418, -76.6065),
    "Banco de Bogotá Norte": (2.4600, -76.5990),
    "Banco Davivienda Centro": (2.4419, -76.6061),
    "Banco Davivienda Campanario": (2.4641, -76.5984),
    "BBVA Popayán": (2.4421, -76.6059),
    "Banco Agrario Popayán": (2.4415, -76.6063),
    "Banco Popular Popayán": (2.4416, -76.6064),
    "Banco Occidente Popayán": (2.4422, -76.6060),
    "Banco AV Villas Popayán": (2.4417, -76.6062),
    "Banco Caja Social Popayán": (2.4423, -76.6058),
    "Banco W": (2.4424, -76.6059),
    "Scotiabank Colpatria Popayán": (2.4419, -76.6063),
    "Bancamía Popayán": (2.4420, -76.6064),
    "Confiar Cooperativa": (2.4430, -76.6055),
    "Caja Promotora de Vivienda Militar": (2.4415, -76.6060),
    "Efecty Centro": (2.4418, -76.6060),
    "Giros y Finanzas": (2.4422, -76.6062),
    "Supergiros": (2.4420, -76.6061),
    "Western Union Popayán": (2.4419, -76.6060),
    "ATM Bancolombia Parque Caldas": (2.4419, -76.6063),
    "ATM Bancolombia Terminal": (2.4530, -76.5961),
    "ATM Davivienda Centro": (2.4420, -76.6060),
    "ATM Éxito": (2.4500, -76.6031),

    # GASOLINERAS / ESTACIONES DE SERVICIO
    "Bomba de Gasolina La Esmeralda": (2.4440, -76.6160),
    "Bomba de Gasolina Pandiguando": (2.4468, -76.6170),
    "Estación de Servicio Terpel Norte": (2.4600, -76.5992),
    "Estación de Servicio Biomax Campanario": (2.4644, -76.5986),
    "Estación de Servicio Primax Centro": (2.4430, -76.6055),
    "Estación de Servicio Texaco Sur": (2.4290, -76.6040),
    "Estación de Servicio Mansarovar": (2.4660, -76.5960),
    "Estación de Servicio Yanaconas": (2.4400, -76.5960),
    "Bomba de Gasolina Obando": (2.4472, -76.6186),
    "Estación Mobil Centro": (2.4425, -76.6048),
    "EDS Terpel Sur": (2.4300, -76.6070),
    "Bomba Los Tejares": (2.4370, -76.6185),
    "EDS Llano Largo": (2.4475, -76.6190),
    "EDS Vía al Norte": (2.4700, -76.5980),
    "EDS Aeropuerto": (2.4548, -76.5855),

    # DROGUERÍAS Y FARMACIAS
    "Droguería La Rebaja Centro": (2.4420, -76.6060),
    "Droguería La Rebaja Norte": (2.4600, -76.5992),
    "Droguería La Rebaja Terminal": (2.4530, -76.5963),
    "Droguería Colsubsidio Popayán": (2.4422, -76.6058),
    "Farmatodo Campanario": (2.4643, -76.5985),
    "Droguería Cruz Verde Centro": (2.4419, -76.6062),
    "Farmacia Cafam": (2.4428, -76.6060),
    "Drogas El Descuento": (2.4416, -76.6064),
    "Droguería Medimás": (2.4421, -76.6063),
    "Droguería Don Pedro": (2.4415, -76.6061),
    "Farmacia San Marcos": (2.4435, -76.6050),
    "Droguería Yanaconas": (2.4401, -76.5960),
    "Droguería La Esmeralda": (2.4440, -76.6157),
    "Droguería Pandiguando": (2.4470, -76.6171),

    # GALERÍAS Y MERCADOS
    "Galería La Esmeralda": (2.4438, -76.6158),
    "La Galería": (2.4438, -76.6158),
    "Galería de Bolívar": (2.4485, -76.6080),
    "Plaza de Mercado del Norte": (2.4600, -76.5990),
    "Galería Popular Yanaconas": (2.4403, -76.5962),
    "Plaza Central de Mercado": (2.4435, -76.6055),

    # PLAZAS, PARQUES Y RECREACIÓN
    "Parque de la Independencia": (2.4415, -76.6055),
    "Parque Mosquera": (2.4430, -76.6068),
    "Parque de Belén": (2.4395, -76.6095),
    "Parque de las Aves": (2.4460, -76.6080),
    "Parque Infantil Campanario": (2.4645, -76.5983),
    "Parque Recreacional El Lago": (2.4321, -76.5971),
    "Parque El Parque de Alfonso López": (2.4381, -76.6112),
    "Parque Las Palmas": (2.4359, -76.6149),
    "Parque La Esmeralda": (2.4439, -76.6158),
    "Jardín Botánico de Popayán": (2.4443, -76.6068),
    "Ecoparque Las Monjas": (2.4401, -76.6100),
    "Parque Lineal Río Molino": (2.4400, -76.6060),
    "Cancha de Tejo La Ermita": (2.4405, -76.6086),
    "Canchas Múltiples SENA": (2.4599, -76.5993),
    "Cancha de Fútbol Obando": (2.4471, -76.6186),
    "Cancha Pandiguando": (2.4471, -76.6172),

    # ESTADIOS, COLISEOS Y DEPORTES
    "Estadio Ciro López": (2.4475, -76.6095),
    "Coliseo de Ferias y Exposiciones": (2.4470, -76.6090),
    "Velódromo Alcides Nieto Patiño": (2.4490, -76.6085),
    "Piscina Olímpica": (2.4485, -76.6080),
    "Polideportivo Norte": (2.4605, -76.5988),
    "Polideportivo Sur": (2.4310, -76.6050),
    "Polideportivo Alfonso López": (2.4382, -76.6113),
    "Cancha de Fútbol Bolívar": (2.4486, -76.6082),
    "Liga de Fútbol del Cauca": (2.4474, -76.6093),
    "Club Campestre Popayán": (2.4500, -76.5900),

    # HOTELES Y HOSPEDAJES
    "Hotel Camino Real Popayán": (2.4420, -76.6062),
    "Hotel La Plazuela": (2.4418, -76.6060),
    "Hotel Monasterio": (2.4422, -76.6058),
    "Hotel Los Balcones": (2.4421, -76.6064),
    "Hotel Bolívar Plaza": (2.4415, -76.6062),
    "Hotel Dann Monasterio": (2.4423, -76.6059),
    "Hotel Casa Grande": (2.4420, -76.6065),
    "Hostal Caracol": (2.4424, -76.6061),
    "Hotel La Palma Popayán": (2.4417, -76.6063),
    "Hotel El Viajero Hostel": (2.4416, -76.6062),
    "Hotel Ibis Popayán": (2.4419, -76.6060),
    "Hospedaje El Sol": (2.4421, -76.6057),
    "Hotel Torres del Norte": (2.4603, -76.5987),
    "Motel La Cabaña": (2.4571, -76.6103),

    # RESTAURANTES Y BARES
    "Restaurante Italiano": (2.4416, -76.6060),
    "La Fresa Popayán": (2.4418, -76.6055),
    "Restaurante El Solar": (2.4419, -76.6064),
    "Crepes y Waffles Popayán": (2.4645, -76.5984),
    "La Vieja Guardia": (2.4420, -76.6058),
    "Restaurante Rincon Paisa": (2.4425, -76.6060),
    "El Rancho de Helenita": (2.4430, -76.6055),
    "Fresal Estadio": (2.4475, -76.6092),
    "Panadería La Española": (2.4421, -76.6063),
    "Pastelería Francesa": (2.4418, -76.6060),
    "Restaurante El Capitolio": (2.4419, -76.6059),
    "Bar Club Náutico": (2.4500, -76.5900),
    "Discoteca Rumberos": (2.4445, -76.6050),
    "Discoteca El Goce Pagano": (2.4440, -76.6045),
    "Bar el Parque": (2.4418, -76.6062),
    "Asadero El Rincón Llanero": (2.4430, -76.6052),
    "Cevichería y Pescadería La Mar": (2.4422, -76.6059),
    "Heladería Mimo's": (2.4643, -76.5984),
    "McDonald's Campanario": (2.4644, -76.5986),
    "KFC Campanario": (2.4642, -76.5984),

    # CENTROS RELIGIOSOS
    "Templo Adventista del Séptimo Día Popayán": (2.4440, -76.6050),
    "Iglesia Pentecostal Unida de Colombia": (2.4450, -76.6040),
    "Centro Mundial de Adoración": (2.4460, -76.6030),
    "Iglesia Bethel": (2.4470, -76.6020),
    "Iglesia Cuadrangular Popayán": (2.4480, -76.6010),
    "Iglesia Cristiana": (2.4491, -76.6000),
    "Comunidad Judía de Popayán": (2.4420, -76.6060),
    "Centro Islámico del Cauca": (2.4425, -76.6055),
    "Santuario San Pio de Pietrelcina": (2.4430, -76.6060),
    "Capilla San Antonio de Padua": (2.4511, -76.6221),
    "Iglesia Yanaconas": (2.4402, -76.5962),
    "Iglesia Pandiguando": (2.4469, -76.6171),
    "Parroquia Alfonso López": (2.4382, -76.6112),
    "Parroquia San Isidro": (2.4501, -76.6216),
    "Parroquia Las Palmas": (2.4358, -76.6149),

    # PUENTES, GLORIETAS Y SEMÁFOROS
    "Puente de Mamá Tita": (2.4390, -76.6060),
    "Puente Vía al Sur": (2.4300, -76.6040),
    "Puente Varón": (2.4380, -76.6075),
    "Puente Centenario": (2.4370, -76.6050),
    "Puente Calibío": (2.4980, -76.5810),
    "Puente Panamericana Norte": (2.4600, -76.5950),
    "Puente del Campamento": (2.4522, -76.6168),
    "Puente San Jorge": (2.4470, -76.6180),
    "Puente Autopista Sur": (2.4260, -76.6050),
    "Entrada Campanario": (2.4643, -76.5988),
    "Glorieta Panamericana Norte": (2.4660, -76.5960),
    "Glorieta Estadio": (2.4472, -76.6092),
    "Glorieta Terminal": (2.4532, -76.5958),
    "Glorieta La Esmeralda": (2.4439, -76.6161),
    "Glorieta Aeropuerto": (2.4552, -76.5853),
    "Semáforos La Esmeralda": (2.4440, -76.6158),
    "Semáforos Pandiguando": (2.4470, -76.6170),
    "Semáforos Campanario": (2.4644, -76.5987),
    "Semáforos Terminal": (2.4531, -76.5960),
    "Semáforos Estadio": (2.4474, -76.6093),
    "Semáforos Centro": (2.4420, -76.6060),
    "Y de Las Palmas": (2.4358, -76.6148),

    # COMFACAUCA E INSTALACIONES GREMIALES
    "Comfacauca Sede Principal": (2.4480, -76.6000),
    "Torres de Comfacauca": (2.4478, -76.5998),
    "Comfacauca Recreación": (2.4482, -76.5997),
    "ANDI Seccional Cauca": (2.4418, -76.6058),
    "Fenalco Cauca": (2.4420, -76.6059),
    "ACOPI Popayán": (2.4419, -76.6060),

    # RÍOS, QUEBRADAS Y GEOGRAFÍA
    "Río Molino": (2.4400, -76.6060),
    "Río Ejido": (2.4380, -76.6050),
    "Río Cauca": (2.4350, -76.5920),
    "Río Pisojé": (2.4700, -76.6300),
    "Río Palacé": (2.4200, -76.6000),
    "Quebrada Los Robles": (2.4460, -76.6080),
    "Quebrada San Francisco": (2.4430, -76.6070),
    "Cerro Las Tres Cruces": (2.4380, -76.6100),
    "Cerro Morro de Tulcán": (2.4432, -76.6088),
    "Loma del Calvario": (2.4428, -76.6074),

    # CORREOS Y LOGÍSTICA
    "Servientrega Centro": (2.4418, -76.6060),
    "Servientrega Campanario": (2.4643, -76.5984),
    "Coordinadora Mercantil": (2.4460, -76.6010),
    "Interrapidísimo Popayán": (2.4422, -76.6058),
    "Envia Popayán": (2.4419, -76.6061),
    "4-72 Correos Centro": (2.4415, -76.6062),
    "TCC Popayán": (2.4425, -76.6055),
    "Deprisa Popayán": (2.4428, -76.6063),

    # REFERENCIAS COTIDIANAS
    "La Y de Pomona": (2.4396, -76.6145),
    "Entrada al Morro": (2.4432, -76.6088),
    "Subida a Belén": (2.4393, -76.6093),
    "Bajada de Yanaconas": (2.4402, -76.5962),
    "Sector del Éxito": (2.4500, -76.6031),
    "Por el Terminal": (2.4530, -76.5960),
    "Antes de la Galería": (2.4437, -76.6157),
    "Detrás del Hospital San José": (2.4378, -76.6068),
    "Frente al Estadio": (2.4476, -76.6095),
    "Abajo de Campanario": (2.4640, -76.5988),
    "Subida al Morro": (2.4432, -76.6090),
    "Por Pandiguando": (2.4470, -76.6172),
    "Vía a Cali": (2.4650, -76.5970),
    "Vía a Bogotá": (2.4350, -76.5900),
    "Vía a Silvia": (2.4420, -76.5700),
    "Vía al Aeropuerto": (2.4550, -76.5870),
    "Vía a la Rejoya": (2.4300, -76.6500),
    "Vía a Julumito": (2.4150, -76.6300),
    "Por la Panamericana": (2.4660, -76.5960),
    "Carretera Panamericana": (2.4660, -76.5960),
    "Autopista Panamericana": (2.4660, -76.5960),

    # GIMNASIOS
    "Bodytech Campanario": (2.4643, -76.5983),
    "Bodytech Centro": (2.4425, -76.6060),
    "Smart Fit Popayán": (2.4641, -76.5982),
    "Gimnasio Olímpico Popayán": (2.4488, -76.6082),
    "Templo del Cuerpo": (2.4430, -76.6058),
    "Center Gym Popayán": (2.4432, -76.6056),

    # SECTOR INDUSTRIAL
    "Zona Industrial Popayán": (2.4660, -76.5950),
    "Bodega ICONTEC Popayán": (2.4660, -76.5948),
    "Parque Industrial": (2.4665, -76.5945),
    "Zona Franca Cauca": (2.4670, -76.5940),
    "Bodegas Campanario": (2.4648, -76.5982),
}


# ══════════════════════════════════════════════════════════════════════════════
# CORREGIMIENTOS, VEREDAS Y ZONAS RURALES
# ══════════════════════════════════════════════════════════════════════════════

CORREGIMIENTOS: Dict[str, Tuple[float, float]] = {
    # Corregimientos Oficiales
    "Julumito": (2.4150, -76.6300),
    "La Yunga": (2.4050, -76.6400),
    "San Bernardino": (2.4000, -76.5900),
    "Calibío": (2.5000, -76.5800),
    "Poblazón": (2.4100, -76.5700),
    "Quintana": (2.4200, -76.5600),
    "Las Guacas": (2.4728043891159093, -76.54795054691283),
    "Los Cerrillos": (2.3900, -76.6100),
    "Pisojé Alto": (2.4700, -76.6400),
    "Pisojé Bajo": (2.4650, -76.6350),
    "Santa Rosa": (2.4000, -76.6200),
    "El Charco": (2.3950, -76.5950),
    "Torres": (2.4600, -76.6400),
    "La Rejoya": (2.4300, -76.6500),
    "Figueroa": (2.4800, -76.6200),
    "Samanga": (2.3600, -76.6000),
    "El Canelo": (2.3700, -76.5900),
    "Santa Bárbara Rural": (2.4500, -76.6350),
    "San Rafael Rural": (2.4650, -76.6250),
    "La Meseta": (2.4100, -76.6100),
    "Puelenje": (2.4250, -76.5700),
    "Coconuco": (2.3400, -76.4600),
    "El Tablón": (2.4100, -76.6500),
    "La Mesa de los Santos": (2.3850, -76.6200),
    "Pandiguando Rural": (2.4780, -76.6350),
    "Patía Vía": (2.3200, -76.6100),
    # Veredas
    "Vereda El Hogar": (2.4200, -76.5800),
    "Vereda La Playa Rural": (2.4150, -76.5700),
    "Vereda El Reposo Rural": (2.4050, -76.5900),
    "Vereda Guacas": (2.3800, -76.5810),
    "Vereda Campoalegre": (2.4300, -76.5600),
    "Vereda La Toma": (2.4500, -76.5700),
    "Vereda Los Higuerones": (2.4600, -76.5700),
    "Vereda El Cofre": (2.4700, -76.5700),
    "Vereda La Laguna": (2.3600, -76.6200),
    "Vereda Piedra Sentada": (2.3800, -76.6300),
    "Vereda El Japio": (2.4800, -76.5700),
    "Vereda Morales": (2.4900, -76.5600),
    "Vereda Calibío Bajo": (2.4950, -76.5800),
    "Vereda San Ignacio Rural": (2.4100, -76.6200),
    "Vereda El Vergel Rural": (2.3900, -76.5800),
    "Vereda Los Uvos": (2.4400, -76.5600),
    "Vereda Sector La Linda": (2.4550, -76.5750),
    "Vereda Pisojé": (2.4680, -76.6310),
    "Vereda La Floresta Rural": (2.4020, -76.6000),
    "Vereda Santa Rosa del Cauca": (2.4080, -76.6180),
    "Vereda El Paraíso Rural": (2.4350, -76.5650),
    "Vereda La Granja": (2.4250, -76.5650),
    "Vereda Tres Quebradas": (2.3750, -76.5950),
    "Vereda Pedregal": (2.3900, -76.5850),
    "Vereda La Marquesa": (2.4650, -76.6100),
    "Vereda Pandiguando Campesino": (2.4750, -76.6300),
    "Vereda Cuatro Esquinas Rural": (2.4150, -76.6050),
    # Fincas y Parcelaciones
    "Finca El Paraíso Julumito": (2.4120, -76.6280),
    "Finca Las Mercedes": (2.4050, -76.6380),
    "Hacienda El Recuerdo Rural": (2.4000, -76.5880),
    "Parcelación La Floresta Calibío": (2.5020, -76.5780),
    "Parcelación Los Pinos Pisojé": (2.4720, -76.6390),
    "Parcelación El Campestre": (2.4250, -76.5680),
    # Caseríos
    "Caserío La Honda": (2.4100, -76.6400),
    "Caserío El Arado": (2.4050, -76.6300),
    "Caserío San Juan": (2.3950, -76.6050),
    "Caserío La Cabuyal": (2.3850, -76.5900),
    "Caserío El Cedro": (2.4700, -76.6300),
    "Caserío Laguna Seca": (2.3650, -76.6150),
}

CORREGIMIENTO_ALIASES: Dict[str, List[str]] = {
    "Julumito": ["julumito", "hulumito", "julomito", "jullumito", "barrio julumito"],
    "La Yunga": ["la yunga", "yunga", "la iunga", "la junga"],
    "Calibío": ["calibio", "calibío", "calibo", "calibi", "vía calibio"],
    "Poblazón": ["poblazon", "poblazón", "poblazon", "la poblazon"],
    "Quintana": ["quintana", "la quintana", "sector quintana"],
    "Las Guacas": ["las guacas", "guacas", "las guakas"],
    "Los Cerrillos": ["los cerrillos", "cerrillos", "los cerillos"],
    "Pisojé Alto": ["pisoje alto", "pisojé alto", "pisoje arriba"],
    "Pisojé Bajo": ["pisoje bajo", "pisojé bajo", "pisoje abajo", "pisoje"],
    "Torres": ["torres", "sector torres", "corregimiento torres"],
    "La Rejoya": ["la rejoya", "rejoya", "la rehoya", "la rejoya rural"],
    "Figueroa": ["figueroa", "la figueroa", "sector figueroa"],
    "Samanga": ["samanga", "la samanga", "corregimiento samanga"],
    "El Canelo": ["el canelo", "canelo", "sector el canelo"],
    "Coconuco": ["coconuco", "el coconuco", "balneario coconuco", "aguas termales"],
    "Puelenje": ["puelenje", "la puelenje", "corregimiento puelenje"],
    "Santa Rosa": ["santa rosa", "sta rosa", "s rosa rural"],
}


# ══════════════════════════════════════════════════════════════════════════════
# EQUIVALENCIAS FONÉTICAS PARA STT / WHISPER
# ══════════════════════════════════════════════════════════════════════════════

STT_PHONETIC_MAP: Dict[str, str] = {
    "yanaconaz": "Yanaconas",
    "yanocanas": "Yanaconas",
    "yanacona": "Yanaconas",
    "yanacones": "Yanaconas",
    "ianaconas": "Yanaconas",
    "pubensa": "Pubenza",
    "pubbensa": "Pubenza",
    "pubenssa": "Pubenza",
    "campanaryo": "Campanario",
    "campanaro": "Campanario",
    "campanero": "Campanario",
    "campanarryo": "Campanario",
    "mosqueraa": "Mosquera",
    "moskeraa": "Mosquera",
    "maria osidente": "María Occidente",
    "maria ocsidente": "María Occidente",
    "belandcasar": "Belalcázar",
    "belalcasar": "Belalcázar",
    "belalkasar": "Belalcázar",
    "yambitar": "Yambitará",
    "yanbitara": "Yambitará",
    "yambita": "Yambitará",
    "pandiguando": "Pandiguando",
    "pandigando": "Pandiguando",
    "pandigwando": "Pandiguando",
    "pandiguandoo": "Pandiguando",
    "pandy huando": "Pandiguando",
    "pandi guando": "Pandiguando",
    "pandiwando": "Pandiguando",
    "pandiguan": "Pandiguando",
    "la ximena": "La Ximena",
    "laximena": "La Ximena",
    "la jimena": "La Ximena",
    "obando": "José María Obando",
    "jose obando": "José María Obando",
    "humilladero": "Puente del Humilladero",
    "el humilladero": "Puente del Humilladero",
    "humillladero": "Puente del Humilladero",
    "rejoja": "La Rejoya",
    "rehoya": "La Rejoya",
    "guakas": "Las Guacas",
    "guaquez": "Las Guacas",
    "calibio": "Calibío",
    "calibo": "Calibío",
    "pisoje": "Pisojé Bajo",
    "pisojee": "Pisojé Bajo",
    "la galeria": "Galería La Esmeralda",
    "la galería": "Galería La Esmeralda",
    "galeria esmeralda": "Galería La Esmeralda",
    "el sena": "SENA Popayán",
    "sena norte": "SENA Norte",
    "sena centro": "SENA Centro De Comercio Y Servicios, Cl. 4 #2-80, Centro, Popayán, Cauca",
    "el terminal": "Terminal de Transporte Popayán",
    "la terminal": "Terminal de Transporte Popayán",
    "terminal de buses": "Terminal de Transporte Popayán",
    "el aeropuerto": "Aeropuerto Guillermo León Valencia",
    "hospital san jose": "Hospital Universitario San José",
    "hospital san josé": "Hospital Universitario San José",
    "el hospital": "Hospital Universitario San José",
    "la clinica": "Clínica La Estancia",
    "clinica estancia": "Clínica La Estancia",
    "el estadio": "Estadio Ciro López",
    "estadio ciro": "Estadio Ciro López",
    "la universidad": "Universidad del Cauca",
    "la unicauca": "Universidad del Cauca",
    "el morro": "Morro de Tulcán",
    "el parque": "Parque Caldas",
    "parque caldas": "Parque Caldas",
    "la gobernacion": "Gobernación del Cauca",
    "la alcaldia": "Alcaldía de Popayán",
    "la bomba": "Bomba de Gasolina La Esmeralda",
    "bomba yanaconas": "Estación de Servicio Yanaconas",
    "la olimpica": "Supermercado Olímpica Centro",
    "el exito": "Almacenes Éxito Centro",
    "la esmeralda": "La Esmeralda",
    "la torre del reloj": "Torre del Reloj",
    "torre reloj": "Torre del Reloj",
    "el hueco": "Puente del Humilladero",
    "el puente": "Puente del Humilladero",
    "la fiscalia": "Fiscalía General",
    "la registraduria": "Registraduría Nacional",
    "el icbf": "ICBF Popayán",
    "comfacauca": "Comfacauca Sede Principal",
    "las torres": "Torres de Comfacauca",
}


# ══════════════════════════════════════════════════════════════════════════════
# REFERENCIAS COLOQUIALES — Frase coloquial → Nombre canónico
# ══════════════════════════════════════════════════════════════════════════════

COLLOQUIAL_REFERENCES: Dict[str, str] = {
    "por campanario": "Centro Comercial Campanario",
    "abajo de campanario": "Centro Comercial Campanario",
    "arriba de campanario": "Centro Comercial Campanario",
    "entrada campanario": "Centro Comercial Campanario",
    "por el terminal": "Terminal de Transporte Popayán",
    "frente al terminal": "Terminal de Transporte Popayán",
    "al lado del terminal": "Terminal de Transporte Popayán",
    "por el estadio": "Estadio Ciro López",
    "frente al estadio": "Estadio Ciro López",
    "por la galería": "Galería La Esmeralda",
    "al lado de la galería": "Galería La Esmeralda",
    "frente a la galería": "Galería La Esmeralda",
    "detrás de la galería": "Galería La Esmeralda",
    "por pandiguando": "Pandiguando",
    "bajando de yanaconas": "Yanaconas",
    "subiendo al morro": "Morro de Tulcán",
    "por la esmeralda": "La Esmeralda",
    "por los semáforos de la esmeralda": "La Esmeralda",
    "semáforos de la esmeralda": "La Esmeralda",
    "por el sena norte": "SENA Norte",
    "sena norte": "SENA Norte",
    "la bomba de yanaconas": "Yanaconas",
    "el olímpica del centro": "Supermercado Olímpica Centro",
    "el éxito": "Almacenes Éxito Centro",
    "frente al éxito": "Almacenes Éxito Centro",
    "al lado del éxito": "Almacenes Éxito Centro",
    "detrás del hospital san josé": "Hospital Universitario San José",
    "frente al hospital": "Hospital Universitario San José",
    "saliendo del hospital": "Hospital Universitario San José",
    "subiendo a belén": "Santuario de Belén",
    "bajando de belén": "Santuario de Belén",
    "por belén": "Santuario de Belén",
    "por la universidad": "Universidad del Cauca",
    "frente a la unicauca": "Universidad del Cauca",
    "detrás de la unicauca": "Universidad del Cauca",
    "por el aeropuerto": "Aeropuerto Guillermo León Valencia",
    "saliendo al aeropuerto": "Aeropuerto Guillermo León Valencia",
    "vía al aeropuerto": "Aeropuerto Guillermo León Valencia",
    "por la panamericana": "Centro Comercial Campanario",
    "en la panamericana": "Centro Comercial Campanario",
    "la y de pomona": "Pomona",
    "entrada a pomona": "Pomona",
    "por pomona": "Pomona",
    "por obando": "José María Obando",
    "sector obando": "José María Obando",
    "por llano largo": "Llano Largo",
    "por la cañada": "La Cañada",
    "los semáforos del norte": "SENA Norte",
    "semáforos panamericana": "Centro Comercial Campanario",
    "la bomba de pandiguando": "Pandiguando",
    "al lado de la gobernación": "Gobernación del Cauca",
    "frente a la alcaldía": "Alcaldía de Popayán",
    "por el parque caldas": "Parque Caldas",
    "alrededor del parque": "Parque Caldas",
    "por el centro": "Centro",
    "por la torre del reloj": "Torre del Reloj",
    "al frente del reloj": "Torre del Reloj",
    "debajo del puente": "Puente del Humilladero",
    "encima del puente": "Puente del Humilladero",
    "por el humilladero": "Puente del Humilladero",
    "por comfacauca": "Comfacauca Sede Principal",
    "frente a comfacauca": "Comfacauca Sede Principal",
    "las torres comfacauca": "Torres de Comfacauca",
    "diagonal al banco": "Parque Caldas",
    "por los juzgados": "Juzgados Popayán",
    "por la fiscalía": "Fiscalía General",
    "cañadas de brujas": "Canales de Brujas",
    "por torres": "Torres",
    "vía a torres": "Torres",
    "vía al norte": "SENA Norte",
    "carretera al norte": "SENA Norte",
    "panamericana sur": "Olímpica Sur",
    "vía al sur": "Olímpica Sur",
    "salida al sur": "Olímpica Sur",
    "entrando a popayán": "Centro Comercial Campanario",
    "saliendo de popayán": "Centro Comercial Campanario",
    "por alfonsol": "Alfonso López",
    "por alfonso": "Alfonso López",
    "barrio el centro": "Centro",
    "por los cerillos": "Los Cerrillos",
    "camino a julumito": "Julumito",
    "vía a la rejoya": "La Rejoya",
}


# ══════════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN DE TEXTO
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_text(s: str) -> str:
    """Normalización robusta: minúsculas, sin tildes, sin especiales, espacios comprimidos."""
    if _HAS_SHARED:
        return _normalize_shared(s)
    s = s.lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ══════════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN AVANZADA DE DIRECCIONES COLOMBIANAS
# ══════════════════════════════════════════════════════════════════════════════

_SPANISH_ORDINALS: Dict[str, str] = {
    "primera": "1", "segundo": "2", "segunda": "2", "tercero": "3", "tercera": "3",
    "cuarto": "4", "cuarta": "4", "quinto": "5", "quinta": "5",
    "sexto": "6", "sexta": "6", "séptimo": "7", "septimo": "7", "septima": "7",
    "octavo": "8", "octava": "8", "noveno": "9", "novena": "9", "décimo": "10", "decimo": "10",
}

_SPANISH_CARDINALS: Dict[str, str] = {
    "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
    "once": "11", "doce": "12", "trece": "13", "catorce": "14", "quince": "15",
    "dieciséis": "16", "dieciseis": "16", "diecisiete": "17", "dieciocho": "18",
    "diecinueve": "19", "veinte": "20", "veintiuno": "21", "veintidos": "22",
    "veintidós": "22", "veintitres": "23", "veinticuatro": "24",
    "veinticinco": "25", "veintiseis": "26", "veintisiete": "27", "veintiocho": "28",
    "veintinueve": "29", "treinta": "30", "cuarenta": "40", "cincuenta": "50",
    "sesenta": "60", "setenta": "70", "ochenta": "80", "noventa": "90",
    "cien": "100", "ciento": "100",
    "la quinta": "5", "la novena": "9", "la sexta": "6", "la octava": "8",
    "la séptima": "7", "la septima": "7", "la décima": "10", "la decima": "10",
}


def _normalize_address_advanced(raw: str) -> str:
    """Normalización avanzada de direcciones colombianas para Popayán."""
    if not raw:
        return ""
    t = raw.lower().strip()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s+", " ", t)

    for word, num in {**_SPANISH_CARDINALS, **_SPANISH_ORDINALS}.items():
        t = re.sub(r"\b" + re.escape(word) + r"\b", num, t)

    t = re.sub(r"\bnumero\b", "#", t)
    t = re.sub(r"\bnum\b\.?\s*", "# ", t)
    t = re.sub(r"\bn[°o]\.?\s*", "# ", t)
    t = re.sub(r"\bno\.\s*", "# ", t)

    t = re.sub(r"\btransversal\b\.?\s*", "transversal ", t)
    t = re.sub(r"\btvl?\b\.?\s*", "transversal ", t)
    t = re.sub(r"\btr\b\.?\s*", "transversal ", t)
    t = re.sub(r"\bdiagonal\b\.?\s*", "diagonal ", t)
    t = re.sub(r"\bdg\b\.?\s*", "diagonal ", t)
    t = re.sub(r"\bdgl\b\.?\s*", "diagonal ", t)
    t = re.sub(r"\bavenida\b\.?\s*", "avenida ", t)
    t = re.sub(r"\bav\b\.?\s*", "avenida ", t)
    t = re.sub(r"\bavn\b\.?\s*", "avenida ", t)
    t = re.sub(r"\ban\b\.?\s*", "avenida norte ", t)
    t = re.sub(r"\bcarrera\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bcra\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bkra?\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bkr\b\.?\s*", "carrera ", t)
    t = re.sub(r"\bk\b\.?\s*(?=\d)", "carrera ", t)
    t = re.sub(r"\bcar\b\.?\s*(?=\d)", "carrera ", t)
    t = re.sub(r"\bcalles?\b\.?\s*", "calle ", t)
    t = re.sub(r"\bcll?\b\.?\s*", "calle ", t)
    t = re.sub(r"\bcl\b\.?\s*", "calle ", t)

    t = re.sub(r"\bla\s+(\d+[ab]?)\s+con\s+(?:la\s+)?(\d+[ab]?)\b",
               r"carrera \1 con calle \2", t)
    t = re.sub(r"^(\d+[ab]?)\s+con\s+(\d+[ab]?)$",
               r"carrera \1 con calle \2", t)
    t = re.sub(r"\b(carrera)\s+(\d+[ab]?)\s+con\s+(\d+[ab]?)\b",
               r"\1 \2 con calle \3", t)
    t = re.sub(r"\b(calle)\s+(\d+[ab]?)\s+con\s+(\d+[ab]?)\b",
               r"\1 \2 con carrera \3", t)

    t = re.sub(r"\besquina\b", "con", t)
    t = re.sub(r"\besq\b\.?\s*", "con ", t)

    t = re.sub(r"\s+", " ", t).strip()
    return t


# ══════════════════════════════════════════════════════════════════════════════
# ÍNDICE DE ALIASES
# ══════════════════════════════════════════════════════════════════════════════

_ALIAS_INDEX: List[Tuple[str, str, Tuple[float, float]]] = []
_ALIAS_INDEX_BUILT = False


def _build_alias_index():
    """Construye el índice maestro de aliases para búsqueda local eficiente."""
    global _ALIAS_INDEX, _ALIAS_INDEX_BUILT
    pairs: List[Tuple[str, str, Tuple[float, float]]] = []

    def _get_coords(name):
        return (ALL_BARRIOS.get(name)
                or LANDMARKS.get(name)
                or CORREGIMIENTOS.get(name))

    for name, coords in ALL_BARRIOS.items():
        norm = _normalize_text(name)
        pairs.append((norm, name, coords))
        for prefix in ("barrio", "sector", "urbanizacion", "etapa", "conjunto"):
            pairs.append((f"{prefix} {norm}", name, coords))

    for canonical, alias_list in BARRIO_ALIASES.items():
        coords = _get_coords(canonical)
        if coords is None:
            continue
        for alias in alias_list:
            alias_norm = _normalize_text(alias)
            pairs.append((alias_norm, canonical, coords))

    for name, coords in LANDMARKS.items():
        norm = _normalize_text(name)
        pairs.append((norm, name, coords))
        for art in ("el ", "la ", "los ", "las ", "un ", "una "):
            if norm.startswith(art):
                pairs.append((norm[len(art):], name, coords))

    for name, coords in CORREGIMIENTOS.items():
        norm = _normalize_text(name)
        pairs.append((norm, name, coords))
        pairs.append((f"corregimiento {norm}", name, coords))
        pairs.append((f"vereda {norm}", name, coords))
        pairs.append((f"casario {norm}", name, coords))

    for canonical, alias_list in CORREGIMIENTO_ALIASES.items():
        coords = CORREGIMIENTOS.get(canonical)
        if coords is None:
            continue
        for alias in alias_list:
            alias_norm = _normalize_text(alias)
            pairs.append((alias_norm, canonical, coords))

    # COLLOQUIAL_REFERENCES: Dict[str, str] — phrase → canonical_name
    for phrase, canonical in COLLOQUIAL_REFERENCES.items():
        norm = _normalize_text(phrase)
        coords = _get_coords(canonical)
        if coords:
            pairs.append((norm, canonical, coords))

    for stt_variant, canonical in STT_PHONETIC_MAP.items():
        norm = _normalize_text(stt_variant)
        coords = _get_coords(canonical)
        if coords:
            pairs.append((norm, canonical, coords))

    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    _ALIAS_INDEX = pairs
    _ALIAS_INDEX_BUILT = True


def _ensure_index():
    if not _ALIAS_INDEX_BUILT:
        _build_alias_index()


# ══════════════════════════════════════════════════════════════════════════════
# PATRONES REGEX PARA NOMENCLATURA
# ══════════════════════════════════════════════════════════════════════════════

_STREET_PATTERNS = [
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)\s*"
        r"(?:con|y|esquina|esq\.?)\s*"
        r"(?P<type2>calle|carrera|avenida|diagonal|transversal)?\s*"
        r"(?P<num2>\d+[a-z]?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)\s*"
        r"[#]\s*"
        r"(?P<num2>\d+[a-z]?)\s*"
        r"[-–]\s*"
        r"(?P<num3>\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)\s+"
        r"(?P<num2>\d+[a-z]?)\s*"
        r"[-–]\s*"
        r"(?P<num3>\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<type1>calle|carrera|avenida|diagonal|transversal)\s+"
        r"(?P<num1>\d+[a-z]?)"
        r"(?:\s+(?:norte|sur|n|s))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<type1>carrera|calle)\s+"
        r"(?P<num1>\d+)\s*"
        r"(?P<suffix>n|norte|s|sur)\b",
        re.IGNORECASE,
    ),
]


def _estimate_coords_from_street(address: str) -> Optional[Tuple[float, float, str]]:
    """Estima coordenadas a partir de nomenclatura urbana de Popayán."""
    normalized = _normalize_address_advanced(address)

    for pattern in _STREET_PATTERNS:
        m = pattern.search(normalized)
        if not m:
            continue

        groups = m.groupdict()
        type1 = groups.get("type1", "").lower()
        num1_raw = groups.get("num1", "0") or "0"
        num1_match = re.match(r"\d+", num1_raw)
        num1 = int(num1_match.group()) if num1_match else 0
        if num1 == 0:
            continue

        is_norte = bool(re.search(r"\bnorte\b|\bn\b", normalized))
        norte_mult = 1.15 if is_norte else 1.0

        lat = NOMENCLATURA_ORIGIN[0]
        lng = NOMENCLATURA_ORIGIN[1]

        type2 = (groups.get("type2") or "").lower()
        num2_raw = groups.get("num2") or "0"
        num2_match = re.match(r"\d+", num2_raw)
        num2 = int(num2_match.group()) if num2_match else 0

        if type1 in ("calle",):
            lat = NOMENCLATURA_ORIGIN[0] + num1 * BLOCK_LAT * norte_mult
            if type2 in ("carrera",) and num2 > 0:
                lng = NOMENCLATURA_ORIGIN[1] + num2 * BLOCK_LNG
            elif num2 > 0 and not type2:
                lng = NOMENCLATURA_ORIGIN[1] + num2 * BLOCK_LNG
        elif type1 in ("carrera",):
            lng = NOMENCLATURA_ORIGIN[1] + num1 * BLOCK_LNG
            if type2 in ("calle",) and num2 > 0:
                lat = NOMENCLATURA_ORIGIN[0] + num2 * BLOCK_LAT * norte_mult
            elif num2 > 0 and not type2:
                lat = NOMENCLATURA_ORIGIN[0] + num2 * BLOCK_LAT * norte_mult
        elif type1 in ("avenida", "diagonal", "transversal"):
            lat = NOMENCLATURA_ORIGIN[0] + num1 * BLOCK_LAT * 0.75
            lng = NOMENCLATURA_ORIGIN[1] + num1 * BLOCK_LNG * 0.60

        bb = POPAYAN_BBOX
        if (bb["min_lat"] <= lat <= bb["max_lat"] and
                bb["min_lng"] <= lng <= bb["max_lng"]):
            display = f"{address.strip()}, Popayán, Cauca, Colombia"
            logger.info(f"[GEODATA] Street → ({lat:.5f}, {lng:.5f}): {address!r}")
            return (lat, lng, display)

    return None


# ══════════════════════════════════════════════════════════════════════════════
# RESOLUCIÓN DE NOMBRE CANÓNICO
# ══════════════════════════════════════════════════════════════════════════════

def resolve_canonical(query: str) -> Optional[str]:
    """
    Resuelve texto libre del usuario a un nombre canónico (sin coordenadas).

    Pipeline:
    1. Aplica STT_PHONETIC_MAP
    2. Busca en BARRIO_ALIASES (exact match normalizado)
    3. Busca en COLLOQUIAL_REFERENCES
    4. Fuzzy match por bigrams (umbral 0.45) sobre nombres canónicos
    5. Extrae landmark de frases compuestas con patrones espaciales
    6. Si ningún patrón funciona, retorna None
    """
    if not query or len(query.strip()) < 2:
        return None

    _ensure_index()
    q = query.strip()
    q_low = q.lower()
    q_norm = _normalize_text(q)

    # Bloquear palabras genéricas
    generic_words = {"taxi", "taxis", "domicilio", "servicio", "movil", "móvil"}
    if q_norm in generic_words:
        return None

    # 1. STT_PHONETIC_MAP
    for stt_variant, canonical in STT_PHONETIC_MAP.items():
        if q_low == stt_variant or q_low.startswith(stt_variant + " ") or q_low.endswith(" " + stt_variant):
            return canonical
        if q_norm == _normalize_text(stt_variant):
            return canonical

    # 2. BARRIO_ALIASES — exact match normalizado
    for canonical, aliases in BARRIO_ALIASES.items():
        if _normalize_text(canonical) == q_norm:
            return canonical
        for alias in aliases:
            if _normalize_text(alias) == q_norm:
                return canonical

    # 3. COLLOQUIAL_REFERENCES — exact match
    for phrase, canonical in COLLOQUIAL_REFERENCES.items():
        if _normalize_text(phrase) == q_norm:
            return canonical

    # 3b. COLLOQUIAL_REFERENCES — parcial (la frase está contenida en el input o viceversa)
    for phrase, canonical in COLLOQUIAL_REFERENCES.items():
        phrase_norm = _normalize_text(phrase)
        if phrase_norm in q_norm or q_norm in phrase_norm:
            return canonical

    # 4. Fuzzy match por bigrams sobre todos los nombres canónicos (umbral 0.45)
    # Primero, intentar sin prefijos comunes ("barrio X" → "X")
    prefix_pattern = r'^(?:barrio|sector|urbanizacion|etapa|conjunto|residencial|los|las|el|la)\s+'
    q_no_prefix = re.sub(prefix_pattern, '', q_norm, flags=re.IGNORECASE).strip()

    all_canonicals = set(ALL_BARRIOS.keys()) | set(LANDMARKS.keys()) | set(CORREGIMIENTOS.keys())
    # Probar fuzzy match tanto con texto original como sin prefijo
    for q_fuzzy in [q_no_prefix, q_norm]:
        if len(q_fuzzy) >= 3:
            q_bigrams = set(q_fuzzy[i:i+2] for i in range(len(q_fuzzy) - 1))
            if q_bigrams:
                best_canonical = None
                best_score = 0.0
                for name in all_canonicals:
                    name_norm = _normalize_text(name)
                    name_bigrams = set(name_norm[i:i+2] for i in range(len(name_norm) - 1))
                    if not name_bigrams:
                        continue
                    union = q_bigrams | name_bigrams
                    inter = q_bigrams & name_bigrams
                    score = len(inter) / len(union) if union else 0.0
                    if score >= 0.45 and score > best_score:
                        best_score = score
                        best_canonical = name
                if best_canonical:
                    return best_canonical

    # 5. Extraer landmark de frases compuestas con patrones espaciales
    spatial_patterns = [
        # "esquina de X con Y" → extraer X
        (r'(?:(?:esquina|esq)\s+(?:de|del|la|el)\s+)(.+?)\s+(?:con|y|de)\s+', 1),
        # "frente a/al X" → extraer X
        (r'frente\s+(?:a|al|a\s+la|a\s+el)\s+(.+)', 1),
        # "al lado de X" → extraer X
        (r'al\s+lado\s+(?:de|del|de\s+la|de\s+el)\s+(.+)', 1),
        # "detrás de X" → extraer X
        (r'detras\s+(?:de|del|de\s+la|de\s+el)\s+(.+)', 1),
        (r'detras\s+(?:de|del|de\s+la|de\s+el)\s+(.+)', 1),
        # "cerca de/al X" → extraer X
        (r'cerca\s+(?:de|del|al|a\s+la|a\s+el)\s+(.+)', 1),
        # "por el/la X" → extraer X
        (r'por\s+(?:el|la|los|las)\s+(.+)', 1),
        # "a una cuadra de X" → extraer X
        (r'a\s+una\s+cuadra\s+(?:de|del)\s+(.+)', 1),
        # "subiendo/bajando a X" → extraer X
        (r'(?:subiendo|bajando)\s+(?:a|al|a\s+la|a\s+el)\s+(.+)', 1),
        # "en X, en la esquina" → extraer X
        (r'en\s+(.+?),\s*en\s+la\s+esquina', 1),
        # "en la esquina de X" → extraer X
        (r'en\s+la\s+esquina\s+(?:de|del)\s+(.+)', 1),
    ]

    for pattern, group_idx in spatial_patterns:
        match = re.search(pattern, q_norm, re.IGNORECASE)
        if match:
            landmark_text = match.group(group_idx).strip()
            # Limpiar preposiciones finales
            landmark_text = re.sub(r'\s+(?:con|y|de|del|en|para)$', '', landmark_text).strip()
            if len(landmark_text) >= 3:
                # Intentar resolver el landmark extraído
                inner = resolve_canonical(landmark_text)
                if inner:
                    return inner

    # 6. Si el texto contiene un nombre conocido embebido, intentar extraerlo
    for name in all_canonicals:
        name_norm = _normalize_text(name)
        if len(name_norm) >= 4 and name_norm in q_norm:
            return name

    return None


# ══════════════════════════════════════════════════════════════════════════════
# GEOCODIFICACIÓN LOCAL
# ══════════════════════════════════════════════════════════════════════════════

def geocode_local(query: str) -> Optional[Tuple[float, float, str]]:
    """
    Geocodifica una ubicación en la base de datos local de Popayán.

    Orden de búsqueda:
    1. Mapa STT fonético
    2. Referencias coloquiales exactas
    3. Match exacto en índice de aliases
    4. Match parcial por contención
    5. Estimación por nomenclatura de calles/carreras
    """
    _ensure_index()

    query_strip = query.strip()

    # Block generic service words from being geocoded as locations
    generic_words = {"taxi", "taxis", "domicilio", "domicilios", "servicio", "servicios", "movil", "móvil", "un taxi", "el taxi"}
    if query_strip.lower() in generic_words:
        return None

    # 1. STT fonético
    query_low = query_strip.lower()
    for stt_key, canonical in STT_PHONETIC_MAP.items():
        if query_low == stt_key or query_low.startswith(stt_key + " ") or query_low.endswith(" " + stt_key):
            coords = (ALL_BARRIOS.get(canonical)
                      or LANDMARKS.get(canonical)
                      or CORREGIMIENTOS.get(canonical))
            if coords:
                logger.info(f"[GEODATA] STT map: {query!r} → {canonical}")
                return (coords[0], coords[1], f"{canonical}, Popayán, Cauca, Colombia")

    # 2. Referencias coloquiales exactas
    query_norm_coll = _normalize_text(query_strip)
    for phrase, canonical in COLLOQUIAL_REFERENCES.items():
        if _normalize_text(phrase) == query_norm_coll:
            coords = (ALL_BARRIOS.get(canonical)
                      or LANDMARKS.get(canonical)
                      or CORREGIMIENTOS.get(canonical))
            if coords:
                logger.info(f"[GEODATA] Coloquial: {query!r} → {canonical}")
                return (coords[0], coords[1], f"{canonical}, Popayán, Cauca, Colombia")
    # Check if the query contains street/carrera nomenclature with a number.
    is_street = bool(re.search(r'(?:calle|carrera|cl|cra|cr|transversal|tr|diagonal|diag|avenida|av|kr|kra)\s*\d+', query_strip.lower()))

    if not is_street:
        for phrase, canonical in COLLOQUIAL_REFERENCES.items():
            phrase_norm = _normalize_text(phrase)
            if phrase_norm in query_norm_coll or query_norm_coll in phrase_norm:
                coords = (ALL_BARRIOS.get(canonical)
                          or LANDMARKS.get(canonical)
                          or CORREGIMIENTOS.get(canonical))
                if coords:
                    logger.info(f"[GEODATA] Coloquial parcial: {query!r} → {canonical}")
                    return (coords[0], coords[1], f"{canonical}, Popayán, Cauca, Colombia")

    query_norm = _normalize_text(query_strip)

    # 3. Match exacto en índice
    for alias_norm, canonical, coords in _ALIAS_INDEX:
        if alias_norm == query_norm:
            display = f"{canonical}, Popayán, Cauca, Colombia"
            logger.info(f"[GEODATA] Exacto: {query!r} → {canonical}")
            return (coords[0], coords[1], display)

    # 4. Match parcial
    best_match = None
    best_score = 0
    if not is_street:
        for alias_norm, canonical, coords in _ALIAS_INDEX:
            if len(alias_norm) < 3:
                continue
            if alias_norm in query_norm:
                score = len(alias_norm)
                if score > best_score:
                    best_match = (canonical, coords)
                    best_score = score
            elif query_norm in alias_norm:
                score = len(query_norm)
                if score > best_score:
                    best_match = (canonical, coords)
                    best_score = score

    if best_match:
        canonical, coords = best_match
        display = f"{canonical}, Popayán, Cauca, Colombia"
        logger.info(f"[GEODATA] Parcial: {query!r} → {canonical}")
        return (coords[0], coords[1], display)

    # 5. Nomenclatura — NO usar coordenadas hardcodeadas para calles
    #    Dejar que las APIs (Nominatim/Google) geocodifiquen direcciones reales
    #    Solo retornar None para que el pipeline use geocoder_service
    if is_street:
        logger.info(f"[GEODATA] Street detected, deferring to API geocoding: {query!r}")
        return None

    logger.info(f"[GEODATA] Sin coincidencia local: {query!r}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# VALIDACIÓN DE EXISTENCIA
# ══════════════════════════════════════════════════════════════════════════════

def validate_location_exists(query: str) -> dict:
    """
    Valida si una ubicación existe en Popayán.

    Retorna dict con: exists, confidence, type, canonical_name, comuna, coords, suggestion.
    """
    _ensure_index()

    _empty = {
        "exists": False,
        "confidence": "low",
        "type": "unknown",
        "canonical_name": None,
        "comuna": None,
        "coords": None,
        "suggestion": "Indica una dirección, barrio o lugar conocido de Popayán.",
    }

    if not query or len(query.strip()) < 2:
        return _empty

    query_norm = _normalize_text(query)

    for name, coords in ALL_BARRIOS.items():
        if _normalize_text(name) == query_norm or query_norm in _normalize_text(name):
            return {
                "exists": True, "confidence": "high", "type": "barrio",
                "canonical_name": name, "comuna": BARRIO_TO_COMUNA.get(name),
                "coords": coords, "suggestion": None,
            }

    for canonical, alias_list in BARRIO_ALIASES.items():
        for alias in alias_list:
            if _normalize_text(alias) == query_norm:
                coords = ALL_BARRIOS.get(canonical)
                if coords:
                    return {
                        "exists": True, "confidence": "high", "type": "barrio",
                        "canonical_name": canonical, "comuna": BARRIO_TO_COMUNA.get(canonical),
                        "coords": coords, "suggestion": None,
                    }

    for name, coords in LANDMARKS.items():
        if _normalize_text(name) == query_norm or query_norm in _normalize_text(name):
            return {
                "exists": True, "confidence": "high", "type": "landmark",
                "canonical_name": name, "comuna": None, "coords": coords, "suggestion": None,
            }

    for name, coords in CORREGIMIENTOS.items():
        if _normalize_text(name) == query_norm or query_norm in _normalize_text(name):
            return {
                "exists": True, "confidence": "high", "type": "corregimiento",
                "canonical_name": name, "comuna": None, "coords": coords, "suggestion": None,
            }

    for phrase, canonical in COLLOQUIAL_REFERENCES.items():
        if _normalize_text(phrase) == query_norm or query_norm in _normalize_text(phrase):
            coords = (ALL_BARRIOS.get(canonical)
                      or LANDMARKS.get(canonical)
                      or CORREGIMIENTOS.get(canonical))
            return {
                "exists": True, "confidence": "medium", "type": "coloquial",
                "canonical_name": canonical, "comuna": None, "coords": coords, "suggestion": None,
            }

    street_result = _estimate_coords_from_street(query)
    if street_result:
        return {
            "exists": True, "confidence": "medium", "type": "street",
            "canonical_name": query.strip(), "comuna": None,
            "coords": (street_result[0], street_result[1]), "suggestion": None,
        }

    suggestions = _find_similar_places(query_norm)
    suggestion_text = f"¿Quisiste decir: {', '.join(suggestions[:3])}?" if suggestions else None

    return {
        "exists": False, "confidence": "low", "type": "unknown",
        "canonical_name": None, "comuna": None, "coords": None,
        "suggestion": suggestion_text,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BÚSQUEDA FUZZY POR BIGRAMS
# ══════════════════════════════════════════════════════════════════════════════

def _find_similar_places(query_norm: str, max_results: int = 5, threshold: float = 0.28) -> List[str]:
    """Encuentra lugares con nombres similares usando bigrams. Umbral Jaccard = 0.28."""
    if len(query_norm) < 3:
        return []

    query_bigrams = set(query_norm[i:i+2] for i in range(len(query_norm) - 1))
    if not query_bigrams:
        return []

    candidates: List[Tuple[float, str]] = []
    all_places: Dict[str, Tuple[float, float]] = {}
    all_places.update(ALL_BARRIOS)
    all_places.update(LANDMARKS)
    all_places.update(CORREGIMIENTOS)

    for name in all_places:
        name_norm = _normalize_text(name)
        name_bigrams = set(name_norm[i:i+2] for i in range(len(name_norm) - 1))
        if not name_bigrams:
            continue
        union = query_bigrams | name_bigrams
        inter = query_bigrams & name_bigrams
        similarity = len(inter) / len(union) if union else 0.0
        if similarity >= threshold:
            candidates.append((similarity, name))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in candidates[:max_results]]


def fuzzy_search(query: str, max_results: int = 5) -> List[dict]:
    """
    Búsqueda fuzzy completa. Retorna lista de candidatos con scores.

    Retorna: [{"name": str, "type": str, "coords": tuple, "score": float, "comuna": int|None}]
    """
    _ensure_index()
    query_norm = _normalize_text(query)
    if len(query_norm) < 2:
        return []

    query_bigrams = set(query_norm[i:i+2] for i in range(len(query_norm) - 1))
    results: List[Tuple[float, str, str, Tuple[float, float]]] = []

    def _score_and_add(name: str, place_type: str, coords: Tuple[float, float]):
        name_norm = _normalize_text(name)
        if query_norm in name_norm or name_norm in query_norm:
            results.append((1.0, name, place_type, coords))
            return
        name_bigrams = set(name_norm[i:i+2] for i in range(len(name_norm) - 1))
        if not name_bigrams or not query_bigrams:
            return
        union = query_bigrams | name_bigrams
        inter = query_bigrams & name_bigrams
        sim = len(inter) / len(union) if union else 0.0
        if sim >= 0.20:
            results.append((sim, name, place_type, coords))

    for name, coords in ALL_BARRIOS.items():
        _score_and_add(name, "barrio", coords)
    for name, coords in LANDMARKS.items():
        _score_and_add(name, "landmark", coords)
    for name, coords in CORREGIMIENTOS.items():
        _score_and_add(name, "corregimiento", coords)

    results.sort(key=lambda x: x[0], reverse=True)
    seen: set = set()
    out = []
    for score, name, place_type, coords in results:
        if name not in seen:
            seen.add(name)
            out.append({
                "name": name, "type": place_type, "coords": coords,
                "score": round(score, 3),
                "comuna": BARRIO_TO_COMUNA.get(name) if place_type == "barrio" else None,
            })
        if len(out) >= max_results:
            break
    return out


# ══════════════════════════════════════════════════════════════════════════════
# INFERENCIA DE BARRIO / COMUNA POR COORDENADAS
# ══════════════════════════════════════════════════════════════════════════════

def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia haversine en kilómetros."""
    if _HAS_SHARED:
        return _haversine_shared(lat1, lng1, lat2, lng2)
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_nearby_barrios(lat: float, lng: float, radius_km: float = 1.0) -> List[dict]:
    """Barrios dentro de radius_km de las coordenadas dadas, ordenados por distancia."""
    results = []
    for name, (blat, blng) in ALL_BARRIOS.items():
        dist = _haversine(lat, lng, blat, blng)
        if dist <= radius_km:
            results.append({
                "name": name, "comuna": BARRIO_TO_COMUNA.get(name),
                "lat": blat, "lng": blng, "distance_km": round(dist, 4),
            })
    results.sort(key=lambda x: x["distance_km"])
    return results


def infer_barrio_from_coords(lat: float, lng: float) -> Optional[dict]:
    """Retorna el barrio más cercano a las coordenadas dadas."""
    closest = get_nearby_barrios(lat, lng, radius_km=5.0)
    return closest[0] if closest else None


def get_nearby_landmarks(lat: float, lng: float, radius_km: float = 0.5) -> List[dict]:
    """Landmarks dentro de radius_km de las coordenadas dadas."""
    results = []
    for name, (blat, blng) in LANDMARKS.items():
        dist = _haversine(lat, lng, blat, blng)
        if dist <= radius_km:
            results.append({
                "name": name, "lat": blat, "lng": blng,
                "distance_km": round(dist, 4),
            })
    results.sort(key=lambda x: x["distance_km"])
    return results


# ══════════════════════════════════════════════════════════════════════════════
# INFERENCIA DE ORIGEN/DESTINO DESDE TEXTO LIBRE (DISPATCH TAXI)
# ══════════════════════════════════════════════════════════════════════════════

def parse_trip_locations(text: str) -> dict:
    """
    Extrae posibles origen y destino desde texto libre de un usuario de taxi.

    Retorna dict con: origin_text, destination_text, origin_coords,
                      destination_coords, ambiguous, raw_text.
    """
    t = text.strip()
    origin_text = None
    destination_text = None

    patterns_od = [
        r"(?:de|desde)\s+(.+?)\s+(?:hasta|a|para|al|hacia)\s+(.+)",
        r"(.+?)\s+(?:hasta|a|para|al|hacia)\s+(.+)",
        r"(?:recoge?me|estoy|me encuentro|me hallo)\s+(?:en|por|cerca)\s+(.+?)\s+(?:y|para)\s+(?:lleva(?:me)?|ir|voy)\s+(?:a|al|hacia|para)\s+(.+)",
    ]
    for pat in patterns_od:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            origin_text = m.group(1).strip()
            destination_text = m.group(2).strip()
            break

    if not destination_text:
        dest_patterns = [
            r"(?:quiero ir|voy|llevar(?:me)?|para|al|a la?|hacia)\s+(.+)",
            r"(?:necesito taxi|pido taxi|me manda un taxi)\s+(?:para|a|al|hacia)\s+(.+)",
        ]
        for pat in dest_patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                destination_text = m.group(1).strip()
                break

    origin_coords = geocode_local(origin_text) if origin_text else None
    dest_coords = geocode_local(destination_text) if destination_text else None

    origin_ll = (origin_coords[0], origin_coords[1]) if origin_coords else None
    dest_ll = (dest_coords[0], dest_coords[1]) if dest_coords else None

    ambiguous = (
        (origin_text and not origin_ll) or
        (destination_text and not dest_ll) or
        (not origin_text and not destination_text)
    )

    return {
        "origin_text": origin_text,
        "destination_text": destination_text,
        "origin_coords": origin_ll,
        "destination_coords": dest_ll,
        "ambiguous": ambiguous,
        "raw_text": t,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ESTADÍSTICAS
# ══════════════════════════════════════════════════════════════════════════════

def get_stats() -> dict:
    """Estadísticas de la base de datos geoespacial."""
    total_aliases = sum(len(v) for v in BARRIO_ALIASES.values())
    return {
        "total_barrios": len(ALL_BARRIOS),
        "total_landmarks": len(LANDMARKS),
        "total_corregimientos": len(CORREGIMIENTOS),
        "total_colloquial_refs": len(COLLOQUIAL_REFERENCES),
        "total_stt_phonetic_entries": len(STT_PHONETIC_MAP),
        "total_barrio_aliases": total_aliases,
        "comunas": 9,
        "barrios_por_comuna": {
            i: len(d) for i, d in _COMUNA_DICTS
        },
    }


# ── Preconstruir índice al importar ──────────────────────────────────────────
_build_alias_index()
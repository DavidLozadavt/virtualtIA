from typing import Dict

_SPEECH_CORRECTIONS: Dict[str, str] = {
    # ── "los sauces" misheard as: ──
    "entonces": "los sauces",
    "en sauce": "los sauces",
    "en sauces": "los sauces",
    "lo sauce": "los sauces",
    "las sauces": "los sauces",
    "ensauces": "los sauces",
    "el sauce": "los sauces",
    "los sauce": "los sauces",
    "lo sauces": "los sauces",
    # ── "valle del ortigal" misheard as: ──
    "valle vertical": "valle del ortigal",
    "valle del vertical": "valle del ortigal",
    "valle de ortigal": "valle del ortigal",
    "valle ortigal": "valle del ortigal",
    "balle del ortigal": "valle del ortigal",
    "va del ortigal": "valle del ortigal",
    "vale del ortigal": "valle del ortigal",
    # ── "maría oriente" misheard as: ──
    "mari oriente": "maría oriente",
    "maria de oriente": "maría oriente",
    "maria oriente": "maría oriente",
    "maría de oriente": "maría oriente",
    "la maría oriente": "maría oriente",
    # ── "maría occidente" misheard as: ──
    "maria occidente": "maría occidente",
    "mari occidente": "maría occidente",
    "maría de occidente": "maría occidente",
    # ── "la esmeralda" misheard as: ──
    "la esmerada": "la esmeralda",
    "esmerada": "la esmeralda",
    "esmeranda": "la esmeralda",
    "la esmeralda se": "la esmeralda",
    # ── "pandiguando" misheard as: ──
    "pandi cuando": "pandiguando",
    "pan de cuando": "pandiguando",
    "pandi guando": "pandiguando",
    "pandiguandos": "pandiguando",
    # ── "yanaconas" misheard as: ──
    "yanaco más": "yanaconas",
    "yanacona": "yanaconas",
    "jana con as": "yanaconas",
    "janacona": "yanaconas",
    "yanacones": "yanaconas",
    # ── "campanario" misheard as: ──
    "campana río": "campanario",
    "campana rio": "campanario",
    "el campana rio": "campanario",
    "el campanarios": "campanario",
    # ── "belalcázar" misheard as: ──
    "bella alcázar": "belalcázar",
    "bella alcazar": "belalcázar",
    "belal cázar": "belalcázar",
    "belga azar": "belalcázar",
    # ── "los comuneros" misheard as: ──
    "lo comunero": "los comuneros",
    "lo comuneros": "los comuneros",
    "los comunero": "los comuneros",
    # ── "alfonso lópez" misheard as: ──
    "alfonzo lópez": "alfonso lópez",
    "alfonzo lopez": "alfonso lópez",
    "alfonso lope": "alfonso lópez",
    "alfonso lópe": "alfonso lópez",
    # ── "pueblillo" misheard as: ──
    "pueblo illo": "pueblillo",
    "pueblito": "pueblillo",
    "pueblo ijo": "pueblillo",
    # ── "yambitará" misheard as: ──
    "jambitará": "yambitará",
    "jambitara": "yambitará",
    "jan bitara": "yambitará",
    # ── "loma de la virgen" misheard as: ──
    "forma de la virgen": "loma de la virgen",
    "roma de la virgen": "loma de la virgen",
    "loma la virgen": "loma de la virgen",
    # ── "terminal" misheard as: ──
    "la terminal": "terminal",
    "el terminal": "terminal",
    # ── "lomas de granada" misheard as: ──
    "loma de granada": "lomas de granada",
    "lomas granada": "lomas de granada",
    # ── "la sombrilla" misheard as: ──
    "la sombilla": "la sombrilla",
    "la sombrija": "la sombrilla",
    # ── "cinco de abril" misheard as: ──
    "5 de abril": "cinco de abril",
    "sinco de abril": "cinco de abril",
    # ── "la pamba" misheard as: ──
    "la pampa": "la pamba",
    "la bamba": "la pamba",
    # ── "parque caldas" misheard as: ──
    "parque calda": "parque caldas",
    "parque de caldas": "parque caldas",
    # ── "valpaíso" / "valparaíso" ──
    "balparaíso": "valparaíso",
    "valpa raíso": "valparaíso",
    "valpariso": "valparaíso",
    # ── "primero de mayo" ──
    "1 de mayo": "primero de mayo",
    "primer de mayo": "primero de mayo",
    # ── Various ──
    "kennedy": "kennedy",  # ensure it doesn't get corrected
    "retiro al sol": "retiro alto",
    "santa en elena": "santa helena",
    "la campiñas": "la campiña",
    "el triunfos": "el triunfo",
    "la florida": "la florida",
}


_TWILIO_HINTS = (
    "calle,carrera,barrio,con,esquina,norte,sur,número,"
    # Barrios that are commonly misrecognized
    "los sauces,sauces,maría oriente,maria oriente,alfonso lópez,alfonso lopez,"
    "pandiguando,yanaconas,campanario,la esmeralda,esmeralda,belalcázar,"
    "los comuneros,comuneros,pueblillo,yambitará,camilo torres,"
    "valle del ortigal,ortigal,polideportivo,valle vertical,"
    # Major barrios all comunas
    "modelo,loma linda,prados del norte,santa clara,pubenza,el recuerdo,"
    "bello horizonte,el tablazo,la primavera,villa del norte,san ignacio,"
    "los ángeles,pinares,san fernando,bolívar,ciudad jardín,periodistas,"
    "los hoyos,la estancia,villa mercedes,el prado,los álamos,la pamba,"
    "berlín,suizo,las ferias,la campiña,santa mónica,la floresta,los andes,"
    "valparaíso,primero de mayo,loma de la virgen,sindical,calicanto,limonar,"
    "las palmas,nazaret,chapinero,nuevo popayán,la libertad,santa librada,"
    "el libertador,el triunfo,popular,llano largo,kennedy,la sombrilla,"
    "lomas de granada,la capitana,cinco de abril,maría occidente,"
    "pomona,el uvo,las américas,santa rosa,los tejares,el cadillal,"
    "retiro alto,la colina,versalles,la paz sur,jorge eliécer gaitán,"
    "villa del viento,torres del río,provitec,el jardín,zaguan,"
    "rincón de la estancia,la campiña,el plateado,la alameda,"
    # Landmarks
    "centro,parque caldas,torre del reloj,puente del humilladero,"
    "catedral,universidad del cauca,unicauca,sena,"
    "hospital san josé,clínica la estancia,terminal,aeropuerto,"
    "galería,estadio,coliseo,morro de tulcán,polideportivo,"
    "centro comercial campanario,terra plaza,anarkos,éxito,"
    # Corregimientos
    "julumito,la yunga,calibío,poblazón,las guacas,pisojé,"
    # City name
    "popayán"
)


POPAYAN_PLACES: dict = {
    # ── Centros Comerciales ──
    "Centro Comercial Campanario": [
        "centro comercial campanario", "campanario", "el campanario",
        "cc campanario", "c.c. campanario", "mall campanario",
    ],
    "Centro Comercial Terra Plaza": [
        "centro comercial terra plaza", "terra plaza", "terraplaza",
        "cc terra plaza", "terra", "c.c. terra plaza",
    ],
    "Centro Comercial Anarkos": [
        "centro comercial anarkos", "anarkos", "cc anarkos",
    ],
    "Centro Comercial Plaza Colonial": [
        "plaza colonial", "cc plaza colonial", "centro comercial plaza colonial",
    ],
    "Éxito": ["éxito", "exito", "almacén éxito", "almacen exito", "el éxito"],

    # ── Centro Histórico y alrededores ──
    "Centro Histórico": [
        "centro histórico", "centro historico", "el centro histórico",
        "el centro historico", "casco histórico", "casco antiguo",
    ],
    "Centro": [
        "el centro", "centro de popayán", "centro de popayan",
        "centro de la ciudad", "al centro", "por el centro",
    ],
    "Parque Caldas": [
        "parque caldas", "parque de caldas", "el parque caldas",
        "plaza de caldas", "caldas", "la plaza principal",
        "el parque principal", "parque central",
    ],
    "Torre del Reloj": [
        "torre del reloj", "la torre del reloj", "el reloj",
    ],
    "Puente del Humilladero": [
        "puente del humilladero", "el humilladero", "puente humilladero",
        "el puente del humilladero",
    ],
    "Iglesia San Francisco": [
        "iglesia san francisco", "san francisco", "iglesia de san francisco",
        "templo san francisco",
    ],
    "Iglesia Santo Domingo": [
        "iglesia santo domingo", "santo domingo", "templo santo domingo",
    ],
    "Catedral Basílica": [
        "catedral", "la catedral", "catedral basílica", "catedral basilica",
        "iglesia catedral",
    ],
    "Pandiguando": ["pandiguando", "el pandiguando", "estatua pandiguando"],
    "Morro de Tulcán": ["morro de tulcán", "morro de tulcan", "el morro", "tulcán", "tulcan"],
    "Pueblito Patojo": ["pueblito patojo", "el pueblito patojo", "rincón payanés", "rincon payanes"],

    # ── Universidades ──
    "Universidad del Cauca": [
        "universidad del cauca", "unicauca", "la unicauca",
        "u del cauca", "la universidad del cauca",
    ],
    "Universidad Autónoma": [
        "universidad autónoma", "universidad autonoma", "uniautónoma",
        "uniautonoma", "la autónoma", "la autonoma",
    ],
    "Fundación Universitaria de Popayán": [
        "fundación universitaria", "fundacion universitaria", "fup", "la fup",
    ],
    "SENA Popayán": ["sena", "el sena", "sena popayán", "sena popayan"],
    "Colegio Mayor del Cauca": [
        "colegio mayor", "colegio mayor del cauca", "unimayor",
    ],
    "Universidad Antonio Nariño": [
        "universidad antonio nariño", "universidad antonio narino",
        "antonio nariño universidad",
    ],
    "Fundación Universitaria María Cano": [
        "maría cano", "maria cano", "universidad maría cano",
        "universidad maria cano", "fundación maría cano",
    ],

    # ── Hospitales / Clínicas ──
    "Hospital Universitario San José": [
        "hospital universitario san josé", "hospital universitario san jose",
        "hospital universitario", "hospital san josé", "hospital san jose",
        "el hospital", "san josé hospital",
    ],
    "Clínica La Estancia": [
        "clínica la estancia", "clinica la estancia", "la estancia clínica",
        "clínica estancia",
    ],
    "Clínica San Rafael": [
        "clínica san rafael", "clinica san rafael", "san rafael clínica",
    ],
    "Clínica Santa Gracia": [
        "clínica santa gracia", "clinica santa gracia", "santa gracia",
    ],
    "Hospital María Occidente": [
        "hospital maría occidente", "hospital maria occidente",
    ],
    "Cruz Roja Popayán": ["cruz roja", "la cruz roja"],

    # ── Terminal / Aeropuerto ──
    "Terminal de Transporte": [
        "terminal de transporte", "terminal de transportes",
        "la terminal", "terminal", "el terminal",
    ],
    "Aeropuerto Guillermo León Valencia": [
        "aeropuerto guillermo león valencia", "aeropuerto guillermo leon valencia",
        "aeropuerto", "el aeropuerto", "aeropuerto de popayán",
    ],

    # ── Parques / Plazas / Ríos ──
    "Parque de las Aves": ["parque de las aves", "las aves"],
    "Río Molino": ["río molino", "rio molino", "el río molino", "el rio molino"],
    "Río Ejido": ["río ejido", "rio ejido"],
    "Río Cauca": ["río cauca", "rio cauca"],
    "Estadio Ciro López": [
        "estadio ciro lópez", "estadio ciro lopez", "el estadio",
        "estadio", "ciro lópez", "ciro lopez",
    ],
    "Coliseo": ["coliseo", "el coliseo", "coliseo de popayán"],

    # ── Galerías / Mercados ──
    "Galería La Esmeralda": [
        "galería la esmeralda", "galeria la esmeralda",
        "galería", "galeria", "la galería", "la galeria",
        "plaza de mercado", "la plaza de mercado",
    ],
    "Galería de Bolívar": [
        "galería bolívar", "galeria bolivar", "galería de bolívar",
    ],

    # ── Entidades públicas ──
    "Gobernación del Cauca": ["gobernación", "gobernacion", "gobernación del cauca"],
    "Alcaldía de Popayán": ["alcaldía", "alcaldia", "alcaldía de popayán"],
    "Fiscalía": ["fiscalía", "fiscalia", "la fiscalía"],
    "Registraduría": ["registraduría", "registraduria"],
    "Bomberos Popayán": ["bomberos", "los bomberos", "estación de bomberos"],

    # ── Barrios especiales / urbanizaciones ──
    "Valle del Ortigal": [
        "valle del ortigal", "el ortigal", "ortigal",
        "barrio valle del ortigal", "urbanización valle del ortigal",
        "conjunto valle del ortigal",
    ],
    "Villa del Viento": ["villa del viento", "barrio villa del viento", "villas del viento"],
    "El Jardín": ["el jardín", "el jardin", "barrio el jardín"],
    "Torres del Río": ["torres del río", "torres del rio", "barrio torres del río"],
    "Rincón de la Estancia": ["rincón de la estancia", "rincon de la estancia"],
    "Provitec": ["provitec", "barrio provitec"],
    "Zaguan": ["zaguan", "barrio zaguan", "el zaguan"],

    # ── BARRIOS COMUNA 1 (Norte / Noroccidente) ──
    "Modelo": ["modelo", "barrio modelo", "el modelo"],
    "Loma Linda": ["loma linda", "barrio loma linda"],
    "Prados del Norte": ["prados del norte", "barrio prados del norte"],
    "La Cabaña": ["la cabaña", "la cabana", "barrio la cabaña"],
    "Santa Clara": ["santa clara", "barrio santa clara"],
    "Casas Fiscales": ["casas fiscales", "barrio casas fiscales"],
    "Nueva Granada": ["nueva granada", "barrio nueva granada"],
    "Machángara": ["machángara", "machangara", "barrio machángara"],
    "La Playa": ["la playa", "barrio la playa"],
    "Campamento": ["campamento", "barrio campamento"],
    "Puerta de Hierro": ["puerta de hierro", "barrio puerta de hierro"],
    "Pubenza": ["pubenza", "barrio pubenza"],
    "Antonio Nariño": ["antonio nariño", "antonio narino", "barrio antonio nariño"],
    "Campobello": ["campobello", "barrio campobello"],
    "El Recuerdo": ["el recuerdo", "barrio el recuerdo"],
    "Belalcázar": ["belalcázar", "belalcazar", "barrio belalcázar"],
    "Los Laureles": ["los laureles", "barrio los laureles"],
    "Los Rosales": ["los rosales", "barrio los rosales"],
    "Alcalá": ["alcalá", "alcala", "barrio alcalá"],
    "Monterrosales": ["monterrosales", "barrio monterrosales"],
    "Ciudad Capri": ["ciudad capri", "capri", "barrio capri"],
    "Puerta del Sol": ["puerta del sol", "barrio puerta del sol"],

    # ── BARRIOS COMUNA 2 (Norte) ──
    "Pino Pardo": ["pino pardo", "barrio pino pardo"],
    "Balcón del Norte": ["balcón del norte", "balcon del norte"],
    "María Paz": ["maría paz", "maria paz", "barrio maría paz"],
    "Zuldemaida": ["zuldemaida", "barrio zuldemaida"],
    "Santiago de Cali": ["santiago de cali", "barrio santiago de cali"],
    "Morinda": ["morinda", "barrio morinda"],
    "El Tablazo": ["el tablazo", "barrio el tablazo"],
    "La Florida": ["la florida", "barrio la florida"],
    "La Primavera": ["la primavera", "barrio la primavera"],
    "Villa del Norte": ["villa del norte", "barrio villa del norte"],
    "El Placer": ["el placer", "barrio el placer"],
    "Bello Horizonte": ["bello horizonte", "bellohorizonte", "barrio bello horizonte"],
    "Cruz Roja (barrio)": ["barrio cruz roja", "sector cruz roja"],
    "El Bambú": ["el bambú", "el bambu", "barrio el bambú"],
    "Bella Vista": ["bella vista", "barrio bella vista", "bellavista"],
    "San Ignacio": ["san ignacio", "barrio san ignacio"],
    "La Arboleda": ["la arboleda", "barrio la arboleda"],
    "La Esperanza": ["la esperanza", "barrio la esperanza"],
    "Canterbury": ["canterbury"],
    "Villa del Viento": ["villa del viento", "barrio villa del viento"],
    "Los Cámbulos": ["los cámbulos", "los cambulos", "barrio los cámbulos"],
    "El Pinar": ["el pinar", "barrio el pinar"],
    "Guayacanes del Río": ["guayacanes del río", "guayacanes del rio", "guayacanes"],
    "Minuto de Dios": ["minuto de dios", "barrio minuto de dios"],
    "Chamizal": ["chamizal", "barrio chamizal", "el chamizal"],
    "Matamoros": ["matamoros", "barrio matamoros"],
    "Los Ángeles": ["los ángeles", "los angeles", "barrio los ángeles"],
    "Pinares": ["pinares", "barrio pinares"],
    "San Fernando": ["san fernando", "barrio san fernando"],
    "Luna Blanca": ["luna blanca", "barrio luna blanca"],
    "Urbanización La Aldea": ["la aldea", "urbanización la aldea"],

    # ── BARRIOS COMUNA 3 (Oriente) ──
    "Bolívar": ["bolívar", "bolivar", "barrio bolívar", "barrio bolivar"],
    "Ciudad Jardín": ["ciudad jardín", "ciudad jardin", "barrio ciudad jardín"],
    "Periodistas": ["periodistas", "barrio periodistas"],
    "Sotará": ["sotará", "sotara", "barrio sotará"],
    "Deportistas": ["deportistas", "barrio deportistas"],
    "Los Hoyos": ["los hoyos", "barrio los hoyos"],
    "Yambitará": ["yambitará", "yambitara", "barrio yambitará"],
    "Villa Mercedes": ["villa mercedes", "barrio villa mercedes"],
    "Yanaconas": ["yanaconas", "barrio yanaconas"],
    "La Ximena": ["la ximena", "barrio la ximena", "ximena"],
    "Pueblillo": ["pueblillo", "el pueblillo", "barrio pueblillo"],
    "José Antonio Galán": ["josé antonio galán", "jose antonio galan", "galán", "galan"],
    "Torres del Río": ["torres del río", "torres del rio"],
    "Galicia": ["galicia", "barrio galicia"],
    "La Estancia": ["la estancia", "barrio la estancia", "estancia"],
    "Moravia": ["moravia", "barrio moravia"],
    "Alicante": ["alicante", "barrio alicante"],
    "Acacias": ["acacias", "barrio acacias", "las acacias"],

    # ── BARRIOS COMUNA 4 (Centro) ──
    "Santa Teresita": ["santa teresita", "barrio santa teresita"],
    "Vásquez Cobo": ["vásquez cobo", "vasquez cobo", "barrio vásquez cobo"],
    "El Prado": ["el prado", "barrio el prado"],
    "Siglo XX": ["siglo veinte", "siglo xx", "barrio siglo xx"],
    "Los Álamos": ["los álamos", "los alamos", "barrio los álamos"],
    "San Rafael Viejo": ["san rafael viejo", "barrio san rafael viejo"],
    "El Refugio": ["el refugio", "barrio el refugio", "refugio"],
    "Liceo": ["liceo", "barrio liceo", "el liceo"],
    "La Pamba": ["la pamba", "barrio la pamba", "pamba"],
    "Loma de Cartagena": ["loma de cartagena", "barrio loma de cartagena"],
    "El Empedrado": ["el empedrado", "barrio el empedrado", "empedrado"],
    "San Camilo": ["san camilo", "barrio san camilo"],
    "Hernando Lora": ["hernando lora", "barrio hernando lora"],

    # ── BARRIOS COMUNA 5 (Oriente / Sur-Oriente) ──
    "Avelino Ull": ["avelino ull", "barrio avelino ull", "avelino"],
    "Los Braceros": ["los braceros", "barrio los braceros"],
    "El Lago": ["el lago", "barrio el lago"],
    "Berlín": ["berlín", "berlin", "barrio berlín"],
    "Suizo": ["suizo", "barrio suizo", "el suizo"],
    "Las Ferias": ["las ferias", "barrio las ferias"],
    "La Campiña": ["la campiña", "la campina", "barrio la campiña"],
    "María Oriente": ["maría oriente", "maria oriente", "barrio maría oriente", "barrio maria oriente"],
    "Los Sauces": ["los sauces", "barrio los sauces", "sauces"],
    "Santa Mónica": ["santa mónica", "santa monica", "barrio santa mónica"],
    "La Floresta": ["la floresta", "barrio la floresta", "floresta"],
    "Los Andes": ["los andes", "barrio los andes"],
    "La Alameda": ["la alameda", "barrio la alameda", "alameda"],
    "El Plateado": ["el plateado", "barrio el plateado", "plateado"],
    "Villa Oriente": ["villa oriente", "barrio villa oriente"],
    "San Andrés": ["san andrés", "san andres", "barrio san andrés"],
    "Altos Sauces": ["altos sauces", "poblado de los altos sauces", "altos de los sauces"],
    "Portal de Santa Mónica": ["portal de santa mónica", "portal de santa monica", "portal santa mónica"],

    # ── BARRIOS COMUNA 6 (Sur / Sur-Occidente) ──
    "Alfonso López": ["alfonso lópez", "alfonso lopez", "barrio alfonso lópez", "barrio alfonso lopez"],
    "Valparaíso": ["valparaíso", "valparaiso", "barrio valparaíso"],
    "Primero de Mayo": ["primero de mayo", "barrio primero de mayo", "1 de mayo"],
    "Los Comuneros": ["los comuneros", "barrio los comuneros", "comuneros"],
    "Loma de la Virgen": ["loma de la virgen", "barrio loma de la virgen", "la virgen"],
    "Sindical": ["sindical", "barrio sindical"],
    "Calicanto": ["calicanto", "barrio calicanto"],
    "Deán Bajo": ["deán bajo", "dean bajo", "barrio deán bajo"],
    "Gabriel García Márquez": [
        "gabriel garcía márquez", "gabriel garcia marquez",
        "barrio garcía márquez", "barrio garcia marquez", "garcía márquez",
    ],
    "Jorge Eliécer Gaitán": [
        "jorge eliécer gaitán", "jorge eliecer gaitan",
        "barrio gaitán", "barrio gaitan", "gaitán", "gaitan",
    ],
    "Limonar": ["limonar", "barrio limonar", "el limonar"],
    "La Paz Sur": ["la paz sur", "barrio la paz sur", "la paz"],
    "La Gran Victoria": ["la gran victoria", "barrio la gran victoria", "gran victoria"],
    "Versalles": ["versalles", "barrio versalles"],
    "La Ladera": ["la ladera", "barrio la ladera", "ladera"],
    "La Colina": ["la colina", "barrio la colina", "colina"],
    "Nuevo Japón": ["nuevo japón", "nuevo japon", "barrio nuevo japón"],
    "Tejares de Otón": ["tejares de otón", "tejares de oton", "barrio tejares"],
    "Las Veraneras": ["las veraneras", "veraneras", "barrio las veraneras"],
    "Panamericano": ["panamericano", "barrio panamericano"],
    "Camino Real": ["camino real", "barrio camino real"],

    # ── BARRIOS COMUNA 7 (Occidente) ──
    "Nazaret": ["nazaret", "barrio nazaret"],
    "Isabela": ["isabela", "barrio isabela"],
    "Las Palmas": ["las palmas", "barrio las palmas"],
    "Colombia II Etapa": ["colombia segunda etapa", "colombia dos"],
    "Los Campos": ["los campos", "barrio los campos"],
    "Treinta y Uno de Marzo": ["treinta y uno de marzo", "31 de marzo"],
    "El Mirador": ["el mirador", "barrio el mirador", "mirador"],
    "Las Vegas": ["las vegas", "barrio las vegas"],
    "Solidaridad": ["solidaridad", "barrio solidaridad"],
    "Chapinero": ["chapinero", "barrio chapinero"],
    "Retiro Alto": ["retiro alto", "barrio retiro alto"],
    "Nuevo Popayán": ["nuevo popayán", "nuevo popayan", "barrio nuevo popayán"],
    "La Unión": ["la unión", "la union", "barrio la unión"],
    "La Libertad": ["la libertad", "barrio la libertad"],
    "La Conquista": ["la conquista", "barrio la conquista"],
    "Las Brisas": ["las brisas", "barrio las brisas"],
    "Independencia": ["independencia", "barrio independencia"],
    "Santa Librada": ["santa librada", "barrio santa librada"],
    "Corsocial": ["corsocial", "barrio corsocial"],
    "Villa Occidente": ["villa occidente", "barrio villa occidente"],
    "Villa España": ["villa españa", "villa espana", "barrio villa españa"],

    # ── BARRIOS COMUNA 8 (Noroccidente) ──
    "Pandiguando (barrio)": ["barrio pandiguando"],
    "El Libertador": ["el libertador", "barrio el libertador", "libertador"],
    "El Triunfo": ["el triunfo", "barrio el triunfo", "triunfo"],
    "Popular": ["popular", "barrio popular", "el popular"],
    "La Cañada": ["la cañada", "la canada", "barrio la cañada"],
    "Llano Largo": ["llano largo", "barrio llano largo"],
    "José María Obando": ["josé maría obando", "jose maria obando", "obando"],
    "Guayabal": ["guayabal", "barrio guayabal", "el guayabal"],
    "La Isla": ["la isla", "barrio la isla"],
    "Esperanza Sur": ["esperanza sur", "barrio esperanza sur"],
    "Camilo Torres": ["camilo torres", "barrio camilo torres"],
    "Junín": ["junín", "junin", "barrio junín"],
    "Santa Helena": ["santa helena", "barrio santa helena"],
    "Lomas de Granada": ["lomas de granada", "barrio lomas de granada", "granada"],
    "Mis Ranchitos": ["mis ranchitos", "barrio mis ranchitos"],
    "La Capitana": ["la capitana", "barrio la capitana"],
    "San Antonio de Padua": ["san antonio de padua", "san antonio", "barrio san antonio"],
    "Kennedy": ["kennedy", "barrio kennedy"],
    "San José (barrio)": ["barrio san josé", "barrio san jose"],
    "La Sombrilla": ["la sombrilla", "barrio la sombrilla"],
    "Carlos Primero": ["carlos primero", "barrio carlos primero"],
    "Cinco de Abril": ["cinco de abril", "5 de abril", "barrio cinco de abril"],
    "María Occidente": ["maría occidente", "maria occidente", "barrio maría occidente"],
    "Los Naranjos": ["los naranjos", "barrio los naranjos"],
    "Nuevo Hogar": ["nuevo hogar", "barrio nuevo hogar"],
    "La Esmeralda": ["la esmeralda", "esmeralda", "barrio la esmeralda"],

    # ── BARRIOS COMUNA 9 (Sur-Occidente) ──
    "Pomona": ["pomona", "barrio pomona"],
    "Lomas de Pomona": ["lomas de pomona", "barrio lomas de pomona"],
    "Bosques de Pomona": ["bosques de pomona", "barrio bosques de pomona"],
    "El Uvo": ["el uvo", "barrio el uvo"],
    "Las Américas": ["las américas", "las americas", "barrio las américas"],
    "Santa Rosa": ["santa rosa", "barrio santa rosa"],
    "Los Tejares": ["los tejares", "barrio los tejares", "tejares"],
    "El Cadillal": ["el cadillal", "barrio el cadillal", "cadillal"],
    "Valencia": ["valencia", "barrio valencia"],
    "Santa Inés": ["santa inés", "santa ines", "barrio santa inés"],
    "El Sendero": ["el sendero", "barrio el sendero"],

    # ── Corregimientos / Zonas rurales ──
    "Julumito": ["julumito", "vereda julumito"],
    "La Yunga": ["la yunga", "vereda la yunga"],
    "San Bernardino": ["san bernardino", "vereda san bernardino"],
    "Calibío": ["calibío", "calibio", "vereda calibío"],
    "Poblazón": ["poblazón", "poblazon", "resguardo poblazón"],
    "Quintana": ["quintana", "resguardo quintana"],
    "Las Guacas": ["las guacas", "vereda las guacas"],
    "Los Cerrillos": ["los cerrillos", "cerrillos", "vereda los cerrillos"],
    "Pisojé": ["pisojé", "pisoje", "vereda pisojé"],
    "La María": ["la maría", "la maria", "vereda la maría"],
    "Puelenje": ["puelenje", "vereda puelenje"],
    "Coconuco": ["coconuco", "vereda coconuco"],
    "Torres de Comfacauca": ["torres de comfacauca", "comfacauca"],
    "Vereda de Torres": ["vereda de torres", "veredas de torres"],
}


# Catálogo de Lugares de Referencia — Popayán, Cauca, Colombia

> **Propósito (leer primero).** Este catálogo se usa **ÚNICAMENTE** para mejorar la
> validación / clasificación y normalización del parser de direcciones — es decir,
> para decidir si un topónimo dicho por el usuario es un `NEIGHBORHOOD` (barrio),
> un `LANDMARK` (punto de referencia) o un `PLACE_NAME` (institución / lugar con
> nombre propio), y para normalizar variantes de dictado/STT hacia un nombre
> canónico. **NUNCA** se debe usar para inventar, completar o sustituir una
> dirección que el usuario no dijo. Si el usuario no mencionó un lugar de esta
> lista, el sistema no debe insertarlo.

**Alcance:** estrictamente Popayán y el departamento del Cauca. No incluye lugares
de otras ciudades.

**Convenciones de las tablas:**
- `Canonical name`: nombre normalizado preferido.
- `Type`: NEIGHBORHOOD | CONJUNTO | UNIVERSITY | COLEGIO | HEALTH | MALL | PUBLIC_ENTITY | LANDMARK | PARK.
- `Common spoken/STT variants`: variantes de dictado. Las marcadas **(USAGE)** son
  mis-audiciones fonéticas *inferidas* (no oficiales); sirven solo como pistas de
  normalización, no como hechos verificados.
- `Source`: número de la lista **Sources** al final. `UNVERIFIED` = no confirmado
  contra fuente primaria.

> **Nota de verificación general.** La lista maestra oficial de barrios por comuna
> proviene del plano **"Comunas Popayán"** de la Alcaldía (fuente [1]). Ese PDF es
> un plano cartográfico; su texto se extrajo de forma fragmentaria, por lo que los
> nombres de barrio se corroboraron adicionalmente con listados secundarios que
> citan el mismo plano. Donde el barrio no pudo confirmarse contra [1]/[2] se marca
> UNVERIFIED. **Popayán tiene 9 comunas urbanas** ([1][2]); las fuentes divergen en
> el conteo total de barrios (se citan cifras de ~258 a ~295), lo cual se refleja
> como pregunta abierta.

---

## 1. Barrios por comuna

> Los barrios cuya comuna exacta no pudo confirmarse contra [1]/[2] se marcan
> UNVERIFIED aunque el nombre del barrio sí exista. **Comuna 9 no pudo detallarse**
> desde las fuentes consultadas (ver Preguntas abiertas).

### Comuna 1 (Norte)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Modelo | NEIGHBORHOOD | "el modelo" | 1 |
| Loma Linda | NEIGHBORHOOD | "loma linda", "lomalinda" | 1 |
| Prados del Norte | NEIGHBORHOOD | "prados norte" | 1 |
| La Cabaña | NEIGHBORHOOD | "la cabania" (USAGE) | 1 |
| Santa Clara | NEIGHBORHOOD | — | 1 |
| Casas Fiscales | NEIGHBORHOOD | "casa fiscales" (USAGE) | 1 |
| Nueva Granada (Champagnat) | NEIGHBORHOOD | "champañat", "champaña" (USAGE) | 1 |
| Machángara | NEIGHBORHOOD | "machangara", "manchangara" (USAGE) | 1 |
| La Playa | NEIGHBORHOOD | — | 1 |
| Campamento | NEIGHBORHOOD | — | 1 |
| Puerta de Hierro | NEIGHBORHOOD | "puerto de hierro" (USAGE) | 1 |
| Catay | NEIGHBORHOOD | "catai", "cataly" (USAGE) | 1 |
| Antonio Nariño | NEIGHBORHOOD | "antonio narino", "nariño" | 1 |
| Villa Paola | NEIGHBORHOOD | — | 1 |
| Campo Bello | NEIGHBORHOOD | "campobello" | 1 |
| El Recuerdo | NEIGHBORHOOD | "recuerdo norte" (USAGE) | 1 |
| La Villa | NEIGHBORHOOD | — | 1 |
| Bloques de Pubenza | NEIGHBORHOOD | "pubenza", "pubensa" (USAGE) | 1 |
| Belalcázar | NEIGHBORHOOD | "belalcazar", "belarcazar" (USAGE) | 1 |
| Los Laureles | NEIGHBORHOOD | — | 1 |
| Los Rosales | NEIGHBORHOOD | "monte rosales" (distinto) | 1 |
| Alcalá | NEIGHBORHOOD | "alcala" | 1 |
| Monte Rosales | NEIGHBORHOOD | "monterrosales" | 1 |
| Fancal Capri | NEIGHBORHOOD | "capri", "fancal" | 1 UNVERIFIED |
| María Alejandra | NEIGHBORHOOD | — | 1 |
| Navarra | NEIGHBORHOOD | — | 1 |
| Cerritos de la Paz | NEIGHBORHOOD | "cerritos" | 1 UNVERIFIED |

### Comuna 2 (Nororiente)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Villa Melisa | NEIGHBORHOOD | "villa melissa" | 1 |
| La Esperanza | NEIGHBORHOOD | "esperanza" | 1 |
| Canterbury | NEIGHBORHOOD | "canterburi", "canterbery" (USAGE) | 1 |
| La Arboleda | NEIGHBORHOOD | "arboleda" | 1 |
| El Uvo | NEIGHBORHOOD | "eluvo", "el huvo" (USAGE) | 1 |
| San Ignacio | NEIGHBORHOOD | — | 1 |
| Bella Vista | NEIGHBORHOOD | "bellavista" | 1 |
| El Bambú | NEIGHBORHOOD | "bambu", "el vambu" (USAGE) | 1 |
| Cruz Roja | NEIGHBORHOOD | — | 1 |
| Río Vista | NEIGHBORHOOD | "riovista" | 1 |
| Bello Horizonte | NEIGHBORHOOD | "belo horizonte" (USAGE) | 1 |
| El Placer | NEIGHBORHOOD | "placer" | 1 |
| Villa del Norte | NEIGHBORHOOD | — | 1 |
| La Primavera | NEIGHBORHOOD | "primavera" | 1 |
| Rinconcito Primaveral | NEIGHBORHOOD | — | 1 |
| La Florida | NEIGHBORHOOD | "florida" | 1 |
| González | NEIGHBORHOOD | "gonzalez", "barrio gonzalez" | 1 |
| El Tablazo | NEIGHBORHOOD | "tablazo" | 1 |
| Morinda | NEIGHBORHOOD | "morínda" | 1 |
| Destechados | NEIGHBORHOOD | "los destechados", "destechados" (USAGE) | 1 |
| Santiago de Cali | NEIGHBORHOOD | — | 1 |
| Zuldemaida | NEIGHBORHOOD | "suldemaida", "zulde maida" (USAGE) | 1 |
| María Paz | NEIGHBORHOOD | "mariapaz" | 1 |
| Balcón Norte | NEIGHBORHOOD | — | 1 |
| Pino Pardo | NEIGHBORHOOD | "pinopardo" | 1 |
| Matamoros | NEIGHBORHOOD | — | 1 |
| Chamizal | NEIGHBORHOOD | "chamisal" (USAGE) | 1 |
| Tóez | NEIGHBORHOOD | "toes", "toez" (USAGE) | 1 |
| Villa Claudia | NEIGHBORHOOD | — | 1 |
| Guayacanes del Río | NEIGHBORHOOD | "guayacanes" | 1 |
| El Pinar | NEIGHBORHOOD | "pinar" | 1 |
| Los Cámbulos | NEIGHBORHOOD | "cambulos" | 1 |
| Luna Blanca | NEIGHBORHOOD | — | 1 |
| Cordillera | NEIGHBORHOOD | — | 1 |
| Villa del Viento | NEIGHBORHOOD | — | 1 |
| Pinares y Canal de Brujas | NEIGHBORHOOD | "canal de brujas", "pinares" | 1 |
| Los Ángeles | NEIGHBORHOOD | "los angeles" | 1 |
| Galilea | NEIGHBORHOOD | — | 1 |

### Comuna 3 (Suroriente / sector Yanaconas)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Bolívar | NEIGHBORHOOD | "bolivar" (también sector histórico) | 1 |
| Ciudad Jardín | NEIGHBORHOOD | "ciudad jardin" | 1 |
| Los Periodistas | NEIGHBORHOOD | "periodistas" | 1 |
| Sotará | NEIGHBORHOOD | "sotara", "sotará" | 1 |
| Los Deportistas | NEIGHBORHOOD | "deportistas" | 1 |
| Los Hoyos | NEIGHBORHOOD | "loyos" (USAGE) | 1 |
| Yambitará | NEIGHBORHOOD | "yambitara", "llambitara" (USAGE) | 1 |
| Villa Mercedes | NEIGHBORHOOD | — | 1 |
| Yanaconas | NEIGHBORHOOD | "yanacona", "llanaconas" (USAGE) | 1 |
| La Ximena | NEIGHBORHOOD | "la jimena", "ximena" (USAGE) | 1 |
| Palace | NEIGHBORHOOD | "palas", "palace" (USAGE) | 1 |
| Pueblillo | NEIGHBORHOOD | "pueblito" (confusión con Pueblito Patojo) (USAGE) | 1 |
| Vega de Prieto | NEIGHBORHOOD | "vega prieto" | 1 |
| José Antonio Galán | NEIGHBORHOOD | "galan" | 1 |
| Las Tres Margaritas | NEIGHBORHOOD | "tres margaritas" | 1 |

### Comuna 4 (Centro histórico y aledaños)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Centro (Centro Histórico) | NEIGHBORHOOD | "el centro", "centro historico" | 1, 4 |
| Caldas | NEIGHBORHOOD | "parque caldas" (confusión landmark) | 1 |
| La Pamba | NEIGHBORHOOD | "lapamba", "la bamba" (USAGE) | 1 |
| El Empedrado | NEIGHBORHOOD | "empedrado" | 1 |
| San Camilo | NEIGHBORHOOD | — | 1 |
| Las Américas | NEIGHBORHOOD | "americas" | 1 |
| Colombia I Etapa | NEIGHBORHOOD | "colombia primera etapa" | 1 |
| Argentina | NEIGHBORHOOD | "la argentina" | 1 |
| El Cadillal | NEIGHBORHOOD | "cadillal", "cadial" (USAGE) | 1 |
| Valencia | NEIGHBORHOOD | — | 1 |
| El Achiral | NEIGHBORHOOD | "achiral" | 1 |
| Hernando Lora | NEIGHBORHOOD | — | 1 |
| Moscopán | NEIGHBORHOOD | "moscopan" | 1 UNVERIFIED |
| Obrero | NEIGHBORHOOD | "el obrero" | 1 |
| Santa Inés | NEIGHBORHOOD | "santa ines" | 1 |
| Fucha | NEIGHBORHOOD | "la fucha", "pucha" (USAGE) | 1 |
| Loma de Cartagena | NEIGHBORHOOD | "loma cartagena" | 1 |
| El Liceo | NEIGHBORHOOD | "liceo" | 1 |
| El Refugio | NEIGHBORHOOD | "refugio" | 1 |
| San Rafael | NEIGHBORHOOD | (nombre también en Comuna 6) | 1 UNVERIFIED |
| Los Álamos | NEIGHBORHOOD | "alamos" | 1 |
| Siglo XX | NEIGHBORHOOD | "siglo veinte" | 1 |
| El Prado | NEIGHBORHOOD | "prado" | 1 |
| Vásquez Cobo | NEIGHBORHOOD | "vasquez cobo" | 1 |
| Santa Teresita | NEIGHBORHOOD | — | 1 |
| Pomona | NEIGHBORHOOD | "la pomona" | 1 |
| Bosques de Pomona | NEIGHBORHOOD | "bosques pomona" | 1 |
| Portales del Río | NEIGHBORHOOD | "portales rio" | 1 |
| Santa Catalina | NEIGHBORHOOD | — | 1 |
| Belén | NEIGHBORHOOD | "belen" | 1 |
| Villa Helena | NEIGHBORHOOD | "villa elena" (USAGE) | 1 |
| Fundecur | NEIGHBORHOOD | "fundecur" | 1 UNVERIFIED |
| Provitec II Etapa | NEIGHBORHOOD | "provitec" | 1 UNVERIFIED |

### Comuna 5 (Oriente / sector Las Ferias) — 19 barrios
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Los Sauces | NEIGHBORHOOD | "sauces" | 1 |
| María Oriente | NEIGHBORHOOD | — | 1 |
| Santa Mónica | NEIGHBORHOOD | "santa monica" | 1 |
| Ferias I Etapa | NEIGHBORHOOD | "las ferias", "ferias primera etapa" | 1 |
| Avelino Ull | NEIGHBORHOOD | "avelino ul", "abelino" (USAGE) | 1 |
| Braceros | NEIGHBORHOOD | "los braceros" | 1 |
| Colgate Palmolive | NEIGHBORHOOD | "colgate", "palmolive" | 1 |
| Suizo | NEIGHBORHOOD | "el suizo" | 1 |
| Berlín | NEIGHBORHOOD | "berlin" | 1 |
| Ferias II Etapa (La Campiña) | NEIGHBORHOOD | "la campiña", "campiña", "campina" (USAGE) | 1 |
| El Lago | NEIGHBORHOOD | "lago" | 1 |
| La Floresta | NEIGHBORHOOD | "floresta" | 1 |
| Los Andes | NEIGHBORHOOD | "andes" | 1 |
| La Alameda | NEIGHBORHOOD | "alameda" | 1 |
| El Portal de las Ferias | NEIGHBORHOOD | "portal de las ferias" | 1 |
| San Andrés | NEIGHBORHOOD | "san andres" | 1 |
| Villa Oriente | NEIGHBORHOOD | — | 1 |
| Poblado de los Altos Sauces | NEIGHBORHOOD | "altos sauces", "el poblado" | 1 |
| Portal de Santa Mónica | NEIGHBORHOOD | "portal santa monica" | 1 |

### Comuna 6 (Suroccidente / sector Bello Horizonte–Calicanto)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Nueva Granada | NEIGHBORHOOD | — | 1 |
| San Rafael | NEIGHBORHOOD | — | 1 |
| Samuel Silverio | NEIGHBORHOOD | "samuel silverio bolaños" | 1 |
| Camino Real | NEIGHBORHOOD | — | 1 |
| Nuevo Deán | NEIGHBORHOOD | "nuevo dean", "el dean" | 1 |
| El Salvador | NEIGHBORHOOD | "salvador" | 1 |
| Santa Rita | NEIGHBORHOOD | — | 1 |
| San José de los Tejares | NEIGHBORHOOD | "los tejares", "tejares" | 1 |
| Pajonal | NEIGHBORHOOD | "el pajonal" | 1 |
| Santa Fe | NEIGHBORHOOD | "santafe" | 1 |
| La Ladera | NEIGHBORHOOD | "ladera" | 1 |
| José Hilario López | NEIGHBORHOOD | "hilario lopez" | 1 |
| Valparaíso | NEIGHBORHOOD | "valparaiso" | 1 |
| Primero de Mayo | NEIGHBORHOOD | "1 de mayo" | 1 |
| Los Comuneros | NEIGHBORHOOD | "comuneros" | 1 |
| Loma de la Virgen | NEIGHBORHOOD | "loma la virgen" | 1 |
| Sindical II | NEIGHBORHOOD | "sindical", "sindical dos" | 1 |
| Alfonso López | NEIGHBORHOOD | "alfonso lopez" | 1 |
| Calicanto | NEIGHBORHOOD | "cali canto" (USAGE) | 1 |
| Gabriel García Márquez | NEIGHBORHOOD | "garcia marquez" | 1 |
| El Boquerón | NEIGHBORHOOD | "boqueron" | 1 |
| Jorge Eliécer Gaitán | NEIGHBORHOOD | "gaitan", "jorge eliecer" | 1 |
| El Limonar | NEIGHBORHOOD | "limonar" | 1 |
| La Paz Sur | NEIGHBORHOOD | "la paz" | 1 |
| La Gran Victoria | NEIGHBORHOOD | "gran victoria" | 1 |
| Versalles II | NEIGHBORHOOD | "versalles", "bersalles" (USAGE) | 1 |
| Villa del Carmen | NEIGHBORHOOD | — | 1 |
| La Colina | NEIGHBORHOOD | "colina" | 1 |
| Nuevo Japón | NEIGHBORHOOD | "nuevo japon" | 1 |
| Nuevo País | NEIGHBORHOOD | "nuevo pais" | 1 |
| Tejares de Otón | NEIGHBORHOOD | "tejares de oton" | 1 |
| Veraneras | NEIGHBORHOOD | "las veraneras" | 1 |
| Villareal | NEIGHBORHOOD | "villarreal" | 1 |
| Villa del Sur | NEIGHBORHOOD | — | 1 |
| Palermo | NEIGHBORHOOD | — | 1 |
| Villa Hermosa | NEIGHBORHOOD | "villahermosa" | 1 |
| Nueva Frontera | NEIGHBORHOOD | — | 1 |
| El Recuerdo (Sur) | NEIGHBORHOOD | "recuerdo" (homónimo Comuna 1) | 1 UNVERIFIED |
| Colinas de Calicanto | NEIGHBORHOOD | "colinas calicanto" | 1 |

### Comuna 7 (Norte alto / sector Chapinero–Las Palmas)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Nazareth | NEIGHBORHOOD | "nazaret" | 1 |
| La Isabela | NEIGHBORHOOD | "isabela" | 1 |
| Las Palmas I y II | NEIGHBORHOOD | "las palmas" | 1 |
| Colombia II Etapa | NEIGHBORHOOD | "colombia segunda etapa" | 1 |
| Los Campos | NEIGHBORHOOD | "campos" | 1 |
| Treinta y Uno de Marzo | NEIGHBORHOOD | "31 de marzo" | 1 |
| El Mirador | NEIGHBORHOOD | "mirador" | 1 |
| Tomás Cipriano de Mosquera | NEIGHBORHOOD | "tomas cipriano", "mosquera" | 1 |
| Las Vegas | NEIGHBORHOOD | "vegas" | 1 |
| La Solidaridad | NEIGHBORHOOD | "solidaridad" | 1 |
| Chapinero | NEIGHBORHOOD | "chapinero" | 1 |
| Retiro Alto | NEIGHBORHOOD | "el retiro", "retiro" | 1 |
| Nuevo Popayán | NEIGHBORHOOD | — | 1 |
| La Unión | NEIGHBORHOOD | "union" | 1 |
| La Libertad | NEIGHBORHOOD | "libertad" | 1 |
| La Conquista | NEIGHBORHOOD | "conquista" | 1 |
| Las Brisas | NEIGHBORHOOD | "brisas" | 1 |
| La Independencia | NEIGHBORHOOD | "independencia" | 1 |
| Santa Librada | NEIGHBORHOOD | — | 1 |
| Corsocial | NEIGHBORHOOD | "corsocial" | 1 UNVERIFIED |
| La Heroica | NEIGHBORHOOD | "heroica" | 1 |
| Nuevo Hogar | NEIGHBORHOOD | — | 1 |
| Santo Domingo Sabio (I) | NEIGHBORHOOD | "santo domingo savio" (USAGE) | 1 |
| Villas del Palmar | NEIGHBORHOOD | "villas palmar" | 1 |
| Villa Occidente | NEIGHBORHOOD | — | 1 |

### Comuna 8 (Sur / sector La Esmeralda–Junín)
| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Camilo Torres | NEIGHBORHOOD | "camilo torres" | 2 |
| Junín | NEIGHBORHOOD | "junin" | 2 |
| Santa Helena | NEIGHBORHOOD | "santa elena" (USAGE) | 2 |
| Popular (El Zaguán) | NEIGHBORHOOD | "el popular", "zaguan" | 2 |
| Canadá | NEIGHBORHOOD | "canada" | 2 |
| Llano Largo | NEIGHBORHOOD | "llanolargo", "yano largo" (USAGE) | 2 |
| José María Obando | NEIGHBORHOOD | "obando" | 2 |
| Minuto de Dios (La Esmeralda) | NEIGHBORHOOD | "minuto de dios", "esmeralda" | 2 |
| Guayabal | NEIGHBORHOOD | "el guayabal" | 2 |
| La Esmeralda | NEIGHBORHOOD | "esmeralda" | 2 |
| Libertador | NEIGHBORHOOD | "el libertador" | 2 |
| Pandiguando | NEIGHBORHOOD | "pandihuando", "pandiwando" (USAGE) | 2 |
| La Isla I y II | NEIGHBORHOOD | "la isla" | 2 |
| El Triunfo | NEIGHBORHOOD | "triunfo" | 2 |
| Esperanza Sur | NEIGHBORHOOD | — | 2 |
| Asoprecovi | NEIGHBORHOOD | "asoprecovi" | 2 UNVERIFIED |

### Comuna 9 (Occidente / sector Vegas del Cauca–Lomas de Granada)
> **Verificación:** el plano oficial [1] no fue legible por extracción (PDF binario,
> confirmado esta ronda). Los barrios siguientes provienen del callejero secundario
> OpenAlfa [35] (no pudo abrirse directamente: HTTP 403) y de menciones cruzadas.
> **Lomas de Granada** sí se confirma como Comuna 9 vía Sala de Prensa de la
> Gobernación del Cauca [36]; **La María Occidente** se corrobora con el punto ESE
> "Hospital María Occidente" [32]. El resto queda **UNVERIFIED** hasta transcribir el
> plano [1] o el ítem ArcGIS "COMUNAS Y BARRIOS POPAYAN".

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Lomas de Granada | NEIGHBORHOOD | "lomas de granada", "loma de granada" | 35, 36 |
| La María Occidente | NEIGHBORHOOD | "la maria", "maria occidente" | 32, 35 |
| Vegas del Cauca | NEIGHBORHOOD | "vegas del cauca", "las vegas del cauca" | 35 UNVERIFIED |
| El Edén | NEIGHBORHOOD | "el eden", "eden" | 35 UNVERIFIED |
| Kennedy | NEIGHBORHOOD | "kennedy", "kenedy" (USAGE) | 35 UNVERIFIED |
| Los Lagos | NEIGHBORHOOD | "los lagos", "lagos" | 35 UNVERIFIED |
| Villa Colombia | NEIGHBORHOOD | "villa colombia" | 35 UNVERIFIED |
| San José (Comuna 9) | NEIGHBORHOOD | "san jose" (homónimo Iglesia/otras zonas) | 35 UNVERIFIED |

---

## 2. Conjuntos / urbanizaciones residenciales

> **Advertencia de verificación:** No se localizó un listado oficial de la Alcaldía
> de conjuntos cerrados. Los nombres siguientes se mencionan con frecuencia como
> sectores/urbanizaciones, pero muchos coinciden con nombres de barrio (arriba) y
> su condición de "conjunto cerrado" concreto proviene de listados inmobiliarios,
> no de fuente primaria. Tratar como **USAGE/UNVERIFIED**: útiles para reconocer el
> nombre, no para afirmar tipología.

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Prados del Norte | CONJUNTO | "prados norte" | 15 UNVERIFIED |
| Ciudad Jardín | CONJUNTO | "ciudad jardin" | 15 UNVERIFIED |
| El Recuerdo / Recuerdo Norte | CONJUNTO | "recuerdo norte" | 15 UNVERIFIED |
| Bosques de Pomona | CONJUNTO | "bosques pomona" | 15 UNVERIFIED |
| Portales del Río | CONJUNTO | "portales rio" | 15 UNVERIFIED |
| Portal de Santa Mónica | CONJUNTO | "portal santa monica" | 15 UNVERIFIED |
| Poblado de los Altos Sauces | CONJUNTO | "altos sauces", "el poblado" | 15 UNVERIFIED |

---

## 3. Universidades e instituciones de educación superior

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Universidad del Cauca (Unicauca) | UNIVERSITY | "unicauca", "universidad del cauca", "la del cauca" | 7, 8 |
| Unicauca — Campus Tulcán (Cra 2ª, sector Tulcán) | UNIVERSITY | "sede tulcan", "campus tulcan", "tulcan" | 7, 8 |
| Unicauca — Claustro de Santo Domingo (Derecho) | UNIVERSITY | "santo domingo", "claustro santo domingo" | 7 |
| Unicauca — Edificio Pomona (Ciencias Contables/FCCEA) | UNIVERSITY | "pomona", "edificio pomona" | 7 |
| Unicauca — Facultad Ciencias Agrarias (Las Guacas) | UNIVERSITY | "las guacas", "agrarias" | 7 UNVERIFIED |
| Unicauca — Centro Deportivo Universitario (CDU) | UNIVERSITY | "cdu", "el cdu", "cede portivo" (USAGE) | 7 |
| Institución Universitaria Colegio Mayor del Cauca (Unimayor) | UNIVERSITY | "colegio mayor", "unimayor", "el mayor" | 9 |
| Fundación Universitaria de Popayán (FUP) | UNIVERSITY | "fup", "la fundacion", "fundacion universitaria" | 16 UNVERIFIED (sitio oficial fup.edu.co no verificado) |
| UNAD — CEAD Popayán (Sede Norte: Cra 5 #46N-67) | UNIVERSITY | "unad", "la unad", "cead popayan" | 10 |
| UNAD — Sede Centro (Cra 3 #2-55) | UNIVERSITY | "unad centro" | 10 |
| SENA Regional Cauca — Sede Centro (Cl 4 #2-80) | UNIVERSITY | "el sena", "sena centro", "sena regional" | 11 |
| SENA — Centro Agropecuario (Cra 9 N #71N-60, El Placer/Alto Cauca) | UNIVERSITY | "sena agropecuario", "el agropecuario" | 11 UNVERIFIED (dirección exacta secundaria) |

---

## 4. IPS / hospitales / clínicas

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Hospital Universitario San José | HEALTH | "san jose", "hospital san jose", "el universitario" | 12 |
| Hospital Susana López de Valencia E.S.E. (Cl 15 #17A-196, La Ladera) | HEALTH | "susana lopez", "hospital susana lopez", "la susana" | 13 |
| Clínica La Estancia (Cl 15N #2-256) | HEALTH | "la estancia", "clinica la estancia" | 14 |
| Clínica Santa Gracia (Cl 14N #15-50) | HEALTH | "santa gracia", "clinica santa gracia" | 17 UNVERIFIED (dir. de directorio secundario) |
| Consulta externa HSLV (Cra 8A, sector San Camilo) | HEALTH | "consulta externa susana lopez" | 13 UNVERIFIED |

> STT frecuente: "Susana López" puede oírse como "susana lope", "usana lopez" (USAGE).
> "La Estancia" puede confundirse con el barrio/sector "La Estancia" (USAGE).

---

## 5. Centros comerciales

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Centro Comercial Campanario (Cra 9, norte) | MALL | "campanario", "el campanario", "cc campanario" | 18 |
| Centro Comercial Anarkos (Anarkos Plaza, sector histórico) | MALL | "anarkos", "anarcos", "anarcus" (USAGE), "anarkos plaza" | 19 |
| Centro Comercial Terraplaza | MALL | "terraplaza", "terra plaza", "tierra plaza" (USAGE) | 20 |

---

## 6. Entidades públicas

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Alcaldía de Popayán — Edificio CAM (Cra 6 #4-21) | PUBLIC_ENTITY | "la alcaldia", "el cam", "edificio cam", "centro administrativo municipal" | 5 |
| Concejo Municipal de Popayán (Edificio CAM) | PUBLIC_ENTITY | "el concejo", "concejo municipal" | 6 |
| Gobernación del Cauca | PUBLIC_ENTITY | "la gobernacion", "gobernacion del cauca" | UNVERIFIED |
| Terminal de Transportes de Popayán | PUBLIC_ENTITY | "el terminal", "terminal de transportes", "la terminal" | 21 |
| Aeropuerto Guillermo León Valencia (PPN) | PUBLIC_ENTITY | "el aeropuerto", "guillermo leon valencia", "aeropuerto de popayan" | 22 |
| Palacio de Justicia de Popayán | PUBLIC_ENTITY | "palacio de justicia", "los juzgados" | UNVERIFIED |
| Notarías de Popayán (1ª a 6ª) | PUBLIC_ENTITY | "la notaria primera", "notaria segunda", etc. | UNVERIFIED |

> Nota: "CAM" = **Centro Administrativo Municipal**, sede de la Alcaldía (fuente [5]).
> No confundir con "CAM" de otras ciudades.

---

## 7. Puntos de referencia / landmarks turísticos

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Morro de Tulcán | LANDMARK | "el morro", "morro de tulcan", "morro del tulcan" | 3 |
| Cerro de las Tres Cruces | LANDMARK | "las tres cruces", "tres cruces" | 3 |
| Puente del Humilladero | LANDMARK | "el humilladero", "puente humilladero", "puente del umilladero" (USAGE) | 3 |
| Torre del Reloj | LANDMARK | "torre del reloj", "el reloj", "la torre" | 3 |
| Parque Caldas (Plaza de Caldas) | LANDMARK | "parque caldas", "el parque", "plaza caldas", "parque central" | 3 |
| Teatro Municipal Guillermo Valencia | LANDMARK | "teatro municipal", "el teatro", "guillermo valencia" | 3 |
| Rincón Payanés / Pueblito Patojo | LANDMARK | "pueblito patojo", "rincon payanes", "el pueblito", "pueblito patoco" (USAGE) | 23 |
| La Pamba (sector / iglesia de La Pamba) | LANDMARK | "la pamba", "iglesia la pamba", "la bamba" (USAGE) | 1, 23 |
| Sector Bolívar | LANDMARK | "bolivar", "por el bolivar" | 1, 23 |
| Puente de la Custodia | LANDMARK | "puente de la custodia", "la custodia" | 23 UNVERIFIED |

---

## 8. Colegios / instituciones educativas (primaria y secundaria)

> **Alcance:** solo educación básica/media (colegios y escuelas). La educación
> superior está en §3. **Colegio Mayor del Cauca (Unimayor)** NO es un colegio de
> secundaria: es institución universitaria (ver §3).
>
> **Verificación:** la lista maestra oficial de I.E. la publican la Secretaría de
> Educación Municipal (SEM Popayán, "I.E. Oficiales de Popayán" [25]) y Datos
> Abiertos Colombia [24]; ese XLS/dataset no pudo leerse íntegro esta ronda, por lo
> que la existencia se confirma con sitios oficiales de cada colegio donde los hay
> ([26][27][28]) y el resto queda **UNVERIFIED** (nombre plausible, dirección/comuna
> de directorio secundario). "Colegio" en Popayán suele decirse como el nombre corto
> del plantel.

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Colegio Champagnat (Popayán) — Cra 9 #5N-51, Comuna 1 | COLEGIO | "champañat", "champaña", "colegio champañat" (USAGE) | 26 |
| I.E. INEM Francisco José de Caldas — Transv 9 #3N-02, Comuna 1 | COLEGIO | "el inem", "inem", "colegio inem", "inen" (USAGE) | 27 |
| I.E. Nuestra Señora del Carmen (Franciscanas) — Cra 5A #20N-24, sector La Estancia | COLEGIO | "el carmen", "las franciscanas", "colegio del carmen" | 28 |
| Escuela Normal Superior de Popayán | COLEGIO | "la normal", "normal superior", "la normal superior" | 25 UNVERIFIED |
| I.E. Francisco Antonio de Ulloa — Cl 7 #3-40 | COLEGIO | "ulloa", "francisco antonio ulloa", "el ulloa" | 25 UNVERIFIED (dir. secundaria) |
| I.E. Antonio García Paredes — Cl 17 #12-40 | COLEGIO | "antonio garcia", "garcia paredes" | 25 UNVERIFIED (dir. secundaria) |
| Centro Educativo / Colegio Comfacauca | COLEGIO | "comfacauca", "colegio comfacauca" | 25 UNVERIFIED |
| I.E. Liceo Nacional | COLEGIO | "el liceo", "liceo nacional" (confusión con barrio "El Liceo", Comuna 4) | 25 UNVERIFIED |
| Colegio Sagrado Corazón de Jesús (Bethlemitas) | COLEGIO | "sagrado corazon", "bethlemitas", "las bethlemitas" | 24, 25 UNVERIFIED |
| I.E. Nuestra Señora de Fátima | COLEGIO | "fatima", "colegio fatima" | 24, 25 UNVERIFIED |
| I.E. República de Suiza | COLEGIO | "republica de suiza", "la suiza" | 24, 25 UNVERIFIED |
| I.E. San Agustín | COLEGIO | "san agustin", "colegio san agustin" (confusión con iglesia San Agustín) | 24, 25 UNVERIFIED |

---

## 9. Monumentos / lugares históricos (centro histórico)

> Complementa §7 (landmarks turísticos). **Puente del Humilladero, Torre del Reloj,
> Morro de Tulcán, Cerro de las Tres Cruces y Parque/Plaza de Caldas ya figuran en
> §7** y no se repiten aquí. Esta sección añade iglesias del centro histórico y el
> Panteón de los Próceres. Fuente principal: Alcaldía–Turismo "Iglesias" [29] y
> "Lugares emblemáticos" [3]; Panteón vía Universidad del Cauca [30].

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Basílica Catedral Nuestra Señora de la Asunción | LANDMARK | "la catedral", "catedral", "la basilica" | 29 |
| Iglesia de San Francisco | LANDMARK | "san francisco", "iglesia san francisco" | 29, 3 |
| Iglesia de Santo Domingo (Cra 5 con Cl 4) | LANDMARK | "santo domingo" (también claustro Unicauca, §3) | 29 |
| Iglesia La Ermita | LANDMARK | "la ermita", "la hermita" (USAGE) | 29 |
| Templo de La Encarnación | LANDMARK | "la encarnacion", "encarnacion" | 29 |
| Iglesia de San José (Templo de la Compañía) | LANDMARK | "san jose", "iglesia san jose" | 29 |
| Templo de El Carmen | LANDMARK | "el carmen", "iglesia del carmen" | 29 |
| Iglesia y Convento de San Agustín | LANDMARK | "san agustin", "iglesia san agustin" | 29 |
| Capilla de Belén | LANDMARK | "belen", "capilla de belen", "el humilladero de belen" | 29, 3 |
| Panteón de los Próceres | LANDMARK | "panteon", "panteon de los proceres", "pante on" (USAGE) | 30 |

---

## 10. Parques

> **Advertencia:** "Parque de las Banderas" es un hito conocido de **Cali**, no de
> Popayán; se incluye solo como alerta de posible confusión y se marca UNVERIFIED
> (no se localizó fuente primaria que lo ubique en Popayán). Parque/Plaza de Caldas
> ya está en §7.

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Parque Caldas (Plaza de Caldas) | PARK | "parque caldas", "el parque", "plaza caldas", "parque central" | 3 |
| Parque Mosquera | PARK | "parque mosquera", "mosquera" (confusión con apellido/barrio) | 31 |
| Parque de las Banderas | PARK | "las banderas", "parque banderas" | UNVERIFIED (probable hito de Cali, no Popayán) |
| Parque lineal / lineales del río Molino y Ejido | PARK | "parque lineal", "el lineal" | UNVERIFIED (no se confirmó nombre/ubicación oficial) |

---

## 11. IPS / puestos de salud / EPS (ampliación de §4)

> Amplía §4 con los puntos de atención de primer nivel de la **ESE Popayán**
> (fuente oficial [32]) y la **IPS Comfacauca** [33]. La **Red de Salud del Norte
> ESE** [34] es una entidad con sedes principalmente rurales del norte del Cauca; se
> incluye por reconocimiento del nombre. Direcciones tomadas del portal oficial ESE
> donde constan; las no listadas quedan UNVERIFIED.

| Canonical name | Type | Common spoken/STT variants | Source |
|---|---|---|---|
| Centro de Salud Sur Occidente (Cl 5 con Cra 4) | HEALTH | "sur occidente", "centro de salud suroccidente" | 32 |
| Hospital María Occidente — ESE (Cl 2 #24-42, sector La María) | HEALTH | "maria occidente", "hospital maria occidente", "la maria" | 32 |
| Hospital Toribío Maya — ESE (Cl 64N #11-13) | HEALTH | "toribio maya", "hospital toribio maya" | 32 |
| Centro de Salud Sur Oriente (Cra 3E #7-13, La Floresta) | HEALTH | "sur oriente", "centro de salud suroriente" | 32 |
| Centro de Salud Loma de la Virgen | HEALTH | "loma de la virgen", "loma la virgen" | 32 |
| Centro de Salud 31 de Marzo (Cl 31 #16A) | HEALTH | "31 de marzo", "treinta y uno de marzo" | 32 |
| Centro de Salud Pueblillo (Cra 4E con Cl 26) | HEALTH | "pueblillo" (confusión con barrio, §Comuna 3) | 32 |
| Centro de Salud Yanaconas (Cra 4 #26N) | HEALTH | "yanaconas" (confusión con barrio, §Comuna 3) | 32 |
| IPS Comfacauca Popayán (Cl 2 con Cra 10, Centro) | HEALTH | "comfacauca", "ips comfacauca", "la comfacauca" | 33 |
| Central de Especialistas IPS (Comfacauca) | HEALTH | "central de especialistas" | 33 UNVERIFIED (dir. no publicada) |
| Red de Salud del Norte E.S.E. | HEALTH | "ese norte", "red de salud del norte", "red del norte" | 34 UNVERIFIED (sedes rurales, no urbano Popayán) |

---

## Sources (URLs)

1. Alcaldía de Popayán — Plano "Comunas Popayán" (POT / cartografía): https://popayan.gov.co/MiMunicipio/Territorios/Comunas%20Popayán.pdf
2. Alcaldía de Popayán — Territorios (índice de cartografías POT): https://popayan.gov.co/MiMunicipio/Paginas/Territorios.aspx
3. Alcaldía de Popayán — Turismo, "Lugares emblemáticos": https://www.popayan.gov.co/SecretariasyEntidades/Turismo/Paginas/Lugares-emblem%C3%A1ticos.aspx
4. Alcaldía de Popayán — "Centro histórico cartografía" (PDF): https://popayan.gov.co/MiMunicipio/Territorios/Centro%20hist%C3%B3rico%20cartograf%C3%ADa.pdf
5. Alcaldía de Popayán — "Alianza con Popayán elimina barreras de acceso al CAM": https://www.popayan.gov.co/NuestraAlcaldia/SaladePrensa/Paginas/Alianza-con-Popay%C3%A1n-elimina-barreras-de-acceso-al-CAM.aspx
6. Concejo Municipal de Popayán (sitio oficial): https://www.concejodepopayan.gov.co/
7. Universidad del Cauca — Wikipedia (ES): https://es.wikipedia.org/wiki/Universidad_del_Cauca
8. Universidad del Cauca — Directorio institucional (oficial): https://www.unicauca.edu.co/directorio-institucional-2/
9. Institución Universitaria Colegio Mayor del Cauca — Unimayor (oficial): https://unimayor.edu.co/web/
10. UNAD — Directorio CEAD Popayán (oficial): https://directorio.unad.edu.co/zona-centro-sur/popayan  ·  https://centrosur.unad.edu.co/popayan
11. SENA — Regional Cauca (oficial): https://www.sena.edu.co/es-co/regionales/zonaPacifica/Paginas/_Cauca.aspx
12. Hospital Universitario San José — nota de prensa (El País): https://www.elpais.com.co/colombia/protesta-de-trabajadores-del-hospital-universitario-san-jose-de-popayan-ante-el-cierre-de-dos-unidades-de-atencion-medica-0906.html
13. Hospital Susana López de Valencia E.S.E. (oficial): https://www.hosusana.gov.co/
14. Clínica La Estancia (oficial): https://laestancia.com.co/web/
15. Portales inmobiliarios (Mitula / Inmobiliaria Adriana Rivera) — listados de conjuntos: https://casas.mitula.com.co/casas/casas-conjunto-cerrado-popayan  ·  https://inmobiliariaadrianarivera.com/inmuebles/
16. Fundación Universitaria de Popayán — perfil (Los Estudiantes): https://losestudiantes.com/fundacion-universitaria-de-popayan
17. Clínica Santa Gracia — directorio (directmap): https://directmap.org/popay%C3%A1n/478
18. Centro Comercial Campanario — Constructora ARINSA: https://constructoraarinsa.com/proyecto/campanario/
19. Centro Comercial Anarkos — Páginas Amarillas: https://www.paginasamarillas.com.co/empresas/centro-comercial-anarkos/popayan-15386615
20. Centro Comercial Terraplaza — Tripadvisor: https://www.tripadvisor.com/Attraction_Review-g319824-d19370484-Reviews-Centro_Comercial_Terraplaza-Popayan_Cauca_Department.html
21. Terminal de Transportes de Popayán (Facebook oficial): https://www.facebook.com/terminaldepopayan/
22. Aeropuerto Guillermo León Valencia — Aeronáutica Civil (oficial): https://www.aerocivil.gov.co/aeropuertos/pages/guillermo-leon-valencia.aspx
23. Colombia Travel (ProColombia) — Popayán: https://colombia.travel/en/popayan
24. Datos Abiertos Colombia — "Instituciones Educativas de Cauca": https://www.datos.gov.co/Educaci-n/Instituciones-Educativas-de-Cauca/rvze-dant
25. Secretaría de Educación Municipal de Popayán — "I.E. Oficiales de Popayán" (XLS, no legible íntegro esta ronda): http://www.sempopayan.gov.co/attachments/article/33/I.E.%20OFICIALES%20DE%20POPAYAN.xls
26. Colegio Champagnat Popayán (sitio oficial): https://champagnatpopayan.edu.co/
27. I.E. INEM Francisco José de Caldas Popayán (sitio oficial): https://inempopayan.edu.co/  ·  Sede principal: https://inempopayan.edu.co/sede-principal/
28. I.E. Nuestra Señora del Carmen — Franciscanas Popayán (sitio oficial): https://www.franciscanas.edu.co/
29. Alcaldía de Popayán — Turismo, "Iglesias": https://www.popayan.gov.co/SecretariasyEntidades/Turismo/Paginas/Iglesias.aspx
30. Universidad del Cauca — Museos, "Panteón de los Próceres": https://portal.unicauca.edu.co/versionP/Conozca%20Unicauca/Museos/Pante%C3%B3n%20de%20los%20Pr%C3%B3ceres
31. Alcaldía de Popayán — Sala de Prensa, renovación del Parque Mosquera: https://www.popayan.gov.co/NuestraAlcaldia/SaladePrensa/Paginas/Inici%C3%B3-la-obra-de-renovaci%C3%B3n-y-recuperaci%C3%B3n-de-espacios-peatonales-en-el-Parque-Mosquera-de-Popay%C3%A1n.aspx
32. ESE Popayán (Empresa Social del Estado) — "Puntos de Atención" (oficial): https://www.esepopayan.gov.co/Puntos-de-Atencion
33. Comfacauca — "IPS Popayán" (oficial): https://www.comfacauca.com/sede/ips-popayan/
34. Red de Salud del Norte E.S.E. (oficial): https://www.esenorte.gov.co/
35. Callejero Colombia / OpenAlfa — "Comuna 9, Perímetro Urbano Popayán" (secundario; HTTP 403 al acceder directo): https://callejero-colombia.openalfa.com/comuna-9_perimetro-urbano-popayan
36. Gobernación del Cauca — Sala de Prensa, "Pavimento en Tu Barrio" en Lomas de Granada (confirma Comuna 9): https://www.cauca.gov.co/Prensa/SaladePrensa/Paginas/Socializacion-de-Pavimento-en-Tu-Barrio-en-Lomas-de-Granada.aspx

---

## Preguntas abiertas / UNVERIFIED

1. **Comuna 9 — parcialmente detallada, mayoría UNVERIFIED.** Se agregaron 8 barrios
   (Lomas de Granada, La María Occidente, Vegas del Cauca, El Edén, Kennedy, Los
   Lagos, Villa Colombia, San José) desde el callejero secundario OpenAlfa [35] y
   corroboraciones cruzadas. Solo **Lomas de Granada** [36] y **La María Occidente**
   [32] tienen respaldo semioficial; el resto es UNVERIFIED. El plano oficial [1]
   volvió a resultar ilegible por extracción (PDF binario). **Acción pendiente:**
   abrir [1] o el ítem ArcGIS `id=3affdc2b43b44016bd34c978a4455a30` ("COMUNAS Y
   BARRIOS POPAYAN") en un visor para confirmar la lista completa y exacta.
2. **Conteo total de barrios en disputa.** Distintas fuentes citan ~258, ~295 o
   "más de 200" barrios en 9 comunas. No se pudo fijar una cifra oficial única.
3. **Asignación comuna↔barrio de algunos homónimos.** "El Recuerdo", "San Rafael"
   y "Esmeralda" aparecen en más de una comuna; marcado UNVERIFIED donde no se
   confirmó contra [1]/[2].
4. **Barrios marcados UNVERIFIED** (Fancal Capri, Cerritos de la Paz, Moscopán,
   Fundecur, Provitec II, Corsocial, Asoprecovi): nombre plausible pero no
   corroborado contra fuente primaria legible.
5. **Conjuntos residenciales:** no hay listado oficial; los nombres provienen de
   portales inmobiliarios [15] y coinciden con barrios. Requiere validación local.
6. **Fundación Universitaria de Popayán:** no se verificó su dominio oficial
   (probable `fup.edu.co`); citado vía perfil terciario [16].
7. **Gobernación del Cauca, Palacio de Justicia, notarías:** no se confirmó
   dirección/sitio oficial en esta ronda (probable `cauca.gov.co` / Rama Judicial).
8. **Direcciones exactas** de clínicas/SENA provienen en parte de directorios
   secundarios; confirmar contra sitios oficiales antes de usarlas como ground truth.
9. **Variantes (USAGE)** son inferencias fonéticas para es-CO/acento payanés; deben
   ajustarse con logs reales de STT, no tomarse como transcripciones observadas.
10. **Colegios (§8):** la lista oficial SEM [25] (XLS) y Datos Abiertos [24] no se
    leyeron íntegros esta ronda. Solo Champagnat [26], INEM [27] y Nuestra Señora del
    Carmen [28] tienen sitio oficial confirmado; los demás planteles y sus
    direcciones/comunas provienen de directorios secundarios (UNVERIFIED). **Acción:**
    descargar y parsear el XLS [25] para la nómina completa de I.E. oficiales con
    comuna/dirección.
11. **Parques (§10):** "Parque de las Banderas" es hito de **Cali**, no confirmado en
    Popayán; y no se localizó el nombre oficial de parques lineales del río Molino/
    Ejido. Confirmar contra cartografía de espacio público de la Alcaldía.
12. **Monumentos/iglesias (§9):** direcciones exactas de la mayoría de iglesias no
    constan en la fuente oficial [29] (solo Santo Domingo: Cra 5 con Cl 4). Útiles
    para clasificar como LANDMARK, no como ground truth de dirección.
13. **IPS/salud (§11):** el portal ESE [32] no publica dirección de algunos centros
    (p. ej. Loma de la Virgen) ni la Central de Especialistas Comfacauca [33];
    marcados UNVERIFIED. Confirmar antes de usar como referencia de ubicación.

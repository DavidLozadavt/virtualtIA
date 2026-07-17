# Módulo WhatsApp de Seguimiento de Aspirantes (SENA) - Procesador de Respuestas

Este módulo actúa como un **procesador de respuestas de plantillas (campañas) sin estado (stateless)**, diseñado específicamente para la Fase 1 del proyecto de Seguimiento de Aspirantes del SENA.

---

## 1. Diseño y Flujo de Operación

A diferencia del módulo conversacional complejo de taxis, este canal funciona de manera completamente pasiva y sin mantener estado conversacional en el microservicio Python de IA.

```
[Usuario Móvil]
       │  (Presiona botón "Sí" o "No")
       ▼
[Meta WhatsApp Webhook]
       │
       ▼
[Laravel Telecom Manager] (Verifica firmas, consulta credenciales de company_id = 2)
       │
       ▼  (Reenvía datos del mensaje universal)
[FastAPI: POST /wh/whatsapp_aspirantes/universal]
       │
       ▼  (Determina de forma stateless si es SI/NO)
[Python: aspirantes_handler]
       │
       ▼  (Informa la respuesta)
[Laravel ERP: POST /api/sena/aspirante/update-response]
```

### Características Principales:
* **Sin Sesión:** El webhook de entrada contiene toda la información necesaria (`phone`, `body`/`button_id`, `message_id`, `company_id`). Python no almacena historiales ni variables temporales en memoria para este canal.
* **Aislamiento Total:** El flujo no se conecta con ninguna clase del módulo de taxis (Tax Belalcázar) ni interfiere con sus sesiones conversacionales.
* **Centralización de Credenciales:** Todas las credenciales de WhatsApp (Access Tokens, Verify Tokens, Phone ID) se guardan en la tabla `telecom_configs` en Laravel ERP. Python delega completamente el envío de mensajes e inicio de campañas a Laravel.

---

## 2. Variables de Entorno (.env)

El canal utiliza el `company_id` asignado a la Empresa 2 de Aspirantes en Laravel:

```env
# Módulo de Aspirantes
ASPIRANTES_COMPANY_ID=2
```

---

## 3. Lógica de Interpretación de Respuestas

El manejador normaliza el texto y el identificador de botón, interpretando la respuesta de la siguiente manera:
* **SI:** Coincidencias con términos como `"si"`, `"sii"`, `"sí"`, `"yes"`, `"confirmar"`, `"aceptar"`, o el ID de botón `"si_button"`.
* **NO:** Coincidencias con términos como `"no"`, `"cancelar"`, `"rechazar"`, o el ID de botón `"no_button"`.

Cualquier otra respuesta fuera del formato esperado de la plantilla se ignora para evitar registros erróneos en la base de datos de Laravel.

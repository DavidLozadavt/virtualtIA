# Inventario de School (generado por scripts/school_inventory.py)

155 rutas del front · 122 permisos en la base.

## (sin grupo)

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| FormPublicPage | `/formulario/:slug` | — | consultar, crear | GET escarapela/{id}, GET formulario-publico/{id}, POST eventos-multimedia/{id}/register, POST formulario-publico/upload-adjunto, POST formulario-publico/{id}/responder |
| FormPublicPage | `/formulario-publico/:slug` | — | consultar, crear | GET escarapela/{id}, GET formulario-publico/{id}, POST eventos-multimedia/{id}/register, POST formulario-publico/upload-adjunto, POST formulario-publico/{id}/responder |
| EscarapelaPublicPage | `/escarapela/:codigo` | — | consultar, crear | GET escarapela/{id}, GET formulario-publico/{id}, POST eventos-multimedia/{id}/register, POST formulario-publico/upload-adjunto, POST formulario-publico/{id}/responder |
| InvitadoPublic | `/invitado/:token` | — | consultar, crear, editar, eliminar | DELETE {id}/{id}, GET invitado/token/{id}/items, GET {id}/export-csv, GET {id}/stats, POST invitado, POST invitado/abonar/{id} … |
| EventPublicShowPage | `/evento/:id` | — | consultar, crear, eliminar | DELETE eventos-multimedia/{id}, DELETE items/{id}, GET all_areas, GET areas, GET deezer/search?q={id}, GET eventos-multimedia/{id} … |
| StudentInscriptionPage | `/inscripcion-estudiante` | — | consultar, crear | GET escarapela/{id}, GET formulario-publico/{id}, POST eventos-multimedia/{id}/register, POST formulario-publico/upload-adjunto, POST formulario-publico/{id}/responder |
| PortalAspirantePage | `/portal-aspirante/:token` | — | consultar, crear | GET portal-aspirante/{id}, POST portal-aspirante/{id}/comprobante, POST portal-aspirante/{id}/iniciar-pago, POST portal-aspirante/{id}/verificar-pago |
| / | `/` | — | — | — |
| Gestión de tareas | `meeting/calendar` | GESTION_BOARD_TASK | — | — |
| Gestión de tipos de contrato | `/gestion-contratos/tipo-contratos` | GESTION_TIPO_CONTRATO | consultar, crear, editar, eliminar | DELETE tipo_contrato/{id}, GET tipo_contrato, POST tipo_contrato, PUT tipo_contrato/{id} |
| Gestión de entidades seguridad social | `/gestion-contratos/entidades-seguridad-social` | GESTION_ENTIDADES_SEGURIDAD_SOCIAL | consultar, crear, editar, eliminar | DELETE entidades_seguridad_social/{id}, GET entidades_seguridad_social, POST entidades_seguridad_social, PUT entidades_seguridad_social/{id} |
| Gestión de areas | `/gestion-contratos/areas` | GESTION_AREAS | consultar, crear, editar, eliminar | DELETE areas/{id}, GET all_areas, GET sedes, POST areas, PUT areas/{id} |
| Configuracion Pagos | `/pagos/configuracion-pagos` | GESTION_CONFIGURACION_PAGOS | consultar, crear, editar, eliminar | DELETE delete_configuracion_pago/{id}, DELETE delete_configuracion_pago_vigencia/{id}, GET clase_vehiculos, GET configuracion_pago_vigencias/{id}, GET configuraciones_pago, GET cost_centers … |
| ConfiguracionValoresEconomicosPage | `/pagos/configuracion-valores-economicos` | PERMISO_GESTION_VALORES_ECONOMICOS | — | — |
| FacturasAcademicasPage | `/pagos/facturas-academicas` | GESTION_VALORES_ECONOMICOS | consultar, crear | GET configuraciones_pago, GET facturas_academicas, GET facturas_academicas/{id}, GET procesos, POST generar_factura_valores_economicos |
| Gestión medios de pago | `/pagos/medio-pagos` | GESTION_MEDIO_PAGO | consultar, crear, editar | GET medio_pagos, POST medio_pagos, PUT medio_pagos/{id} |
| Gestión medios de pago | `/pagos/medio-pagos` | GESTION_MEDIO_PAGO | consultar, crear, editar | GET medio_pagos, POST medio_pagos, PUT medio_pagos/{id} |
| Configuracion Pagos | `/pagos/configuracion-pagos` | GESTION_CONFIGURACION_PAGOS | consultar, crear, editar, eliminar | DELETE delete_configuracion_pago/{id}, DELETE delete_configuracion_pago_vigencia/{id}, GET clase_vehiculos, GET configuracion_pago_vigencias/{id}, GET configuraciones_pago, GET cost_centers … |
| Gestión medios de pago | `/pagos/medio-pagos` | GESTION_MEDIO_PAGO | consultar, crear, editar | GET medio_pagos, POST medio_pagos, PUT medio_pagos/{id} |
| Gestión de tipos de pago | `/pagos/tipo-pagos` | GESTION_TIPO_PAGO | consultar, crear, editar, eliminar | DELETE tipo_pagos/{id}, GET tipo_pagos, POST tipo_pagos, PUT tipo_pagos/{id} |
| Gestión de tipos de documento | `/tipo-documento/tipo-documentos` | GESTION_TIPO_DOCUMENTOS | consultar, crear, editar, eliminar | DELETE tipo_documentos/{id}, GET procesos, GET tipo_documentos, POST tipo_documentos, PUT tipo_documentos/{id} |
| Gestión de tareas | `/aplicaciones/canva` | GESTION_BOARD_TASK | consultar, crear, editar, eliminar | DELETE delete_assign_card/{id}, DELETE delete_card/{id}, DELETE delete_check_item/{id}, DELETE delete_check_list/{id}, DELETE delete_comment_item_check/{id}, DELETE delete_date_card/{id} … |
| Gestión de tareas | `/aplicaciones/canva/board` | GESTION_BOARD_TASK | consultar, crear, editar, eliminar | DELETE delete_assign_card/{id}, DELETE delete_card/{id}, DELETE delete_check_item/{id}, DELETE delete_check_list/{id}, DELETE delete_comment_item_check/{id}, DELETE delete_date_card/{id} … |
| Gestión de procesos | `/proceso` | GESTION_PROCESOS | consultar, crear, editar, eliminar | DELETE actividades-contrato/{id}, DELETE actividades-instructores/{id}, DELETE actividades-instructores/{id}/documento, DELETE comisiones-instructores/{id}, DELETE gc/documento/eliminar/{id}, DELETE procesos/{id} … |
| Gestión de conexiones | `/conexiones` | GESTION_CONEXIONES | consultar, crear, editar, eliminar | DELETE conexiones_source/{id}, GET conexiones_source, POST conexiones_source, PUT conexiones_source/{id} |
| Gestión de nómina | `/nomina/configuracion` | GESTION_NOMINA | consultar, crear, editar | GET configurations_nomina, POST configurations_nomina, POST update_solicitud_by_supervisor/{id}, PUT configurations_nomina/{id} |
| Gestión de nómina | `/nomina/centros-costos` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE cost_centers/{id}, GET areas?idSede={id}, GET cost_centers, GET get_centros_operaciones, GET lista_usuarios, GET sedes … |
| Gestión de nómina | `/nomina/tarifas-riesgo` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE tarifas_arls/{id}, GET tarifas_arls, POST tarifas_arls, PUT tarifas_arls/{id} |
| Gestión de nómina | `/nomina/configuracion-horas-extra` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE configuracion_horas_extra/{id}, GET configuracion_horas_extra, POST configuracion_horas_extra, PUT configuracion_horas_extra/{id} |
| Gestión de nómina | `/nomina/configuracion-grupos-nomina` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE grupos_nomina/{id}, GET grupos_nomina, POST grupos_nomina, PUT grupos_nomina/{id} |
| Gestión de nómina | `/nomina/comisiones` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE comisiones/{id}, GET comisiones, POST comisiones, PUT comisiones/{id} |
| Gestión de nómina | `/nomina/tipo-incapacidad` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE tipos_incapacidades/{id}, GET tipos_incapacidades, POST tipos_incapacidades, PUT tipos_incapacidades/{id} |
| Gestión de nómina | `/nomina/tu-nomina` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE delete_reemplazo/{id}, DELETE get_deducciones/{id}, DELETE horas_extra_trabajador/{id}, GET all_areas, GET comments_solicitud_hora_extra?idSolicitud={id}, GET configuracion_horas_extra … |
| Gestión de nómina | `/nomina/tu-nomina/liquidacion-nomina` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE delete_reemplazo/{id}, DELETE get_deducciones/{id}, DELETE horas_extra_trabajador/{id}, GET all_areas, GET comments_solicitud_hora_extra?idSolicitud={id}, GET configuracion_horas_extra … |
| Gestión de nómina trabajador | `/nomina/novedades/vacaciones-trabajador` | GESTION_NOMINA_TRABAJADOR | consultar, crear | GET contracts_actives_nominas, GET observaciones_solicitud_vacac?idSolicitud={id}, GET solicitud_vacaciones, GET vacaciones, GET vacaciones?idContrato={id}, POST create_solicitud_by_supervisor … |
| Gestión de nómina | `/nomina/novedades/vacaciones-admin` | GESTION_NOMINA | consultar, crear | GET contracts_actives_nominas, GET vacaciones?idContrato={id}, POST create_solicitud_by_supervisor, POST solicitud_vacaciones_by_supervisor, POST update_solicitud_by_supervisor/{id} |
| Gestión de nómina trabajador | `/nomina/novedades/licencias-trabajador` | GESTION_NOMINA_TRABAJADOR | consultar, crear, editar | GET configurations_incapacidades, GET contracts_actives_nominas, GET get_my_solicitudes_worker, GET get_trazability_licencias/{id}, GET observacion_inc_personas?idSolicitud={id}, GET solicitud_inc_personas … |
| Gestión de nómina | `/nomina/novedades/licencias-admin` | GESTION_NOMINA | consultar, crear, editar | GET configurations_incapacidades, GET contracts_actives_nominas, GET get_trazability_licencias/{id}, GET solicitud_inc_personas, GET tipos_incapacidades, POST calculate_days_restantes_cuidado_ninez … |
| Gestión de nómina | `/nomina/novedades/horas-extra-admin` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE horas_extra_trabajador/{id}, GET configuracion_horas_extra, GET contracts_actives_nominas, GET horas_extra_supervisor, POST solicitud_trabajador_horas_extra_by_supervisor, PUT solicitud_trabajador_horas_extra_by_supervisor/{id} … |
| Gestión de horas extra por parte del empleado | `/nomina/novedades/horas-extra-trabajador` | GESTION_HORAS_EXTRA_TRABAJADOR | consultar, crear, editar, eliminar | DELETE horas_extra_trabajador/{id}, GET configuracion_horas_extra, GET horas_extra_trabajador, POST solicitud_trabajador_horas_extra, PUT solicitud_trabajador_horas_extra/{id} |
| Gestión de nómina | `/nomina/novedades/otras-deducciones` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE get_deducciones/{id}, GET configurations_nomina, GET contracts_actives_nominas, GET get_deducciones, GET get_tipo_conceptos, POST store_deducciones … |
| Gestión de nómina | `/nomina/novedades/bonificaciones` | GESTION_NOMINA | consultar, crear, editar | GET contracts_actives_nominas, GET get_bonificaciones, POST store_bonificacion, PUT update_bonificacion/{id} |
| Gestión de nómina | `/nomina/novedades/reemplazos` | GESTION_NOMINA | consultar, crear, editar, eliminar | DELETE delete_reemplazo/{id}, GET contracts_actives_nominas, GET get_reemplazos, POST store_reemplazo, PUT update_reemplazo/{id} |
| Gestión de nómina | `/nomina/novedades/configuracion-incapacidades` | GESTION_NOMINA | consultar, crear, editar | GET configurations_incapacidades, POST configurations_incapacidades, PUT configurations_incapacidades/{id} |
| Gestion de compras | `/compras/terceros` | GESTION_COMPRAS | consultar, crear, editar, eliminar | DELETE terceros/{id}, GET clase_productos, GET clases, GET cuentas_by_id/{id}, GET grupos_by_id/{id}, GET medio_pagos … |
| Gestion de compras | `/compras/terceros/registrar-compra` | GESTION_COMPRAS | consultar, crear, editar, eliminar | DELETE terceros/{id}, GET clase_productos, GET clases, GET cuentas_by_id/{id}, GET grupos_by_id/{id}, GET medio_pagos … |
| Gestion de compras | `/compras/cuentas-pagar` | GESTION_COMPRAS | consultar, crear | GET facturas, POST store_pago_factura |
| Gestion de sedes | `/sedes/gestion-sedes` | GESTION_SEDE | consultar, crear, eliminar | DELETE sedes/{id}, GET lista_usuarios, GET sedes, POST sede_update/{id}, POST sedes |
| Gestion de puntos de venta | `/gestion-punto-de-ventas/punto-ventas` | GESTION_PUNTO_VENTAS | consultar, crear, eliminar | DELETE punto_de_ventas/{id}, GET punto_de_ventas, GET sedes, POST punto_de_ventas, POST punto_de_ventas_edit/{id} |
| Gestion de puntos de venta | `/punto-de-ventas/puntos-de-ventas` | GESTION_PUNTO_VENTAS | consultar, crear, editar, eliminar | DELETE viajes/{id}, GET caja-latest/{id}, GET descuentos_planilla, GET detalle_revision, GET generate_alcoholimetria_conductor, GET generate_planilla_pdf … |
| Gestion de puntos de venta | `/punto-de-ventas/pos-tienda` | GESTION_PUNTO_VENTAS | consultar, crear | GET buscar_tercero, GET count_shoppingcart_productos_pos, GET get_point_sales_by_sede_tipe_shop/{id}, GET get_stock_minimo_punto_venta/{id}, GET get_transaccion_caja/{id}, GET puntos-de-venta/{id}/verificar-usuario … |
| Gestion de puntos de venta | `/tienda/caja/:idPunto?` | GESTION_PUNTO_VENTAS | consultar, crear | GET buscar_tercero, GET count_shoppingcart_productos_pos, GET get_stock_minimo_punto_venta/{id}, GET get_transaccion_caja/{id}, GET tipo_identificaciones, POST caja-cerrar/{id} … |
| Gestion de puntos de venta | `/caja/:idPunto?` | GESTION_PUNTO_VENTAS | consultar, crear, editar, eliminar | DELETE viajes/{id}, GET caja-latest/{id}, GET descuentos_planilla, GET detalle_revision, GET generate_alcoholimetria_conductor, GET generate_planilla_pdf … |
| gestion de afiliaciones | `/gestion-vinculaciones/vinculacion-vehiculo` | GESTION_AFILIACIONES | consultar, crear, editar | GET ciudades/departamento/{id}, GET clase_vehiculos, GET contrato-tipos-identificacion, GET contrato_active/{id}, GET departamentos, GET documents_by_proceso_nombre/VINCULACIÓN CONDUCTOR … |
| GestionRutasPage | `/transporte/gestion-rutas` | GESTION_RUTAS | consultar, crear, editar, eliminar | DELETE rutas/{id}, GET ciudades/departamento/{id}, GET departamentos, GET get_rutas_hijas/{id}, GET places, GET rutas … |
| Gestión de autorización menores | `/transporte/autorizacion-menores` | GESTION_AUTORIZACION_MENORES | consultar, crear | GET get_viajes_for_autorizacion, POST store_registro_permiso_menor |
| CronogramaRutasPage | `/cronograma/cronograma-rutas` | GESTION_CRONOGRAMA_RUTAS | consultar, crear, editar, eliminar | DELETE viajes/{id}, GET get_all_drivers, GET viajes, GET viajes/{id}, PATCH viajes/{id}/remove-vehicle, POST save_trip_agenda … |
| DetalleRevisionPage | `/detalles/detalles-revision` | GESTION_DETALLES_REVISION | consultar, crear, editar, eliminar | DELETE detalle_revision/{id}, GET detalle_revision, POST detalle_revision, PUT detalle_revision/{id} |
| DescuentoPlanillaPage | `/transporte/descuentos-planilla` | GESTION_DESCUENTOS_PLANILLA | consultar, crear, editar, eliminar | DELETE descuentos_planilla/{id}, GET descuentos_planilla, POST descuentos_planilla, PUT descuentos_planilla/{id} |
| RevicionVehiculosPage | `/trasporte/revision-vehiculos` | GESTION_REVISION_PREOPERACIONAL | consultar | GET test-pdf-observaciones, GET vehiculos/{id}/ultima_revision, GET vehiculos_con_rechazos |
| Gestión de configuración de empresa | `/empresa/configuracion-empresa` | GESTION_CONFIGURACION_EMPRESA | crear | POST company_update |
| Gestión de configuración de empresa | `/empresa/migracion-datos` | GESTION_CONFIGURACION_EMPRESA | crear | POST cargar-trabajadores, POST ejecutar_procedimiento_trabajadores |
| gestion de afiliaciones | `/gestion-vinculaciones/vinculaciones` | GESTION_AFILIACIONES | consultar, crear, editar | GET ciudades/departamento/{id}, GET clase_vehiculos, GET contrato-tipos-identificacion, GET contrato_active/{id}, GET contratos_vinculacion/{id}, GET departamentos … |
| gestion de afiliaciones | `/gestion-vinculaciones/vinculaciones-pendientes` | GESTION_AFILIACIONES | consultar, crear, editar, eliminar | DELETE delete_referencia_personal/{id}, GET get_afiliaciones_pendientes, GET get_conductores_by_id/{id}, GET get_documents_by_conductor/{id}, GET get_documents_by_propietario/{id}, GET get_documents_by_vehiculo/{id} … |
| PerfilPage | `/perfil` | — | consultar, crear | GET ciudades/departamento/{id}, GET contrato-tipos-identificacion, GET contrato_by_user, GET departamentos, GET profile/access-check, POST update_person |
| Gestión de cuentas puc | `/gestion-cuentas/cuentas-puc` | GESTION_CUENTAS_PUC | consultar, crear, editar, eliminar | DELETE delete_subcuenta_propia/{id}, GET clases, GET cuentas_by_id/{id}, GET grupos_by_id/{id}, GET subcuentas_by_code?codigo={id}, GET subcuentas_by_id/{id} … |
| Gestión de aportes socios | `/gestion-cuentas/aportes-socios` | GESTION_APORTES_SOCIOS | consultar, crear, editar, eliminar | DELETE terceros/{id}, GET clase_productos, GET get_socios, GET medio_pagos, GET tipo_pagos, GET tipo_productos_by_id/{id} … |
| Gestión de aportes socios | `/gestion-cuentas/aportes-socios/registrar-aporte` | GESTION_APORTES_SOCIOS | consultar, crear, editar, eliminar | DELETE terceros/{id}, GET clase_productos, GET get_socios, GET medio_pagos, GET tipo_pagos, GET tipo_productos_by_id/{id} … |
| BoletinGeneralAdmin | `/boletin/admin` | BOLETIN_ADMIN | consultar, crear | GET coordinador/boletines, GET coordinador/boletines/exportar, GET coordinador/boletines/{id}, GET coordinador/boletines/{id}/pdf, POST coordinador/boletines/ficha/{id}/enviar |
| BoletinGeneralEstudiante | `/boletin/estudiante` | BOLETIN_ESTUDIANTE | — | — |
| Gestión académica | `/gestion-academica/configuracion/programas` | GESTION_ACADEMICA | consultar, crear, editar, eliminar | DELETE actividades-proyecto/{id}, DELETE fase-proyecto-rap/{id}, DELETE fases-proyecto/{id}, DELETE fichas/{id}, DELETE grado-materia, DELETE horarios/materia/{id} … |
| Gestión académica | `/gestion-academica/pagos/checkout` | GESTION_ACADEMICA | consultar | GET medio_pagos, GET tipo_pagos |
| Gestión académica | `/gestion-academica/inscripciones/solicitudes` | GESTION_ACADEMICA | consultar, crear, eliminar | DELETE solicitudes_inscripcion/{id}, GET facturas_academicas/{id}, GET medio_pagos, GET solicitudes_inscripcion, GET solicitudes_inscripcion/{id}, GET tipo_pagos … |
| Gestión académica | `/gestion-academica/inscripciones/solicitudes/:idSolicitud/validar` | GESTION_ACADEMICA | consultar, crear, eliminar | DELETE solicitudes_inscripcion/{id}, GET facturas_academicas/{id}, GET medio_pagos, GET solicitudes_inscripcion, GET solicitudes_inscripcion/{id}, GET tipo_pagos … |
| Gestión académica | `/gestion-academica/inscripciones/configuracion` | GESTION_ACADEMICA | consultar, crear | GET inscripcion/configuracion, GET inscripcion/formularios, POST inscripcion/configuracion |
| Gestión académica | `/gestion-academica/configuracion/redes` | GESTION_ACADEMICA | consultar, eliminar | DELETE red/{id}, GET red |
| Gestión académica | `/gestion-academica/configuracion/redes/programas/:idRed` | GESTION_ACADEMICA | consultar, crear, editar, eliminar | DELETE actividades-proyecto/{id}, DELETE fase-proyecto-rap/{id}, DELETE fases-proyecto/{id}, DELETE fichas/{id}, DELETE grado-materia, DELETE horarios/materia/{id} … |
| Gestión académica | `/gestion-academica/configuracion/redes/programas/:idRed/:idPrograma/aperturar` | GESTION_ACADEMICA | consultar, crear, editar, eliminar | DELETE actividades-proyecto/{id}, DELETE fase-proyecto-rap/{id}, DELETE fases-proyecto/{id}, DELETE proyectos-formativos/{id}, GET actividades-proyecto, GET aperturaPrograma … |
| Gestión académica | `/gestion-academica/configuracion/redes/programas/:idRed/proyecto/:idPrograma` | GESTION_ACADEMICA | consultar, crear, editar, eliminar | DELETE actividades-proyecto/{id}, DELETE fase-proyecto-rap/{id}, DELETE fases-proyecto/{id}, DELETE proyectos-formativos/{id}, GET actividades-proyecto, GET aperturaPrograma … |
| Gestión académica | `/gestion-academica/configuracion/redes/programas/:idRed/proyecto/:idPrograma/fase/:idFase` | GESTION_ACADEMICA | consultar, crear, editar, eliminar | DELETE actividades-proyecto/{id}, DELETE fase-proyecto-rap/{id}, DELETE fases-proyecto/{id}, DELETE proyectos-formativos/{id}, GET actividades-proyecto, GET aperturaPrograma … |
| Gestión académica | `/gestion-academica/configuracion/programas/:programId/fichas` | GESTION_ACADEMICA | consultar, crear, editar, eliminar | DELETE actividades-proyecto/{id}, DELETE fase-proyecto-rap/{id}, DELETE fases-proyecto/{id}, DELETE fichas/{id}, DELETE grado-materia, DELETE horarios/materia/{id} … |
| Gestión de reporte superintendencia | `/reporte-superintendencia/reporte-superintendencia` | GESTION_REPORTE_SUPERINTENDENCIA | consultar, crear, editar, eliminar | DELETE delete_report_superintendencia/{id}, GET get_persona_auxiliar/{id}, GET get_report_superintendencia, GET get_vehiculo_auxiliar/{id}, POST store_persona_auxiliar, POST store_report_superintendencia … |
| GESTION_FACTURACION_ELECTRONICA | `/facturacion-electronica/facturacion-electronica` | GESTION_FACTURACION_ELECTRONICA | consultar, crear | GET get_facturas_electronicas, GET notas-credito, POST facturas_electronicas/retry |
| ReservasPage | `/reservas-pendientes/reservas-pendientes` | GESTION_RESERVAS_PENDIENTES | consultar, eliminar | DELETE reservas/{id}, GET reservas?page={id} |
| TarifasPage | `/tarifas/tarifas` | GESTION_TARIFAS | consultar, crear, editar, eliminar | DELETE clase_vehiculo_tarifas/{id}, GET clase_vehiculo_tarifas, GET clase_vehiculos, POST clase_vehiculo_tarifas, PUT clase_vehiculo_tarifas/{id} |
| Gestion de puntos de venta | `/gestion-productos/configuracion-producto` | GESTION_PUNTO_VENTAS | consultar, crear, editar | GET categorias, GET clase_productos, GET get_historial_precios/{id}, GET marcas, GET medidas, GET products_by_tipo_producto?search={id}&per_page={id}&page={id} … |
| ConfigurarAsientosPage | `/configuracion/asientos` | GESTION_CONFIGURACION_ASIENTOS | consultar, crear, editar, eliminar | DELETE mapas-asientos/{id}, GET clase_vehiculos, GET mapas-asientos, GET mapas-asientos/{id}, POST mapas-asientos, PUT mapas-asientos/{id} |
| Gestión de profesor. | `/profesores` | GESTION_PROFESOR | consultar | GET actividades-por-evaluar, GET get_data_rmi_configuration_by_year, GET instructor-lider, GET instructores/mi-dashboard |
| Gestión de estudiante. | `/estudiantes` | GESTION_ESTUDIANTE | consultar | GET actividades-aprendiz, GET fichas/estudiante/clases, GET mis-asistencias-generales |
| Gestion fr aprendices | `/aprendizJornada` | GESTION_APRENDIZ | consultar, crear | GET ciudades/departamento/{id}, GET contrato-tipos-identificacion, GET contrato_by_user, GET departamentos, GET profile/access-check, POST update_person |
| Horario | `/ambiente-virtual/horario` | AULA_VIRTUAL_INSTRUCTOR_HORARIO | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Horario | `/ambiente-virtual/planeacion-pedagogica` | AULA_VIRTUAL_INSTRUCTOR_PLANEACION_PEDAGOGICA, AULA_VIRTUAL_INSTRUCTOR_HORARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Horario | `/ambiente-virtual/planeacion-pedagogica/nueva` | AULA_VIRTUAL_INSTRUCTOR_PLANEACION_PEDAGOGICA, AULA_VIRTUAL_INSTRUCTOR_HORARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Horario | `/ambiente-virtual/planeacion-pedagogica/:id` | AULA_VIRTUAL_INSTRUCTOR_PLANEACION_PEDAGOGICA, AULA_VIRTUAL_INSTRUCTOR_HORARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Aula virtual instructor | `/ambiente-virtual/justificaciones-pendientes` | AULA_VIRTUAL_INSTRUCTOR, GESTION_USUARIO | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Aula virtual instructor | `/ambiente-virtual/clase/:id` | AULA_VIRTUAL_INSTRUCTOR, GESTION_USUARIO | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| ErrorsRouting | `error/*` | — | — | — |
| AuthPage | `auth/*` | — | crear | POST active_users, POST login, POST login_factus, POST logout, POST refesh_token_factus, POST set_company … |
| * | `*` | — | — | — |
| RegistroAutorizacionMenoresWebForm | `autorizacion-menores` | — | consultar, crear | GET get_viajes_for_autorizacion, POST store_registro_permiso_menor |

## Administrativo

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Videoconferencias | `/videoconferencias/standalone` | GESTION_VIDEOCONFERENCIAS, GESTION_HORAS_INSTRUCTOR | consultar, crear, eliminar | DELETE reuniones_temporales/{id}, GET reuniones_temporales, POST reuniones_temporales, POST reuniones_temporales/{id}/extend, POST token_livekit |
| RMI | `/rmi` | GESTION_HORAS_INSTRUCTOR_RMI | consultar, crear, editar | GET actividades-instructores, GET gc/documentos/{id}, GET get_all_rmi_details_for_admin, GET get_informe_by_coordinador_rmi?{id}, GET horario/ficha/{id}, GET instructores/fichas … |
| GC | `/gc` | GESTION_HORAS_INSTRUCTOR_GC | consultar, crear, editar | GET actividades-instructores, GET gc/documentos/{id}, GET get_all_rmi_details_for_admin, GET get_informe_by_coordinador_rmi?{id}, GET horario/ficha/{id}, GET instructores/fichas … |
| Horas de instrucor | `/cf` | GESTION_HORAS_INSTRUCTOR | consultar, crear, editar | GET actividades-instructores, GET gc/documentos/{id}, GET get_all_rmi_details_for_admin, GET get_informe_by_coordinador_rmi?{id}, GET horario/ficha/{id}, GET instructores/fichas … |
| Horas de instrucor | `/videoconferencias` | GESTION_HORAS_INSTRUCTOR | consultar, crear, eliminar | DELETE reuniones_temporales/{id}, GET reuniones_temporales, POST reuniones_temporales, POST reuniones_temporales/{id}/extend, POST token_livekit |

## Ambiente Virtual

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Mis Formaciones | `/ambiente-virtual/historial-raps` | AULA_VIRTUAL_INSTRUCTOR_FORMACIONES | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Mis Actividades | `/ambiente-virtual/instructor/mis-actividades` | AULA_VIRTUAL_INSTRUCTOR_ACTIVIDADES | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Mis clases | `/ambiente-virtual/mis-clases` | AULA_VIRTUAL_APRENDIZ_CLASES | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Mis Actividades | `/ambiente-virtual/actividades` | AULA_VIRTUAL_APRENDIZ_ACTIVIDADES | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Grupos | `/ambiente-virtual/grupos` | AULA_VIRTUAL_APRENDIZ_GRUPOS | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |
| Biblioteca de conocimiento | `/ambiente-virtual/biblioteca-conocimiento` | AULA_VIRTUAL_APRENDIZ_BIBLIOTECA | consultar, crear, editar, eliminar | DELETE actividades/{id}, DELETE actividades/{id}/materiales-apoyo/{id}, DELETE fichas/{id}/grupos/{id}, DELETE fichas/{id}/grupos/{id}/integrantes/{id}, DELETE fichas/{id}/materiales-apoyo/{id}, GET actividades … |

## Ciadet

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Carpetas viajeras | `/carpetas-viajeras` | CIADET_SECRETARIA_CARPETAS | consultar, crear, editar, eliminar | DELETE usuarios/{id}, GET carpetas-viajeras, GET contrato-tipos-identificacion, GET lista_usuarios_paginado, POST update_status_user/{id}, POST update_user/{id} … |
| Instructores | `/ciadet/instructores` | CIADET_SECRETARIA_INSTRUCTORES | consultar, crear, editar, eliminar | DELETE usuarios/{id}, GET carpetas-viajeras, GET contrato-tipos-identificacion, GET lista_usuarios_paginado, POST update_status_user/{id}, POST update_user/{id} … |
| Mis carpetas | `/mis_carpetas` | CIADET_INSTRUCTOR | crear, editar, eliminar | DELETE carpetas-viajeras/{id}, DELETE carpetas-viajeras/{id}/archivos/{id}, POST carpetas-viajeras, POST carpetas-viajeras/{id}/archivos, PUT carpetas-viajeras/{id} |

## Gestión Instructor

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Informe de Pago | `/informeInstructor` | GESTION_INSTRUCTOR_PAGO | consultar, crear, editar, eliminar | DELETE actividades-contrato/{id}, DELETE actividades-instructores/{id}, DELETE actividades-instructores/{id}/documento, DELETE comisiones-instructores/{id}, DELETE gc/documento/eliminar/{id}, GET actividades-contrato?idContrato={id} … |
| Actas | `/actas` | GESTION_INSTRUCTOR_ACTAS | consultar, crear, editar, eliminar | DELETE actas/anexos/{id}, GET actas/asistente/{id}, GET actas/contrato/{id}, GET actas/ficha-data, GET actas/generar-pdf/{id}, GET ciudades-departamento … |
| Novedades | `/instructor-lider` | GESTION_INSTRUCTOR_NOVEDADES | consultar, crear | GET instructor-lider, GET instructor-lider/ficha/{id}/aprendices, POST instructor-lider/cambiar-estado-aprendiz |
| Solicitudes Instructor | `/solicitudes-instructor` | GESTION_INSTRUCTOR_SOLICITUDES | consultar, crear | GET fichas/instructor/{id}/clases-asignadas, GET instructor-lider, GET materias-programa, GET materias/instructores, GET solicitud-materia, GET solicitud-materia?mine=true … |
| Mis Solicitudes | `/mis-solicitudes-instructor` | GESTION_INSTRUCTOR_MIS_SOLICITUDES | consultar, crear | GET fichas/instructor/{id}/clases-asignadas, GET instructor-lider, GET materias-programa, GET materias/instructores, GET solicitud-materia, GET solicitud-materia?mine=true … |

## Gestión Laboral

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Contratación | `/gestion-contratos/contratacion` | GESTION_CONTRATACION | consultar, crear | GET actividades_riesgo_profesional, GET all_areas, GET areas_conocimiento, GET bancos, GET centrosFormacion, GET ciudades/departamento/{id} … |
| Contratos | `/gestion-contratos/contratos` | GESTION_CONTRATOS | consultar, crear | GET actividades_riesgo_profesional, GET all_areas, GET areas, GET bancos, GET centrosFormacion, GET centrosFormacion/regional/{id} … |
| Contratos | `/gestion-contratos/contratos/contrato` | GESTION_CONTRATOS | consultar, crear | GET actividades_riesgo_profesional, GET all_areas, GET areas, GET bancos, GET centrosFormacion, GET centrosFormacion/regional/{id} … |
| Pagos de Contratos | `/gestion-contratos/pagos-pendientes/pago` | GESTION_PAGOS_CONTRATOS | consultar, crear | GET certificacion_bancaria/{id}, GET pago_by_id, GET pagos_adicionales/{id}/{id}, GET pagos_pendientes, POST get_report_month, POST pago_mensual … |
| Pagos de Contratos | `/gestion-contratos/pagos-pendientes` | GESTION_PAGOS_CONTRATOS | consultar, crear | GET certificacion_bancaria/{id}, GET pago_by_id, GET pagos_adicionales/{id}/{id}, GET pagos_pendientes, POST get_report_month, POST pago_mensual … |
| Pagos Adicionales | `/compras/cuentas-cobrar` | GESTION_CUENTAS_PENDIENTES | consultar, crear | GET cuentas_pendientes, GET pagos_abonos_cuentas_por_cobrar/{id}, POST generar_cuenta_cobro_cxc/{id}/{id}, POST store_abono_pago_cuenta_cobrar, POST store_comprobante_pago, POST store_comprobante_pago_efectivo |
| Pagos Adicionales | `/gestion-cuentas/ahorro-tercero` | GESTION_CUENTAS_PENDIENTES | consultar | GET get_ahorros_terceros |
| Pagos Adicionales | `/cobros-polizas/cobros-polizas` | GESTION_CUENTAS_PENDIENTES | consultar, crear | GET get_asociados_admin, GET tipo_afiliaciones, POST store_cuenta_cobrar_poliza |

## Gestión académica

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Periodos | `/gestion-academica/configuracion/periodos` | GESTION_PERIODOS | consultar, crear, editar, eliminar | DELETE periodos/{id}, GET periodos, POST periodos, PUT periodos/{id} |
| Jornadas | `/gestion-academica/configuracion/jornadas` | GESTION_JORNADAS | consultar, editar, eliminar | DELETE jornadas/eliminar, GET dias, GET jornadas/agrupadas, PUT jornadas/cambiar-estado |
| Regionales | `/regionales` | GESTION_REGIONAL | consultar, crear, eliminar | DELETE regional/{id}, GET ciudades, GET regional, GET regional/{id}, POST regional, POST regional/{id} |
| Centro Formación | `/centrosFormacion` | GESTION_CENTROS_FORMACION | consultar, crear, eliminar | DELETE centrosFormacion/{id}, GET centrosFormacion, GET centrosFormacion/{id}, GET ciudades, GET regional, POST centrosFormacion/user … |
| Centro Formación | `/sedesSena` | GESTION_CENTROS_FORMACION | consultar, crear, eliminar | DELETE sedesSena/{id}, GET centrosFormacion/regional/{id}, GET centrosFormacion/{id}, GET ciudades, GET regional, GET sedesSena … |
| Centro Formación | `/ambientes` | GESTION_CENTROS_FORMACION | consultar, crear, editar, eliminar | DELETE infraestructuras/{id}, GET infraestructuras/tipos, GET infraestructuras/{id}, GET regionalInfraestructuras/{id}, GET sedes/regional/{id}, POST infraestructuras … |
| Centro Formación | `/redes` | GESTION_CENTROS_FORMACION | consultar, crear, eliminar | DELETE red/{id}, GET red, POST red, POST red/{id} |

## Gestión de Usuarios

| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |
|---|---|---|---|---|
| Usuarios | `gestion-usuarios/usuarios` | GESTION_USUARIO | consultar, crear, eliminar | DELETE usuarios/{id}, GET contrato-tipos-identificacion, GET lista_usuarios_paginado, GET roles, POST update_status_user/{id}, POST update_user/{id} … |
| Roles | `gestion-usuarios/roles` | GESTION_ROLES | consultar, crear, editar, eliminar | DELETE roles/{id}, GET permisos, GET permisos_rol?rol={id}, GET roles, POST permisos/crear, POST permisos/{id} … |
| Permisos | `gestion-usuarios/permisos` | GESTION_ROL_PERMISOS | consultar, crear, editar | GET permisos, GET permisos_rol?rol={id}, GET roles, POST permisos/crear, POST permisos/{id}, POST permissions/{id}/set-parent … |
| Usuarios | `gestion-pedidos` | GESTION_USUARIO | consultar | GET get_cotizacion, GET get_cotizaciones?search={id}&per_page={id}&page={id}, GET get_pedidos?search={id}&per_page={id}&page={id}, GET get_pedidos_pendientes?search={id}&per_page={id}&page={id} |
| Usuarios | `gestion-pedidos-pendientes` | GESTION_USUARIO | consultar | GET get_cotizacion, GET get_cotizaciones?search={id}&per_page={id}&page={id}, GET get_pedidos?search={id}&per_page={id}&page={id}, GET get_pedidos_pendientes?search={id}&per_page={id}&page={id} |
| Usuarios | `gestion-cotizaciones` | GESTION_USUARIO | consultar | GET get_cotizacion, GET get_cotizaciones?search={id}&per_page={id}&page={id}, GET get_pedidos?search={id}&per_page={id}&page={id}, GET get_pedidos_pendientes?search={id}&per_page={id}&page={id} |
| Usuarios | `gestion-escenarios` | GESTION_USUARIO | consultar, crear, eliminar | DELETE escenarios/{id}, GET escenarios, POST escenarios, POST escenarios/{id} |
| Usuarios | `gestion-servicios` | GESTION_USUARIO | consultar, crear, editar, eliminar | DELETE servicios/{id}, GET category_services, GET clase_servicios, GET clases, GET escenarios, GET servicios … |
| Usuarios | `/gestion-academica/configuracion/infraestructura` | GESTION_USUARIO | — | — |
| Usuarios | `/multimedia/gestion-multimedia` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, eliminar | DELETE delete_grupo_multimedia/{id}, GET deezer/search/{id}, GET deezer/search?q={id}, GET multimedia_by_company?tipo={id}&archived={id}, POST store_grupo_multimedia, POST update_grupo_multimedia/{id} |
| Usuarios | `/formularios` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE formularios/{id}, DELETE formularios/{id}/respuestas/{id}, GET escarapela/{id}, GET eventos-multimedia?per_page=50, GET formulario-publico/{id}, GET formularios … |
| Usuarios | `/formularios/builder` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE formularios/{id}/respuestas/{id}, GET formularios/{id}, GET formularios/{id}/respuestas, POST formularios, POST formularios/upload-logo, PUT formularios/{id} |
| Usuarios | `/formularios/builder/:id` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE formularios/{id}/respuestas/{id}, GET formularios/{id}, GET formularios/{id}/respuestas, POST formularios, POST formularios/upload-logo, PUT formularios/{id} |
| Usuarios | `/gestion-eventos` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, editar, eliminar | DELETE eventos-multimedia/{id}, DELETE items/{id}, DELETE {id}/{id}, GET all_areas, GET areas, GET deezer/search?q={id} … |
| Usuarios | `/gestion-eventos/nuevo` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, eliminar | DELETE eventos-multimedia/{id}, DELETE items/{id}, GET all_areas, GET areas, GET deezer/search?q={id}, GET eventos-multimedia/{id} … |
| Usuarios | `/gestion-eventos/editar/:id` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, eliminar | DELETE eventos-multimedia/{id}, DELETE items/{id}, GET all_areas, GET areas, GET deezer/search?q={id}, GET eventos-multimedia/{id} … |
| Usuarios | `/gestion-eventos/show/:id` | GESTION_USUARIO, AULA_VIRTUAL_INSTRUCTOR | consultar, crear, eliminar | DELETE eventos-multimedia/{id}, DELETE items/{id}, GET all_areas, GET areas, GET deezer/search?q={id}, GET eventos-multimedia/{id} … |
| Usuarios | `/factura/proveedor/registrar-factura` | GESTION_USUARIO | consultar, crear, editar, eliminar | DELETE terceros/{id}, GET categorias, GET clase_productos, GET clases, GET cuentas_by_id/{id}, GET grupos_by_id/{id} … |
| Usuarios | `/factura/proveedor` | GESTION_USUARIO | consultar, crear, editar, eliminar | DELETE terceros/{id}, GET categorias, GET clase_productos, GET clases, GET cuentas_by_id/{id}, GET grupos_by_id/{id} … |
| Usuarios | `/gestion-almacen` | GESTION_USUARIO | consultar, crear, eliminar | DELETE almacenes/{id}, GET almacenes, GET get_productos_almacen/{id}, GET sedes, POST almacenes, POST almacenes/{id} … |
| Usuarios | `/gestion-solicitud-almacen` | GESTION_USUARIO | consultar, crear | GET get_solicitudes_almacen, GET trazabilidad_producto_almacen/{id}, POST aprobar_producto_almacen/{id}, POST rechazar_producto_almacen |

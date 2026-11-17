<?php
/**
 * Datos de prueba SINTÉTICOS de SchoolSena para validar Lyra de punta a punta.
 *
 * SÓLO PARA LA BASE LOCAL (virtualt_school-vt en 127.0.0.1). Se niega a correr
 * contra cualquier otro host. Es idempotente: borra lo que creó antes (todo
 * lleva la marca "lyra-prueba") y lo vuelve a crear.
 *
 * Crea, en la empresa 1:
 *   - aprendiz  lyra.aprendiz@lyra-prueba.local  (rol APRENDIZ) con ficha 9900001,
 *     un examen (cuestionario) calificado ayer, una actividad calificada hace 5 días,
 *     una pendiente para mañana y una vencida hace una semana;
 *   - admin     lyra.admin@lyra-prueba.local     (rol ADMINISTRADOR VT);
 *   - permisos dinámicos (mismo rol, permisos distintos):
 *       aprendiz.basico@lyra-prueba.local  rol APRENDIZ SENA
 *       aprendiz.plus@lyra-prueba.local    rol APRENDIZ SENA + permisos directos
 *                                          GESTION_CENTROS_FORMACION y LYRA_PRUEBA_NUEVA
 *       admin.centro@lyra-prueba.local     rol ADMIN CENTRO (administrador restringido)
 *       instructor.admin@lyra-prueba.local rol INSTRUCTOR SENA + GESTION_NOMINA y GESTION_REGIONAL directos
 *     y el permiso LYRA_PRUEBA_NUEVA (ruta /lyra-prueba/nueva), que simula un
 *     permiso recién creado.
 * No toca personas ni usuarios reales. La contraseña sale de LYRA_TEST_PASSWORD.
 *
 *   LYRA_TEST_PASSWORD=... php scripts/fixtures/school_local_lyra.php           (crear)
 *   php scripts/fixtures/school_local_lyra.php --borrar                         (quitar)
 */

$host = getenv('SCHOOL_DB_HOST') ?: '127.0.0.1';
if (!in_array($host, ['127.0.0.1', 'localhost'], true)) {
    fwrite(STDERR, "Sólo se permite la base local.\n");
    exit(1);
}
$pdo = new PDO("mysql:host=$host;dbname=virtualt_school-vt;charset=utf8mb4", 'root', '', [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$borrar = in_array('--borrar', $argv, true);
$clave = getenv('LYRA_TEST_PASSWORD');
if (!$borrar && !$clave) {
    fwrite(STDERR, "Falta LYRA_TEST_PASSWORD.\n");
    exit(1);
}

$q = fn(string $sql, array $p = []) => tap($pdo->prepare($sql), fn($st) => $st->execute($p));
function tap($v, $f) { $f($v); return $v; }
$one = fn(string $sql, array $p = []) => $q($sql, $p)->fetchColumn();

$pdo->beginTransaction();
try {
    // ── limpieza de lo creado antes ─────────────────────────────────────────
    $personas = $q("SELECT id FROM persona WHERE identificacion LIKE 'LYRA-PRUEBA-%'")->fetchAll(PDO::FETCH_COLUMN);
    $fichaId = $one("SELECT id FROM ficha WHERE codigo = '9900001'");
    $q("DELETE FROM calificacionactividad WHERE idActividad IN (SELECT id FROM actividades WHERE tituloActividad LIKE '[PRUEBA LYRA]%')");
    if ($personas) {
        $in = implode(',', array_map('intval', $personas));
        $q("DELETE FROM calificacionactividad WHERE idAMartriculaAcademica IN (SELECT ma.id FROM matriculaacademica ma JOIN matricula m ON m.id = ma.idMatricula WHERE m.idPersona IN ($in))");
        $q("DELETE FROM matriculaacademica WHERE idMatricula IN (SELECT id FROM matricula WHERE idPersona IN ($in))");
        $q("DELETE FROM matricula WHERE idPersona IN ($in)");
        $acts = $q("SELECT a.id FROM activation_company_users a JOIN usuario u ON u.id = a.user_id WHERE u.idpersona IN ($in)")->fetchAll(PDO::FETCH_COLUMN);
        if ($acts) {
            $q("DELETE FROM model_has_permissions WHERE model_type = 'App\\\\Models\\\\ActivationCompanyUser' AND model_id IN (" . implode(',', array_map('intval', $acts)) . ")");
            $q("DELETE FROM model_has_roles WHERE model_type = 'App\\\\Models\\\\ActivationCompanyUser' AND model_id IN (" . implode(',', array_map('intval', $acts)) . ")");
            $q("DELETE FROM activation_company_users WHERE id IN (" . implode(',', array_map('intval', $acts)) . ")");
        }
        $q("DELETE FROM usuario WHERE idpersona IN ($in)");
    }
    $q("DELETE FROM actividades WHERE tituloActividad LIKE '[PRUEBA LYRA]%'");
    $q("DELETE FROM role_has_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name = 'LYRA_PRUEBA_NUEVA')");
    $q("DELETE FROM permissions WHERE name = 'LYRA_PRUEBA_NUEVA'");
    if ($fichaId) {
        $q("DELETE FROM matriculaacademica WHERE idFicha = ?", [$fichaId]);
        $q("DELETE FROM ficha WHERE id = ?", [$fichaId]);
    }
    $q("DELETE FROM aperturarprograma WHERE observacion = 'lyra-prueba'");
    $q("DELETE FROM programa WHERE codigoPrograma = 'LYRA-PRUEBA'");
    if ($personas) {
        $q("DELETE FROM persona WHERE id IN (" . implode(',', array_map('intval', $personas)) . ")");
    }
    if ($borrar) {
        $pdo->commit();
        echo "Datos de prueba de Lyra eliminados.\n";
        exit(0);
    }

    // ── personas y usuarios de prueba ───────────────────────────────────────
    $hash = password_hash($clave, PASSWORD_BCRYPT);
    $rol = fn(string $n) => $one("SELECT id FROM roles WHERE name = ? AND company_id = 0", [$n]);
    $crear = function (string $doc, string $n1, string $a1, string $email, string $rolNombre) use ($q, $pdo, $hash, $rol) {
        $q("INSERT INTO persona (identificacion, nombre1, apellido1, email, idTipoIdentificacion, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, NOW(), NOW())", [$doc, $n1, $a1, $email]);
        $persona = (int) $pdo->lastInsertId();
        $q("INSERT INTO usuario (email, contrasena, idpersona, created_at, updated_at) VALUES (?, ?, ?, NOW(), NOW())",
           [$email, $hash, $persona]);
        $usuario = (int) $pdo->lastInsertId();
        $q("INSERT INTO activation_company_users (user_id, state_id, company_id, fechaInicio, fechaFin, created_at, updated_at)
            VALUES (?, 1, 1, CURDATE(), DATE_ADD(CURDATE(), INTERVAL 2 YEAR), NOW(), NOW())", [$usuario]);
        $act = (int) $pdo->lastInsertId();
        $q("INSERT INTO model_has_roles (role_id, model_type, model_id) VALUES (?, 'App\\\\Models\\\\ActivationCompanyUser', ?)",
           [$rol($rolNombre), $act]);
        return $persona;
    };
    $aprendiz = $crear('LYRA-PRUEBA-1', 'Aprendiz', 'Prueba Lyra', 'lyra.aprendiz@lyra-prueba.local', 'APRENDIZ');
    $admin = $crear('LYRA-PRUEBA-2', 'Admin', 'Prueba Lyra', 'lyra.admin@lyra-prueba.local', 'ADMINISTRADOR VT');

    // ── permisos dinámicos: mismo rol, permisos distintos ───────────────────
    $q("INSERT INTO permissions (name, guard_name, description, path, created_at, updated_at)
        VALUES ('LYRA_PRUEBA_NUEVA', 'web', 'Sección nueva de prueba', '/lyra-prueba/nueva', NOW(), NOW())");
    $permisoNuevo = (int) $pdo->lastInsertId();
    $crear('LYRA-PRUEBA-3', 'Aprendiz', 'Basico Lyra', 'aprendiz.basico@lyra-prueba.local', 'APRENDIZ SENA');
    $plus = $crear('LYRA-PRUEBA-4', 'Aprendiz', 'Plus Lyra', 'aprendiz.plus@lyra-prueba.local', 'APRENDIZ SENA');
    $crear('LYRA-PRUEBA-5', 'Admin', 'Centro Lyra', 'admin.centro@lyra-prueba.local', 'ADMIN CENTRO');
    // Instructor con permisos administrativos directos (nómina y regionales).
    $instr = $crear('LYRA-PRUEBA-6', 'Instructor', 'Admin Lyra', 'instructor.admin@lyra-prueba.local', 'INSTRUCTOR SENA');
    $actInstr = (int) $one("SELECT a.id FROM activation_company_users a JOIN usuario u ON u.id = a.user_id WHERE u.idpersona = ?", [$instr]);
    foreach (['GESTION_NOMINA', 'GESTION_REGIONAL'] as $nombrePermiso) {
        $q("INSERT INTO model_has_permissions (permission_id, model_type, model_id) VALUES (?, 'App\\\\Models\\\\ActivationCompanyUser', ?)",
           [(int) $one("SELECT id FROM permissions WHERE name = ?", [$nombrePermiso]), $actInstr]);
    }
    $actPlus = (int) $one("SELECT a.id FROM activation_company_users a JOIN usuario u ON u.id = a.user_id WHERE u.idpersona = ?", [$plus]);
    foreach ([(int) $one("SELECT id FROM permissions WHERE name = 'GESTION_CENTROS_FORMACION'"), $permisoNuevo] as $perm) {
        $q("INSERT INTO model_has_permissions (permission_id, model_type, model_id) VALUES (?, 'App\\\\Models\\\\ActivationCompanyUser', ?)",
           [$perm, $actPlus]);
    }

    // ── programa, apertura y ficha ──────────────────────────────────────────
    $q("INSERT INTO programa (nombrePrograma, codigoPrograma, idNivelEducativo, idTipoFormacion, idEstadoPrograma, idCompany, created_at, updated_at)
        VALUES ('Programa de prueba Lyra', 'LYRA-PRUEBA', 1, 1, 1, 1, NOW(), NOW())");
    $programa = (int) $pdo->lastInsertId();
    $sede = (int) $one("SELECT id FROM sedes ORDER BY id LIMIT 1");
    $periodo = (int) $one("SELECT id FROM periodo ORDER BY id DESC LIMIT 1");
    $q("INSERT INTO aperturarprograma (observacion, idPeriodo, idPrograma, estado, idSede, fechaInicialClases, fechaFinalClases, created_at, updated_at)
        VALUES ('lyra-prueba', ?, ?, 'ACTIVO', ?, DATE_SUB(CURDATE(), INTERVAL 2 MONTH), DATE_ADD(CURDATE(), INTERVAL 8 MONTH), NOW(), NOW())",
       [$periodo, $programa, $sede]);
    $apertura = (int) $pdo->lastInsertId();
    $jornada = (int) $one("SELECT id FROM jornadas ORDER BY id LIMIT 1");
    $q("INSERT INTO ficha (idJornada, idAsignacion, codigo, idRegional, idSede, created_at, updated_at)
        VALUES (?, ?, '9900001', 1, ?, NOW(), NOW())", [$jornada, $apertura, $sede]);
    $ficha = (int) $pdo->lastInsertId();

    // ── matrícula del aprendiz ──────────────────────────────────────────────
    $q("INSERT INTO matricula (fecha, idPersona, idCompany, idGrado, estado, idFicha, created_at, updated_at)
        VALUES (CURDATE(), ?, 1, 1, 'EN FORMACION', ?, NOW(), NOW())", [$aprendiz, $ficha]);
    $matricula = (int) $pdo->lastInsertId();
    $q("INSERT INTO matriculaacademica (idFicha, idMatricula, idMateria, created_at, updated_at) VALUES (?, ?, 75, NOW(), NOW())",
       [$ficha, $matricula]);
    $ma = (int) $pdo->lastInsertId();

    // ── actividades y calificaciones ────────────────────────────────────────
    $actividad = function (string $titulo, string $tipo) use ($q, $pdo, $admin) {
        $q("INSERT INTO actividades (tipoActividad, tituloActividad, descripcionActividad, idMateria, idEstado, idCompany, idPersona)
            VALUES (?, ?, 'Actividad sintética para pruebas de Lyra', 75, 1, 1, ?)", [$tipo, $titulo, $admin]);
        return (int) $pdo->lastInsertId();
    };
    $examen = $actividad('[PRUEBA LYRA] Examen parcial de algoritmos', 'cuestionario');
    $taller = $actividad('[PRUEBA LYRA] Taller de ciclos', 'con evidencia');
    $proyecto = $actividad('[PRUEBA LYRA] Proyecto de vectores', 'con evidencia');
    $lectura = $actividad('[PRUEBA LYRA] Lectura de algoritmos', 'sin evidencia');
    $nota = function (int $act, string $ini, string $fin, ?string $num, ?string $fechaCal) use ($q, $ma, $admin) {
        $q("INSERT INTO calificacionactividad (fechaCreacion, fechaInicial, fechaFinal, calificacionNumerica, idActividad,
              idAMartriculaAcademica, idPersona, idCorte, fechaCalificacion, created_at, updated_at)
            VALUES (CURDATE(), $ini, $fin, ?, ?, ?, ?, 1, $fechaCal, NOW(), NOW())", [$num, $act, $ma, $admin]);
    };
    $nota($examen, "DATE_SUB(NOW(), INTERVAL 3 DAY)", "DATE_SUB(NOW(), INTERVAL 1 DAY)", '4.2', "DATE_SUB(NOW(), INTERVAL 1 DAY)");
    $nota($taller, "DATE_SUB(NOW(), INTERVAL 10 DAY)", "DATE_SUB(NOW(), INTERVAL 5 DAY)", '3.8', "DATE_SUB(NOW(), INTERVAL 5 DAY)");
    $nota($proyecto, "DATE_SUB(NOW(), INTERVAL 2 DAY)", "DATE_ADD(CURDATE(), INTERVAL '1 18' DAY_HOUR)", null, "NULL");
    $nota($lectura, "DATE_SUB(NOW(), INTERVAL 14 DAY)", "DATE_SUB(NOW(), INTERVAL 7 DAY)", null, "NULL");

    $pdo->commit();
    echo "Datos de prueba creados: ficha 9900001, aprendiz y admin de prueba en la empresa 1.\n";
} catch (Throwable $e) {
    $pdo->rollBack();
    fwrite(STDERR, "Error: " . $e->getMessage() . "\n");
    exit(1);
}

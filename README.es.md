<div align="center">

<img src="assets/brand/mumu-loop.gif" width="148" alt="Mascota animada de Mumu">

<h1>Mumu</h1>

**Un cliente integral que ayuda a los universitarios con su vida universitaria y sus estudios.**

[简体中文](README.md) · [English](README.en.md) · [繁體中文](README.zh-TW.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · **Español**

[![CI](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml/badge.svg)](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ravicc02/Mumu?include_prereleases&label=release&color=4054d8)](https://github.com/ravicc02/Mumu/releases)
![Windows x64](https://img.shields.io/badge/Windows-x64-1e293b?logo=windows)

[Descargar](https://github.com/ravicc02/Mumu/releases) · [Cambios](CHANGELOG.md) · [Incidencias](https://github.com/ravicc02/Mumu/issues/new/choose)

<a href="assets/demo/demo.mp4">
  <img src="assets/demo/demo.gif" width="100%" alt="Demostración del espacio de trabajo de Mumu">
</a>

[Ver la demostración completa de 23 segundos](assets/demo/demo.mp4)

</div>

## Aviso de uso

> [!CAUTION]
> Mumu es un cliente local de tareas de aprendizaje para Windows. Úsalo solo con cuentas propias o con cuentas para las que tengas autorización explícita de gestión.
>
> No utilices este proyecto para realización comercial de tareas, venta de cuentas, elusión de la verificación de plataformas, robo de datos u otras actividades ilegales. El usuario es responsable de las restricciones de cuenta, la pérdida de datos y las consecuencias legales derivadas de un uso que infrinja las normas de la escuela o de la plataforma.

> [!IMPORTANT]
> Mumu no está afiliado ni respaldado por Xuexitong, EduCoder, Zhihuishu, ICourse163, ICVE, ZJY o Yuketang. Los nombres y logotipos solo identifican los servicios conectados.
>
> Las páginas y capacidades de las plataformas pueden cambiar. Mumu no garantiza compatibilidad con todas las escuelas, cursos, tipos de pregunta o versiones de página. Las solicitudes al modelo envían el contexto de la tarea al proveedor configurado. Confirma las normas de la plataforma y los requisitos de tratamiento de datos antes de usar la aplicación.

### Descargo de responsabilidad

> Este proyecto está destinado únicamente a la investigación técnica y el intercambio de aprendizaje. No lo utilices en ninguna situación que infrinja las normas de las plataformas, las regulaciones escolares o las leyes y reglamentos aplicables. Cada usuario asume la responsabilidad correspondiente.

## Funciones

| Función | Descripción |
| --- | --- |
| **Cuentas y plataformas** | Guarda cuentas por plataforma e ID de usuario estable, sincroniza nombres y avatares, actualiza el registro existente tras una nueva autorización y nunca fusiona cuentas por nombre visible |
| **Tareas** | Recopila tareas sin terminar y actualmente entregables; identifica tipos de pregunta, genera respuestas, completa de forma determinista, guarda borradores, evalúa y verifica la entrega |
| **Cursos** | Lee cursos y puntos de tarea, omite contenido completado, ejecuta flujos compatibles de medios y lectura, y conserva la evidencia de progreso de la plataforma |
| **Asistencia** | Procesa varias cuentas por lotes; admite códigos, gestos, códigos QR y ubicación, con enlaces de lote revocables |
| **Navegador de tareas en vivo** | Asigna a cada tarea un navegador aislado y una copia de la autorización; permite visualización en vivo, pausa, reintento e intervención sin detener la tarea al cerrar el visor |

Las tareas se ejecutan en navegadores independientes y pueden procesar cuentas distintas en paralelo. Mumu solo marca una tarea como completada cuando existe un resultado verificable de la plataforma; las afirmaciones del modelo, las capturas y la finalización parcial no se consideran pruebas de finalización.

## Plataformas compatibles

| Plataforma | Tareas | Cursos | Asistencia |
| :---: | :---: | :---: | :---: |
| <img src="assets/platforms/xuexitong.png" height="28" alt="Logotipo de Xuexitong"><br>**Xuexitong** | Sí | Sí | Sí |
| <img src="assets/platforms/educoder.png" height="28" alt="Logotipo de EduCoder"><br>**EduCoder** | Sí | — | — |
| <img src="assets/platforms/zhihuishu.ico" height="28" alt="Logotipo de Zhihuishu"><br>**Zhihuishu** | — | Sí | — |
| <img src="assets/platforms/icourse.ico" height="28" alt="Logotipo de ICourse163"><br>**ICourse163** | — | Sí | — |
| <img src="assets/platforms/icve.ico" height="28" alt="Logotipo de ICVE"><br>**ICVE** | — | Sí | — |
| <img src="assets/platforms/zjy.ico" height="28" alt="Logotipo de ZJY"><br>**ZJY** | — | Sí | — |
| <img src="assets/platforms/yuketang.ico" height="28" alt="Logotipo de Yuketang"><br>**Yuketang** | — | Sí | — |

## Primeros pasos

1. Descarga el instalador de Windows x64 desde [Releases](https://github.com/ravicc02/Mumu/releases). Las versiones candidatas aparecen como Pre-release.
2. En el primer inicio, revisa la comprobación del entorno. Instala las dependencias que falten cuando sea posible o utiliza el comando manual mostrado si falla la instalación.
3. Configura el endpoint del modelo, la API Key, el modelo y el esfuerzo de razonamiento; después autoriza una cuenta de plataforma. Ambos pasos pueden omitirse y completarse más tarde en Configuración.
4. Selecciona una tarea para ejecutarla. Cuando Mumu solicite intervención, revisa la página en el navegador original y continúa.
5. Comprueba el resultado de verificación de la plataforma. La entrega final de Xuexitong y la evaluación automática de EduCoder se configuran por separado en los ajustes de tareas.

**Ejecutar desde el código fuente**

Entorno compatible: Windows 10/11 x64, Node.js 24, Python 3.13 y Chrome o Edge.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm start
~~~

Para previsualizar la aplicación web, utiliza dos terminales:

~~~powershell
# Terminal 1: raíz del repositorio
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877

# Terminal 2: raíz del repositorio
npm run desktop:web
~~~

El frontend se ejecuta en http://127.0.0.1:5174/ y la API local en http://127.0.0.1:8877/. Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para el desarrollo y la compilación.

## Datos y privacidad

El directorio de datos predeterminado sigue siendo %LOCALAPPDATA%/AIAssistantStudio y guarda autorizaciones, tareas, datos SQLite y capturas. El nombre antiguo se mantiene por compatibilidad de actualización y no cambia con el nombre visible del producto. Las solicitudes al modelo envían las preguntas y el contexto necesario al servicio configurado por el usuario.

~~~text
logs/
  main.log       Registro detallado de hoy
  oldmain.log    Archivo de registros anteriores
~~~

Si aparece un problema, abre el directorio de registros desde Configuración y facilita un main.log con los datos sensibles eliminados. Las incidencias públicas no deben incluir cookies, API Keys, credenciales QR ni archivos de autorización. Consulta [Seguridad y privacidad](.github/SECURITY.md).

## Estructura del proyecto

~~~text
desktop/       Vue y Electron; src/app es la entrada, src/features agrupa dominios de negocio y src/shared contiene controles compartidos
backend/       FastAPI, adaptadores de plataforma y ejecución de tareas; services se divide por cuenta, tarea, ejecución y notificaciones
scripts/       Versionado, auditoría de código, empaquetado, pruebas de humo y herramientas de publicación
assets/        Marca, demostración y logotipos de plataformas usados por README
.github/       CI, Releases, plantillas de incidencias y mantenimiento de dependencias
~~~

## Contribución

[Guía de contribución](CONTRIBUTING.md) · [Registro de cambios](CHANGELOG.md)

## Star History

<div align="center">

<a href="https://star-history.com/#ravicc02/mumu&amp;Date">
  <img src="https://api.star-history.com/svg?repos=ravicc02/mumu&amp;type=Date" width="100%" alt="Mumu Star History">
</a>

</div>
